#
# Copyright (C) 2006-2010 OpenWrt.org
#
# This is free software, licensed under the GNU General Public License v2.
# See /LICENSE for more information.
#

curdir:=package

include $(INCLUDE_DIR)/feeds.mk
include $(INCLUDE_DIR)/rootfs.mk

-include $(TMP_DIR)/.packagedeps
package-y += kernel/linux
$(curdir)/autoremove:=1
$(curdir)/builddirs:=$(sort $(package-) $(package-y) $(package-m))
$(curdir)/builddirs-default:=. $(sort $(package-y) $(package-m))
$(curdir)/builddirs-prereq:=. $(sort $(prereq-y) $(prereq-m))
ifdef CHECK_ALL
$(curdir)/builddirs-check:=$($(curdir)/builddirs)
$(curdir)/builddirs-download:=$($(curdir)/builddirs)
endif
ifneq ($(IGNORE_ERRORS),)
  package-y-filter := $(package-y)
  package-m-filter := $(filter-out $(package-y),$(package-m))
  package-n-filter := $(filter-out $(package-y) $(package-m),$(package-))
  package-ignore-errors := $(filter n m y,$(IGNORE_ERRORS))
  package-ignore-errors := $(if $(package-ignore-errors),$(package-ignore-errors),n m)
  package-ignore-subdirs := $(sort $(foreach m,$(package-ignore-errors),$(package-$(m)-filter)))
  $(curdir)/builddirs-ignore-download := $(package-ignore-subdirs)
  $(curdir)/builddirs-ignore-compile := $(package-ignore-subdirs)
  $(curdir)/builddirs-ignore-host-download := $(package-ignore-subdirs)
  $(curdir)/builddirs-ignore-host-compile := $(package-ignore-subdirs)
endif

PACKAGE_INSTALL_FILES:= \
	$(foreach pkg,$(sort $(package-y)), \
		$(foreach variant, \
			$(if $(strip $(package/$(pkg)/variants)), \
				$(package/$(pkg)/variants), \
				$(if $(package/$(pkg)/default-variant), \
					$(package/$(pkg)/default-variant), \
					default \
				) \
			), \
			$(PKG_INFO_DIR)/$(lastword $(subst /,$(space),$(pkg))).$(variant).install \
		) \
	)

$(curdir)/cleanup: $(TMP_DIR)/.build
	rm -rf $(STAGING_DIR_ROOT)

$(curdir)/merge:
	rm -rf $(PACKAGE_DIR_ALL)
	mkdir -p $(PACKAGE_DIR_ALL)
	-$(foreach pdir,$(PACKAGE_SUBDIRS),$(if $(wildcard $(pdir)/*.apk),ln -s $(pdir)/*.apk $(PACKAGE_DIR_ALL);))

$(curdir)/merge-index: $(curdir)/merge
	(cd $(PACKAGE_DIR_ALL) && $(STAGING_DIR_HOST)/bin/apk mkndx \
			--root $(TOPDIR) \
			--keys-dir $(TOPDIR) \
			--sign $(BUILD_KEY_APK_SEC) \
			--output APKINDEX.tar.gz \
			*.apk; \
	)

ifndef SDK
  $(curdir)/compile: $(curdir)/system/apk/host/compile
endif

$(curdir)/install: $(TMP_DIR)/.build $(curdir)/merge $(curdir)/merge-index
	- find $(STAGING_DIR_ROOT) -type d | $(XARGS) chmod 0755
	rm -rf $(TARGET_DIR) $(TARGET_DIR_ORIG)
	mkdir -p $(TARGET_DIR)/tmp

	$(file >$(TMP_DIR)/apk_install_list,\
	    $(foreach pkg,$(shell cat $(PACKAGE_INSTALL_FILES) 2>/dev/null),$(pkg)$(call GetABISuffix,$(pkg))))
	
	$(call apk,$(TARGET_DIR)) add --initdb --arch $(ARCH_PACKAGES) $$(cat $(TMP_DIR)/apk_install_list)

	$(CP) $(TARGET_DIR) $(TARGET_DIR_ORIG)

	$(call prepare_rootfs,$(TARGET_DIR),$(TOPDIR)/files)

$(curdir)/index: FORCE
	@echo Generating package index...
	-for d in $(PACKAGE_SUBDIRS); do ( \
		mkdir -p $$d; \
		cd $$d || continue; \
		$(STAGING_DIR_HOST)/bin/apk mkndx \
			--root $(TOPDIR) \
			--keys-dir $(TOPDIR) \
			--sign $(BUILD_KEY_APK_SEC) \
			--output APKINDEX.tar.gz \
			*.apk; \
	); done

$(curdir)/flags-install:= -j1

$(eval $(call stampfile,$(curdir),package,prereq,.config))
$(eval $(call stampfile,$(curdir),package,cleanup,$(TMP_DIR)/.build))
$(eval $(call stampfile,$(curdir),package,compile,$(TMP_DIR)/.build))
$(eval $(call stampfile,$(curdir),package,install,$(TMP_DIR)/.build))
$(eval $(call stampfile,$(curdir),package,check,$(TMP_DIR)/.build))

$(eval $(call subdir,$(curdir)))
