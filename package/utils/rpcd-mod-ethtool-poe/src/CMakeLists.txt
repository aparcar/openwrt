cmake_minimum_required(VERSION 3.13)

PROJECT(rpcd-mod-ethtool-poe C)

ADD_DEFINITIONS(-Os -Wall -Werror --std=gnu99 -g3 -Wmissing-declarations)

SET(CMAKE_SHARED_LIBRARY_LINK_C_FLAGS "")

ADD_LIBRARY(rpcd-mod-ethtool-poe MODULE ethtool-poe.c)
TARGET_LINK_LIBRARIES(rpcd-mod-ethtool-poe ubox ubus nl-tiny)
SET_TARGET_PROPERTIES(rpcd-mod-ethtool-poe PROPERTIES OUTPUT_NAME ethtool-poe PREFIX "")

INSTALL(TARGETS rpcd-mod-ethtool-poe LIBRARY DESTINATION lib/rpcd)
