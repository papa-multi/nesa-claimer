#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
BOOTSTRAP_DIR="$PROJECT_DIR/.bootstrap"
UV_BIN="$BOOTSTRAP_DIR/bin/uv"
MANAGED_PYTHON_DIR="$BOOTSTRAP_DIR/python"
UV_VERSION="0.11.29"

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
fail() {
  trap - ERR
  printf '%b\n' "${RED}Installation failed:${RESET} $*" >&2
  exit 1
}
on_error() {
  local status=$?
  local line=${BASH_LINENO[0]:-unknown}
  trap - ERR
  printf '%b\n' "${RED}Installation failed:${RESET} command exited with status ${status} near line ${line}." >&2
  printf '%s\n' "Review the output above, correct the reported system or network error, and run Option 1 again." >&2
  exit "$status"
}
trap on_error ERR

OS_NAME="$(uname -s)"
OS_VERSION="unknown"
if [[ -r /etc/os-release ]]; then
  # Distribution-maintained data containing NAME, VERSION_ID and PRETTY_NAME.
  # shellcheck disable=SC1091
  source /etc/os-release
  OS_NAME="${PRETTY_NAME:-${NAME:-$OS_NAME}}"
  OS_VERSION="${VERSION_ID:-unknown}"
elif command -v sw_vers >/dev/null 2>&1; then
  OS_NAME="macOS $(sw_vers -productVersion)"
  OS_VERSION="$(sw_vers -productVersion)"
fi
info "Detected operating system: $OS_NAME"
info "Detected architecture: $(uname -m)"

run_privileged() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    fail "Root access or sudo is required to install missing system packages."
  fi
}

install_system_packages() {
  if command -v apt-get >/dev/null 2>&1; then
    info "Installing required Ubuntu/Debian system packages"
    run_privileged apt-get update
    if [[ "${ID:-}" == "ubuntu" ]] &&
       ! apt-cache policy python3-venv | grep -Eq 'Candidate: [^()]'; then
      info "Enabling Ubuntu's official universe repository for pip and venv"
      run_privileged env DEBIAN_FRONTEND=noninteractive apt-get install -y \
        software-properties-common ca-certificates
      run_privileged add-apt-repository -y universe
      run_privileged apt-get update
    fi
    run_privileged env DEBIAN_FRONTEND=noninteractive apt-get install -y \
      python3 python3-venv python3-pip python3-dev \
      build-essential pkg-config libffi-dev libssl-dev \
      ca-certificates curl git
  elif command -v dnf >/dev/null 2>&1; then
    info "Installing required Fedora/RHEL system packages"
    run_privileged dnf install -y \
      python3 python3-pip python3-devel gcc gcc-c++ make pkgconf-pkg-config \
      rust cargo libffi-devel openssl-devel ca-certificates curl git
  elif command -v yum >/dev/null 2>&1; then
    info "Installing required RHEL-compatible system packages"
    run_privileged yum install -y \
      python3 python3-pip python3-devel gcc gcc-c++ make pkgconfig \
      rust cargo libffi-devel openssl-devel ca-certificates curl git
  elif command -v pacman >/dev/null 2>&1; then
    info "Installing required Arch Linux system packages"
    run_privileged pacman -Sy --needed --noconfirm \
      python python-pip base-devel pkgconf rust libffi openssl ca-certificates curl git
  elif command -v apk >/dev/null 2>&1; then
    info "Installing required Alpine Linux system packages"
    run_privileged apk add --no-cache \
      python3 py3-pip py3-virtualenv python3-dev build-base pkgconf cargo \
      libffi-dev openssl-dev ca-certificates curl git
  elif command -v brew >/dev/null 2>&1; then
    info "Installing required macOS packages with Homebrew"
    brew install python git curl ca-certificates pkg-config libffi openssl@3 rust
  elif command -v python3 >/dev/null 2>&1; then
    warn "No supported package manager was found; validating the existing Python installation."
  else
    fail "No supported package manager or Python 3 installation was found."
  fi
}

