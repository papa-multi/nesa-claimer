# Security policy

Do not include private keys, seed phrases, Hugging Face tokens, or unredacted
environment files in a security report, issue, pull request, screenshot, or log.

If you believe the tool can expose a private key, sign an unintended payload,
submit a duplicate claim, or send rewards to the wrong address, do not run the
claim workflow. Report the issue privately to the repository maintainer and
include only a minimal reproduction using disposable test keys.

This project intentionally keeps private keys in process memory only. Public
state files contain compressed public keys, Node IDs, destination addresses,
allocations, transaction hashes, and errors, but never private keys.

Public state is stored outside the repository under
`~/.local/state/nesa-claimer/` by default. Set `NESA_CLAIMER_STATE_DIR` to
override that location.
