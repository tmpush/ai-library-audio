import base64
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from shared.license import verify


class EncryptedVault:
    """Encrypt license state with a key held by Windows Credential Manager/macOS Keychain."""
    def __init__(self, path: Path, account: str):
        self.path, self.account = path, account

    def cipher(self):
        import keyring
        backend = keyring.get_keyring()
        # No plaintext or third-party fallback keyring is accepted.
        module = type(backend).__module__
        if module not in {"keyring.backends.Windows", "keyring.backends.macOS"}:
            raise ValueError("Не найдено системное защищённое хранилище Windows/macOS")
        key = keyring.get_password("local-books-order", self.account)
        if key is None:
            if self.path.exists():
                raise ValueError("Ключ системного хранилища недоступен. Повторно активируйте лицензию после восстановления хранилища")
            key = Fernet.generate_key().decode()
            keyring.set_password("local-books-order", self.account, key)
        return Fernet(key.encode())

    def load(self):
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.cipher().decrypt(self.path.read_bytes()))
        except (InvalidToken, json.JSONDecodeError) as exc:
            raise ValueError("Локальная лицензия повреждена") from exc

    def save(self, value):
        encrypted = self.cipher().encrypt(json.dumps(value).encode())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_bytes(encrypted)
        temp.replace(self.path)


class LicenseClient:
    def __init__(self, server_url, public_key, machine_id, vault, transport=None, clock=time.time):
        url = urlparse(server_url)
        if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"}):
            raise ValueError("Для сервера лицензий нужен HTTPS")
        self.url, self.machine_id, self.vault = server_url.rstrip("/"), machine_id, vault
        self.public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
        self.http = transport or httpx.Client(timeout=8, follow_redirects=False)
        self.clock = clock
        self.lock = threading.RLock()
        self.next_attempt = 0

    def _request(self, path, key):
        response = self.http.post(self.url + path, json={"key": key, "machine_id": self.machine_id})
        if response.status_code in {401, 403}:
            if path == '/v1/check':
                self.vault.save({"denied": True, "last_seen": int(self.clock())})
            raise ValueError("Лицензия отозвана, срок истёк или устройство отвязано")
        response.raise_for_status()
        envelope = response.json()
        now = int(self.clock())
        verify(envelope, self.public_key, self.machine_id, now)
        return envelope

    def activate(self, key):
        with self.lock:
            envelope = self._request("/v1/activate", key.strip())
            self.vault.save({"key": key.strip(), "lease": envelope, "last_seen": int(self.clock())})
            self.next_attempt = 0
            return envelope["payload"]

    def authorize(self, force_check=False):
        with self.lock:
            state = self.vault.load()
            if not state.get("lease") or state.get("denied"):
                raise ValueError("Введите действующий лицензионный ключ для синтеза")
            now = int(self.clock())
            envelope = state["lease"]
            # Verify signature and machine binding even when offline lease has expired;
            # a successful online renewal may refresh the expired lease below.
            try:
                payload = verify(envelope, self.public_key, self.machine_id, now, state.get("last_seen", 0))
                local_error = None
            except ValueError as exc:
                payload = {}
                local_error = exc
            due = force_check or local_error is not None or now >= payload.get("check_after", 0)
            if due and (force_check or time.monotonic() >= self.next_attempt):
                self.next_attempt = time.monotonic() + 60
                try:
                    envelope = self._request("/v1/check", state["key"])
                    payload = envelope["payload"]
                    local_error = None
                    state["lease"] = envelope
                except (httpx.TransportError, httpx.HTTPStatusError):
                    # Only a valid cached lease survives a transport/server outage.
                    pass
            if local_error is not None:
                raise local_error
            state["last_seen"] = max(now, state.get("last_seen", 0))
            self.vault.save(state)
            return payload

    def status(self):
        try:
            payload = self.authorize()
            return {"valid": True, "expires_at": payload["expires_at"], "offline_until": payload["offline_until"], "plan": payload["plan"]}
        except (ValueError, OSError, httpx.HTTPError) as exc:
            return {"valid": False, "message": str(exc)}
