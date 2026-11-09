# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2020-present Team CoreELEC (https://coreelec.org)

# WPA Enterprise (802.1X) setup for ConnMan.
#
# ConnMan only connects to 802.1X networks that are provisioned by a config
# file. This module asks for the credentials, retrieves the certificate of the
# authentication server and lets the user confirm it (trust on first use).
#
# To get the certificate without exposing the password, the network is first
# provisioned with a server certificate hash that can never match. The TLS
# handshake then fails before the inner authentication starts, but
# wpa_supplicant still reports the server certificate via its D-Bus
# "Certification" signal. After the user accepted it, the network is
# provisioned again with the hash of exactly this certificate.

import base64
import hashlib
import os
import tempfile
import threading
import time

import ravel
import xbmc
import xbmcgui
from dbussy import DBusError

import dbus_connman
import dbus_utils
import log
import oe


CONFIG_DIR = '/storage/.cache/connman'
CONFIG_PREFIX = 'coreelec-8021x-'
PROBE_HASH = 'hash://server/sha256/' + '0' * 64
WPA_INTERFACE = 'fi.w1.wpa_supplicant1.Interface'
WPA_PATH = '/fi/w1/wpa_supplicant1'
PROBE_TIMEOUT = 30
PROVISION_TIMEOUT = 10

# set while the probe connection runs, so that the expected failure is not
# reported to the user
probing = False


def ssid_hex(service_path):
    # /net/connman/service/wifi_<mac>_<ssid hex>_managed_ieee8021x
    parts = os.path.basename(service_path).split('_')
    if len(parts) < 5 or parts[0] != 'wifi':
        return None
    return parts[2]


def config_path(service_path):
    ssid = ssid_hex(service_path)
    if ssid is None:
        return None
    return os.path.join(CONFIG_DIR, f'{CONFIG_PREFIX}{ssid}.config')


def is_8021x(properties):
    return 'ieee8021x' in properties.get('Security', [])


def is_hidden(service_path):
    # hidden networks have 'hidden' instead of the SSID in the service path
    return ssid_hex(service_path) == 'hidden'


def needs_setup(properties):
    # provisioned services are immutable, everything else needs a config file
    return is_8021x(properties) and not properties.get('Immutable', False)


def _escape(value):
    # GKeyFile value escaping
    value = value.replace('\\', '\\\\').replace('\n', '\\n') \
        .replace('\t', '\\t').replace('\r', '\\r')
    if value.startswith(' '):
        value = '\\s' + value[1:]
    return value


def write_config(service_path, settings):
    path = config_path(service_path)
    ssid = ssid_hex(service_path)
    lines = [
        '# created by the CoreELEC settings add-on',
        f'[service_{ssid}]',
        'Type = wifi',
        f'SSID = {ssid}',
    ]
    for key in ('EAP', 'Phase2', 'Identity', 'AnonymousIdentity',
                'Passphrase', 'CACertFile'):
        if settings.get(key):
            lines.append(f'{key} = {_escape(settings[key])}')
    os.makedirs(CONFIG_DIR, exist_ok=True)
    # contains the password: only root may read it
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as config:
        config.write('\n'.join(lines) + '\n')


def _get_properties(service_path):
    try:
        return dbus_connman.service_get_properties(service_path)
    except Exception:
        # the service disappears for a moment while ConnMan reprovisions it
        return {}


def wait_for_provisioning(service_path, provisioned):
    # ConnMan marks provisioned services as immutable
    deadline = time.time() + PROVISION_TIMEOUT
    while time.time() < deadline:
        if bool(_get_properties(service_path).get('Immutable', False)) == provisioned:
            return True
        time.sleep(0.25)
    log.log(f'provisioning of {service_path} not confirmed', log.WARNING)
    return False


def request_connect(service_path, abort=None):
    # Errors are ignored on purpose: if ConnMan already connects on its
    # own after provisioning, the call just returns InProgress. It must not
    # go through dbus_error_handler(), which restarts the attempt then.
    if abort is not None and abort.is_set():
        return
    try:
        dbus_connman.service_connect(service_path)
    except Exception as e:
        log.log(f'connect {service_path}: {e!r}', log.DEBUG)


def remove_config(service_path):
    path = config_path(service_path)
    if path and os.path.isfile(path):
        os.remove(path)
        return True
    return False


class CertificateListener(object):

    def __init__(self):
        self.certificates = {}
        self.received = threading.Event()

    def start(self):
        dbus_utils.BUS.listen_signal(
            interface=WPA_INTERFACE,
            fallback=True,
            func=self._on_certification,
            path=WPA_PATH,
            name='Certification')

    def stop(self):
        try:
            dbus_utils.BUS.unlisten_signal(
                interface=WPA_INTERFACE,
                fallback=True,
                func=self._on_certification,
                path=WPA_PATH,
                name='Certification')
        except Exception as e:
            log.log(f'unlisten Certification: {e!r}', log.WARNING)

    @ravel.signal(name='Certification', in_signature='a{sv}', arg_keys=('certification',))
    async def _on_certification(self, certification):
        certification = dbus_utils.convert_from_dbussy(certification)
        depth = int(certification.get('depth', -1))
        if depth < 0:
            return
        self.certificates[depth] = certification
        if depth == 0:
            self.received.set()


