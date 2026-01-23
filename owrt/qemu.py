"""
QEMU-based runtime testing for OpenWrt images.

Provides utilities to boot OpenWrt images in QEMU and run tests against them.
This module supports both simple smoke tests and integration with the
openwrt-tests framework for comprehensive testing.

Usage:
    # Run basic smoke tests
    python -m owrt test armsr-armv8 --firmware path/to/kernel.bin

    # Run with openwrt-tests framework
    python -m owrt test armsr-armv8 --firmware path/to/kernel.bin --full
"""

import os
import re
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple


@dataclass
class QEMUConfig:
    """QEMU configuration for a target."""
    binary: str
    machine: str
    cpu: str
    memory: str = "256M"
    extra_args: List[str] = field(default_factory=list)
    kernel_arg: str = "-kernel"  # Some targets use -bios
    console: str = "ttyAMA0"  # Serial console device


# QEMU configurations for supported targets
QEMU_TARGETS = {
    "armsr-armv8": QEMUConfig(
        binary="qemu-system-aarch64",
        machine="virt",
        cpu="cortex-a57",
        memory="256M",
        extra_args=[
            "-device", "virtio-rng-pci",
            "-device", "virtio-net-pci,netdev=lan",
        ],
        console="ttyAMA0",
    ),
    "x86-64": QEMUConfig(
        binary="qemu-system-x86_64",
        machine="q35",
        cpu="qemu64",
        memory="512M",
        extra_args=[
            "-enable-kvm",
            "-device", "virtio-rng-pci",
            "-device", "virtio-net-pci,netdev=lan",
        ],
        kernel_arg="-kernel",
        console="ttyS0",
    ),
    "malta-be": QEMUConfig(
        binary="qemu-system-mips",
        machine="malta",
        cpu="24Kc",
        memory="256M",
        extra_args=[
            "-device", "virtio-rng-pci",
            "-device", "pcnet,netdev=lan",
        ],
        console="ttyS0",
    ),
}


@dataclass
class QEMUTestResult:
    """Result of a single QEMU test."""
    name: str
    passed: bool
    duration: float
    output: str = ""
    error: str = ""


