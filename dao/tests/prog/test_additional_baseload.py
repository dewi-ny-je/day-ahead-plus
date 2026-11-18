"""Forecasts of uncontrolled loads (additional_baseload)."""

import datetime as dt
import logging
from types import SimpleNamespace

import pytest
from sqlalchemy import (
    Column,
    Double,
    Float,
    Integer,
    MetaData,
    String,
    Table,
)

from dao.lib.db_manager import DBmanagerObj
from dao.prog import additional_baseload as abl
from dao.prog.check_db import CheckDB
from dao.prog.config.models.additional_baseload import AdditionalBaseloadConfig
from dao.prog.da_base import DaBase
from dao.prog.da_report import Report

HOUR = 3600
QUARTER = 900
T0 = dt.datetime(2026, 1, 5, 12, 0).timestamp()  # local time


def quarters(n, start=T0):
    slot_start = [start + i * QUARTER for i in range(n)]
    return slot_start, [t + QUARTER for t in slot_start]


# ---------------------------------------------------------------- parsing
def test_parse_entries_reads_fields_and_units():
    data = [
        {"begin": "2026-01-05T12:05:00", "len": 20, "watt": 1200},
        {"begin": "2026-01-05T13:00:00", "len": "01:30:00", "watt": "500"},
    ]
    entries = abl.parse_entries(data, "begin", "len", "watt")
    assert entries == [
        (T0 + 300, T0 + 300 + 1200, 1200.0),
        (T0 + HOUR, T0 + HOUR + 5400, 500.0),
    ]


def test_parse_entries_honours_time_zone():
    aware = dt.datetime(2026, 1, 5, 11, 0, tzinfo=dt.UTC)
    entries = abl.parse_entries(
        [{"start": aware.isoformat(), "duration": 15, "power": 100}],
        "start",
        "duration",
        "power",
    )
    assert entries[0][0] == aware.timestamp()


@pytest.mark.parametrize(
    "data",
    [
        None,
        "unavailable",
        [{"start": "2026-01-05T12:00:00", "duration": 15}],  # no power
        [{"start": "2026-01-05T12:00:00", "power": 100}],  # no duration
        [{"duration": 15, "power": 100}],  # no start
        [{"start": "soon", "duration": 15, "power": 100}],
        [{"start": None, "duration": 15, "power": 100}],
        [{"start": "2026-01-05T12:00:00", "duration": "long", "power": 100}],
        [{"start": "2026-01-05T12:00:00", "duration": 0, "power": 100}],
        [{"start": "2026-01-05T12:00:00", "duration": 15, "power": -100}],
        [{"start": "2026-01-05T12:00:00", "duration": 15, "power": float("nan")}],
        ["text"],
    ],
)
def test_parse_entries_skips_unusable(data):
    assert abl.parse_entries(data, "start", "duration", "power") == []


# -------------------------------------------------------------- alignment
def test_run_inside_one_quarter():
    slot_start, slot_end = quarters(4)
    # 6 minutes of 2 kW from 12:17
    energy = abl.align_entries([(T0 + 1020, T0 + 1380, 2000)], slot_start, slot_end)
    assert energy == pytest.approx([0, 0.2, 0, 0])


def test_run_over_parts_of_several_quarters():
    slot_start, slot_end = quarters(4)
    # 12:10 - 12:40 at 1 kW: 5, 15 and 10 minutes
    energy = abl.align_entries([(T0 + 600, T0 + 2400, 1000)], slot_start, slot_end)
    assert energy == pytest.approx([5 / 60, 0.25, 10 / 60, 0])
    assert sum(energy) == pytest.approx(0.5)


def test_hourly_slots_and_summed_items():
    # two quarters followed by two hours, as with an extended horizon
    slot_start = [T0, T0 + QUARTER, T0 + 2 * QUARTER, T0 + 2 * QUARTER + HOUR]
    slot_end = slot_start[1:] + [slot_start[-1] + HOUR]
    entries = [
        (T0 + QUARTER, T0 + QUARTER + 2 * HOUR, 1000),
        (T0, T0 + QUARTER, 400),
    ]
    energy = abl.align_entries(entries, slot_start, slot_end)
    assert energy == pytest.approx([0.1, 0.25, 1.0, 0.75])


