# V5 economics evidence acquisition

This directory contains a finite, outcome-blind acquisition of public official
evidence needed by the V5 Fee and Settlement Integrity colony. The acquisition
window is January 1, 2025 through August 31, 2026, covering both the exposed
2025 development interval and the registered V5 confirmation pool.

`acquire_official_evidence.py` requests only KXHIGHLAX series metadata, event
metadata without nested markets, fee-change histories, and bounded official
documents. It never requests market outcomes, protected labels, private account
data, orders, or fills. Raw source documents and filtered API manifests are
content addressed in `manifests/source-manifest.json`.

The archived Kalshi fee PDFs are immutable Internet Archive captures of the
official Kalshi source URL. They are retained because the live URL is replaced
when Kalshi publishes a new schedule. Archive provenance is explicit and is not
treated as a live Kalshi response.

Acquisition uses the network once. Subsequent economics validation must use the
saved hashes and run offline.
