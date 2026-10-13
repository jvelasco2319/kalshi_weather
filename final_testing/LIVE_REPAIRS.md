# Four-city live paper repairs — October 13

The frozen weather weights, October checkpoints, native price/probability
screens, wallet caps, fees and historical offline results are preserved.
These are prospective operational repairs, saved as new application releases.

* NOAA downloads have bounded retries for throttling/transient errors. LA
  reuses fields only after original raw/index identity and point decoding pass;
  it requires the original 20 temperature points. No sample values or old
  receipt clocks are substituted for unavailable weather.
* Dated market definitions are retained at their genuine first complete
  receipt. Phoenix still requires those definitions and weather before 17 UTC.
  Phoenix's collector can read its own generated runtime JSON separately from
  hash-bound frozen source files. Its former source-map rejection discarded
  the frame and atmosphere even after all 44 fields had downloaded.
* Each city has a resident hash-bound worker. Preparation happens within five
  minutes before scheduled publication, retaining creation/input clocks.
  Publication and decisions must actually finish in the first second of the
  scheduled minute. V8D's impossible exact-nanosecond transport guard becomes
  that first-second window, preserving its 18 UTC entry and five-second arrival.
  LA's new native session begins at actual resident startup, so the first
  decision can consume a forecast actually published during that session.
  Phoenix's monitoring and preparation packets cannot enter trades before
  actual publication. Late assembly of older captured inputs stays explicitly
  in the monitoring lane. Every new action checks file identities; unchanged
  passing hashes can be reused for five seconds, with a full recheck whenever
  metadata changes, the short interval expires, or the periodic audit runs.
* All four contract terms specify the named station's daily climate maximum.
  A dated paper mapping requires at least three recent, distinct completed
  days, including yesterday, with equal independently retrieved NWS and TWC
  official highs. The mapping is a checked same-scalar paper hypothesis;
  future source differences/revisions and calibration transfer remain unproven.
  Missing or changed evidence blocks new entries. TWC remains the displayed
  settlement provider. No completed-day outcomes enter forecast features.
* The official public quadratic fee schedule and fresh series multiplier/
  fee-change receipts must match the frozen paper formula. When the public
  PDF rate-limits reads, the originally retained identical registered PDF is
  used with its original retrieval date. Native rounding remains explicit;
  no private-account fee precision or exchange fill is claimed.

These repairs cannot recover an actually missed online trade time. Historical
reconstruction remains in the separate Offline History tab. A missing quote,
insufficient depth, failed price screen or risk pause is a valid reason to
retain cash, rather than a defect to override. November still requires new
explicit checkpoints. No exchange order transport or account login is added.
