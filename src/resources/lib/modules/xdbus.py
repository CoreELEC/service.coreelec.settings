# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2009-2013 Stephan Raue (stephan@openelec.tv)
# Copyright (C) 2013 Lutz Fiebach (lufie@openelec.tv)
# Copyright (C) 2019-present Team LibreELEC (https://libreelec.tv)
# Copyright (C) 2020-present Team CoreELEC (https://coreelec.org)

# Note: This module is a stub. D-Bus event handling is now managed by
# dbus_utils.py using dbussy/ravel, with signal listeners in each module
# (bluetooth.py, connman.py) inheriting from their respective dbus_* Listener classes.

lock = threading.Lock()

class xdbus:

    ENABLED = False
    menu = {'99': {}}

    def __init__(self, oeMain):
        pass

    def start_service(self):
        pass

    def stop_service(self):
        pass

    def exit(self):
        pass

    def restart(self):
        pass
