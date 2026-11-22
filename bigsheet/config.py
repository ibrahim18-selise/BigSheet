"""Application paths and the optional admin policy file."""

import json
import os
import sys
from pathlib import Path

APP_NAME = "BigSheet"
APP_VERSION = "1.0.0"

DEFAULT_POLICY = {
    # Allow exporting data to CSV, Excel, Parquet or JSON files.
    "allow_export": True,
    # Allow copying cells to the clipboard.
    "allow_copy": True,
    # Allow the SQL window.
    "allow_sql": True,
    # Refuse to save a workbook without a password.
    "require_encryption": False,
}


def user_config_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    return Path.home() / ".config" / APP_NAME.lower()


def system_config_dir():
    if sys.platform == "darwin":
        return Path("/Library/Application Support") / APP_NAME
    if sys.platform.startswith("win"):
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / APP_NAME
    return Path("/etc") / APP_NAME.lower()


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_policy():
    """User policy first, then the system (IT-managed) policy overrides it."""
    policy = dict(DEFAULT_POLICY)
    for source in (user_config_dir() / "policy.json", system_config_dir() / "policy.json"):
        for key, value in _read_json(source).items():
            if key in policy:
                policy[key] = bool(value)
    return policy
