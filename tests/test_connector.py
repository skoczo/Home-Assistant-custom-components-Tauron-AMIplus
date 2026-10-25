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

    # Test delete_older_than
    old_day = datetime.datetime(2026, 10, 1)
    cache.add_value(old_day, generation=False, value={"old": 1})
    cache.delete_older_than(datetime.datetime(2026, 10, 15))
    assert cache.get_value(old_day, generation=False) is None
    assert cache.get_value(day, generation=False) == data

    cache.clear()
    assert cache.get_value(day, generation=False) is None


async def test_daily_data_cache_persistence(mocker):
    mock_store = mocker.AsyncMock()
    mock_store.async_load.return_value = {
        "consumption": {"2026-10-20": {"EC": 5}},
        "generation": {"2026-10-20": {"EC": 2}},
    }

    cache = DailyDataCache("test_meter")
    cache._store = mock_store

    await cache.async_load()
    assert cache.get_value(datetime.date(2026, 10, 20), generation=False) == {"EC": 5}
    assert cache.get_value(datetime.date(2026, 10, 20), generation=True) == {"EC": 2}

    cache.add_value(datetime.date(2026, 10, 21), generation=False, value={"EC": 10})
    await cache.async_save()

    mock_store.async_save.assert_awaited_once()
    saved_data = mock_store.async_save.call_args[0][0]
    assert "2026-10-21" in saved_data["consumption"]
    assert saved_data["consumption"]["2026-10-21"] == {"EC": 10}


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


async def test_get_raw_values_daily_for_range(mocker):
    connector = TauronAmiplusConnector("user", "pass", "12345")

    day1 = datetime.date(2026, 10, 20)
    day2 = datetime.date(2026, 10, 21)

    # Preload day 1 into cache
    connector._cache.add_value(
        day1,
        generation=False,
        value={
            "data": {
                "allData": [{"Date": "2026-10-20", "Hour": 1, "EC": "2.0", "Zone": "1", "Status": "0"}],
                "sum": 2.0,
                "zones": {"1": 2.0},
                "zonesName": {"1": "G11"},
            }
        },
    )

    # Mock chart values fetch only for uncached day 2
    mock_chart_values = mocker.patch.object(
        connector,
        "get_chart_values",
        return_value={
            "data": {
                "allData": [{"Date": "2026-10-21", "Hour": 1, "EC": "3.0", "Zone": "1", "Status": "0"}],
                "sum": 3.0,
                "zones": {"1": 3.0},
                "zonesName": {"1": "G11"},
            }
        },
    )

    data = await connector.get_raw_values_daily_for_range(day1, day2, generation=False)
    assert data is not None
    assert len(data["data"]["allData"]) == 2
    assert data["data"]["sum"] == 5.0
    assert data["data"]["zones"] == {"1": 5.0}
    # get_chart_values should only be called once (for day 2), because day 1 was a cache hit!
    mock_chart_values.assert_called_once()


async def test_get_data_set_optimizations(mocker):
    # Without show_balanced
    connector = TauronAmiplusConnector("user", "pass", "12345", show_balanced=False)
    mocker.patch.object(connector, "get_reading", return_value={"reading": 1})
    mocker.patch.object(connector, "get_values_daily", return_value=({"daily": 1}, "2026-10-24"))
    mocker.patch.object(connector, "get_values_monthly", return_value={"monthly": 1})
    mocker.patch.object(connector, "get_values_yearly", return_value={"yearly": 1})
    mock_month_hourly = mocker.patch.object(connector, "get_values_month_hourly", return_value={"month_hourly": 1})
    mock_30_days = mocker.patch.object(connector, "get_values_last_30_days_hourly", return_value={"30_days": 1})

    dataset, _ = await connector.get_data_set(generation=False)
    # 30-day fetch is never called
    mock_30_days.assert_not_called()
    # Month hourly is not called when show_balanced=False
    mock_month_hourly.assert_not_called()
    assert dataset.json_month_hourly is None
    assert dataset.json_last_30_days_hourly is None

    # With show_balanced=True
    connector_balanced = TauronAmiplusConnector("user", "pass", "12345", show_balanced=True)
    mocker.patch.object(connector_balanced, "get_reading", return_value={"reading": 1})
    mocker.patch.object(connector_balanced, "get_values_daily", return_value=({"daily": 1}, "2026-10-24"))
    mocker.patch.object(connector_balanced, "get_values_monthly", return_value={"monthly": 1})
    mocker.patch.object(connector_balanced, "get_values_yearly", return_value={"yearly": 1})
    mock_month_hourly_b = mocker.patch.object(connector_balanced, "get_values_month_hourly", return_value={"month_hourly": 1})

    dataset_b, _ = await connector_balanced.get_data_set(generation=False)
    mock_month_hourly_b.assert_called_once()
    assert dataset_b.json_month_hourly == {"month_hourly": 1}


