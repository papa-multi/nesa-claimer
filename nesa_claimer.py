#!/usr/bin/env python3
"""Interactive batch discovery and claiming for historical Nesa miner rewards."""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import platform
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import ecdsa
import base58
import requests
from bech32 import bech32_encode, convertbits
from coincurve import PrivateKey
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from eth_utils import is_address, is_checksum_address, to_checksum_address
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table


APP_NAME = "nesa-claimer"
VERSION = "1.0.0"
HISTORICAL_API = "https://api-dev.nesa.ai"
REGISTRY_APIS = (
    ("legacy dashboard", "https://api-test.nesa.ai"),
    ("development dashboard", HISTORICAL_API),
    ("current dashboard", "https://api.nesa.ai"),
)
ALLOCATION_API = "https://rewards-proxy.nesa.ai/api/allocation"
ALTERNATE_CLAIM_API = "https://rewards-proxy.nesa.ai/api/claim-alternate"
NORMAL_CLAIM_API = "https://rewards-proxy.nesa.ai/api/claim"
EVM_RPC = "https://erpc.nesa.ai"
EXPLORER_TX = "https://explorer-evm.nesa.ai/tx/"
ANES_PER_NES = Decimal(10**18)
RIPEMD160_EMPTY_DIGEST = bytes.fromhex(
    "9c1185a5c5e9fc54612808977ee8f548b2258d31"
)
MAX_RETRIES = 5
PAGE_SIZE = 20
CLAIM_DELAY_SECONDS = 5

STATE_DIR = Path(
    os.environ.get("NESA_CLAIMER_STATE_DIR", "~/.local/state/nesa-claimer")
).expanduser()
CONFIG_PATH = STATE_DIR / "config.json"
RESEARCH_PATH = STATE_DIR / "research-report.json"
CLAIMS_PATH = STATE_DIR / "claim-results.json"

console = Console()


class CliError(RuntimeError):
    """A user-facing, safely printable error."""


class AmbiguousClaimError(CliError):
    """A request may have submitted, so retrying automatically is unsafe."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    """Write secret-free state atomically with owner-only permissions."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CliError(f"Could not read state file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CliError(f"State file has an invalid format: {path}")
    return value


def normalize_private_key(raw: str) -> bytearray:
    value = raw.strip()
    if value.startswith("0x"):
        value = value[2:]
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise CliError("Expected exactly 32 bytes encoded as 64 hexadecimal characters.")
    secret = bytearray.fromhex(value)
    try:
        ecdsa.SigningKey.from_string(bytes(secret), curve=ecdsa.SECP256k1)
    except Exception as exc:
        for index in range(len(secret)):
            secret[index] = 0
        raise CliError("The value is not a valid secp256k1 private key.") from exc
    return secret


def compressed_public_key(secret: bytes | bytearray) -> str:
    signing_key = ecdsa.SigningKey.from_string(bytes(secret), curve=ecdsa.SECP256k1)
    raw = signing_key.get_verifying_key().to_string()
    prefix = b"\x02" if raw[-1] % 2 == 0 else b"\x03"
    return (prefix + raw[:32]).hex()


def public_key_fingerprint(public_key: str) -> str:
    return hashlib.sha256(bytes.fromhex(public_key)).hexdigest()[:12]


def _hashlib_ripemd160(value: bytes) -> bytes:
    digest = hashlib.new("ripemd160")
    digest.update(value)
    return digest.digest()


def _pycryptodome_ripemd160(value: bytes) -> bytes:
    try:
        from Crypto.Hash import RIPEMD160
    except (ImportError, ModuleNotFoundError) as exc:
        raise CliError(
            "RIPEMD160 is unavailable through both Python/OpenSSL and the "
            "PyCryptodome fallback. Run Option 1 to install or repair prerequisites."
        ) from exc
    return RIPEMD160.new(data=value).digest()


def ripemd160_backend() -> str:
    """Return a backend only after it passes the standard empty-message vector."""
    try:
        if _hashlib_ripemd160(b"") == RIPEMD160_EMPTY_DIGEST:
            return "hashlib/OpenSSL"
    except Exception:
        pass

    try:
        if _pycryptodome_ripemd160(b"") == RIPEMD160_EMPTY_DIGEST:
            return "PyCryptodome fallback"
    except Exception:
        pass

    raise CliError(
        "RIPEMD160 support is unavailable or failed its cryptographic self-test. "
        "Run Option 1 to install or repair prerequisites, then retry research."
    )


def ripemd160(value: bytes) -> bytes:
    backend = ripemd160_backend()
    if backend == "hashlib/OpenSSL":
        return _hashlib_ripemd160(value)
    return _pycryptodome_ripemd160(value)


def derive_normal_identity(
    secret: bytes | bytearray, bech32_prefix: str = "nesa"
) -> dict[str, str]:
    """Derive the identity used by Nesa's official normal claim script."""
    secret_bytes = bytes(secret)
    secp_public = bytes.fromhex(compressed_public_key(secret_bytes))
    address_bytes = ripemd160(hashlib.sha256(secp_public).digest())
    words = convertbits(address_bytes, 8, 5)
    if words is None:
        raise CliError("Could not derive the Nesa signing address.")
    cosmos_address = bech32_encode(bech32_prefix, words)

    seed = hashlib.sha256(secret_bytes).digest()
    ed_private = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    node_public = ed_private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    node_id = base58.b58encode(hashlib.sha256(node_public).digest()).decode("ascii")
    return {
        "cosmos_address": cosmos_address,
        "node_id": node_id,
        "node_public_key": node_public.hex(),
    }


