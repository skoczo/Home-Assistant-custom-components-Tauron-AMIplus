"""Tests for sensor helper methods."""
import pytest
from custom_components.tauron_amiplus.sensor import TauronAmiplusSensor


def test_get_data_from_json():
    sample_json = {
        "data": {
            "sum": 12.3456,
            "zones": {"1": 5.1234, "2": 7.2222},
            "zonesName": {"1": "Szczyt", "2": "Poza szczytem"},
            "allData": [
                {"Date": "2026-10-20", "EC": "1.0", "Hour": 1, "Zone": "1"},
                {"Date": "2026-10-24", "EC": "2.0", "Hour": 24, "Zone": "2"},
            ]
        }
    }
    total, zones, data_range = TauronAmiplusSensor.get_data_from_json(sample_json)
    assert total == 12.346
    assert zones == {"Szczyt": 5.123, "Poza szczytem": 7.222}
    assert data_range == "2026-10-20 - 2026-10-24"


def test_get_balanced_data_normal():
    con_json = {
        "data": {
            "zonesName": {"1": "T1"},
            "allData": [
                {"Date": "2026-10-24", "EC": "1.5", "Hour": 1, "Zone": "1"},
                {"Date": "2026-10-24", "EC": "0.5", "Hour": 2, "Zone": "1"},
            ]
        }
    }
    gen_json = {
        "data": {
            "zonesName": {"1": "T1"},
            "allData": [
                {"Date": "2026-10-24", "EC": "0.5", "Hour": 1, "Zone": "1"},
                {"Date": "2026-10-24", "EC": "2.0", "Hour": 2, "Zone": "1"},
            ]
        }
    }
    balance, sum_con, sum_gen, zones, data_range = TauronAmiplusSensor.get_balanced_data(con_json, gen_json)
    # Hour 1: con 1.5, gen 0.5 -> diff +1.0 (consumption)
    # Hour 2: con 0.5, gen 2.0 -> diff -1.5 (generation)
    # Total balance = +1.0 + (-1.5) = -0.5
    assert sum_con == 1.0
    assert sum_gen == -1.5
    assert balance == -0.5
    assert data_range == "2026-10-24 - 2026-10-24"


def test_get_balanced_data_missing_zone_key():
    """Reproduces issue #290: KeyError when a zone id is not present in zonesName."""
    con_json = {
        "data": {
            "zonesName": {"1": "T1"},
            "allData": [
                {"Date": "2026-10-24", "EC": "1.0", "Hour": 1, "Zone": "2"},
            ]
        }
    }
    gen_json = {
        "data": {
            "zonesName": {"1": "T1"},
            "allData": [
                {"Date": "2026-10-24", "EC": "0.0", "Hour": 1, "Zone": "2"},
            ]
        }
    }
    balance, sum_con, sum_gen, zones, data_range = TauronAmiplusSensor.get_balanced_data(con_json, gen_json)
    assert balance == 1.0
    assert "zone_2_consumption" in zones
    assert zones["zone_2_consumption"] == 1.0
