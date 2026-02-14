# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2009-2013 Stephan Raue (stephan@openelec.tv)
# Copyright (C) 2013 Lutz Fiebach (lufie@openelec.tv)
# Copyright (C) 2019-present Team LibreELEC (https://libreelec.tv)
# Copyright (C) 2020-present Team CoreELEC (https://coreelec.org)

import threading
import time
import weakref

import xbmc
import xbmcgui
from dbussy import DBusError

import dbus_bluez
import dbus_obex
import hostname
import log
import modules
import oe
import oeWindows

BT_DEVICES_LIST_REFRESH_INTERVAL_SECONDS = 5


class bluetooth(modules.Module):

    menu = {'6': {
        'name': 32331,
        'menuLoader': 'menu_connections',
        'listTyp': 'btlist',
        'InfoText': 704,
        }}
    ENABLED = False
    OBEX_ROOT = None
    OBEX_DAEMON = None
    BLUETOOTH_DAEMON = None
    D_OBEXD_ROOT = None

    # type 1=int, 2=string, 3=array, 4=bool
    properties = {
        0: {
            'type': 4,
            'value': 'Paired',
        },
        1: {
            'type': 2,
            'value': 'Adapter',
        },
        2: {
            'type': 4,
            'value': 'Connected',
        },
        3: {
            'type': 2,
            'value': 'Address',
        },
        5: {
            'type': 1,
            'value': 'Class',
        },
        6: {
            'type': 4,
            'value': 'Trusted',
        },
        7: {
            'type': 2,
            'value': 'Icon',
        },
    }

    @log.log_function()
    def __init__(self, oeMain):
        super().__init__()
        self.oe = oeMain
        self.visible = False
        self.listItems = {}
        self.dbusBluezAdapter = None
        self.discovering = False
        self.found_devices = frozenset()

    @log.log_function()
    def do_init(self):
        self.visible = True

    @log.log_function()
    def start_service(self):
        self._restore_audio_on_start()
        self._save_default_audio_device()
        self.bluez_agent = Bluez_Agent(self)
        self.obex_agent = Obex_Agent(self)
        self.bluez_listener = Bluez_Listener(self)
        self.obex_listener = Obex_Listener(self)
        self.find_adapter()

    def _save_default_audio_device(self):
        """Save the default audio device for restoration on BT disconnect.

        Strategy:
        1. Check for user-configured device setting (future feature)
        2. If we have a saved device from previous session, keep it
        3. If current device is not Bluetooth, save it
        4. If currently on Bluetooth, fall back to passthrough device (HDMI/ALSA)
        """
        # Check if user has configured a preferred device
        user_configured = oe.read_setting('bluetooth', 'restore_audio_device')
        if user_configured:
            # Save user-configured device as the default to restore to
            oe.write_setting('bluetooth', 'default_audio_device', user_configured)
            log.log(f'Set default_audio_device to user-configured: {user_configured}', log.INFO)
            return

        # If we have a saved device from previous session, keep it
        saved_device = oe.read_setting('bluetooth', 'default_audio_device')
        if saved_device:
            log.log(f'Keeping previously saved audio device: {saved_device}', log.INFO)
            return

        # Get current audio device
        result = oe.jsonrpc({
            'method': 'Settings.GetSettingValue',
            'params': {'setting': 'audiooutput.audiodevice'},
        })
        if result is not None:
            current_device = result.get('value', '')

            # If not on Bluetooth, save current device
            if current_device and 'PULSE' not in current_device:
                oe.write_setting('bluetooth', 'default_audio_device', current_device)
                log.log(f'Saved current audio device: {current_device}', log.INFO)
                return

        # Fallback: Currently on Bluetooth, use passthrough device (typically HDMI)
        pt_result = oe.jsonrpc({
            'method': 'Settings.GetSettingValue',
            'params': {'setting': 'audiooutput.passthroughdevice'},
        })
        if pt_result is not None:
            fallback_device = pt_result.get('value', '')
            if fallback_device:
                oe.write_setting('bluetooth', 'default_audio_device', fallback_device)
                log.log(f'Currently on BT at startup, using passthrough device as fallback: {fallback_device}', log.INFO)

    def _restore_audio_on_start(self):
        """Restore audio settings if a previous session ended with BT audio active."""
        saved_device = oe.read_setting('bluetooth', 'default_audio_device')
        if saved_device:
            log.log(f'Restoring audio device on start: {saved_device}', log.DEBUG)
            oe.jsonrpc({
                'method': 'Settings.SetSettingValue',
                'params': {
                    'setting': 'audiooutput.audiodevice',
                    'value': saved_device,
                },
            })
            oe.write_setting('bluetooth', 'default_audio_device', '')
        saved_passthrough = oe.read_setting('bluetooth', 'passthrough')
        if saved_passthrough:
            log.log(f'Restoring audio passthrough on start: {saved_passthrough}', log.DEBUG)
            try:
                # Convert string to boolean (passthrough is a boolean setting)
                passthrough_value = bool(int(saved_passthrough))
                oe.jsonrpc({
                    'method': 'Settings.SetSettingValue',
                    'params': {
                        'setting': 'audiooutput.passthrough',
                        'value': passthrough_value,
                    },
                })
            except (ValueError, TypeError) as e:
                log.log(f'Failed to restore passthrough (invalid value): {saved_passthrough}', log.ERROR)
            oe.write_setting('bluetooth', 'passthrough', '')
        saved_channels = oe.read_setting('bluetooth', 'channels')
        if saved_channels:
            log.log(f'Restoring audio channels on start: {saved_channels}', log.DEBUG)
            try:
                oe.jsonrpc({
                    'method': 'Settings.SetSettingValue',
                    'params': {
                        'setting': 'audiooutput.channels',
                        'value': int(saved_channels),
                    },
                })
            except (ValueError, TypeError) as e:
                log.log(f'Failed to restore channels (invalid value): {saved_channels}', log.ERROR)
            oe.write_setting('bluetooth', 'channels', '')

    @log.log_function()
    def stop_service(self):
        try:
            if hasattr(self, 'dbusBluezAdapter') and self.dbusBluezAdapter is not None:
                # Only unregister if bluez is actually running, otherwise D-Bus
                # will try to auto-activate it and hang for 25 seconds
                if dbus_bluez.system_has_bluez():
                    self.bluez_agent.unregister_agent()
        except Exception:
            pass
        if hasattr(self, 'connection_thread'):
            try:
                self.connection_thread.stop()
                self.connection_thread.join(timeout=2)
                del self.connection_thread
            except (AttributeError, Exception):
                pass
        if hasattr(self, 'discovery_thread'):
            try:
                self.discovery_thread.stop()
                self.discovery_thread.join(timeout=2)
                del self.discovery_thread
            except (AttributeError, Exception):
                pass
        if hasattr(self, 'dbusBluezAdapter'):
            self.dbusBluezAdapter = None

    @log.log_function()
    def exit(self):
        if hasattr(self, 'connection_thread'):
            try:
                self.connection_thread.stop()
                self.connection_thread.join(timeout=2)
                del self.connection_thread
            except (AttributeError, Exception):
                pass
        if hasattr(self, 'discovery_thread'):
            try:
                self.discovery_thread.stop()
                self.discovery_thread.join(timeout=2)
                del self.discovery_thread
            except (AttributeError, Exception):
                pass
        self.clear_list()
        self.visible = False

    # ###################################################################
    # # Bluetooth Adapter
    # ###################################################################

    @log.log_function()
    def find_adapter(self):
        self.dbusBluezAdapter = dbus_bluez.find_adapter()
        if self.dbusBluezAdapter:
            self.init_adapter()

    @log.log_function()
    def init_adapter(self):
        dbus_bluez.adapter_set_alias(self.dbusBluezAdapter, hostname.get_hostname())
        dbus_bluez.adapter_set_powered(self.dbusBluezAdapter, True)
        if oe.get_service_option('bluez', 'CONNECT_PAIRED', '1') == '1':
            if not hasattr(self, 'connection_thread') or not self.connection_thread.is_alive():
                self.connection_thread = connectionThread(self)
                self.connection_thread.start()

    @log.log_function()
    def start_discovery(self):
        oe.set_busy(1)
        # Check BlueZ's actual state, not our cached flag
        # BlueZ can stop discovery on its own (timeout, or during certain operations)
        if dbus_bluez.adapter_get_discovering(self.dbusBluezAdapter):
            self.discovering = True
            oe.set_busy(0)
            return

        self.discovering = True
        dbus_bluez.adapter_start_discovery(self.dbusBluezAdapter)
        oe.set_busy(0)

    @log.log_function()
    def stop_discovery(self):
        oe.set_busy(1)
        if self.discovering:
            dbus_bluez.adapter_stop_discovery(self.dbusBluezAdapter)
            self.discovering = False
        oe.set_busy(0)

    # ###################################################################
    # # Bluetooth Device
    # ###################################################################

    @log.log_function()
    def get_devices(self):
        return dbus_bluez.find_devices()

    @log.log_function()
    def init_device(self, listItem=None):
        if listItem is None:
            listItem = oe.winOeMain.getControl(oe.listObject['btlist']).getSelectedItem()
        if listItem is None:
            return
        if listItem.getProperty('Paired') != '1':
            self.pair_device(listItem.getProperty('entry'))
        else:
            self.connect_device(listItem.getProperty('entry'))

    @log.log_function()
    def trust_connect_device(self, listItem=None):
        # ########################################################
        # # This function is used to Pair PS3 Remote without auth
        # ########################################################
        if listItem is None:
            listItem = oe.winOeMain.getControl(oe.listObject['btlist']).getSelectedItem()
        if listItem is None:
            return
        self.trust_device(listItem.getProperty('entry'))
        self.connect_device(listItem.getProperty('entry'))

    @log.log_function()
    def enable_device_standby(self, listItem=None):
        devices = oe.read_setting('bluetooth', 'standby')
        if devices is not None:
            devices = devices.split(',')
        else:
            devices = []
        if not listItem.getProperty('entry') in devices:
            devices.append(listItem.getProperty('entry'))
        oe.write_setting('bluetooth', 'standby', ','.join(devices))

    @log.log_function()
    def disable_device_standby(self, listItem=None):
        devices = oe.read_setting('bluetooth', 'standby')
        if devices is not None:
            devices = devices.split(',')
        else:
            devices = []
        if listItem.getProperty('entry') in devices:
            devices.remove(listItem.getProperty('entry'))
        oe.write_setting('bluetooth', 'standby', ','.join(devices))

    @log.log_function()
    def pair_device(self, path):
        oe.set_busy(1)
        try:
            dbus_bluez.device_pair(path)
            self.trust_device(path)
            self.connect_device(path)
            self.menu_connections()
        except DBusError as e:
            self.dbus_error_handler(e)
            # Remove device to clean up partial pairing state
            # This helps with devices that need multiple pairing attempts
            try:
                dbus_bluez.adapter_remove_device(self.dbusBluezAdapter, path)
            except Exception:
                pass
        finally:
            oe.set_busy(0)

    @log.log_function()
    def trust_device(self, path):
        oe.set_busy(1)
        dbus_bluez.device_set_trusted(path, True)
        oe.set_busy(0)

    @log.log_function()
    def connect_device(self, path):
        oe.set_busy(1)
        try:
            dbus_bluez.device_connect(path)
            self.menu_connections()
        except DBusError as e:
            self.dbus_error_handler(e)
        finally:
            oe.set_busy(0)

    @log.log_function()
    def disconnect_device(self, listItem=None):
        if listItem is None:
            listItem = self.oe.winOeMain.getControl(self.oe.listObject['btlist']).getSelectedItem()
        if listItem is None:
            return
        self.disconnect_device_by_path(listItem.getProperty('entry'))

    @log.log_function()
    def disconnect_device_by_path(self, path):
        oe.set_busy(1)
        try:
            dbus_bluez.device_disconnect(path)
            self.menu_connections()
        except DBusError as e:
            self.dbus_error_handler(e)
        finally:
            oe.set_busy(0)

    @log.log_function()
    def remove_device(self, listItem=None):
        if listItem is None:
            listItem = oe.winOeMain.getControl(oe.listObject['btlist']).getSelectedItem()
        if listItem is None:
            return
        oe.set_busy(1)
        log.log(f"remove_device->entry: {listItem.getProperty('entry')}", log.DEBUG)
        path = listItem.getProperty('entry')
        dbus_bluez.adapter_remove_device(self.dbusBluezAdapter, path)
        self.disable_device_standby(listItem)
        self.menu_connections()
        oe.set_busy(0)

    # ###################################################################
    # # Bluetooth Error Handler
    # ###################################################################

    @log.log_function()
    def dbus_error_handler(self, error):
        log.log(f'error message: {repr(error.message)}', log.DEBUG)
        oe.set_busy(0)
        oe.notify('Bluetooth error', error.message.split('.')[0], 'bt')
        if hasattr(self, 'pinkey_window'):
            self.close_pinkey_window()

    # ###################################################################
    # # Bluetooth GUI
    # ###################################################################

    @log.log_function()
    def clear_list(self):
        for entry in list(self.listItems.keys()):
            del self.listItems[entry]
        self.listItems = {}

    @log.log_function()
    def menu_connections(self, focusItem=None):
        if oe.is_busy():
            return
        # Retry finding adapter if not available (fixes race condition on boot)
        # Only retry here to avoid interrupting user during periodic refreshes
        if self.dbusBluezAdapter is None:
            log.log('Adapter not found, retrying...', log.DEBUG)
            self.find_adapter()
        self.discover_devices()
        if self.dbusBluezAdapter is not None and (not hasattr(self, 'discovery_thread') or self.discovery_thread.stopped):
            if hasattr(self, 'discovery_thread') and self.discovery_thread.stopped:
                del self.discovery_thread
            self.start_discovery()
            self.discovery_thread = discoveryThread(self)
            self.discovery_thread.start()

    @log.log_function()
    def discover_devices(self):
        if not hasattr(oe, 'winOeMain'):
            return
        if not oe.winOeMain.visible:
            return
        control_list = oe.winOeMain.getControl(int(oe.listObject['btlist']))
        if not dbus_bluez.system_has_bluez():
            oe.winOeMain.getControl(1301).setLabel(oe._(32346))
            control_list.reset()
            self.clear_list()
            log.log('exit_function (BT Disabled)', log.DEBUG)
            oe.winOeMain.setProperty('show_bt_label', 'true')
            return
        if self.dbusBluezAdapter is None:
            oe.winOeMain.getControl(1301).setLabel(oe._(32338))
            control_list.reset()
            self.clear_list()
            log.log('exit_function (No Adapter)', log.DEBUG)
            oe.winOeMain.setProperty('show_bt_label', 'true')
            return
        if not dbus_bluez.adapter_get_powered(self.dbusBluezAdapter):
            oe.winOeMain.getControl(1301).setLabel(oe._(32338))
            control_list.reset()
            self.clear_list()
            oe.winOeMain.setProperty('show_bt_label', 'true')
            log.log('exit_function (No Adapter Powered)', log.DEBUG)
            return

        self.dbusDevices = self.get_devices()
        if self.dbusDevices:
            oe.winOeMain.clearProperty('show_bt_label')
            oe.winOeMain.getControl(1301).setLabel('')
            found_devices = frozenset(self.dbusDevices.keys())
            existing_devices = frozenset(self.listItems.keys())
            new_devices = found_devices - existing_devices
            deactivated_devices = existing_devices - found_devices
        else:
            control_list.reset()
            self.clear_list()
            oe.winOeMain.getControl(1301).setLabel(oe._(32339))
            oe.winOeMain.setProperty('show_bt_label', 'true')
            return

        selected_dbus_device = None
        selected_item = control_list.getSelectedItem()
        if selected_item:
            selected_dbus_device = selected_item.getProperty('entry')
        for dbusDevice, device_properties in self.dbusDevices.items():
            dictProperties = {}
            apName = ''
            dictProperties['entry'] = dbusDevice
            dictProperties['modul'] = self.__class__.__name__
            dictProperties['action'] = 'open_context_menu'
            if 'Name' in device_properties:
                apName = device_properties['Name']
            if not 'Icon' in device_properties:
                dictProperties['Icon'] = 'default'
            for prop in self.properties:
                name = self.properties[prop]['value']
                if name in device_properties:
                    value = device_properties[name]
                    if name == 'Connected':
                        if value:
                            dictProperties['ConnectedState'] = oe._(32334)
                        else:
                            dictProperties['ConnectedState'] = oe._(32335)
                    if self.properties[prop]['type'] == 1:
                        value = str(int(value))
                    if self.properties[prop]['type'] == 2:
                        value = str(value)
                    if self.properties[prop]['type'] == 3:
                        value = str(len(value))
                    if self.properties[prop]['type'] == 4:
                        value = str(int(value))
                    dictProperties[name] = value
            if dbusDevice in new_devices:
                self.listItems[dbusDevice] = oe.winOeMain.addConfigItem(apName, dictProperties, oe.listObject['btlist'])
            else:
                if dbusDevice in self.listItems:
                    self.listItems[dbusDevice].setLabel(apName)
                    for dictProperty in dictProperties:
                        try:
                            self.listItems[dbusDevice].setProperty(dictProperty, dictProperties[dictProperty])
                        except KeyError as e:
                            log.log(f'Suppressed error: {repr(e)}', log.INFO)
            for dbusDevice in deactivated_devices:
                for i in range(control_list.size()):
                    list_item = control_list.getListItem(i)
                    if list_item.getProperty('entry') == dbusDevice and dbusDevice in self.listItems:
                        control_list.removeItem(i)
                        try:
                            del self.listItems[dbusDevice]
                        except KeyError as e:
                            log.log(f'Suppressed error: {repr(e)}', log.INFO)
                        break
            if (new_devices or deactivated_devices) and selected_dbus_device is not None:
                for i in range(control_list.size()):
                    list_item = control_list.getListItem(i)
                    if list_item.getProperty('entry') == selected_dbus_device:
                        control_list.selectItem(i)
                        break

    @log.log_function()
    def open_context_menu(self, listItem):
        values = {}
        if listItem is None:
            listItem = oe.winOeMain.getControl(oe.listObject['btlist']).getSelectedItem()
        if listItem.getProperty('Paired') != '1':
            values[1] = {
                'text': oe._(32145),
                'action': 'init_device',
                }
            if listItem.getProperty('Trusted') != '1':
                values[2] = {
                    'text': oe._(32358),
                    'action': 'trust_connect_device',
                    }
        if listItem.getProperty('Connected') == '1':
            values[3] = {
                'text': oe._(32143),
                'action': 'disconnect_device',
                }
            devices = oe.read_setting('bluetooth', 'standby')
            if devices is not None:
                devices = devices.split(',')
            else:
                devices = []
            if listItem.getProperty('entry') in devices:
                values[4] = {
                    'text': oe._(32389),
                    'action': 'disable_device_standby',
                    }
            else:
                values[4] = {
                    'text': oe._(32388),
                    'action': 'enable_device_standby',
                    }
        elif listItem.getProperty('Paired') == '1':
            values[1] = {
                'text': oe._(32144),
                'action': 'init_device',
                }
        elif listItem.getProperty('Trusted') == '1':
            values[2] = {
                'text': oe._(32144),
                'action': 'trust_connect_device',
                }
        values[5] = {
            'text': oe._(32141),
            'action': 'remove_device',
            }
        values[6] = {
            'text': oe._(32142),
            'action': 'menu_connections',
            }
        items = []
        actions = []
        for key in list(values.keys()):
            items.append(values[key]['text'])
            actions.append(values[key]['action'])
        select_window = xbmcgui.Dialog()
        title = oe._(32012)
        result = select_window.select(title, items)
        if result >= 0:
            getattr(self, actions[result])(listItem)

    @log.log_function()
    def open_pinkey_window(self, runtime=60, title=32343):
        self.pinkey_window = oeWindows.pinkeyWindow('service-CoreELEC-Settings-getPasskey.xml', oe.__cwd__, 'Default')
        self.pinkey_window.show()
        self.pinkey_window.set_title(oe._(title))
        self.pinkey_timer = pinkeyTimer(self, runtime)
        self.pinkey_timer.start()

    @log.log_function()
    def close_pinkey_window(self):
        if hasattr(self, 'pinkey_timer'):
            self.pinkey_timer.stop()
            self.pinkey_timer.join()
            self.pinkey_timer = None
            del self.pinkey_timer
        if hasattr(self, 'pinkey_window'):
            self.pinkey_window.close()
            self.pinkey_window = None
            del self.pinkey_window

    def standby_devices(self):
        if self.dbusBluezAdapter:
            devices = oe.read_setting('bluetooth', 'standby')
            if devices:
                oe.input_request = True
                for device in devices.split(','):
                    if dbus_bluez.device_get_connected(device):
                        self.disconnect_device_by_path(device)
                oe.input_request = False


