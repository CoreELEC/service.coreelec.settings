# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2020-present Team LibreELEC (https://libreelec.tv)
# Copyright (C) 2020-present Team CoreELEC (https://coreelec.org)

import inspect
import os
import pprint
import sys
from functools import wraps

from debug_utils import get_exception_message, format_stack_trace, inspect_object


DEBUG = 0
INFO = 1
WARNING = 2
ERROR = 3
FATAL = 4
NONE = 5

_DEFAULT = DEBUG
_HEADER = 'SETTINGS: '


try:
    import xbmc
    _NO_DEBUG_ENV = os.environ.get('DEBUG', 'no') == 'no'
    def _log(message, level=_DEFAULT):
        if level == DEBUG and _NO_DEBUG_ENV:
            return
        xbmc.log(message, level)
except ModuleNotFoundError:
    def _log(message, level=_DEFAULT):
        print(message)


def log(message, level=_DEFAULT):
    _log(f'{_HEADER}{sys._getframe().f_back.f_code.co_name} # {message}', level)


def log_stack_trace(message='', level=_DEFAULT):
    """
    Log extended stack trace from the point of execution to the starting frame

    The stack trace includes code fragments (if available) and local variables for each frame.
    """
    message = _HEADER + message
    frame_stack = list(reversed(inspect.stack(5)))[:-1]
    stack_trace_string = format_stack_trace(frame_stack)
    if message:
        message += '\n' + stack_trace_string
    _log(_HEADER + message, level)


def log_object_state(obj, level=_DEFAULT):
    """Log all object attributes"""
    object_info_string = inspect_object(obj)
    _log(_HEADER + object_info_string, level)


def log_function(level=_DEFAULT):
    def _log_function_1(function):
        header = f'{_HEADER}{function.__qualname__} '
        @wraps(function)
        def _log_function_2(*args, **kwargs):
            try:
                _log(f'{header}-', level)
                for arg in args:
                    _log(f'{header}< {pprint.pformat(arg)}', level)
                for key, value in kwargs.items():
                    _log(f'{header}< {key}={pprint.pformat(value)}', level)
                result = function(*args, **kwargs)
                _log(f'{header}> {pprint.pformat(result)}', level)
                return result
            except Exception as e:
                _log(f'{header}# {repr(e)}', ERROR)
                outer_stack = list(reversed(inspect.stack(5)))[:-1]
                exception_message = get_exception_message(e, outer_stack)
                _log(header + exception_message, ERROR)
                if 'self' in inspect.getfullargspec(function).args and args:
                    obj = args[0]
                    object_state = inspect_object(obj)
                    _log(header + object_state, ERROR)
                e.__traceback__ = None
        return _log_function_2
    return _log_function_1


def utf8ify(pstr):
    return pstr.encode('utf-8', 'replace').decode('utf-8')

def asciify(pstr):
    return pstr.encode('ascii', 'replace').decode('utf-8')
