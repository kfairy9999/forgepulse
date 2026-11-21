"""Token hashing and AES-GCM wrapping, compatible with the Worker implementation."""

from __future__ import annotations

import binascii
import hmac
import os
import secrets
from hashlib import sha256

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def normalize_master_key(hex_key: str) -> bytes:
    clean = (hex_key or "").strip()
    if len(clean) != 64:
        raise ValueError("FORGEPULSE_MASTER_KEY must be 32 bytes as 64 hex characters")
    try:
        return binascii.unhexlify(clean)
    except binascii.Error as exc:
        raise ValueError("FORGEPULSE_MASTER_KEY is not valid hex") from exc


def hash_token(master_hex: str, token: str) -> str:
    digest = hmac.new(master_hex.strip().encode("utf-8"), token.encode("utf-8"), sha256)
    return digest.hexdigest()


def tokens_equal(master_hex: str, token: str, expected_hash: str) -> bool:
    actual = hash_token(master_hex, token)
    return hmac.compare_digest(actual, expected_hash)


def encrypt_string(master_hex: str, plaintext: str) -> str:
    key = normalize_master_key(master_hex)
    iv = os.urandom(12)
    cipher = AESGCM(key).encrypt(iv, plaintext.encode("utf-8"), None)
    return _b64(iv + cipher)


def decrypt_string(master_hex: str, blob: str) -> str:
    key = normalize_master_key(master_hex)
    packed = _unb64(blob)
    plain = AESGCM(key).decrypt(packed[:12], packed[12:], None)
    return plain.decode("utf-8")


def random_token(prefix: str) -> str:
    return prefix + secrets.token_hex(24)


def random_id(prefix: str) -> str:
    return prefix + secrets.token_hex(10)


def hmac_sha256_hex(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, sha256).hexdigest()


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


def _unb64(value: str) -> bytes:
    import base64

    return base64.b64decode(value.encode("ascii"))
