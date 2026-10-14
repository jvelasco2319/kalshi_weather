# Buying, recovery and current-policy replay

The current application keeps nine independent paper wallets, the same saved
weather models and native trading budgets. It has no real-order transport.

## What was repaired

| Repair | Result |
| --- | --- |
| Phoenix forecast clocks | Genuine inference completion must precede 17 UTC. Publishing a prepared forecast retains that clock; a late forecast cannot become a timely buy. |
| Concurrent wallet updates | Each wallet's native state and financial commit are serialized. Separate wallets share observed physical book capacity rather than each spending the same depth. |
| Restart recovery | Unattempted future orders can resume; expired unattempted orders can cancel; saved results can commit once. Uncertain attempts retain their reservations and stay visibly unknown. |
| Preparation and arrival scheduling | Weather, forecast preparation and book capture run ahead of their allowed slots. Up to 19 native NY/Miami basket arrivals can prepare concurrently. Original entry and arrival deadlines remain enforced. LA arrival books must be received at or after the native due time. |
| LA buying/accounting | Transport carries the original fee settings, partial-fill permission and budget-bound V8D quantity. Native/common cash, positions and fees are checked before commit. |
| Online/offline parity | New archive results use `CURRENT_LIVE_PARITY_v1`. F3's former forecast-based exit is disabled in this replay to match the current online application. Earlier history is preserved separately. |
| UI descriptions | The page shows actual buying windows, unconfirmed reservations, scheduler failures and actual filled versus requested quantities. Bet shading still follows filled positions. |

## LA's selling rule

LA retains **sell only when an accepted completed daily report invalidates the
position**. A forecast becoming less likely does not trigger an exit.

The currently verified weather producer requires the climate day to be
complete. It therefore normally cannot provide an intraday rescue before the
dated market closes. Checking every 30 seconds does not turn preliminary
hourly readings into final daily-high proof. A protective sale also needs a
positive supported bid; a report alone does not guarantee a sale.

The offline replay checks that report timing against the actual dated closing
times. Where a possible later-close opportunity needs books outside the saved
monitoring window, the replay stays unknown until that evidence exists. It does
not invent a sale or silently count it as holding to settlement.

## Validation and practical limits

The local acceptance suite passed 172 checks. Additional focused checks cover
all nine native buying paths, simultaneous wallet updates, restart stages,
official settlement and actual LA partial/budget-bound fills. Six isolated
offline controls and UI controls passed. A saved October 1 LA regression kept
the same 78 BUY contracts and $0.21 fees under the new exit policy.

Those controlled tests establish implementation behavior, not future trading
profit. Archived delivery times, actual exchange fills and future equivalence
between the physical weather-model target and TWC settlement remain conditional.
Native data, price, risk and probability screens can correctly prevent a trade.
The server must be running before the required capture and publication times;
missed online slots are not backdated. November needs an explicitly registered
checkpoint rather than an automatic October fallback.

To check the deployment-selected application and source gate after setup:

```powershell
.\.venv\Scripts\python.exe -B final_testing\verify_application.py
.\.venv\Scripts\python.exe -B -m unittest discover -s final_testing\tests -p "test_buying*.py"
```

Current release registrations and focused receipts are in `final_testing/evidence`.
The existing launcher uses the release paths and hashes in `deployment.json`.
