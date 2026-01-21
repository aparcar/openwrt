"""
Dependency resolver - resolves package dependencies into a build graph.

Produces a DAG (Directed Acyclic Graph) that can be used by Ninja
for parallel builds with proper ordering.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import hashlib
import json

from .config import Config, PackageConfig, SubpackageConfig


@dataclass
class ConditionalDep:
    """Parsed conditional dependency."""
    condition: str  # The condition (e.g., 'IPV6', 'PACKAGE_foo')
    dependency: str  # The actual dependency name
    negated: bool  # True if condition is negated (!)


@dataclass
class ProviderInfo:
    """Information about a package that provides a virtual name."""
    package_name: str
    priority: int


@dataclass
class BuildTarget:
    """Represents a build target (package, kernel, or image)."""
    name: str
    target_type: str  # 'package', 'kernel', 'toolchain', 'image', 'source'
    deps: List[str] = field(default_factory=list)
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    cache_key: str = ''
    config: Optional[PackageConfig] = None
    source_name: Optional[str] = None  # For packages: which source they use

    def __hash__(self):
        return hash(self.name)


@dataclass
class BuildPlan:
    """Complete build plan with all targets and their dependencies."""
    targets: Dict[str, BuildTarget]
    build_order: List[str]
    cached: Set[str]
    to_build: Set[str]

    @property
    def total_targets(self) -> int:
        return len(self.targets)

    @property
    def cached_count(self) -> int:
        return len(self.cached)

    @property
    def to_build_count(self) -> int:
        return len(self.to_build)


class DependencyResolver:
    """Resolves dependencies and produces a build plan."""

    def __init__(self, config: Config, cache_dir: Optional[Path] = None):
        self.config = config
        self.cache_dir = cache_dir or config.build_dir / 'cache'
        self._packages: Dict[str, PackageConfig] = {}
        self._targets: Dict[str, BuildTarget] = {}
        self._providers: Dict[str, List[ProviderInfo]] = {}
        # Context for conditional dependencies
        self._enabled_features: Set[str] = set(config.features)
        self._requested_packages: Set[str] = set()

    def resolve(
        self,
        packages: List[str],
        include_kernel: bool = True,
        include_toolchain: bool = True,
    ) -> BuildPlan:
        """
        Resolve all dependencies and produce a build plan.

        Args:
            packages: List of package names to build
            include_kernel: Include kernel in build plan
            include_toolchain: Include toolchain in build plan

        Returns:
            BuildPlan with resolved dependencies
        """
        self._targets.clear()
        self._providers.clear()
        self._requested_packages = set(packages)

        # Pre-scan all packages to build provider registry
        self._scan_providers()

        # Add toolchain target
        if include_toolchain:
            self._add_toolchain_target()

        # Add kernel target
        if include_kernel:
            self._add_kernel_target()

        # Resolve package dependencies
        for pkg_name in packages:
            self._resolve_package(pkg_name)

        # Compute build order (topological sort)
        build_order = self._topological_sort()

        # Check cache for each target
        cached = set()
        to_build = set()

        for name in build_order:
            target = self._targets[name]
            target.cache_key = self._compute_cache_key(target)

            if self._is_cached(target):
                cached.add(name)
            else:
                to_build.add(name)

        return BuildPlan(
            targets=self._targets.copy(),
            build_order=build_order,
            cached=cached,
            to_build=to_build,
        )

    def _add_toolchain_target(self):
        """Add toolchain as a build target."""
        target = BuildTarget(
            name='toolchain',
            target_type='toolchain',
            deps=[],
            outputs=[f'{self.config.toolchain_dir}/bin/{self.config.cross_compile}gcc'],
        )
        self._targets['toolchain'] = target

    def _add_kernel_target(self):
        """Add kernel as a build target."""
        target = BuildTarget(
            name='kernel',
            target_type='kernel',
            deps=['toolchain'],
            outputs=[
                str(self.config.kernel_build_dir / 'output' / 'kernel.bin'),
                str(self.config.kernel_build_dir / 'output' / 'dtbs'),
            ],
        )
        self._targets['kernel'] = target

    def _resolve_package(self, name: str, seen: Optional[Set[str]] = None) -> Optional[BuildTarget]:
        """Recursively resolve a package and its dependencies.

        For subpackages: resolves to the source package (one ninja job per source).
        All subpackages from a source are built together in one job.
        For auto-generated -dev packages: maps to the source package that generates them.
        """
        if seen is None:
            seen = set()

        # Check for circular dependency
        if name in seen:
            raise ValueError(f"Circular dependency detected: {name}")

        # Check if this is a virtual package name that has a provider
        if name in self._providers:
            provider = self._get_provider(name)
            if provider:
                name = provider.package_name

        # Load package config
        pkg = self._load_package(name)
        if not pkg:
            # Package not found - might be a system package or virtual
            return None

        # Register providers for this package
        self._register_providers(pkg)

        # For subpackages, resolve to the source package instead
        # This ensures one ninja job per source, not per subpackage
        if isinstance(pkg, SubpackageConfig):
            source_name = pkg.source_name
            # If source package already resolved, return it
            if source_name in self._targets:
                return self._targets[source_name]
            # Otherwise, load and resolve the source package
            source_pkg = pkg.parent
            return self._resolve_source_package(source_name, source_pkg, seen)

        # For auto-generated -dev packages, map to the actual source package
        # E.g., 'libjson-c-dev' -> 'libjson-c' source package
        actual_name = pkg.name
        if name.endswith('-dev') and name != actual_name:
            # This is a -dev reference that resolved to a different package
            # Use the actual package name for the target
            if actual_name in self._targets:
                return self._targets[actual_name]
            return self._resolve_source_package(actual_name, pkg, seen)

        # Already resolved (for non-subpackages)
        if name in self._targets:
            return self._targets[name]

        return self._resolve_source_package(name, pkg, seen)

    def _resolve_source_package(self, name: str, pkg: PackageConfig, seen: Set[str]) -> BuildTarget:
        """Resolve a source package (not a subpackage)."""
        # Check if already being resolved (cycle detection)
        if name in seen:
            raise ValueError(f"Circular dependency detected: {name}")

        # Already resolved
        if name in self._targets:
            return self._targets[name]

        seen.add(name)

        # All packages depend on toolchain
        deps = ['toolchain']

        # Collect all subpackage names (to skip internal deps)
        subpackage_names = set(pkg.subpackages.keys())

        # Resolve build and runtime dependencies (deduplicated)
        # Include deps from all subpackages too, but skip internal subpackage deps
        # Filter conditional dependencies based on enabled features/packages
        all_deps = set(self._filter_dependencies(pkg.build_deps))
        all_deps |= set(self._filter_dependencies(pkg.runtime_deps))
        for subpkg in pkg.subpackages.values():
            for dep in self._filter_dependencies(subpkg.runtime_deps):
                if dep not in subpackage_names:  # Skip sibling subpackages
                    all_deps.add(dep)

        for dep_name in all_deps:
            if dep_name == name or dep_name in subpackage_names:
                continue  # Skip self-dependencies and sibling deps
            dep_target = self._resolve_package(dep_name, seen.copy())
            if dep_target and dep_target.name not in deps:
                deps.append(dep_target.name)

        # Create target for the source package
        target = BuildTarget(
            name=name,
            target_type='package',
            deps=deps,
            outputs=[str(self.config.packages_dir / 'stamp' / f'{name}.built')],
            config=pkg,
            source_name=name,
        )
        self._targets[name] = target

        seen.discard(name)
        return target

    def _load_package(self, name: str) -> Optional[PackageConfig]:
        """Load package configuration.

        Handles auto-generated -dev packages: when a -dev package isn't found
        explicitly, searches for a source package that would auto-generate it.
        """
        if name in self._packages:
            return self._packages[name]

        pkg = PackageConfig.find_package(name)
        if pkg:
            self._packages[name] = pkg
            return pkg

        # Handle auto-generated -dev packages
        if name.endswith('-dev'):
            pkg = self._find_dev_package_source(name)
            if pkg:
                self._packages[name] = pkg
                return pkg

        return None

    def _find_dev_package_source(self, dev_name: str) -> Optional[PackageConfig]:
        """Find the source package that would auto-generate a -dev package.

        E.g., 'libubus-dev' -> find source package 'ubus' which has 'libubus' subpackage.
        This searches for a -dev subpackage using the standard find_package mechanism.
        """
        # Extract base name (e.g., 'libubox' from 'libubox-dev')
        base_name = dev_name[:-4]  # Remove '-dev' suffix

        # Use the standard find_package mechanism to find the base package
        # This handles the actual package directory structure (package/libs/libubox/, etc.)
        base_pkg = PackageConfig.find_package(base_name)
        if base_pkg:
            # Found the base package - return it so the source gets built
            # which will produce the -dev subpackage
            if isinstance(base_pkg, SubpackageConfig):
                return base_pkg  # Already a subpackage reference
            return base_pkg

        return None

    def _register_providers(self, pkg) -> None:
        """Register all provides for a package.

        Calculates priority:
        - default_variant: true → priority 100
        - abi_version set → priority 10 (future feature)
        - Default → priority 0

        Args:
            pkg: PackageConfig or SubpackageConfig
        """
        provides = getattr(pkg, 'provides', [])
        default_variant = getattr(pkg, 'default_variant', False)

        if not provides:
            return

        # Calculate priority
        priority = 0
        if default_variant:
            priority = 100

        for provide_name in provides:
            provider = ProviderInfo(
                package_name=pkg.name,
                priority=priority,
            )

            if provide_name not in self._providers:
                self._providers[provide_name] = []

            # Check for provider conflicts (only one package can provide a name)
            existing = self._providers[provide_name]
            for ep in existing:
                if ep.package_name != pkg.name:
                    raise ValueError(
                        f"Provider conflict: both '{ep.package_name}' and "
                        f"'{pkg.name}' provide '{provide_name}'"
                    )

            self._providers[provide_name].append(provider)
            # Sort by priority (highest first)
            self._providers[provide_name].sort(key=lambda p: -p.priority)

    def _get_provider(self, name: str) -> Optional[ProviderInfo]:
        """Get the highest priority provider for a virtual name.

        Args:
            name: The virtual package name (without @ prefix)

        Returns:
            ProviderInfo for the highest priority provider, or None
        """
        providers = self._providers.get(name, [])
        if providers:
            return providers[0]  # Already sorted by priority
        return None

    def _parse_conditional_dep(self, dep: str) -> Tuple[Optional[ConditionalDep], str]:
        """Parse a dependency string that may be conditional.

        OpenWrt conditional dependency formats:
        - 'libc' - unconditional dependency
        - '+IPV6:libc' - depends on libc if IPV6 feature is enabled
        - '+!IPV6:libc' - depends on libc if IPV6 feature is NOT enabled
        - '+PACKAGE_foo:bar' - depends on bar if package foo is selected

        Args:
            dep: Dependency string (e.g., '+IPV6:libc' or 'libc')

        Returns:
            Tuple of (ConditionalDep or None, actual dependency name)
        """
        if not dep.startswith('+'):
            # Unconditional dependency
            return None, dep

        # Parse conditional: +CONDITION:dep or +!CONDITION:dep
        if ':' not in dep:
            # Malformed conditional, treat as unconditional
            return None, dep

        condition_part, actual_dep = dep[1:].split(':', 1)

        negated = condition_part.startswith('!')
        if negated:
            condition_part = condition_part[1:]

        return ConditionalDep(
            condition=condition_part,
            dependency=actual_dep,
            negated=negated,
        ), actual_dep

    def _evaluate_condition(self, cond: ConditionalDep) -> bool:
        """Evaluate a conditional dependency.

        Condition types:
        - Feature conditions: Check if feature is in config.features
        - Package conditions (PACKAGE_*): Check if package is requested
        - Kernel config (TODO): Would check kernel config options

        Args:
            cond: The conditional dependency to evaluate

        Returns:
            True if the dependency should be included, False to skip
        """
        condition = cond.condition

        # Check for PACKAGE_* condition
        if condition.startswith('PACKAGE_'):
            pkg_name = condition[8:]  # Remove 'PACKAGE_' prefix
            result = pkg_name in self._requested_packages
        else:
            # Feature condition - check enabled features
            result = condition in self._enabled_features

        # Apply negation if needed
        if cond.negated:
            result = not result

        return result

    def _filter_dependencies(self, deps: List[str]) -> List[str]:
        """Filter dependencies based on conditions.

        Args:
            deps: List of dependency strings (may include conditionals)

        Returns:
            List of actual dependency names (conditionals evaluated)
        """
        result = []
        for dep in deps:
            cond, actual_dep = self._parse_conditional_dep(dep)

            if cond is None:
                # Unconditional dependency
                result.append(actual_dep)
            elif self._evaluate_condition(cond):
                # Conditional dependency with condition met
                result.append(actual_dep)
            # else: condition not met, skip this dependency

        return result

    def _scan_providers(self) -> None:
        """Scan all packages to build the provider registry.

        This must be called before dependency resolution to ensure
        virtual package dependencies can be resolved even if the
        provider hasn't been explicitly requested.
        """
        from pathlib import Path

        poc_dir = Path(__file__).parent.parent
        package_root = poc_dir / 'package'

        if not package_root.exists():
            return

        # Recursively find all package.yaml files
        for pkg_file in package_root.rglob('package.yaml'):
            pkg_path = pkg_file.parent

            try:
                pkg = PackageConfig.load(pkg_path)

                # Register provides from main package
                self._register_providers(pkg)

                # Register provides from subpackages
                for subpkg in pkg.subpackages.values():
                    self._register_providers(subpkg)

            except Exception:
                # Skip packages that fail to load
                pass

    def _topological_sort(self) -> List[str]:
        """
        Topological sort of build targets using Kahn's algorithm.
        Returns targets in dependency order (dependencies first).
        """
        # Build in-degree map
        in_degree: Dict[str, int] = {name: 0 for name in self._targets}
        for target in self._targets.values():
            for dep in target.deps:
                if dep in in_degree:
                    in_degree[target.name] += 1

        # Start with nodes that have no dependencies
        queue = [name for name, degree in in_degree.items() if degree == 0]
        result = []

        while queue:
            # Sort for deterministic order
            queue.sort()
            name = queue.pop(0)
            result.append(name)

            # Reduce in-degree for dependents
            for target in self._targets.values():
                if name in target.deps:
                    in_degree[target.name] -= 1
                    if in_degree[target.name] == 0:
                        queue.append(target.name)

        if len(result) != len(self._targets):
            raise ValueError("Circular dependency detected in build graph")

        return result

    def _compute_cache_key(self, target: BuildTarget) -> str:
        """Compute a cache key for a target based on its inputs."""
        h = hashlib.sha256()

        # Target name and type
        h.update(f"{target.name}:{target.target_type}".encode())

        # Target config
        h.update(self.config.name.encode())
        h.update(self.config.arch.encode())

        if target.target_type == 'toolchain':
            h.update(self.config.toolchain['gcc_version'].encode())
            h.update(self.config.toolchain['libc'].encode())

        elif target.target_type == 'kernel':
            h.update(self.config.kernel['full_version'].encode())
            h.update(self.config.kernel.get('source_hash', '').encode())

        elif target.target_type == 'package' and target.config:
            pkg = target.config
            h.update(pkg.version.encode())
            source_hash = pkg.source.get('hash', pkg.source.get('sha256', ''))
            h.update(source_hash.encode())

        # Dependencies' cache keys
        for dep_name in sorted(target.deps):
            if dep_name in self._targets:
                dep_key = self._targets[dep_name].cache_key
                if dep_key:
                    h.update(dep_key.encode())

        return h.hexdigest()[:16]

    def _is_cached(self, target: BuildTarget) -> bool:
        """Check if a target's outputs are cached."""
        cache_file = self.cache_dir / 'keys' / f'{target.name}.key'

        if not cache_file.exists():
            return False

        stored_key = cache_file.read_text().strip()
        if stored_key != target.cache_key:
            return False

        # Verify outputs exist
        for output in target.outputs:
            if not Path(output).exists():
                return False

        return True

    def mark_cached(self, target: BuildTarget):
        """Mark a target as cached after successful build."""
        cache_dir = self.cache_dir / 'keys'
        cache_dir.mkdir(parents=True, exist_ok=True)

        cache_file = cache_dir / f'{target.name}.key'
        cache_file.write_text(target.cache_key)

    def get_dependency_graph(self) -> Dict[str, List[str]]:
        """Return the dependency graph as an adjacency list."""
        return {name: target.deps for name, target in self._targets.items()}

    def print_plan(self, plan: BuildPlan):
        """Print a human-readable build plan."""
        print(f"\nBuild Plan for {self.config.name}:")
        print(f"  Total targets: {plan.total_targets}")
        print(f"  Cached: {plan.cached_count}")
        print(f"  To build: {plan.to_build_count}")
        print(f"\nBuild order:")

        for i, name in enumerate(plan.build_order, 1):
            target = plan.targets[name]
            status = "[cached]" if name in plan.cached else "[build]"
            deps = f" <- {', '.join(target.deps)}" if target.deps else ""
            print(f"  {i:3}. {status:8} {name} ({target.target_type}){deps}")
