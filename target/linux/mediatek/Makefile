# Copyright (c) 2015 OpenWrt.org
#
include $(TOPDIR)/rules.mk

ARCH:=arm
BOARD:=mediatek
BOARDNAME:=MediaTek Ralink ARM
SUBTARGETS:=mt7622 mt7623 mt7629
FEATURES:=squashfs nand separate_ramdisk fpu

KERNEL_PATCHVER:=5.4
KERNEL_TESTING_PATCHVER:=5.10

DEFAULT_PACKAGES += \
	kmod-leds-gpio kmod-gpio-button-hotplug

include $(INCLUDE_DIR)/target.mk

$(eval $(call BuildTarget))
