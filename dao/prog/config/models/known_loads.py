"""
Configuration model for forecasts of loads that are not controlled by DAO.
"""

from pydantic import BaseModel, ConfigDict, Field

from .base import EntityId


class KnownLoadConfig(BaseModel):
    """Forecast of a known load that DAO does not control."""

    name: str = Field(
        description="Name of the load",
        json_schema_extra={
            "x-help": "Descriptive name for this load, for example 'Air conditioner' or 'Dishwasher'. Used in the logging.",
            "x-ui-section": "Known loads",
        },
    )
    entity_forecast: EntityId = Field(
        alias="entity forecast",
        description="HA entity with the forecast of the load in an attribute",
        json_schema_extra={
            "x-help": "Home Assistant entity that holds the forecast of this load in one of its attributes. The attribute is a list of entries, each with a start time, a duration and a power. DAO aligns the entries to its own intervals, so they need not start or end on a quarter.",
            "x-ui-section": "Known loads",
            "x-ui-widget-filter": "sensor",
        },
    )
    ha_attribute: str = Field(
        default="forecast",
        alias="ha attribute",
        description="Name of the attribute of the forecast entity that holds the list of entries",
        json_schema_extra={
            "x-help": "Name of the attribute of the forecast entity that holds the list of entries.",
            "x-ui-section": "Known loads",
        },
    )
    ha_start_field: str = Field(
        default="start",
        alias="ha start field",
        description="Name of the start time field in an entry",
        json_schema_extra={
            "x-help": "Name/key of the field with the start of an entry: a date and time, preferably with a time zone (ISO 8601). Without a time zone the local time is assumed.",
            "x-ui-section": "Known loads",
        },
    )
    ha_duration_field: str = Field(
        default="duration",
        alias="ha duration field",
        description="Name of the duration field in an entry",
        json_schema_extra={
            "x-help": "Name/key of the field with the duration of an entry: a number of minutes, or a text like '01:30:00' (hours:minutes:seconds).",
            "x-unit": "min",
            "x-ui-section": "Known loads",
        },
    )
    ha_power_field: str = Field(
        default="power",
        alias="ha power field",
        description="Name of the power field in an entry",
        json_schema_extra={
            "x-help": "Name/key of the field with the average power of the load during the entry, in watts.",
            "x-unit": "W",
            "x-ui-section": "Known loads",
        },
    )
    entity_energy: EntityId | None = Field(
        default=None,
        alias="entity energy",
        description="HA energy entity (kWh or Wh) with the measured consumption of the load",
        json_schema_extra={
            "x-help": "Optional: Home Assistant energy entity (kWh or Wh, never power) with the measured consumption of this load. It must have a state_class ('total_increasing', or 'total' with last_reset), so that Home Assistant keeps long-term statistics for it; it may reset, for example every night. The calculated baseload subtracts this consumption from the history. Without it, DAO stores the forecast of this load and subtracts that from the history.",
            "x-ui-section": "Known loads",
            "x-ui-widget-filter": "sensor",
            "x-validation-hint": "Energy entity with a state_class",
        },
    )

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
        loc_by_alias=False,
        json_schema_extra={
            "x-ui-group": "Devices",
            "x-icon": "home-lightning-bolt",
            "x-order": 7,
            "x-help": """# Known loads

Forecasts of loads that DAO does not control, for example an air conditioner or an appliance run that is already planned. DAO plans these known loads on top of the baseload, so the planning of the batteries and the other devices takes it into account.

## Forecast

Each item names a Home Assistant entity with a list of entries in one of its attributes:

```yaml
forecast:
  - start: "2026-11-14T18:10:00+01:00"
    duration: 95        # minutes
    power: 1200         # W
```

The entries are aligned to the intervals of the optimisation by energy: each entry adds its power times the part of its duration that falls inside an interval. Entries in the past or beyond the planning horizon are ignored. When the entity or the attribute is missing, nothing is added.

## History

With a calculated baseload these loads are removed from the history, so they are not counted twice:
- **With an energy entity**: its consumption is read from the long-term statistics of Home Assistant.
- **Without an energy entity**: DAO stores the forecast at each optimisation and subtracts the stored values.
""",
            "x-ui-section": "Known loads",
        },
    )
