"""Tests for statistics updater and incremental synchronization."""
import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.util.dt import as_utc, get_time_zone

from custom_components.tauron_amiplus.connector import (
    TauronAmiplusDataSet,
    TauronAmiplusRawData,
)
from custom_components.tauron_amiplus.const import (
    CONF_METER_ID,
    CONF_METER_NAME,
    CONF_SHOW_BALANCED,
    CONF_SHOW_GENERATION,
    CONST_BALANCED,
    CONST_CONSUMPTION,
    CONST_GENERATION,
)
from custom_components.tauron_amiplus.statistics import TauronAmiplusStatisticsUpdater


def test_get_time():
    hour_1 = {"Date": "2026-10-24", "Hour": 1}
    t1 = TauronAmiplusStatisticsUpdater.get_time(hour_1)
    assert t1.tzinfo is not None
    # 2026-10-24 00:00 CEST -> 2026-10-23 22:00 UTC
    assert t1 == datetime.datetime(2026, 10, 23, 22, 0, tzinfo=datetime.timezone.utc)

    # 25th hour (DST change)
    hour_25 = {"Date": "2026-10-25", "Hour": 25}
    t25 = TauronAmiplusStatisticsUpdater.get_time(hour_25)
    assert t25.tzinfo is not None
    assert t25 == datetime.datetime(2026, 10, 25, 23, 0, tzinfo=datetime.timezone.utc)


def test_prepare_balanced_raw_data():
    # Empty cases
    assert TauronAmiplusStatisticsUpdater.prepare_balanced_raw_data({}) == ([], [])
    assert TauronAmiplusStatisticsUpdater.prepare_balanced_raw_data({CONST_CONSUMPTION: []}) == ([], [])

    raw_data = {
        CONST_CONSUMPTION: [
            {"Date": "2026-10-24", "Hour": 1, "EC": "2.5", "Zone": "1"},
            {"Date": "2026-10-24", "Hour": 2, "EC": "1.0", "Zone": "1"},
            {"Date": "2026-10-24", "Hour": 3, "EC": "0.5", "Zone": "1"},
        ],
        CONST_GENERATION: [
            {"Date": "2026-10-24", "Hour": 1, "EC": "1.0", "Zone": "1"},  # Net consumption 1.5
            {"Date": "2026-10-24", "Hour": 2, "EC": "3.0", "Zone": "1"},  # Net generation 2.0
            # Hour 3 generation missing (e.g. dropped packet) -> safely skipped, doesn't crash
        ],
    }

    b_cons, b_gen = TauronAmiplusStatisticsUpdater.prepare_balanced_raw_data(raw_data)
    assert len(b_cons) == 2
    assert len(b_gen) == 2

    # Hour 1: 2.5 - 1.0 = 1.5 consumption, 0 gen
    assert b_cons[0]["EC"] == "1.5"
    assert b_gen[0]["EC"] == "0"

    # Hour 2: 1.0 - 3.0 = -2.0 -> 0 cons, 2.0 gen
    assert b_cons[1]["EC"] == "0"
    assert b_gen[1]["EC"] == "2.0"


async def test_update_stats_incremental(mocker):
    mock_hass = MagicMock()
    mock_add_stats = mocker.patch(
        "custom_components.tauron_amiplus.statistics.async_add_external_statistics"
    )

    updater = TauronAmiplusStatisticsUpdater(
        mock_hass, None, "123456", "Dom", show_generation=False, show_balanced=False
    )

    raw_hours = [
        {"Date": "2026-10-24", "Hour": 1, "EC": "1.0", "Zone": "1"},
        {"Date": "2026-10-24", "Hour": 2, "EC": "2.0", "Zone": "1"},
        {"Date": "2026-10-24", "Hour": 3, "EC": "3.0", "Zone": "1"},
    ]

    # Assume hours 1 and 2 are already in Recorder up to hour 2 start
    t2 = updater.get_time(raw_hours[1])
    initial_sum = 100.0

    await updater.update_stats(
        "tauron_importer:123456_consumption",
        "Tauron Dom Zużycie",
        initial_sum,
        last_stats_time=t2,
        zone_id=None,
        raw_data=raw_hours,
    )

    mock_add_stats.assert_called_once()
    metadata, stats_data = mock_add_stats.call_args[0][1], mock_add_stats.call_args[0][2]
    # Only hour 3 should be added!
    assert len(stats_data) == 1
    assert stats_data[0]["state"] == 3.0
    assert stats_data[0]["sum"] == 103.0
    assert stats_data[0]["start"] == updater.get_time(raw_hours[2])


