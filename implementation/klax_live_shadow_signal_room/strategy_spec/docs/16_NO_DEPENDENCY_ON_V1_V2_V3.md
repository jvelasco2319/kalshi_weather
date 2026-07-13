# No Dependency on v1, v2, or v3

The user did not implement the older packages. Codex must not:

- copy their source trees into the repository;
- create migration tables solely for their strategy IDs;
- reproduce their historical full-day calibration baseline;
- require their feature flags or configuration files;
- implement their old consensus before implementing this package;
- combine conflicting amendment documents.

Useful ideas have already been consolidated here: fixed-point fees, conservative probabilities, remaining-window state, event-level risk, sequence-valid books, and shadow-first deployment.

The only migration is from the repository's current behavior to the current five-model strategy. Keep old commands working, but the new strategy gets its own versioned config and decision records.
