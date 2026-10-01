# Architecture and the attributes we preserve

The loop is: register the question and checks → propose different explanations → evaluate → independently reproduce and challenge → synthesize findings → continue, fork, combine or reject → periodically reallocate → freeze a selection → separately confirm → report the limits.

## Inspiration and provenance

[OpenAI's published Navier–Stokes account](https://openai.com/index/navier-stokes-solution/) describes coordinating groups, varied formulations, tool use, easier related problems, insight consolidation into follow-up prompts, resources shifted after a breakthrough, and separate Lean verification. Our template translates those workflow ideas into a smaller research process. It does not reproduce OpenAI's internal model or undisclosed orchestration.

The hourly cadence, allocation percentages, retirement thresholds, finite catalogs, named falsification colony, and holdout rules are our design choices. OpenAI does not disclose them in that account.

## Main attributes and their concrete implementation

| Attribute | Template mechanism | What it establishes |
|---|---|---|
| Different formulations and approaches | `research_questions`, `colony_briefs`, alternative explanations | A campaign explores more than one story for the same observation. |
| Easier stepping-stone questions | Questions marked `surrogate` with `transfer_test`; cross-question parent proposals need `transfer_rationale` | A partial answer can guide a new main-question test. It is not automatically a main result. |
| Tool-grounded evidence | Approved adapter, numerical metrics, row/experiment ledger, source/input hashes | Conclusions can be inspected and rerun. |
| Coordinated communication | Host dispatches real colony tasks; every epoch requires named cross-family reviews | Task files alone do not count as agent work. |
| Shared knowledge | Sealed `syntheses` artifacts, evidence-linked lessons, latest synthesis embedded in next tasks | Useful and failed findings inform the next round. |
| Iteration from intermediate results | Parent candidate IDs plus `CONTINUE`, `FORK`, `COMBINE`, `CHALLENGE` | Follow-ups have a traceable reason. Canonically identical parameters do not become new experiments. |
| Evidence-driven resource shifts | Hourly scorecard, reviewed winners, retirement and child-colony decisions | More experiment slots can go to productive families without deleting the failed record. |
| Independent checking | Two nonfamily reviewers, independent numerical reproduction, falsification work | Agreement alone cannot certify a result. The reproduction path must be substantively independent. |
| Separate final verification | Main-question freeze, one-shot confirmation claim, registered gates | A development winner receives a separate check. Empirical confirmation is not mathematical proof. |
| Controlled operation | Finite budgets, kernel writer lock, task timeouts, immutable input/source bindings | Resuming does not refund attempts or extend the registered deadline. |

## What the coordinator does

The coordinator gives colonies distinct briefs, dispatches within the available host slots, reads artifacts, combines the strongest supported insights, records unresolved objections, and requests executable follow-ups. It must distinguish a partial measurement improvement from passing the full acceptance criteria. It does not replace criticism with a majority vote.

The host can coordinate actual agents concurrently. The deterministic evaluation runner executes candidates sequentially and writes durable artifacts. This avoids presenting parallel research as if a local parameter loop were multiple independently reasoning agents.

## Development and conclusions

During development, use chronological or grouped checks, negative controls, sample-size gates, uncertainty and multiplicity corrections where the problem requires them. These belong in the approved adapter and registered gates; the generic engine cannot invent correct statistics for every problem.

Every lesson has `SUPPORTED`, `REJECTED` or `UNRESOLVED` status and cites artifacts. Here `SUPPORTED` means reproduced all-gate development evidence for that stated test, not a universal scientific fact. Broader patterns stay unresolved until tested. The final report must distinguish confirmation passes, confirmation failures, unavailable evidence, integrity failures and unexplored hypotheses.

## Limits of the generalization

A separate verifier is a workflow similarity to formal proof checking, but statistical results can deteriorate on new conditions even after confirmation. A finite catalog can find improvements within its registered language; it cannot establish that every possible method has been considered. Hashes detect unintended changes, not malicious rewriting of both data and hashes. Trusted adapters still need real permission controls.

There is no autonomous hosted-agent service in this package. The accompanying host protocol supplies that orchestration and must honestly report which agents actually ran.
