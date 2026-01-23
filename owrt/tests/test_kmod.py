"""Tests for kernel module packaging (kmod.py)."""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import tempfile
import shutil

from owrt.kmod import KmodDefinition, KmodRegistry


class TestKmodDefinition:
    """Tests for KmodDefinition dataclass."""

    def test_package_name(self):
        """Test package name generation."""
        kmod = KmodDefinition(name="usb-core")
        assert kmod.package_name == "kmod-usb-core"

    def test_package_name_with_dashes(self):
        """Test package name with multiple dashes."""
        kmod = KmodDefinition(name="crypto-hw-safexcel")
        assert kmod.package_name == "kmod-crypto-hw-safexcel"

    def test_autoload_priority_default(self):
        """Test default autoload priority."""
        kmod = KmodDefinition(name="test")
        assert kmod.autoload_priority == 50

    def test_autoload_priority_custom(self):
        """Test custom autoload priority."""
        kmod = KmodDefinition(name="test", autoload={'priority': 10})
        assert kmod.autoload_priority == 10

    def test_autoload_modules_default(self):
        """Test default autoload modules (empty list)."""
        kmod = KmodDefinition(name="test")
        assert kmod.autoload_modules == []

    def test_autoload_modules_custom(self):
        """Test custom autoload modules."""
        kmod = KmodDefinition(
            name="test",
            autoload={'modules': ['test_mod1', 'test_mod2']}
        )
        assert kmod.autoload_modules == ['test_mod1', 'test_mod2']

    def test_autoload_boot_default(self):
        """Test default autoload boot (False)."""
        kmod = KmodDefinition(name="test")
        assert kmod.autoload_boot is False

    def test_autoload_boot_true(self):
        """Test autoload boot set to True."""
        kmod = KmodDefinition(name="test", autoload={'boot': True})
        assert kmod.autoload_boot is True

    def test_get_kconfig_options_empty(self):
        """Test kconfig options when none defined."""
        kmod = KmodDefinition(name="test")
        assert kmod.get_kconfig_options() == []

    def test_get_kconfig_options_single(self):
        """Test single kconfig option."""
        kmod = KmodDefinition(
            name="test",
            kconfig=[{'name': 'CONFIG_USB', 'value': 'm'}]
        )
        assert kmod.get_kconfig_options() == ['CONFIG_USB=m']

    def test_get_kconfig_options_multiple(self):
        """Test multiple kconfig options."""
        kmod = KmodDefinition(
            name="test",
            kconfig=[
                {'name': 'CONFIG_USB', 'value': 'm'},
                {'name': 'CONFIG_USB_SUPPORT', 'value': 'y'},
            ]
        )
        options = kmod.get_kconfig_options()
        assert 'CONFIG_USB=m' in options
        assert 'CONFIG_USB_SUPPORT=y' in options

    def test_get_kconfig_options_default_value(self):
        """Test kconfig option with default value."""
        kmod = KmodDefinition(
            name="test",
            kconfig=[{'name': 'CONFIG_TEST'}]  # No value specified
        )
        assert kmod.get_kconfig_options() == ['CONFIG_TEST=m']


