"""Tests for AXONAI_* env-var overlay onto DEFAULT_CONFIG."""

from __future__ import annotations

import importlib
import pytest

import axonai.default_config as default_config_module


def _reload_with_env(monkeypatch, **overrides):
    """Set/clear env vars then reload default_config to re-evaluate DEFAULT_CONFIG."""
    for key in list(default_config_module._ENV_OVERRIDES):
        monkeypatch.delenv(key, raising=False)
    for key, val in overrides.items():
        monkeypatch.setenv(key, val)
    return importlib.reload(default_config_module)


def test_no_env_uses_built_in_defaults(monkeypatch):
    dc = _reload_with_env(monkeypatch)
    assert dc.DEFAULT_CONFIG["mt5_terminal_path"] == "C:\\Program Files\\MetaTrader 5 EXNESS\\terminal64.exe"
    assert dc.DEFAULT_CONFIG["mt5_symbol_suffix"] == ""
    assert dc.DEFAULT_CONFIG["realtime_magic_number"] == 123456
    assert dc.DEFAULT_CONFIG["realtime_dry_run"] is False


def test_string_overrides(monkeypatch):
    dc = _reload_with_env(
        monkeypatch,
        AXONAI_MT5_TERMINAL_PATH="C:\\custom\\terminal.exe",
        AXONAI_MT5_SYMBOL_SUFFIX="pro",
    )
    assert dc.DEFAULT_CONFIG["mt5_terminal_path"] == "C:\\custom\\terminal.exe"
    assert dc.DEFAULT_CONFIG["mt5_symbol_suffix"] == "pro"


def test_int_coercion(monkeypatch):
    dc = _reload_with_env(
        monkeypatch,
        AXONAI_REALTIME_MAGIC_NUMBER="999888",
        AXONAI_REALTIME_DEVIATION="15",
    )
    assert dc.DEFAULT_CONFIG["realtime_magic_number"] == 999888
    assert isinstance(dc.DEFAULT_CONFIG["realtime_magic_number"], int)
    assert dc.DEFAULT_CONFIG["realtime_deviation"] == 15
    assert isinstance(dc.DEFAULT_CONFIG["realtime_deviation"], int)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True), ("True", True), ("1", True), ("yes", True), ("on", True),
        ("false", False), ("False", False), ("0", False), ("no", False), ("off", False),
    ],
)
def test_bool_coercion(monkeypatch, raw, expected):
    dc = _reload_with_env(monkeypatch, AXONAI_REALTIME_DRY_RUN=raw)
    assert dc.DEFAULT_CONFIG["realtime_dry_run"] is expected


def test_empty_env_value_is_passthrough(monkeypatch):
    """Empty AXONAI_* values must not clobber the built-in default."""
    dc = _reload_with_env(
        monkeypatch,
        AXONAI_MT5_SYMBOL_SUFFIX="",
        AXONAI_REALTIME_MAGIC_NUMBER="",
    )
    assert dc.DEFAULT_CONFIG["mt5_symbol_suffix"] == ""
    assert dc.DEFAULT_CONFIG["realtime_magic_number"] == 123456


def test_invalid_int_raises(monkeypatch):
    """Garbage int values should surface a ValueError at import, not silently misconfigure."""
    monkeypatch.setenv("AXONAI_REALTIME_MAGIC_NUMBER", "not-a-number")
    with pytest.raises(ValueError):
        importlib.reload(default_config_module)
    # Restore module state for subsequent tests in this process
    monkeypatch.delenv("AXONAI_REALTIME_MAGIC_NUMBER", raising=False)
    importlib.reload(default_config_module)


def test_unknown_env_var_is_ignored(monkeypatch):
    """Env vars outside _ENV_OVERRIDES must not bleed into DEFAULT_CONFIG."""
    dc = _reload_with_env(
        monkeypatch,
        AXONAI_NONEXISTENT_KEY="oops",
    )
    assert "nonexistent_key" not in dc.DEFAULT_CONFIG

