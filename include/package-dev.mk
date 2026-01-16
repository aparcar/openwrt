# SPDX-License-Identifier: GPL-2.0-only
#
# Copyright (C) 2024 OpenWrt.org
#
# Development Package (-dev) Support
#
# This module provides support for generating -dev packages from
# Build/InstallDev content. Similar to Alpine Linux's approach,
# -dev packages contain only development files (headers, static
# libraries, pkg-config files) needed for building other packages.
#
# Usage:
#   In package Makefile, after defining Build/InstallDev:
#     $(eval $(call BuildPackage,mylib))
#     $(eval $(call BuildDevPackage,mylib))
#
#   Or automatically for all packages with Build/InstallDev:
#     Set PKG_BUILD_DEV_PACKAGE:=1
#

# Development file patterns
DEV_FILE_PATTERNS := \
	usr/include \
	usr/lib/*.a \
	usr/lib/*.la \
	usr/lib/pkgconfig \
	usr/share/pkgconfig \
	usr/share/aclocal \
	usr/lib/cmake

# Default -dev package section
DEV_SECTION := devel

# Generate a -dev package definition
#
# 1: base package name
# 2: (optional) custom description
define Package/dev-template
  SECTION:=$(DEV_SECTION)
  CATEGORY:=Development
  TITLE:=$(TITLE) (development files)
  DEPENDS:=+$(1)
  HIDDEN:=1
endef

# Install development files from staging to package
#
# 1: destination directory
define Package/dev-install-template
	$(INSTALL_DIR) $(1)/usr
	$(if $(wildcard $(STAGING_DIR)/usr/include),\
		$(CP) -a $(STAGING_DIR)/usr/include $(1)/usr/ 2>/dev/null || true)
	$(if $(wildcard $(STAGING_DIR)/usr/lib/*.a),\
		$(INSTALL_DIR) $(1)/usr/lib && \
		$(CP) $(STAGING_DIR)/usr/lib/*.a $(1)/usr/lib/ 2>/dev/null || true)
	$(if $(wildcard $(STAGING_DIR)/usr/lib/*.la),\
		$(INSTALL_DIR) $(1)/usr/lib && \
		$(CP) $(STAGING_DIR)/usr/lib/*.la $(1)/usr/lib/ 2>/dev/null || true)
	$(if $(wildcard $(STAGING_DIR)/usr/lib/pkgconfig),\
		$(INSTALL_DIR) $(1)/usr/lib && \
		$(CP) -a $(STAGING_DIR)/usr/lib/pkgconfig $(1)/usr/lib/ 2>/dev/null || true)
	$(if $(wildcard $(STAGING_DIR)/usr/share/pkgconfig),\
		$(INSTALL_DIR) $(1)/usr/share && \
		$(CP) -a $(STAGING_DIR)/usr/share/pkgconfig $(1)/usr/share/ 2>/dev/null || true)
	$(if $(wildcard $(STAGING_DIR)/usr/share/aclocal),\
		$(INSTALL_DIR) $(1)/usr/share && \
		$(CP) -a $(STAGING_DIR)/usr/share/aclocal $(1)/usr/share/ 2>/dev/null || true)
	$(if $(wildcard $(STAGING_DIR)/usr/lib/cmake),\
		$(INSTALL_DIR) $(1)/usr/lib && \
		$(CP) -a $(STAGING_DIR)/usr/lib/cmake $(1)/usr/lib/ 2>/dev/null || true)
endef

# Build a development package from Build/InstallDev output
#
# 1: base package name (the -dev suffix will be added automatically)
#
# This creates a <name>-dev package that contains development files
# from the staging directory after Build/InstallDev has run.
#
# Example:
#   $(eval $(call BuildPackage,libfoo))
#   $(eval $(call BuildDevPackage,libfoo))
#
# This creates both 'libfoo' and 'libfoo-dev' packages.
#
define BuildDevPackage
  # Only generate -dev package if Build/InstallDev is defined
  ifdef Build/InstallDev

  # Define the -dev package
  define Package/$(1)-dev
    $(call Package/dev-template,$(1))
  endef

  # Install development files
  define Package/$(1)-dev/install
	$$(call Package/dev-install-template,$$(1))
  endef

  # Register the -dev package
  $$(eval $$(call BuildPackage,$(1)-dev))

  endif # Build/InstallDev
endef

# Automatic -dev package generation
# When PKG_BUILD_DEV_PACKAGE:=1 is set, automatically create -dev packages
# for all packages that have Build/InstallDev defined.
#
ifdef PKG_BUILD_DEV_PACKAGE
  # Hook into BuildPackage to also generate -dev variant
  define _OrigBuildPackage
    $(call BuildPackage,$(1))
  endef

  define BuildPackageWithDev
    $(call _OrigBuildPackage,$(1))
    $(call BuildDevPackage,$(1))
  endef
endif

#
# SDK-specific -dev package handling
#
# When building in SDK mode with sandbox isolation, we want to:
# 1. Install only -dev packages to the sandbox staging
# 2. Skip runtime package installation
# 3. Provide minimal build dependencies
#

ifdef SDK_SANDBOX_ENABLED

# List of standard development dependencies that should always be available
SDK_BASE_DEV_DEPS := \
	libc-dev \
	libgcc-dev \
	libpthread-dev

# Check if a package is a -dev package
# Returns non-empty if package name ends in -dev
is_dev_package = $(filter %-dev,$(1))

# Get the -dev variant of a package name
# 1: package name
get_dev_package = $(if $(call is_dev_package,$(1)),$(1),$(1)-dev)

# Get the base package from a -dev package name
# 1: dev package name
get_base_package = $(patsubst %-dev,%,$(1))

# Filter dependencies to prefer -dev variants for SDK builds
#
# 1: list of dependencies
# Returns: dependencies with -dev variants where available
define filter_sdk_deps
$(foreach dep,$(1),\
  $(if $(wildcard $(STAGING_DIR)/pkginfo/$(call get_dev_package,$(dep)).version),\
    $(call get_dev_package,$(dep)),\
    $(dep)))
endef

endif # SDK_SANDBOX_ENABLED

#
# Development package metadata for package info
#
# Adds Build-Dev-Depends field to package dump info for packages that
# need specific -dev packages at build time.
#

# Hook to add dev-depends to dump info
ifdef DUMP
  define DevDependsInfo
    $(if $(PKG_BUILD_DEV_DEPENDS),Build-Dev-Depends: $(PKG_BUILD_DEV_DEPENDS)
    )
  endef
endif