async def test_async_clear_cache(mocker):
    connector = TauronAmiplusConnector("user", "pass", "12345")
    mock_save = mocker.patch.object(connector._cache, "async_save", new_callable=mocker.AsyncMock)

    connector._cache.add_value(datetime.date(2026, 10, 24), generation=False, value={"EC": 1})
    await connector.async_clear_cache()

    assert connector._cache.get_value(datetime.date(2026, 10, 24), generation=False) is None
    mock_save.assert_awaited_once()


async def test_incomplete_day_downloaded_only_once_per_cycle(mocker):
    import copy

    connector = TauronAmiplusConnector("user", "pass", "12345")
    today = datetime.date(2026, 10, 25)

    partial_day_response = {
        "success": True,
        "data": {
            "values": [0.338, 0.087] + [None] * 22,
            "zones": {"1": 0.425},
            "sum": 0.425,
            "chartZones": {"1": [False] * 24},
            "zonesName": {"1": "Cała doba (T1)"},
            "tariff": "G11",
            "allData": [
                {"EC": "0.338", "Date": "2026-10-25T02:00:00.000+02:00", "Hour": "1", "Status": "0", "Zone": "1"},
                {"EC": "0.087", "Date": "2026-10-25T02:00:00.000+02:00", "Hour": "2", "Status": "0", "Zone": "1"},
            ] + [
                {"EC": None, "Date": "2026-10-25T02:00:00.000+02:00", "Hour": str(h), "Status": None, "Zone": "1"}
                for h in range(3, 25)
            ],
        },
    }

    mock_chart_values = mocker.patch.object(
        connector,
        "get_chart_values",
        side_effect=lambda payload: copy.deepcopy(partial_day_response),
    )

    # First call (e.g. from get_values_month_hourly)
    res1 = await connector.get_raw_values_daily_for_day(today, generation=False)
    # Second call (e.g. from get_values_year_hourly in the same update cycle)
    res2 = await connector.get_raw_values_daily_for_day(today, generation=False)

    assert res1 is not None
    assert res2 is not None
    # Under TDD: An incomplete day should not be re-downloaded repeatedly in the same cycle!
    assert mock_chart_values.call_count == 1
    assert connector._cycle_downloads == 1


async def test_incomplete_day_is_retried_in_next_cycle(mocker):
    import copy

    connector = TauronAmiplusConnector("user", "pass", "12345")
    today = datetime.date(2026, 10, 25)

    partial_day_response = {
        "success": True,
        "data": {
            "values": [0.338, 0.087] + [None] * 22,
            "zones": {"1": 0.425},
            "sum": 0.425,
            "chartZones": {"1": [False] * 24},
            "zonesName": {"1": "Cała doba (T1)"},
            "tariff": "G11",
            "allData": [
                {"EC": "0.338", "Date": "2026-10-25T02:00:00.000+02:00", "Hour": "1", "Status": "0", "Zone": "1"},
                {"EC": "0.087", "Date": "2026-10-25T02:00:00.000+02:00", "Hour": "2", "Status": "0", "Zone": "1"},
            ] + [
                {"EC": None, "Date": "2026-10-25T02:00:00.000+02:00", "Hour": str(h), "Status": None, "Zone": "1"}
                for h in range(3, 25)
            ],
        },
    }

    mock_chart_values = mocker.patch.object(
        connector,
        "get_chart_values",
        side_effect=lambda payload: copy.deepcopy(partial_day_response),
    )

    # First cycle
    res1 = await connector.get_raw_values_daily_for_day(today, generation=False)
    assert res1 is not None
    assert mock_chart_values.call_count == 1
    # Day is incomplete, so it shouldn't be locked into persistent cache
    assert connector._cache.get_value(today, generation=False) is None

    # Next cycle (cycle cache cleared on new cycle / restart / timer)
    connector._cycle_cache.clear()
    res2 = await connector.get_raw_values_daily_for_day(today, generation=False)
    assert res2 is not None
    assert mock_chart_values.call_count == 2


