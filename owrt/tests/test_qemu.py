"""Tests for QEMU-based runtime testing (qemu.py)."""

import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from dataclasses import dataclass

from owrt.qemu import (
    QEMUConfig,
    QEMURunner,
    QEMUTestRunner,
    QEMUTestResult,
    QEMU_TARGETS,
    run_qemu_tests,
)


class TestQEMUConfig:
    """Tests for QEMUConfig dataclass."""

    def test_default_values(self):
        """Test default values for QEMUConfig."""
        config = QEMUConfig(
            binary="qemu-system-aarch64",
            machine="virt",
            cpu="cortex-a57",
        )
        assert config.memory == "256M"
        assert config.extra_args == []
        assert config.kernel_arg == "-kernel"
        assert config.console == "ttyAMA0"

    def test_custom_values(self):
        """Test custom values for QEMUConfig."""
        config = QEMUConfig(
            binary="qemu-system-x86_64",
            machine="q35",
            cpu="qemu64",
            memory="512M",
            extra_args=["-enable-kvm"],
            kernel_arg="-bios",
            console="ttyS0",
        )
        assert config.binary == "qemu-system-x86_64"
        assert config.memory == "512M"
        assert config.extra_args == ["-enable-kvm"]
        assert config.kernel_arg == "-bios"
        assert config.console == "ttyS0"


class TestQEMUTargets:
    """Tests for predefined QEMU targets."""

    def test_armsr_armv8_target_exists(self):
        """Test armsr-armv8 target configuration exists."""
        assert "armsr-armv8" in QEMU_TARGETS
        config = QEMU_TARGETS["armsr-armv8"]
        assert config.binary == "qemu-system-aarch64"
        assert config.machine == "virt"
        assert config.cpu == "cortex-a57"

    def test_x86_64_target_exists(self):
        """Test x86-64 target configuration exists."""
        assert "x86-64" in QEMU_TARGETS
        config = QEMU_TARGETS["x86-64"]
        assert config.binary == "qemu-system-x86_64"
        assert config.machine == "q35"

    def test_malta_be_target_exists(self):
        """Test malta-be target configuration exists."""
        assert "malta-be" in QEMU_TARGETS
        config = QEMU_TARGETS["malta-be"]
        assert config.binary == "qemu-system-mips"
        assert config.machine == "malta"


class TestQEMUTestResult:
    """Tests for QEMUTestResult dataclass."""

    def test_passed_result(self):
        """Test creating a passed test result."""
        result = QEMUTestResult(
            name="test_uname",
            passed=True,
            duration=0.5,
            output="Linux openwrt 5.15.0",
        )
        assert result.passed is True
        assert result.name == "test_uname"
        assert result.duration == 0.5
        assert result.error == ""

    def test_failed_result(self):
        """Test creating a failed test result."""
        result = QEMUTestResult(
            name="test_ssh",
            passed=False,
            duration=30.0,
            error="Connection refused",
        )
        assert result.passed is False
        assert result.error == "Connection refused"


class TestQEMURunner:
    """Tests for QEMURunner class."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for tests."""
        tmpdir = tempfile.mkdtemp()
        yield Path(tmpdir)
        shutil.rmtree(tmpdir)

    @pytest.fixture
    def mock_firmware(self, temp_dir):
        """Create a mock firmware file."""
        firmware = temp_dir / "kernel.bin"
        firmware.write_bytes(b"\x00" * 1024)
        return firmware

    def test_init_valid_target(self, mock_firmware):
        """Test initializing with a valid target."""
        runner = QEMURunner("armsr-armv8", mock_firmware)
        assert runner.target == "armsr-armv8"
        assert runner.config.binary == "qemu-system-aarch64"

    def test_init_invalid_target(self, mock_firmware):
        """Test initializing with an invalid target raises error."""
        with pytest.raises(ValueError, match="Unsupported QEMU target"):
            QEMURunner("invalid-target", mock_firmware)

    def test_find_free_port(self, mock_firmware):
        """Test finding a free port."""
        runner = QEMURunner("armsr-armv8", mock_firmware)
        port = runner._find_free_port()
        assert isinstance(port, int)
        assert port > 0

    @patch("subprocess.run")
    def test_check_qemu_available_true(self, mock_run, mock_firmware):
        """Test checking QEMU availability when available."""
        mock_run.return_value = MagicMock(returncode=0)
        runner = QEMURunner("armsr-armv8", mock_firmware)
        assert runner._check_qemu_available() is True

    @patch("subprocess.run")
    def test_check_qemu_available_false(self, mock_run, mock_firmware):
        """Test checking QEMU availability when not available."""
        mock_run.side_effect = FileNotFoundError()
        runner = QEMURunner("armsr-armv8", mock_firmware)
        assert runner._check_qemu_available() is False

    def test_start_missing_firmware(self, temp_dir):
        """Test starting with missing firmware raises error."""
        missing_firmware = temp_dir / "nonexistent.bin"
        runner = QEMURunner("armsr-armv8", missing_firmware)
        with pytest.raises(FileNotFoundError):
            runner.start()

    @patch("subprocess.run")
    def test_start_missing_qemu(self, mock_run, mock_firmware):
        """Test starting with missing QEMU binary raises error."""
        mock_run.side_effect = FileNotFoundError()
        runner = QEMURunner("armsr-armv8", mock_firmware)
        with pytest.raises(RuntimeError, match="QEMU binary not found"):
            runner.start()

    def test_get_console_log_empty(self, mock_firmware):
        """Test getting console log when empty."""
        runner = QEMURunner("armsr-armv8", mock_firmware)
        assert runner.get_console_log() == []

    def test_context_manager_stop(self, mock_firmware):
        """Test that context manager calls stop on exit."""
        runner = QEMURunner("armsr-armv8", mock_firmware)
        runner.process = MagicMock()

        with patch.object(runner, "start"):
            with patch.object(runner, "stop") as mock_stop:
                with runner:
                    pass
                mock_stop.assert_called_once()

    def test_stop_terminates_process(self, mock_firmware):
        """Test that stop terminates the QEMU process."""
        runner = QEMURunner("armsr-armv8", mock_firmware)
        mock_process = MagicMock()
        runner.process = mock_process

        runner.stop()

        mock_process.terminate.assert_called_once()
        assert runner.process is None


