# Security and Fail-Closed Behavior

- Keep API keys outside config files and journals.
- Never include credentials in raw payload archives or exception messages.
- Use the repository's existing signing/authentication implementation.
- Separate read-only market-data credentials from future order permissions where possible.
- The shadow process must receive a non-submitting order sink, not a live client with a boolean guard.
- Persist configuration hashes but not secrets.
- Reject clock-skewed, future-dated, stale, ambiguous, or incomplete source states.
- Reject unknown settlement rules and fee schedules.
- Invalidate the book on disconnect or sequence gap.
- Cancel future resting orders on pause by default when canary work is separately approved.
- Add a process-level kill file/environment switch and an account reconciliation mismatch kill switch.
- Do not enable live code in this implementation run.
