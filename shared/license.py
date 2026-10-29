"""Signed, machine-bound offline leases. All times are UTC epoch seconds."""
import base64
import json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sign(payload: dict, key: Ed25519PrivateKey) -> dict:
    return {"payload": payload, "signature": base64.b64encode(key.sign(canonical(payload))).decode()}


def verify(envelope: dict, public_key: Ed25519PublicKey, machine_id: str, now: int, last_seen: int = 0) -> dict:
    try:
        payload = envelope["payload"]
        public_key.verify(base64.b64decode(envelope["signature"], validate=True), canonical(payload))
    except Exception as exc:
        raise ValueError("Подпись лицензии недействительна") from exc
    if payload.get("version") != 1 or payload.get("machine_id") != machine_id:
        raise ValueError("Лицензия выдана другому устройству")
    if now < last_seen - 300 or now < payload["issued_at"] - 300:
        raise ValueError("Время компьютера изменено. Подключитесь к серверу лицензий")
    if now >= min(payload["offline_until"], payload["expires_at"]):
        raise ValueError("Срок разрешения истёк. Подключитесь к интернету или продлите подписку")
    return payload
