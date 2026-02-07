# SPDX-License-Identifier: GPL-2.0-only
"""
YAML-based build configuration loader and validator.

Loads the build pipeline definition from stages.yaml and merges with
user configuration from config.yaml or CLI overrides.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:
    yaml = None


@dataclass
class StageConfig:
    """Configuration for a single build stage."""

    name: str
    containerfile: str = ""
    base_image: str = ""
    depends_on: list[str] = field(default_factory=list)
    source_dirs: list[str] = field(default_factory=list)
    exclude_dirs: list[str] = field(default_factory=list)
    cache_mounts: list[dict[str, str]] = field(default_factory=list)
    build_args: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    parallel: bool = False
    timeout_minutes: int = 120
    outputs: list[str] = field(default_factory=list)


@dataclass
class TargetConfig:
    """Target board/architecture configuration."""

    board: str = ""
    subtarget: str = ""
    arch: str = ""
    arch_packages: str = ""
    kernel_version: str = ""
    profiles: list[str] = field(default_factory=list)


@dataclass
class CacheConfig:
    """Cache system configuration."""

    root: str = ".build-cache"
    cas_max_size_gb: float = 50.0
    ac_ttl_days: int = 30
    ccache_size_gb: float = 10.0
    registry: str = ""
    registry_prefix: str = ""
    enable_remote: bool = False
    compression: str = "zstd"


@dataclass
class BuildConfig:
    """Complete build system configuration."""

    topdir: str = ""
    stages: dict[str, StageConfig] = field(default_factory=dict)
    target: TargetConfig = field(default_factory=TargetConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    container_runtime: str = "podman"  # podman, docker, buildah
    parallel_jobs: int = 0  # 0 = auto-detect
    dl_dir: str = "dl"
    output_dir: str = "bin"
    verbose: bool = False
    reproducible: bool = True
    sign_packages: bool = False

    def resolve_paths(self) -> None:
        """Resolve relative paths to absolute based on topdir."""
        if not self.topdir:
            return
        for attr in ("dl_dir", "output_dir"):
            val = getattr(self, attr)
            if val and not os.path.isabs(val):
                setattr(self, attr, os.path.join(self.topdir, val))
        if not os.path.isabs(self.cache.root):
            self.cache.root = os.path.join(self.topdir, self.cache.root)


def _load_yaml_file(path: str) -> dict:
    """Load a YAML file, with fallback for missing PyYAML."""
    if yaml is None:
        # Minimal YAML subset parser for when PyYAML isn't installed
        return _parse_simple_yaml(path)
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


def _parse_simple_yaml(path: str) -> dict:
    """Minimal YAML parser for bootstrap (before PyYAML is available).

    Handles simple key: value pairs, lists, and one level of nesting.
    Not a full YAML parser - just enough to read our config files.
    """
    result = {}
    current_key = None
    current_dict = result
    indent_stack = [(0, result)]

    with open(path, "r") as f:
        for line in f:
            stripped = line.rstrip()
            if not stripped or stripped.lstrip().startswith("#"):
                continue

            indent = len(line) - len(line.lstrip())
            content = stripped.strip()

            # Handle list items
            if content.startswith("- "):
                value = content[2:].strip()
                if current_key and isinstance(current_dict.get(current_key), list):
                    if ":" in value and not value.startswith('"'):
                        # Inline dict in list
                        item = {}
                        for part in value.split(","):
                            part = part.strip()
                            if ":" in part:
                                k, v = part.split(":", 1)
                                item[k.strip()] = v.strip().strip('"').strip("'")
                        current_dict[current_key].append(item)
                    else:
                        current_dict[current_key].append(
                            value.strip('"').strip("'")
                        )
                continue

            if ":" not in content:
                continue

            key, _, value = content.partition(":")
            key = key.strip()
            value = value.strip()

            # Pop indent stack
            while indent_stack and indent <= indent_stack[-1][0] and len(indent_stack) > 1:
                indent_stack.pop()
            current_dict = indent_stack[-1][1]

            if not value:
                # Start of a nested dict or list
                # Peek at next line to determine type
                new_dict = {}
                current_dict[key] = new_dict
                indent_stack.append((indent + 2, new_dict))
                current_key = key
            elif value == "[]":
                current_dict[key] = []
                current_key = key
            else:
                # Strip quotes
                value = value.strip('"').strip("'")
                # Type coercion
                if value.lower() in ("true", "yes"):
                    value = True
                elif value.lower() in ("false", "no"):
                    value = False
                elif value.isdigit():
                    value = int(value)
                else:
                    try:
                        value = float(value)
                    except ValueError:
                        pass
                current_dict[key] = value
                current_key = key

    return result


def _dict_to_stage_config(name: str, d: dict) -> StageConfig:
    """Convert a dict from YAML to a StageConfig."""
    return StageConfig(
        name=name,
        containerfile=d.get("containerfile", ""),
        base_image=d.get("base_image", ""),
        depends_on=d.get("depends_on", []),
        source_dirs=d.get("source_dirs", []),
        exclude_dirs=d.get("exclude_dirs", []),
        cache_mounts=d.get("cache_mounts", []),
        build_args=d.get("build_args", {}),
        env=d.get("env", {}),
        parallel=d.get("parallel", False),
        timeout_minutes=d.get("timeout_minutes", 120),
        outputs=d.get("outputs", []),
    )


def load_config(
    config_path: str,
    stages_path: str,
    topdir: str,
    overrides: Optional[dict] = None,
) -> BuildConfig:
    """Load and merge configuration from YAML files.

    Args:
        config_path: Path to config.yaml (user settings).
        stages_path: Path to stages.yaml (pipeline definition).
        topdir: OpenWrt source tree root.
        overrides: CLI argument overrides.

    Returns:
        Merged BuildConfig.
    """
    config_data = _load_yaml_file(config_path) if os.path.exists(config_path) else {}
    stages_data = _load_yaml_file(stages_path) if os.path.exists(stages_path) else {}

    # Parse target config
    target_raw = config_data.get("target", {})
    target = TargetConfig(
        board=target_raw.get("board", ""),
        subtarget=target_raw.get("subtarget", ""),
        arch=target_raw.get("arch", ""),
        arch_packages=target_raw.get("arch_packages", ""),
        kernel_version=target_raw.get("kernel_version", ""),
        profiles=target_raw.get("profiles", []),
    )

    # Parse cache config
    cache_raw = config_data.get("cache", {})
    cache = CacheConfig(
        root=cache_raw.get("root", ".build-cache"),
        cas_max_size_gb=cache_raw.get("cas_max_size_gb", 50.0),
        ac_ttl_days=cache_raw.get("ac_ttl_days", 30),
        ccache_size_gb=cache_raw.get("ccache_size_gb", 10.0),
        registry=cache_raw.get("registry", ""),
        registry_prefix=cache_raw.get("registry_prefix", ""),
        enable_remote=cache_raw.get("enable_remote", False),
        compression=cache_raw.get("compression", "zstd"),
    )

    # Parse stage configs
    stages = {}
    for stage_name, stage_data in stages_data.get("stages", {}).items():
        if isinstance(stage_data, dict):
            stages[stage_name] = _dict_to_stage_config(stage_name, stage_data)

    # Build final config
    cfg = BuildConfig(
        topdir=topdir,
        stages=stages,
        target=target,
        cache=cache,
        container_runtime=config_data.get("container_runtime", "podman"),
        parallel_jobs=config_data.get("parallel_jobs", 0),
        dl_dir=config_data.get("dl_dir", "dl"),
        output_dir=config_data.get("output_dir", "bin"),
        verbose=config_data.get("verbose", False),
        reproducible=config_data.get("reproducible", True),
    )

    # Apply CLI overrides
    if overrides:
        for key, value in overrides.items():
            if hasattr(cfg, key) and value is not None:
                setattr(cfg, key, value)

    cfg.resolve_paths()
    return cfg


def load_openwrt_dotconfig(dotconfig_path: str) -> dict[str, str]:
    """Parse OpenWrt .config file into a dict of CONFIG_* values."""
    config = {}
    if not os.path.exists(dotconfig_path):
        return config

    with open(dotconfig_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                # Check for "# CONFIG_FOO is not set"
                if line.startswith("# CONFIG_") and line.endswith(" is not set"):
                    key = line.split()[1]
                    config[key] = "n"
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                config[key.strip()] = value.strip().strip('"')

    return config


def extract_target_from_dotconfig(config: dict[str, str]) -> TargetConfig:
    """Extract target information from a parsed .config."""
    return TargetConfig(
        board=config.get("CONFIG_TARGET_BOARD", "").strip('"'),
        subtarget=config.get("CONFIG_TARGET_SUBTARGET", "").strip('"'),
        arch=config.get("CONFIG_ARCH", "").strip('"'),
        arch_packages=config.get("CONFIG_TARGET_ARCH_PACKAGES", "").strip('"'),
    )
