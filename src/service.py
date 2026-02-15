# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2009-2013 Stephan Raue (stephan@openelec.tv)
# Copyright (C) 2013 Lutz Fiebach (lufie@openelec.tv)
# Copyright (C) 2019-present Team LibreELEC (https://libreelec.tv)
# Copyright (C) 2020-present Team CoreELEC (https://coreelec.org)

import asyncio
import oe
import dbus_utils
import xbmc
import xbmcgui
import time
import threading
import socket
import os
import xbmcaddon
import filecmp

class service_thread(threading.Thread):

    def __init__(self, oeMain):
        try:
            oeMain.dbg_log('_service_::__init__', 'enter_function', oeMain.LOGDEBUG)
            self.oe = oeMain
            self.wait_evt = threading.Event()
            self.socket_file = '/var/run/service.coreelec.settings.sock'
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.setblocking(1)
            if os.path.exists(self.socket_file):
                os.remove(self.socket_file)
            self.sock.bind(self.socket_file)
            self.sock.listen(1)
            self.stopped = False
            threading.Thread.__init__(self)
            self.daemon = True
            self.oe.dbg_log('_service_::__init__', 'exit_function', self.oe.LOGDEBUG)
        except Exception as e:
            self.oe.dbg_log('_service_::__init__', 'ERROR: (' + repr(e) + ')')

    def stop(self):
        try:
            self.oe.dbg_log('_service_::stop', 'enter_function', self.oe.LOGDEBUG)
            self.stopped = True
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(self.socket_file)
            sock.send(bytes('exit', 'utf-8'))
            sock.close()
            self.sock.close()
            self.oe.dbg_log('_service_::stop', 'exit_function', self.oe.LOGDEBUG)
        except Exception as e:
            self.oe.dbg_log('_service_::stop', 'ERROR: (' + repr(e) + ')')

    def run(self):
        try:
            self.oe.dbg_log('_service_::run', 'enter_function', self.oe.LOGDEBUG)
            if self.oe.read_setting('coreelec', 'wizard_completed') == None:
                threading.Thread(target=self.oe.openWizard).start()
            elif self.oe.BOOT_HINT == 'UPDATE' and self.oe.HAS_RNOTES:
                threading.Thread(target=self.oe.showPopUp, args=('Release Notes: CoreELEC %s' % oe.VERSION, '/etc/release-notes',)).start()

            if os.path.isfile('/tmp/tee.message'):
                threading.Thread(target=self.oe.showPopUp, args=('CoreELEC Media Playback', '/tmp/tee.message',)).start()

            if os.path.isfile('/tmp/dovi.message'):
                threading.Thread(target=self.oe.showPopUp, args=('CoreELEC Dolby Vision Media Playback', '/tmp/dovi.message',)).start()

            if os.path.isfile('/tmp/firmware.message'):
                shownotify=True
                if os.path.isfile('%s/firmware.message' % self.oe.CONFIG_CACHE):
                    shownotify=not filecmp.cmp('/tmp/firmware.message', '%s/firmware.message' % self.oe.CONFIG_CACHE, shallow=False)
                if shownotify:
                    self.oe.copy_file('/tmp/firmware.message', '%s/firmware.message' % self.oe.CONFIG_CACHE, silent=True)
                    self.oe.notify(self.oe._(32363), None, path='/tmp/firmware.message', timeout=120000)
            else:
                if os.path.isfile('%s/firmware.message' % self.oe.CONFIG_CACHE):
                    os.remove('%s/firmware.message' % self.oe.CONFIG_CACHE)
                    self.oe.notify(self.oe._(32363),
                      'Android firmware has been updated, and now %s offers the best possible media support.' % self.oe._(32363),
                      path=None,timeout=20000)

            while self.stopped == False:
                self.oe.dbg_log('_service_::run', 'WAITING:', self.oe.LOGINFO)
                conn, addr = self.sock.accept()
                message = (conn.recv(1024)).decode('utf-8')
                self.oe.dbg_log('_service_::run', 'MESSAGE:' + message, self.oe.LOGINFO)
                conn.close()
                if message == 'openConfigurationWindow':
                    if not hasattr(self.oe, 'winOeMain'):
                        threading.Thread(target=self.oe.openConfigurationWindow).start()
                    else:
                        if self.oe.winOeMain.visible != True:
                            threading.Thread(target=self.oe.openConfigurationWindow).start()
                if message == 'exit':
                    self.stopped = True
            self.oe.dbg_log('_service_::run', 'exit_function', self.oe.LOGDEBUG)
        except Exception as e:
            self.oe.dbg_log('_service_::run', 'ERROR: (' + repr(e) + ')')


