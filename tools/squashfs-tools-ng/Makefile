include $(TOPDIR)/rules.mk

PKG_NAME:=squashfs-tools-ng
PKG_VERSION:=0.9
PKG_RELEASE:=1

PKG_SOURCE:=$(PKG_NAME)-$(PKG_VERSION).tar.gz
PKG_SOURCE_URL:=https://codeload.github.com/AgentD/squashfs-tools-ng/tar.gz/v$(PKG_VERSION)?
PKG_HASH:=da94cadbfa0c8e7cb9af7482e499ed1c51cb38cfd1e6eee63afbae7debec8a61

PKG_FIXUP:=autoreconf

include $(INCLUDE_DIR)/host-build.mk

define Host/Install
	$(INSTALL_DIR) $(STAGING_DIR_HOST)/bin/
	$(INSTALL_BIN) $(HOST_BUILD_DIR)/gensquashfs $(STAGING_DIR_HOST)/bin/gensquashfs
	$(INSTALL_BIN) $(HOST_BUILD_DIR)/tar2sqfs $(STAGING_DIR_HOST)/bin/tar2sqfs
endef

define Host/Clean
endef

$(eval $(call HostBuild))