####################################################################
## Bluez Listener class
####################################################################
class Bluez_Listener(dbus_bluez.Listener):

    @log.log_function()
    def __init__(self, parent):
        self.parent = weakref.proxy(parent)
        self._last_connect_event = {}  # path -> timestamp for debounce
        self._last_disconnect_event = {}  # path -> timestamp for debounce
        self._audio_devices = set()  # paths of connected audio devices
        self._seed_audio_devices()
        super().__init__()

    def _seed_audio_devices(self):
        """Populate cache with already-connected audio devices."""
        try:
            devices = dbus_bluez.find_devices()
            for path, props in devices.items():
                if props.get('Connected'):
                    device_class = props.get('Class', 0)
                    if device_class and (device_class & (1 << 21)):
                        self._audio_devices.add(path)
                        log.log(f'Seeded audio device cache: {path}', log.DEBUG)
        except Exception as e:
            log.log(f'Failed to seed audio device cache: {e}', log.DEBUG)

    def _get_device_class(self, path):
        try:
            return dbus_bluez.device_get_property(path, 'Class')
        except Exception:
            return 0

    def _is_audio_device(self, path):
        device_class = self._get_device_class(path)
        # Bit 21 = audio rendering capability (speaker/headphones)
        return bool(device_class and (device_class & (1 << 21)))

    def _is_notify_device(self, path):
        """Check if device warrants connect/disconnect notifications.
        Skips peripherals like remotes and game controllers that reconnect
        frequently. Notifies for audio, phone, computer, imaging, etc."""
        device_class = self._get_device_class(path)
        if not device_class:
            return True
        # Major device class is bits 12-8
        major_class = (device_class >> 8) & 0x1F
        # 5 = Peripheral (remotes, game controllers, joysticks)
        return major_class != 5

    def _handle_audio_connect(self, path):
        if oe.get_service_option('bluez', 'SWITCH_AUDIO_DEVICE', '1') != '1':
            log.log('Audio switching disabled in settings', log.INFO)
            return
        device_class = self._get_device_class(path)
        is_audio = self._is_audio_device(path)
        log.log(f'BT device connected: {path}, class={device_class}, is_audio={is_audio}', log.INFO)
        if not is_audio:
            return
        self._audio_devices.add(path)
        log.log(f'Bluetooth audio device connected, switching audio settings', log.INFO)
        # Get current audio device
        result = oe.jsonrpc({
            'method': 'Settings.GetSettingValue',
            'params': {'setting': 'audiooutput.audiodevice'},
        })
        if result is not None:
            current_device = result.get('value', '')
        else:
            current_device = ''
        # Save the device to restore on disconnect
        user_configured = oe.read_setting('bluetooth', 'restore_audio_device')
        if user_configured:
            # User has configured a preferred restore device - always use it
            oe.write_setting('bluetooth', 'default_audio_device', user_configured)
            log.log(f'Set default_audio_device from user config: {user_configured}', log.INFO)
        elif 'PULSE' not in current_device:
            # No user preference - save current non-BT device
            oe.write_setting('bluetooth', 'default_audio_device', current_device)
            log.log(f'Saved current audio device: {current_device}', log.INFO)
        # Get and save current passthrough state
        pt_result = oe.jsonrpc({
            'method': 'Settings.GetSettingValue',
            'params': {'setting': 'audiooutput.passthrough'},
        })
        if pt_result is not None:
            passthrough = pt_result.get('value', 0)
            if passthrough:
                oe.write_setting('bluetooth', 'passthrough', str(int(passthrough)))
        # Get and save current channels setting
        ch_result = oe.jsonrpc({
            'method': 'Settings.GetSettingValue',
            'params': {'setting': 'audiooutput.channels'},
        })
        if ch_result is not None:
            channels = ch_result.get('value', -1)
            # Only save if not already 2.0 (value 1)
            if channels != 1:
                oe.write_setting('bluetooth', 'channels', str(int(channels)))
        # Switch audio to PulseAudio Bluetooth
        oe.jsonrpc({
            'method': 'Settings.SetSettingValue',
            'params': {
                'setting': 'audiooutput.audiodevice',
                'value': 'PULSE:Default|Bluetooth Audio (PULSEAUDIO)',
            },
        })
        # Set channels to 2.0 (value 1 in Kodi)
        oe.jsonrpc({
            'method': 'Settings.SetSettingValue',
            'params': {
                'setting': 'audiooutput.channels',
                'value': 1,
            },
        })
        # Disable passthrough if it was on (with 1s delay as in old code)
        if pt_result is not None and pt_result.get('value', 0):
            time.sleep(1)
            oe.jsonrpc({
                'method': 'Settings.SetSettingValue',
                'params': {
                    'setting': 'audiooutput.passthrough',
                    'value': False,
                },
            })
        log.log('Switched audio output to Bluetooth with 2.0 channels and passthrough disabled', log.INFO)

    def _handle_audio_disconnect(self, path):
        if path not in self._audio_devices:
            return
        self._audio_devices.discard(path)
        log.log(f'Bluetooth audio device disconnected, restoring settings: {path}', log.INFO)
        saved_device = oe.read_setting('bluetooth', 'default_audio_device')
        log.log(f'Saved device to restore: {saved_device}', log.INFO)
        if saved_device:
            # Check if currently on PULSE before restoring
            result = oe.jsonrpc({
                'method': 'Settings.GetSettingValue',
                'params': {'setting': 'audiooutput.audiodevice'},
            })
            if result is not None:
                current_device = result.get('value', '')
            else:
                current_device = ''
            log.log(f'Current audio device: {current_device}', log.INFO)
            if 'PULSE' in current_device:
                log.log(f'Restoring audio device to: {saved_device}', log.INFO)
                oe.jsonrpc({
                    'method': 'Settings.SetSettingValue',
                    'params': {
                        'setting': 'audiooutput.audiodevice',
                        'value': saved_device,
                    },
                })
                log.log(f'Audio device restored successfully', log.INFO)
            else:
                log.log(f'Not on PULSE, skipping audio device restore', log.INFO)
            oe.write_setting('bluetooth', 'default_audio_device', '')
        else:
            log.log('No saved device to restore, will still attempt channels/passthrough', log.INFO)
        # Restore channels if it was saved
        saved_channels = oe.read_setting('bluetooth', 'channels')
        if saved_channels:
            try:
                oe.jsonrpc({
                    'method': 'Settings.SetSettingValue',
                    'params': {
                        'setting': 'audiooutput.channels',
                        'value': int(saved_channels),
                    },
                })
                oe.write_setting('bluetooth', 'channels', '')
                log.log(f'Restored audio channels to: {saved_channels}', log.DEBUG)
            except (ValueError, TypeError) as e:
                log.log(f'Failed to restore channels: {e}', log.ERROR)
                oe.write_setting('bluetooth', 'channels', '')
        # Restore passthrough if it was saved
        saved_passthrough = oe.read_setting('bluetooth', 'passthrough')
        if saved_passthrough:
            try:
                # Convert string to boolean (passthrough is a boolean setting)
                passthrough_value = bool(int(saved_passthrough))
                log.log(f'Restoring passthrough to: {passthrough_value}', log.INFO)
                oe.jsonrpc({
                    'method': 'Settings.SetSettingValue',
                    'params': {
                        'setting': 'audiooutput.passthrough',
                        'value': passthrough_value,
                    },
                })
                oe.write_setting('bluetooth', 'passthrough', '')
                log.log('Passthrough restored successfully', log.INFO)
            except (ValueError, TypeError) as e:
                log.log(f'Failed to restore passthrough: {e}', log.ERROR)
                oe.write_setting('bluetooth', 'passthrough', '')

    @log.log_function()
    def on_interfaces_added(self, path, interfaces):
        if dbus_bluez.INTERFACE_ADAPTER in interfaces:
            self.parent.dbusBluezAdapter = path
            self.parent.init_adapter()
        if hasattr(self.parent, 'pinkey_window'):
            if path == self.parent.pinkey_window.device:
                self.parent.close_pinkey_window()
        if self.parent.visible:
            self.parent.discover_devices()

    @log.log_function()
    def on_interfaces_removed(self, path, interfaces):
        if dbus_bluez.INTERFACE_ADAPTER in interfaces:
            self.parent.dbusBluezAdapter = None
        if self.parent.visible and not hasattr(self.parent, 'discovery_thread'):
            self.parent.discover_devices()

    @log.log_function()
    def on_properties_changed(self, interface, changed, invalidated, path):
        # Handle audio device switching and notifications on connect/disconnect
        if 'Connected' in changed:
            now = time.monotonic()
            if changed['Connected']:
                # Debounce: ignore duplicate connect signals within 1 second
                last = self._last_connect_event.get(path, 0)
                if now - last < 1.0:
                    log.log(f'Debounce: ignoring duplicate connect for {path}', log.DEBUG)
                else:
                    self._last_connect_event[path] = now
                    if (oe.get_service_option('bluez', 'NOTIFY_CONNECTED', '1') == '1'
                            and self._is_notify_device(path)):
                        try:
                            name = dbus_bluez.device_get_name(path)
                        except Exception:
                            name = path
                        oe.notify('Bluetooth', f'Connected to {name}', 'bt')
                    self._handle_audio_connect(path)
            else:
                # Debounce: ignore duplicate disconnect signals within 1 second
                last = self._last_disconnect_event.get(path, 0)
                if now - last < 1.0:
                    log.log(f'Debounce: ignoring duplicate disconnect for {path}', log.DEBUG)
                else:
                    self._last_disconnect_event[path] = now
                    if (oe.get_service_option('bluez', 'NOTIFY_CONNECTED', '1') == '1'
                            and self._is_notify_device(path)):
                        try:
                            name = dbus_bluez.device_get_name(path)
                        except Exception:
                            name = path
                        oe.notify('Bluetooth', f'Disconnected from {name}', 'bt')
                    self._handle_audio_disconnect(path)

        if self.parent.visible:
            properties = [
                'Paired',
                'Adapter',
                'Connected',
                'Address',
                'Class',
                'Trusted',
                'Icon',
                ]
            if path in self.parent.listItems:
                for prop in changed:
                    if prop in properties:
                        self.parent.listItems[path].setProperty(str(prop), str(changed[prop]))
            else:
                self.parent.discover_devices()


