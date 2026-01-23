"""
Tests for the image pipeline system.
"""

import gzip
import lzma
import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from owrt.pipeline import (
    PipelineContext,
    PipelineStep,
    CompressStep,
    PadStep,
    PadExtraStep,
    PadOffsetStep,
    ConcatStep,
    CopyKernelStep,
    CopyRootfsStep,
    AppendDTBStep,
    AppendRootfsStep,
    CheckSizeStep,
    SysupgradeTarStep,
    SaveArtifactStep,
    LoadArtifactStep,
    ImagePipeline,
    run_pipeline,
    PIPELINE_STEPS,
)


@pytest.fixture
def temp_work_dir():
    """Create a temporary working directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_kernel(temp_work_dir):
    """Create a sample kernel file."""
    kernel_file = temp_work_dir / "kernel.bin"
    # Create a 1KB test kernel
    kernel_file.write_bytes(b'\x00' * 1024 + b'KERNEL_MAGIC')
    return kernel_file


@pytest.fixture
def sample_dtb(temp_work_dir):
    """Create a sample DTB file."""
    dtb_file = temp_work_dir / "test.dtb"
    # Create a minimal DTB-like file
    dtb_file.write_bytes(b'\xd0\x0d\xfe\xed' + b'\x00' * 100)
    return dtb_file


@pytest.fixture
def sample_rootfs(temp_work_dir):
    """Create a sample rootfs file."""
    rootfs_file = temp_work_dir / "rootfs.squashfs"
    rootfs_file.write_bytes(b'hsqs' + b'\x00' * 500)
    return rootfs_file


@pytest.fixture
def pipeline_context(temp_work_dir, sample_kernel, sample_dtb, sample_rootfs):
    """Create a pipeline context for testing."""
    return PipelineContext(
        kernel=sample_kernel,
        dtb=sample_dtb,
        rootfs=sample_rootfs,
        work_dir=temp_work_dir,
        output_dir=temp_work_dir,
        arch="arm64",
        kernel_load_addr="0x44000000",
        kernel_entry_addr="0x44000000",
        target="mediatek/filogic",
        board="openwrt_one",
        verbose=False,
    )


class TestCompressStep:
    """Tests for compression step."""

    def test_gzip_compression(self, pipeline_context, sample_kernel):
        """Test gzip compression."""
        pipeline_context.current = sample_kernel
        step = CompressStep()

        output = step.execute(pipeline_context, {"compression": "gzip"})

        assert output.exists()
        assert output.suffix == ".gzip"

        # Verify it's valid gzip
        with gzip.open(output, 'rb') as f:
            decompressed = f.read()
        assert decompressed == sample_kernel.read_bytes()

    def test_lzma_compression(self, pipeline_context, sample_kernel):
        """Test lzma compression."""
        pipeline_context.current = sample_kernel
        step = CompressStep()

        output = step.execute(pipeline_context, {"compression": "lzma"})

        assert output.exists()
        assert output.suffix == ".lzma"

        # Verify it's valid lzma
        with lzma.open(output, 'rb') as f:
            decompressed = f.read()
        assert decompressed == sample_kernel.read_bytes()

    def test_xz_compression(self, pipeline_context, sample_kernel):
        """Test xz compression."""
        pipeline_context.current = sample_kernel
        step = CompressStep()

        output = step.execute(pipeline_context, {"compression": "xz"})

        assert output.exists()
        assert output.suffix == ".xz"

        # Verify it's valid xz
        with lzma.open(output, 'rb', format=lzma.FORMAT_XZ) as f:
            decompressed = f.read()
        assert decompressed == sample_kernel.read_bytes()

    def test_no_compression(self, pipeline_context, sample_kernel):
        """Test passthrough (no compression)."""
        pipeline_context.current = sample_kernel
        step = CompressStep()

        output = step.execute(pipeline_context, {"compression": "none"})

        assert output.exists()
        assert output.read_bytes() == sample_kernel.read_bytes()


class TestPadStep:
    """Tests for padding step."""

    def test_pad_to_size(self, pipeline_context, temp_work_dir):
        """Test padding to specific size."""
        # Create a small file
        input_file = temp_work_dir / "small.bin"
        input_file.write_bytes(b'test')
        pipeline_context.current = input_file

        step = PadStep()
        output = step.execute(pipeline_context, {"size": "1k"})

        assert output.exists()
        assert output.stat().st_size == 1024

        # Verify original content preserved
        content = output.read_bytes()
        assert content[:4] == b'test'
        assert content[4:] == b'\x00' * (1024 - 4)

    def test_pad_to_64k(self, pipeline_context, temp_work_dir):
        """Test padding to 64K."""
        input_file = temp_work_dir / "input.bin"
        input_file.write_bytes(b'A' * 100)
        pipeline_context.current = input_file

        step = PadStep()
        output = step.execute(pipeline_context, {"to": "64k"})

        assert output.stat().st_size == 64 * 1024

    def test_parse_size_suffixes(self, pipeline_context, temp_work_dir):
        """Test size suffix parsing."""
        step = PadStep()

        assert step._parse_size("64k") == 64 * 1024
        assert step._parse_size("1M") == 1024 * 1024
        assert step._parse_size("2G") == 2 * 1024 * 1024 * 1024
        assert step._parse_size("512") == 512


class TestConcatStep:
    """Tests for concatenation step."""

    def test_concat_files(self, pipeline_context, temp_work_dir):
        """Test concatenating multiple files."""
        # Create test files
        file1 = temp_work_dir / "part1.bin"
        file2 = temp_work_dir / "part2.bin"
        file1.write_bytes(b'PART1')
        file2.write_bytes(b'PART2')

        pipeline_context.artifacts = {
            "part1": file1,
            "part2": file2,
        }

        step = ConcatStep()
        output = step.execute(pipeline_context, {
            "parts": [
                {"type": "file", "path": "${part1}"},
                {"type": "file", "path": "${part2}"},
            ]
        })

        assert output.exists()
        assert output.read_bytes() == b'PART1PART2'

    def test_concat_with_padding(self, pipeline_context, temp_work_dir):
        """Test concatenation with padding between parts."""
        file1 = temp_work_dir / "part1.bin"
        file2 = temp_work_dir / "part2.bin"
        file1.write_bytes(b'PART1')
        file2.write_bytes(b'PART2')

        pipeline_context.artifacts = {
            "part1": file1,
            "part2": file2,
        }

        step = ConcatStep()
        output = step.execute(pipeline_context, {
            "parts": [
                {"type": "file", "path": "${part1}", "pad_to": "16"},
                {"type": "file", "path": "${part2}"},
            ]
        })

        content = output.read_bytes()
        assert len(content) == 16 + 5  # 16 bytes padded + 5 bytes PART2
        assert content[:5] == b'PART1'
        assert content[5:16] == b'\x00' * 11
        assert content[16:] == b'PART2'


class TestCopyKernelStep:
    """Tests for kernel copy step."""

    def test_copy_kernel(self, pipeline_context, sample_kernel):
        """Test copying kernel as starting point."""
        step = CopyKernelStep()
        output = step.execute(pipeline_context, {})

        assert output.exists()
        assert output.read_bytes() == sample_kernel.read_bytes()
        assert pipeline_context.current == output


class TestImagePipeline:
    """Tests for the pipeline execution."""

    def test_simple_pipeline(self, pipeline_context, sample_kernel):
        """Test a simple compress pipeline."""
        pipeline_def = [
            {"kernel": None},  # Copy kernel
            {"compress": "gzip"},
        ]

        pipeline = ImagePipeline(verbose=False)
        output = pipeline.execute(pipeline_def, pipeline_context)

        assert output.exists()
        # Verify it's gzipped
        with gzip.open(output, 'rb') as f:
            decompressed = f.read()
        assert decompressed == sample_kernel.read_bytes()

    def test_multi_step_pipeline(self, pipeline_context, sample_kernel):
        """Test a multi-step pipeline."""
        pipeline_def = [
            {"kernel": None},
            {"compress": "gzip"},
            {"pad": {"size": "64k"}},
        ]

        pipeline = ImagePipeline(verbose=False)
        output = pipeline.execute(pipeline_def, pipeline_context)

        assert output.exists()
        assert output.stat().st_size == 64 * 1024

    def test_string_step_definition(self, pipeline_context):
        """Test pipeline with string step definitions."""
        pipeline_def = [
            "kernel",
            {"compress": "gzip"},
        ]

        pipeline = ImagePipeline(verbose=False)
        output = pipeline.execute(pipeline_def, pipeline_context)

        assert output.exists()


class TestRunPipeline:
    """Tests for the run_pipeline convenience function."""

    def test_run_pipeline_basic(self, temp_work_dir, sample_kernel, sample_dtb):
        """Test basic pipeline execution."""
        pipeline_def = [
            {"kernel": None},
            {"compress": "gzip"},
        ]

        output = run_pipeline(
            pipeline_def=pipeline_def,
            kernel=sample_kernel,
            dtb=sample_dtb,
            work_dir=temp_work_dir,
            verbose=False,
        )

        assert output.exists()


class TestPipelineStepsRegistry:
    """Tests for the pipeline steps registry."""

    def test_all_steps_registered(self):
        """Verify all expected steps are registered."""
        expected_steps = [
            # Core steps
            'compress', 'pad', 'pad-extra', 'pad-offset', 'pad-rootfs',
            # Image format steps
            'fit', 'uimage', 'ubi', 'sysupgrade-tar',
            # Metadata and validation
            'metadata', 'check-size',
            # File manipulation
            'concat', 'append-dtb', 'append-rootfs', 'append-kernel',
            # Input selection
            'kernel', 'rootfs',
            # Artifact management
            'save-artifact', 'load-artifact',
        ]
        for step in expected_steps:
            assert step in PIPELINE_STEPS, f"Step '{step}' not found in registry"

    def test_custom_step_registration(self):
        """Test registering a custom step."""

        class CustomStep(PipelineStep):
            name = "custom"

            def execute(self, ctx, config):
                return ctx.current

        pipeline = ImagePipeline()
        pipeline.register_step("custom", CustomStep)

        assert "custom" in pipeline.steps


class TestPipelineContext:
    """Tests for PipelineContext."""

    def test_default_values(self):
        """Test default context values."""
        ctx = PipelineContext()

        assert ctx.kernel is None
        assert ctx.dtb is None
        assert ctx.arch == "arm64"
        assert ctx.kernel_load_addr == "0x44000000"
        assert ctx.artifacts == {}

    def test_with_values(self, sample_kernel, sample_dtb):
        """Test context with values."""
        ctx = PipelineContext(
            kernel=sample_kernel,
            dtb=sample_dtb,
            arch="arm",
            kernel_load_addr="0x80008000",
        )

        assert ctx.kernel == sample_kernel
        assert ctx.dtb == sample_dtb
        assert ctx.arch == "arm"
        assert ctx.kernel_load_addr == "0x80008000"


class TestAppendDTBStep:
    """Tests for DTB appending step."""

    def test_append_dtb(self, pipeline_context, sample_kernel, sample_dtb):
        """Test appending DTB to kernel."""
        pipeline_context.current = sample_kernel

        step = AppendDTBStep()
        output = step.execute(pipeline_context, {})

        assert output.exists()
        content = output.read_bytes()
        kernel_content = sample_kernel.read_bytes()
        dtb_content = sample_dtb.read_bytes()

        # Output should be kernel + dtb
        assert content == kernel_content + dtb_content


class TestAppendRootfsStep:
    """Tests for rootfs appending step."""

    def test_append_rootfs(self, pipeline_context, sample_kernel, sample_rootfs):
        """Test appending rootfs to kernel."""
        pipeline_context.current = sample_kernel

        step = AppendRootfsStep()
        output = step.execute(pipeline_context, {})

        assert output.exists()
        content = output.read_bytes()
        kernel_content = sample_kernel.read_bytes()
        rootfs_content = sample_rootfs.read_bytes()

        assert content == kernel_content + rootfs_content


class TestPadExtraStep:
    """Tests for extra padding step."""

    def test_pad_extra(self, pipeline_context, temp_work_dir):
        """Test adding extra padding."""
        input_file = temp_work_dir / "input.bin"
        input_file.write_bytes(b'test')
        pipeline_context.current = input_file

        step = PadExtraStep()
        output = step.execute(pipeline_context, {"size": "100"})

        assert output.exists()
        assert output.stat().st_size == 4 + 100  # original + padding

        content = output.read_bytes()
        assert content[:4] == b'test'
        assert content[4:] == b'\x00' * 100

    def test_pad_extra_with_suffix(self, pipeline_context, temp_work_dir):
        """Test extra padding with size suffix."""
        input_file = temp_work_dir / "input.bin"
        input_file.write_bytes(b'A')
        pipeline_context.current = input_file

        step = PadExtraStep()
        output = step.execute(pipeline_context, {"size": "1k"})

        assert output.stat().st_size == 1 + 1024


class TestPadOffsetStep:
    """Tests for offset padding step."""

    def test_pad_offset_alignment(self, pipeline_context, temp_work_dir):
        """Test alignment padding with offset."""
        input_file = temp_work_dir / "input.bin"
        # 10 bytes of data
        input_file.write_bytes(b'0123456789')
        pipeline_context.current = input_file

        step = PadOffsetStep()
        # Pad to 16-byte boundary with no offset
        output = step.execute(pipeline_context, {"pad": "16", "offset": "0"})

        assert output.exists()
        # 10 bytes rounds up to 16
        assert output.stat().st_size == 16

    def test_pad_offset_with_offset(self, pipeline_context, temp_work_dir):
        """Test alignment with non-zero offset."""
        input_file = temp_work_dir / "input.bin"
        input_file.write_bytes(b'ABC')  # 3 bytes
        pipeline_context.current = input_file

        step = PadOffsetStep()
        # With offset 5, total is 8, which is already aligned to 8
        # So no padding needed
        output = step.execute(pipeline_context, {"pad": "8", "offset": "5"})

        assert output.stat().st_size == 3  # No change needed


class TestCheckSizeStep:
    """Tests for size checking step."""

    def test_check_size_ok(self, pipeline_context, temp_work_dir):
        """Test size check passes when under limit."""
        input_file = temp_work_dir / "input.bin"
        input_file.write_bytes(b'A' * 100)
        pipeline_context.current = input_file

        step = CheckSizeStep()
        output = step.execute(pipeline_context, {"max": "1k"})

        assert output == input_file  # Same file returned

    def test_check_size_fail(self, pipeline_context, temp_work_dir):
        """Test size check fails when over limit."""
        input_file = temp_work_dir / "input.bin"
        input_file.write_bytes(b'A' * 2000)
        pipeline_context.current = input_file

        step = CheckSizeStep()
        with pytest.raises(ValueError, match="too large"):
            step.execute(pipeline_context, {"max": "1k"})

    def test_check_size_no_limit(self, pipeline_context, temp_work_dir):
        """Test size check with no limit (0) passes."""
        input_file = temp_work_dir / "input.bin"
        input_file.write_bytes(b'A' * 1000000)
        pipeline_context.current = input_file

        step = CheckSizeStep()
        output = step.execute(pipeline_context, {"max": "0"})

        assert output == input_file


class TestCopyRootfsStep:
    """Tests for rootfs copy step."""

    def test_copy_rootfs(self, pipeline_context, sample_rootfs):
        """Test copying rootfs as starting point."""
        step = CopyRootfsStep()
        output = step.execute(pipeline_context, {})

        assert output.exists()
        assert output.read_bytes() == sample_rootfs.read_bytes()
        assert pipeline_context.current == output


class TestSysupgradeTarStep:
    """Tests for sysupgrade tar creation."""

    def test_sysupgrade_tar(self, pipeline_context, sample_kernel, sample_rootfs):
        """Test creating sysupgrade tarball."""
        import tarfile

        pipeline_context.current = sample_kernel

        step = SysupgradeTarStep()
        output = step.execute(pipeline_context, {})

        assert output.exists()
        assert output.suffix == ".tar"

        # Verify tar contents
        with tarfile.open(output, 'r') as tar:
            names = tar.getnames()
            assert "sysupgrade.conf" in names
            assert "kernel" in names
            assert "rootfs" in names


class TestArtifactSteps:
    """Tests for artifact save/load steps."""

    def test_save_and_load_artifact(self, pipeline_context, temp_work_dir):
        """Test saving and loading artifacts."""
        input_file = temp_work_dir / "input.bin"
        input_file.write_bytes(b'artifact_data')
        pipeline_context.current = input_file

        # Save
        save_step = SaveArtifactStep()
        save_step.execute(pipeline_context, {"name": "my_artifact"})

        assert "my_artifact" in pipeline_context.artifacts
        assert pipeline_context.artifacts["my_artifact"] == input_file

        # Create a new file to change current
        other_file = temp_work_dir / "other.bin"
        other_file.write_bytes(b'other')
        pipeline_context.current = other_file

        # Load
        load_step = LoadArtifactStep()
        loaded = load_step.execute(pipeline_context, {"name": "my_artifact"})

        assert loaded == input_file
        assert pipeline_context.current == input_file


class TestComplexPipeline:
    """Tests for complex multi-step pipelines."""

    def test_kernel_compress_pad_pipeline(self, pipeline_context, sample_kernel):
        """Test a realistic pipeline: kernel -> compress -> pad."""
        pipeline_def = [
            {"kernel": None},
            {"compress": "gzip"},
            {"pad": {"size": "128k"}},
        ]

        pipeline = ImagePipeline(verbose=False)
        output = pipeline.execute(pipeline_def, pipeline_context)

        assert output.exists()
        assert output.stat().st_size == 128 * 1024

    def test_kernel_append_dtb_pipeline(self, pipeline_context, sample_kernel, sample_dtb):
        """Test kernel with appended DTB."""
        pipeline_def = [
            {"kernel": None},
            {"append-dtb": None},
        ]

        pipeline = ImagePipeline(verbose=False)
        output = pipeline.execute(pipeline_def, pipeline_context)

        assert output.exists()
        expected_size = sample_kernel.stat().st_size + sample_dtb.stat().st_size
        assert output.stat().st_size == expected_size

    def test_artifact_pipeline(self, pipeline_context, sample_kernel, temp_work_dir):
        """Test pipeline with artifact save/load."""
        pipeline_def = [
            {"kernel": None},
            {"compress": "gzip"},
            {"save-artifact": {"name": "compressed_kernel"}},
            {"pad": {"size": "64k"}},
            {"save-artifact": {"name": "padded_kernel"}},
        ]

        pipeline = ImagePipeline(verbose=False)
        output = pipeline.execute(pipeline_def, pipeline_context)

        assert "compressed_kernel" in pipeline_context.artifacts
        assert "padded_kernel" in pipeline_context.artifacts
        assert output.stat().st_size == 64 * 1024
