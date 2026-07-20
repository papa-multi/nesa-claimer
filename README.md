# Nesa Claimer

Nesa Claimer is a simple terminal tool for researching and claiming Nesa miner
rewards across multiple private keys and historical Node IDs.

> Claims are irreversible. Review every Node ID, reward amount, and destination
> address before confirming a claim.

## Features

- Add any number of private keys securely.
- Find multiple Node IDs associated with each key.
- Display original and remaining rewards.
- Detect already-claimed allocations.
- Validate the destination EVM address.
- Claim all available rewards with live status and transaction logs.
- Save only secret-free reports outside the project directory.

## Install

```bash
git clone https://github.com/papa-multi/nesa-claimer.git
cd nesa-claimer
chmod 700 nesa-claimer install.sh
./nesa-claimer
```

On the first run, choose **Option 1**. It detects the operating system, installs
the required system packages, and creates an isolated `.venv` automatically.
When installation finishes, the main menu opens automatically.

Option 1 also verifies RIPEMD160 support. If the system implementation is
unavailable, the compatible fallback is installed and tested automatically.
The launcher always uses the project virtual environment; manual activation is
not required.

Ubuntu 20.04, 22.04, and 24.04 are supported. If an older Ubuntu release only
provides Python 3.8 or 3.9, Option 1 automatically installs an isolated,
project-managed Python 3.12 runtime without replacing the system Python.

For later runs:

```bash
cd nesa-claimer
./nesa-claimer
```

## Menu

```text
1  Install all prerequisites
2  Securely add private keys
3  Research Node IDs and rewards
4  Add or change EVM destination address
5  Claim all available rewards
0  Exit
```

### 1. Install all prerequisites

Installs Python and the required packages in an isolated project environment.

### 2. Securely add private keys

Enter how many private keys you have, then enter them one at a time.

**Private-key input uses a hidden prompt. Typed keys are never displayed on the
screen.** Keys are kept in memory only for the current session and are not saved
to files, reports, logs, or configuration.

### 3. Research Node IDs and rewards

Searches available Nesa services for every Node ID associated with each key. It
displays the reward for each Node ID, totals for each key, and the total across
all keys.

Some older Node IDs were generated separately from the private key. If an old
registry is unavailable, the tool reports the result as inconclusive instead of
saying that the miner never existed.

Review the results and confirm them before claiming.

### 4. Add an EVM destination

Enter the EVM address that should receive successful claims. The address is
validated and shown for confirmation before it is saved.

### 5. Claim all available rewards

Refreshes every allocation, signs eligible claims locally, and submits them one
at a time. The terminal displays the current key number, Node ID, reward amount,
status, transaction hash, verification result, errors, and final summary.

The tool asks for confirmation before submitting any claim.

## Security

- Never paste private keys into chats, shell commands, GitHub issues, or files.
- Private-key entry is hidden and never echoed to the terminal.
- Private keys are never uploaded or written to disk by the tool.
- No Hugging Face token, seed phrase, or wallet export is required.
- Runtime reports are stored outside the repository under
  `~/.local/state/nesa-claimer/` with owner-only permissions.
- Memory clearing is best effort; use a trusted and secure computer.
- Always verify the destination address before claiming.

## Public services used

| Purpose | Endpoint |
|---|---|
| Legacy public-key registry | `https://api-test.nesa.ai` |
| Development public-key registry | `https://api-dev.nesa.ai` |
| Current public-key registry | `https://api.nesa.ai` |
| Reward allocations | `https://rewards-proxy.nesa.ai/api/allocation` |
| Alternate claim submission | `https://rewards-proxy.nesa.ai/api/claim-alternate` |
| Deterministic claim submission | `https://rewards-proxy.nesa.ai/api/claim` |
| Nesa EVM JSON-RPC | `https://erpc.nesa.ai` |
| EVM transaction explorer | `https://explorer-evm.nesa.ai` |

The tool may send public keys, Node IDs, public addresses, allocation queries,
signatures, nonces, timestamps, and signed claim data to these services. Private
keys are never transmitted.

Claim payloads were checked against Nesa's official
[`miner-rewards-cli`](https://github.com/nesaorg/miner-rewards-cli/tree/alternate-key-cli)
claim tools.

## Disclaimer

Use this tool only for miners and keys you own or are authorized to operate.
This community project is not affiliated with Nesa and is provided without
warranty. The maintainers cannot reverse claims or recover funds sent to an
incorrect address.
