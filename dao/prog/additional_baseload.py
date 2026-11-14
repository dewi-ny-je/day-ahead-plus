"""
Forecasts of loads that are not controlled by DAO (config "additional_baseload").

A forecast is a list of entries, each with a start time, a duration and a
power. The entries are aligned to time slots by energy: each entry adds its
power times the part of its duration that falls inside a slot.
"""

import logging
import math

import pandas as pd

# code of the stored forecast in the table "variabel"
VAR_CODE = "abl"


def _start_ts(value) -> float:
    """
    :param value: date and time of the start of an entry: a datetime or a text
        (ISO 8601). Without a time zone it is taken as local time.
    :return: the start as a unix timestamp
    """
    moment = pd.Timestamp(value)
    if moment is pd.NaT:
        raise ValueError(f"no date and time: {value!r}")
    return moment.to_pydatetime().timestamp()


def _duration_s(value) -> float:
    """
    :param value: duration of an entry: a number of minutes, or a text like
        "01:30:00" (hours:minutes:seconds)
    :return: the duration in seconds
    """
    if isinstance(value, str):
        try:
            return float(value) * 60
        except ValueError:
            return pd.Timedelta(value).total_seconds()
    return float(value) * 60


def parse_entries(
    data, start_field: str, duration_field: str, power_field: str
) -> list[tuple[float, float, float]]:
    """
    Reads the entries of a forecast.
    :param data: the list of entries, each a dict with the three fields
    :param start_field: key of the start time
    :param duration_field: key of the duration
    :param power_field: key of the power in W
    :return: list of (start, end, power): start and end as unix timestamps,
        power in W. Entries that miss a field, cannot be read or have no
        positive duration and power are left out.
    """
    entries = []
    if not isinstance(data, (list, tuple)):
        return entries
    for entry in data:
        try:
            start = _start_ts(entry[start_field])
            duration = _duration_s(entry[duration_field])
            power = float(entry[power_field])
        except (KeyError, TypeError, ValueError) as ex:
            logging.debug(f"Additional baseload: entry {entry} skipped ({ex!r})")
            continue
        if not all(math.isfinite(x) for x in (start, duration, power)):
            logging.debug(f"Additional baseload: entry {entry} skipped")
            continue
        if duration <= 0 or power <= 0:
            continue
        entries.append((start, start + duration, power))
    return entries


def align_entries(
    entries: list[tuple[float, float, float]],
    slot_start: list[float],
    slot_end: list[float],
) -> list[float]:
    """
    Aligns forecast entries to time slots by energy.
    :param entries: list of (start, end, power) as returned by parse_entries
    :param slot_start: unix timestamp of the start of each slot
    :param slot_end: unix timestamp of the end of each slot
    :return: the energy in kWh of the entries in each slot. Entries, or parts
        of them, outside the slots add nothing.
    """
    energy = [0.0] * len(slot_start)
    for start, end, power in entries:
        for u in range(len(slot_start)):
            overlap = min(end, slot_end[u]) - max(start, slot_start[u])
            if overlap > 0:
                energy[u] += power * overlap / 3_600_000
    return energy


def slot_grid(start: float, end: float, step: int) -> tuple[list[float], list[float]]:
    """
    :param start: unix timestamp from which the slots are wanted
    :param end: unix timestamp of the end of the last slot
    :param step: length of a slot in seconds
    :return: (slot_start, slot_end) of the whole slots of length step, on the
        grid of step, that lie between start and end
    """
    first = math.ceil(start / step) * step
    slot_start = [float(t) for t in range(int(first), int(end) - step + 1, step)]
    return slot_start, [t + step for t in slot_start]
