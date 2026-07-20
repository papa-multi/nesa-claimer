# Nesa Claimer

Nesa Claimer is a security-focused, menu-driven command-line tool for researching
and claiming historical Nesa miner rewards across multiple signing keys. It can
discover multiple Node IDs linked to one key, check each allocation, and submit
eligible claims to a validated EVM destination.

> [!CAUTION]
> Claim transactions are irreversible. Review every discovered Node ID, reward
> amount, claim method, and destination address before approving a submission.
> This community project is not affiliated with or endorsed by Nesa.

## What the tool does

Nesa Claimer supports the two identity paths used by Nesa's official reward
tools:

- **Historical/alternate identity:** searches known public dashboard registries
  for Node IDs associated with the compressed secp256k1 public key, then uses
  Nesa's alternate-key claim endpoint.
- **Deterministic identity:** reproduces the identity algorithm used by the
  official normal claim script and includes that Node ID only when Nesa's reward
  service verifies an allocation for it.

For every verified Node ID, the application displays the original allocation,
remaining claimable amount, claim status, discovery source, and required claim
method. One signing key may be associated with multiple historical Node IDs;
each ID is checked and, when eligible, claimed separately.

## Important historical limitation

Older Nesa installations could generate the miner Node ID independently from
the secp256k1 request-signing key. In that situation, the private key cannot
mathematically reconstruct the old Node ID. Discovery depends on a surviving
historical public key-to-Node-ID registry record.

If a legacy registry is unavailable or times out, Nesa Claimer reports the
lookup as **inconclusive**. It does not claim that the key never operated a
miner. A newly derived Node ID is never presented as a historical match unless
the reward service verifies an allocation for it.

## Security model

Private keys are entered one at a time through a **hidden terminal prompt**:

- typed characters are not displayed on screen;
- keys are never printed in tables, logs, errors, or reports;
- keys are not accepted through command-line arguments;
- keys are not written to `.env` files, key lists, configuration, or state;
- keys remain only in the running Python process and are cleared on normal exit;
- reports identify keys using a short public-key fingerprint, never secret data;
- signatures are produced locally and only signed claim payloads are submitted.

Memory clearing in Python is best effort. The interpreter or operating system
may retain temporary copies in memory or swap. Run the tool only on a trusted,
fully patched computer with no untrusted monitoring, terminal recording, or
malware.

The application does not require a Hugging Face token, seed phrase, mnemonic,
wallet export, or Nesa account password.

## Requirements

- Bash
- Python 3.10 or newer
- Internet access to the public Nesa services
- `sudo` or root access only when system packages must be installed
- A terminal that supports hidden password-style input

The installer supports Debian/Ubuntu, Fedora/RHEL, Arch Linux, Alpine Linux,
macOS with Homebrew, and compatible WSL environments.

## Install and run

Clone your published repository and launch the bootstrap menu:

```bash
git clone https://github.com/YOUR_USERNAME/nesa-claimer.git
cd nesa-claimer
chmod 700 nesa-claimer install.sh
./nesa-claimer
```

On a fresh system, select **Option 1: Install all prerequisites**. The installer:

1. detects the available package manager;
2. installs Python, virtual-environment support, CA certificates, and Git when
   required;
3. creates an isolated `.venv` inside the project directory;
4. installs pinned Python dependencies; and
5. verifies the installed application and cryptographic libraries.

The full menu opens automatically after a successful installation. For later
runs, use:

```bash
cd nesa-claimer
./nesa-claimer
```

## Main menu

```text
1  Install all prerequisites
2  Securely add private keys
3  Research Node IDs and rewards
4  Add or change EVM destination address
5  Claim all available rewards
0  Exit
```

### Option 1 — Install all prerequisites

Installs and verifies the system and Python requirements. Re-running this option
is safe when the isolated environment already exists.

### Option 2 — Securely add private keys

Enter the number of keys, then enter each 32-byte secp256k1 private key through
the hidden prompt. Both plain 64-character hexadecimal values and `0x`-prefixed
values are accepted.

