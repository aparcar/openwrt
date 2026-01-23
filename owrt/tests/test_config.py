"""
Tests for config.py - Config, PackageConfig, SubpackageConfig, VariantConfig.
"""

import pytest
from pathlib import Path
import tempfile
import shutil

import yaml
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from owrt.config import Config, PackageConfig, SubpackageConfig, VariantConfig, compute_package_content_hash
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


class TestVariantConfig:
    """Tests for VariantConfig class."""

    def test_variant_basic(self, temp_packages_dir):
        """Test basic variant configuration."""
        pkg_data = {
            'name': 'ustream-ssl',
            'version': '2024.01.01',
            'release': 1,
            'license': 'ISC',
            'source': {'type': 'git', 'url': 'https://example.com/repo.git'},
            'build': {'system': 'cmake'},
            'variants': {
                'mbedtls': {
                    'package_name': 'libustream-mbedtls',
                    'default': True,
                    'description': 'ustream with mbedtls',
                    'dependencies': {
                        'runtime': ['libmbedtls'],
                    },
                    'cmake_options': ['-DBACKEND=mbedtls'],
                },
                'openssl': {
                    'package_name': 'libustream-openssl',
                    'description': 'ustream with openssl',
                    'dependencies': {
                        'runtime': ['libopenssl'],
                    },
                    'cmake_options': ['-DBACKEND=openssl'],
                },
            },
        }

        pkg_dir = create_package_dir(temp_packages_dir, 'ustream-ssl', pkg_data)
        pkg = PackageConfig.load(pkg_dir)

        assert pkg.has_variants
        assert len(pkg.variants) == 2

        mbedtls = pkg.get_variant('mbedtls')
        assert mbedtls is not None
        assert mbedtls.package_name == 'libustream-mbedtls'
        assert mbedtls.default is True
        assert 'libmbedtls' in mbedtls.runtime_deps

        openssl = pkg.get_variant('openssl')
        assert openssl is not None
        assert openssl.package_name == 'libustream-openssl'
        assert openssl.default is False

    def test_variant_inherits_parent(self, temp_packages_dir):
        """Test variant inherits parent properties."""
        pkg_data = {
            'name': 'test-pkg',
            'version': '1.0.0',
            'release': 2,
            'license': 'MIT',
            'source': {'type': 'tarball', 'url': 'https://example.com/test.tar.gz'},
            'build': {'system': 'autotools'},
            'variants': {
                'variant1': {
                    'package_name': 'test-variant1',
                },
            },
        }

        pkg_dir = create_package_dir(temp_packages_dir, 'test-pkg', pkg_data)
        pkg = PackageConfig.load(pkg_dir)
        variant = pkg.get_variant('variant1')

        assert variant.version == '1.0.0'
        assert variant.release == 2
        assert variant.license == 'MIT'
        assert variant.pkg_dir == pkg.pkg_dir
        assert variant.source_name == 'test-pkg'

    def test_variant_build_options_merged(self, temp_packages_dir):
        """Test variant build options are merged with parent."""
        pkg_data = {
            'name': 'test-pkg',
            'version': '1.0.0',
            'license': 'MIT',
            'source': {'type': 'tarball', 'url': 'https://example.com/test.tar.gz'},
            'build': {
                'system': 'cmake',
                'cmake_options': ['-DBASE_OPT=1'],
            },
            'variants': {
                'v1': {
                    'package_name': 'test-v1',
                    'cmake_options': ['-DVARIANT_OPT=2'],
                },
            },
        }

        pkg_dir = create_package_dir(temp_packages_dir, 'test-pkg', pkg_data)
        pkg = PackageConfig.load(pkg_dir)
        variant = pkg.get_variant('v1')

        build = variant.build
        assert '-DBASE_OPT=1' in build['cmake_options']
        assert '-DVARIANT_OPT=2' in build['cmake_options']

    def test_get_default_variant(self, temp_packages_dir):
        """Test getting the default variant."""
        pkg_data = {
            'name': 'test-pkg',
            'version': '1.0.0',
            'license': 'MIT',
            'source': {'type': 'tarball', 'url': 'https://example.com/test.tar.gz'},
            'build': {'system': 'cmake'},
            'variants': {
                'v1': {'package_name': 'test-v1'},
                'v2': {'package_name': 'test-v2', 'default': True},
            },
        }

        pkg_dir = create_package_dir(temp_packages_dir, 'test-pkg', pkg_data)
        pkg = PackageConfig.load(pkg_dir)

        default = pkg.get_default_variant()
        assert default is not None
        assert default.name == 'v2'


