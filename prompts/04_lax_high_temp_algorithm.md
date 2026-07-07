# Prompt 04 — LAX high-temperature probability algorithm

Implement the first working probability model.

## Files to implement/update

```text
src/kalshi_weather/model/probability.py
src/kalshi_weather/model/lax_high_temp.py
src/kalshi_weather/trading/signals.py
src/kalshi_weather/cli.py
tests/test_probability.py
```

## Tasks

1. Parse bracket ranges from Kalshi market titles/subtitles/rules where available.
   - Support labels like `70° to 71°`, `65° or below`, `74° or above`.
   - If parsing fails, show the market but mark probability unavailable.
2. Generate settlement samples:

```python
future_high_sample = normal(blended_model_future_high, residual_sigma_f)
settlement_high_sample = max(observed_high_so_far, future_high_sample)
```

3. Compute bracket probabilities from samples.
4. Normalize probabilities across the discovered event brackets if all brackets are parsed.
5. Compute market executable prices:
   - YES ask from NO bid
   - YES bid from YES bid
   - NO ask from YES bid
   - NO bid from NO bid
6. Compute terminal edges:

```text
yes_edge = p_yes - yes_ask
no_edge = (1 - p_yes) - no_ask
```

7. Wire CLI:

```powershell
kalshi-weather predict-once --series KXHIGHLAX --station KLAX
```

## Acceptance criteria

```powershell
pytest tests/test_probability.py
kalshi-weather predict-once --series KXHIGHLAX --station KLAX
```

Expected:

```text
Tests pass.
Prediction table prints bracket, market prices, model probability, and edge.
```

## Notes

This v0 model is intentionally simple. Calibration comes later. The purpose is to get an end-to-end probability engine running.
