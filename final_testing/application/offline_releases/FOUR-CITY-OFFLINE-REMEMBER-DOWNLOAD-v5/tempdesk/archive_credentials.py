"""Only this user's local Probalytics connection; no secrets in files or exports."""
import ctypes
from ctypes import wintypes
import json
import os
import threading

TARGET = 'TempDesk:ProbalyticsReadOnly:v1'


class CredentialStoreError(Exception):
    pass


class WindowsCredentialStore:
    def __init__(self, target=TARGET):
        self.target = target

    def _api(self):
        if os.name != 'nt':
            raise CredentialStoreError('Windows Credential Manager is unavailable')
        class Credential(ctypes.Structure):
            _fields_ = [('Flags', wintypes.DWORD), ('Type', wintypes.DWORD),
                        ('TargetName', wintypes.LPWSTR), ('Comment', wintypes.LPWSTR),
                        ('LastWritten', wintypes.FILETIME), ('CredentialBlobSize', wintypes.DWORD),
                        ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)), ('Persist', wintypes.DWORD),
                        ('AttributeCount', wintypes.DWORD), ('Attributes', ctypes.c_void_p),
                        ('TargetAlias', wintypes.LPWSTR), ('UserName', wintypes.LPWSTR)]
        api = ctypes.WinDLL('advapi32', use_last_error=True)
        api.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
        api.CredWriteW.restype = wintypes.BOOL
        api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.POINTER(ctypes.POINTER(Credential))]
        api.CredReadW.restype = wintypes.BOOL
        api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        api.CredDeleteW.restype = wintypes.BOOL
        api.CredFree.argtypes = [ctypes.c_void_p]
        api.CredFree.restype = None
        return api, Credential

    def save(self, credentials):
        api, Credential = self._api()
        raw = json.dumps({'schema': 1, 'host': 'clickhouse.probalytics.io',
                          'username': credentials[0], 'password': credentials[1]}).encode('utf-8')
        if len(raw) > 2560:
            raise CredentialStoreError('Connection is too large to remember')
        buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        value = Credential(Type=1, TargetName=self.target, Persist=2,
                           CredentialBlobSize=len(raw), CredentialBlob=buffer,
                           UserName=credentials[0], Comment='TempDesk read-only archive, this user and PC')
        try:
            if not api.CredWriteW(ctypes.byref(value), 0):
                raise CredentialStoreError('Connection could not be remembered')
        finally:
            ctypes.memset(buffer, 0, len(raw))

    def load(self):
        api, Credential = self._api()
        pointer = ctypes.POINTER(Credential)()
        if not api.CredReadW(self.target, 1, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == 1168:
                return None
            raise CredentialStoreError('Saved connection could not be read')
        try:
            value = json.loads(ctypes.string_at(pointer.contents.CredentialBlob,
                                               pointer.contents.CredentialBlobSize))
            if (value.get('schema') != 1 or value.get('host') != 'clickhouse.probalytics.io'
                    or not isinstance(value.get('username'), str) or not value['username']
                    or not isinstance(value.get('password'), str) or not value['password']):
                raise CredentialStoreError('Saved connection is invalid')
            return value['username'], value['password']
        except CredentialStoreError:
            raise
        except Exception:
            raise CredentialStoreError('Saved connection is invalid') from None
        finally:
            api.CredFree(pointer)

    def delete(self):
        api, _ = self._api()
        if not api.CredDeleteW(self.target, 1, 0) and ctypes.get_last_error() != 1168:
            raise CredentialStoreError('Saved connection could not be forgotten')


class ArchiveConnection:
    """Validate before using or saving. API metadata contains no credentials."""
    def __init__(self, probe, store=None):
        self.probe = probe
        self.store = store or WindowsCredentialStore()
        self.lock = threading.RLock()
        self.credentials = None
        self.status = 'DISCONNECTED'
        self.saved = False
        self.generation = 0

    def metadata(self):
        with self.lock:
            return {'archive_connection': self.status, 'archive_credentials_saved': self.saved,
                    'archive_credential_storage': 'Windows Credential Manager'}

    def get(self):
        with self.lock:
            return self.credentials

    def restore(self):
        with self.lock:
            generation = self.generation
            self.status = 'RESTORING'
        try:
            credentials = self.store.load()
            with self.lock:
                if generation != self.generation:
                    return
                self.saved = credentials is not None
            if credentials:
                self.probe(credentials)
            with self.lock:
                if generation == self.generation:
                    self.credentials = credentials
                    self.status = 'CONNECTED' if credentials else 'DISCONNECTED'
        except Exception:
            with self.lock:
                if generation == self.generation:
                    self.status = 'SAVED_CONNECTION_UNAVAILABLE'

    def connect(self, username, password, remember):
        if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
            raise ValueError('Enter the read-only Probalytics login')
        credentials = username, password
        self.probe(credentials)
        with self.lock:
            if remember:
                self.store.save(credentials)
            elif self.saved:
                self.store.delete()
            self.generation += 1
            self.credentials = credentials
            self.saved = bool(remember)
            self.status = 'CONNECTED'
            return {'status': self.status, 'credentials_saved': self.saved}

    def disconnect(self, forget=False):
        with self.lock:
            if forget:
                self.store.delete()
                self.saved = False
            self.generation += 1
            self.credentials = None
            self.status = 'DISCONNECTED'
            return {'status': self.status, 'credentials_saved': self.saved}

    def close(self):
        with self.lock:
            self.generation += 1
            self.credentials = None