class TestQEMUTestRunner:
    """Tests for QEMUTestRunner class."""

    @pytest.fixture
    def mock_qemu(self):
        """Create a mock QEMURunner."""
        qemu = MagicMock(spec=QEMURunner)
        return qemu

    def test_run_test_success(self, mock_qemu):
        """Test running a successful test."""
        mock_qemu.run_ssh_command.return_value = (0, "output", "")
        runner = QEMUTestRunner(mock_qemu)

        result = runner._run_test("test_simple", "true")

        assert result.passed is True
        assert result.name == "test_simple"

    def test_run_test_failure(self, mock_qemu):
        """Test running a failed test."""
        mock_qemu.run_ssh_command.return_value = (1, "", "error")
        runner = QEMUTestRunner(mock_qemu)

        result = runner._run_test("test_fail", "false")

        assert result.passed is False
        assert result.name == "test_fail"

    def test_run_test_with_check_fn(self, mock_qemu):
        """Test running a test with a custom check function."""
        mock_qemu.run_ssh_command.return_value = (0, "Linux", "")
        runner = QEMUTestRunner(mock_qemu)

        result = runner._run_test(
            "test_uname",
            "uname",
            check_fn=lambda c, o, e: "Linux" in o,
        )

        assert result.passed is True

    def test_run_test_check_fn_fails(self, mock_qemu):
        """Test running a test where check function returns False."""
        mock_qemu.run_ssh_command.return_value = (0, "Windows", "")
        runner = QEMUTestRunner(mock_qemu)

        result = runner._run_test(
            "test_uname",
            "uname",
            check_fn=lambda c, o, e: "Linux" in o,
        )

        assert result.passed is False

    def test_run_test_exception(self, mock_qemu):
        """Test running a test that raises an exception."""
        mock_qemu.run_ssh_command.side_effect = RuntimeError("SSH not available")
        runner = QEMUTestRunner(mock_qemu)

        result = runner._run_test("test_error", "echo test")

        assert result.passed is False
        assert "SSH not available" in result.error

    def test_run_smoke_tests(self, mock_qemu):
        """Test running smoke tests."""
        mock_qemu.run_ssh_command.return_value = (0, "Linux openwrt", "")
        runner = QEMUTestRunner(mock_qemu)

        results = runner.run_smoke_tests()

        assert len(results) > 0
        # Should have run multiple tests
        assert mock_qemu.run_ssh_command.call_count >= 5

    def test_get_summary(self, mock_qemu):
        """Test getting test summary."""
        runner = QEMUTestRunner(mock_qemu)
        runner.results = [
            QEMUTestResult("test1", True, 0.1),
            QEMUTestResult("test2", True, 0.2),
            QEMUTestResult("test3", False, 0.3, error="failed"),
        ]

        summary = runner.get_summary()

        assert summary["total"] == 3
        assert summary["passed"] == 2
        assert summary["failed"] == 1
        assert summary["pass_rate"] == pytest.approx(66.67, rel=0.1)
        assert "test3" in summary["failed_tests"]

    def test_run_system_info(self, mock_qemu):
        """Test collecting system info."""
        mock_qemu.run_ssh_command.side_effect = [
            (0, '{"release":{"distribution":"OpenWrt"}}', ""),
            (0, "Mem:  256  64  192", ""),
            (0, "5.15.0", ""),
            (0, "42", ""),
        ]
        runner = QEMUTestRunner(mock_qemu)

        info = runner.run_system_info()

        assert "kernel_version" in info or "memory" in info or "process_count" in info

    def test_check_kernel_errors_none(self, mock_qemu):
        """Test checking kernel errors when none exist."""
        mock_qemu.run_ssh_command.return_value = (
            0,
            "[ 0.000000] Linux version 5.15.0\n[ 1.000000] Normal boot\n",
            "",
        )
        runner = QEMUTestRunner(mock_qemu)

        errors = runner.check_kernel_errors()

        assert errors == []

    def test_check_kernel_errors_found(self, mock_qemu):
        """Test checking kernel errors when they exist."""
        mock_qemu.run_ssh_command.return_value = (
            0,
            "[ 0.000000] Linux version 5.15.0\n[ 1.000000] Kernel panic - not syncing\n",
            "",
        )
        runner = QEMUTestRunner(mock_qemu)

        errors = runner.check_kernel_errors()

        assert len(errors) > 0
        assert any("panic" in e for e in errors)


