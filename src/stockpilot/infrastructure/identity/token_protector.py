"""Authenticated encryption for renewal credentials, never a browser token store."""

import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from stockpilot.application.ports.bff_security import BffUnauthenticated


class AesTokenProtector:
    def __init__(self, key: str):
        try:
            raw = base64.b64decode(key, validate=True)
            if len(raw) != 32:
                raise ValueError
            self._cipher = AESGCM(raw)
        except Exception:
            raise ValueError("A dedicated base64 32-byte renewal key is required") from None

    def seal(self, token: str, nonce: str, binding: str) -> str:
        iv = os.urandom(12)
        payload = json.dumps([token, nonce], separators=(",", ":")).encode()
        return base64.b64encode(iv + self._cipher.encrypt(iv, payload, binding.encode())).decode()

    def open(self, ciphertext: str, binding: str) -> tuple[str, str]:
        try:
            if not isinstance(ciphertext, str) or len(ciphertext) > 100000:
                raise ValueError
            raw = base64.b64decode(ciphertext, validate=True)
            value = json.loads(self._cipher.decrypt(raw[:12], raw[12:], binding.encode()))
            if not isinstance(value, list) or len(value) != 2 or not all(isinstance(v, str) for v in value):
                raise ValueError
            return value[0], value[1]
        except Exception:
            raise BffUnauthenticated("Renewal credentials unavailable") from None
