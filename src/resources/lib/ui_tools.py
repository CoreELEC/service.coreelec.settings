# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2020-present Team LibreELEC (https://libreelec.tv)
# Copyright (C) 2020-present Team CoreELEC (https://coreelec.org)

import xbmcaddon
import xbmcgui

import log


ADDON = xbmcaddon.Addon()
ADDON_ICON = ADDON.getAddonInfo('icon')
ADDON_NAME = ADDON.getAddonInfo('name')


@log.log_function()
def notification(message, heading=ADDON_NAME, icon=ADDON_ICON):
    xbmcgui.Dialog().notification(heading, message, icon)
