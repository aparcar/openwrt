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

    def test_subpackage_license_override(self, temp_packages_dir):
        """Test that subpackage can override parent license."""
        pkg_data = {
            'name': 'toolchain',
            'version': '1.2.5',
            'release': 3,
            'license': 'GPL-3.0-with-GCC-exception',
            'source': {'type': 'toolchain'},
            'build': {'system': 'toolchain'},
            'subpackages': {
                'libgcc': {
                    'description': 'GCC support library',
                    # No license override - should inherit parent
                },
                'libc': {
                    'description': 'C library',
                    'license': 'MIT',  # Override parent license
                },
            },
        }

        pkg_dir = create_package_dir(temp_packages_dir, 'toolchain', pkg_data)
        pkg = PackageConfig.load(pkg_dir)

        libgcc = pkg.get_subpackage('libgcc')
        libc = pkg.get_subpackage('libc')

        # libgcc should inherit parent license
        assert libgcc.license == 'GPL-3.0-with-GCC-exception'
        # libc should have its own MIT license
        assert libc.license == 'MIT'

    def test_subpackage_inherits_parent_attrs(self, temp_packages_dir, subpackage_with_provides):
        """Test that subpackage inherits version, release, pkg_dir from parent."""
        pkg_dir = create_package_dir(
            temp_packages_dir, 'openssl', subpackage_with_provides
        )

        pkg = PackageConfig.load(pkg_dir)
        subpkg = pkg.get_subpackage('libopenssl')

        assert subpkg.version == pkg.version
        assert subpkg.release == pkg.release
        assert subpkg.pkg_dir == pkg.pkg_dir
        assert subpkg.source == pkg.source
        assert subpkg.build == pkg.build

    def test_subpackage_conflicts_and_replaces(self, temp_packages_dir):
        """Test that conflicts and replaces fields are parsed correctly."""
        pkg_data = {
            'name': 'libubox',
            'version': '2024.01.01',
            'release': 1,
            'license': 'ISC',
            'source': {'type': 'git', 'url': 'https://git.openwrt.org/project/libubox.git', 'ref': 'main'},
            'build': {'system': 'cmake'},
            'subpackages': {
                'libubox': {
                    'description': 'Basic utility library',
                    'conflicts': ['libubox-old'],
                    'replaces': ['libubox-legacy'],
                },
            },
        }

        pkg_dir = create_package_dir(temp_packages_dir, 'libubox', pkg_data)
        pkg = PackageConfig.load(pkg_dir)
        subpkg = pkg.get_subpackage('libubox')

        assert subpkg.conflicts == ['libubox-old']
        assert subpkg.replaces == ['libubox-legacy']

    def test_subpackage_install_toolchain_libs(self, temp_packages_dir):
        """Test that toolchain_libs install field is parsed correctly."""
        pkg_data = {
            'name': 'toolchain',
            'version': '1.0.0',
            'release': 1,
            'license': 'GPL-3.0',
            'source': {'type': 'toolchain'},
            'build': {'system': 'toolchain'},
            'subpackages': {
                'libgcc': {
                    'description': 'GCC support library',
                    'install': {
                        'toolchain_libs': ['libgcc_s.so.*'],
                    },
                },
            },
        }

        pkg_dir = create_package_dir(temp_packages_dir, 'toolchain', pkg_data)
        pkg = PackageConfig.load(pkg_dir)
        subpkg = pkg.get_subpackage('libgcc')

        assert subpkg.install['toolchain_libs'] == ['libgcc_s.so.*']

    def test_subpackage_userid_and_scripts(self, temp_packages_dir):
        """Test userid and scripts fields in subpackages."""
        pkg_data = {
            'name': 'dnsmasq',
            'version': '2.90',
            'release': 1,
            'license': 'GPL-2.0',
            'source': {'type': 'tarball', 'url': 'https://example.com/dnsmasq.tar.gz', 'sha256': 'abc'},
            'build': {'system': 'make'},
            'subpackages': {
                'dnsmasq': {
                    'description': 'DNS forwarder',
                    'userid': ['dnsmasq=453:dnsmasq=453'],
                    'scripts': {
                        'postinst': '#!/bin/sh\necho "installed"',
                    },
                },
            },
        }

        pkg_dir = create_package_dir(temp_packages_dir, 'dnsmasq', pkg_data)
        pkg = PackageConfig.load(pkg_dir)
        subpkg = pkg.get_subpackage('dnsmasq')

        assert subpkg.userid == ['dnsmasq=453:dnsmasq=453']
        assert 'postinst' in subpkg.scripts
        assert 'echo "installed"' in subpkg.scripts['postinst']