####################################################################
## Obex Listener class
####################################################################

class Obex_Listener(dbus_obex.Listener):

    @log.log_function()
    def __init__(self, parent):
        self.parent = weakref.proxy(parent)
        self.transfer_progress = None
        self.transfer_start = None
        super().__init__()

    @log.log_function()
    def on_transfer_changed(self, interface, changed, invalidated, path):
        if 'Status' in changed:
            status = changed['Status']
            if status == 'active':
                self.transfer_start = time.monotonic()
                self.transfer_progress = xbmcgui.DialogProgress()
                self.transfer_progress.create('Bluetooth', oe._(32383))
            elif status == 'complete':
                if self.transfer_progress is not None:
                    self.transfer_progress.close()
                    self.transfer_progress = None
                xbmcDialog = xbmcgui.Dialog()
                answer = xbmcDialog.yesno('Bluetooth', oe._(32382))
                if answer == 1:
                    if hasattr(self.parent, 'download_file') and self.parent.download_file:
                        download_dir = self.parent.D_OBEXD_ROOT or '/storage/downloads/'
                        xbmc.executebuiltin(f'PlayMedia({download_dir}{self.parent.download_file})')
            elif status == 'error':
                if self.transfer_progress is not None:
                    self.transfer_progress.close()
                    self.transfer_progress = None
        if 'Transferred' in changed and self.transfer_progress is not None:
            if self.transfer_progress.iscanceled():
                try:
                    dbus_obex.transfer_cancel(path)
                except Exception:
                    pass
                self.transfer_progress.close()
                self.transfer_progress = None
                return
            transferred = changed['Transferred']
            if hasattr(self.parent, 'download_size') and self.parent.download_size > 0:
                percent = int(transferred / 1024 / self.parent.download_size * 100)
            else:
                percent = 0
            elapsed = time.monotonic() - self.transfer_start if self.transfer_start else 1
            speed = transferred / 1024 / max(elapsed, 1)
            self.transfer_progress.update(
                percent,
                f'{transferred / 1024:.0f} KB / {speed:.1f} KB/s')


