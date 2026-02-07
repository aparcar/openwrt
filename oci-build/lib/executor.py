# SPDX-License-Identifier: GPL-2.0-only
"""
Parallel build executor with resource management.

Manages concurrent builds across multiple workers, respecting:
- Dependency ordering (topological waves)
- Resource limits (CPU, memory, I/O)
- Failure propagation (stop on error or continue)
- Progress reporting

Uses a work-stealing approach: when a worker finishes a package,
it picks up the next available package from the current wave.
"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock, Event
from typing import Callable, Optional


class BuildStatus(Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    CACHED = "cached"


@dataclass
class BuildJob:
    """A single build job to execute."""

    name: str
    wave: int = 0
    status: BuildStatus = BuildStatus.PENDING
    depends_on: list[str] = field(default_factory=list)
    result: Optional[object] = None
    start_time: float = 0.0
    end_time: float = 0.0
    worker_id: int = -1

    @property
    def duration(self) -> float:
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return 0.0


class ProgressReporter:
    """Reports build progress to stderr."""

    def __init__(self, total_jobs: int, total_waves: int):
        self._total = total_jobs
        self._waves = total_waves
        self._completed = 0
        self._cached = 0
        self._failed = 0
        self._lock = Lock()
        self._start_time = time.monotonic()
        self._active: dict[int, str] = {}  # worker_id -> job_name

    def job_started(self, job: BuildJob) -> None:
        with self._lock:
            self._active[job.worker_id] = job.name
            self._print_status(f"building {job.name}")

    def job_completed(self, job: BuildJob) -> None:
        with self._lock:
            self._completed += 1
            if job.status == BuildStatus.CACHED:
                self._cached += 1
            self._active.pop(job.worker_id, None)
            elapsed = time.monotonic() - self._start_time
            self._print_status(
                f"{job.name} done ({job.duration:.1f}s) "
                f"[{self._completed}/{self._total}]"
            )

    def job_failed(self, job: BuildJob, error: str) -> None:
        with self._lock:
            self._completed += 1
            self._failed += 1
            self._active.pop(job.worker_id, None)
            self._print_status(f"FAILED {job.name}: {error}")

    def wave_completed(self, wave_idx: int, wave_size: int, duration: float) -> None:
        with self._lock:
            self._print_status(
                f"wave {wave_idx + 1}/{self._waves}: "
                f"{wave_size} jobs in {duration:.1f}s"
            )

    def _print_status(self, msg: str) -> None:
        elapsed = time.monotonic() - self._start_time
        active_str = ", ".join(sorted(self._active.values()))
        line = f"[{elapsed:6.1f}s] {msg}"
        if active_str and len(self._active) > 1:
            line += f" | active: {active_str}"
        print(line, file=sys.stderr)

    def summary(self) -> str:
        elapsed = time.monotonic() - self._start_time
        return (
            f"Completed {self._completed}/{self._total} jobs in {elapsed:.1f}s "
            f"({self._cached} cached, {self._failed} failed)"
        )


class ParallelExecutor:
    """Executes build jobs in parallel waves with dependency tracking.

    Architecture:
    - Jobs are grouped into waves (topological sort of dependency graph)
    - All jobs in a wave can execute concurrently
    - A wave must complete before the next wave starts
    - Within a wave, a thread pool distributes work across workers
    - Failed jobs optionally stop the entire build (fail-fast)
    """

    def __init__(
        self,
        max_workers: int = 0,
        fail_fast: bool = True,
        verbose: bool = False,
    ):
        self.max_workers = max_workers or os.cpu_count() or 1
        self.fail_fast = fail_fast
        self.verbose = verbose
        self._stop_event = Event()
        self._jobs: dict[str, BuildJob] = {}

    def execute(
        self,
        waves: list[list[str]],
        build_fn: Callable[[str], object],
        cache_check_fn: Optional[Callable[[str], Optional[object]]] = None,
    ) -> dict[str, BuildJob]:
        """Execute build jobs organized in dependency waves.

        Args:
            waves: List of waves, each a list of job names.
            build_fn: Function to build a single job. Called with job name,
                      returns a result object or raises on failure.
            cache_check_fn: Optional function to check if a job is cached.
                           Returns cached result or None.

        Returns:
            Dict of job_name -> BuildJob with results.
        """
        # Create all jobs
        total = sum(len(w) for w in waves)
        for wave_idx, wave in enumerate(waves):
            for name in wave:
                self._jobs[name] = BuildJob(name=name, wave=wave_idx)

        reporter = ProgressReporter(total, len(waves))
        self._stop_event.clear()

        for wave_idx, wave in enumerate(waves):
            if self._stop_event.is_set():
                # Mark remaining as skipped
                for remaining_wave in waves[wave_idx:]:
                    for name in remaining_wave:
                        job = self._jobs[name]
                        if job.status == BuildStatus.PENDING:
                            job.status = BuildStatus.SKIPPED
                break

            wave_start = time.monotonic()
            self._execute_wave(
                wave, wave_idx, build_fn, cache_check_fn, reporter
            )
            wave_duration = time.monotonic() - wave_start
            reporter.wave_completed(wave_idx, len(wave), wave_duration)

        if self.verbose:
            print(reporter.summary(), file=sys.stderr)

        return self._jobs

    def _execute_wave(
        self,
        wave: list[str],
        wave_idx: int,
        build_fn: Callable[[str], object],
        cache_check_fn: Optional[Callable[[str], Optional[object]]],
        reporter: ProgressReporter,
    ) -> None:
        """Execute a single wave of parallel builds."""
        if not wave:
            return

        workers = min(self.max_workers, len(wave))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures: dict[Future, BuildJob] = {}

            for idx, name in enumerate(wave):
                if self._stop_event.is_set():
                    break

                job = self._jobs[name]
                job.status = BuildStatus.QUEUED
                job.worker_id = idx % workers

                future = pool.submit(
                    self._run_job, job, build_fn, cache_check_fn, reporter
                )
                futures[future] = job

            for future in as_completed(futures):
                job = futures[future]
                try:
                    future.result()
                except Exception as e:
                    job.status = BuildStatus.FAILED
                    job.result = str(e)
                    reporter.job_failed(job, str(e))
                    if self.fail_fast:
                        self._stop_event.set()

    def _run_job(
        self,
        job: BuildJob,
        build_fn: Callable[[str], object],
        cache_check_fn: Optional[Callable[[str], Optional[object]]],
        reporter: ProgressReporter,
    ) -> None:
        """Run a single build job."""
        if self._stop_event.is_set():
            job.status = BuildStatus.SKIPPED
            return

        # Check cache first
        if cache_check_fn:
            cached = cache_check_fn(job.name)
            if cached is not None:
                job.status = BuildStatus.CACHED
                job.result = cached
                reporter.job_completed(job)
                return

        # Build
        job.status = BuildStatus.RUNNING
        job.start_time = time.monotonic()
        reporter.job_started(job)

        try:
            result = build_fn(job.name)
            job.end_time = time.monotonic()
            job.status = BuildStatus.SUCCESS
            job.result = result
            reporter.job_completed(job)
        except Exception as e:
            job.end_time = time.monotonic()
            job.status = BuildStatus.FAILED
            job.result = str(e)
            reporter.job_failed(job, str(e))
            if self.fail_fast:
                self._stop_event.set()
            raise
