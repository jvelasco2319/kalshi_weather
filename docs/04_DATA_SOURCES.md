# Data sources

## Kalshi

Use the Kalshi public Trade API first:

```text
https://external-api.kalshi.com/trade-api/v2
```

For current end goal, only use public market data endpoints:

```text
GET /markets?series_ticker=KXHIGHLAX&status=open
GET /markets/{ticker}/orderbook
GET /markets/orderbooks?tickers=...
```

Authenticated order endpoints are future-only and should remain disabled.

## Weather observations

Use NWS API:

```text
https://api.weather.gov/stations/KLAX/observations
```

Compute observed high so far from returned observations inside the NWS climate-day window.

## Prototype weather models

Use Open-Meteo first:

```text
https://api.open-meteo.com/v1/gfs
```

Suggested models:

```text
hrrr_conus
nbm_conus
gfs_seamless
aigfs025
hgefs025
```

Suggested variables:

```text
temperature_2m
cloud_cover
shortwave_radiation
wind_speed_10m
wind_direction_10m
relative_humidity_2m
dew_point_2m
```

## Production weather models later

After paper loop works, move model ingestion to:

```text
Herbie + NOAA model GRIB2
HRRR
NBM
GFS/GEFS
ECMWF/AIFS where licensing and access are acceptable
```
