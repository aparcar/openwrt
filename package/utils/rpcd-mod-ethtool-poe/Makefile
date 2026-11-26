include $(TOPDIR)/rules.mk

PKG_NAME:=rpcd-mod-ethtool-poe
PKG_VERSION:=1
PKG_RELEASE:=1

PKG_MAINTAINER:=Paul Spooren <paul.spooren@hs-nordhausen.de>
PKG_LICENSE:=GPL-2.0-or-later

PKG_BUILD_PARALLEL:=1

include $(INCLUDE_DIR)/package.mk
include $(INCLUDE_DIR)/cmake.mk

TARGET_CFLAGS += -I$(STAGING_DIR)/usr/include/libnl-tiny

define Package/rpcd-mod-ethtool-poe
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=rpcd PoE control via ethtool netlink
  DEPENDS:=+rpcd +libubox +libubus +libnl-tiny
  CONFLICTS:=rpcd-mod-poe realtek-poe
endef

define Package/rpcd-mod-ethtool-poe/description
  rpcd plugin exposing the ubus object "poe" to query and control
  IEEE 802.3 Power Sourcing Equipment (PoE) ports. It talks to the
  kernel PSE framework directly via ethtool netlink, no ethtool
  binary is required.

  The info/manage/reload methods mirror the realtek-poe daemon so
  existing clients such as luci-app-poe keep working; status and set
  expose all PSE attributes (priority, power limit, classes, ...).
endef

define Build/Prepare
	$(INSTALL_DIR) $(PKG_BUILD_DIR)
	$(CP) ./src/* $(PKG_BUILD_DIR)/
endef

define Package/rpcd-mod-ethtool-poe/install
	$(INSTALL_DIR) $(1)/usr/lib/rpcd
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/ethtool-poe.so $(1)/usr/lib/rpcd/
	$(INSTALL_DIR) $(1)/usr/share/rpcd/acl.d
	$(INSTALL_DATA) ./files/ethtool-poe.json $(1)/usr/share/rpcd/acl.d/
endef

define Package/rpcd-mod-ethtool-poe/postinst
#!/bin/sh
[ -n "$$IPKG_INSTROOT" ] || /etc/init.d/rpcd reload
endef

$(eval $(call BuildPackage,rpcd-mod-ethtool-poe))
