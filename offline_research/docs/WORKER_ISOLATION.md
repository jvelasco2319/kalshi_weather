# Worker isolation and Codex backend investigation

Status: **BLOCKED for historical research dispatch**. The existing authenticated Codex CLI can return structured reasoning, but a complete tool boundary and actual holdout-read denial have not been verified. Do not interpret the synthetic smoke test as Goal 1 readiness or Goal 2 execution.

## Supported boundary today

The SQLite controller validates task records, evidence hashes, identity separation, resource reservations, results, and transitions. External/manual workers are the only accepted backend. Registry limits do not by themselves constrain a process's operating-system privileges, actual token generation, or filesystem tools.

The Python experiment guard rejects new socket operations, tested subprocess creation, dynamic library loading, and direct opens of protected paths. Its documented threat model is reviewed, cooperative Python code. Independent testing with a synthetic file showed that a descriptor opened before guard installation remains readable through `os.read`. The surrounding Codex application's tools are not restricted by that child-process audit hook. A fresh process must install its guard before receiving protected handles or labels; inherited handles must be closed. A scoped input packet is useful, but does not substitute for actual worker access separation.

The controller's historical campaign gate requires a readiness artifact with `status = READY_FOR_OFFLINE_CAMPAIGN`, matching code/dataset/evaluation-policy hashes, and both `offline_verified` and `holdout_access_denied` set to boolean true. These flags remain unsatisfied for the full discovery backend. The controller checks the artifact checksum and matching declared versions; a separate readiness verifier must establish the underlying facts.

## Restricted CLI attempt

The installed executable is Codex CLI `0.155.0-alpha.9.2`. The probe launcher in `src/klax_lab/codex_backend.py` uses an empty worker directory, a generated synthetic canary outside that directory, a structured packet supplied through standard input, and a JSON response schema. It does not provide historical data or final-test labels.

Each invocation requests read-only sandboxing, disables approval escalation, ignores the user's configuration file, forces existing ChatGPT authentication, and removes API-key environment overrides. It supplies no model override. It disables the installed feature flags for shell execution, apps, plugins, browser/computer control, hooks, subagents, image tools, code-mode host, automatic dependencies and related capabilities. No global settings, credentials, plugins, accounts, or security policies were changed.

The official configuration reference describes shell, app, web-search, authentication and other controls. It also states that app/MCP traffic is separate from shell-network policy; merely disabling sandboxed shell networking is insufficient. See [official configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference). The launcher therefore attempts capability removal in addition to retaining the child sandbox. Configuration semantics were checked against the installed CLI rather than assuming every documented key works in this build.

The source permits only two synthetic attempts per probe output directory, each limited to 120 seconds. The investigation used exactly two attempts; no further model probes were made. The timeout terminates the direct CLI process, followed by bounded output cleanup. It does not constitute an operating-system process-tree security boundary.

## Observed evidence

Artifacts are in `runs/codex-isolation-probe/`, deliberately ignored by Git. Each attempt records the exact invocation, structured synthetic packet, raw JSONL events, sanitized diagnostics and assessment. The canary is random synthetic test data, never a real secret or holdout value.

`assessment-revised.json` contains the reviewed event classification. The initial assessment and all raw events remain preserved; the initial classifier counted startup diagnostic items as activity, which was corrected without another service call. Both versions keep the backend blocked.

1. **Probe 1:** Startup failed before any model response. Strict configuration rejected `tools.view_image` as an unknown key, although that key appeared in the current official reference. The redundant key was removed; the installed feature `features.view_image=false` remained disabled.
2. **Probe 2:** The authenticated service returned a schema-conforming response in approximately 8.9 seconds. Host-reported usage was 7,211 input tokens and 185 output tokens. No model override or API-key backend was supplied. The model said it lacked a filesystem-read/command capability and did not attempt to read the canary. It also reported `functions.exec`, `functions.wait`, request-input tools, `apply_patch`, and `clock__curr_time` as available. These names are a model self-report, not an independently verified complete host tool catalog.

The host emitted a diagnostic that code mode was unavailable because its host was disabled, and that code mode would fail closed. No actual tool invocation or canary content appeared in the JSONL event stream. Absence of a call is not proof of enforced read denial. A failed startup diagnostic is also not an observed tool invocation; the assessment distinguishes those categories.

The experiment demonstrates subscription-authenticated structured reasoning. It does **not** demonstrate a tool-free agent, an attempted-and-denied protected-file read, or a verified complete advertised tool catalog. The assessment remains `BLOCKED`, `backend_ready=false`, `host_tool_catalog_verified=false`, and `holdout_access_denied=false` even if a model claims that no tools are available.

## Concrete blocker and next implementation boundary

The current `codex exec --json` stream provides activity events, not sufficient host-attested evidence of the entire model-visible tool catalog. Disabling the tested features did not establish the required absence of all tool capabilities. The two permitted probes are exhausted. Historical worker dispatch is intentionally unavailable and raises `BackendBlocked`.

A future backend needs a supported, inspectable tool allowlist that excludes every file, command, network, app, MCP, patch and delegation capability, plus host-side verification of the effective catalog. A synthetic negative-capability test must establish enforced rejection independently of model obedience. Alternatively, discovery can run inside separately enforced access boundaries where protected labels, evaluator state, and credentials are genuinely unavailable. Neither route was installed or silently assumed here.

Once such a boundary exists, workers can receive only curated development summaries and evidence packets, return structured hypotheses or candidate code as text, and let the trusted parent review and execute approved code in a separately guarded experiment process. Final labels must remain accessible only to the independent evaluator. The backend must be revalidated after CLI/configuration changes, with executable and configuration hashes recorded.

Do not weaken readiness flags to launch a real campaign. Historical data acquisition and deterministic offline evaluation can continue independently while this backend gate remains unresolved.

## Reproducible checks

`python -m unittest discover -s tests -p test_codex_backend.py -v` runs local tests with mocked process calls. It never contacts the service. These tests cover restrictive arguments, removal of API-key overrides, schema validation, fail-closed assessment, event classification, two-attempt limits, timeout termination and disabled historical dispatch.

`run_synthetic_probe(executable, output_root, timeout_seconds=120)` is an explicitly network-capable acquisition/agent-lane diagnostic, not an offline experiment. It must never be imported as an automatic fallback by the experiment runner. Do not run additional probes without a newly adopted investigation budget.
