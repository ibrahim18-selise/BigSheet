"""Application paths and the optional admin policy file."""

import json
import os
import sys
from pathlib import Path

APP_NAME = "BigSheet"
APP_VERSION = "1.2.0"

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


# ------------------------------------------------------------------ settings

def _settings_path():
    return user_config_dir() / "settings.json"


def load_settings():
    return _read_json(_settings_path())


def save_settings(settings):
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


def total_ram_mb():
    """Physical memory in MB, or None if it cannot be read."""
    try:
        if sys.platform.startswith("win"):
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return int(status.ullTotalPhys // (1024 * 1024))
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024))
    except (OSError, ValueError, AttributeError):
        return None


def default_memory_limit_mb():
    """A quarter of the computer's memory, kept between 1 GB and 8 GB."""
    ram = total_ram_mb() or 8192
    return max(1024, min(ram // 4, 8192))


def memory_limit_mb():
    """The memory limit chosen in Settings, or the default."""
    value = load_settings().get("memory_limit_mb")
    if isinstance(value, int) and value >= 512:
        return value
    return default_memory_limit_mb()