####################################################################
## Bluetooth Agent class
####################################################################

class Bluez_Agent(dbus_bluez.Agent):

    @log.log_function()
    def __init__(self, parent):
        self.parent = weakref.proxy(parent)
        super().__init__()

    @log.log_function()
    def authorize_service(self, device, uuid):
        oe.input_request = True
        xbmcDialog = xbmcgui.Dialog()
        answer = xbmcDialog.yesno('Bluetooth', f'Authorize service {uuid}?')
        oe.input_request = False
        if answer == 1:
            oe.dictModules['bluetooth'].trust_device(device)
        else:
            self.reject('Connection rejected!')

    @log.log_function()
    def request_pincode(self, device):
        oe.input_request = True
        xbmcKeyboard = xbmc.Keyboard('', 'Enter PIN code')
        xbmcKeyboard.doModal()
        pincode = xbmcKeyboard.getText()
        oe.input_request = False
        return pincode

    @log.log_function()
    def request_passkey(self, device):
        oe.input_request = True
        xbmcDialog = xbmcgui.Dialog()
        passkey = int(xbmcDialog.numeric(0, 'Enter passkey (number in 0-999999)', '0'))
        oe.input_request = False
        return passkey

    @log.log_function()
    def display_passkey(self, device, passkey, entered):
        if not hasattr(self.parent, 'pinkey_window'):
            self.parent.open_pinkey_window()
            self.parent.pinkey_window.device = device
            self.parent.pinkey_window.set_label1('Passkey: %06u' % passkey)

    @log.log_function()
    def display_pincode(self, device, pincode):
        if hasattr(self.parent, 'pinkey_window'):
            self.parent.close_pinkey_window()
        self.parent.open_pinkey_window(runtime=30)
        self.parent.pinkey_window.device = device
        self.parent.pinkey_window.set_label1(f'PIN code: {pincode}')

    @log.log_function()
    def request_confirmation(self, device, passkey):
        oe.input_request = True
        xbmcDialog = xbmcgui.Dialog()
        answer = xbmcDialog.yesno('Bluetooth', f'Confirm passkey {passkey}')
        oe.input_request = False
        if answer == 1:
            oe.dictModules['bluetooth'].trust_device(device)
        else:
            self.reject('Passkey does not match')

    @log.log_function()
    def request_authorization(self, device):
        oe.input_request = True
        xbmcDialog = xbmcgui.Dialog()
        answer = xbmcDialog.yesno('Bluetooth', 'Accept pairing?')
        oe.input_request = False
        if answer == 1:
            oe.dictModules['bluetooth'].trust_device(device)
        else:
            self.reject('Pairing rejected')

    @log.log_function()
    def cancel(self):
        if hasattr(self.parent, 'pinkey_window'):
            self.parent.close_pinkey_window()