class TestKmodRegistry:
    """Tests for KmodRegistry."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for tests."""
        tmpdir = tempfile.mkdtemp()
        yield Path(tmpdir)
        shutil.rmtree(tmpdir)

    @pytest.fixture
    def sample_kmods_yaml(self, temp_dir):
        """Create a sample kmods.yaml file."""
        owrt_dir = temp_dir / 'owrt'
        owrt_dir.mkdir()
        
        kmods_content = """
version: '1.0'
categories:
- name: USB Support
  modules:
  - name: usb-core
    title: USB Core support
    description: Kernel support for USB
    kconfig:
    - name: CONFIG_USB
      value: m
    files:
    - drivers/usb/core/usbcore.ko
    autoload:
      priority: 40
      modules:
      - usbcore
      boot: true
    depends: []
  - name: usb-storage
    title: USB Storage support
    description: USB Mass Storage
    kconfig:
    - name: CONFIG_USB_STORAGE
      value: m
    files:
    - drivers/usb/storage/usb-storage.ko
    autoload:
      priority: 50
      modules:
      - usb-storage
      boot: false
    depends:
    - kmod-usb-core
- name: Cryptographic API modules
  modules:
  - name: crypto-hash
    title: CryptoAPI hash support
    description: Hash support
    kconfig:
    - name: CONFIG_CRYPTO_HASH
      value: m
    files:
    - crypto/crypto_hash.ko
    autoload:
      priority: 2
      boot: true
    depends: []
    hidden: true
"""
        (owrt_dir / 'kmods.yaml').write_text(kmods_content)
        return temp_dir

    def test_load_kmods(self, sample_kmods_yaml):
        """Test loading kmod definitions."""
        registry = KmodRegistry(sample_kmods_yaml)
        registry.load()
        
        assert len(registry._definitions) == 3
        assert 'usb-core' in registry._definitions
        assert 'usb-storage' in registry._definitions
        assert 'crypto-hash' in registry._definitions

    def test_get_kmod(self, sample_kmods_yaml):
        """Test getting a specific kmod."""
        registry = KmodRegistry(sample_kmods_yaml)
        
        kmod = registry.get('usb-core')
        assert kmod is not None
        assert kmod.name == 'usb-core'
        assert kmod.title == 'USB Core support'
        assert kmod.package_name == 'kmod-usb-core'

    def test_get_nonexistent_kmod(self, sample_kmods_yaml):
        """Test getting a kmod that doesn't exist."""
        registry = KmodRegistry(sample_kmods_yaml)
        
        kmod = registry.get('nonexistent')
        assert kmod is None

    def test_get_all(self, sample_kmods_yaml):
        """Test getting all kmod definitions."""
        registry = KmodRegistry(sample_kmods_yaml)
        
        all_kmods = registry.get_all()
        assert len(all_kmods) == 3
        assert 'usb-core' in all_kmods
        assert 'usb-storage' in all_kmods

    def test_get_categories(self, sample_kmods_yaml):
        """Test getting category names."""
        registry = KmodRegistry(sample_kmods_yaml)
        
        categories = registry.get_categories()
        assert 'USB Support' in categories
        assert 'Cryptographic API modules' in categories

    def test_get_by_category(self, sample_kmods_yaml):
        """Test getting kmods by category."""
        registry = KmodRegistry(sample_kmods_yaml)
        
        usb_kmods = registry.get_by_category('USB Support')
        assert 'usb-core' in usb_kmods
        assert 'usb-storage' in usb_kmods
        assert 'crypto-hash' not in usb_kmods

    def test_get_kconfig_for_modules(self, sample_kmods_yaml):
        """Test getting kconfig options for multiple modules."""
        registry = KmodRegistry(sample_kmods_yaml)
        
        options = registry.get_kconfig_for_modules(['usb-core', 'usb-storage'])
        assert 'CONFIG_USB=m' in options
        assert 'CONFIG_USB_STORAGE=m' in options

    def test_hidden_module_loaded(self, sample_kmods_yaml):
        """Test that hidden modules are still loaded."""
        registry = KmodRegistry(sample_kmods_yaml)
        
        kmod = registry.get('crypto-hash')
        assert kmod is not None
        assert kmod.hidden is True

    def test_kmod_dependencies(self, sample_kmods_yaml):
        """Test kmod dependency parsing."""
        registry = KmodRegistry(sample_kmods_yaml)
        
        kmod = registry.get('usb-storage')
        assert 'kmod-usb-core' in kmod.depends

    def test_kmod_files(self, sample_kmods_yaml):
        """Test kmod file paths."""
        registry = KmodRegistry(sample_kmods_yaml)
        
        kmod = registry.get('usb-core')
        assert 'drivers/usb/core/usbcore.ko' in kmod.files

    def test_missing_kmods_yaml(self, temp_dir):
        """Test behavior when kmods.yaml doesn't exist."""
        registry = KmodRegistry(temp_dir)
        registry.load()
        
        # Should not raise, just have empty definitions
        assert len(registry._definitions) == 0

    def test_load_only_once(self, sample_kmods_yaml):
        """Test that load() only loads once."""
        registry = KmodRegistry(sample_kmods_yaml)
        
        registry.load()
        count_after_first = len(registry._definitions)
        
        registry.load()  # Second call should be no-op
        count_after_second = len(registry._definitions)
        
        assert count_after_first == count_after_second == 3
