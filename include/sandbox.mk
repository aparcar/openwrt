# SPDX-License-Identifier: GPL-2.0-only
#
# Copyright (C) 2024 OpenWrt.org
#
# SDK Sandbox Integration
#
# This module integrates bubblewrap sandboxing into the OpenWrt build system.
# Enable with CONFIG_SDK_SANDBOX=y or SDK_SANDBOX=1 on command line.
#

# Check if sandbox mode is enabled
SDK_SANDBOX_ENABLED := $(if $(CONFIG_SDK_SANDBOX),1,$(SDK_SANDBOX))

ifdef SDK_SANDBOX_ENABLED

# Path to sandbox script
SDK_SANDBOX_SCRIPT := $(SCRIPT_DIR)/sdk-sandbox.sh

# Check if sandbox script exists
ifeq ($(wildcard $(SDK_SANDBOX_SCRIPT)),)
  $(error SDK Sandbox enabled but script not found: $(SDK_SANDBOX_SCRIPT))
endif

# Check if bubblewrap is available
BWRAP_CHECK := $(shell command -v bwrap 2>/dev/null)
ifeq ($(BWRAP_CHECK),)
  $(warning Bubblewrap (bwrap) not found - sandbox builds will fail)
  $(warning Install with: apt install bubblewrap / dnf install bubblewrap)
endif

# Default sandbox options
SDK_SANDBOX_OPTS :=

# Network access configuration
# Default: disabled (secure)
# Per-package: set PKG_BUILD_NETWORK:=1 in package Makefile
# Global: set CONFIG_SDK_SANDBOX_NETWORK=y
ifdef CONFIG_SDK_SANDBOX_NETWORK
  SDK_SANDBOX_OPTS += --network
endif

# Export for per-package network opt-in
export PKG_BUILD_NETWORK

# Debug mode
ifdef SDK_SANDBOX_DEBUG
  SDK_SANDBOX_OPTS += --debug
endif

# Define the sandbox wrapper command
# This wraps build commands to run inside the sandbox
define SandboxWrap
$(SDK_SANDBOX_SCRIPT) $(SDK_SANDBOX_OPTS) \
	--keep-env=MAKEFLAGS \
	--keep-env=V \
	--keep-env=VERBOSE \
	--workdir=$(TOPDIR) \
	-- $(1)
endef

# Wrap shell commands for sandboxed execution
SANDBOX_SHELL = $(call SandboxWrap,/bin/sh -c)

# Hook into Build/Compile to run in sandbox
# This preserves the original Build/Compile and wraps it
#
# Usage in package Makefile:
#   PKG_BUILD_NETWORK:=1  # Optional: allow network during build
#
define Build/Compile/Sandbox
	$(call SandboxWrap,$(MAKE) -C $(PKG_BUILD_DIR) \
		$(PKG_JOBS) \
		$(MAKE_FLAGS) \
		$(1))
endef

# Information target to show sandbox status
.PHONY: sandbox-info
sandbox-info:
	@echo "SDK Sandbox Configuration:"
	@echo "  Enabled: yes"
	@echo "  Script: $(SDK_SANDBOX_SCRIPT)"
	@echo "  Bubblewrap: $(if $(BWRAP_CHECK),$(BWRAP_CHECK),NOT FOUND)"
	@echo "  Network default: $(if $(CONFIG_SDK_SANDBOX_NETWORK),enabled,disabled)"
	@echo ""
	@echo "Usage:"
	@echo "  make SDK_SANDBOX=1 package/foo/compile"
	@echo ""
	@echo "Per-package network access:"
	@echo "  Add PKG_BUILD_NETWORK:=1 to package Makefile"

else # SDK_SANDBOX_ENABLED

# Sandbox not enabled - provide no-op wrapper
define SandboxWrap
$(1)
endef

SANDBOX_SHELL = /bin/sh -c

define Build/Compile/Sandbox
	$(MAKE) -C $(PKG_BUILD_DIR) \
		$(PKG_JOBS) \
		$(MAKE_FLAGS) \
		$(1)
endef

.PHONY: sandbox-info
sandbox-info:
	@echo "SDK Sandbox: disabled"
	@echo ""
	@echo "Enable with:"
	@echo "  make menuconfig  # Enable CONFIG_SDK_SANDBOX"
	@echo "  or"
	@echo "  make SDK_SANDBOX=1 package/foo/compile"

endif # SDK_SANDBOX_ENABLED

# Export sandbox status for child processes
export SDK_SANDBOX_ENABLED
export SDK_IN_SANDBOX
