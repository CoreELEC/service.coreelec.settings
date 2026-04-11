# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2020-present Team LibreELEC (https://libreelec.tv)
# Copyright (C) 2020-present Team CoreELEC (https://coreelec.org)

import dbussy
import ravel

import dbus_utils

from pathlib import Path
import log


BUS_NAME = 'org.bluez'
ERROR_REJECTED = 'org.bluez.Error.Rejected'
INTERFACE_ADAPTER = 'org.bluez.Adapter1'
INTERFACE_AGENT = 'org.bluez.Agent1'
INTERFACE_AGENT_MANAGER = 'org.bluez.AgentManager1'
INTERFACE_DEVICE = 'org.bluez.Device1'
PATH_BLUEZ = '/org/bluez'
PATH_AGENT = '/CoreELEC/agent/bluez'


@ravel.interface(ravel.INTERFACE.SERVER, name=INTERFACE_AGENT)
class Agent(dbus_utils.Agent):

    def __init__(self):
        super().__init__(BUS_NAME, PATH_AGENT)

    def manager_register_agent(self):
        dbus_utils.call_method(BUS_NAME, PATH_BLUEZ, INTERFACE_AGENT_MANAGER,
                               'RegisterAgent', PATH_AGENT, 'KeyboardDisplay')
        dbus_utils.call_method(BUS_NAME, PATH_BLUEZ, INTERFACE_AGENT_MANAGER,
                               'RequestDefaultAgent', PATH_AGENT)

    def manager_unregister_agent(self):
        dbus_utils.call_method(BUS_NAME, PATH_BLUEZ, INTERFACE_AGENT_MANAGER,
                               'UnregisterAgent', PATH_AGENT)

    @ravel.method(
        in_signature='os',
        out_signature='',
        arg_keys=['device', 'uuid']
    )
    def AuthorizeService(self, device, uuid):
        self.authorize_service(device, uuid)

    @ravel.method(
        in_signature='',
        out_signature=''
    )
    def Cancel(self):
        self.cancel()

    @ravel.method(
        in_signature='ouq',
        out_signature='',
        arg_keys=['device', 'passkey', 'entered']
    )
    def DisplayPasskey(self, device, passkey, entered):
        self.display_passkey(device, passkey, entered)

    @ravel.method(
        in_signature='os',
        out_signature='',
        arg_keys=['device', 'pincode']
    )
    def DisplayPinCode(self, device, pincode):
        self.display_pincode(device, pincode)

    @ravel.method(
        in_signature='',
        out_signature=''
    )
    def Release(self):
        raise NotImplementedError

    @ravel.method(
        in_signature='o',
        out_signature='',
        arg_keys=['device']
    )
    def RequestAuthorization(self, device):
        self.request_authorization(device)

    @ravel.method(
        in_signature='ou',
        out_signature='',
        arg_keys=['device', 'passkey']
    )
    def RequestConfirmation(self, device, passkey):
        self.request_confirmation(device, passkey)

    @ravel.method(
        in_signature='o',
        out_signature='u',
        arg_keys=['device'],
        result_keyword='reply'
    )
    def RequestPasskey(self, device):
        passkey = self.request_passkey(device)
        reply[0] = (dbussy.DBUS.Signature('u'), passkey)

    @ravel.method(
        in_signature='o',
        out_signature='s',
        arg_keys=['device'],
        result_keyword='reply'
    )
    def RequestPinCode(self, device, reply):
        pincode = self.request_pincode(device)
        reply[0] = (dbussy.DBUS.Signature('s'), pincode)

    def reject(self, message):
        raise dbussy.DBusError(ERROR_REJECTED, message)

class Listener(object):

    def __init__(self):
        dbus_utils.BUS.listen_objects_added(func=self._on_interfaces_added)
        dbus_utils.BUS.listen_objects_removed(func=self._on_interfaces_removed)
        dbus_utils.BUS.listen_propchanged(
            interface=dbussy.DBUS.INTERFACE_PROPERTIES,
            fallback=True,
            func=self._on_properties_changed,
            path='/')

    @ravel.signal(name='InterfacesAdded', in_signature='oa{sa{sv}}', arg_keys=('path', 'interfaces'))
    def _on_interfaces_added(self, path, interfaces):
        interfaces = dbus_utils.convert_from_dbussy(interfaces)
        self.on_interfaces_added(path, interfaces)

    @ravel.signal(name='InterfacesRemoved', in_signature='oas', arg_keys=('path', 'interfaces'))
    def _on_interfaces_removed(self, path, interfaces):
        interfaces = dbus_utils.convert_from_dbussy(interfaces)
        self.on_interfaces_removed(path, interfaces)

    @ravel.signal(name='PropertiesChanged', in_signature='sa{sv}as', arg_keys=('interface', 'changed', 'invalidated'), path_keyword='path')
    def _on_properties_changed(self, interface, changed, invalidated, path):
        interface = dbus_utils.convert_from_dbussy(interface)
        changed = dbus_utils.convert_from_dbussy(changed)
        invalidated = dbus_utils.convert_from_dbussy(invalidated)
        self.on_properties_changed(interface, changed, invalidated, path)

def get_managed_objects():
    return dbus_utils.call_method(BUS_NAME, '/', dbussy.DBUSX.INTERFACE_OBJECT_MANAGER, 'GetManagedObjects')


def adapter_get_property(path, name):
    return dbus_utils.call_method(BUS_NAME, path, dbussy.DBUS.INTERFACE_PROPERTIES, 'Get', INTERFACE_ADAPTER, name)


