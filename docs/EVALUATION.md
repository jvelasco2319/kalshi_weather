# Evaluating the architecture

The handbook proposes comparison with a strong single-agent baseline, an independent ensemble with checked aggregation, a coordinator with workers and the full design. This branch provides a source-linked manifest comparator; it does not run those provider experiments automatically or claim that the full design is best.

Prepare representative tasks with independent acceptance outcomes: ambiguity, contradictions, valid negatives, tool failures and controlled interruptions. Keep prompt-development tasks separate from held-out evaluation tasks. Use repeated runs to estimate variability. Hold model family/version and task set fixed for the first topology comparison and use equal total budget caps; report actual use and unused budget separately. Later compare matched actual spending and matched elapsed time as separate studies.

`evaluate-architecture --evaluation-files file1.json file2.json ...` reads manifests with:

- Run ID, topology (`single`, `independent`, `coordinator`, `full`), model ID/version, contract family and evaluation task ID.
- Prompt and harness versions, total budget cap, actual units or explicit null.
- Observed target success, accepted claim count and externally determined invalid accepted claim count.
- Checked evidence count, completed and unplanned duplicate task counts, coordination units.
- Recovery correctness or explicit null, and exact original source/output locators.

The comparator rejects unmatched model versions, task families, budget caps and task sets. It reports success, false acceptance, evidence per known unit, redundancy, coordination cost, recovery and repeated-run variation. Unknown costs and single-run variance remain unavailable; they are not replaced with zero. Input manifests require genuine independent evaluation; schema validation cannot prove their scientific truth.

Also evaluate calibration, source coverage, communication timing, memory applicability, branch counts, review quality and stopping rules where relevant. Change one mechanism at a time when feasible. Keep a mechanism only when its measured benefit warrants its cost. The current software tests validate controller behavior and recovery cases, not these research-performance claims.
