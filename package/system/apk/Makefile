include $(TOPDIR)/rules.mk

PKG_NAME:=apk
PKG_VERSION:=3e6261392f990b541effe94b2515456e977bc79b
PKG_RELEASE:=$(AUTORELEASE)

PKG_SOURCE:=apk-tools-v$(PKG_VERSION).tar.gz
PKG_SOURCE_URL:=https://gitlab.alpinelinux.org/alpine/apk-tools/-/archive/$(PKG_VERSION)
PKG_HASH:=5fd2c8b9272f803760c377ac0f95ca3eec0ad6f6a29a3428e8319ac245ad3348
PKG_BUILD_DIR:=$(BUILD_DIR)/apk-tools-$(PKG_VERSION)
HOST_BUILD_DIR:=$(BUILD_DIR_HOST)/apk-tools-$(PKG_VERSION)

PKG_MAINTAINER:=Paul Spooren <mail@aparcar.org>
PKG_LICENSE:=GPL-2.0-only
PKG_LICENSE_FILES:=LICENSE
PKG_INSTALL:=1

include $(INCLUDE_DIR)/package.mk
include $(INCLUDE_DIR)/host-build.mk
include $(INCLUDE_DIR)/meson.mk

define Package/apk
  SECTION:=base
  CATEGORY:=Base system
  TITLE:=apk package manager
  DEPENDS:=+zlib +libopenssl +libpthread @!arc
  URL:=$(PKG_SOURCE_URL)
endef

MESON_HOST_ARGS += \
	-Ddocs=disabled

MESON_ARGS += \
	-Ddocs=disabled \
	-Dlua=disabled

HOST_LDFLAGS += \
	-Wl,-rpath=$(STAGING_DIR_HOSTPKG)/lib \
	-lpthread

define Package/apk/install
	$(INSTALL_DIR) $(1)/lib/apk/db

	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) $(PKG_INSTALL_DIR)/usr/bin/apk $(1)/usr/bin/apk

	$(INSTALL_DIR) $(1)/usr/lib
	$(INSTALL_DATA) $(PKG_INSTALL_DIR)/usr/lib/libapk.so.* $(1)/usr/lib/

	$(INSTALL_DIR) $(1)/etc/apk/
	echo $(ARCH) > $(1)/etc/apk/arch
	touch $(1)/etc/apk/world
endef

$(eval $(call BuildPackage,apk))
$(eval $(call HostBuild))
