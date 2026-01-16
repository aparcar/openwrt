# SPDX-License-Identifier: GPL-2.0-only
#
# Copyright (C) 2024 OpenWrt.org
#
# Minimal dependency resolution for SDK individual package builds
#
# This module provides lightweight dependency handling that avoids the
# exponential expansion of the default 5-level recursive resolver.
# Use SDK_MINIMAL_DEPS=1 to enable this mode.
#

ifdef SDK_MINIMAL_DEPS

# Get direct dependencies only (no recursive expansion)
# This returns only the immediate dependencies declared in DEPENDS
# without traversing their transitive dependencies.
#
# 1: package name
define find_direct_dependencies
$(strip $(foreach dep,$(call strip_deps,$(Package/$(1)/depends)),\
    $(wildcard $(STAGING_DIR)/pkginfo/$(dep).version)))
endef

# Check if required dependencies are available in staging
# Warns about missing dependencies but doesn't fail the build
#
# 1: package name
define check_staging_deps
$(foreach dep,$(call strip_deps,$(Package/$(1)/depends)),\
    $(if $(wildcard $(STAGING_DIR)/pkginfo/$(dep).version),,\
        $(warning [SDK] Package $(1): dependency '$(dep)' not found in staging)))
endef

# Minimal version of find_library_dependencies
# Only looks at direct dependencies instead of 5-level recursive expansion
#
# 1: package name
find_library_dependencies_minimal = \
	$(wildcard $(patsubst %,$(STAGING_DIR)/pkginfo/%.version, \
		$(filter-out $(BUILD_PACKAGES), \
			$(sort $(call strip_deps,$(Package/$(1)/depends))))))

# Override the default find_library_dependencies when SDK_MINIMAL_DEPS=1
find_library_dependencies = $(call find_library_dependencies_minimal,$(1))

# Helper to list what dependencies would be needed
# Useful for debugging and for the prebuilt installer script
#
# 1: package name
define list_package_deps
$(info Dependencies for $(1):)
$(foreach dep,$(call strip_deps,$(Package/$(1)/depends)),\
    $(info   - $(dep) $(if $(wildcard $(STAGING_DIR)/pkginfo/$(dep).version),[OK],[MISSING])))
endef

# Validate that all direct dependencies are available
# Returns non-empty string if any dependency is missing
#
# 1: package name
define missing_deps
$(strip $(foreach dep,$(call strip_deps,$(Package/$(1)/depends)),\
    $(if $(wildcard $(STAGING_DIR)/pkginfo/$(dep).version),,$(dep))))
endef

# Strict mode: fail build if dependencies are missing
ifdef SDK_STRICT_DEPS
define check_deps_strict
$(if $(call missing_deps,$(1)),\
    $(error [SDK] Package $(1) has missing dependencies: $(call missing_deps,$(1)). \
        Install them first with: make package/<dep>/compile or use scripts/sdk-install-deps.sh $(1)))
endef
endif

endif # SDK_MINIMAL_DEPS