def test_outside_the_slots_adds_nothing():
    slot_start, slot_end = quarters(2)
    entries = [
        (T0 - HOUR, T0 - 60, 1000),  # ended before the first slot
        (T0 + HOUR, T0 + 2 * HOUR, 1000),  # starts after the last slot
        (T0 - QUARTER, T0 + 300, 1200),  # only its last 5 minutes count
    ]
    energy = abl.align_entries(entries, slot_start, slot_end)
    assert energy == pytest.approx([0.1, 0])


def test_first_slot_counts_from_the_start_of_the_run():
    # the run starts 5 minutes into the first quarter
    slot_start = [T0 + 300, T0 + QUARTER]
    slot_end = [T0 + QUARTER, T0 + 2 * QUARTER]
    energy = abl.align_entries([(T0, T0 + 2 * QUARTER, 1000)], slot_start, slot_end)
    assert energy == pytest.approx([10 / 60, 0.25])


def test_slot_grid_holds_whole_slots_only():
    slot_start, slot_end = abl.slot_grid(T0 + 300, T0 + HOUR, QUARTER)
    assert slot_start == [T0 + QUARTER, T0 + 2 * QUARTER, T0 + 3 * QUARTER]
    assert slot_end == [T0 + 2 * QUARTER, T0 + 3 * QUARTER, T0 + HOUR]
    assert abl.slot_grid(T0, T0 + QUARTER, QUARTER) == ([T0], [T0 + QUARTER])


# ------------------------------------------------------------------ config
def test_config_item_aliases_and_defaults():
    item = AdditionalBaseloadConfig.model_validate(
        {
            "name": "Airco",
            "entity forecast": "sensor.airco_forecast",
            "entity energy": "sensor.airco_energy",
            "ha power field": "watt",
        }
    )
    assert item.entity_forecast == "sensor.airco_forecast"
    assert item.entity_energy == "sensor.airco_energy"
    assert (item.ha_attribute, item.ha_start_field, item.ha_duration_field) == (
        "forecast",
        "start",
        "duration",
    )
    assert item.ha_power_field == "watt"
    assert (
        AdditionalBaseloadConfig.model_validate(
            {"name": "Vaatwasser", "entity forecast": "sensor.vw"}
        ).entity_energy
        is None
    )


# ----------------------------------------------------- reading the forecast
def make_base(states: dict, items: list):
    base = DaBase.__new__(DaBase)
    base.config = SimpleNamespace(
        additional_baseload=[AdditionalBaseloadConfig.model_validate(i) for i in items]
    )

    def get_state(entity):
        return SimpleNamespace(attributes=states[entity])

    base.get_state = get_state
    return base


def test_entries_of_items_with_and_without_energy_entity():
    entry_a = {"start": "2026-01-05T12:00:00", "duration": 30, "power": 1000}
    entry_b = {"start": "2026-01-05T13:00:00", "duration": 15, "power": 500}
    base = make_base(
        {
            "sensor.a": {"forecast": [entry_a]},
            "sensor.b": {"runs": [entry_b]},
            "sensor.c": {},
        },
        [
            {"name": "a", "entity forecast": "sensor.a", "entity energy": "sensor.e"},
            {"name": "b", "entity forecast": "sensor.b", "ha attribute": "runs"},
            {"name": "c", "entity forecast": "sensor.c"},  # no attribute
            {"name": "d", "entity forecast": "sensor.d"},  # no entity
        ],
    )
    entries, stored = base.get_additional_baseload_entries()
    assert entries == [
        (T0, T0 + 1800, 1000.0),
        (T0 + HOUR, T0 + HOUR + 900, 500.0),
    ]
    assert stored == [(T0 + HOUR, T0 + HOUR + 900, 500.0)]