class TestRunQemuTests:
    """Tests for run_qemu_tests function."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for tests."""
        tmpdir = tempfile.mkdtemp()
        yield Path(tmpdir)
        shutil.rmtree(tmpdir)

    @pytest.fixture
    def mock_firmware(self, temp_dir):
        """Create a mock firmware file."""
        firmware = temp_dir / "kernel.bin"
        firmware.write_bytes(b"\x00" * 1024)
        return firmware

    @patch("owrt.qemu.QEMURunner")
    def test_run_qemu_tests_boot_failure(self, mock_runner_class, mock_firmware):
        """Test run_qemu_tests when boot fails."""
        mock_runner = MagicMock()
        mock_runner.__enter__ = MagicMock(return_value=mock_runner)
        mock_runner.__exit__ = MagicMock(return_value=False)
        mock_runner.wait_for_boot.return_value = False
        mock_runner_class.return_value = mock_runner

        success, results = run_qemu_tests("armsr-armv8", mock_firmware)

        assert success is False
        assert "error" in results
        assert "Boot timeout" in results["error"]

    @patch("owrt.qemu.QEMURunner")
    def test_run_qemu_tests_ssh_failure(self, mock_runner_class, mock_firmware):
        """Test run_qemu_tests when SSH is not available."""
        mock_runner = MagicMock()
        mock_runner.__enter__ = MagicMock(return_value=mock_runner)
        mock_runner.__exit__ = MagicMock(return_value=False)
        mock_runner.wait_for_boot.return_value = True
        mock_runner.wait_for_ssh.return_value = False
        mock_runner_class.return_value = mock_runner

        success, results = run_qemu_tests("armsr-armv8", mock_firmware)

        assert success is False
        assert "error" in results
        assert "SSH not available" in results["error"]

    @patch("owrt.qemu.QEMURunner")
    @patch("owrt.qemu.QEMUTestRunner")
    def test_run_qemu_tests_success(
        self, mock_test_runner_class, mock_runner_class, mock_firmware
    ):
        """Test run_qemu_tests when all tests pass."""
        # Setup QEMU runner mock
        mock_runner = MagicMock()
        mock_runner.__enter__ = MagicMock(return_value=mock_runner)
        mock_runner.__exit__ = MagicMock(return_value=False)
        mock_runner.wait_for_boot.return_value = True
        mock_runner.wait_for_ssh.return_value = True
        mock_runner_class.return_value = mock_runner

        # Setup test runner mock
        mock_test_runner = MagicMock()
        mock_test_runner.run_smoke_tests.return_value = [
            QEMUTestResult("test1", True, 0.1),
            QEMUTestResult("test2", True, 0.2),
        ]
        mock_test_runner.run_system_info.return_value = {"kernel_version": "5.15.0"}
        mock_test_runner.check_kernel_errors.return_value = []
        mock_test_runner.get_summary.return_value = {
            "total": 2,
            "passed": 2,
            "failed": 0,
            "pass_rate": 100.0,
            "total_time": 0.3,
            "failed_tests": [],
        }
        mock_test_runner_class.return_value = mock_test_runner

        success, results = run_qemu_tests("armsr-armv8", mock_firmware)

        assert success is True
        assert results["summary"]["passed"] == 2
        assert results["summary"]["failed"] == 0

    def test_run_qemu_tests_invalid_target(self, mock_firmware):
        """Test run_qemu_tests with invalid target."""
        success, results = run_qemu_tests("invalid-target", mock_firmware)

        assert success is False
        assert "error" in results
