import secrets

import pytest
import nesa_claimer as app_module

from nesa_claimer import (
    CliError,
    RewardsApp,
    build_claim_payload,
    build_normal_claim_payload,
    canonical_json,
    compressed_public_key,
    derive_normal_identity,
    extract_tx_hash,
    format_nes,
    normalize_private_key,
    ripemd160,
    ripemd160_backend,
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


def test_ripemd160_standard_vectors():
    assert ripemd160(b"").hex() == "9c1185a5c5e9fc54612808977ee8f548b2258d31"
    assert ripemd160(b"a").hex() == "0bdc9d2d256b3ee9daae347be6f4dc835a467ffe"
    assert ripemd160_backend() in {"hashlib/OpenSSL", "PyCryptodome fallback"}


def test_ripemd160_uses_verified_fallback(monkeypatch):
    def unavailable(_value):
        raise ValueError("unsupported hash type ripemd160")

    monkeypatch.setattr(app_module, "_hashlib_ripemd160", unavailable)
    assert ripemd160_backend() == "PyCryptodome fallback"
    assert ripemd160(b"").hex() == "9c1185a5c5e9fc54612808977ee8f548b2258d31"


def test_ripemd160_failure_has_actionable_error(monkeypatch):
    def unavailable(_value):
        raise ValueError("unavailable")

    def missing_fallback(_value):
        raise CliError("fallback missing")

    monkeypatch.setattr(app_module, "_hashlib_ripemd160", unavailable)
    monkeypatch.setattr(app_module, "_pycryptodome_ripemd160", missing_fallback)
    with pytest.raises(CliError, match="Run Option 1"):
        ripemd160_backend()


def test_fallback_preserves_identity_derivation(monkeypatch):
    secret = disposable_secret()
    expected = derive_normal_identity(secret)

    def unavailable(_value):
        raise ValueError("unsupported hash type ripemd160")

    monkeypatch.setattr(app_module, "_hashlib_ripemd160", unavailable)
    assert derive_normal_identity(secret) == expected
    for index in range(len(secret)):
        secret[index] = 0


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


def test_hidden_dummy_key_research_workflow(monkeypatch, tmp_path, capsys):
    secret_text = secrets.token_hex(32)
    monkeypatch.setattr(app_module, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(app_module, "RESEARCH_PATH", tmp_path / "research-report.json")
    monkeypatch.setattr(app_module, "CLAIMS_PATH", tmp_path / "claim-results.json")
    monkeypatch.setattr(app_module.IntPrompt, "ask", lambda *args, **kwargs: 1)
    monkeypatch.setattr(app_module.getpass, "getpass", lambda *args, **kwargs: secret_text)
    monkeypatch.setattr(app_module.Confirm, "ask", lambda *args, **kwargs: True)

    class OfflineClient:
        @staticmethod
        def find_nodes_across_registries(_public_key):
            return [], []

        @staticmethod
        def allocation(_node_id, _cosmos_address=None):
            return {
                "total_allocation": "0",
                "remaining_allocation": "0",
                "claimed": False,
            }

    claimer = RewardsApp()
    claimer.client = OfflineClient()
    claimer.add_keys()
    assert len(claimer.keys) == 1
    claimer.research()
    assert claimer.keys[0].normal_identity["cosmos_address"].startswith("nesa1")
    report = app_module.RESEARCH_PATH.read_text(encoding="utf-8")
    assert secret_text not in report
    assert secret_text not in capsys.readouterr().out
    claimer.wipe_keys()