def _decode_certificate(der):
    # returns subject, issuer and validity; empty dict if not possible
    try:
        import ssl
        pem = ssl.DER_cert_to_PEM_cert(der)
        with tempfile.NamedTemporaryFile('w', suffix='.pem', delete=False) as tmp:
            tmp.write(pem)
            name = tmp.name
        try:
            return ssl._ssl._test_decode_cert(name)
        finally:
            os.remove(name)
    except Exception as e:
        log.log(f'decode certificate: {e!r}', log.WARNING)
        return {}


def _format_name(name):
    # ((('commonName', 'x'),), ...) -> 'CN=x, ...'
    short = {'commonName': 'CN', 'organizationName': 'O',
             'organizationalUnitName': 'OU', 'countryName': 'C'}
    parts = []
    for rdn in name or ():
        for key, value in rdn:
            parts.append(f'{short.get(key, key)}={value}')
    return ', '.join(parts)


def _format_fingerprint(cert_hash):
    cert_hash = cert_hash.upper()
    return ':'.join(cert_hash[i:i + 2] for i in range(0, len(cert_hash), 2))


def retrieve_certificate(service_path, settings):
    # returns (certificate, cancelled)
    global probing
    listener = CertificateListener()
    probe = dict(settings)
    probe['Passphrase'] = 'probe'
    probe['CACertFile'] = PROBE_HASH
    progress = xbmcgui.DialogProgress()
    progress.create('CoreELEC', oe._(33606))
    cancelled = False
    # stops the background connect once the probe is over
    abort = threading.Event()
    probing = True
    listener.start()
    try:
        write_config(service_path, probe)
        wait_for_provisioning(service_path, True)
        # the connect call blocks until ConnMan reports the (expected)
        # failure, run it in the background to keep the dialog responsive
        connector = threading.Thread(target=request_connect, args=(service_path, abort), daemon=True)
        connector.start()
        deadline = time.time() + PROBE_TIMEOUT
        while not listener.received.is_set() and time.time() < deadline:
            if progress.iscanceled():
                cancelled = True
                break
            listener.received.wait(0.5)
    finally:
        abort.set()
        listener.stop()
        try:
            dbus_connman.service_disconnect(service_path)
        except Exception:
            pass
        remove_config(service_path)
        wait_for_provisioning(service_path, False)
        probing = False
        progress.close()
    if cancelled:
        return None, True
    return listener.certificates.get(0), False


def confirm_certificate(certificate):
    der = certificate.get('cert')
    der = bytes(der) if der else b''
    cert_hash = certificate.get('cert_hash') or hashlib.sha256(der).hexdigest()
    info = _decode_certificate(der) if der else {}
    lines = [f"[B]{oe._(33609)}:[/B] {_format_name(info.get('subject')) or certificate.get('subject', '')}"]
    altnames = certificate.get('altsubject') or []
    if altnames:
        lines.append(f"[B]{oe._(33610)}:[/B] {', '.join(altnames)}")
    if info.get('issuer'):
        lines.append(f"[B]{oe._(33611)}:[/B] {_format_name(info['issuer'])}")
    if info.get('notBefore') and info.get('notAfter'):
        lines.append(f"[B]{oe._(33612)}:[/B] {info['notBefore']} - {info['notAfter']}")
    lines.append(f"[B]{oe._(33613)}:[/B]")
    lines.append(_format_fingerprint(cert_hash))
    lines.append('')
    lines.append(oe._(33614))
    accepted = xbmcgui.Dialog().yesno(oe._(33608), '\n'.join(lines))
    return accepted, cert_hash


def ask_settings(name):
    dialog = xbmcgui.Dialog()
    methods = ['PEAP', 'TTLS']
    method = dialog.select(f'{name}: {oe._(33602)}', methods)
    if method < 0:
        return None
    settings = {'EAP': methods[method].lower()}
    if methods[method] == 'PEAP':
        settings['Phase2'] = 'MSCHAPV2'
    else:
        phases = ['MSCHAPV2', 'PAP']
        phase = dialog.select(f'{name}: {oe._(33603)}', phases)
        if phase < 0:
            return None
        settings['Phase2'] = phases[phase]

    def keyboard(label, hidden=False, required=True, default=''):
        # the network name in the heading makes clear what is asked for
        kb = xbmc.Keyboard(default, f'{name}: {oe._(label)}', hidden)
        kb.doModal()
        if not kb.isConfirmed():
            return None
        text = kb.getText()
        if required and not text:
            return None
        return text

    identity = keyboard(33601)
    if identity is None:
        return None
    settings['Identity'] = identity
    # prefilled, so that it can not be mistaken for the password field
    anonymous = keyboard(33604, required=False, default='anonymous')
    if anonymous is None:
        return None
    settings['AnonymousIdentity'] = anonymous
    password = keyboard(33605, hidden=True)
    if password is None:
        return None
    settings['Passphrase'] = password
    return settings


@log.log_function()
def setup(service_path, properties):
    # returns True if the network was provisioned; connecting is handled here
    if config_path(service_path) is None:
        return False
    if is_hidden(service_path):
        xbmcgui.Dialog().ok('CoreELEC', oe._(33615))
        return False
    name = properties.get('Name', '')
    settings = ask_settings(name)
    if settings is None:
        return False
    certificate, cancelled = retrieve_certificate(service_path, settings)
    if cancelled:
        return False
    if not certificate:
        xbmcgui.Dialog().ok('CoreELEC', oe._(33607))
        return False
    accepted, cert_hash = confirm_certificate(certificate)
    if not accepted:
        return False
    settings['CACertFile'] = f'hash://server/sha256/{cert_hash.lower()}'
    write_config(service_path, settings)
    wait_for_provisioning(service_path, True)
    request_connect(service_path)
    return True