The prompt displays only validation progress and a public fingerprint. It never
echoes the private key. Duplicate keys are rejected. Keys are held in memory
only for the current application session, so they must be entered again after
exiting or restarting the program.

### Option 3 — Research Node IDs and rewards

For each in-memory key, the application:

1. derives the compressed public key and Nesa address locally;
2. searches and paginates all configured public dashboard registries;
3. collects every exact public-key-to-Node-ID match;
4. checks the official deterministic Node ID against the allocation service;
5. deduplicates Node IDs and rejects contradictory key mappings;
6. retrieves original and remaining reward amounts;
7. labels already-claimed and currently available allocations; and
8. asks the user to review and confirm the result.

Claiming remains disabled until the current research results are explicitly
confirmed. A secret-free report is written with owner-only permissions to:

```text
~/.local/state/nesa-claimer/research-report.json
```

The report can contain public information such as compressed public keys, Nesa
addresses, Node IDs, allocation amounts, and lookup diagnostics. It never
contains private keys.

### Option 4 — Add or change the EVM destination

Enter the EVM address that should receive successful claims. The application
validates the address and verifies mixed-case EIP-55 checksums before displaying
it for confirmation.

Only the public destination address is saved, with owner-only permissions, to:

```text
~/.local/state/nesa-claimer/config.json
```

Confirm that you control the address. A valid address cannot be recovered or
changed after a blockchain transaction has already sent funds to it.

### Option 5 — Claim all available rewards

Before submitting anything, this option requires:

- private keys loaded during the current session;
- confirmed research results;
- a validated EVM destination;
- acceptance of the displayed claim terms; and
- final confirmation of the claim batch.

Each allocation is refreshed immediately before signing. The terminal shows:

- key entry number and public fingerprint;
- Node ID, evidence source, and claim method;
- live reward amount;
- signing and submission status;
- returned transaction hash and explorer link;
- receipt verification status;
- errors, skipped claims, and ambiguous responses; and
- final successful, failed, skipped, and ambiguous totals.

Claims run sequentially with a delay between requests. Known pre-submission
service failures may be retried. If a network response is ambiguous, the tool
stops rather than risk blindly resubmitting a transaction.

Secret-free claim results are stored outside the repository at:

```text
~/.local/state/nesa-claimer/claim-results.json
```

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

Public keys, Node IDs, addresses, allocation queries, signatures, nonces,
timestamps, and signed claim data may be sent to these services as required by
the selected operation. Private keys are never transmitted.

The claim payload implementations were checked against Nesa's official
[`miner-rewards-cli`](https://github.com/nesaorg/miner-rewards-cli/tree/alternate-key-cli)
`alternate-key-cli` branch at commit
`b204312dd53104df9680f08438c15e25177c0dc8`. Re-audit against upstream whenever
Nesa changes its claim process or endpoints.



## Development

```bash
./install.sh
source .venv/bin/activate
python -m pip install '.[dev]'
python scripts/scan-secrets.py .
pytest -q
python -m build
```

Automated tests and the secret scanner also run through GitHub Actions on pushes
and pull requests for supported Python versions.

## Troubleshooting

### “No verified reward match”

This means no allocation-backed identity was found through the services that
responded. It does not prove an older miner never existed. Read the accompanying
registry diagnostics, especially for legacy service timeouts.

### “Already claimed” with zero remaining

The original allocation and remaining amount are separate values. An allocation
can show a positive original total while its remaining amount is zero because it
has already been claimed.

### The legacy registry returns HTTP 504

The old mapping service is unavailable or too slow. The application records the
lookup as inconclusive and continues with reachable registries and deterministic
allocation verification.

### Keys disappeared after exiting

This is intentional. Private keys are never persisted and must be re-entered
through Option 2 for each application session.

## Responsible use and disclaimer

Use this tool only for miners and signing keys you lawfully own or are authorized
to operate. Follow Nesa's terms and all laws applicable in your jurisdiction.
This software is provided without warranty and is not financial, legal, tax, or
security advice. The maintainers cannot reverse claims, recover funds sent to an
incorrect destination, restore unavailable registry data, or guarantee service
availability.

See [SECURITY.md](SECURITY.md) for responsible vulnerability reporting.
