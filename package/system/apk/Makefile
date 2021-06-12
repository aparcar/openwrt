include $(TOPDIR)/rules.mk

PKG_NAME:=apk
PKG_RELEASE:=$(AUTORELEASE)

PKG_SOURCE_DATE:=2021-12-27
PKG_SOURCE_VERSION:=8d5784aaf5c36f0a67302a9cd332bf8befff4b32
PKG_HASH:=4c1cdce8cdec75aadd0f73f5d55d2b2ac518046b54e2031e02395c3ebad70cef

PKG_VERSION:=3.0.0~pre0-$(PKG_SOURCE_DATE)
PKG_SOURCE:=apk-tools-v$(PKG_SOURCE_VERSION).tar.gz
PKG_SOURCE_URL:=https://gitlab.alpinelinux.org/alpine/apk-tools/-/archive/$(PKG_SOURCE_VERSION)
PKG_BUILD_DIR:=$(BUILD_DIR)/apk-tools-$(PKG_SOURCE_VERSION)
HOST_BUILD_DIR:=$(BUILD_DIR_HOST)/apk-tools-$(PKG_SOURCE_VERSION)

PKG_MAINTAINER:=Paul Spooren <mail@aparcar.org>
PKG_LICENSE:=GPL-2.0-only
PKG_LICENSE_FILES:=LICENSE
PKG_INSTALL:=1

HOST_BUILD_DEPENDS:=lua/host lua-lzlib/host
PKG_BUILD_DEPENDS:=$(HOST_BUILD_DEPENDS)

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
	-Dlua_version=5.1 \
	-Ddocs=disabled
MESON_ARGS += \
	-Dlua_version=5.1 \
	-Ddocs=disabled

HOST_LDFLAGS += \
	-Wl,-rpath $(STAGING_DIR_HOSTPKG)/lib \
	-lpthread

TARGET_CFLAGS += $(FPIC)
HOST_CFLAGS += $(HOST_FPIC)

define Package/apk/install
	$(INSTALL_DIR) $(1)/lib/apk/db

	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) $(PKG_INSTALL_DIR)/usr/bin/apk $(1)/usr/bin/apk

	$(INSTALL_DIR) $(1)/usr/lib
	$(CP) $(PKG_INSTALL_DIR)/usr/lib/libapk.so.* $(1)/usr/lib/

	$(INSTALL_DIR) $(1)/etc/apk/
	echo $(ARCH_PACKAGES) > $(1)/etc/apk/arch
	touch $(1)/etc/apk/world
endef

$(eval $(call BuildPackage,apk))
$(eval $(call HostBuild))