# ------------------------------------------------------------------ history
def make_db_da(tmp_path):
    db_da = DBmanagerObj("sqlite", "da.db", db_path=str(tmp_path))
    md = MetaData()
    variabel = Table(
        "variabel",
        md,
        Column("id", Integer, primary_key=True),
        Column("code", String(10)),
    )
    Table(
        "prognoses",
        md,
        Column("id", Integer, primary_key=True),
        Column("variabel", Integer),
        Column("time", Integer),
        Column("value", Float),
    )
    md.create_all(db_da.engine)
    with db_da.engine.begin() as connection:
        connection.execute(variabel.insert(), [{"id": 63, "code": abl.VAR_CODE}])
    return db_da


def test_save_writes_whole_slots_and_latest_forecast_wins(tmp_path):
    base = DaBase.__new__(DaBase)
    base.db_da = make_db_da(tmp_path)
    base.interval_s = HOUR
    run = (T0 + 300, T0 + HOUR)
    base.save_additional_baseload([(T0, T0 + HOUR, 1000)], *run)
    # the forecast is withdrawn for the last quarter
    base.save_additional_baseload([(T0, T0 + 2700, 1000)], T0 + 1200, T0 + HOUR)
    with base.db_da.engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT time, value FROM prognoses ORDER BY time"
        ).fetchall()
    assert [tuple(r) for r in rows] == [
        (T0 + QUARTER, 0.25),
        (T0 + 2 * QUARTER, 0.25),
        (T0 + 3 * QUARTER, 0.0),
    ]


@pytest.mark.parametrize("first, second", [(QUARTER, HOUR), (HOUR, QUARTER)])
def test_changing_the_interval_leaves_no_stale_rows(tmp_path, first, second):
    base = DaBase.__new__(DaBase)
    base.db_da = make_db_da(tmp_path)
    run = (T0, T0 + 2 * HOUR)
    base.interval_s = first
    base.save_additional_baseload([(T0, T0 + 2 * HOUR, 1000)], *run)
    base.interval_s = second
    base.save_additional_baseload([(T0, T0 + HOUR, 1000)], *run)
    with base.db_da.engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT time, value FROM prognoses ORDER BY time"
        ).fetchall()
    per_hour = [
        sum(value for time, value in rows if T0 + h * HOUR <= time < T0 + (h + 1) * HOUR)
        for h in range(2)
    ]
    assert per_hour == pytest.approx([1.0, 0.0])


def test_shorter_horizon_removes_the_old_tail(tmp_path):
    base = DaBase.__new__(DaBase)
    base.db_da = make_db_da(tmp_path)
    base.save_additional_baseload([(T0, T0 + 3 * HOUR, 1000)], T0, T0 + 3 * HOUR)
    base.save_additional_baseload([(T0, T0 + 3 * HOUR, 1000)], T0, T0 + HOUR)
    with base.db_da.engine.connect() as connection:
        times = [
            r[0]
            for r in connection.exec_driver_sql("SELECT time FROM prognoses")
        ]
    assert max(times) == T0 + 3 * QUARTER
    assert len(times) == 4


def test_save_keeps_the_rows_before_the_run(tmp_path):
    base = DaBase.__new__(DaBase)
    base.db_da = make_db_da(tmp_path)
    base.save_additional_baseload([(T0, T0 + HOUR, 1000)], T0, T0 + HOUR)
    base.save_additional_baseload([], T0 + 1200, T0 + HOUR)
    with base.db_da.engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT time, value FROM prognoses ORDER BY time"
        ).fetchall()
    assert [tuple(r) for r in rows] == [
        (T0, 0.25),
        (T0 + QUARTER, 0.25),
        (T0 + 2 * QUARTER, 0.0),
        (T0 + 3 * QUARTER, 0.0),
    ]


