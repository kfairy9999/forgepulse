from __future__ import annotations

import pytest

from forgepulse.crypto_util import (
    decrypt_string,
    encrypt_string,
    hash_token,
    hmac_sha256_hex,
    normalize_master_key,
    random_token,
    tokens_equal,
)


def test_encrypt_round_trip() -> None:
    key = "ab" * 32
    blob = encrypt_string(key, "whsec_hello")
    assert blob != "whsec_hello"
    assert decrypt_string(key, blob) == "whsec_hello"
    other = encrypt_string(key, "whsec_hello")
    assert other != blob


def test_token_hash_is_stable_and_compared_safely() -> None:
    key = "ef" * 32
    digest = hash_token(key, "fp_live_abc")
    assert tokens_equal(key, "fp_live_abc", digest) is True
    assert tokens_equal(key, "fp_live_zzz", digest) is False


def test_hmac_and_random_token() -> None:
    assert len(hmac_sha256_hex("secret", b"body")) == 64
    assert random_token("fp_live_").startswith("fp_live_")


def test_master_key_must_be_32_bytes() -> None:
    with pytest.raises(ValueError):
        normalize_master_key("abcd")
    with pytest.raises(ValueError):
        normalize_master_key("zz" * 32)