async def test_update_stats_all_already_recorded(mocker):
    mock_hass = MagicMock()
    mock_add_stats = mocker.patch(
        "custom_components.tauron_amiplus.statistics.async_add_external_statistics"
    )

    updater = TauronAmiplusStatisticsUpdater(
        mock_hass, None, "123456", "Dom", show_generation=False, show_balanced=False
    )

    raw_hours = [
        {"Date": "2026-10-24", "Hour": 1, "EC": "1.0", "Zone": "1"},
        {"Date": "2026-10-24", "Hour": 2, "EC": "2.0", "Zone": "1"},
    ]

    t2 = updater.get_time(raw_hours[1])
    await updater.update_stats(
        "tauron_importer:123456_consumption",
        "Tauron Dom Zużycie",
        initial_sum=50.0,
        last_stats_time=t2,
        zone_id=None,
        raw_data=raw_hours,
    )

    # When no new hours are available, async_add_external_statistics must NOT be called
    mock_add_stats.assert_not_called()


async def test_update_stats_multizone_filtering(mocker):
    mock_hass = MagicMock()
    mock_add_stats = mocker.patch(
        "custom_components.tauron_amiplus.statistics.async_add_external_statistics"
    )

    updater = TauronAmiplusStatisticsUpdater(
        mock_hass, None, "123456", "Dom", show_generation=False, show_balanced=False
    )

    raw_hours = [
        {"Date": "2026-10-24", "Hour": 1, "EC": "1.0", "Zone": "1"},
        {"Date": "2026-10-24", "Hour": 2, "EC": "2.0", "Zone": "2"},
        {"Date": "2026-10-24", "Hour": 3, "EC": "3.0", "Zone": "1"},
    ]

    await updater.update_stats(
        "tauron_importer:123456_consumption_zone_1",
        "Tauron Dom Zużycie Szczyt",
        initial_sum=10.0,
        last_stats_time=None,
        zone_id="1",
        raw_data=raw_hours,
    )

    mock_add_stats.assert_called_once()
    stats_data = mock_add_stats.call_args[0][2]
    assert len(stats_data) == 3
    # Hour 1 (Zone 1): usage 1.0, sum 11.0
    assert stats_data[0]["state"] == 1.0
    assert stats_data[0]["sum"] == 11.0
    # Hour 2 (Zone 2): usage 0.0 for zone 1 stat, sum remains 11.0
    assert stats_data[1]["state"] == 0.0
    assert stats_data[1]["sum"] == 11.0
    # Hour 3 (Zone 1): usage 3.0, sum 14.0
    assert stats_data[2]["state"] == 3.0
    assert stats_data[2]["sum"] == 14.0


async def test_update_all_incremental_range(mocker):
    mock_hass = MagicMock()
    mock_connector = AsyncMock()
    mock_add_stats = mocker.patch(
        "custom_components.tauron_amiplus.statistics.async_add_external_statistics"
    )

    updater = TauronAmiplusStatisticsUpdater(
        mock_hass, mock_connector, "123456", "Dom", show_generation=False, show_balanced=False
    )

    # Mock recorder returning existing statistic up to 2026-10-23 20:00 UTC (22:00 CEST)
    last_stat_time = datetime.datetime(2026, 10, 23, 20, 0, tzinfo=datetime.timezone.utc)
    mocker.patch.object(
        updater,
        "get_last_stats",
        return_value={
            "tauron_importer:123456_consumption": [
                {
                    "start": last_stat_time.timestamp(),
                    "sum": 200.0,
                    "end": (last_stat_time + datetime.timedelta(hours=1)).timestamp(),
                }
            ]
        },
    )

    # Daily data for 2026-10-23 and 2026-10-24
    mock_connector.get_raw_values_daily_for_range.return_value = {
        "data": {
            "allData": [
                {"Date": "2026-10-23", "Hour": 22, "EC": "1.0", "Zone": "1"},  # start is 20:00 UTC -> skipped
                {"Date": "2026-10-24", "Hour": 1, "EC": "2.0", "Zone": "1"},   # start is 22:00 UTC -> added!
            ]
        }
    }

    raw_data = TauronAmiplusRawData()
    raw_data.consumption = TauronAmiplusDataSet()
    raw_data.consumption.json_daily = {"data": {"zonesName": {"1": "Taryfa jednolita"}}}

    await updater.update_all(raw_data)

    # Verify that get_raw_values_daily_for_range was called starting from min_last_date - 1 day
    mock_connector.get_raw_values_daily_for_range.assert_awaited_once()
    call_args = mock_connector.get_raw_values_daily_for_range.call_args[0]
    assert call_args[0] == datetime.date(2026, 10, 22)

    # Verify that only the new hour was saved
    mock_add_stats.assert_called_once()
    stats_data = mock_add_stats.call_args[0][2]
    assert len(stats_data) == 1
    assert stats_data[0]["state"] == 2.0
    assert stats_data[0]["sum"] == 202.0


