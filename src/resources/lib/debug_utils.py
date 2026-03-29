# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2020-present Team LibreELEC (https://libreelec.tv)
# Copyright (C) 2020-present Team CoreELEC (https://coreelec.org)

import inspect
import sys
from contextlib import contextmanager
from platform import uname
from pprint import pformat
from typing import List, Iterable, Tuple, Any, Optional

import xbmc


def _logger(message):
    xbmc.log(message, xbmc.LOGERROR)


def format_vars(variables: List[Tuple[str, Any]]) -> str:
    """
    Format variables dictionary

    :param variables: variables list
    :return: formatted string with sorted ``var = val`` pairs
    """
    var_list = [(var, val) for var, val in variables
                if not (var.startswith('__') and var.endswith('__'))]
    var_list.sort(key=lambda i: i[0])
    lines = []
    for var, val in var_list:
        lines.append('{} = {}'.format(var, pformat(val, indent=4)))
    return '\n'.join(lines)


def _format_code_context(code_context: List[str], lineno: int, index: int) -> str:
    context = ''
    if code_context is not None:
        for i, line in enumerate(code_context, lineno - index):
            if i == lineno:
                context += '{}:>{}'.format(str(i).rjust(5), line)
            else:
                context += '{}: {}'.format(str(i).rjust(5), line)
    return context


FRAME_INFO_TEMPLATE = """File:
{file_path}:{lineno}
----------------------------------------------------------------------------------------------------
Code context:
{code_context}
----------------------------------------------------------------------------------------------------
Local variables:
{local_vars}
====================================================================================================
"""


def format_frame_info(frame_info: tuple) -> str:
    """Get extended information about an execution frame"""
    return FRAME_INFO_TEMPLATE.format(
        file_path=frame_info[1],
        lineno=frame_info[2],
        code_context=_format_code_context(frame_info[4], frame_info[2],
                                          frame_info[5]),
        local_vars=format_vars(frame_info[0].f_locals.items())
    )


OBJECT_INFO_TEMPLATE = """
####################################################################################################
                                           Object info
----------------------------------------------------------------------------------------------------
Object      : {object}
Object type : {object_type}
Object attributes:
{object_attributes}
************************************** End of object info ******************************************
"""


def inspect_object(obj):
    """Get extended information about an object"""
    object_attributes = format_vars(inspect.getmembers(obj))
    object_info_string = OBJECT_INFO_TEMPLATE.format(
        object=obj,
        object_type=type(obj),
        object_attributes=object_attributes
    )
    return object_info_string


STACK_TRACE_TEMPLATE = """
####################################################################################################
                                            Stack Trace
====================================================================================================
{stack_trace}
"""


def format_stack_trace(frame_stack: Iterable[tuple]) -> str:
    """Get extended information about a frame stack"""
    stack_trace = ''
    for frame_info in frame_stack:
        stack_trace += format_frame_info(frame_info)
    return STACK_TRACE_TEMPLATE.format(stack_trace=stack_trace)


EXCEPTION_TEMPLATE = """
*********************************** Unhandled exception detected ***********************************
####################################################################################################
                                           Diagnostic info
----------------------------------------------------------------------------------------------------
Exception type  : {exc_type}
Exception value : {exc}
System info     : {system_info}
Python version  : {python_version}
Kodi version    : {kodi_version}
sys.argv        : {sys_argv}
----------------------------------------------------------------------------------------------------
sys.path:
{sys_path}
{stack_trace}
************************************* End of diagnostic info ***************************************
"""


def get_exception_message(exc: Exception,
                          outer_stack_trace: Optional[List[inspect.FrameInfo]] = None) -> str:
    """Get extended information about the currently handled exception"""
    stack_trace = inspect.trace(5)
    if outer_stack_trace is not None:
        stack_trace = outer_stack_trace + stack_trace
    stack_trace_string = format_stack_trace(stack_trace)
    message = EXCEPTION_TEMPLATE.format(
        exc_type=exc.__class__.__name__,
        exc=exc,
        system_info=uname(),
        python_version=sys.version.replace('\n', ' '),
        kodi_version=xbmc.getInfoLabel('System.BuildVersion'),
        sys_argv=sys.argv,
        sys_path=pformat(sys.path),
        stack_trace=stack_trace_string
    )
    return message


@contextmanager
def log_exception(logger_func=_logger):
    """
    Diagnostic helper context manager

    It controls execution within its context and writes extended
    diagnostic info to the Kodi log if an unhandled exception
    happens within the context. The info includes the following items:

    - System info
    - Python version
    - Kodi version
    - Module path.
    - Stack trace including:
        * File path and line number where the exception happened
        * Code fragment where the exception has happened.
        * Local variables at the moment of the exception.

    After logging the diagnostic info the exception is re-raised.

    Example::

        with debug_exception():
            # Some risky code
            raise RuntimeError('Fatal error!')

    :param logger_func: logger function that accepts a single argument
        that is a log message.
    """
    try:
        yield
    except Exception as exc:
        message = get_exception_message(exc)
        logger_func(message)
        raise exc
