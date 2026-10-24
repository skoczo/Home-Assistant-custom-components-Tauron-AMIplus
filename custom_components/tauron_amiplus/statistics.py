import datetime
import logging

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticMetaData, StatisticMeanType
from homeassistant.components.recorder.statistics import (async_add_external_statistics, get_last_statistics,
                                                          statistics_during_period)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import as_utc, get_time_zone, utc_from_timestamp

from .connector import TauronAmiplusConnector, TauronAmiplusRawData
from .const import (CONF_METER_ID, CONF_METER_NAME, CONF_SHOW_BALANCED, CONF_SHOW_GENERATION, CONST_BALANCED,
                    CONST_CONSUMPTION, CONST_GENERATION, DEFAULT_NAME, STATISTICS_DOMAIN)

_LOGGER = logging.getLogger(__name__)


class TauronAmiplusStatisticsUpdater:

    def __init__(self, hass: HomeAssistant, connector: TauronAmiplusConnector, meter_id: str, meter_name: str,
                 show_generation: bool, show_balanced: bool) -> None:
        self.hass = hass
        self.connector = connector
        self.meter_id = meter_id
        self.meter_name = meter_name
        self.show_generation = show_generation
        self.show_balanced = show_balanced

    @staticmethod
    async def manually_update(hass, start_date: datetime.date, entry) -> None:
        username = entry.data[CONF_USERNAME]
        password = entry.data[CONF_PASSWORD]
        meter_id = entry.data[CONF_METER_ID]
        meter_name = entry.data[CONF_METER_NAME]

        show_generation = entry.options.get(CONF_SHOW_GENERATION, False)
        show_balanced = entry.options.get(CONF_SHOW_BALANCED, False)

        connector = TauronAmiplusConnector(
            username,
            password,
            meter_id,
            hass=hass,
            config_entry_id=entry.entry_id,
            show_generation=show_generation,
            show_balanced=show_balanced,
        )
        statistics_updater = TauronAmiplusStatisticsUpdater(
            hass, connector, meter_id, meter_name, show_generation, show_balanced
        )

        data = await connector.get_raw_data()
        start_datetime = datetime.datetime.combine(
            start_date, datetime.time(0, 0, tzinfo=get_time_zone("Europe/Warsaw"))
        )
        if data is not None:
            await statistics_updater.update_all(data, start_datetime)
            await connector.async_save_cache()

    async def update_all(self, last_data: TauronAmiplusRawData, start_date: datetime.datetime = None) -> None:
        if last_data.consumption is None:
            return

        zones = {}
        if last_data.consumption.json_daily and "data" in last_data.consumption.json_daily:
            zones = last_data.consumption.json_daily["data"].get("zonesName", {})
        if not zones and last_data.consumption.json_monthly and "data" in last_data.consumption.json_monthly:
            zones = last_data.consumption.json_monthly["data"].get("zonesName", {})

        all_stat_ids = await self.prepare_stats_ids(zones)

        warsaw_tz = get_time_zone("Europe/Warsaw")
        now = datetime.datetime.now(warsaw_tz)

        if start_date is not None:
            fetch_from = start_date.astimezone(warsaw_tz).date()
            start_date_utc = as_utc(start_date)
            for s, v in all_stat_ids.items():
                if v["last_stats_time"] is not None and v["last_stats_time"] >= start_date_utc:
                    v["sum"] = 0.0
                    v["last_stats_time"] = None
        else:
            has_all_stats = all(v["last_stats_time"] is not None for v in all_stat_ids.values())
            if has_all_stats:
                min_last_time = min(v["last_stats_time"] for v in all_stat_ids.values())
                min_last_date = min_last_time.astimezone(warsaw_tz).date()
                fetch_from = min_last_date - datetime.timedelta(days=1)
            else:
                # First run or missing stats: fetch default range (30 days back)
                fetch_from = (now - datetime.timedelta(days=30)).date()

        # Guard against excessively old query ranges
        max_history_date = (now - datetime.timedelta(days=365)).date()
        if fetch_from < max_history_date:
            fetch_from = max_history_date

        fetch_to = now.date()
        self.log(f"Fetching raw daily energy data for range: {fetch_from} to {fetch_to}")

        data_consumption = await self.connector.get_raw_values_daily_for_range(fetch_from, fetch_to, False)
        if data_consumption is None or not data_consumption.get("data", {}).get("allData"):
            self.log(f"No consumption data received for range {fetch_from} - {fetch_to}")
            return

        raw_data = {CONST_CONSUMPTION: data_consumption["data"]["allData"]}

        if self.show_generation or self.show_balanced:
            data_generation = await self.connector.get_raw_values_daily_for_range(fetch_from, fetch_to, True)
            if data_generation is not None and data_generation.get("data", {}).get("allData"):
                raw_data[CONST_GENERATION] = data_generation["data"]["allData"]
            else:
                raw_data[CONST_GENERATION] = []

        if self.show_balanced:
            balanced_consumption, balanced_generation = self.prepare_balanced_raw_data(raw_data)
            raw_data[f"{CONST_BALANCED}_{CONST_CONSUMPTION}"] = balanced_consumption
            raw_data[f"{CONST_BALANCED}_{CONST_GENERATION}"] = balanced_generation

        for s, v in all_stat_ids.items():
            if v["data_source"] not in raw_data:
                continue
            await self.update_stats(
                s,
                v["name"],
                v["sum"],
                v["last_stats_time"],
                v["zone"],
                raw_data[v["data_source"]],
            )

    async def prepare_stats_ids(self, zones):
        suffixes = [{
            "id": CONST_CONSUMPTION,
            "name": CONST_CONSUMPTION,
            "data": CONST_CONSUMPTION,
            "zone": None
        }]
        if self.show_generation:
            suffixes.append({
                "id": CONST_GENERATION,
                "name": CONST_GENERATION,
                "data": CONST_GENERATION,
                "zone": None
            })
        if self.show_balanced:
            suffixes.append({
                "id": f"{CONST_BALANCED}_{CONST_CONSUMPTION}",
                "name": f"{CONST_BALANCED} {CONST_CONSUMPTION}",
                "data": f"{CONST_BALANCED}_{CONST_CONSUMPTION}",
                "zone": None
            })
            suffixes.append({
                "id": f"{CONST_BALANCED}_{CONST_GENERATION}",
                "name": f"{CONST_BALANCED} {CONST_GENERATION}",
                "data": f"{CONST_BALANCED}_{CONST_GENERATION}",
                "zone": None
            })
        if len(zones) > 1:
            for s in [*suffixes]:
                suffixes.extend([
                    {
                        "id": f'{s["id"]}_zone_{zone}',
                        "name": f'{s["name"]} {zone_name}',
                        "data": s["data"],
                        "zone": zone
                    }
                    for zone, zone_name in zones.items()
                ])

        all_stat_ids = {
            self.get_stats_id(s["id"]): {
                "name": self.get_stats_name(s["name"]),
                "zone": s["zone"],
                "data_source": s["data"],
                "sum": 0.0,
                "last_stats_time": None,
                "last_stats_end": None
            }
            for s in suffixes
        }
        for k, v in all_stat_ids.items():
            last_stat = await self.get_last_stats(k)
            if k in last_stat and len(last_stat[k]) > 0:
                row = last_stat[k][0]
                v["sum"] = float(row.get("sum", 0.0) or 0.0)
                start = row.get("start")
                if isinstance(start, (int, float)):
                    start = utc_from_timestamp(start)
                elif isinstance(start, datetime.datetime) and start.tzinfo is None:
                    start = as_utc(start)
                v["last_stats_time"] = start

                end = row.get("end")
                if isinstance(end, (int, float)):
                    end = utc_from_timestamp(end)
                elif isinstance(end, datetime.datetime) and end.tzinfo is None:
                    end = as_utc(end)
                v["last_stats_end"] = end

        return all_stat_ids

    def get_stats_id(self, suffix):
        return f"{STATISTICS_DOMAIN}:{self.meter_id}_{suffix}".lower()

    def get_stats_name(self, suffix):
        return f"{DEFAULT_NAME} {self.meter_name} {suffix}"

    @staticmethod
    def are_stats_up_to_date(last_stats_end):
        if last_stats_end is None:
            return False
        now = datetime.datetime.now()
        return (as_utc(now) - last_stats_end).days < 30

    @staticmethod
    def prepare_balanced_raw_data(raw_data) -> (list, list):
        consumption_data = raw_data.get(CONST_CONSUMPTION, [])
        generation_data = raw_data.get(CONST_GENERATION, [])
        if not consumption_data or not generation_data:
            return [], []

        gen_by_time = {(g["Date"], g["Hour"]): g for g in generation_data}

        balanced_consumption = []
        balanced_generation = []

        for consumption in consumption_data:
            key = (consumption["Date"], consumption["Hour"])
            if key not in gen_by_time:
                continue
            generation = gen_by_time[key]
            value_consumption = float(consumption["EC"])
            value_generation = float(generation["EC"])
            balance = value_consumption - value_generation
            if balance > 0:
                output_consumption = {
                    "EC": f"{balance}",
                    "Date": consumption["Date"],
                    "Hour": consumption["Hour"],
                    "Zone": consumption["Zone"],
                }
                output_generation = {
                    "EC": "0",
                    "Date": generation["Date"],
                    "Hour": generation["Hour"],
                    "Zone": generation["Zone"],
                }
            else:
                output_consumption = {
                    "EC": "0",
                    "Date": consumption["Date"],
                    "Hour": consumption["Hour"],
                    "Zone": consumption["Zone"],
                }
                output_generation = {
                    "EC": f"{-balance}",
                    "Date": generation["Date"],
                    "Hour": generation["Hour"],
                    "Zone": generation["Zone"],
                }
            balanced_consumption.append(output_consumption)
            balanced_generation.append(output_generation)

        return balanced_consumption, balanced_generation

    async def update_stats(self, statistic_id, statistic_name, initial_sum, last_stats_time, zone_id, raw_data):
        current_sum = initial_sum
        metadata: StatisticMetaData = {
            "has_mean": False,
            "has_sum": True,
            "name": statistic_name,
            "source": STATISTICS_DOMAIN,
            "statistic_id": statistic_id,
            "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
            "mean_type": StatisticMeanType.NONE,
            "unit_class": "energy",
        }
        statistic_data = []
        for raw_hour in raw_data:
            start = self.get_time(raw_hour)
            if last_stats_time is not None and start <= last_stats_time:
                continue
            usage = float(raw_hour["EC"])
            if zone_id is not None and raw_hour["Zone"] != zone_id:
                usage = 0.0
            current_sum += usage
            stats = {
                "start": start,
                "state": usage,
                "sum": current_sum,
            }
            statistic_data.append(stats)

        if statistic_data:
            async_add_external_statistics(self.hass, metadata, statistic_data)
            self.log(f"Added {len(statistic_data)} new entries for statistic: {statistic_id}")
        else:
            self.log(f"No new entries to update for statistic: {statistic_id}")

    async def get_last_stats_date(self, statistic_id):
        last_stats = await self.get_last_stats(statistic_id)
        if statistic_id in last_stats and len(last_stats[statistic_id]) > 0:
            end = last_stats[statistic_id][0]["end"]
            if isinstance(end, (int, float)):
                end = utc_from_timestamp(end)
            elif isinstance(end, datetime.datetime) and end.tzinfo is None:
                end = as_utc(end)
            return end
        return None

    async def get_last_stats(self, statistic_id):
        return await get_instance(self.hass).async_add_executor_job(
            get_last_statistics,
            self.hass, 1, statistic_id, True, {"state", "sum"})

    async def get_stats(self, raw_data, statistic_id):
        return await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass, self.get_time(raw_data[0]), None, [statistic_id], "hour", None, {"state", "sum"})

    def log(self, msg):
        _LOGGER.debug(f"[{self.meter_id}]: {msg}")

    @staticmethod
    def get_time(raw_hour):
        zone = get_time_zone("Europe/Warsaw")
        date_str = raw_hour["Date"]
        hour = int(raw_hour["Hour"]) - 1
        if hour < 24:
            dt = datetime.datetime.strptime(f"{date_str} {hour:02d}:00", "%Y-%m-%d %H:%M").replace(tzinfo=zone)
        else:
            dt = datetime.datetime.strptime(f"{date_str} 23:00", "%Y-%m-%d %H:%M").replace(tzinfo=zone) + datetime.timedelta(hours=1)
        return as_utc(dt)
