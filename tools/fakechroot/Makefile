#
# This is free software, licensed under the GNU General Public License v2.
# See /LICENSE for more information.
#
include $(TOPDIR)/rules.mk

PKG_NAME:=fakechroot
PKG_VERSION:=2.20.1
PKG_RELEASE:=$(AUTORELEASE)

PKG_SOURCE:=$(PKG_NAME)_$(PKG_VERSION)+ds.orig.tar.xz
PKG_SOURCE_URL:=@DEBIAN/pool/main/f/fakechroot
PKG_HASH:=138a31f79cfa3bd21718e424e0a7f640916a0586593934a854833cd6436409e4
PKG_LICENSE:=GPL-2.1
PKG_LICENSE_FILES:=COPYING
PKG_FIXUP:=autoreconf

include $(INCLUDE_DIR)/host-build.mk

$(eval $(call HostBuild))
