# Prompt 03 — Weather ingestion: NWS + Open-Meteo

Implement live weather data ingestion for KLAX.

## Files to implement/update

```text
src/kalshi_weather/data/nws_client.py
src/kalshi_weather/data/open_meteo_client.py
src/kalshi_weather/model/lax_high_temp.py
src/kalshi_weather/time_utils.py
src/kalshi_weather/cli.py
tests/test_time_utils.py
```

## Tasks

1. Implement NWS client for:

```text
GET https://api.weather.gov/stations/KLAX/observations
```

2. Require a configured `NWS_USER_AGENT` header.
3. Implement Pacific local-standard-time climate-day calculation:
   - Standard offset for Pacific Standard Time = UTC-8.
   - For market date D, NWS climate day runs from D 00:00 PST to D+1 00:00 PST.
   - Convert that interval to UTC.
4. Fetch KLAX observations inside the climate-day window and compute:
   - latest observation time
   - observed high so far in °F
   - number of observations used
5. Implement Open-Meteo forecast client for LAX coordinates using the configured model list and variables.
6. Extract model-specific or blended remaining-day maximum temperature.
7. Wire CLI:

```powershell
kalshi-weather weather-snapshot --station KLAX
```

## Acceptance criteria

```powershell
pytest tests/test_time_utils.py
kalshi-weather weather-snapshot --station KLAX
```

Expected:

```text
Tests pass.
Weather command prints observed high so far and model maxes or a clear external-API error.
```

## Do not do

Do not add Herbie yet.
Do not train ML yet.
