"""Tests for connector methods."""
import datetime
from custom_components.tauron_amiplus.connector import DailyDataCache, TauronAmiplusConnector


def test_daily_data_cache():
    cache = DailyDataCache("test_meter")
    day = datetime.datetime(2026, 10, 24)
    data = {"test": 123}

    assert cache.get_value(day, generation=False) is None
    cache.add_value(day, generation=False, value=data)
    assert cache.get_value(day, generation=False) == data
    assert cache.get_value(day, generation=True) is None

    cache.clear()
    assert cache.get_value(day, generation=False) is None


def test_get_meters_parser():
    sample_html = """
    <select name="site[client]">
        <option value="12345678" data-data='{"type": "G11"}'>Licznik Dom (12345678)</option>
        <option value="87654321" data-data='{"type": "WO"}'>Licznik Firma (87654321)</option>
    </select>
    """
    meters = TauronAmiplusConnector._get_meters(sample_html)
    assert len(meters) == 2
    assert meters[0] == {"meter_id": "12345678", "meter_name": "Licznik Dom (12345678)", "meter_type": "G11"}
    assert meters[1] == {"meter_id": "87654321", "meter_name": "Licznik Firma (87654321)", "meter_type": "WO"}
