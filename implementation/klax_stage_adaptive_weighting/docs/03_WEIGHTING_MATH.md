# Stage-adaptive weighting math

## 1. Proper probability score

For model `m`, target date `i`, evaluation `t`, and the bracket `j_i` that eventually settled:

\[
\ell_{m,i,t}=-\log\left(\operatorname{clip}(p_{mean,m,i,t,j_i},0.01,0.99)\right)
\]

Use `p_mean`, not `p_safe`, because the posterior means form a proper probability distribution across mutually exclusive outcomes.

## 2. One date-level score per market stage

A day with many evaluations must not count as many independent days. For stage `s`:

\[
\ell_{m,i,s}=\operatorname{mean}_{t\in(i,s)}\ell_{m,i,t}
\]

Each settled target date contributes at most one score per model and stage.

## 3. Recency weighting

For prior date age `d_i` in completed target dates and half-life `H=45`:

\[
a_i=2^{-d_i/H}
\]

\[
L_{m,s}=\frac{\sum_i a_i\ell_{m,i,s}}{\sum_i a_i}
\]

Effective sample size:

\[
n_{eff,m,s}=\frac{(\sum_i a_i)^2}{\sum_i a_i^2}
\]

## 4. Shrinkage

Let `J` be the number of verified mutually exclusive outcomes and `L_0=log(J)`. With shrinkage `κ=30`:

\[
L^*_{m,s}=\frac{n_{eff,m,s}L_{m,s}+\kappa L_0}{n_{eff,m,s}+\kappa}
\]

If the configured minimum history is not met, set the reliability multiplier to 1 and label the model `stage_prior_only` for that stage.

## 5. Stage prior and reliability multiplier

For stage prior `π_{m,s}` and `η=1`:

\[
q_{m,s}=\exp\left[-\eta\left(L^*_{m,s}-\min_k L^*_{k,s}\right)\right]
\]

\[
u_{m,s}=\pi_{m,s}q_{m,s}
\]

The best historical model has multiplier 1. Models with worse shrunk log loss receive a multiplier below 1. This formulation does not allow performance adaptation to increase a model above its stage prior before normalization; relative influence can still rise because weaker models shrink.

## 6. Transition blending

During a post-boundary transition:

\[
u_m=(1-a)u_{m,previous}+a u_{m,current}
\]

Then apply eligibility and caps.

## 7. Eligibility and maturity

Set `u_m=0` when the model is unavailable, stale beyond policy, ineligible, or research-only.

NBM cap by eligible completed dates:

| Dates | Maximum weight |
|---:|---:|
| 0–9 | 0% |
| 10–29 | 10% |
| 30–59 | 20% |
| 60+ | 25% |

## 8. Caps and normalization

Apply:

- individual model cap 35%;
- combined `gfs013 + gfs_seamless` cap 45%;
- NBM maturity cap;
- deterministic proportional redistribution to uncapped eligible models.

Final weights satisfy:

\[
\sum_m w_m=1
\]

within a tight numerical tolerance.

If remaining eligible models cannot absorb redistributed mass without violating caps, fail closed with a stable configuration/cap reason code; do not silently exceed a cap.

## 9. Mixture

Use the existing model probabilities and replace only the weights:

\[
p_{mix,j}=\sum_m w_m p_{mean,m,j}
\]

The existing conservative mixture and weighted-component lower-bound rules remain unchanged.

## 10. No feedback from the current event

All score rows used for an evaluation on target date `D` must satisfy:

```text
score_target_date < D
settlement_known_at <= evaluated_at
```

The outcome of `D` may update future dates only after settlement.