def make_variabel_db(tmp_path, rows):
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{tmp_path}/v.db")
    md = MetaData()
    variabel = Table(
        "variabel",
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("code", String(10), unique=True, nullable=False),
        Column("name", String(50), unique=True, nullable=False),
        Column("dim", String(10), nullable=False),
        Column("aggregate", String(3), nullable=False, default="avg"),
        sqlite_autoincrement=True,
    )
    md.create_all(engine)
    if rows:
        with engine.begin() as connection:
            connection.execute(variabel.insert(), rows)
    checkdb = CheckDB.__new__(CheckDB)
    checkdb.engine = engine
    return checkdb, variabel


def all_variabel(checkdb):
    with checkdb.engine.connect() as connection:
        return {
            tuple(r)
            for r in connection.exec_driver_sql(
                "SELECT id, code, aggregate FROM variabel"
            )
        }


ABL = [63, "abl", "Extra basislast", "kWh"]


def test_migration_adds_abl_with_its_id(tmp_path):
    checkdb, variabel = make_variabel_db(tmp_path, [])
    checkdb.add_variabel(variabel, ABL, "sum")
    checkdb.add_variabel(variabel, ABL, "sum")
    assert all_variabel(checkdb) == {(63, "abl", "sum")}


def test_migration_keeps_another_variable_on_the_same_id(tmp_path):
    other = {"id": 63, "code": "own", "name": "Own", "dim": "kWh", "aggregate": "sum"}
    checkdb, variabel = make_variabel_db(tmp_path, [other])
    checkdb.add_variabel(variabel, ABL, "sum")
    rows = all_variabel(checkdb)
    assert (63, "own", "sum") in rows
    assert {code for _, code, _ in rows} == {"own", "abl"}


def make_report(tmp_path, with_sum=True):
    """A Report on two SQLite databases, with one grid sensor."""
    db_ha = DBmanagerObj("sqlite", "ha.db", db_path=str(tmp_path))
    md = MetaData()
    columns = [
        Column("id", Integer, primary_key=True),
        Column("metadata_id", Integer),
        Column("start_ts", Double),
        Column("state", Double),
    ]
    if with_sum:
        columns.append(Column("sum", Double))
    statistics = Table("statistics", md, *columns)
    meta = Table(
        "statistics_meta",
        md,
        Column("id", Integer, primary_key=True),
        Column("statistic_id", String(255)),
        Column("unit_of_measurement", String(255)),
    )
    md.create_all(db_ha.engine)
    sensors = ["sensor.grid", "sensor.airco", "sensor.no_stats", "sensor.state_only"]
    with db_ha.engine.begin() as connection:
        connection.execute(
            meta.insert(),
            [
                {"id": i + 1, "statistic_id": s, "unit_of_measurement": "kWh"}
                for i, s in enumerate(sensors)
            ],
        )
    report = Report.__new__(Report)
    report.db_ha = db_ha
    report.db_da = make_db_da(tmp_path)
    report.config = SimpleNamespace(baseload_calc_periode=7)
    report.grid_consumption_sensors = ["sensor.grid"]
    for name in (
        "grid_production",
        "solar_production_ac",
        "ev_consumption",
        "wp_consumption",
        "boiler_consumption",
        "machine_consumption",
        "battery_consumption",
        "battery_production",
        "additional_baseload",
    ):
        setattr(report, name + "_sensors", [])
    return report, statistics


def yesterday():
    today = dt.datetime.combine(dt.date.today(), dt.time())
    return today - dt.timedelta(days=1)


def insert(report, statistics, metadata_id, states, sums=None):
    """Hourly rows from the hour before yesterday 00:00."""
    first = yesterday().timestamp() - HOUR
    rows = []
    for i, state in enumerate(states):
        row = {"metadata_id": metadata_id, "start_ts": first + i * HOUR, "state": state}
        if sums is not None:
            row["sum"] = sums[i]
        rows.append(row)
    with report.db_ha.engine.begin() as connection:
        connection.execute(statistics.insert(), rows)


def grid_two_kwh_per_hour(report, statistics, hours=4):
    totals = [2.0 * i for i in range(hours + 1)]
    insert(report, statistics, 1, totals, totals)


