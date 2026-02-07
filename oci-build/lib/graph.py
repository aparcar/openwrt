# SPDX-License-Identifier: GPL-2.0-only
"""
Dependency graph with topological ordering and rebuild detection.

Supports:
- DAG construction from package metadata
- Topological sort with parallel wave detection
- Reverse dependency lookup for rebuild cascading
- Cycle detection
"""

import os
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PackageNode:
    """A node in the dependency graph representing a build unit."""

    name: str
    source_dir: str = ""
    build_identity: str = ""
    output_hash: str = ""
    depends: list[str] = field(default_factory=list)
    build_depends: list[str] = field(default_factory=list)
    category: str = ""
    section: str = ""
    # Set during scheduling
    wave: int = -1
    state: str = "pending"  # pending, building, cached, built, failed

    @property
    def all_deps(self) -> list[str]:
        return list(set(self.depends + self.build_depends))


class DependencyGraph:
    """Directed acyclic graph of build dependencies.

    Nodes are packages (or stages). Edges represent build-time dependencies.
    Supports topological sorting into parallel "waves" where all packages
    in a wave can build concurrently.
    """

    def __init__(self):
        self._nodes: dict[str, PackageNode] = {}
        self._edges: dict[str, set[str]] = defaultdict(set)  # node -> deps
        self._reverse: dict[str, set[str]] = defaultdict(set)  # node -> rdeps

    def add_node(self, node: PackageNode) -> None:
        """Add a package node to the graph."""
        self._nodes[node.name] = node
        for dep in node.all_deps:
            self._edges[node.name].add(dep)
            self._reverse[dep].add(node.name)

    def get_node(self, name: str) -> Optional[PackageNode]:
        return self._nodes.get(name)

    @property
    def nodes(self) -> dict[str, PackageNode]:
        return self._nodes

    def dependencies(self, name: str) -> set[str]:
        """Direct dependencies of a node."""
        return self._edges.get(name, set())

    def reverse_dependencies(self, name: str) -> set[str]:
        """Packages that directly depend on this node."""
        return self._reverse.get(name, set())

    def transitive_deps(self, name: str) -> set[str]:
        """All transitive dependencies of a node."""
        visited = set()
        queue = deque(self._edges.get(name, set()))
        while queue:
            dep = queue.popleft()
            if dep in visited:
                continue
            visited.add(dep)
            queue.extend(self._edges.get(dep, set()) - visited)
        return visited

    def transitive_rdeps(self, name: str) -> set[str]:
        """All packages transitively depending on this node."""
        visited = set()
        queue = deque(self._reverse.get(name, set()))
        while queue:
            rdep = queue.popleft()
            if rdep in visited:
                continue
            visited.add(rdep)
            queue.extend(self._reverse.get(rdep, set()) - visited)
        return visited

    def detect_cycles(self) -> list[list[str]]:
        """Detect cycles using DFS. Returns list of cycles found."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in self._nodes}
        parent = {}
        cycles = []

        def dfs(node):
            color[node] = GRAY
            for dep in self._edges.get(node, set()):
                if dep not in color:
                    continue
                if color[dep] == GRAY:
                    # Found cycle - reconstruct it
                    cycle = [dep, node]
                    curr = node
                    while curr in parent and parent[curr] != dep:
                        curr = parent[curr]
                        cycle.append(curr)
                    cycles.append(list(reversed(cycle)))
                elif color[dep] == WHITE:
                    parent[dep] = node
                    dfs(dep)
            color[node] = BLACK

        for node in self._nodes:
            if color[node] == WHITE:
                dfs(node)

        return cycles

    def topological_waves(self) -> list[list[str]]:
        """Sort nodes into parallel waves using Kahn's algorithm.

        Each wave contains nodes whose dependencies are all in earlier waves.
        All nodes within a wave can be built concurrently.

        Returns:
            List of waves, where each wave is a list of node names.

        Raises:
            ValueError: If the graph contains cycles.
        """
        # Only consider nodes that exist in the graph
        existing = set(self._nodes.keys())

        # Compute in-degree (only counting edges to existing nodes)
        in_degree = {name: 0 for name in existing}
        for name in existing:
            for dep in self._edges.get(name, set()):
                if dep in existing:
                    in_degree[name] = in_degree.get(name, 0) + 1

        # Start with nodes that have no dependencies (or only external deps)
        queue = deque(
            sorted(name for name, deg in in_degree.items() if deg == 0)
        )
        waves = []
        processed = set()

        while queue:
            wave = list(queue)
            waves.append(wave)
            next_queue = []

            for name in wave:
                processed.add(name)
                node = self._nodes[name]
                node.wave = len(waves) - 1
                for rdep in sorted(self._reverse.get(name, set())):
                    if rdep not in existing or rdep in processed:
                        continue
                    in_degree[rdep] -= 1
                    if in_degree[rdep] == 0:
                        next_queue.append(rdep)

            queue = deque(sorted(next_queue))

        if len(processed) != len(existing):
            unprocessed = existing - processed
            raise ValueError(
                f"Dependency cycle detected involving: {sorted(unprocessed)[:10]}"
            )

        return waves

    def rebuild_set(self, changed_packages: set[str]) -> set[str]:
        """Determine the minimal set of packages to rebuild.

        Given a set of packages whose sources changed, computes the full
        set of packages that need rebuilding (changed + all reverse deps).
        """
        to_rebuild = set()
        for pkg in changed_packages:
            to_rebuild.add(pkg)
            to_rebuild.update(self.transitive_rdeps(pkg))
        return to_rebuild

    def filter_subgraph(self, keep: set[str]) -> "DependencyGraph":
        """Create a subgraph containing only the specified nodes."""
        sub = DependencyGraph()
        for name in keep:
            node = self._nodes.get(name)
            if node:
                # Filter deps to only those in keep set
                filtered = PackageNode(
                    name=node.name,
                    source_dir=node.source_dir,
                    depends=[d for d in node.depends if d in keep],
                    build_depends=[d for d in node.build_depends if d in keep],
                    category=node.category,
                    section=node.section,
                )
                sub.add_node(filtered)
        return sub

    def summary(self) -> dict:
        """Return graph statistics."""
        edge_count = sum(len(deps) for deps in self._edges.values())
        try:
            waves = self.topological_waves() if self._nodes else []
        except ValueError:
            waves = []
        return {
            "nodes": len(self._nodes),
            "edges": edge_count,
            "waves": len(waves),
            "max_wave_width": max(len(w) for w in waves) if waves else 0,
        }


def parse_openwrt_packageinfo(packageinfo_path: str) -> DependencyGraph:
    """Parse OpenWrt's .packageinfo format into a dependency graph.

    The .packageinfo file is generated by `make package/dump` and contains
    structured metadata about all packages.
    """
    graph = DependencyGraph()
    current = None

    with open(packageinfo_path, "r") as f:
        for line in f:
            line = line.rstrip("\n")

            if line.startswith("Package: "):
                if current:
                    graph.add_node(current)
                current = PackageNode(name=line[9:])

            elif current and line.startswith("Depends: "):
                deps_str = line[9:]
                deps = []
                for dep in deps_str.split():
                    dep = dep.strip()
                    # Strip version constraints like (+foo) or (@feature)
                    if dep.startswith("+"):
                        dep = dep[1:]
                    elif dep.startswith("@"):
                        continue
                    if dep:
                        deps.append(dep)
                current.depends = deps

            elif current and line.startswith("Build-Depends: "):
                current.build_depends = [
                    d.strip() for d in line[15:].split(",") if d.strip()
                ]

            elif current and line.startswith("Source: "):
                current.source_dir = line[8:]

            elif current and line.startswith("Section: "):
                current.section = line[9:]

            elif current and line.startswith("Category: "):
                current.category = line[10:]

    if current:
        graph.add_node(current)

    return graph


def parse_openwrt_package_makefile(makefile_path: str) -> Optional[PackageNode]:
    """Extract basic metadata from a single OpenWrt package Makefile.

    This is a lightweight parser for quick dependency extraction without
    running make dump. Falls back gracefully on complex Makefiles.
    """
    name = None
    depends = []
    build_depends = []

    try:
        with open(makefile_path, "r") as f:
            content = f.read()
    except (OSError, IOError):
        return None

    def _is_valid_dep(dep_name: str) -> bool:
        """Filter out obviously invalid dependency names."""
        if not dep_name or len(dep_name) < 2:
            return False
        # Skip make variables, paths, backslashes, parens
        bad_chars = set("$(){}/<>\\|!&*?\"'")
        if any(c in dep_name for c in bad_chars):
            return False
        return True

    for line in content.splitlines():
        stripped = line.strip()

        if stripped.startswith("PKG_NAME:="):
            name = stripped.split(":=", 1)[1].strip()
        elif "DEPENDS:=" in stripped or "DEPENDS+=" in stripped:
            dep_str = stripped.split("=", 1)[1] if "=" in stripped else ""
            for dep in dep_str.split():
                dep = dep.strip()
                if dep.startswith("+"):
                    dep = dep[1:]
                elif dep.startswith("@"):
                    continue
                if _is_valid_dep(dep):
                    depends.append(dep)
        elif stripped.startswith("PKG_BUILD_DEPENDS:="):
            bd_str = stripped.split(":=", 1)[1]
            for dep in bd_str.split():
                dep = dep.strip()
                if _is_valid_dep(dep):
                    build_depends.append(dep)

    if name is None:
        # Try to infer from directory name
        name = os.path.basename(os.path.dirname(makefile_path))

    return PackageNode(
        name=name,
        source_dir=os.path.dirname(makefile_path),
        depends=depends,
        build_depends=build_depends,
    )
