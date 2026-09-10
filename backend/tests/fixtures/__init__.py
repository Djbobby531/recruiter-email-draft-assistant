"""
Regression fixture loader. Every real-world edge case used across the test
suite lives under a subdirectory here as a plain text/JSON file, so it can be
inspected, diffed, and extended independently of test code. Per project
policy: a bug found later gets a new fixture here + a regression test that
must never be deleted, even after the underlying bug is fixed.
"""
from __future__ import annotations

import json
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent


def load_text(subdir: str, filename: str) -> str:
    return (FIXTURES_DIR / subdir / filename).read_text(encoding="utf-8")


def load_json(subdir: str, filename: str) -> dict:
    return json.loads((FIXTURES_DIR / subdir / filename).read_text(encoding="utf-8"))