def test_energy_entity_is_subtracted_across_a_reset(tmp_path):
    report, statistics = make_report(tmp_path)
    grid_two_kwh_per_hour(report, statistics)
    # 0.5 kWh every hour; the meter resets in the third hour
    insert(report, statistics, 2, [5.0, 5.5, 6.0, 0.5, 1.0], [0.0, 0.5, 1.0, 1.5, 2.0])
    report.additional_baseload_sensors = ["sensor.airco"]
    baseload = report.calc_weekday_baseload(yesterday().weekday())
    assert baseload[:4] == pytest.approx([1.5] * 4)


def test_energy_entity_without_statistics_is_reported_once(tmp_path, caplog):
    report, statistics = make_report(tmp_path)
    grid_two_kwh_per_hour(report, statistics)
    # only a state, as an entity without a state_class that still got rows
    insert(report, statistics, 4, [0.0, 1.0, 2.0, 3.0, 4.0], [None] * 5)
    report.additional_baseload_sensors = ["sensor.no_stats", "sensor.state_only"]
    weekday = yesterday().weekday()
    with caplog.at_level(logging.ERROR):
        first = report.calc_weekday_baseload(weekday)
        second = report.calc_weekday_baseload(weekday)
    assert first[:4] == second[:4] == pytest.approx([2.0] * 4)
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 2
    assert any("sensor.no_stats" in e for e in errors)
    assert any("sensor.state_only" in e for e in errors)


def test_statistics_table_without_sum_column(tmp_path, caplog):
    report, statistics = make_report(tmp_path, with_sum=False)
    insert(report, statistics, 1, [0.0, 2.0, 4.0])
    insert(report, statistics, 2, [0.0, 1.0, 2.0])
    report.additional_baseload_sensors = ["sensor.airco"]
    with caplog.at_level(logging.ERROR):
        baseload = report.calc_weekday_baseload(yesterday().weekday())
    assert baseload[:2] == pytest.approx([2.0, 2.0])
    assert "sensor.airco" in caplog.text


def test_stored_forecast_is_subtracted_per_hour(tmp_path):
    report, statistics = make_report(tmp_path)
    grid_two_kwh_per_hour(report, statistics)
    start = yesterday().timestamp()
    # hour 0: four quarters of 0.1 kWh, hour 2: one quarter of 0.3 kWh
    rows = [(start + q * QUARTER, 0.1) for q in range(4)] + [(start + 2 * HOUR, 0.3)]
    with report.db_da.engine.begin() as connection:
        for time, value in rows:
            connection.exec_driver_sql(
                "INSERT INTO prognoses (variabel, time, value) VALUES (63, ?, ?)",
                (int(time), value),
            )
    baseload = report.calc_weekday_baseload(yesterday().weekday())
    assert baseload[:4] == pytest.approx([1.6, 2.0, 1.7, 2.0])


def test_stored_forecast_has_local_and_utc_hours(tmp_path):
    report, _ = make_report(tmp_path)
    start = yesterday().timestamp()
    with report.db_da.engine.begin() as connection:
        for q in range(5):
            connection.exec_driver_sql(
                "INSERT INTO prognoses (variabel, time, value) VALUES (63, ?, 0.1)",
                (int(start + q * QUARTER),),
            )
    df = report.get_stored_additional_baseload(
        yesterday(), yesterday() + dt.timedelta(days=1)
    )
    assert list(df["slot"]) == [start, start + HOUR]
    assert list(df["tijd"]) == [yesterday(), yesterday() + dt.timedelta(hours=1)]
    assert list(df["additional_baseload"]) == pytest.approx([0.4, 0.1])


def test_negative_baseload_is_clamped(tmp_path):
    report, statistics = make_report(tmp_path)
    grid_two_kwh_per_hour(report, statistics)
    insert(report, statistics, 2, [0.0, 3.0, 3.5, 4.0, 4.5], [0.0, 3.0, 3.5, 4.0, 4.5])
    report.additional_baseload_sensors = ["sensor.airco"]
    baseload = report.calc_weekday_baseload(yesterday().weekday())
    assert baseload[:4] == pytest.approx([0.0, 1.5, 1.5, 1.5])
