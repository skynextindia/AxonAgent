"""Unit tests for AxonDaemon._entry_direction — the single source of truth
for mapping a peak event to a trade side (fail-closed on ambiguity)."""

from __future__ import annotations

import types

from axonai.realtime.daemon import AxonDaemon


def _evt(direction):
    return types.SimpleNamespace(details={"direction": direction})


def test_bullish_maps_to_buy():
    assert AxonDaemon._entry_direction(_evt("bullish_exhaustion")) == "BUY"
    assert AxonDaemon._entry_direction(_evt("bullish_reversal")) == "BUY"


def test_bearish_maps_to_sell():
    assert AxonDaemon._entry_direction(_evt("bearish_exhaustion")) == "SELL"
    assert AxonDaemon._entry_direction(_evt("bearish_reversal")) == "SELL"


def test_indeterminate_fails_closed():
    # Missing / empty / unknown direction must return None, never a default side.
    assert AxonDaemon._entry_direction(_evt("")) is None
    assert AxonDaemon._entry_direction(_evt(None)) is None
    assert AxonDaemon._entry_direction(_evt("sideways")) is None
    assert AxonDaemon._entry_direction(types.SimpleNamespace(details={})) is None
