# Forecast State and As-of Mathematics

All timestamps are timezone-aware. Internal comparison is UTC. Target-day and market-hour labels use `America/Los_Angeles`.

## 1. Select a run available at the decision time

For model `m`, target date `d`, and evaluation time `t`, select the newest complete run satisfying:

\[
source\_available\_at \le t
\]

and:

\[
received\_at \le t
\]

A nearest-time join is forbidden. A row sourced after `t` is future leakage even when its nominal checkpoint is earlier.

## 2. Remaining forecast maximum

Let the verified station/settlement day boundaries be `D_start` and `D_end`. For forecast valid time `tau`:

\[
R_{m,d,t}=\max_{\tau \in [\max(t,D_{start}),D_{end}]} \widehat T_{m,d,t,\tau}
\]

Only points from the selected run and only valid times that have not passed may contribute.

## 3. Observed maximum so far

From accepted KLAX observations available by `t`:

\[
O_{d,t}=\max T_{KLAX}
\]

Rejected or future-arriving observations do not contribute.

## 4. Raw live state

\[
X_{m,d,t}=\max(O_{d,t},R_{m,d,t})
\]

If observations are not yet available, use `R` and mark the missing observation state. If neither exists, the model is unavailable.

## 5. Historical reconstruction

For every prior date and time bucket, rebuild the same `X` from the forecast run and observations that were actually available then. The legacy scalar field `model_run_full_klax_climate_day` cannot certify current target-day behavior.

## 6. Comparable spread

\[
spread_{d,t}=\max_m X_{m,d,t}-\min_m X_{m,d,t}
\]

Spread is calculated from raw live states using identical semantics. It is not calculated from old full-day maxima or provider aliases.

## 7. Physical target

The physical forecast target is the final decimal KLAX station high `Z_d`. The official settlement value is modeled separately.
