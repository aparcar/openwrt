# SPDX-License-Identifier: GPL-2.0-only
#
# Copyright (C) 2017 OpenWrt.org

include $(TOPDIR)/rules.mk

ARCH:=mipsel
BOARD:=pistachio
BOARDNAME:=MIPS pistachio
FEATURES:=fpu usb usbgadget squashfs targz nand
CPU_TYPE:=24kc
CPU_SUBTYPE:=24kf

KERNEL_PATCHVER:=5.4

DEFAULT_PACKAGES += \
	kmod-gpio-button-hotplug \
	uboot-envtools

include $(INCLUDE_DIR)/target.mk

KERNELNAME:=vmlinux dtbs

$(eval $(call BuildTarget))
