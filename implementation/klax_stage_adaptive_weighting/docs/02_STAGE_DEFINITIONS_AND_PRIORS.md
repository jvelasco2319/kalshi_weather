# Stage definitions and initial priors

## Stage classifier

All boundaries are evaluated in `America/Los_Angeles` against the target date.

| Stage | Inclusive local interval |
|---|---|
| `pre_target` | From market open on the previous day through target date 01:59:59 |
| `target_02_10` | 02:00:00–10:59:59 |
| `target_11_13` | 11:00:00–13:59:59 |
| `target_14_16` | 14:00:00–16:59:59 |
| `target_17_close` | 17:00:00 through market close |

Every evaluation must map to exactly one stage. Handle DST using timezone-aware datetimes; do not classify from naive UTC hour arithmetic.

## Initial stage priors

| Model | Pre-target | 02–10 | 11–13 | 14–16 | 17–close |
|---|---:|---:|---:|---:|---:|
| ECMWF IFS | 23% | 22% | 20% | 20% | 21% |
| GFS 0.13° | 28% | 27% | 21% | 18% | 19% |
| GFS Seamless | 17% | 18% | 24% | 20% | 23% |
| NAM | 14% | 15% | 17% | 27% | 22% |
| NBM | 18% | 18% | 18% | 15% | 15% |

These priors encode the broad historical pattern:

- GFS 0.13° strongest early;
- GFS Seamless more useful around late morning and early afternoon;
- NAM more useful in the target-day afternoon;
- ECMWF retained as an independent anchor;
- NBM retained as a maturity-capped local signal rather than promoted from one successful day.

The combined GFS prior never exceeds 45%. NBM's actual weight is additionally constrained by its maturity cap.

## Boundary smoothing

A hard boundary could create an artificial probability jump at 11:00, 14:00, or 17:00. During the configured 30 minutes **after** a boundary:

1. calculate the prior/reliability unnormalized vector for the previous stage;
2. calculate the vector for the current stage;
3. blend them with:

\[
u_m=(1-a)u_{m,previous}+a u_{m,current}
\]

where `a = elapsed_minutes / transition_minutes` clipped to `[0,1]`;
4. apply caps and normalize after blending.

Do not use the next stage before its boundary.