####################################################################
## Obex Agent class
####################################################################

class Obex_Agent(dbus_obex.Agent):

    @log.log_function()
    def __init__(self, parent):
        self.parent = weakref.proxy(parent)
        super().__init__()

    def authorize_push(self, transfer):
        oe.input_request = True
        xbmcDialog = xbmcgui.Dialog()
        properties = dbus_obex.transfer_get_all_properties(transfer)
        answer = xbmcDialog.yesno('Bluetooth', f"{oe._(32381)}\n\n{properties['Name']}")
        oe.input_request = False
        log.log(f'answer={repr(answer)}', log.DEBUG)
        if answer != 1:
            self.reject('Not Authorized')
        self.parent.download_path = transfer
        self.parent.download_file = properties['Name']
        self.parent.download_size = properties['Size'] / 1024
        if 'Type' in properties:
            self.parent.download_type = properties['Type']
        else:
            self.parent.download_type = None
        return properties['Name']


class connectionThread(threading.Thread):

    def __init__(self, parent):
        super().__init__()
        self.parent = weakref.proxy(parent)
        self._stop_event = threading.Event()
        self.daemon = True

    def stop(self):
        self._stop_event.set()

    @log.log_function()
    def run(self):
        devices = self.parent.get_devices()
        for path, props in devices.items():
            if self._stop_event.is_set() or oe.xbmcm.abortRequested():
                break
            if props.get('Paired') and not props.get('Connected'):
                try:
                    log.log(f'Auto-connecting paired device: {path}', log.DEBUG)
                    dbus_bluez.device_connect(path)
                except Exception as e:
                    log.log(f'Failed to auto-connect {path}: {e}', log.DEBUG)
                oe.xbmcm.waitForAbort(1)


