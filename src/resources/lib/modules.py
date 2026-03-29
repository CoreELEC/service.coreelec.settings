# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2020-present Team LibreELEC (https://libreelec.tv)
# Copyright (C) 2020-present Team CoreELEC (https://coreelec.org)

import defaults
import log


class Module(object):

    @log.log_function()
    def __init__(self):
        name = self.__class__.__name__
        settings = getattr(defaults, name, None)
        if settings:
            for key, value in settings.items():
                setattr(self, key, value)
                log.log(f'{name}.{key}={value}')

    def do_init(self):
        pass

    def exit(self):
        pass

    def start_service(self):
        pass

    def stop_service(self):
        pass
