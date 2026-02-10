# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2020-present Team LibreELEC (https://libreelec.tv)
# Copyright (C) 2020-present Team CoreELEC (https://coreelec.org)

import dbussy
import ravel

import dbus_utils


BUS_NAME = 'org.bluez.obex'
ERROR_REJECTED = 'org.bluez.Error.Rejected'
INTERFACE_AGENT = 'org.bluez.obex.Agent1'
INTERFACE_AGENT_MANAGER = 'org.bluez.obex.AgentManager1'
INTERFACE_TRANSFER = 'org.bluez.obex.Transfer1'
PATH_OBEX = '/org/bluez/obex'
PATH_AGENT = '/CoreELEC/agent/obex'

@ravel.interface(ravel.INTERFACE.SERVER, name=INTERFACE_AGENT)
class Agent(dbus_utils.Agent):

    def __init__(self):
        super().__init__(BUS_NAME, PATH_AGENT)

    def manager_register_agent(self):
        dbus_utils.call_method(
            BUS_NAME, PATH_OBEX, INTERFACE_AGENT_MANAGER, 'RegisterAgent', PATH_AGENT)

    @ravel.method(
        in_signature='',
        out_signature=''
    )
    def Release(self):
        pass

    @ravel.method(
        in_signature='o',
        out_signature='s',
        arg_keys=['path'],
        result_keyword='reply'
    )
    def AuthorizePush(self, transfer):
        name = self.authorize_push(transfer)
        reply[0] = (dbussy.DBUS.Signature('s'), name)

    @ravel.method(
        in_signature='',
        out_signature=''
    )
    def Cancel(self):
        pass

    def reject(self, message):
        raise dbussy.DBusError(ERROR_REJECTED, message)

class Listener(object):

    def __init__(self):
        dbus_utils.BUS.listen_propchanged(
            interface=dbussy.DBUS.INTERFACE_PROPERTIES,
            fallback=True,
            func=self._on_transfer_changed,
            path='/')

    @ravel.signal(name='PropertiesChanged', in_signature='sa{sv}as',
                  arg_keys=('interface', 'changed', 'invalidated'), path_keyword='path')
    def _on_transfer_changed(self, interface, changed, invalidated, path):
        interface = dbus_utils.convert_from_dbussy(interface)
        if interface != INTERFACE_TRANSFER:
            return
        changed = dbus_utils.convert_from_dbussy(changed)
        invalidated = dbus_utils.convert_from_dbussy(invalidated)
        self.on_transfer_changed(interface, changed, invalidated, path)


def transfer_get_all_properties(path):
    return dbus_utils.call_method(BUS_NAME, path, dbussy.DBUS.INTERFACE_PROPERTIES, 'GetAll', INTERFACE_TRANSFER)


def transfer_cancel(path):
    return dbus_utils.call_method(BUS_NAME, path, INTERFACE_TRANSFER, 'Cancel')
