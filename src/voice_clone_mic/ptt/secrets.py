"""Key storage. A missing keychain refuses the save. There is no plaintext file."""

from __future__ import annotations

from dataclasses import dataclass

SERVICE = "local.voice-clone-mic"
ACCOUNT = "elevenlabs-api-key"
UNAVAILABLE = "Secure keychain is unavailable. The key was not saved."


class SecretStoreError(RuntimeError):
    """The key was not written."""


class MemorySecretStore:
    """Process-local stand-in used by tests and the offline demo."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_secret(self, service: str, account: str, secret: str) -> None:
        if not secret:
            raise SecretStoreError("An empty key was not saved.")
        self.values[(service, account)] = secret

    def get_secret(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def delete_secret(self, service: str, account: str) -> None:
        self.values.pop((service, account), None)


@dataclass
class _Backend:
    add: object
    copy: object
    delete: object


class KeychainSecretStore:
    """Saves through an injected Security backend. Never starts a subprocess."""

    def __init__(self, backend: object | None) -> None:
        self.backend = backend

    def set_secret(self, service: str, account: str, secret: str) -> None:
        self._call("add", service, account, secret)

    def get_secret(self, service: str, account: str) -> str | None:
        if self.backend is None:
            return None
        try:
            return self.backend.copy(service, account)
        except SecretStoreError:
            raise
        except Exception:
            raise SecretStoreError(UNAVAILABLE) from None

    def delete_secret(self, service: str, account: str) -> None:
        self._call("delete", service, account)

    def _call(self, name: str, service: str, account: str, secret: str | None = None) -> None:
        if self.backend is None:
            raise SecretStoreError(UNAVAILABLE)
        method = getattr(self.backend, name)
        try:
            if secret is None:
                method(service, account)
            else:
                method(service, account, secret)
        except SecretStoreError:
            raise
        except Exception:
            raise SecretStoreError(UNAVAILABLE) from None


def system_keychain_backend() -> _Backend | None:
    """Return a Security.framework backend, or None when PyObjC Security is absent."""

    try:
        import Security
    except ImportError:
        return None

    def query(service: str, account: str) -> dict:
        return {
            Security.kSecClass: Security.kSecClassGenericPassword,
            Security.kSecAttrService: service,
            Security.kSecAttrAccount: account,
        }

    def add(service: str, account: str, secret: str) -> None:
        payload = query(service, account)
        payload[Security.kSecValueData] = secret.encode("utf-8")
        status, _data = _pair(Security.SecItemAdd(payload, None))
        if status == getattr(Security, "errSecDuplicateItem", -25299):
            delete(service, account)
            status, _data = _pair(Security.SecItemAdd(payload, None))
        if status != 0:
            raise OSError(status)

    def copy(service: str, account: str) -> str | None:
        payload = query(service, account)
        payload[Security.kSecReturnData] = True
        payload[Security.kSecMatchLimit] = Security.kSecMatchLimitOne
        status, data = _pair(Security.SecItemCopyMatching(payload, None))
        if status == getattr(Security, "errSecItemNotFound", -25300):
            return None
        if status != 0 or data is None:
            raise OSError(status)
        raw = bytes(data)
        return raw.decode("utf-8")

    def delete(service: str, account: str) -> None:
        status, _data = _pair(Security.SecItemDelete(query(service, account)))
        missing = getattr(Security, "errSecItemNotFound", -25300)
        if status not in (0, missing):
            raise OSError(status)

    return _Backend(add, copy, delete)


def _pair(result: object) -> tuple[object, object | None]:
    if isinstance(result, tuple):
        if len(result) == 1:
            return result[0], None
        return result[0], result[1]
    return result, None
