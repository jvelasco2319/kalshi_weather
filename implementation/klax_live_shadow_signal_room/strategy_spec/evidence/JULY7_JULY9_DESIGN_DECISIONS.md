# July 7 and July 9 Design Decisions

## July 7

- The winning bracket was 73–74°F.
- NBM mapped to that bracket throughout the supplied successful slice while hotter models converged later.
- The winner was available at low prices early, demonstrating the potential value of a minority model signal.
- The old historical full-day scalar could retain a past forecast maximum late in the day.
- Some nominal checkpoint summaries used source timestamps after the checkpoint, so strict backward as-of selection is mandatory.

Design consequences:

- use remaining forecast hours plus observed maximum;
- preserve model-specific probability distributions;
- include NBM with maturity-controlled influence;
- forbid nearest-time joins.

## July 9

- The price of the eventual winner moved from an economically interesting range to a range where a 10%–15% return could become mathematically impossible.
- Trade quantity was missing from every exported row.
- Candles did not provide synchronized executable depth.
- Corresponding model snapshots were absent.

Design consequences:

- evaluate continuously on events, not only hourly;
- enforce a dynamic exact price ceiling;
- require `count_fp`, cursor completion, and sequence-valid books;
- fail closed when model/market state is incomplete;
- distinguish forecast accuracy from executable profitability.