async def test_update_all_with_explicit_start_date(mocker):
    mock_hass = MagicMock()
    mock_connector = AsyncMock()
    mock_add_stats = mocker.patch(
        "custom_components.tauron_amiplus.statistics.async_add_external_statistics"
    )

    updater = TauronAmiplusStatisticsUpdater(
        mock_hass, mock_connector, "123456", "Dom", show_generation=False, show_balanced=False
    )

    # Existing stats are at 2026-10-20
    existing_stat_time = datetime.datetime(2026, 10, 20, 10, 0, tzinfo=datetime.timezone.utc)
    mocker.patch.object(
        updater,
        "get_last_stats",
        return_value={
            "tauron_importer:123456_consumption": [
                {
                    "start": existing_stat_time.timestamp(),
                    "sum": 500.0,
                    "end": (existing_stat_time + datetime.timedelta(hours=1)).timestamp(),
                }
            ]
        },
    )

    mock_connector.get_raw_values_daily_for_range.return_value = {
        "data": {
            "allData": [
                {"Date": "2026-10-01", "Hour": 1, "EC": "5.0", "Zone": "1"},
            ]
        }
    }

    raw_data = TauronAmiplusRawData()
    raw_data.consumption = TauronAmiplusDataSet()
    raw_data.consumption.json_daily = {"data": {"zonesName": {"1": "G11"}}}

    # User re-imports from 2026-10-01 (before existing stat)
    start_date = datetime.datetime(2026, 10, 1, 0, 0, tzinfo=get_time_zone("Europe/Warsaw"))
    await updater.update_all(raw_data, start_date=start_date)

    # fetch_from should be 2026-10-01
    call_args = mock_connector.get_raw_values_daily_for_range.call_args[0]
    assert call_args[0] == datetime.date(2026, 10, 1)

    mock_add_stats.assert_called_once()
    stats_data = mock_add_stats.call_args[0][2]
    assert stats_data[0]["sum"] == 5.0  # Reset sum to 0 + 5.0


async def test_update_all_generation_and_balanced_multizone(mocker):
    mock_hass = MagicMock()
    mock_connector = AsyncMock()
    mock_add_stats = mocker.patch(
        "custom_components.tauron_amiplus.statistics.async_add_external_statistics"
    )

    updater = TauronAmiplusStatisticsUpdater(
        mock_hass, mock_connector, "123456", "Dom", show_generation=True, show_balanced=True
    )

    # Empty stats in recorder (initial setup)
    mocker.patch.object(updater, "get_last_stats", return_value={})

    mock_connector.get_raw_values_daily_for_range.side_effect = [
        # consumption
        {
            "data": {
                "allData": [
                    {"Date": "2026-10-24", "Hour": 1, "EC": "3.0", "Zone": "1"},
                    {"Date": "2026-10-24", "Hour": 2, "EC": "1.0", "Zone": "2"},
                ]
            }
        },
        # generation
        {
            "data": {
                "allData": [
                    {"Date": "2026-10-24", "Hour": 1, "EC": "1.0", "Zone": "1"},
                    {"Date": "2026-10-24", "Hour": 2, "EC": "4.0", "Zone": "2"},
                ]
            }
        },
    ]

    raw_data = TauronAmiplusRawData()
    raw_data.consumption = TauronAmiplusDataSet()
    raw_data.consumption.json_daily = {"data": {"zonesName": {"1": "Szczyt", "2": "Pozaszczyt"}}}

    await updater.update_all(raw_data)

    # 4 base stats (consumption, generation, balanced_consumption, balanced_generation)
    # * 3 (overall, zone 1, zone 2) = 12 statistics added
    assert mock_add_stats.call_count == 12


