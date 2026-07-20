import secrets

import pytest

from nesa_claimer import (
    build_claim_payload,
    build_normal_claim_payload,
    canonical_json,
    compressed_public_key,
    derive_normal_identity,
    extract_tx_hash,
    format_nes,
    normalize_private_key,
    validate_evm_address,
)


def disposable_secret():
    while True:
        try:
            return normalize_private_key(secrets.token_hex(32))
        except Exception:
            continue


def test_random_private_key_derives_compressed_public_key():
    secret = disposable_secret()
    public_key = compressed_public_key(secret)
    assert len(public_key) == 66
    assert public_key[:2] in {"02", "03"}


def test_private_key_accepts_0x_prefix():
    secret = disposable_secret()
    assert normalize_private_key("0x" + secret.hex()) == secret


@pytest.mark.parametrize("value", ["", "0x1234", "g" * 64])
def test_private_key_rejects_invalid_values(value):
    with pytest.raises(Exception):
        normalize_private_key(value)


def test_evm_checksum_validation_and_normalization():
    expected = "0xC693b0F0358D23e19f03e15F964ECa9F9D1ae32d"
    assert validate_evm_address(expected.lower()) == expected
    assert validate_evm_address(expected) == expected


def test_invalid_mixed_case_checksum_is_rejected():
    with pytest.raises(Exception):
        validate_evm_address("0xc693b0F0358D23e19f03e15F964ECa9F9D1ae32d")


def test_claim_payload_matches_official_canonical_shape():
    secret = disposable_secret()
    public_key = compressed_public_key(secret)
    payload = build_claim_payload(
        secret,
        public_key,
        "Node123",
        "0xC693b0F0358D23e19f03e15F964ECa9F9D1ae32d",
        "1230000000000000000",
        nonce="ab" * 32,
        timestamp=1234567890,
    )
    assert payload["data"] == {
        "node_id": "Node123",
        "evm_address": "0xc693b0f0358d23e19f03e15f964eca9f9d1ae32d",
        "allocation": "1230000000000000000",
    }
    assert payload["auth"]["public_key"] == public_key
    assert payload["auth"]["nonce"] == "ab" * 32
    assert payload["auth"]["timestamp"] == 1234567890
    assert len(payload["auth"]["signature"]) == 128


def test_normal_identity_and_dual_signature_payload():
    secret = disposable_secret()
    identity = derive_normal_identity(secret)
    assert identity["cosmos_address"].startswith("nesa1")
    assert len(identity["node_public_key"]) == 64
    assert len(identity["node_id"]) >= 43

    allocation = {
        "total_allocation": "2000000000000000000",
        "remaining_allocation": "2000000000000000000",
        "claimed": False,
    }
    payload = build_normal_claim_payload(
        secret,
        identity,
        "0xC693b0F0358D23e19f03e15F964ECa9F9D1ae32d",
        allocation,
    )
    assert payload["data"]["node_id"] == identity["node_id"]
    assert payload["data"]["allocation_response"] == allocation
    assert len(payload["auth"]["signature"]) == 130
    assert len(payload["auth"]["node_signature"]) == 128


def test_canonical_json_is_sorted_and_compact():
    assert canonical_json({"z": 1, "a": "x"}) == '{"a":"x","z":1}'


def test_recursive_transaction_hash_extraction():
    tx = "0x" + "12" * 32
    assert extract_tx_hash({"clique": {"relay_claim": {"txHash": tx}}}) == tx


def test_amount_formatting_preserves_integer_zero():
    assert format_nes(30 * 10**18) == "30"
    assert format_nes(1230000000000000000) == "1.23"
