# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2020-present Team LibreELEC (https://libreelec.tv)
# Copyright (C) 2020-present Team CoreELEC (https://coreelec.org)

import dbussy
import ravel

import dbus_utils
import log


BUS_NAME = 'net.connman'
ERROR_AGENT_CANCELLED = 'net.connman.Agent.Error.Canceled'
INTERFACE_AGENT = 'net.connman.Agent'
INTERFACE_CLOCK = 'net.connman.Clock'
INTERFACE_MANAGER = 'net.connman.Manager'
INTERFACE_SERVICE = 'net.connman.Service'
INTERFACE_TECHNOLOGY = 'net.connman.Technology'
PATH_TECH_ETHERNET = '/net/connman/technology/ethernet'
PATH_TECH_WIFI = '/net/connman/technology/wifi'
PATH_AGENT = '/CoreELEC/agent/connman'


@ravel.interface(ravel.INTERFACE.SERVER, name=INTERFACE_AGENT)
class Agent(dbus_utils.Agent):

    @log.log_function()
    def __init__(self):
        super().__init__(BUS_NAME, PATH_AGENT)

    def manager_register_agent(self):
        dbus_utils.call_method(
            BUS_NAME, '/', INTERFACE_MANAGER, 'RegisterAgent', PATH_AGENT)

    def manager_unregister_agent(self):
        dbus_utils.call_method(
            BUS_NAME, '/', INTERFACE_MANAGER, 'UnregisterAgent', PATH_AGENT
        )

    @ravel.method(
        in_signature='',
        out_signature=''
    )
    def Cancel(self):
        self.cancel()

    def cancel(self):
        pass

    @ravel.method(
        in_signature='',
        out_signature=''
    )
    def Release(self):
        pass

    @ravel.method(
        in_signature='os',
        out_signature='',
        arg_keys=['path', 'error']
    )
    async def ReportError(self, path, error):
        self.report_error(path, error)

    @ravel.method(
        in_signature='os',
        out_signature='',
        arg_keys=['service', 'url']
    )
    def RequestBrowser(self, path, url):
        raise NotImplementedError

    @ravel.method(
        in_signature='oa{sv}',
        out_signature='a{sv}',
        args_keyword='request',
        result_keyword='reply'
    )
    async def RequestInput(self, request, reply):
        request = dbus_utils.convert_from_dbussy(request)
        input = self.request_input(*request)
        input = {k: (dbussy.DBUS.Signature('s'), v)
                 for (k, v) in input.items()}
        reply[0] = input

    def agent_abort(self):
        raise ravel.ErrorReturn(ERROR_AGENT_CANCELLED, 'Input cancelled')


class Listener(object):

    def __init__(self):
        dbus_utils.BUS.listen_signal(
            interface=INTERFACE_MANAGER,
            fallback=True,
            func=self._on_property_changed,
            path='/',
            name='PropertyChanged')
        dbus_utils.BUS.listen_signal(
            interface=INTERFACE_MANAGER,
            fallback=True,
            func=self._on_services_changed,
            path='/',
            name='ServicesChanged')
        dbus_utils.BUS.listen_signal(
            interface=INTERFACE_SERVICE,
            fallback=True,
            func=self._on_property_changed,
            path='/',
            name='PropertyChanged')
        dbus_utils.BUS.listen_signal(
            interface=INTERFACE_TECHNOLOGY,
            fallback=True,
            func=self._on_technology_changed,
            path='/',
            name='PropertyChanged')

    @ravel.signal(name='PropertyChanged', in_signature='sv', arg_keys=('name', 'value'), path_keyword='path')
    async def _on_property_changed(self, name, value, path):
        value = dbus_utils.convert_from_dbussy(value)
        await self.on_property_changed(name, value, path)

    @ravel.signal(name='ServicesChanged', in_signature='a(oa{sv})ao', arg_keys=('services', 'removed'))
    async def _on_services_changed(self, services, removed):
        services = dbus_utils.convert_from_dbussy(services)
        removed = dbus_utils.convert_from_dbussy(removed)
        await self.on_services_changed(services, removed)

    @ravel.signal(name='PropertyChanged', in_signature='sv', arg_keys=('name', 'value'), path_keyword='path')
    async def _on_technology_changed(self, name, value, path):
        value = dbus_utils.convert_from_dbussy(value)
        await self.on_technology_changed(name, value, path)


