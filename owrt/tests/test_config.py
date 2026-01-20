"""
Tests for config.py - PackageConfig and SubpackageConfig.
"""

import pytest
from pathlib import Path

import yaml
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from owrt.config import PackageConfig, SubpackageConfig
from owrt.tests.conftest import create_package_dir


class TestPackageConfig:
    """Tests for PackageConfig class."""

    def test_provides_parsed(self, temp_packages_dir, package_with_provides):
        """Test that provides field is correctly parsed from YAML."""
        pkg_dir = create_package_dir(
            temp_packages_dir, 'mbedtls', package_with_provides
        )

        pkg = PackageConfig.load(pkg_dir)

        assert pkg.provides == ['libssl']
        assert pkg.default_variant is True

    def test_provides_default_empty(self, temp_packages_dir, sample_package_yaml):
        """Test that provides defaults to empty list."""
        pkg_dir = create_package_dir(
            temp_packages_dir, 'test-pkg', sample_package_yaml
        )

        pkg = PackageConfig.load(pkg_dir)

        assert pkg.provides == []
        assert pkg.default_variant is False



class TestSubpackageConfig:
    """Tests for SubpackageConfig class."""

    def test_subpackage_provides_parsed(self, temp_packages_dir, subpackage_with_provides):
        """Test that provides in subpackages are correctly parsed."""
        pkg_dir = create_package_dir(
            temp_packages_dir, 'openssl', subpackage_with_provides
        )

        pkg = PackageConfig.load(pkg_dir)
        subpkg = pkg.get_subpackage('libopenssl')

        assert subpkg is not None
        assert subpkg.provides == ['libssl']
        assert subpkg.default_variant is False

    def test_subpackage_default_variant(self, temp_packages_dir):
        """Test subpackage with default_variant set."""
        pkg_data = {
            'name': 'wolfssl',
            'version': '5.0.0',
            'release': 1,
            'license': 'GPL-2.0',
            'source': {'type': 'tarball', 'url': 'https://example.com/wolfssl.tar.gz', 'sha256': 'xyz'},
            'build': {'system': 'autotools'},
            'subpackages': {
                'libwolfssl': {
                    'description': 'WolfSSL library',
                    'provides': ['libssl'],
                    'default_variant': True,
                    'files': [{'src': 'usr/lib/*.so*', 'dst': '/usr/lib/'}],
                },
            },
        }

        pkg_dir = create_package_dir(temp_packages_dir, 'wolfssl', pkg_data)
        pkg = PackageConfig.load(pkg_dir)
        subpkg = pkg.get_subpackage('libwolfssl')

        assert subpkg.provides == ['libssl']
        assert subpkg.default_variant is True
