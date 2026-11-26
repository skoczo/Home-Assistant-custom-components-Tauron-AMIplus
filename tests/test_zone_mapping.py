import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.util.dt import get_time_zone

from custom_components.tauron_amiplus.connector import TauronAmiplusConnector
from custom_components.tauron_amiplus.sensor import TauronAmiplusSensor
from custom_components.tauron_amiplus.statistics import TauronAmiplusStatisticsUpdater


@pytest.mark.parametrize(("tariff", "weekend"), [("G13", "T3"), ("G12w", "T2")])
async def test_same_local_zone_in_different_days(mocker, tariff, weekend):
    friday = datetime.date(2026, 10, 30)
    sunday = friday + datetime.timedelta(days=2)

    def day(date, code, total):
        # For the observed G13 weekend response, the daily subtotal is
        # indexed by "1" while the hourly record uses Zone "3".
        hourly_zone = "3" if code == "T3" else "1"
        return {"data": {
            "sum": total, "tariff": tariff, "zones": {"1": total},
            "workName": {"1": code}, "zonesName": {"1": "Same label"},
            "allData": [{"Date": date.isoformat(), "Hour": 1, "Zone": hourly_zone,
                         "WorkName": code, "Taryfa": tariff, "EC": total}],
        }}

    first, last = day(friday, "T1", 1), day(sunday, weekend, 3)
    connector = TauronAmiplusConnector("user", "pass", "123")
    mocker.patch.object(connector, "get_raw_values_daily_for_day", new=AsyncMock(
        side_effect=[first, None, last]))
    result = await connector.get_raw_values_daily_for_range(friday, sunday, False)
    a, b = f"{tariff.upper()}_T1", f"{tariff.upper()}_{weekend}"
    total, zones, _ = TauronAmiplusSensor.get_data_from_json(result)
    assert (total, zones) == (4, {a: 1, b: 3})
    assert "ZoneKey" not in first["data"]["allData"][0]

    rows = result["data"]["allData"]
    zeros = [dict(row, EC=0) for row in rows]
    balance = TauronAmiplusSensor.get_balanced_data(result, {"data": {"allData": zeros}})
    assert balance[3] == {a + "_consumption": 1, b + "_consumption": 3}
    updater = TauronAmiplusStatisticsUpdater(None, connector, "123", "test", True, True)
    write = mocker.patch("custom_components.tauron_amiplus.statistics.async_add_external_statistics")
    for zone, expected in [(a, [1, 0]), (b, [0, 3])]:
        await updater.update_stats("test", "test", 0, None, zone, rows)
        assert [point["state"] for point in write.call_args.args[2]] == expected


async def test_published_daily_total_survives_missing_hour_value(mocker):
    date = datetime.date(2026, 10, 30)
    day = {"data": {"sum": 4, "tariff": "G13", "zones": {"1": 4},
                    "zonesName": {"1": "Peak (T1)"}, "allData": [
                        {"Zone": "1", "EC": 1}, {"Zone": "1", "EC": None}]}}
    connector = TauronAmiplusConnector("user", "pass", "123")
    mocker.patch.object(connector, "get_raw_values_daily_for_day", new=AsyncMock(return_value=day))
    result = await connector.get_raw_values_daily_for_range(date, date, False)
    assert TauronAmiplusSensor.get_data_from_json(result)[:2] == (4, {"G13_T1": 4})


def test_daily_tariff_fallback_does_not_override_conflicting_evidence():
    zones = {"1": "Peak (T1)", "2": "Off-peak (T2)"}
    rows = [{"Zone": "1", "Tariff": " g12w "}, {"Zone": "2", "Tariff": "unknown"}]
    resolve = TauronAmiplusConnector.daily_zone_keys
    assert resolve({"zonesName": zones, "allData": rows})[0] == {
        "1": "G12W_T1", "2": "G12W_T2",
    }
    assert resolve({"zonesName": zones, "allData": rows, "tariff": "G13"})[0] == {
        "1": "G12W_T1", "2": "T2",
    }