class cxbmcm(xbmc.Monitor):

    def __init__(self, *args, **kwargs):
        xbmc.Monitor.__init__(self)

    def onScreensaverActivated(self):
        oe.__oe__.dbg_log('c_xbmcm::onScreensaverActivated', 'enter_function', oe.__oe__.LOGDEBUG)
        if oe.__oe__.read_setting('bluetooth', 'standby'):
            threading.Thread(target=oe.__oe__.standby_devices).start()
        oe.__oe__.dbg_log('c_xbmcm::onScreensaverActivated', 'exit_function', oe.__oe__.LOGDEBUG)

    def onDPMSActivated(self):
        oe.__oe__.dbg_log('c_xbmcm::onDPMSActivated', 'enter_function', oe.__oe__.LOGDEBUG)
        if oe.__oe__.read_setting('bluetooth', 'standby'):
            threading.Thread(target=oe.__oe__.standby_devices).start()
        oe.__oe__.dbg_log('c_xbmcm::onDPMSActivated', 'exit_function', oe.__oe__.LOGDEBUG)

    def onAbortRequested(self):
        pass


xbmcm = cxbmcm()
dbus_utils.LOOP_THREAD.start()
oe.load_modules()
oe.start_service()
monitor = service_thread(oe.__oe__)
monitor.start()

while not xbmcm.abortRequested():
    if xbmcm.waitForAbort(60):
        break

    if not oe.__oe__.read_setting('bluetooth', 'standby'):
        continue

    timeout = oe.__oe__.read_setting('bluetooth', 'idle_timeout')
    if not timeout:
        continue

    try:
        timeout = int(timeout)
    except:
        continue

    if timeout < 1:
        continue

    if xbmc.getGlobalIdleTime() / 60 >= timeout:
        oe.__oe__.dbg_log('service', 'idle timeout reached', oe.__oe__.LOGDEBUG)
        oe.__oe__.standby_devices()

if hasattr(oe, 'winOeMain') and hasattr(oe.winOeMain, 'visible'):
    if oe.winOeMain.visible == True:
        oe.winOeMain.close()

try:
    # Cancel all pending event loop tasks so threads blocked on
    # future.result() (e.g. connectionThread doing device_connect via
    # run_method) get unblocked and can exit.
    def _cancel_all_tasks():
        for task in asyncio.all_tasks(dbus_utils.LOOP):
            task.cancel()
    dbus_utils.LOOP.call_soon_threadsafe(_cancel_all_tasks)
except Exception:
    pass
oe.stop_service()
monitor.stop()
try:
    # Force the event loop to stop, then join the thread.
    dbus_utils.LOOP.call_soon_threadsafe(dbus_utils.LOOP.stop)
    dbus_utils.LOOP_THREAD.stop()
except Exception:
    pass
# Detach the D-Bus connection from the asyncio event loop while the
# interpreter is still fully functional. This removes the ctypes watch
# and timeout callbacks that libdbus holds. Without this, Python's
# module cleanup frees the ctypes callback objects while libdbus still
# references them, causing a SIGSEGV.
# Also clear ravel's dispatch trees so its __del__ skips calling
# bus_remove_match (which would fail after detaching from the loop).
try:
    import dbussy
    conn = dbus_utils.BUS.connection
    if conn._dbobj is not None and conn.loop is not None:
        dbussy.dbus.dbus_connection_set_watch_functions(
            conn._dbobj, None, None, None, None, None)
        dbussy.dbus.dbus_connection_set_timeout_functions(
            conn._dbobj, None, None, None, None, None)
        conn.loop = None
    dbus_utils.BUS._server_dispatch = None
    dbus_utils.BUS._client_dispatch = None
except Exception:
    pass
