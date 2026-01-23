"""
Tests for resolver.py - DependencyResolver and provider resolution.
"""

import pytest
from pathlib import Path
from unittest.mock import Mock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from owrt.resolver import DependencyResolver, ProviderInfo
from owrt.config import PackageConfig, Config
from owrt.tests.conftest import create_package_dir


class TestProviderInfo:
    """Tests for ProviderInfo dataclass."""

    def test_provider_info_creation(self):
        """Test basic ProviderInfo creation."""
        info = ProviderInfo(
            package_name='mbedtls',
            priority=100,
        )

        assert info.package_name == 'mbedtls'
        assert info.priority == 100


class TestProviderRegistry:
    """Tests for provider registration in DependencyResolver."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock Config object."""
        config = Mock(spec=Config)
        config.name = 'test-target'
        config.arch = 'aarch64'
        config.build_dir = Path('/tmp/build')
        config.packages_dir = Path('/tmp/build/packages/test')
        config.staging_dir = Path('/tmp/build/staging/test')
        config.kernel_build_dir = Path('/tmp/build/kernel/test')
        config.toolchain_dir = Path('/tmp/build/toolchain/test')
        config.cross_compile = 'aarch64-openwrt-linux-musl-'
        config.toolchain = {'gcc_version': '14.3.0', 'libc': 'musl'}
        config.features = []
        return config

    def test_register_providers_basic(self, mock_config, temp_packages_dir, package_with_provides):
        """Test that providers are registered correctly."""
        pkg_dir = create_package_dir(
            temp_packages_dir, 'mbedtls', package_with_provides
        )
        pkg = PackageConfig.load(pkg_dir)

        resolver = DependencyResolver(mock_config)
        resolver._register_providers(pkg)

        assert 'libssl' in resolver._providers
        providers = resolver._providers['libssl']
        assert len(providers) == 1
        assert providers[0].package_name == 'mbedtls'
        assert providers[0].priority == 100  # default_variant=True

    def test_default_variant_priority(self, mock_config, temp_packages_dir):
        """Test that default_variant=True gives priority 100."""
        pkg_data_default = {
            'name': 'mbedtls',
            'version': '3.6.5',
            'release': 1,
            'provides': ['libssl'],
            'default_variant': True,
            'source': {'type': 'local'},
            'build': {'system': 'cmake'},
        }

        pkg_dir = create_package_dir(temp_packages_dir, 'mbedtls', pkg_data_default)
        pkg = PackageConfig.load(pkg_dir)

        resolver = DependencyResolver(mock_config)
        resolver._register_providers(pkg)

        providers = resolver._providers['libssl']
        assert providers[0].package_name == 'mbedtls'
        assert providers[0].priority == 100

    def test_get_provider_highest_priority(self, mock_config, temp_packages_dir):
        """Test that _get_provider returns highest priority provider."""
        pkg_data = {
            'name': 'mbedtls',
            'version': '3.6.5',
            'release': 1,
            'provides': ['libssl'],
            'default_variant': True,
            'source': {'type': 'local'},
            'build': {'system': 'cmake'},
        }

        pkg_dir = create_package_dir(temp_packages_dir, 'mbedtls', pkg_data)
        pkg = PackageConfig.load(pkg_dir)

        resolver = DependencyResolver(mock_config)
        resolver._register_providers(pkg)

        provider = resolver._get_provider('libssl')
        assert provider is not None
        assert provider.package_name == 'mbedtls'

    def test_provider_conflict(self, mock_config, temp_packages_dir):
        """Test that multiple providers for same name raise error."""
        pkg_data1 = {
            'name': 'mbedtls',
            'version': '3.6.5',
            'release': 1,
            'provides': ['libssl'],
            'source': {'type': 'local'},
            'build': {'system': 'cmake'},
        }
        pkg_data2 = {
            'name': 'openssl',
            'version': '3.0.0',
            'release': 1,
            'provides': ['libssl'],
            'source': {'type': 'local'},
            'build': {'system': 'custom'},
        }

        pkg_dir1 = create_package_dir(temp_packages_dir, 'mbedtls', pkg_data1)
        pkg_dir2 = create_package_dir(temp_packages_dir, 'openssl', pkg_data2)

        pkg1 = PackageConfig.load(pkg_dir1)
        pkg2 = PackageConfig.load(pkg_dir2)

        resolver = DependencyResolver(mock_config)
        resolver._register_providers(pkg1)

        # Second provider should raise conflict error
        with pytest.raises(ValueError, match="Provider conflict"):
            resolver._register_providers(pkg2)


class TestVirtualDependencyResolution:
    """Tests for resolving virtual package dependencies."""

    @pytest.fixture
    def mock_config(self):
        """Create a mock Config object."""
        config = Mock(spec=Config)
        config.name = 'test-target'
        config.arch = 'aarch64'
        config.build_dir = Path('/tmp/build')
        config.packages_dir = Path('/tmp/build/packages/test')
        config.staging_dir = Path('/tmp/build/staging/test')
        config.kernel_build_dir = Path('/tmp/build/kernel/test')
        config.toolchain_dir = Path('/tmp/build/toolchain/test')
        config.cross_compile = 'aarch64-openwrt-linux-musl-'
        config.toolchain = {'gcc_version': '14.3.0', 'libc': 'musl'}
        config.features = []
        return config

    def test_resolve_virtual_dependency(self, mock_config, temp_packages_dir, monkeypatch):
        """Test that a dependency on libssl resolves to mbedtls."""
        # Create mbedtls that provides libssl
        mbedtls_data = {
            'name': 'mbedtls',
            'version': '3.6.5',
            'release': 1,
            'provides': ['libssl'],
            'default_variant': True,
            'source': {'type': 'local'},
            'build': {'system': 'cmake'},
        }

        # Create curl that depends on libssl
        curl_data = {
            'name': 'curl',
            'version': '8.0.0',
            'release': 1,
            'source': {'type': 'local'},
            'dependencies': {
                'runtime': ['libssl'],  # Virtual dependency
                'build': [],
            },
            'build': {'system': 'autotools'},
        }

        create_package_dir(temp_packages_dir, 'mbedtls', mbedtls_data)
        create_package_dir(temp_packages_dir, 'curl', curl_data)

        # Patch PackageConfig.find_package to use our temp directory
        def mock_find_package(name):
            pkg_dir = temp_packages_dir / name
            if pkg_dir.exists() and (pkg_dir / 'package.yaml').exists():
                return PackageConfig.load(pkg_dir)
            return None

        monkeypatch.setattr(PackageConfig, 'find_package', staticmethod(mock_find_package))

        # Patch _scan_providers to use our temp directory
        def mock_scan_providers(self):
            for pkg_path in temp_packages_dir.iterdir():
                if not pkg_path.is_dir():
                    continue
                pkg_file = pkg_path / 'package.yaml'
                if not pkg_file.exists():
                    continue
                try:
                    pkg = PackageConfig.load(pkg_path)
                    self._register_providers(pkg)
                    for subpkg in pkg.subpackages.values():
                        self._register_providers(subpkg)
                except Exception:
                    pass

        monkeypatch.setattr(DependencyResolver, '_scan_providers', mock_scan_providers)

        resolver = DependencyResolver(mock_config)
        plan = resolver.resolve(['curl'], include_kernel=False, include_toolchain=False)

        # Both curl and mbedtls should be in the build plan
        assert 'curl' in plan.targets
        assert 'mbedtls' in plan.targets

        # curl should depend on mbedtls (not on non-existent libssl)
        curl_target = plan.targets['curl']
        assert 'mbedtls' in curl_target.deps