@pytest.mark.parametrize("cursor_age", [None, 10, 400])
async def test_late_zone_discovery_expands_import(mocker, cursor_age):
    tz = get_time_zone("Europe/Warsaw")
    now = datetime.datetime.now(tz)
    today = now.date()
    yesterday = today - datetime.timedelta(days=1)
    history_days = 30 if cursor_age is None else min(cursor_age + 2, 365)
    initial_day = today - datetime.timedelta(days=history_days)
    recent_day = today - datetime.timedelta(days=2)
    write = mocker.patch("custom_components.tauron_amiplus.statistics.async_add_external_statistics")

    async def fetch(start, end, generation, *, only_complete_days):
        assert not write.called
        assert end == yesterday and not generation and only_complete_days
        rows = [{"Date": yesterday.isoformat(), "Hour": 2, "Zone": "1",
                 "ZoneKey": "G13_T3", "EC": 3}]
        if start == initial_day:
            rows.insert(0, {"Date": initial_day.isoformat(), "Hour": 1, "Zone": "1",
                            "ZoneKey": "G13_T1", "EC": 1})
            rows.insert(1, {"Date": (yesterday - datetime.timedelta(days=3)).isoformat(),
                            "Hour": 1, "Zone": "1", "ZoneKey": "G13_T3", "EC": 2})
        return {"data": {"allData": rows, "zonesName": {"G13_T1": "G13_T1", "G13_T3": "G13_T3"}}}

    connector = SimpleNamespace(get_raw_values_daily_for_range=AsyncMock(side_effect=fetch))
    updater = TauronAmiplusStatisticsUpdater(None, connector, "123", "test", False, False)
    total_id = updater.get_stats_id("consumption")
    existing_hour = datetime.datetime.combine(yesterday, datetime.time(0), tzinfo=tz)

    def last_stats(stat):
        if stat == total_id:
            return {stat: [{"start": existing_hour, "sum": 100}]}
        if cursor_age is not None and stat.endswith("_zone_g13_t1"):
            return {stat: [{"start": existing_hour, "sum": 10}]}
        if cursor_age is not None and stat.endswith("_zone_g13_t3"):
            return {stat: [{"start": existing_hour - datetime.timedelta(days=cursor_age), "sum": 20}]}
        return {}

    mocker.patch.object(updater, "get_last_stats", side_effect=last_stats)
    mocker.patch.object(updater, "find_earliest_incomplete_day", return_value=None)
    daily = {"data": {"tariff": "G13", "zonesName": {"1": "Peak (T1)"}}}
    await updater.update_all(SimpleNamespace(consumption=SimpleNamespace(json_daily=daily, json_monthly=None)))

    calls = connector.get_raw_values_daily_for_range.await_args_list
    assert [call.args[0] for call in calls] == [recent_day, initial_day]
    series = {call.args[1]["statistic_id"]: call.args[2] for call in write.call_args_list}
    assert [row["state"] for row in series[total_id + "_zone_g13_t1"]] == (
        [1, 0, 0] if cursor_age is None else [0]
    )
    t3 = series[total_id + "_zone_g13_t3"]
    assert [row["state"] for row in t3 if row["state"]] == [2, 3]
    assert t3[-1]["sum"] == (5 if cursor_age is None else 25)
    assert [row["sum"] for row in series[total_id]] == [103]


@pytest.mark.parametrize("reverse", [False, True])
async def test_conflicting_zone_metadata_preserves_unassigned_energy(mocker, reverse):
    date = datetime.date(2026, 9, 25)
    rows = [
        {"Zone": "1", "Tariff": "G11", "WorkName": "T1", "EC": 1},
        {"Zone": "1", "Tariff": "G13", "WorkName": "T3", "EC": 3},
    ]
    if reverse:
        rows.reverse()
    day = {"data": {"sum": 4, "zones": {"1": 4}, "zonesName": {"1": "T1"}, "allData": rows}}
    connector = TauronAmiplusConnector("user", "pass", "123")
    mocker.patch.object(connector, "get_raw_values_daily_for_day", new=AsyncMock(return_value=day))
    result = await connector.get_raw_values_daily_for_range(date, date, False)

    assert TauronAmiplusSensor.get_data_from_json(result)[:2] == (4, {"unresolved_zone_1": 4})
    assert {row["ZoneKey"] for row in result["data"]["allData"]} == {"unresolved_zone_1"}