def runtime_preflight() -> dict[str, str]:
    """Verify the installed runtime and cryptographic primitives without networking."""
    if sys.version_info < (3, 10):
        raise CliError(
            f"Python 3.10 or newer is required; detected {platform.python_version()}."
        )

    backend = ripemd160_backend()
    if ripemd160(b"a").hex() != "0bdc9d2d256b3ee9daae347be6f4dc835a467ffe":
        raise CliError("RIPEMD160 failed its standard cryptographic test vector.")

    zero_address = validate_evm_address("0x0000000000000000000000000000000000000000")
    if zero_address != "0x0000000000000000000000000000000000000000":
        raise CliError("EVM address checksum verification failed.")

    secret: bytearray | None = None
    try:
        while secret is None:
            candidate = bytearray(secrets.token_bytes(32))
            try:
                ecdsa.SigningKey.from_string(bytes(candidate), curve=ecdsa.SECP256k1)
                secret = candidate
            except Exception:
                for index in range(len(candidate)):
                    candidate[index] = 0
        identity = derive_normal_identity(secret)
        public_key = compressed_public_key(secret)
        if (
            len(public_key) != 66
            or not identity["cosmos_address"].startswith("nesa1")
            or len(identity["node_public_key"]) != 64
            or not identity["node_id"]
        ):
            raise CliError("Disposable identity derivation preflight failed.")
    finally:
        if secret is not None:
            for index in range(len(secret)):
                secret[index] = 0

    return {
        "python": platform.python_version(),
        "executable": sys.executable,
        "ripemd160": backend,
        "status": "ready",
    }


def validate_evm_address(value: str) -> str:
    address = value.strip()
    if not is_address(address):
        raise CliError("Invalid EVM address. Expected 0x followed by 40 hexadecimal characters.")
    body = address[2:]
    if not (body.islower() or body.isupper()) and not is_checksum_address(address):
        raise CliError("The mixed-case EVM address has an invalid EIP-55 checksum.")
    return to_checksum_address(address)


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def build_claim_payload(
    secret: bytes | bytearray,
    public_key: str,
    node_id: str,
    evm_address: str,
    allocation: str,
    *,
    nonce: str | None = None,
    timestamp: int | None = None,
) -> dict[str, Any]:
    destination = evm_address.lower()
    message_object = {
        "action": "claim_rewards",
        "allocation": str(allocation),
        "auth_mode": "alternate_key",
        "evm_address": destination,
        "node_id": node_id,
        "version": 1,
    }
    message = canonical_json(message_object)
    nonce = nonce or secrets.token_hex(32)
    timestamp = timestamp if timestamp is not None else int(time.time())
    signed_text = f"{message}:{nonce}:{timestamp}".encode("utf-8")
    signing_key = ecdsa.SigningKey.from_string(bytes(secret), curve=ecdsa.SECP256k1)
    signature = signing_key.sign(signed_text).hex()
    return {
        "data": {
            "node_id": node_id,
            "evm_address": destination,
            "allocation": str(allocation),
        },
        "auth": {
            "public_key": public_key,
            "nonce": nonce,
            "timestamp": timestamp,
            "signature": signature,
        },
    }


def build_normal_claim_payload(
    secret: bytes | bytearray,
    identity: dict[str, str],
    evm_address: str,
    allocation_response: dict[str, Any],
) -> dict[str, Any]:
    """Build the dual-signature payload used by Nesa's official normal claim."""
    allocation = allocation_response.get("remaining_allocation")
    if allocation is None:
        allocation = allocation_response.get("allocation")
    claim_data = {
        "cosmos_address": identity["cosmos_address"],
        "node_id": identity["node_id"],
        "node_public_key": identity["node_public_key"],
        "evm_address": evm_address,
        "allocation": allocation,
        "allocation_response": allocation_response,
        "timestamp": utc_now(),
    }
    encoded = canonical_json(claim_data).encode("utf-8")
    digest = hashlib.sha256(encoded).digest()
    secp = PrivateKey(bytes(secret))
    seed = hashlib.sha256(bytes(secret)).digest()
    ed_private = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    return {
        "data": claim_data,
        "auth": {
            "message_hash": digest.hex(),
            "signature": secp.sign_recoverable(digest, hasher=None).hex(),
            "public_key": secp.public_key.format(compressed=True).hex(),
            "signing_algorithm": "secp256k1_recoverable_sha256_canonical_json",
            "node_signature": ed_private.sign(encoded).hex(),
            "node_signing_algorithm": "ed25519_canonical_json",
        },
    }