class TestConfig:
    """Tests for Config class (target configuration)."""

    def test_load_target(self):
        """Test loading a real target."""
        config = Config.load_target('armsr-armv8')

        assert config.name == 'armsr-armv8'
        assert config.board == 'armsr'
        assert config.subtarget == 'armv8'
        assert config.arch == 'aarch64'

    def test_target_tuple(self):
        """Test target tuple generation."""
        config = Config.load_target('armsr-armv8')

        assert 'aarch64' in config.target_tuple
        assert 'linux' in config.target_tuple
        assert 'musl' in config.target_tuple

    def test_cross_compile(self):
        """Test cross-compile prefix."""
        config = Config.load_target('armsr-armv8')

        assert config.cross_compile.endswith('-')
        assert 'aarch64' in config.cross_compile

    def test_get_profile(self):
        """Test getting a profile."""
        config = Config.load_target('armsr-armv8')

        # Should have at least one profile
        assert len(config.profiles) > 0

        profile = config.get_profile(config.profiles[0]['name'])
        assert 'name' in profile

    def test_get_profile_not_found(self):
        """Test getting non-existent profile raises."""
        config = Config.load_target('armsr-armv8')

        with pytest.raises(ValueError, match="not found"):
            config.get_profile('nonexistent-profile-xyz')

    def test_get_default_profile_name(self):
        """Test getting default profile name."""
        config = Config.load_target('armsr-armv8')

        default_name = config.get_default_profile_name()
        assert default_name is not None
        assert isinstance(default_name, str)

    def test_get_profile_packages(self):
        """Test getting merged package list for profile."""
        config = Config.load_target('armsr-armv8')

        packages = config.get_profile_packages(config.get_default_profile_name())

        # Should include global defaults
        assert 'base-files' in packages
        assert 'busybox' in packages

    def test_paths_set(self):
        """Test that build paths are set correctly."""
        config = Config.load_target('armsr-armv8')

        assert config.build_dir is not None
        assert config.toolchain_dir is not None
        assert config.staging_dir is not None
        assert config.kernel_build_dir is not None
        assert config.dl_dir is not None

    def test_load_target_not_found(self):
        """Test loading non-existent target raises."""
        with pytest.raises(FileNotFoundError):
            Config.load_target('nonexistent-target-xyz')

    def test_toolchain_config(self):
        """Test toolchain configuration."""
        config = Config.load_target('armsr-armv8')

        assert 'libc' in config.toolchain
        assert 'gcc_version' in config.toolchain
        assert config.toolchain['libc'] in ('musl', 'glibc')

    def test_kernel_config(self):
        """Test kernel configuration."""
        config = Config.load_target('armsr-armv8')

        assert 'version' in config.kernel
        assert 'full_version' in config.kernel


class TestComputePackageContentHash:
    """Tests for compute_package_content_hash function."""

    def test_hash_is_consistent(self, temp_packages_dir, sample_package_yaml):
        """Test that hash is consistent for same content."""
        pkg_dir = create_package_dir(temp_packages_dir, 'test', sample_package_yaml)
        pkg = PackageConfig.load(pkg_dir)

        hash1 = compute_package_content_hash(pkg)
        hash2 = compute_package_content_hash(pkg)

        assert hash1 == hash2

    def test_hash_changes_with_version(self, temp_packages_dir, sample_package_yaml):
        """Test that hash changes when version changes."""
        pkg_dir = create_package_dir(temp_packages_dir, 'test', sample_package_yaml)
        pkg1 = PackageConfig.load(pkg_dir)
        hash1 = compute_package_content_hash(pkg1)

        # Modify version
        sample_package_yaml['version'] = '2.0.0'
        pkg_file = pkg_dir / 'package.yaml'
        with open(pkg_file, 'w') as f:
            yaml.dump(sample_package_yaml, f)

        pkg2 = PackageConfig.load(pkg_dir)
        hash2 = compute_package_content_hash(pkg2)

        assert hash1 != hash2

    def test_hash_length(self, temp_packages_dir, sample_package_yaml):
        """Test hash is 12 characters."""
        pkg_dir = create_package_dir(temp_packages_dir, 'test', sample_package_yaml)
        pkg = PackageConfig.load(pkg_dir)

        hash_val = compute_package_content_hash(pkg)

        assert len(hash_val) == 12

    def test_hash_includes_toolchain(self, temp_packages_dir, sample_package_yaml):
        """Test that toolchain info affects hash."""
        pkg_dir = create_package_dir(temp_packages_dir, 'test', sample_package_yaml)
        pkg = PackageConfig.load(pkg_dir)

        hash_no_tc = compute_package_content_hash(pkg)
        hash_with_tc = compute_package_content_hash(
            pkg, toolchain_info={'gcc_version': '14.0.0', 'libc': 'musl'}
        )

        assert hash_no_tc != hash_with_tc

    def test_hash_works_for_subpackage(self, temp_packages_dir):
        """Test hash works for subpackages too."""
        pkg_data = {
            'name': 'parent',
            'version': '1.0.0',
            'license': 'MIT',
            'source': {'type': 'tarball', 'url': 'https://example.com/test.tar.gz'},
            'build': {'system': 'make'},
            'subpackages': {
                'child': {
                    'description': 'Child package',
                },
            },
        }

        pkg_dir = create_package_dir(temp_packages_dir, 'parent', pkg_data)
        pkg = PackageConfig.load(pkg_dir)
        subpkg = pkg.get_subpackage('child')

        hash_val = compute_package_content_hash(subpkg)
        assert len(hash_val) == 12
