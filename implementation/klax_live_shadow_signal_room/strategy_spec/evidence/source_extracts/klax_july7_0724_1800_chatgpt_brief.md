# KLAX Kalshi + Model Brief for ChatGPT

## Scope
- Target market date: 2026-07-07
- Time slice analyzed: 2026-07-07 07:24am PT through 06:00pm PT
- Kalshi event: KXHIGHLAX-26JUL07
- Full market window: 2026-07-06 07:00am PT through 2026-07-08 12:59am PT
- Winning Kalshi bracket: 73-74 F, ticker KXHIGHLAX-26JUL07-B73.5
- Decimal observed KLAX high: 73.9 F at 2026-07-07 1:53pm PT from KLAX METAR T02330150
- Observed high-so-far during slice: 64.94 F at 7:24am PT to 73.94 F by 6pm PT

## Source files
- Kalshi candles: data\processed\klax_temperature_history\kalshi_market_history\2026-07-07\klax_kalshi_market_history_1m_2026-07-07.csv
- Kalshi trades: data\processed\klax_temperature_history\kalshi_market_history\2026-07-07\klax_kalshi_trades_2026-07-07.csv
- Kalshi markets/results: data\processed\klax_temperature_history\kalshi_market_history\2026-07-07\klax_kalshi_markets_2026-07-07.csv
- Bot model estimates: data\processed\klax_temperature_history\klax_bot_model_estimates_2026-07-07_0600_1800_pt.csv
- Bot observations: data\processed\klax_temperature_history\klax_bot_model_estimates_2026-07-07_0600_1800_pt_observations.csv

## Market Summary, 7:24am-6pm PT
| ticker                  | bracket   | result   |   7:24_yes_bid |   7:24_yes_ask |   7:24_price |   18:00_yes_bid |   18:00_yes_ask |   18:00_price |   min_yes_bid |   max_yes_bid |   min_yes_ask |   max_yes_ask |   candle_volume |   trade_count |   min_trade_yes |   max_trade_yes |
|:------------------------|:----------|:---------|---------------:|---------------:|-------------:|----------------:|----------------:|--------------:|--------------:|--------------:|--------------:|--------------:|----------------:|--------------:|----------------:|----------------:|
| KXHIGHLAX-26JUL07-B69.5 | 69-70 F    | no       |           0    |           0.01 |       nan    |            0    |            0.01 |          0.01 |          0    |          0    |          0.01 |          0.01 |        10065.5  |           192 |            0.01 |            0.01 |
| KXHIGHLAX-26JUL07-B71.5 | 71-72 F    | no       |           0    |           0.01 |       nan    |            0    |            0.01 |        nan    |          0    |          0    |          0.01 |          0.01 |        34333.9  |           308 |            0.01 |            0.01 |
| KXHIGHLAX-26JUL07-B73.5 | 73-74 F    | yes      |           0.05 |           0.06 |         0.06 |            0.99 |            1    |          0.99 |          0.02 |          0.99 |          0.03 |          1    |       117419    |          5504 |            0.01 |            0.99 |
| KXHIGHLAX-26JUL07-B75.5 | 75-76 F    | no       |           0.5  |           0.51 |         0.51 |            0    |            0.01 |          0.01 |          0    |          0.76 |          0.01 |          0.77 |        87575.3  |          5117 |            0.01 |            0.77 |
| KXHIGHLAX-26JUL07-T69   | <69 F      | no       |           0    |           0.01 |       nan    |            0    |            0.01 |        nan    |          0    |          0    |          0.01 |          0.01 |         1033.63 |             5 |            0.01 |            0.01 |
| KXHIGHLAX-26JUL07-T76   | >76 F      | no       |           0.46 |           0.48 |         0.48 |            0    |            0.01 |          0.01 |          0    |          0.77 |          0.01 |          0.78 |       153246    |          3691 |            0.01 |            0.79 |

## Model Summary, 7:24am-6pm PT
| model_key                      |   rows | first_time   |   first_est_f | first_bracket   | last_time   |   last_est_f | last_bracket   |   mean_est_f |   mean_abs_error_vs_73_9 |   pct_bot_bracket_73_74 |   pct_raw_est_73_to_74 |
|:-------------------------------|-------:|:-------------|--------------:|:----------------|:------------|-------------:|:---------------|-------------:|-------------------------:|------------------------:|-----------------------:|
| noaa_herbie:nbm                |    210 | 07:24:00     |         72.59 | 73-74           | 17:03:28    |        73.94 | 73-74          |        73.32 |                     0.6  |                   100   |                   57.1 |
| open_meteo:gfs013              |    247 | 07:24:00     |         75.9  | 75-76           | 17:58:28    |        73.94 | 73-74          |        74.61 |                     0.71 |                    62.8 |                   17   |
| open_meteo:gfs_global          |    246 | 07:24:00     |         75.9  | 75-76           | 17:58:28    |        73.94 | 73-74          |        74.61 |                     0.71 |                    62.6 |                   16.7 |
| current:current_weighted_blend |    248 | 07:24:00     |         76.13 | 75-76           | 17:58:28    |        73.94 | 73-74          |        74.81 |                     0.95 |                    62.5 |                   49.2 |
| synthetic:consensus_median     |    248 | 07:24:00     |         76.1  | 75-76           | 17:58:28    |        73.94 | 73-74          |        74.85 |                     0.99 |                    62.5 |                   48.8 |
| open_meteo:gfs_seamless        |    248 | 07:24:00     |         76.3  | 75-76           | 17:58:28    |        73.94 | 73-74          |        75.01 |                     1.15 |                    52   |                   52   |
| open_meteo:best_match          |    247 | 07:24:00     |         76.3  | 75-76           | 17:58:28    |        73.94 | 73-74          |        75.01 |                     1.16 |                    51.8 |                   51.8 |
| noaa_herbie:hrrr               |    219 | 07:24:00     |         76.06 | 75-76           | 17:58:28    |        73.94 | 73-74          |        75.05 |                     1.34 |                    48.4 |                   48.4 |
| noaa_herbie:gfs                |     68 | 08:36:39     |         75.29 | 75-76           | 16:17:11    |        75.15 | 75-76          |        75.25 |                     1.35 |                     0   |                    0   |
| noaa_herbie:rap                |    201 | 07:24:00     |         82.8  | >76             | 16:59:34    |        75.45 | 75-76          |        79.55 |                     5.68 |                     0   |                    0   |

## Key market behavior
- At 7:24am PT, the eventual winner 73-74 F was cheap: yes_bid=0.05, yes_ask=0.06.
- At 7:24am PT, hotter outcomes were priced much higher: 75-76 F yes_ask=0.51 and >76 F yes_ask=0.48.
- Winner 73-74 F repriced upward through the day: ask about 0.27 at noon, 0.62 at 1:53pm when the decimal high was reached, 0.84 at 3pm, and 1.00 by 6pm.
- The market strongly shifted from hot brackets to the 73-74 F bracket during the target-day session.

## Key model behavior
- NBM was the best early signal in the bot data: its bot-estimated bracket was 73-74 for 100% of successful rows in this slice.
- Most other models started too warm around 75-76 or >76 at 7:24am and converged later.
- GFS and RAP stayed too warm in the available bot data.
- Open-Meteo GFS013/GFS Global and the weighted/consensus blends converged to 73-74 by the end of the slice.

## Backtest caveat
Kalshi data here is one-minute top-of-book candlestick history plus trades. It is enough for a conservative taker-style backtest using YES ask for buys and YES bid for sells, but it is not historical full-depth orderbook data, so it cannot prove queue position, maker fills, or slippage through depth.
