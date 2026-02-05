#
# Copyright (C) 2025 OpenWrt.org
#
# This is free software, licensed under the GNU General Public License v2.
# See /LICENSE for more information.
#

include $(TOPDIR)/rules.mk

PKG_NAME:=cloud-init
PKG_VERSION:=1
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0
PKG_MAINTAINER:=OpenWrt Contributors <openwrt-devel@lists.openwrt.org>

include $(INCLUDE_DIR)/package.mk

define Package/cloud-init
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=Simple cloud-init for OpenWrt
  DEPENDS:=+jshn +uci
  PKGARCH:=all
endef

define Package/cloud-init/description
  A lightweight cloud-init implementation for OpenWrt that supports
  automatic instance configuration from datasources commonly used in
  cloud and virtualized environments.

  Supported datasources:
  - NoCloud (seed directory or config drive labeled cidata)

  Supported configuration:
  - Hostname and timezone
  - Root password (hash)
  - SSH authorized keys
  - Network interfaces (via UCI)
  - File creation (write_files)
  - Command execution (runcmd)
endef

define Package/cloud-init/conffiles
/etc/config/cloud-init
endef

define Build/Compile
endef

define Package/cloud-init/install
	$(INSTALL_DIR) $(1)/etc/init.d
	$(INSTALL_BIN) ./files/etc/init.d/cloud-init $(1)/etc/init.d/cloud-init

	$(INSTALL_DIR) $(1)/etc/config
	$(INSTALL_CONF) ./files/etc/config/cloud-init $(1)/etc/config/cloud-init

	$(INSTALL_DIR) $(1)/usr/sbin
	$(INSTALL_BIN) ./files/usr/sbin/cloud-init $(1)/usr/sbin/cloud-init
endef

$(eval $(call BuildPackage,cloud-init))