async def test_update_all_no_consumption_data(mocker):
    mock_hass = MagicMock()
    mock_connector = AsyncMock()
    mock_add_stats = mocker.patch(
        "custom_components.tauron_amiplus.statistics.async_add_external_statistics"
    )

    updater = TauronAmiplusStatisticsUpdater(
        mock_hass, mock_connector, "123456", "Dom", show_generation=False, show_balanced=False
    )

    # Case 1: last_data.consumption is None
    raw_data_none = TauronAmiplusRawData()
    await updater.update_all(raw_data_none)
    mock_connector.get_raw_values_daily_for_range.assert_not_called()

    # Case 2: connector returns None
    raw_data = TauronAmiplusRawData()
    raw_data.consumption = TauronAmiplusDataSet()
    mocker.patch.object(updater, "get_last_stats", return_value={})
    mock_connector.get_raw_values_daily_for_range.return_value = None

    await updater.update_all(raw_data)
    mock_add_stats.assert_not_called()


async def test_get_last_stats_date_types(mocker):
    mock_hass = MagicMock()
    updater = TauronAmiplusStatisticsUpdater(
        mock_hass, None, "123456", "Dom", show_generation=False, show_balanced=False
    )

    # Test float timestamp
    mocker.patch.object(
        updater,
        "get_last_stats",
        return_value={"tauron_importer:test": [{"end": 1700000000.0}]},
    )
    d1 = await updater.get_last_stats_date("tauron_importer:test")
    assert d1 == datetime.datetime(2023, 11, 14, 22, 13, 20, tzinfo=datetime.timezone.utc)

    # Test naive datetime
    mocker.patch.object(
        updater,
        "get_last_stats",
        return_value={"tauron_importer:test": [{"end": datetime.datetime(2026, 10, 24, 12, 0)}]},
    )
    d2 = await updater.get_last_stats_date("tauron_importer:test")
    assert d2.tzinfo == datetime.timezone.utc

    # Test empty
    mocker.patch.object(updater, "get_last_stats", return_value={})
    assert await updater.get_last_stats_date("tauron_importer:empty") is None


def test_are_stats_up_to_date():
    assert not TauronAmiplusStatisticsUpdater.are_stats_up_to_date(None)
    now = datetime.datetime.now(datetime.timezone.utc)
    recent = now - datetime.timedelta(days=5)
    old = now - datetime.timedelta(days=40)
    assert TauronAmiplusStatisticsUpdater.are_stats_up_to_date(recent)
    assert not TauronAmiplusStatisticsUpdater.are_stats_up_to_date(old)


async def test_manually_update_entry(mocker):
    mock_hass = MagicMock()
    mock_entry = MagicMock()
    mock_entry.data = {
        CONF_USERNAME: "user",
        CONF_PASSWORD: "pw",
        CONF_METER_ID: "12345",
        CONF_METER_NAME: "Dom",
    }
    mock_entry.options = {
        CONF_SHOW_GENERATION: True,
        CONF_SHOW_BALANCED: True,
    }
    mock_entry.entry_id = "test_entry_id"

    mock_connector_cls = mocker.patch("custom_components.tauron_amiplus.statistics.TauronAmiplusConnector")
    mock_connector = mock_connector_cls.return_value
    mock_data = MagicMock()
    mock_connector.get_raw_data = AsyncMock(return_value=mock_data)
    mock_connector.async_save_cache = AsyncMock()

    mock_update_all = mocker.patch.object(TauronAmiplusStatisticsUpdater, "update_all", new_callable=AsyncMock)

    await TauronAmiplusStatisticsUpdater.manually_update(
        mock_hass, datetime.date(2026, 10, 1), mock_entry
    )

    mock_connector.get_raw_data.assert_awaited_once()
    mock_update_all.assert_awaited_once()
    mock_connector.async_save_cache.assert_awaited_once()
