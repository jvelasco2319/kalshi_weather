# Probability Lab weighting extension

Use `ui_reference/approved_probability_lab_exact.html` as the exact approved baseline. Preserve its existing distributions, probability funnel, equations, market comparison, and price-sensitivity views.

## Additions required

### Header/status

Show:

```text
Market stage
Weighting mode
Weighting revision
Stage transition state
History readiness
```

### Model contribution ledger

Expand each model row to include or expose on drill-down:

```text
fixed prior
stage prior
stage history dates
stage n-eff
stage log loss
shrunk log loss
reliability multiplier
pre-cap weight
individual cap applied
family cap applied
NBM maturity cap
final effective weight
eligibility/exclusion reason
```

### Weight path chart

Add a chart titled **Model weights through the market**.

The backend must return the rows. Plot final effective weight over evaluation time for all five models. Include vertical stage-boundary labels and clearly indicate when NBM weight is zero because of maturity.

### Current-stage attribution figure

Show a compact waterfall or step table for each selected model:

```text
stage prior
× reliability multiplier
= pre-cap influence
→ individual/maturity/family caps
= final weight
```

Do not calculate this in JavaScript. Render backend-provided values and equation trace.

### Counterfactual comparison

For the selected contract and side, show:

```text
fixed-baseline pTrade
stage-prior pTrade
stage-reliability pTrade
market required probability
```

Label only one mode as the configured primary shadow mode.

### Equation trace additions

The backend should emit rows such as:

```text
stage classification
stage-prior lookup
stage log loss
shrinkage
reliability multiplier
transition blend
individual cap
GFS-family cap
NBM maturity cap
normalization
final mixture contribution
```

### Partial states

If stage score history is insufficient, show the stage prior and status `PRIOR ONLY`. Do not hide the model or display a fake reliability score.

### Live/replay consistency

The Command Center, Probability Lab, and API must show the same:

```text
evaluation_id
stage_id
weighting_revision
primary_weighting_mode
final model weights
```

## Browser restrictions

The browser may perform chart layout and formatting only. It must not calculate:

- stage classification;
- log loss;
- effective sample size;
- shrinkage;
- reliability multipliers;
- cap redistribution;
- final weights;
- counterfactual probabilities.
