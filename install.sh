#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"

if [[ -t 1 ]]; then
  BOLD='\033[1m'
  GREEN='\033[32m'
  YELLOW='\033[33m'
  RED='\033[31m'
  RESET='\033[0m'
else
  BOLD=''
  GREEN=''
  YELLOW=''
  RED=''
  RESET=''
fi

info() { printf '%b\n' "${BOLD}==>${RESET} $*"; }
success() { printf '%b\n' "${GREEN}✓${RESET} $*"; }
warn() { printf '%b\n' "${YELLOW}!${RESET} $*"; }
fail() { printf '%b\n' "${RED}Error:${RESET} $*" >&2; exit 1; }

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  SUDO=()
elif command -v sudo >/dev/null 2>&1; then
  SUDO=(sudo)
else
  fail "Root access or sudo is required to install missing system packages."
fi

install_system_packages() {
  if command -v apt-get >/dev/null 2>&1; then
    info "Installing system packages with apt"
    "${SUDO[@]}" apt-get update
    "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y \
      python3 python3-venv python3-pip ca-certificates git
  elif command -v dnf >/dev/null 2>&1; then
    info "Installing system packages with dnf"
    "${SUDO[@]}" dnf install -y python3 python3-pip ca-certificates git
  elif command -v yum >/dev/null 2>&1; then
    info "Installing system packages with yum"
    "${SUDO[@]}" yum install -y python3 python3-pip ca-certificates git
  elif command -v pacman >/dev/null 2>&1; then
    info "Installing system packages with pacman"
    "${SUDO[@]}" pacman -Sy --needed --noconfirm python python-pip ca-certificates git
  elif command -v apk >/dev/null 2>&1; then
    info "Installing system packages with apk"
    "${SUDO[@]}" apk add --no-cache python3 py3-pip py3-virtualenv ca-certificates git
  elif command -v brew >/dev/null 2>&1; then
    info "Installing system packages with Homebrew"
    brew install python git ca-certificates
  elif command -v python3 >/dev/null 2>&1; then
    warn "No supported package manager found; using the existing Python installation."
  else
    fail "No supported package manager or Python 3 installation was found."
  fi
}

if [[ "${NESA_CLAIMER_SKIP_SYSTEM_PACKAGES:-0}" == "1" ]]; then
  warn "Skipping system package installation by explicit environment override."
else
  install_system_packages
fi

PYTHON=''
if command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON="$(command -v python)"
else
  fail "Python was not available after package installation."
fi

if ! "$PYTHON" - <<'PY_CHECK'
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY_CHECK
then
  fail "Python 3.10 or newer is required."
fi
success "Python $($PYTHON -c 'import platform; print(platform.python_version())')"

if "$PYTHON" - <<'PY_RIPEMD_SYSTEM'
import hashlib
expected = "9c1185a5c5e9fc54612808977ee8f548b2258d31"
raise SystemExit(0 if hashlib.new("ripemd160", b"").hexdigest() == expected else 1)
PY_RIPEMD_SYSTEM
then
  success "System RIPEMD160 support detected"
else
  warn "System RIPEMD160 is unavailable; the verified PyCryptodome fallback will be installed."
fi

info "Creating isolated virtual environment"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  "$PYTHON" -m venv "$VENV_DIR" || fail \
    "Could not create the virtual environment. Install your OS python3-venv package."
fi

info "Installing Python dependencies"
"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel || \
  fail "Could not update Python package tooling. Check network access and retry."
"$VENV_DIR/bin/python" -m pip install --upgrade -e "$PROJECT_DIR" || \
  fail "Could not install application dependencies, including the RIPEMD160 fallback."

info "Verifying installation"
if ! "$VENV_DIR/bin/python" - <<'PY_VERIFY'
import ecdsa
import base58
import requests
import rich
import bech32
import coincurve
import cryptography
from Crypto.Hash import RIPEMD160
from eth_utils import to_checksum_address
import nesa_claimer

assert nesa_claimer.VERSION
assert to_checksum_address("0x0000000000000000000000000000000000000000")
assert RIPEMD160.new(data=b"").hexdigest() == "9c1185a5c5e9fc54612808977ee8f548b2258d31"
backend = nesa_claimer.ripemd160_backend()
assert nesa_claimer.ripemd160(b"").hex() == "9c1185a5c5e9fc54612808977ee8f548b2258d31"
print(f"RIPEMD160 backend verified: {backend}")
PY_VERIFY
then
  fail "Installation verification failed. RIPEMD160 support is not usable in the project environment."
fi

chmod 700 "$PROJECT_DIR/nesa-claimer" "$PROJECT_DIR/install.sh"
success "All prerequisites are installed and verified."
printf '\nRun the tool with:\n  %s/nesa-claimer\n' "$PROJECT_DIR"
