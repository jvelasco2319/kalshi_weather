# Evidence notes

- `klax_model_metrics_by_market_stage.csv` contains the historical raw model leadership by market hour used to initialize stage priors.
- `klax_model_metrics_common_window.csv` contains overall raw accuracy and bias for the four models with comparable history.
- `klax_july7_0724_1800_chatgpt_brief.md` documents the one joined day on which NBM supplied a valuable early minority signal.

These files justify the direction of the priors. They do **not** certify final stage weights because the older historical model rows were not always reconstructed from the same remaining-window state now used live. Production adaptation must use current state-consistent walk-forward evaluations.