if [[ "${NESA_CLAIMER_SKIP_SYSTEM_PACKAGES:-0}" == "1" ]]; then
  warn "Skipping system package installation by explicit environment override."
else
  install_system_packages
fi

find_compatible_python() {
  local candidate
  for candidate in \
    python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" - <<'PY_VERSION' >/dev/null 2>&1
import sys
raise SystemExit(0 if (3, 10) <= sys.version_info < (3, 13) else 1)
PY_VERSION
    then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

bootstrap_managed_python() {
  local installer
  local installer_url="https://astral.sh/uv/${UV_VERSION}/install.sh"

  command -v curl >/dev/null 2>&1 || \
    fail "curl is required to install a compatible Python runtime."
  mkdir -p "$BOOTSTRAP_DIR/bin" "$MANAGED_PYTHON_DIR" "$BOOTSTRAP_DIR/cache"
  chmod 700 "$BOOTSTRAP_DIR" "$BOOTSTRAP_DIR/bin" \
    "$MANAGED_PYTHON_DIR" "$BOOTSTRAP_DIR/cache"

  if [[ ! -x "$UV_BIN" ]] ||
     [[ "$("$UV_BIN" --version 2>/dev/null || true)" != "uv $UV_VERSION"* ]]; then
    info "Installing the pinned uv bootstrap tool ($UV_VERSION)"
    installer="$(mktemp "${TMPDIR:-/tmp}/nesa-uv-installer.XXXXXX")"
    if ! curl --proto '=https' --tlsv1.2 --fail --silent --show-error \
      --location --retry 5 --retry-delay 2 --connect-timeout 20 \
      "$installer_url" --output "$installer"; then
      rm -f -- "$installer"
      fail "Could not securely download the pinned Python bootstrap installer."
    fi
    chmod 700 "$installer"
    if ! env UV_UNMANAGED_INSTALL="$BOOTSTRAP_DIR/bin" \
      UV_NO_MODIFY_PATH=1 sh "$installer"; then
      rm -f -- "$installer"
      fail "The pinned Python bootstrap installer failed."
    fi
    rm -f -- "$installer"
  fi

  [[ -x "$UV_BIN" ]] || fail "The managed Python bootstrap tool was not installed."
  export UV_PYTHON_INSTALL_DIR="$MANAGED_PYTHON_DIR"
  export UV_CACHE_DIR="$BOOTSTRAP_DIR/cache"
  export UV_NO_MODIFY_PATH=1
  export UV_NO_PROGRESS=1
  info "Installing a project-managed Python 3.12 runtime"
  "$UV_BIN" python install 3.12 --managed-python --no-bin || \
    fail "Could not install the managed Python 3.12 runtime."
  PYTHON="$("$UV_BIN" python find 3.12 --managed-python)" || \
    fail "Managed Python 3.12 was installed but could not be located."
}

PYTHON=''
PYTHON_PROVIDER='system'
if PYTHON="$(find_compatible_python)"; then
  :
elif [[ "${NESA_CLAIMER_SKIP_SYSTEM_PACKAGES:-0}" == "1" ]]; then
  detected="$(python3 --version 2>&1 || python --version 2>&1 || printf 'not installed')"
  fail "Python 3.10 through 3.12 is required when system-package installation is skipped. Detected: $detected"
else
  detected="$(python3 --version 2>&1 || python --version 2>&1 || printf 'not installed')"
  warn "No compatible system Python was found (detected: $detected)."
  bootstrap_managed_python
  PYTHON_PROVIDER='project-managed'
fi

if [[ ! -x "$PYTHON" ]] || ! "$PYTHON" - <<'PY_VERSION' >/dev/null 2>&1
import sys
raise SystemExit(0 if (3, 10) <= sys.version_info < (3, 13) else 1)
PY_VERSION
then
  fail "A working Python 3.10 through 3.12 runtime could not be installed."
fi

PYTHON_VERSION="$($PYTHON -c 'import platform; print(platform.python_version())')"
OPENSSL_VERSION="$($PYTHON -c 'import ssl; print(ssl.OPENSSL_VERSION)')"
success "Python $PYTHON_VERSION detected at $PYTHON"
info "Python provider: $PYTHON_PROVIDER"
info "Python SSL backend: $OPENSSL_VERSION"

if "$PYTHON" - <<'PY_RIPEMD_SYSTEM'
import hashlib
expected = "9c1185a5c5e9fc54612808977ee8f548b2258d31"
try:
    actual = hashlib.new("ripemd160", b"").hexdigest()
except (TypeError, ValueError):
    raise SystemExit(1)
raise SystemExit(0 if actual == expected else 1)
PY_RIPEMD_SYSTEM
then
  success "System RIPEMD160 support passed its cryptographic test vector"
else
  warn "System RIPEMD160 is unavailable; the pinned PyCryptodome fallback will be used."
fi

info "Creating or repairing the isolated virtual environment"
if [[ -d "$VENV_DIR" ]]; then
  if [[ ! -x "$VENV_DIR/bin/python" ]] || ! "$VENV_DIR/bin/python" - <<'PY_VENV' >/dev/null 2>&1
import sys
raise SystemExit(0 if (3, 10) <= sys.version_info < (3, 13) else 1)
PY_VENV
  then
    warn "The existing virtual environment is incomplete or incompatible; rebuilding it."
    "$PYTHON" -m venv --clear "$VENV_DIR" || \
      fail "Could not rebuild .venv. Verify that the OS Python venv package is installed."
  else
    "$PYTHON" -m venv --upgrade "$VENV_DIR" || \
      fail "Could not refresh the existing .venv. Remove only .venv and run Option 1 again."
  fi
else
  "$PYTHON" -m venv "$VENV_DIR" || \
    fail "Could not create .venv. Verify that the OS Python venv package is installed."
fi

# Activation affects this installer process. The project launcher independently
# selects .venv/bin/python on every later run, so users never activate it manually.
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
export PYTHONNOUSERSITE=1
export PIP_DISABLE_PIP_VERSION_CHECK=1

if [[ "${VIRTUAL_ENV:-}" != "$VENV_DIR" ]]; then
  fail "The project virtual environment could not be activated."
fi
if [[ "$(python -c 'import sys; print(sys.prefix)')" != "$VENV_DIR" ]]; then
  fail "Python is not running from the project virtual environment."
fi
success "Virtual environment active: $VENV_DIR"

info "Upgrading pip, setuptools and wheel inside .venv"
python -m pip install --upgrade pip setuptools wheel || \
  fail "Could not upgrade Python package tooling. Check network and certificate access."

if [[ ! -f "$PROJECT_DIR/requirements.txt" || ! -f "$PROJECT_DIR/pyproject.toml" ]]; then
  fail "Project dependency files are missing. Restore the repository and retry."
fi

info "Installing all pinned Python dependencies inside .venv"
python -m pip install --upgrade --requirement "$PROJECT_DIR/requirements.txt" || \
  fail "Could not install pinned dependencies. Check the pip error above and retry."
python -m pip install --no-deps --no-build-isolation --editable "$PROJECT_DIR" || \
  fail "Could not install the Nesa Claimer application inside .venv."
python -m pip check || fail "Installed Python dependencies are inconsistent."

info "Running dependency, cryptographic and application startup preflight"
python "$PROJECT_DIR/nesa_claimer.py" --preflight || \
  fail "Application preflight failed inside .venv."

chmod 700 "$PROJECT_DIR/nesa-claimer" "$PROJECT_DIR/install.sh"
if ! "$PROJECT_DIR/nesa-claimer" --preflight --quiet; then
  fail "The project launcher could not start the verified application environment."
fi

success "Installation completed successfully"
printf '%s\n' "  Operating system: $OS_NAME"
printf '%s\n' "  Python:           $PYTHON_VERSION"
printf '%s\n' "  Python provider:  $PYTHON_PROVIDER"
printf '%s\n' "  Virtual env:      $VENV_DIR"
printf '%s\n' "  Dependencies:     verified"
printf '%s\n' "  Cryptography:     verified"
printf '\nRun the tool with:\n  %s/nesa-claimer\n' "$PROJECT_DIR"
