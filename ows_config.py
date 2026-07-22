#!/usr/bin/env python3
"""
Configuration loader for OWS Bot.

All tunable values (technicians, recall lookups, approval codes, error lists,
rental rates, timeouts, …) live in config.toml so nothing needs a code edit.

Precedence (lowest → highest):
  1. Built-in defaults below (mirror the original hardcoded values, so the
     bot still runs if config.toml is missing)
  2. config.toml         — committed, shared team config
  3. config.local.toml   — gitignored, personal overrides (e.g. your STARS ID)
  4. Environment / .env  — STARS_ID, CDP_URL, CLAUDE_MODEL
"""
from __future__ import annotations

import copy
import os
import sys
import tomllib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILES = ("config.toml", "config.local.toml")

DEFAULTS: dict = {
    "technicians": {
        "default": "",
        "list": [],
    },
    "connection": {
        "cdp_url": "http://127.0.0.1:9222",
    },
    "timeouts": {
        "default_ms": 30_000,
        "short_ms": 5_000,
        "poll_interval_s": 2.0,
        "poll_timeout_s": 30,
    },
    "logging": {
        "base_dir": "logs",
    },
    "claude": {
        "model": "claude-haiku-4-5-20251001",
    },
    "ai": {
        "provider": "anthropic",
        "model": "",
        "base_url": "",
    },
    "approval_codes": {
        "SFRU473": "DDDO",
        "ODM0001": "DDR4",
        "ODM0002": "DDR4",
        "RRP0001": "DDR1",
        "BES0027": "DDET",
    },
    "subcodes": {
        "cc_82": ["PRENT", "QCM", "P11"],
        "test_drive_ops": ["7001D1", "1006DXQ"],
        "sfru448_subcode": "LTIS",
    },
    "rental": {
        "default_daily_rate": 45,
        "premium_daily_rate": 60,
        "premium_models": [
            "F-150", "F-250", "F-350", "F-450", "F-550", "F-600",
            "F150", "F250", "F350", "F450", "F550", "F600", "TRANSIT",
        ],
        "prent_max_days": 10,
    },
    "recalls": {
        "26P02": {"cc": "04", "ccc": "G07", "causal_part": "14B291", "causal_qty": "0"},
        "25P21": {"cc": "42", "ccc": "C05", "causal_part": "19703", "causal_qty": "0"},
        "25B06": {"cc": "X9", "ccc": "S40", "causal_part": "14B321", "causal_qty": "0"},
        "24N08": {"cc": "28", "ccc": "S26", "causal_part": "7861203", "causal_qty": "0"},
        "22N17": {"cc": "91", "ccc": "G07", "causal_part": "14529", "causal_qty": "0"},
        "25P35": {"cc": "41", "ccc": "G07", "causal_part": "1621597", "causal_qty": "0"},
        "24U14": {"cc": "04", "ccc": "A07", "causal_part": "18D890", "causal_qty": "0"},
        "25B61": {"cc": "39", "ccc": "P50", "causal_part": "7S004", "causal_qty": "0"},
    },
    "user_review": {
        "errors": [
            "RDC0002", "FSA0001", "FSA0003", "FSA0022", "ROV0039", "MCA0004",
            "TOT0001", "RVC0011", "SUB0001", "SUB0003", "SUB0021", "SCCK112",
            "SCCK109", "SCCK075", "SCCK069", "SCCK062", "SCCK055", "SCCK050",
            "SCCK046", "SCCK012", "SCCK602", "BES0206", "BES0254", "SFRNG81",
            "PAC1023", "LAB0003", "ATT0001", "SUB0005", "SDCU903", "SFRU462",
            "MIS0005", "SFRU330", "SFRUC13", "EFC16335", "SUB0013", "ROV0068",
            "SFRUD96", "ROV0024", "SCCK005", "LAB0005", "SFRU364", "BOM0002",
            "SFRNG46", "RVC0013", "SFRU733", "TV0001", "MIS0002", "SFPNK20",
        ],
        "notes": {
            "ATT0001": "Requires document attachment",
        },
    },
    "scrape": {
        "false_positive_codes": ["MHT7000", "CPR0126", "REH52"],
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base (override wins). Mutates base."""
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    for name in CONFIG_FILES:
        path = os.path.join(SCRIPT_DIR, name)
        if not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as f:
                _deep_merge(cfg, tomllib.load(f))
        except tomllib.TOMLDecodeError as e:
            print(f"[OWS][CONFIG] FATAL: {name} is not valid TOML: {e}", flush=True)
            sys.exit(2)
    return cfg


CONFIG: dict = load_config()


# ── Accessors ────────────────────────────────────────────────────────────────

def get_technician(key: str | None = None) -> dict | None:
    """Return the technician dict for `key`, or the default technician."""
    techs = CONFIG["technicians"].get("list") or []
    if key is None:
        key = CONFIG["technicians"].get("default", "")
    for tech in techs:
        if tech.get("key") == key:
            return tech
    return None


def get_stars_id() -> str:
    """STARS ID for the LAB0019 fix. Env STARS_ID wins over config.toml."""
    env_id = os.getenv("STARS_ID", "").strip()
    if env_id:
        return env_id
    tech = get_technician()
    if tech:
        stars = str(tech.get("stars_id", "")).strip()
        # The shipped placeholder technician is not a real ID
        if stars and stars != "000000000":
            return stars
    return ""


def get_cdp_url() -> str:
    return os.getenv("CDP_URL") or CONFIG["connection"]["cdp_url"]


def get_claude_model() -> str:
    return os.getenv("CLAUDE_MODEL") or CONFIG["claude"]["model"]


def get_ai_config() -> dict:
    """Resolve the AI provider used for condition-code / part inference.
    Env overrides: AI_PROVIDER, AI_MODEL, AI_BASE_URL. When the provider is
    'anthropic' and no model is set, falls back to the legacy [claude] model /
    CLAUDE_MODEL so existing setups keep working."""
    ai = CONFIG.get("ai", {})
    provider = (os.getenv("AI_PROVIDER") or ai.get("provider") or "anthropic").strip().lower()
    model = (os.getenv("AI_MODEL") or ai.get("model") or "").strip()
    if not model and provider == "anthropic":
        model = get_claude_model()
    base_url = (os.getenv("AI_BASE_URL") or ai.get("base_url") or "").strip()
    return {"provider": provider, "model": model, "base_url": base_url}


def get_approval_code(error_code: str, fallback: str = "") -> str:
    return CONFIG["approval_codes"].get(error_code.upper(), fallback)


if __name__ == "__main__":
    # `python3 ows_config.py` — print the effective merged config for debugging
    import json
    print(json.dumps(CONFIG, indent=2))
    print(f"\nEffective CDP URL:      {get_cdp_url()}")
    _ai = get_ai_config()
    print(f"Effective AI provider:  {_ai['provider']} / {_ai['model'] or '(default)'}")
    tech = get_technician()
    print(f"Default technician:     {tech.get('name') if tech else '(none)'}")
    print(f"Effective STARS ID:     {get_stars_id() or '(not set)'}")
