"""Pinned board Python ABIs. The legacy 3.11 lock remains unchanged."""
import platform
from pathlib import Path
import sys
import sysconfig


def supported_abi():
    return (platform.python_implementation() == "CPython"
            and sys.version_info[:2] in ((3, 11), (3, 13))
            and not sysconfig.get_config_var("Py_GIL_DISABLED"))


def requirements_lock(version=None):
    # Local asset inspection defaults to the legacy lock; real installs first
    # pass supported_abi in native_gate and preflight.
    version = sys.version_info[:2] if version is None else tuple(version)
    name = ("requirements-linux-arm64-py313.lock" if version == (3, 13)
            else "requirements-linux-arm64.lock")
    return Path(__file__).with_name(name)
