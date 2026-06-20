"""
Minimal .env loader — zero dependencies.

Reads KEY=VALUE lines from a .env file at the project root and sets them in
os.environ WITHOUT overwriting variables already present in the real
environment (real env wins). Lines starting with # and blank lines ignored.
Quotes around values are stripped. Silent + safe if the file is missing.
"""
from __future__ import annotations
import os


def load_env(path: str | None = None) -> int:
    if path is None:
        # project root = two levels up from this file (core/ -> root)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, ".env")
    if not os.path.isfile(path):
        return 0
    n = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
                    n += 1
    except OSError:
        return 0
    return n
