# SPDX-License-Identifier: GPL-2.0-only

include $(TOPDIR)/rules.mk

ARCH:=aarch64
BOARD:=bcm4908
BOARDNAME:=Broadcom BCM4908 (ARMv8A CPUs Brahma-B53)
FEATURES:=squashfs nand usb pci pcie gpio source-only
CPU_TYPE:=cortex-a53
SUBTARGETS:=generic

KERNEL_PATCHVER:=5.4
KERNEL_TESTING_PATCHVER:=5.4

define Target/Description
	Build firmware images for Broadcom BCM4908 SoC family routers.
endef

DEFAULT_PACKAGES += kmod-usb-ohci kmod-usb2 kmod-usb3

include $(INCLUDE_DIR)/target.mk

KERNELNAME:=Image dtbs

$(eval $(call BuildTarget))