class discoveryThread(threading.Thread):

    def __init__(self, parent):
        super().__init__()
        self.parent = weakref.proxy(parent)
        self.last_run = 0
        self._stop_event = threading.Event()
        self.stopped = False
        self.main_menu = oe.winOeMain.getControl(oe.winOeMain.guiMenList)

    @property
    def stopped(self):
        return self._stop_event.is_set()

    @stopped.setter
    def stopped(self, value):
        if value:
            self._stop_event.set()
        else:
            self._stop_event.clear()

    @log.log_function()
    def stop(self):
        self.stopped = True
        self.parent.stop_discovery()

    @log.log_function()
    def run(self):
        self._stop_event.clear()
        while not self.stopped and not oe.xbmcm.abortRequested():
            current_time = time.monotonic()
            if (self.main_menu.getSelectedItem().getProperty('modul') == 'bluetooth'
                    and current_time > self.last_run + BT_DEVICES_LIST_REFRESH_INTERVAL_SECONDS):
                self.parent.discover_devices()
                self.last_run = current_time
            elif self.main_menu.getSelectedItem().getProperty('modul') != 'bluetooth':
                self.stop()
            oe.xbmcm.waitForAbort(1)


class pinkeyTimer(threading.Thread):

    def __init__(self, parent, runtime=60):
        self.parent = weakref.proxy(parent)
        self.start_time = time.monotonic()
        self.last_run = time.monotonic()
        self._stop_event = threading.Event()
        self.stopped = False
        self.runtime = runtime
        super().__init__()

    @property
    def stopped(self):
        return self._stop_event.is_set()

    @stopped.setter
    def stopped(self, value):
        if value:
            self._stop_event.set()
        else:
            self._stop_event.clear()

    @log.log_function()
    def stop(self):
        self.stopped = True

    @log.log_function()
    def run(self):
        self._stop_event.clear()
        self.endtime = self.start_time + self.runtime
        while not self.stopped and not oe.xbmcm.abortRequested():
            current_time = time.monotonic()
            percent = round(100 / self.runtime * (self.endtime - current_time), 0)
            self.parent.pinkey_window.getControl(1704).setPercent(percent)
            if current_time >= self.endtime:
                self.stopped = True
                self.parent.close_pinkey_window()
            else:
                oe.xbmcm.waitForAbort(1)