def adapter_get_powered(path):
    return adapter_get_property(path, 'Powered')


def adapter_get_discovering(path):
    return adapter_get_property(path, 'Discovering')


def adapter_remove_device(path, device):
    return dbus_utils.call_method(BUS_NAME, path, INTERFACE_ADAPTER, 'RemoveDevice', device)


def adapter_set_property(path, name, value):
    return dbus_utils.call_method(BUS_NAME, path, dbussy.DBUS.INTERFACE_PROPERTIES, 'Set', INTERFACE_ADAPTER, name, value)


def adapter_set_alias(path, alias):
    return adapter_set_property(path, 'Alias', (dbussy.DBUS.Signature('s'), alias))


def adapter_set_powered(path, powered):
    return adapter_set_property(path, 'Powered', (dbussy.DBUS.Signature('b'), powered))


def adapter_start_discovery(path):
    return dbus_utils.call_method(BUS_NAME, path, INTERFACE_ADAPTER, 'StartDiscovery')


def adapter_stop_discovery(path):
    return dbus_utils.call_method(BUS_NAME, path, INTERFACE_ADAPTER, 'StopDiscovery')


def device_get_property(path, name):
    return dbus_utils.call_method(BUS_NAME, path, dbussy.DBUS.INTERFACE_PROPERTIES, 'Get', INTERFACE_DEVICE, name)


def device_get_connected(path):
    return device_get_property(path, 'Connected')


def device_get_name(path):
    return device_get_property(path, 'Name')


def device_connect(path):
    return dbus_utils.run_method(BUS_NAME, path, INTERFACE_DEVICE, 'Connect')


def device_disconnect(path):
    return dbus_utils.call_method(BUS_NAME, path, INTERFACE_DEVICE, 'Disconnect')


def device_pair(path):
    return dbus_utils.run_method(BUS_NAME, path, INTERFACE_DEVICE, 'Pair')


def device_set_property(path, name, value):
    return dbus_utils.call_method(BUS_NAME, path, dbussy.DBUS.INTERFACE_PROPERTIES, 'Set', INTERFACE_DEVICE, name, value)


def device_set_trusted(path, trusted):
    return device_set_property(path, 'Trusted', (dbussy.DBUS.Signature('b'), trusted))


def get_adapter_transport(hci_name):
    """
    Detect if the Bluetooth adapter (hciX) is connected via USB or UART.
    Returns: 'USB', 'UART', 'Other', or 'Unknown'
    """
    sys_path = Path(f"/sys/class/bluetooth/{hci_name}")
    if not sys_path.exists():
        return "Unknown (not found)"

    # Follow the symlink to the real device
    try:
        device_path = (sys_path / "device").resolve()
    except Exception:
        return "Unknown (no device link)"

    # Walk up the parent directories and look for "usb" or "serial" in the path
    for parent in device_path.parents:
        parent_str = str(parent).lower() + '/'

        if "/usb" in parent_str or parent.name.startswith("usb"):
            return "USB"

        if any(x in parent_str for x in ["/serial/", "/tty/", "ttyama", "ttyS", "uart", "hci_uart"]):
            return "UART"

    # Fallback: check the subsystem symlink
    subsystem_link = device_path / "subsystem"
    if subsystem_link.is_symlink():
        subsystem = subsystem_link.resolve().name
        if subsystem == "usb":
            return "USB"
        if subsystem in ("serial", "amba", "platform"):
            return "UART"

    return "Other"


def find_adapter(selected):
    # USB 00:15:83:4F:32:56
    if system_has_bluez():
        if selected == '':
            objects = get_managed_objects()
            for path, interfaces in objects.items():
                if interfaces.get(INTERFACE_ADAPTER):
                    adapter_props = interfaces[INTERFACE_ADAPTER]
                    address = adapter_props.get('Address', False)
                    hci = path.strip().split('/')[-1]
                    transport = get_adapter_transport(hci)
                    log.log(f'using first available adapter: {transport} {address} {hci}', log.INFO)
                    return path
        else:
            selected_parts = selected.split(' ')
            if len(selected_parts) > 1:
                selected_address = selected_parts[1]
            else:
                return

            objects = get_managed_objects()
            for path, interfaces in objects.items():
                if interfaces.get(INTERFACE_ADAPTER):
                    adapter_props = interfaces[INTERFACE_ADAPTER]
                    address = adapter_props.get('Address', False)

                    if address == selected_address:
                        log.log(f'using aapter {selected}', log.INFO)
                        return path

            log.log(f'not found adapter {selected}', log.INFO)


def find_all_adapters():
    # UART b6:10:62:74:41:d0
    # USB 00:15:83:4F:32:56
    adapters = ['']

    if system_has_bluez():
        objects = get_managed_objects()
        for path, interfaces in objects.items():
            if interfaces.get(INTERFACE_ADAPTER):
                adapter_props = interfaces[INTERFACE_ADAPTER]
                address = adapter_props.get('Address', False)
                hci = path.strip().split('/')[-1]
                transport = get_adapter_transport(hci)

                adapters.append(f'{transport} {address}')
                log.log(f'found adapter {transport} {address} {hci}', log.INFO)

    return adapters


def find_devices():
    devices = {}
    objects = get_managed_objects()
    for path, interfaces in objects.items():
        if interfaces.get(INTERFACE_DEVICE):
            devices[path] = interfaces[INTERFACE_DEVICE]
    return devices


def system_has_bluez():
    return BUS_NAME in dbus_utils.list_names()
