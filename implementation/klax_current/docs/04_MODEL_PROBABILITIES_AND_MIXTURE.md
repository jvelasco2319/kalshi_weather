# Model Probabilities and Five-Model Mixture

The trading engine must preserve model disagreement and minority signals. It must not reduce the five models to a single nearest bracket before producing probabilities.

## A. Model-specific residual library

For model `m`, prior date `i`, and time bucket `h`:

\[
r_{m,i,h}=Z_i-X_{m,i,h}
\]

where `Z_i` is the final decimal KLAX high and `X` is the historically reconstructed live state.

Use at most the most recent 45 eligible prior target dates. Require 30 dates for a normal model contribution. NBM may contribute provisionally from 10 dates under its maturity cap.

### Recency weight

For age `a_i` measured in completed target dates and half-life `H=21`:

\[
a_i=2^{-a_i/H}
\]

Normalize the weights within each model/time bucket.

Effective sample size:

\[
n_{eff}=\frac{(\sum_i a_i)^2}{\sum_i a_i^2}
\]

## B. Model point estimate for reporting

Weighted median residual:

\[
\widetilde r_m=WeightedMedian(r_{m,i,h},a_i)
\]

Corrected model point:

\[
C_m=\max(O_{live},X_{m,live}+\widetilde r_m)
\]

This point is for reporting. The probability engine uses the full residual distribution.

## C. Model-specific physical and settlement scenarios

For every prior date `i`:

\[
Z^*_{m,i}=\max(O_{live},X_{m,live}+r_{m,i,h})
\]

Historical station-to-settlement gap:

\[
g_i=Y_i-Z_i
\]

Official scenario under the current verified market quantizer `Q`:

\[
Y^*_{m,i}=Q(Z^*_{m,i}+g_i)
\]

The physical residual and settlement gap remain paired by date.

## D. Model-specific bracket counts

For bracket `j`, use the recency weights to form a weighted frequency. Scale it by `n_eff` to obtain fractional evidence count `k_mj`.

With `J` brackets and symmetric Dirichlet prior `alpha=0.5`:

\[
p_{mean,mj}=\frac{k_{mj}+\alpha}{n_{eff,m}+J\alpha}
\]

Conservative Yes probability:

\[
p_{safe,yes,mj}=BetaPPF(0.10,k_{mj}+\alpha,n_{eff,m}-k_{mj}+(J-1)\alpha)
\]

Conservative No probability is calculated from the complement posterior, not as `1 - p_safe_yes`.

## E. Historical reliability weights

For each model and broad market stage, calculate walk-forward negative log loss over prior settled dates only:

\[
L_m=-\frac{1}{n_m}\sum_i \log(clip(p_{m,i,realized},0.01,0.99))
\]

Shrink toward the uniform-bracket score `L0=log(J)` with `kappa=30` dates:

\[
L^*_m=\frac{n_mL_m+\kappa L_0}{n_m+\kappa}
\]

With prior model weight `pi_m` and `eta=1`:

\[
u_m=\pi_m\exp(-\eta(L^*_m-\min_k L^*_k))
\]

Normalize after applying:

- individual cap 0.35;
- GFS family cap 0.45;
- NBM maturity cap;
- model availability and history gates.

Weights are fixed for a target date/time bucket from prior settled dates. They do not update using the current date's eventual outcome.

## F. Mixture probabilities

Model mixture mean:

\[
p_{mix,j}=\sum_m w_m p_{mean,mj}
\]

Use the minimum effective sample size among positively weighted models:

\[
n_{mix}=\min_m n_{eff,m}
\]

Create fractional mixture counts `k_mix,j = n_mix * p_mix,j` and calculate a Beta lower bound from those counts.

Final trade probability is the more conservative of:

1. the mixture-count lower bound; and
2. the weighted average of component lower bounds.

\[
p_{trade,yes,j}=\min(p_{safe,mix,yes,j},\sum_m w_mp_{safe,yes,mj})
\]

Apply the analogous rule to No.

## G. Reported forecast

Report:

- weighted mean of corrected model points;
- weighted median physical scenario;
- 10th–90th physical scenario interval;
- every model's state, residual median, weight, history count, and probabilities.

The trade engine uses contract probabilities, not the reported point estimate.
