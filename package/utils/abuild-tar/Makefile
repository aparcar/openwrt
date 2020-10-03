include $(TOPDIR)/rules.mk

PKG_NAME:=abuild-tar
PKG_VERSION:=3.7.0
PKG_RELEASE:=$(AUTORELEASE)

PKG_SOURCE:=abuild-v$(PKG_VERSION).tar.gz
PKG_SOURCE_URL:=https://codeload.github.com/alpinelinux/abuild/tar.gz/v$(PKG_VERSION)?
PKG_HASH:=bf633e363d60fee598761b42ccb6152954de11e40a2077b922b382be4d7998b6
PKG_BUILD_DIR:=$(BUILD_DIR)/abuild-$(PKG_VERSION)
HOST_BUILD_DIR:=$(BUILD_DIR_HOST)/abuild-$(PKG_VERSION)

PKG_LICENSE:=GPL-2.0
PKG_LICENSE_FILES:=LICENSE

PKG_INSTALL:=1
HOST_BUILD_DEPENDS:=libressl/host zlib/host

include $(INCLUDE_DIR)/package.mk
include $(INCLUDE_DIR)/host-build.mk

define Package/abuild-tar
  SECTION:=base
  CATEGORY:=Base system
  TITLE:=abuild-tar util
  DEPENDS:=+zlib +libopenssl +libpthread
  URL:=$(PKG_SOURCE_URL)
endef

HOST_LDFLAGS += -lpthread

define Host/Compile
	$(MAKE) -C $(HOST_BUILD_DIR) $(HOST_MAKE_FLAGS) abuild-tar abuild-sign functions.sh
endef

define Package/abuild-tar/install
	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) $(PKG_INSTALL_DIR)/usr/bin/abuild-tar $(1)/bin/abuild-tar
endef

define Host/Install
	$(INSTALL_BIN) $(HOST_BUILD_DIR)/abuild-tar $(STAGING_DIR_HOST)/bin/abuild-tar
endef

$(eval $(call BuildPackage,abuild-tar))
$(eval $(call HostBuild))