class QEMURunner:
    """Manages QEMU instances for testing OpenWrt images."""

    def __init__(
        self,
        target: str,
        firmware: Path,
        verbose: bool = False,
        timeout: int = 120,
    ):
        """Initialize QEMU runner.

        Args:
            target: Target name (e.g., 'armsr-armv8')
            firmware: Path to firmware/kernel image
            verbose: Enable verbose output
            timeout: Boot timeout in seconds
        """
        self.target = target
        self.firmware = Path(firmware)
        self.verbose = verbose
        self.timeout = timeout

        if target not in QEMU_TARGETS:
            raise ValueError(f"Unsupported QEMU target: {target}. "
                           f"Supported: {list(QEMU_TARGETS.keys())}")

        self.config = QEMU_TARGETS[target]
        self.process: Optional[subprocess.Popen] = None
        self.ssh_port: Optional[int] = None
        self._console_log: List[str] = []

    def _find_free_port(self) -> int:
        """Find a free TCP port."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            return s.getsockname()[1]

    def _check_qemu_available(self) -> bool:
        """Check if the required QEMU binary is available."""
        try:
            subprocess.run(
                [self.config.binary, "--version"],
                capture_output=True,
                check=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def start(self) -> bool:
        """Start QEMU with the firmware image.

        Returns:
            True if QEMU started successfully
        """
        if not self._check_qemu_available():
            raise RuntimeError(f"QEMU binary not found: {self.config.binary}")

        if not self.firmware.exists():
            raise FileNotFoundError(f"Firmware not found: {self.firmware}")

        self.ssh_port = self._find_free_port()
        monitor_port = self._find_free_port()

        # Build QEMU command
        cmd = [
            self.config.binary,
            "-M", self.config.machine,
            "-cpu", self.config.cpu,
            "-m", self.config.memory,
            self.config.kernel_arg, str(self.firmware),
            "-nographic",
            "-serial", "stdio",
            "-monitor", f"tcp:127.0.0.1:{monitor_port},server,nowait",
            # Network with SSH port forward
            "-netdev", f"user,id=lan,hostfwd=tcp:127.0.0.1:{self.ssh_port}-:22",
        ]

        # Add extra args
        cmd.extend(self.config.extra_args)

        # Add kernel command line for serial console
        cmd.extend(["-append", f"console={self.config.console}"])

        if self.verbose:
            print(f"Starting QEMU: {' '.join(cmd)}")

        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        return True

    def wait_for_boot(self, timeout: Optional[int] = None) -> bool:
        """Wait for the system to boot and become ready.

        Args:
            timeout: Boot timeout in seconds (default: self.timeout)

        Returns:
            True if system booted successfully
        """
        if not self.process:
            return False

        timeout = timeout or self.timeout
        start_time = time.time()
        boot_complete = False

        # Patterns indicating boot progress/completion
        boot_patterns = [
            r"Please press Enter to activate this console",
            r"BusyBox .* built-in shell",
            r"procd: - init -",
        ]

        while time.time() - start_time < timeout:
            if self.process.poll() is not None:
                if self.verbose:
                    print("QEMU process exited unexpectedly")
                return False

            # Read available output (non-blocking would be better, but this works)
            try:
                # Set a short timeout for reading
                import select
                if select.select([self.process.stdout], [], [], 1.0)[0]:
                    line = self.process.stdout.readline()
                    if line:
                        self._console_log.append(line.rstrip())
                        if self.verbose:
                            print(f"[QEMU] {line.rstrip()}")

                        for pattern in boot_patterns:
                            if re.search(pattern, line):
                                boot_complete = True
                                # Give a bit more time for services to start
                                time.sleep(5)
                                return True
            except Exception:
                pass

        return boot_complete

    def wait_for_ssh(self, timeout: int = 60) -> bool:
        """Wait for SSH to become available.

        Args:
            timeout: SSH timeout in seconds

        Returns:
            True if SSH is available
        """
        if not self.ssh_port:
            return False

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                result = sock.connect_ex(('127.0.0.1', self.ssh_port))
                sock.close()
                if result == 0:
                    # Give SSH daemon a moment to fully initialize
                    time.sleep(2)
                    return True
            except Exception:
                pass
            time.sleep(1)

        return False

    def run_ssh_command(
        self,
        command: str,
        timeout: int = 30,
    ) -> Tuple[int, str, str]:
        """Run a command via SSH.

        Args:
            command: Command to run
            timeout: Command timeout in seconds

        Returns:
            Tuple of (exit_code, stdout, stderr)
        """
        if not self.ssh_port:
            raise RuntimeError("QEMU not started or SSH port not available")

        ssh_cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
            "-o", "BatchMode=yes",
            "-p", str(self.ssh_port),
            "root@127.0.0.1",
            command,
        ]

        try:
            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Command timed out"
        except Exception as e:
            return -1, "", str(e)

    def send_console_command(self, command: str):
        """Send a command to the serial console.

        Args:
            command: Command to send
        """
        if self.process and self.process.stdin:
            self.process.stdin.write(command + "\n")
            self.process.stdin.flush()

    def stop(self):
        """Stop the QEMU instance."""
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None

    def get_console_log(self) -> List[str]:
        """Get the captured console log."""
        return self._console_log.copy()

    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop()
        return False


class QEMUTestRunner:
    """Runs tests against a QEMU OpenWrt instance."""

    def __init__(self, qemu: QEMURunner, verbose: bool = False):
        """Initialize test runner.

        Args:
            qemu: QEMURunner instance
            verbose: Enable verbose output
        """
        self.qemu = qemu
        self.verbose = verbose
        self.results: List[QEMUTestResult] = []

    def _run_test(
        self,
        name: str,
        command: str,
        check_fn: Optional[callable] = None,
    ) -> QEMUTestResult:
        """Run a single test.

        Args:
            name: Test name
            command: SSH command to run
            check_fn: Optional function to validate output

        Returns:
            QEMUTestResult
        """
        start = time.time()
        try:
            exit_code, stdout, stderr = self.qemu.run_ssh_command(command)
            duration = time.time() - start

            if check_fn:
                passed = check_fn(exit_code, stdout, stderr)
            else:
                passed = exit_code == 0

            return QEMUTestResult(
                name=name,
                passed=passed,
                duration=duration,
                output=stdout,
                error=stderr if not passed else "",
            )
        except Exception as e:
            return QEMUTestResult(
                name=name,
                passed=False,
                duration=time.time() - start,
                error=str(e),
            )

    def run_smoke_tests(self) -> List[QEMUTestResult]:
        """Run basic smoke tests.

        Returns:
            List of TestResult
        """
        tests = [
            # Basic system tests
            ("test_shell", "true", None),
            ("test_uname", "uname -a", lambda c, o, e: c == 0 and "Linux" in o),
            ("test_busybox", "busybox --help", lambda c, o, e: c == 0 and "BusyBox" in o),

            # Init system
            ("test_procd", "pidof procd", lambda c, o, e: c == 0 and o.strip().isdigit()),

            # Network
            ("test_loopback", "ip addr show lo", lambda c, o, e: c == 0 and "127.0.0.1" in o),
            ("test_lan_interface", "ip addr show br-lan 2>/dev/null || ip addr show eth0",
             lambda c, o, e: c == 0),

            # Services
            ("test_dropbear", "pidof dropbear", lambda c, o, e: c == 0),
            ("test_ubus", "ubus list", lambda c, o, e: c == 0 and "system" in o),

            # Filesystem
            ("test_proc", "ls /proc/version", lambda c, o, e: c == 0),
            ("test_sys", "ls /sys/class/net", lambda c, o, e: c == 0),

            # Memory
            ("test_memory", "free", lambda c, o, e: c == 0 and "Mem:" in o),

            # Package manager
            ("test_apk", "apk --version 2>/dev/null || opkg --version",
             lambda c, o, e: c == 0),
        ]

        self.results = []
        for name, command, check_fn in tests:
            if self.verbose:
                print(f"  Running {name}...", end=" ", flush=True)

            result = self._run_test(name, command, check_fn)
            self.results.append(result)

            if self.verbose:
                status = "PASS" if result.passed else "FAIL"
                print(f"{status} ({result.duration:.2f}s)")
                if not result.passed and result.error:
                    print(f"    Error: {result.error}")

        return self.results

    def run_system_info(self) -> Dict[str, Any]:
        """Collect system information.

        Returns:
            Dict with system info
        """
        info = {}

        # Get board info via ubus
        code, out, _ = self.qemu.run_ssh_command(
            "ubus call system board 2>/dev/null"
        )
        if code == 0:
            import json
            try:
                info["board"] = json.loads(out)
            except json.JSONDecodeError:
                pass

        # Get memory info
        code, out, _ = self.qemu.run_ssh_command("free -m")
        if code == 0:
            lines = out.strip().split("\n")
            if len(lines) >= 2:
                parts = lines[1].split()
                if len(parts) >= 4:
                    info["memory"] = {
                        "total_mb": int(parts[1]),
                        "used_mb": int(parts[2]),
                        "free_mb": int(parts[3]),
                    }

        # Get kernel version
        code, out, _ = self.qemu.run_ssh_command("uname -r")
        if code == 0:
            info["kernel_version"] = out.strip()

        # Get process count
        code, out, _ = self.qemu.run_ssh_command("ps | wc -l")
        if code == 0:
            info["process_count"] = int(out.strip())

        return info

    def check_kernel_errors(self) -> List[str]:
        """Check dmesg for kernel errors.

        Returns:
            List of error messages found
        """
        error_patterns = [
            r"Oops:",
            r"BUG:",
            r"corruption",
            r"Kernel panic",
            r"Out of memory",
            r"segfault",
            r"Unable to handle kernel",
        ]

        code, dmesg, _ = self.qemu.run_ssh_command("dmesg")
        if code != 0:
            return ["Failed to read dmesg"]

        errors = []
        for pattern in error_patterns:
            matches = re.findall(f".*{pattern}.*", dmesg)
            errors.extend(matches)

        return errors

    def get_summary(self) -> Dict[str, Any]:
        """Get test summary.

        Returns:
            Dict with summary statistics
        """
        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)
        total_time = sum(r.duration for r in self.results)

        return {
            "total": len(self.results),
            "passed": passed,
            "failed": failed,
            "pass_rate": (passed / len(self.results) * 100) if self.results else 0,
            "total_time": total_time,
            "failed_tests": [r.name for r in self.results if not r.passed],
        }


def run_qemu_tests(
    target: str,
    firmware: Path,
    verbose: bool = False,
    timeout: int = 180,
) -> Tuple[bool, Dict[str, Any]]:
    """Run QEMU tests on a firmware image.

    Args:
        target: Target name (e.g., 'armsr-armv8')
        firmware: Path to firmware image
        verbose: Enable verbose output
        timeout: Boot timeout in seconds

    Returns:
        Tuple of (success, results_dict)
    """
    results = {
        "target": target,
        "firmware": str(firmware),
        "tests": [],
        "system_info": {},
        "kernel_errors": [],
        "summary": {},
    }

    try:
        if verbose:
            print(f"Starting QEMU for {target}...")

        with QEMURunner(target, firmware, verbose=verbose, timeout=timeout) as qemu:
            if verbose:
                print("Waiting for boot...")

            if not qemu.wait_for_boot():
                results["error"] = "Boot timeout"
                return False, results

            if verbose:
                print("Waiting for SSH...")

            if not qemu.wait_for_ssh():
                results["error"] = "SSH not available"
                return False, results

            if verbose:
                print("\nRunning smoke tests...")

            runner = QEMUTestRunner(qemu, verbose=verbose)

            # Run smoke tests
            test_results = runner.run_smoke_tests()
            results["tests"] = [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "duration": r.duration,
                    "error": r.error,
                }
                for r in test_results
            ]

            # Collect system info
            if verbose:
                print("\nCollecting system info...")
            results["system_info"] = runner.run_system_info()

            # Check for kernel errors
            if verbose:
                print("Checking for kernel errors...")
            results["kernel_errors"] = runner.check_kernel_errors()

            # Generate summary
            results["summary"] = runner.get_summary()

            success = results["summary"]["failed"] == 0 and not results["kernel_errors"]

            if verbose:
                print(f"\n{'=' * 50}")
                print(f"Results: {results['summary']['passed']}/{results['summary']['total']} passed")
                if results["kernel_errors"]:
                    print(f"Kernel errors: {len(results['kernel_errors'])}")
                print(f"{'=' * 50}")

            return success, results

    except Exception as e:
        results["error"] = str(e)
        return False, results
