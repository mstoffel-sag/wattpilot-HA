"""Tests for utils.py — GetChargerProp helper."""

from __future__ import annotations
import types
import pytest

# All imports come from conftest bootstrap
from tests.conftest import GetChargerProp, MockCharger, MockChargerMissingProps


class TestGetChargerProp:

    def test_returns_value_for_known_property(self, charger):
        assert GetChargerProp(charger, "car") == 1

    def test_returns_default_for_missing_property(self, charger):
        assert GetChargerProp(charger, "upo", "MISSING") == "MISSING"

    def test_returns_none_for_missing_property_no_default(self, charger):
        assert GetChargerProp(charger, "does_not_exist") is None

    def test_returns_default_when_property_value_is_none(self):
        c = MockCharger(extra_props={"nullprop": None})
        assert GetChargerProp(c, "nullprop", 42) == 42

    def test_returns_none_when_property_is_none_and_no_default(self):
        c = MockCharger(extra_props={"nullprop": None})
        assert GetChargerProp(c, "nullprop") is None

    def test_missing_cards_returns_int_default_not_raises(self, charger_no_optional):
        """Bug #2 regression: missing cards returns -1 (int), not a crash."""
        result = GetChargerProp(charger_no_optional, "cards", -1)
        assert result == -1
        assert not isinstance(result, list)

    def test_charger_without_allProps_returns_default(self):
        class Bare: pass
        assert GetChargerProp(Bare(), "car", "fallback") == "fallback"

    def test_identifier_none_returns_default(self, charger):
        assert GetChargerProp(charger, None, "X") == "X"

    def test_list_property_returned_intact(self, charger):
        nrg = GetChargerProp(charger, "nrg")
        assert isinstance(nrg, list) and len(nrg) == 16

    def test_namespace_property_returned_intact(self, charger):
        ccw = GetChargerProp(charger, "ccw")
        assert hasattr(ccw, "ssid") and ccw.ssid == "MyWifi"

    def test_known_string_property(self, charger):
        assert GetChargerProp(charger, "sse") == "TEST12345"

    def test_known_int_property(self, charger):
        assert GetChargerProp(charger, "rbc") == 3
