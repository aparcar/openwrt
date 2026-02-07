# SPDX-License-Identifier: GPL-2.0-only
"""
CLI interface for the OpenWrt OCI build system.

Commands:
  build     - Run the full build pipeline (or specific stages)
  packages  - Build packages incrementally
  cache     - Manage the build cache (inspect, clean, gc)
  graph     - Dependency graph operations (show, dot export)
  status    - Show build status and cache statistics
"""

import argparse
import json
import os
import sys
from typing import Optional

from . import __version__, STAGES


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="owrt-build",
        description="OpenWrt OCI Build System - Fast, reproducible, incremental builds",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  owrt-build build                    Build all stages
  owrt-build build --stages tools     Build only host tools
  owrt-build build --force kernel     Force rebuild kernel stage
  owrt-build packages                 Incremental package build
  owrt-build packages --only luci     Build only luci + deps
  owrt-build cache status             Show cache statistics
  owrt-build cache gc                 Garbage collect old entries
  owrt-build graph show               Show dependency graph summary
  owrt-build status                   Show overall build status
""",
    )

    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "-C",
        "--directory",
        default=".",
        help="OpenWrt source directory (default: current dir)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config.yaml (default: oci-build/config.yaml)",
    )
    parser.add_argument(
        "--runtime",
        choices=["podman", "docker", "buildah"],
        default=None,
        help="Container runtime (default: auto-detect)",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=None,
        help="Parallel build jobs (default: auto)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose output"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be built without executing",
    )

    subparsers = parser.add_subparsers(dest="command", help="Build commands")

    # ---- build command ----
    build_parser = subparsers.add_parser(
        "build", help="Run the build pipeline"
    )
    build_parser.add_argument(
        "--stages",
        nargs="+",
        choices=list(STAGES),
        default=None,
        help="Specific stages to build (default: all)",
    )
    build_parser.add_argument(
        "--force",
        nargs="*",
        default=None,
        help="Force rebuild specific stages (skip cache)",
    )
    build_parser.add_argument(
        "--from-stage",
        choices=list(STAGES),
        default=None,
        help="Start from this stage (skip earlier stages)",
    )
    build_parser.add_argument(
        "--to-stage",
        choices=list(STAGES),
        default=None,
        help="Stop after this stage",
    )

    # ---- packages command ----
    pkg_parser = subparsers.add_parser(
        "packages", help="Incremental package builds"
    )
    pkg_parser.add_argument(
        "--only",
        nargs="+",
        default=None,
        help="Build only these packages (+ dependencies)",
    )
    pkg_parser.add_argument(
        "--force",
        nargs="*",
        default=None,
        help="Force rebuild specific packages",
    )
    pkg_parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of parallel package build workers",
    )
    pkg_parser.add_argument(
        "--no-deps",
        action="store_true",
        help="Don't rebuild reverse dependencies",
    )

    # ---- cache command ----
    cache_parser = subparsers.add_parser(
        "cache", help="Cache management"
    )
    cache_sub = cache_parser.add_subparsers(dest="cache_command")

    cache_sub.add_parser("status", help="Show cache statistics")
    cache_sub.add_parser("gc", help="Garbage collect unused entries")
    cache_sub.add_parser("clean", help="Remove all cache data")

    cache_inspect = cache_sub.add_parser(
        "inspect", help="Inspect a cache entry"
    )
    cache_inspect.add_argument(
        "identity", help="Build identity hash to inspect"
    )

    # ---- graph command ----
    graph_parser = subparsers.add_parser(
        "graph", help="Dependency graph operations"
    )
    graph_sub = graph_parser.add_subparsers(dest="graph_command")

    graph_sub.add_parser("show", help="Show graph summary")
    graph_sub.add_parser("waves", help="Show parallel build waves")
    graph_sub.add_parser("cycles", help="Check for dependency cycles")

    graph_deps = graph_sub.add_parser(
        "deps", help="Show dependencies for a package"
    )
    graph_deps.add_argument("package", help="Package name")
    graph_deps.add_argument(
        "--reverse", action="store_true", help="Show reverse dependencies"
    )
    graph_deps.add_argument(
        "--transitive", action="store_true", help="Include transitive deps"
    )

    graph_dot = graph_sub.add_parser(
        "dot", help="Export graph as DOT format"
    )
    graph_dot.add_argument(
        "-o", "--output", default="-", help="Output file (default: stdout)"
    )

    # ---- status command ----
    subparsers.add_parser("status", help="Show build status")

    # ---- identity command ----
    id_parser = subparsers.add_parser(
        "identity", help="Compute build identity for a stage or package"
    )
    id_parser.add_argument(
        "target", help="Stage name or package path"
    )
    id_parser.add_argument(
        "--components",
        action="store_true",
        help="Show identity components (for debugging)",
    )

    return parser


def resolve_topdir(directory: str) -> str:
    """Find the OpenWrt source tree root."""
    path = os.path.abspath(directory)

    # Check if this is an OpenWrt source tree
    indicators = ["rules.mk", "include/toplevel.mk", "Config.in"]
    if all(os.path.exists(os.path.join(path, f)) for f in indicators):
        return path

    # Try parent directories
    parent = os.path.dirname(path)
    if parent != path:
        return resolve_topdir(parent)

    print(
        "Error: Not inside an OpenWrt source tree. "
        "Use -C to specify the directory.",
        file=sys.stderr,
    )
    sys.exit(1)


def cmd_build(args, config):
    """Execute the build pipeline."""
    from .cache import BuildCache
    from .oci import OCIRuntime
    from .stage import PipelineExecutor

    cache = BuildCache(config.cache.root)
    oci = OCIRuntime(
        runtime=config.container_runtime,
        verbose=config.verbose,
        dry_run=args.dry_run,
    )

    executor = PipelineExecutor(config, cache, oci)

    # Determine stages to build
    stages = args.stages
    if stages is None:
        stages = list(STAGES)

    if args.from_stage:
        start_idx = list(STAGES).index(args.from_stage)
        stages = [s for s in stages if list(STAGES).index(s) >= start_idx]

    if args.to_stage:
        end_idx = list(STAGES).index(args.to_stage)
        stages = [s for s in stages if list(STAGES).index(s) <= end_idx]

    # Force-rebuild stages
    force_stages = set(args.force) if args.force else set()

    results = executor.run(stages=stages, force_stages=force_stages)

    # Exit code based on results
    if any(not h and not hit for hit, h in results.values()):
        return 1
    return 0


def cmd_packages(args, config):
    """Execute incremental package builds."""
    from .cache import BuildCache
    from .oci import OCIRuntime
    from .packages import build_packages_incremental

    cache = BuildCache(config.cache.root)
    oci = OCIRuntime(
        runtime=config.container_runtime,
        verbose=config.verbose,
        dry_run=args.dry_run,
    )

    force = set(args.force) if args.force else None
    results = build_packages_incremental(
        config=config,
        cache=cache,
        oci=oci,
        packages=args.only,
        force=force,
    )

    failed = sum(1 for r in results.values() if not r.success)
    return 1 if failed > 0 else 0


def cmd_cache(args, config):
    """Cache management commands."""
    from .cache import BuildCache

    cache = BuildCache(config.cache.root)

    if args.cache_command == "status":
        _cache_status(cache, config)
    elif args.cache_command == "gc":
        _cache_gc(cache)
    elif args.cache_command == "clean":
        _cache_clean(config)
    elif args.cache_command == "inspect":
        _cache_inspect(cache, args.identity)
    else:
        print("Usage: owrt-build cache {status|gc|clean|inspect}", file=sys.stderr)
        return 1

    return 0


def _cache_status(cache, config):
    """Show cache statistics."""
    ac_entries = cache.ac.list_entries()

    # Compute CAS size
    cas_size = 0
    cas_count = 0
    cas_root = cache.cas.root
    if os.path.exists(cas_root):
        for dirpath, _, filenames in os.walk(cas_root):
            for f in filenames:
                fpath = os.path.join(dirpath, f)
                cas_size += os.path.getsize(fpath)
                cas_count += 1

    # Stage hashes
    stage_info = {}
    for stage in STAGES:
        h = cache.get_stage_hash(stage)
        if h:
            stage_info[stage] = h[:16]

    print("Build Cache Status")
    print("=" * 40)
    print(f"  Cache root:    {config.cache.root}")
    print(f"  Action cache:  {len(ac_entries)} entries")
    print(f"  Content store: {cas_count} blobs ({cas_size / 1024 / 1024:.1f} MB)")
    print()
    if stage_info:
        print("Last successful builds:")
        for stage, h in stage_info.items():
            print(f"  {stage:12s} {h}")
    else:
        print("No previous builds recorded.")


def _cache_gc(cache):
    """Garbage collect unused CAS blobs."""
    # Collect all referenced hashes from AC
    referenced = set()
    for entry_id in cache.ac.list_entries():
        manifest = cache.ac.lookup(entry_id)
        if manifest:
            referenced.update(manifest.outputs.values())

    freed = cache.cas.gc(referenced)
    print(f"Garbage collected {freed / 1024 / 1024:.1f} MB")


def _cache_clean(config):
    """Remove all cache data."""
    import shutil

    cache_root = config.cache.root
    if os.path.exists(cache_root):
        shutil.rmtree(cache_root)
        print(f"Removed {cache_root}")
    else:
        print("Cache directory does not exist.")


def _cache_inspect(cache, identity):
    """Inspect a cache entry."""
    manifest = cache.ac.lookup(identity)
    if manifest is None:
        print(f"No cache entry for identity: {identity}", file=sys.stderr)
        return

    print(json.dumps(json.loads(manifest.to_json()), indent=2))


def cmd_graph(args, config):
    """Dependency graph operations."""
    from .graph import DependencyGraph
    from .packages import PackageScanner

    scanner = PackageScanner(config.topdir)
    graph = scanner.scan()

    if args.graph_command == "show":
        stats = graph.summary()
        print("Package Dependency Graph")
        print("=" * 40)
        print(f"  Packages:      {stats['nodes']}")
        print(f"  Dependencies:  {stats['edges']}")
        print(f"  Build waves:   {stats['waves']}")
        print(f"  Max parallel:  {stats['max_wave_width']}")

    elif args.graph_command == "waves":
        waves = graph.topological_waves()
        for i, wave in enumerate(waves):
            print(f"Wave {i + 1} ({len(wave)} packages): {', '.join(wave[:10])}")
            if len(wave) > 10:
                print(f"  ... and {len(wave) - 10} more")

    elif args.graph_command == "cycles":
        cycles = graph.detect_cycles()
        if cycles:
            print(f"Found {len(cycles)} dependency cycles:")
            for cycle in cycles[:10]:
                print(f"  {' -> '.join(cycle)}")
        else:
            print("No dependency cycles found.")

    elif args.graph_command == "deps":
        node = graph.get_node(args.package)
        if not node:
            print(f"Package not found: {args.package}", file=sys.stderr)
            return 1

        if args.reverse:
            if args.transitive:
                deps = graph.transitive_rdeps(args.package)
            else:
                deps = graph.reverse_dependencies(args.package)
            label = "Reverse dependencies"
        else:
            if args.transitive:
                deps = graph.transitive_deps(args.package)
            else:
                deps = graph.dependencies(args.package)
            label = "Dependencies"

        print(f"{label} of {args.package} ({len(deps)}):")
        for dep in sorted(deps):
            print(f"  {dep}")

    elif args.graph_command == "dot":
        _export_dot(graph, args.output)

    else:
        print("Usage: owrt-build graph {show|waves|cycles|deps|dot}", file=sys.stderr)
        return 1

    return 0


def _export_dot(graph, output_path):
    """Export dependency graph as Graphviz DOT format."""
    lines = ["digraph packages {", "  rankdir=LR;", "  node [shape=box];"]

    for name, node in graph.nodes.items():
        for dep in node.all_deps:
            if dep in graph.nodes:
                lines.append(f'  "{name}" -> "{dep}";')

    lines.append("}")
    content = "\n".join(lines)

    if output_path == "-":
        print(content)
    else:
        with open(output_path, "w") as f:
            f.write(content)
        print(f"Exported to {output_path}", file=sys.stderr)


def cmd_status(args, config):
    """Show overall build status."""
    from .cache import BuildCache

    cache = BuildCache(config.cache.root)

    print("OpenWrt OCI Build Status")
    print("=" * 50)
    print(f"  Source:   {config.topdir}")
    print(f"  Target:   {config.target.board}/{config.target.subtarget}")
    print(f"  Arch:     {config.target.arch}")
    print(f"  Runtime:  {config.container_runtime}")
    print(f"  Jobs:     {config.parallel_jobs or os.cpu_count()}")
    print()

    print("Stage Status:")
    for stage in STAGES:
        h = cache.get_stage_hash(stage)
        if h:
            print(f"  {stage:12s}  built  {h[:16]}")
        else:
            print(f"  {stage:12s}  --")

    return 0


def cmd_identity(args, config):
    """Compute and display build identity."""
    from .cache import BuildCache
    from .hasher import BuildIdentity, MerkleTree, MtimeIndex
    from .oci import OCIRuntime
    from .stage import StageExecutor

    cache = BuildCache(config.cache.root)
    mtime_path = os.path.join(config.cache.root, "mtime.json")
    mtime_index = MtimeIndex(mtime_path).load()
    oci = OCIRuntime(runtime=config.container_runtime, dry_run=True)

    executor = StageExecutor(config, cache, oci, mtime_index)

    if args.target in STAGES:
        stage_cfg = config.stages.get(args.target)
        if stage_cfg:
            bid = executor._compute_identity(args.target, stage_cfg)
            print(f"Build identity for stage '{args.target}':")
            print(f"  Hash: {bid.hexdigest()}")
            if args.components:
                print("  Components:")
                for k, v in sorted(bid.components().items()):
                    print(f"    {k}: {v[:16]}...")
    else:
        print(f"Unknown target: {args.target}", file=sys.stderr)
        return 1

    return 0


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    # Resolve source directory
    topdir = resolve_topdir(args.directory)

    # Load configuration
    from .config import (
        load_config,
        load_openwrt_dotconfig,
        extract_target_from_dotconfig,
    )

    config_path = args.config or os.path.join(topdir, "oci-build", "config.yaml")
    stages_path = os.path.join(topdir, "oci-build", "stages.yaml")

    overrides = {}
    if args.runtime:
        overrides["container_runtime"] = args.runtime
    if args.jobs:
        overrides["parallel_jobs"] = args.jobs
    if args.verbose:
        overrides["verbose"] = True

    config = load_config(config_path, stages_path, topdir, overrides)

    # Auto-detect target from .config if not set
    if not config.target.board:
        dotconfig_path = os.path.join(topdir, ".config")
        if os.path.exists(dotconfig_path):
            dotconfig = load_openwrt_dotconfig(dotconfig_path)
            config.target = extract_target_from_dotconfig(dotconfig)

    # Dispatch to command handler
    commands = {
        "build": cmd_build,
        "packages": cmd_packages,
        "cache": cmd_cache,
        "graph": cmd_graph,
        "status": cmd_status,
        "identity": cmd_identity,
    }

    handler = commands.get(args.command)
    if handler:
        return handler(args, config) or 0
    else:
        parser.print_help()
        return 0
