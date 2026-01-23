"""
Pytest fixtures for OpenWrt PoC build system tests.
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any

import yaml


@pytest.fixture
def temp_packages_dir(tmp_path):
    """Create a temporary packages directory for testing."""
    packages_dir = tmp_path / "packages"
    packages_dir.mkdir()
    return packages_dir


@pytest.fixture
def mock_target_config():
    """Create a mock target configuration."""
    return {
        'name': 'test-target',
        'arch': 'aarch64',
        'toolchain': {
            'gcc_version': '14.2.0',
            'libc': 'musl',
        },
        'default_packages': ['base-files'],
    }


@pytest.fixture
def sample_package_yaml():
    """Return a sample package YAML dict."""
    return {
        'name': 'test-pkg',
        'version': '1.0.0',
        'release': 1,
        'license': 'MIT',
        'source': {
            'type': 'tarball',
            'url': 'https://example.com/test-1.0.0.tar.gz',
            'sha256': 'abc123',
        },
        'dependencies': {
            'runtime': [],
            'build': [],
        },
        'build': {
            'system': 'autotools',
        },
    }


@pytest.fixture
def package_with_provides():
    """Return a package YAML dict with provides."""
    return {
        'name': 'mbedtls',
        'version': '3.6.5',
        'release': 1,
        'license': 'Apache-2.0',
        'provides': ['libssl'],
        'default_variant': True,
        'source': {
            'type': 'tarball',
            'url': 'https://example.com/mbedtls-3.6.5.tar.gz',
            'sha256': 'abc123',
        },
        'dependencies': {
            'runtime': [],
            'build': [],
        },
        'build': {
            'system': 'cmake',
        },
    }




@pytest.fixture
def subpackage_with_provides():
    """Return a package YAML with a subpackage that has provides."""
    return {
        'name': 'openssl',
        'version': '3.0.0',
        'release': 1,
        'license': 'Apache-2.0',
        'source': {
            'type': 'tarball',
            'url': 'https://example.com/openssl-3.0.0.tar.gz',
            'sha256': 'def456',
        },
        'dependencies': {
            'runtime': [],
            'build': [],
        },
        'build': {
            'system': 'custom',
        },
        'subpackages': {
            'libopenssl': {
                'description': 'OpenSSL library',
                'provides': ['libssl'],
                'files': [
                    {'src': 'usr/lib/*.so*', 'dst': '/usr/lib/'},
                ],
            },
        },
    }


def create_package_dir(packages_dir: Path, name: str, pkg_data: Dict[str, Any]) -> Path:
    """Helper to create a package directory with package.yaml."""
    pkg_dir = packages_dir / name
    pkg_dir.mkdir(parents=True, exist_ok=True)

    pkg_file = pkg_dir / 'package.yaml'
    with open(pkg_file, 'w') as f:
        yaml.dump(pkg_data, f)

    return pkg_dir
