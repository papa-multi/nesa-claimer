#!/usr/bin/env python3
"""Fail when repository files resemble private credentials or key material."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
SKIP_PARTS = {".git", ".venv", "build", "dist", "__pycache__", ".pytest_cache"}
SENSITIVE_NAMES = [
    re.compile(r"^\.env(?:\..+)?$", re.I),
    re.compile(r".*\.env(?:\..+)?$", re.I),
    re.compile(r"private[-_]?keys?", re.I),
    re.compile(r"^keys?\.txt$", re.I),
    re.compile(r"wallet.*\.(?:json|txt|env)$", re.I),
    re.compile(r"(?:credentials?|secrets?|tokens?)(?:[-_.].*)?$", re.I),
    re.compile(r"^(?:node_id|peer_id)\.id$", re.I),
    re.compile(r"(?:mnemonic|seed[-_]?phrase)", re.I),
]
CONTENT_PATTERNS = {
    "Hugging Face token": re.compile(r"hf_[A-Za-z0-9]{20,}"),
    "64-hex secret candidate": re.compile(
        r"(?<![0-9A-Fa-f])(?:0x)?[0-9A-Fa-f]{64}(?![0-9A-Fa-f])"
    ),
    "private-key assignment": re.compile(
        r"(?:NODE_PRIV_KEY|PRIVATE_KEY|SECRET_KEY|SEED_PHRASE)\s*=\s*[^\s<]+",
        re.I,
    ),
    "PEM private key": re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    "extended private key": re.compile(r"\b(?:xprv|yprv|zprv)[1-9A-HJ-NP-Za-km-z]{40,}"),
    "GitHub token": re.compile(r"\bgh(?:p|o|u|s|r)_[A-Za-z0-9]{30,}\b"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "OpenAI-style token": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
}


def candidates():
    for path in ROOT.rglob("*"):
        if path.resolve() == SELF or not path.is_file():
            continue
        if any(part in SKIP_PARTS for part in path.relative_to(ROOT).parts):
            continue
        yield path


def main() -> int:
    problems: list[str] = []
    for path in candidates():
        relative = path.relative_to(ROOT)
        if any(pattern.search(path.name) for pattern in SENSITIVE_NAMES):
            problems.append(f"sensitive filename: {relative}")
        try:
            if path.stat().st_size > 5_000_000:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for label, pattern in CONTENT_PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                problems.append(f"{label}: {relative}:{line}")

    if problems:
        print("Potential secrets detected:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("Secret scan passed: no credential or private-key patterns detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