def extract_tx_hash(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("tx_hash", "txHash", "transaction_hash", "transactionHash", "hash"):
            candidate = value.get(key)
            if isinstance(candidate, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", candidate):
                return candidate.lower()
        for nested in value.values():
            found = extract_tx_hash(nested)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = extract_tx_hash(nested)
            if found:
                return found
    return None


def allocation_claimed(value: dict[str, Any]) -> bool:
    if value.get("claimed") is True:
        return True
    status = value.get("claimed_status")
    if isinstance(status, dict):
        if status.get("claimed") is True:
            return True
        try:
            return Decimal(str(status.get("claimed_amount", "0"))) > 0
        except InvalidOperation:
            return False
    return False


def remaining_allocation(value: dict[str, Any]) -> int:
    for key in ("remaining_allocation", "claimable_allocation", "allocation", "amount"):
        candidate = value.get(key)
        if candidate is not None and not isinstance(candidate, dict):
            try:
                return int(candidate)
            except (TypeError, ValueError) as exc:
                raise CliError(f"Allocation API returned invalid {key}: {candidate}") from exc
    return 0


def nes(anes: int | str) -> Decimal:
    return Decimal(str(anes)) / ANES_PER_NES


def format_nes(anes: int | str) -> str:
    rendered = f"{nes(anes):f}"
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


@dataclass
class NodeReward:
    node_id: str
    moniker: str | None
    total_anes: int
    remaining_anes: int
    claimed: bool
    source: str = "historical registry"
    claim_mode: str = "alternate"
    cosmos_address: str | None = None
    node_public_key: str | None = None
    public_record: dict[str, Any] = field(default_factory=dict)


@dataclass
class KeyEntry:
    number: int
    secret: bytearray = field(repr=False)
    public_key: str
    fingerprint: str
    nodes: list[NodeReward] = field(default_factory=list)
    normal_identity: dict[str, str] = field(default_factory=dict)
    lookup_notes: list[str] = field(default_factory=list)

    def wipe(self) -> None:
        for index in range(len(self.secret)):
            self.secret[index] = 0


class NesaClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": f"{APP_NAME}/{VERSION}"})
        self.unavailable_registries: set[str] = set()

    def _get(
        self, url: str, *, max_retries: int = MAX_RETRIES, **kwargs: Any
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                response = self.session.get(url, timeout=30, **kwargs)
                if response.status_code == 429 or response.status_code >= 500:
                    raise requests.HTTPError(
                        f"HTTP {response.status_code}", response=response
                    )
                response.raise_for_status()
                value = response.json()
                if not isinstance(value, dict):
                    raise CliError("Server returned an unexpected JSON value.")
                return value
            except (requests.RequestException, ValueError, CliError) as exc:
                last_error = exc
                if attempt == max_retries - 1:
                    break
                retry_after = None
                response = getattr(exc, "response", None)
                if response is not None:
                    retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                time.sleep(min(delay, 30))
        raise CliError(f"Public API request failed after retries: {last_error}")

    def find_nodes(
        self,
        public_key: str,
        base_url: str = HISTORICAL_API,
        *,
        max_retries: int = MAX_RETRIES,
    ) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        skip = 0
        seen_pages: set[tuple[str, ...]] = set()
        while True:
            url = f"{base_url}/nodes/{quote(public_key, safe='')}/list"
            response = self._get(
                url,
                params={"limit": PAGE_SIZE, "skip": skip},
                max_retries=max_retries,
            )
            items = response.get("list") or response.get("data") or []
            if isinstance(items, dict):
                items = items.get("list") or []
            if not isinstance(items, list):
                raise CliError("Historical registry returned an invalid node list.")

            page_ids: list[str] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                node_id = item.get("node_id") or item.get("nodeId") or item.get("nodeID")
                returned_key = str(item.get("public_key") or "").lower()
                if node_id and returned_key == public_key.lower():
                    found[str(node_id)] = item
                    page_ids.append(str(node_id))

            page_signature = tuple(page_ids)
            if items and page_signature in seen_pages:
                raise CliError("Historical registry repeated a page; pagination stopped safely.")
            seen_pages.add(page_signature)
            skip += len(items)
            total = response.get("total_count")
            if not items or len(items) < PAGE_SIZE:
                break
            if isinstance(total, int) and skip >= total:
                break
        return [found[node_id] for node_id in sorted(found)]

    def find_nodes_across_registries(
        self, public_key: str
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Search every known public dashboard registry without treating outages as zero."""
        found: dict[str, dict[str, Any]] = {}
        notes: list[str] = []
        for label, base_url in REGISTRY_APIS:
            if base_url in self.unavailable_registries:
                notes.append(f"{label} skipped after an earlier request failed")
                continue
            try:
                # The legacy service currently tends to return a slow 504. One
                # attempt is enough per run; if it fails, later keys skip it.
                retries = 1 if base_url == "https://api-test.nesa.ai" else MAX_RETRIES
                records = self.find_nodes(
                    public_key, base_url=base_url, max_retries=retries
                )
            except CliError as exc:
                self.unavailable_registries.add(base_url)
                notes.append(f"{label} unavailable: {exc}")
                continue
            for record in records:
                node_id = str(
                    record.get("node_id") or record.get("nodeId") or record.get("nodeID")
                )
                copy = dict(record)
                copy["_registry_source"] = label
                found.setdefault(node_id, copy)
        return [found[node_id] for node_id in sorted(found)], notes

    def allocation(
        self, node_id: str, cosmos_address: str | None = None
    ) -> dict[str, Any]:
        params = {"node_id": node_id}
        if cosmos_address:
            params["cosmos_address"] = cosmos_address
        return self._get(ALLOCATION_API, params=params)

    def submit_claim(
        self, payload: dict[str, Any], node_id: str, endpoint: str
    ) -> dict[str, Any]:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self.session.post(endpoint, json=payload, timeout=60)
                try:
                    body = response.json()
                except ValueError:
                    body = {"raw_response": response.text[:1000]}
            except requests.RequestException as exc:
                raise AmbiguousClaimError(
                    f"Claim connection ended without a response for {node_id}: {exc}"
                ) from exc

            if response.ok and isinstance(body, dict):
                return body

            printable = json.dumps(body, sort_keys=True)
            safe_connect_502 = (
                response.status_code == 502
                and (
                    "connect_address" in printable
                    or "try again in 30 seconds" in printable.lower()
                )
            )
            if safe_connect_502 and attempt < MAX_RETRIES:
                current = self.allocation(node_id)
                if allocation_claimed(current):
                    raise AmbiguousClaimError(
                        "Allocation became claimed after a 502 response; automatic retry stopped."
                    )
                console.print(
                    f"    [yellow]Temporary backend 502; retrying in 30 seconds "
                    f"({attempt}/{MAX_RETRIES})…[/yellow]"
                )
                time.sleep(30)
                continue
            raise CliError(
                f"Claim API rejected {node_id} with HTTP {response.status_code}: {printable}"
            )
        raise CliError(f"Claim retries exhausted for {node_id}")

    def rpc(self, method: str, params: list[Any]) -> Any:
        try:
            response = self.session.post(
                EVM_RPC,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=30,
            )
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise CliError(f"Nesa EVM RPC request failed: {exc}") from exc
        if body.get("error"):
            raise CliError(f"Nesa EVM RPC error: {body['error']}")
        return body.get("result")

    def wait_for_receipt(self, tx_hash: str, timeout: int = 180) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            receipt = self.rpc("eth_getTransactionReceipt", [tx_hash])
            if isinstance(receipt, dict):
                return receipt
            time.sleep(3)
        raise CliError(f"Timed out waiting for receipt: {tx_hash}")


class RewardsApp:
    def __init__(self) -> None:
        self.client = NesaClient()
        self.keys: list[KeyEntry] = []
        self.research_confirmed = False
        self.evm_address: str | None = None
        self.claim_records: dict[str, dict[str, Any]] = {}
        self._load_public_state()

    def _load_public_state(self) -> None:
        config = load_json(CONFIG_PATH, {})
        address = config.get("evm_address")
        if isinstance(address, str):
            try:
                self.evm_address = validate_evm_address(address)
            except CliError:
                pass
        claims = load_json(CLAIMS_PATH, {"claims": []}).get("claims", [])
        if isinstance(claims, list):
            self.claim_records = {
                str(item.get("node_id")): item
                for item in claims
                if isinstance(item, dict) and item.get("node_id")
            }

    def run(self) -> None:
        try:
            while True:
                self._header()
                self._status()
                menu = Table(box=box.ROUNDED, show_header=False, padding=(0, 1))
                menu.add_column(style="bold cyan", width=3)
                menu.add_column()
                menu.add_row("1", "Install all prerequisites")
                menu.add_row("2", "Securely add private keys")
                menu.add_row("3", "Research Node IDs and rewards")
                menu.add_row("4", "Add or change EVM destination address")
                menu.add_row("5", "Claim all available rewards")
                menu.add_row("0", "Exit")
                console.print(menu)
                choice = Prompt.ask(
                    "Select an option", choices=["0", "1", "2", "3", "4", "5"]
                )
                try:
                    if choice == "1":
                        self.install_prerequisites()
                    elif choice == "2":
                        self.add_keys()
                    elif choice == "3":
                        self.research()
                    elif choice == "4":
                        self.add_evm_address()
                    elif choice == "5":
                        self.claim_all()
                    else:
                        break
                except (CliError, OSError) as exc:
                    console.print(Panel(str(exc), title="Operation stopped", border_style="red"))
                if choice != "0":
                    Prompt.ask("Press Enter to return to the main menu", default="")
        finally:
            self.wipe_keys()
            console.print("[dim]Private keys cleared from application memory.[/dim]")

    def _header(self) -> None:
        console.clear()
        console.print(
            Panel.fit(
                f"[bold cyan]Nesa Claimer[/bold cyan]\n"
                f"[dim]Secure multi-key discovery and claiming · v{VERSION}[/dim]",
                border_style="cyan",
            )
        )

    def _status(self) -> None:
        state = Table.grid(padding=(0, 2))
        state.add_column(style="dim")
        state.add_column()
        state.add_row("Private keys in memory", str(len(self.keys)))
        state.add_row(
            "Research confirmed",
            "[green]Yes[/green]" if self.research_confirmed else "[yellow]No[/yellow]",
        )
        state.add_row("EVM destination", self.evm_address or "[yellow]Not configured[/yellow]")
        console.print(state)
        console.print()

    def install_prerequisites(self) -> None:
        checks = Table(title="Prerequisite status", box=box.ROUNDED)
        checks.add_column("Component")
        checks.add_column("Status")
        checks.add_row("Python", f"[green]{sys.version.split()[0]}[/green]")
        checks.add_row("requests", f"[green]{requests.__version__}[/green]")
        checks.add_row("ecdsa", f"[green]{ecdsa.__version__}[/green]")
        checks.add_row("Terminal UI", "[green]Rich installed[/green]")
        checks.add_row("EIP-55 / Keccak", "[green]eth-utils installed[/green]")
        try:
            backend = ripemd160_backend()
            checks.add_row("RIPEMD160", f"[green]{backend} verified[/green]")
        except CliError:
            checks.add_row("RIPEMD160", "[red]Unavailable — repair required[/red]")
        console.print(checks)

        installer = Path(__file__).resolve().with_name("install.sh")
        if not installer.is_file():
            console.print(
                "[green]All runtime prerequisites are installed.[/green] "
                "The standalone system installer is available in the GitHub repository."
            )
            return
        console.print("[cyan]Running the complete prerequisite installer…[/cyan]")
        result = subprocess.run(["bash", str(installer)], check=False)
        if result.returncode != 0:
            raise CliError(f"Prerequisite installer exited with status {result.returncode}.")
        try:
            result = runtime_preflight()
        except CliError as exc:
            raise CliError(
                "Installation completed, but the application preflight still failed. "
                "Check the installer output and Python environment before continuing."
            ) from exc
        console.print(
            f"[green]Runtime verified: Python {result['python']}, "
            f"RIPEMD160 via {result['ripemd160']}.[/green]"
        )
        console.print("[bold green]All prerequisites installed and verified.[/bold green]")

    def wipe_keys(self) -> None:
        for entry in self.keys:
            entry.wipe()
        self.keys.clear()
        self.research_confirmed = False

    def add_keys(self) -> None:
        if self.keys and not Confirm.ask(
            "Replace the private keys currently held in memory?", default=False
        ):
            return
        count = IntPrompt.ask("How many private keys do you want to enter?", default=1)
        if count < 1:
            raise CliError("The number of keys must be at least 1.")
        if count > 10000 and not Confirm.ask(
            f"You entered {count:,} keys. Continue?", default=False
        ):
            return

        staged: list[KeyEntry] = []
        seen: set[str] = set()
        console.print(
            Panel(
                "Input is hidden. Keys remain in memory only and are never written "
                "to reports, configuration, logs, or command arguments.",
                title="Secure entry",
                border_style="green",
            )
        )
        try:
            number = 1
            while number <= count:
                raw = getpass.getpass(f"Private key {number}/{count}: ")
                try:
                    secret = normalize_private_key(raw)
                    raw = ""
                    public_key = compressed_public_key(secret)
                    if public_key in seen:
                        for index in range(len(secret)):
                            secret[index] = 0
                        console.print("  [yellow]Duplicate key skipped; enter a different key.[/yellow]")
                        continue
                    staged.append(
                        KeyEntry(
                            number=number,
                            secret=secret,
                            public_key=public_key,
                            fingerprint=public_key_fingerprint(public_key),
                        )
                    )
                    seen.add(public_key)
                    console.print(
                        f"  [green]✓[/green] Key {number} validated "
                        f"([dim]{public_key_fingerprint(public_key)}[/dim])"
                    )
                    number += 1
                except CliError as exc:
                    console.print(f"  [red]Invalid key:[/red] {exc}")
        except (KeyboardInterrupt, EOFError):
            for entry in staged:
                entry.wipe()
            raise CliError("Key entry cancelled; no new keys were retained.")

        self.wipe_keys()
        self.keys = staged
        self.research_confirmed = False
        console.print(f"[bold green]{len(self.keys)} unique private keys loaded securely.[/bold green]")

    def research(self) -> None:
        if not self.keys:
            raise CliError("Choose Option 2 and add private keys first.")
        backend = ripemd160_backend()
        console.print(f"[dim]RIPEMD160 preflight passed ({backend}).[/dim]")
        self.research_confirmed = False
        global_nodes: dict[str, int] = {}

        for entry in self.keys:
            with console.status(
                f"Researching key {entry.number}/{len(self.keys)} "
                f"[{entry.fingerprint}]…",
                spinner="dots",
            ):
                records, registry_notes = self.client.find_nodes_across_registries(
                    entry.public_key
                )
                nodes: list[NodeReward] = []
                entry.lookup_notes = list(registry_notes)
                identity = derive_normal_identity(entry.secret)
                entry.normal_identity = identity
                for record in records:
                    node_id = str(
                        record.get("node_id") or record.get("nodeId") or record.get("nodeID")
                    )
                    previous = global_nodes.get(node_id)
                    if previous is not None and previous != entry.number:
                        raise CliError(
                            f"Node ID {node_id} was returned for two different signing keys; "
                            "research stopped for manual review."
                        )
                    global_nodes[node_id] = entry.number
                    reward = self.client.allocation(node_id)
                    nodes.append(
                        NodeReward(
                            node_id=node_id,
                            moniker=record.get("moniker"),
                            total_anes=int(reward.get("total_allocation") or 0),
                            remaining_anes=remaining_allocation(reward),
                            claimed=allocation_claimed(reward),
                            source=str(
                                record.get("_registry_source")
                                or "historical public-key registry"
                            ),
                            claim_mode="alternate",
                            public_record=record,
                        )
                    )

                # Newer official installations derive this identity from the
                # private key. Only present it as an associated node when the
                # authoritative rewards service actually has an allocation for
                # it; deriving an ID alone is not historical evidence.
                normal_reward = self.client.allocation(
                    identity["node_id"], identity["cosmos_address"]
                )
                normal_total = int(normal_reward.get("total_allocation") or 0)
                normal_remaining = remaining_allocation(normal_reward)
                normal_claimed = allocation_claimed(normal_reward)
                if normal_total > 0 or normal_remaining > 0 or normal_claimed:
                    if not any(node.node_id == identity["node_id"] for node in nodes):
                        nodes.append(
                            NodeReward(
                                node_id=identity["node_id"],
                                moniker=None,
                                total_anes=normal_total,
                                remaining_anes=normal_remaining,
                                claimed=normal_claimed,
                                source="verified deterministic reward identity",
                                claim_mode="normal",
                                cosmos_address=identity["cosmos_address"],
                                node_public_key=identity["node_public_key"],
                            )
                        )
                else:
                    entry.lookup_notes.append(
                        "The official deterministic identity has no reward allocation."
                    )
                if not records:
                    entry.lookup_notes.append(
                        "No match was returned by the reachable public-key registry. "
                        "This does not rule out an older independently generated Node ID."
                    )
                entry.nodes = nodes
            console.print(
                f"[green]✓[/green] Key {entry.number}: {len(entry.nodes)} verified rewarded "
                "Node ID(s) found"
            )

        self.show_research_results()
        self._save_research_report()
        self.research_confirmed = Confirm.ask(
            "Do you confirm that you reviewed these Node IDs and reward amounts?",
            default=False,
        )
        if self.research_confirmed:
            console.print("[bold green]Research results confirmed.[/bold green]")
        else:
            console.print("[yellow]Results were not confirmed; claiming remains disabled.[/yellow]")

    def show_research_results(self) -> None:
        grand_total = 0
        grand_remaining = 0
        table = Table(
            title="Historical Node IDs and Rewards",
            box=box.ROUNDED,
            show_lines=True,
        )
        table.add_column("Key", justify="right", style="cyan", no_wrap=True)
        table.add_column("Fingerprint", style="dim", no_wrap=True)
        table.add_column("Node ID")
        table.add_column("Moniker")
        table.add_column("Total NES", justify="right")
        table.add_column("Remaining NES", justify="right")
        table.add_column("Evidence")
        table.add_column("Status")

        for entry in self.keys:
            key_total = sum(node.total_anes for node in entry.nodes)
            key_remaining = sum(
                node.remaining_anes for node in entry.nodes if not node.claimed
            )
            grand_total += key_total
            grand_remaining += key_remaining
            if not entry.nodes:
                table.add_row(
                    str(entry.number), entry.fingerprint, "—", "—", "0", "0",
                    "Public lookup incomplete", "No verified reward match"
                )
                continue
            for index, node in enumerate(entry.nodes):
                table.add_row(
                    str(entry.number) if index == 0 else "",
                    entry.fingerprint if index == 0 else "",
                    node.node_id,
                    node.moniker or "—",
                    format_nes(node.total_anes),
                    format_nes(node.remaining_anes),
                    node.source,
                    "[yellow]Already claimed[/yellow]" if node.claimed else "[green]Available[/green]",
                )
            table.add_row(
                "", "", "[bold]Key totals[/bold]", "",
                f"[bold]{format_nes(key_total)}[/bold]",
                f"[bold]{format_nes(key_remaining)}[/bold]", "", ""
            )
        console.print(table)
        console.print(
            Panel(
                f"[bold]Total allocation found: {format_nes(grand_total)} NES[/bold]\n"
                f"[bold]Total currently available: {format_nes(grand_remaining)} NES[/bold]",
                border_style="green",
            )
        )
        if any(entry.lookup_notes for entry in self.keys):
            console.print(
                Panel(
                    "For keys with no verified match, the result is inconclusive for "
                    "pre-deterministic installations. Their old Node IDs were generated "
                    "independently and cannot be reconstructed from the private key alone. "
                    "The historical api-test registry currently returns HTTP 504, so the "
                    "missing key-to-Node-ID mapping cannot presently be verified there.",
                    title="Important lookup limitation",
                    border_style="yellow",
                )
            )

    def _save_research_report(self) -> None:
        allocation_total = sum(
            node.total_anes for entry in self.keys for node in entry.nodes
        )
        total = sum(
            node.remaining_anes
            for entry in self.keys
            for node in entry.nodes
            if not node.claimed
        )
        report = {
            "generated_at": utc_now(),
            "private_keys_included": False,
            "unique_key_count": len(self.keys),
            "node_count": sum(len(entry.nodes) for entry in self.keys),
            "total_allocation_anes": str(allocation_total),
            "total_allocation_NES": format_nes(allocation_total),
            "remaining_anes": str(total),
            "remaining_NES": format_nes(total),
            "lookup_limitation": (
                "A zero public registry result does not prove that no historical miner "
                "existed. Older independently generated Node IDs require a surviving "
                "historical key-to-node mapping."
            ),
            "keys": [
                {
                    "key_number": entry.number,
                    "fingerprint": entry.fingerprint,
                    "compressed_public_key": entry.public_key,
                    "cosmos_address": entry.normal_identity.get("cosmos_address"),
                    "deterministic_node_id": entry.normal_identity.get("node_id"),
                    "lookup_notes": entry.lookup_notes,
                    "total_allocation_anes": str(
                        sum(node.total_anes for node in entry.nodes)
                    ),
                    "total_remaining_anes": str(
                        sum(node.remaining_anes for node in entry.nodes if not node.claimed)
                    ),
                    "nodes": [
                        {
                            "node_id": node.node_id,
                            "moniker": node.moniker,
                            "total_anes": str(node.total_anes),
                            "remaining_anes": str(node.remaining_anes),
                            "remaining_NES": format_nes(node.remaining_anes),
                            "claimed": node.claimed,
                            "source": node.source,
                            "claim_mode": node.claim_mode,
                            "cosmos_address": node.cosmos_address,
                        }
                        for node in entry.nodes
                    ],
                }
                for entry in self.keys
            ],
        }
        atomic_json_write(RESEARCH_PATH, report)
        console.print(f"[dim]Secret-free report saved to {RESEARCH_PATH}[/dim]")

    def add_evm_address(self) -> None:
        raw = Prompt.ask("Enter the destination EVM address")
        address = validate_evm_address(raw)
        console.print(Panel(address, title="Validated EIP-55 address", border_style="green"))
        if not Confirm.ask("Save this destination address?", default=False):
            console.print("[yellow]Address was not saved.[/yellow]")
            return
        self.evm_address = address
        atomic_json_write(CONFIG_PATH, {"evm_address": address, "saved_at": utc_now()})
        console.print("[bold green]Destination address saved.[/bold green]")

    def _terms_confirmed(self) -> bool:
        terms = (
            "By continuing, you certify that you are the lawful operator or an "
            "authorized representative of every node being claimed; claiming is "
            "permitted in your jurisdiction; you are not under United States "
            "jurisdiction; you understand transactions are irreversible; and you "
            "accept the risk of providing an incorrect destination address."
        )
        console.print(Panel(terms, title="Required claim confirmation", border_style="yellow"))
        return Confirm.ask("Do you accept and certify all statements above?", default=False)

    def claim_all(self) -> None:
        if not self.keys:
            raise CliError("Choose Option 2 and add private keys first.")
        if not self.research_confirmed:
            raise CliError("Run Option 3 and confirm the research results first.")
        if not self.evm_address:
            raise CliError("Choose Option 4 and configure an EVM destination first.")

        candidates = [
            (entry, node)
            for entry in self.keys
            for node in entry.nodes
            if not node.claimed and node.remaining_anes > 0
        ]
        total = sum(node.remaining_anes for _, node in candidates)
        if not candidates:
            console.print("[yellow]No currently available allocations were found.[/yellow]")
            return

        console.print(
            Panel(
                f"Node IDs: [bold]{len(candidates)}[/bold]\n"
                f"Maximum available: [bold]{format_nes(total)} NES[/bold]\n"
                f"Destination: [bold]{self.evm_address}[/bold]",
                title="Final claim summary",
                border_style="cyan",
            )
        )
        if not self._terms_confirmed():
            console.print("[yellow]Terms not accepted; no claims submitted.[/yellow]")
            return
        if not Confirm.ask("Submit all eligible claims now?", default=False):
            console.print("[yellow]Claim operation cancelled.[/yellow]")
            return

        successful = failed = skipped = ambiguous = 0
        successful_anes = 0
        for position, (entry, node) in enumerate(candidates, 1):
            console.rule(
                f"Claim {position}/{len(candidates)} · Key {entry.number} "
                f"[{entry.fingerprint}]"
            )
            console.print(f"  Node ID: [bold]{node.node_id}[/bold]")
            console.print(f"  Method:  {node.claim_mode} ({node.source})")
            try:
                live = self.client.allocation(node.node_id, node.cosmos_address)
                live_amount = remaining_allocation(live)
                console.print(f"  Reward:  [bold green]{format_nes(live_amount)} NES[/bold green]")
                if allocation_claimed(live) or live_amount <= 0:
                    console.print("  Status:  [yellow]Skipped — already claimed or zero remaining[/yellow]")
                    skipped += 1
                    self._record_claim(
                        entry, node, live_amount, "skipped", None,
                        "Already claimed or zero remaining allocation",
                    )
                    continue

                console.print("  Submit:  [cyan]Signing locally and submitting…[/cyan]")
                if node.claim_mode == "normal":
                    identity = derive_normal_identity(entry.secret)
                    if identity["node_id"] != node.node_id:
                        raise CliError(
                            "The live private key does not reproduce this normal-claim Node ID."
                        )
                    payload = build_normal_claim_payload(
                        entry.secret, identity, self.evm_address, live
                    )
                    endpoint = NORMAL_CLAIM_API
                elif node.claim_mode == "alternate":
                    payload = build_claim_payload(
                        entry.secret,
                        entry.public_key,
                        node.node_id,
                        self.evm_address,
                        str(live_amount),
                    )
                    endpoint = ALTERNATE_CLAIM_API
                else:
                    raise CliError(f"Unknown claim method: {node.claim_mode}")
                response = self.client.submit_claim(payload, node.node_id, endpoint)
                tx_hash = extract_tx_hash(response)
                if not tx_hash:
                    raise AmbiguousClaimError(
                        "Server accepted the request but returned no transaction hash."
                    )
                console.print(f"  Submit:  [green]Accepted[/green]")
                console.print(f"  TX hash: [link={EXPLORER_TX}{tx_hash}]{tx_hash}[/link]")
                console.print("  Verify:  [cyan]Waiting for on-chain receipt…[/cyan]")
                receipt = self.client.wait_for_receipt(tx_hash)
                if receipt.get("status") == "0x1":
                    console.print("  Verify:  [bold green]SUCCESS[/bold green]")
                    successful += 1
                    successful_anes += live_amount
                    node.claimed = True
                    node.remaining_anes = 0
                    self._record_claim(entry, node, live_amount, "success", tx_hash, None)
                else:
                    console.print("  Verify:  [bold red]FAILED RECEIPT[/bold red]")
                    failed += 1
                    self._record_claim(
                        entry, node, live_amount, "failed", tx_hash,
                        f"Receipt status: {receipt.get('status')}",
                    )
            except AmbiguousClaimError as exc:
                console.print(f"  Status:  [bold red]AMBIGUOUS — STOPPING[/bold red]\n  {exc}")
                ambiguous += 1
                self._record_claim(entry, node, node.remaining_anes, "ambiguous", None, str(exc))
                break
            except CliError as exc:
                console.print(f"  Status:  [red]ERROR[/red] — {exc}")
                failed += 1
                self._record_claim(entry, node, node.remaining_anes, "error", None, str(exc))
            if position < len(candidates):
                time.sleep(CLAIM_DELAY_SECONDS)

        self._claim_summary(successful, failed, skipped, ambiguous, successful_anes)

    def _record_claim(
        self,
        entry: KeyEntry,
        node: NodeReward,
        amount: int,
        status: str,
        tx_hash: str | None,
        error: str | None,
    ) -> None:
        self.claim_records[node.node_id] = {
            "node_id": node.node_id,
            "key_number": entry.number,
            "key_fingerprint": entry.fingerprint,
            "compressed_public_key": entry.public_key,
            "source": node.source,
            "claim_mode": node.claim_mode,
            "destination": self.evm_address,
            "amount_anes": str(amount),
            "amount_NES": format_nes(amount),
            "status": status,
            "tx_hash": tx_hash,
            "explorer_url": EXPLORER_TX + tx_hash if tx_hash else None,
            "error": error,
            "updated_at": utc_now(),
        }
        atomic_json_write(
            CLAIMS_PATH,
            {
                "private_keys_included": False,
                "destination": self.evm_address,
                "updated_at": utc_now(),
                "claims": sorted(
                    self.claim_records.values(),
                    key=lambda item: (item.get("key_number", 0), item.get("node_id", "")),
                ),
            },
        )

    def _claim_summary(
        self, successful: int, failed: int, skipped: int, ambiguous: int, amount: int
    ) -> None:
        table = Table(title="Claim Run Summary", box=box.ROUNDED)
        table.add_column("Result")
        table.add_column("Count", justify="right")
        table.add_row("Successful", f"[green]{successful}[/green]")
        table.add_row("Failed / errors", f"[red]{failed}[/red]")
        table.add_row("Skipped", f"[yellow]{skipped}[/yellow]")
        table.add_row("Ambiguous", f"[red]{ambiguous}[/red]")
        table.add_row("NES finalized this run", f"[bold]{format_nes(amount)}[/bold]")
        console.print(table)
        if self.evm_address:
            try:
                balance = int(self.client.rpc("eth_getBalance", [self.evm_address, "latest"]), 16)
                console.print(
                    f"Destination native balance: [bold]{format_nes(balance)} NES[/bold]"
                )
            except CliError as exc:
                console.print(f"[yellow]Could not query final destination balance: {exc}[/yellow]")
        console.print(f"[dim]Secret-free claim log: {CLAIMS_PATH}[/dim]")


def main() -> int:
    arguments = set(sys.argv[1:])
    if "--preflight" in arguments:
        unknown = arguments - {"--preflight", "--quiet"}
        if unknown:
            console.print(f"[red]Unknown preflight option(s): {', '.join(sorted(unknown))}[/red]")
            return 2
        try:
            result = runtime_preflight()
        except CliError as exc:
            if "--quiet" not in arguments:
                console.print(f"[bold red]Preflight failed:[/bold red] {exc}")
            return 1
        if "--quiet" not in arguments:
            console.print(
                f"[green]Preflight passed[/green] · Python {result['python']} · "
                f"RIPEMD160: {result['ripemd160']}"
            )
        return 0
    try:
        RewardsApp().run()
        return 0
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
        return 130
    except CliError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