def clock_get_properties():
    return dbus_utils.call_method(BUS_NAME, '/', INTERFACE_CLOCK, 'GetProperties')


def clock_set_timeservers(timeservers):
    return dbus_utils.call_method(BUS_NAME, '/', INTERFACE_CLOCK, 'SetProperty', 'Timeservers', (dbussy.DBUS.Signature('as'), timeservers))


def manager_get_properties():
    return dbus_utils.call_method(BUS_NAME, '/', INTERFACE_MANAGER, 'GetProperties')


def manager_get_services():
    return dbus_utils.call_method(BUS_NAME, '/', INTERFACE_MANAGER, 'GetServices')


def manager_get_technologies():
    return dbus_utils.call_method(BUS_NAME, '/', INTERFACE_MANAGER, 'GetTechnologies')


def service_connect(path):
    return dbus_utils.run_method(BUS_NAME, path, INTERFACE_SERVICE, 'Connect')


def service_disconnect(path):
    return dbus_utils.call_method(BUS_NAME, path, INTERFACE_SERVICE, 'Disconnect')


def service_get_properties(path):
    return dbus_utils.call_method(BUS_NAME, path, INTERFACE_SERVICE, 'GetProperties')


def service_remove(path):
    return dbus_utils.call_method(BUS_NAME, path, INTERFACE_SERVICE, 'Remove')


def service_set_autoconnect(path, autoconnect):
    autoconnect = True if autoconnect == '1' else False
    return service_set_property(path, 'AutoConnect', (dbussy.DBUS.Signature('b'), autoconnect))


def service_set_domains_configuration(path, domains):
    return service_set_property(path, 'Domains.Configuration',  (dbussy.DBUS.Signature('as'), domains))


def service_set_ipv4_configuration(path, ipv4):
    return service_set_property(path, 'IPv4.Configuration', (dbussy.DBUS.Signature('a{sv}'), {key: (dbussy.DBUS.Signature('s'), value) for key, value in ipv4.items()}))


def service_set_ipv6_configuration(path, ipv6):
    return service_set_property(path, 'IPv6.Configuration', (dbussy.DBUS.Signature('a{sv}'), {key: (dbussy.DBUS.Signature('y'), int(value)) if key == 'PrefixLength' else (dbussy.DBUS.Signature('s'), value) for key, value in ipv6.items()}))


def service_set_nameservers_configuration(path, nameservers):
    return service_set_property(path, 'Nameservers.Configuration',  (dbussy.DBUS.Signature('as'), nameservers))


def service_set_property(path, name, value):
    return dbus_utils.call_method(BUS_NAME, path, INTERFACE_SERVICE, 'SetProperty', name, value)


def service_set_timeservers_configuration(path, timeservers):
    return service_set_property(path, 'Timeservers.Configuration',  (dbussy.DBUS.Signature('as'), timeservers))


def technology_set_powered(path, state):
    return technology_set_property(path, 'Powered', (dbussy.DBUS.Signature('b'), state))


def technology_set_property(path, name, value):
    return dbus_utils.call_method(BUS_NAME, path, INTERFACE_TECHNOLOGY, 'SetProperty', name, value)


def technology_wifi_scan():
    return dbus_utils.call_method(BUS_NAME, PATH_TECH_WIFI, INTERFACE_TECHNOLOGY, 'Scan')


def technology_wifi_set_tethering(state):
    return technology_set_property(PATH_TECH_WIFI, 'Tethering', (dbussy.DBUS.Signature('b'), state))


def technology_wifi_set_tethering_identifier(identifier):
    return technology_set_property(PATH_TECH_WIFI, 'TetheringIdentifier', (dbussy.DBUS.Signature('s'), identifier))


def technology_wifi_set_tethering_passphrase(passphrase):
    return technology_set_property(PATH_TECH_WIFI, 'TetheringPassphrase', (dbussy.DBUS.Signature('s'), passphrase))


def system_has_connman():
    return BUS_NAME in dbus_utils.list_names()
