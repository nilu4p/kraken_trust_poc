"""External open-data sources (Marine Regions, Open-Meteo, Global Fishing Watch, aisstream.io).

Every source degrades gracefully: on missing keys, timeouts or HTTP errors it
returns ``None`` / empty data and logs why, so the pipeline never crashes.
"""
