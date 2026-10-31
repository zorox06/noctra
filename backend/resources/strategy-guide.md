# NOCTRA — standalone Python strategy brief for a cloud LLM

Use this document together with the user's trading idea to generate a compatible Python strategy for Noctra. This brief is intended to be uploaded to Claude or another cloud LLM. It contains the runtime interface, available data, execution rules and a dated data inventory; no repository or application source files are supplied or needed to write the strategy.

The cloud LLM authors the Python file and may analyze separately attached historical files if its tools support execution. The user then runs it in Noctra on their computer, where the historical data and backtest engine are available. The attached inventory describes that runtime; it does not give the cloud LLM access to candle files, a running app, live account balances or backtest results. Use the specified `data` input rather than local paths or cloud downloads. Without actual execution evidence, label the strategy untested and do not invent performance, validation or a guaranteed win rate.

## Deliverable to the user

Return one complete, self-contained `.py` file defining `strategy(data, params)` and a module-level `BACKTEST` dictionary. Include explicit, strategy-specific choices for starting capital, leverage and allocation, with the user-specified trading costs below; explain the reasoning briefly alongside the file. Do not include Markdown fences inside the Python file. Keep adjustable parameters and absolute price-level rules in the file. There is no separate Test setup, stop/target percentage form or Parameters JSON form.

Honor the user's risk constraints. Where they have not specified sizing or leverage, choose and justify them for the proposed strategy instead of prescribing a universal leverage or full-account allocation. Follow the sizing capabilities and limits below. Explain the entry, stop, target and ratio rules, warm-up, handling of proxy data, selected symbol/timeframe and any unsupported requirement. Distinguish assumptions from measured results.

The user pastes/imports the Python file and clicks Run backtest. Market overview selects BTCUSD/ETHUSD and the timeframe. The run uses the full stored history, not only visible chart candles; optional UTC date boundaries belong in `BACKTEST`. All new UI backtests use absolute-price risk/reward brackets.

## Confirmed costs and research choices

The user has specified **fee_bps = 7** and **slippage_bps = 1**, each **per fill**. Set `BACKTEST["fee_bps"] = 7` and `BACKTEST["slippage_bps"] = 1` in generated strategy files. This means a 0.07% notional fee and 0.01% adverse price slippage on each entry/exit fill. They are the user's simulation assumptions, not a claim about the exchange's historical fee schedule. Do not leave `[X]`/`[Y]` placeholders or ask for these values again. Current source-only engine fallbacks also use these costs; explicit later user overrides and sensitivity scenarios can differ.

Choose and name one executable instrument, `BTCUSD` or `ETHUSD`, and a supported timeframe in the explanation if the user has left those open. Label this as the strategy author's choice; do not return an unresolved `[BTCUSD / ETHUSD / BTC-ETH ratio]` template. BTC/ETH ratios and lead-lag may be features for that instrument, but a ratio is not a separate executable product here. Set `bar_seconds` in `BACKTEST["parameters"]` if timing logic needs it; the user selects the matching chart instrument/timeframe. Leverage and allocation remain strategy-specific choices, not fixed recommendations.

## Absolute prices and the meaning of 1:x

Risk/reward compares **price distances**, not a required percentage move. Let the actual entry fill be E and the absolute stop price be S. Initial distance D = abs(E − S). For requested reward multiplier x, target T = E + xD for a long or E − xD for a short. D is fixed at entry, not recalculated as the market moves.

- Short entry 100, stop 100.5, target 98.5: risk 0.5 and reward 1.5, so **1:3**.
- Short entry 100, stop 100.5, requested ratio 1:2.5: target **98.75**.
- Long entry 100, stop 99.5, requested ratio 1:2.5: target **101.25**.

These explain the encoding; they are not a preloaded trading strategy. There is no implicit 1% stop. Code may choose different absolute stops and different ratios (1:2, 1:2.5, 1:3, etc.) for different setups. Any confidence assessment must be causal and is not certainty that the trade will win.

## Exact return contract

Function: `def strategy(data, params):` where params is BACKTEST['parameters'] or `{}`.

Return a pandas DataFrame with exactly len(data) rows, preserving data.index and row order:

| Output column | Meaning |
| --- | --- |
| `signal` | Exactly 1 for a long entry intention, -1 for short, 0 for no entry intention |
| `stop_loss` | Absolute positive USD price, required on each new nonzero signal edge |
| `take_profit` | Optional absolute positive USD target price. If present on an entry row, it takes priority; the realized planned ratio is calculated from that target and the actual entry fill. |
| `risk_reward` | Optional positive multiplier such as 2, 2.5, 3, or a string such as "1:2.5". Used when the entry row has no take_profit. Falls back to BACKTEST['risk_reward'] when absent/empty. |

Each candidate entry needs stop_loss and either a take_profit value or a valid risk_reward value/fallback. If a target price and ratio are both supplied, the absolute target takes priority; do not claim the other ratio is enforced. Non-entry rows may use NaN/None for bracket columns. Signal warm-up rows must be 0, never NaN/infinite. Price/ratio values on actual entry edges must be finite and positive. Do not return prices, quantities or probabilities as signal values. A plain signal Series or a DataFrame containing signal alone no longer meets the new ratio contract.

Example of return shape only: `pd.DataFrame({"signal": entries, "stop_loss": absolute_stop_prices, "risk_reward": chosen_multipliers}, index=data.index)`. All arrays must have len(data) rows; compute them from completed candles and your strategy rules. Use pandas/NumPy already available in the worker.

## Entry and exit behavior

A new nonzero signal edge is an entry intention: the current signal is nonzero and differs from the preceding signal (initial preceding signal is 0). It is first eligible at the **next candle open**, with configured slippage. Do not shift outputs again. An unchanged 1 or -1 does not generate repeated entries after a bracket closes. Return 0 between same-side entry pulses to enable a later independent setup. A direction change while flat can also be a new intention.

While a bracket is open, all later entry intentions are ignored and **not queued**. A 0 does not flatten the trade; -1 does not reverse an open long; 1 does not reverse an open short. Stop and target remain frozen at their initial absolute prices. The normal exit is whichever bracket boundary is reached. Trailing/moving stops, early time exits, partial exits and simultaneous positions are outside this fixed-ratio mode.

The actual next-open fill E is not necessarily the signal candle's close. Validate stops/targets relative to E: long S < E < T, short T < E < S. In new UI runs, if the stop/target was valid at signal close but the next-open price or adverse slippage crosses it, the engine **skips that entry and continues**. It never moves the strategy levels, invents an entry fill, opens a position or charges a fee for that rejection. This applies to long and short entries, including a target crossed before entry. Rejected entries are recorded with signal row/time, side, signal close, attempted open/fill and requested levels; the UI shows their count and exports retain the full records. An unchanged signal is not retried: a fresh signal edge is required. If levels were already invalid at signal close, or malformed/nonfinite, the engine still raises an explanatory strategy error. Saved older runs retain their recorded strict entry-gap policy. When a multiplier is used, T is computed from E and S, so its planned distance ratio is retained. When absolute T is supplied, the recorded planned R:R reflects E, S and T after the entry fill.

Known opening prints are processed before the bar high/low for a held bracket. A gap through the stop exits at the adverse open and may lose more than 1R. An opening gap beyond the target fills conservatively at the target, without assuming favorable price improvement. Otherwise, if both stop and target touch within one OHLC bar and ordering is unknown, stop-first ordering is used. Entry-bar brackets are active immediately. New entry intentions observed while the prior trade was open are not replayed on that trade's exit bar.

An open position at the final test candle is closed for accounting and marked `end_of_test`, `bracket_closed=False`. It is an **incomplete ratio trade** and can have any realized R. Do not replace that result with a fabricated full target or stop outcome.

## R units, costs and reports

Initial 1R in USD = filled quantity × initial entry-to-stop price distance. Gross R = simulated gross P&L / initial 1R. Fees R = total fill fees / initial 1R. Net R = net P&L / initial 1R. Quantity is rounded down to whole contracts. These R units are per-trade risk multiples, not percentage returns, and summed R is not a percentage equity return.

The plan is a distance ratio. Fees and adverse slippage reduce net outcomes; gaps can exceed the initial loss distance. A planned 1:2.5 does not guarantee a net +2.5R winner or a net -1R loss. The engine never changes fills to force that result and never guarantees a win rate. Reports retain actual fills, stop/target, requested ratio (when used), planned ratio, initial risk, gross/fees/net R, target/stop counts and incomplete test-end exits. Evaluate expectancy in R and account results using actual outcomes.

## Exact columns passed to `data`

These are the base input columns. New UI backtests also receive the additional research columns listed below. Column names are case-sensitive.

| Column | Type / meaning |
| --- | --- |
| `time` | Integer Unix timestamp in **seconds**, the candle's opening time |
| `datetime` | UTC timezone-aware pandas timestamp derived from `time`; also retained as a column |
| `open` | Floating-point opening last-trade price, USD quote units |
| `high` | Floating-point highest last-trade price during that candle |
| `low` | Floating-point lowest last-trade price during that candle |
| `close` | Floating-point last-trade close price |
| `volume` | Native rows: Delta-reported volume, with its original units. Proxy rows: an estimate on that scale, equal to reference base volume × the frozen earlier-overlap volume factor. Never describe this as observed Delta volume. |
| `source` | `delta` for original rows, `delta_1m` for recovered complete native minute aggregates, or `binance_proxy` for adjusted reference candles |
| `is_proxy` | Boolean. True means OHLC/volume are reference estimates; false means native Delta data. Supplied even for unrepaired datasets. |

The index is a UTC `DatetimeIndex` named `datetime`. `datetime` exists both as the index and as a column. Use `data["datetime"]` or `data.index` consistently; reset/rename the index before grouping by a label that would otherwise be ambiguous. The selected symbol and timeframe are UI/dataset metadata, **not additional DataFrame columns**. If code needs a timeframe length, put an explicit `bar_seconds` value in `BACKTEST["parameters"]` or a source constant.

Although `time` labels the candle open, its OHLCV is used only after the candle is complete. Do not treat a current row's close/high/low as information available at that row's open. For session-end timing, derive the close timestamp using the actual selected timeframe.

## Real historical research data joined into `data`

New UI backtests automatically create a frozen, compressed research-input snapshot before running the strategy. Existing BTCUSD/ETHUSD trade candles and Delta's hourly mark, funding, open-interest and spot-index observations are joined into `data`. They are usable by a cloud-authored strategy without repository access, file reads, API calls or manual joins. Each run saves the exact input snapshot ID, checksum, source-file hashes and column coverage; subsequent analyses reuse the same observations.

There are 44 runtime columns: the nine base columns above plus the following 35 research columns. The frozen Parquet has 43 columns; the worker creates `datetime` from `time`. The cloud CSV export also includes `datetime`.

These are additional columns beyond the base table above:

| Columns | Meaning |
| --- | --- |
| `bar_close_time` | Unix seconds UTC: selected candle opening `time` plus the selected timeframe duration; inputs are observable at this decision time |
| `btc_open`, `btc_high`, `btc_low`, `btc_close`, `btc_volume` | BTCUSD OHLCV for exactly the selected row's opening timestamp and timeframe |
| `eth_open`, `eth_high`, `eth_low`, `eth_close`, `eth_volume` | ETHUSD OHLCV at exactly the same timestamp/timeframe |
| `btc_source`, `eth_source` | Candle provenance: `delta`, `delta_1m` or `binance_proxy`; missing when that asset has no corresponding candle |
| `btc_is_proxy`, `eth_is_proxy` | Whether each asset's candle is an adjusted reference estimate; missing is not proof that it is native |
| `btc_available`, `eth_available` | Boolean: the corresponding asset has a candle at this exact timestamp |
| `cross_asset_available` | Boolean: both asset candles are available at this timestamp |
| `btc_eth_ratio` | `btc_close / eth_close`, or missing without both prices. This is a derived feature, not a traded pair or a hedge portfolio return. |
| `btc_mark_price`, `eth_mark_price` | Latest eligible completed hourly mark-price candle close, USD |
| `btc_funding_rate`, `eth_funding_rate` | Latest eligible completed hourly funding-rate candle close, in the raw Delta-reported rate units; not a funding-payment ledger |
| `btc_open_interest`, `eth_open_interest` | Latest eligible completed hourly open-interest candle close in Delta's original reported units; do not assume USD notional or base quantity without a verified conversion |
| `btc_index_price`, `eth_index_price` | Latest eligible completed hourly spot-index candle close, USD |
| `btc_mark_price_available_at`, `eth_mark_price_available_at` | Unix seconds when the supplied hourly mark close became eligible |
| `btc_funding_rate_available_at`, `eth_funding_rate_available_at` | Eligibility timestamps for the supplied funding closes |
| `btc_open_interest_available_at`, `eth_open_interest_available_at` | Eligibility timestamps for the supplied open-interest closes |
| `btc_index_price_available_at`, `eth_index_price_available_at` | Eligibility timestamps for the supplied index closes |

**Causal alignment:** cross-asset trade candles match exact opening times and durations; no stale candle is carried into a missing timestamp. Hourly auxiliary closes become eligible at their opening timestamp plus 3,600 seconds. The join selects only observations whose eligibility timestamp is at or before `bar_close_time`, with age strictly less than 3,600 seconds. It never backward-fills from future observations or carries an old hourly close across a missing hourly observation. A 15m candle opening at 10:00 closes at 10:15: it can see the 09:00–10:00 hourly auxiliary close, not the 10:00–11:00 close. The 10:45–11:00 candle may use the latter at its 11:00 decision time.

**Missing values are deliberate:** before an asset's launch, outside overlap, or during missing/stale auxiliary intervals, numeric values are NaN and source labels may be missing. Neutralize entries that require absent observations. Do not fill future prices/rates backward or replace missing OI/funding with fabricated zeros. The availability timestamps allow explicit age checks. Proxy candle prices and volumes retain their flags; auxiliary observations are native Delta series, with no estimated liquidation or funding events added.

BTC/ETH ratio, causal spread z-scores, rolling hedge-ratio estimates and lead-lag features can now be calculated from `btc_close` and `eth_close` on overlapping history. Fit all coefficients/thresholds using information available by the decision row. If a fitted spread is used for an IC study, freeze its coefficients consistently for each prediction/label; forward refitting must not redefine the historical feature. Execution still trades the chart-selected instrument only. A true dollar-neutral two-leg pair portfolio, simultaneous hedge orders and shared-cash pair P&L remain unsupported.

Funding, OI, basis and index/mark dislocation can be **signal features** using the supplied columns. Funding payments are still excluded from simulated P&L. Mark/index basis must use observations with understood availability and units; a repeated hourly rate is not a new independent funding settlement every 5m/15m bar. There are no generic `funding`, `oi`, `mark_price`, `index_price` aliases: use the exact asset-prefixed names above.

## What the research requires and what is available

| Requirement | Actual data/capability and instructions |
| --- | --- |
| BTCUSD/ETHUSD prices and volume | Archived trade OHLCV, including supported 5m and 15m history; both assets' aligned columns are now provided. Native rows and adjusted gap estimates are labeled separately. |
| Session timing and prior-day levels | Derive from UTC timestamps and completed candles; define the intended session/time zone explicitly. Prior-day levels must use the prior completed day. |
| VWAP/volume seasonality | A candle-volume-weighted typical-price approximation can be calculated from OHLCV. Label it a candle approximation, not exact trade-level VWAP. Compare volume in consistent units within each asset/source, with a stated proxy policy. |
| Higher-timeframe trends | Causally aggregate completed selected-timeframe candles where the duration divides the higher frame, then align only after completion; do not expose a still-forming 4H/daily candle. |
| Cross-asset ratio, spread, hedge ratio and lead-lag | Real aligned BTC/ETH candle inputs are available. Derived features are allowed; paired execution is not. Use overlap/validity filters and trailing or properly frozen estimates. |
| Funding and open interest | Real native hourly observations are joined with causal timestamps and missing-value handling. Rate/OI units are retained; funding cashflows are not simulated. |
| Liquidations | No historical market-wide liquidation tape has been obtained or archived. OHLCV, mark, funding and OI cannot reconstruct actual liquidation events. Do not claim these are available or create liquidation columns from proxies. A verified timestamped historical event dataset with venue, symbol, side, price, quantity/unit and coverage would be required. |
| Order flow, bid/ask, depth, aggressor volume and CVD | No historical trade-direction/depth dataset is supplied. A real timestamped trade/order-book archive with sequence/coverage information would be needed; current ticker snapshots cannot fill historical features. |
| Hard 45-minute / 3-bar time stop | Not an input-data problem: this fixed-bracket execution mode has no early time exit. Do not claim a tighter bracket enforces a holding deadline. Disclose the incompatibility if it is a requirement. |
| Dynamic sizing/leverage or two-leg hedging | Not supported by this execution interface. Explain the per-run exposure choice and one-instrument execution limits without pretending they implement the missing features. |
| Measured IC, regime, walk-forward and cost results | Need actual observations plus execution of the appropriate research code. A cloud LLM given only this Markdown has an inventory/schema, not the candle records. If data files are attached and the cloud tool can execute analysis, use those files, report actual coverage and compute results; otherwise provide runnable analysis code and label results unmeasured. |

For IC research, define the feature's economic rationale, causal lookback, prediction horizon, forward-return label, rank/Pearson metric and sample counts. Forward returns belong only in evaluation labels, never in strategy features or fitted full-sample normalization. Use chronological train/validation/test splits, purge at least the forward-label overlap at boundaries, freeze any fitted thresholds/regime definitions using past training data, and show out-of-sample results separately. Explain any extra embargo. A hard time horizon for a label is not a timed execution exit. For trading results, include the specified costs, actual R outcomes, exposure assumptions and proxy/missing-data sensitivity. Do not describe historical IC or a regime filter as a guarantee of a winning trade.

## Instruments, timeframes and attached inventory

- Venue: Delta Exchange India public market data; instruments `BTCUSD` and `ETHUSD` perpetuals only.
- Supported candle selections: `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `1d`, `1w`.
- Native candle histories are archived separately. Complete minute groups can be aggregated when necessary. Missing interior timestamps may use labeled gap-repair overlays; original Delta candles are preserved. Remaining primary candle gaps block a backtest.
- Hourly auxiliary raw streams use `time`, `open`, `high`, `low`, `close`, `volume`; their kind identifies the observation. The strategy receives their causally joined **close** values and eligibility times, not automatically all raw auxiliary OHLCV columns. Auxiliary `volume` is not assumed to be traded volume.
- Historical liquidation tape, order-book depth and aggressor-trade/CVD history are not archived. Private account liquidation fills are not market-wide liquidation history.

The dated inventory appended below is generated from the actual archive. It distinguishes raw archive coverage, repaired primary snapshots and frozen joined research inputs. Dates and row counts are measured stored coverage, not a promise of all venues or all dates. Input files remain on the user's computer. Local download links must be opened by the user and files attached separately if the cloud LLM needs to inspect actual records. The cloud LLM cannot access the user's `127.0.0.1` links itself. A supplied CSV/Parquet research packet contains observations; this Markdown alone does not.

Public source reference: [Delta historical OHLC API](https://docs.delta.exchange/#get-historical-ohlc-candles). The archive uses trade symbols and the API's `MARK:`, `FUNDING:` and `OI:` series, plus the product's spot index. This reference does not establish the existence of a public historical liquidation-event feed.

### Funding-gap retrieval check

On 2026-10-23, all 21 observed interior funding gaps (50 BTC hourly observations and 46 ETH hourly observations) were re-requested from Delta using its public historical funding endpoint. Every requested gap returned an empty result. These observations remain missing; no rates were invented or substituted from a different venue. Their affected strategy rows are excluded by a funding-dependent validity filter, not automatically treated as zero funding.

## Missing-candle repair and estimation policy

Yahoo was queried for the actual 2024 five-minute gaps and rejected them because its intraday retention is limited. Gap repairs use Binance USD-M BTCUSDT/ETHUSDT perpetual candles, not Yahoo daily candles, interpolated prices, copied adjacent bars or fabricated volume. USDT/USD basis and venue differences remain estimation risks.

Only missing interior timestamps are inserted. Original Delta OHLCV and historical snapshots stay unchanged. The backtest uses a new snapshot with its own checksum; future native Delta candles take priority over older estimates. Reference candles are cached locally; no network request is required to reuse a repaired snapshot.

For each gap, calibration uses at most 2,000 earlier native Delta candles within max(7 days, 2,000 bars), matched to reference timestamps strictly before the gap. When at least 12 matched bars have positive Delta volume, price fitting uses those active bars; otherwise all earlier matches are used and sparse activity is flagged. The median Delta-close/reference-close ratio multiplies all four reference OHLC prices. The ratio of summed Delta volume to summed reference volume on earlier matched bars scales reference volume. This is an empirical activity estimate combining units and venue share, not a proven conversion or reconstructed trade ledger. Zero earlier Delta volume produces zero scaled volume and a warning; original nonzero reference volume remains in saved repair evidence. At least 12 matched earlier candles are required. No later Delta prices or volumes are used to fit a past gap.

Each repair saves raw reference OHLCV, factors, matched/active sample counts, median price difference, adjusted residual median/p90 and sparse/unstable-fit warnings. These diagnostics describe the calibration window, not a guarantee of error during the gap. A long outage or quiet Delta market can make the proxy unreliable. The run assumptions and dataset manifest disclose proxy counts; exports retain them. The original raw lake inventory below continues to report original Delta gaps; repaired snapshots are a separate layer.

Strategies may inspect `data["is_proxy"]` and `data["source"]`. State explicitly how estimated bars affect indicators and trading. Do not treat proxy volume as actual Delta contracts, or report a mixed-source backtest as if every candle were native exchange history.

## Available Python names and helpers

The worker supplies `pd` (pandas), `np` (NumPy), `ema(series, period)`, `sma(series, period)` and `rsi(series, period=14)`.

- `ema`: pandas exponential mean with `span=int(period)`, `adjust=False`.
- `sma`: pandas rolling mean with window `int(period)`; early values are NaN, so neutralize warm-up signals.
- `rsi`: exponential gain/loss smoothing with `alpha=1/int(period)`, `adjust=False`; zero-loss cases map to 100, remaining undefined values to 50. It is not guaranteed identical to another platform's RSI initialization.
- Validate indicator periods as positive integers inside the strategy. Explicitly handle insufficient history.
- Normal Python imports work only for packages installed in the app's `.venv`. Do not assume TA-Lib, vectorbt, ccxt, scipy or other packages are installed. Prefer pandas/NumPy unless another dependency is verified.
- Code executes in a child process with a 60-second limit, at the user's normal Windows filesystem/network permissions. It is not a hardened security sandbox. Keep the source self-contained; do not place orders, call account APIs, read secrets, install dependencies, launch subprocesses or use the internet from a strategy.

## Causality and repeatability requirements

Use current and prior completed bars only. No negative shifts, centered rolling windows, future slices, backward filling from future data or whole-dataset fitted normalization. For thresholds and z-scores, use trailing or expanding statistics known at that row. When resampling higher timeframes, use completed higher-timeframe bars and backward alignment at their actual availability time. Do not forward-fill a still-forming higher-timeframe candle into earlier rows.

The worker calls `strategy` on the complete input and again on its first approximately 75% of rows. The earlier signals and entry stop/target/ratio values must match exactly. Avoid global mutable state, wall-clock decisions and unseeded randomness. If a random procedure is required, describe its causal/reproducible design; merely passing the prefix check does not prove there is no look-ahead bias.

Use vectorized pandas/NumPy for performance where appropriate. Stateful Python loops are supported but must complete within the timeout. Do not place per-row REST or file requests in a strategy. Backtesting, optimization and walk-forward checks can execute the source repeatedly.

## Choose and explain risk management for this strategy

There is no prescribed leverage or allocation policy in this brief. Let the user's requirements and the proposed strategy determine the choices. Include explicit `capital`, `leverage`, `allocation_pct`, `fee_bps` and `slippage_bps` values in `BACKTEST`, and explain their assumptions. If capital was not supplied, choose and label a hypothetical research balance; it is not the user's exchange balance. Use the user-specified 7 bps fee and 1 bp slippage per fill; do not replace them with unspecified assumptions. Omitted settings inherit engine fallbacks, which are not a risk-management recommendation; generated strategies should make these choices explicit.

`allocation_pct` is the percentage of current simulated cash used as **pre-leverage notional**, not the percentage of equity risked at the stop. Approximate entry notional = current cash × allocation_pct / 100 × leverage. Entry quantity = that notional / slipped entry fill, rounded down to whole contracts. Initial dollar risk = filled quantity × abs(entry fill − absolute stop). Approximate initial risk as a fraction of cash, before rounding and costs, is `(allocation_pct / 100) × leverage × abs(entry fill − stop) / entry fill`. A price-distance ratio alone does not control account risk.

Explain how the chosen exposure and expected stop distances affect risk, and account for fees, slippage and adverse gaps when discussing limitations. Do not describe full-account allocation as automatically safe or required. Do not claim an exact account-risk limit is enforced merely by choosing a ratio or leverage. If stop distances vary, actual risk at the stop varies even when the notional allocation is unchanged.

**Current sizing capability:** `leverage` and `allocation_pct` are each one constant setting for a backtest run, chosen by the strategy author. Position quantity is recalculated from current cash on each entry using those settings. The strategy function receives candles and parameters, not live cash, fills or account state. Per-row quantity, leverage, allocation or risk-budget columns are not supported sizing instructions. Dynamic leverage, equity-aware per-trade sizing and exact fixed-account-risk sizing are not currently available through this interface. If the user's idea requires them, identify that limitation explicitly and describe a compatible per-run sizing approximation only if appropriate; do not claim the unsupported policy was implemented.

## Settings inside the Python file

| `BACKTEST` key | Accepted value / purpose |
| --- | --- |
| `name` | Nonblank strategy name, at most 100 characters |
| `capital` | Explicit chosen starting cash in USD, 100–100,000,000 |
| `leverage` | Explicit chosen exposure multiplier, 0.1–5; constant within a run |
| `allocation_pct` | Explicit chosen pre-leverage notional allocation as a percentage of current cash, greater than 0 and at most 100; constant percentage within a run |
| `fee_bps` | Set to **7** basis points per fill, as specified by the user; accepted engine range 0–100 |
| `slippage_bps` | Set to **1** basis point of adverse slippage per fill, as specified by the user; accepted engine range 0–100 |
| `risk_reward` | Optional positive fallback multiplier or ratio string such as "1:2.5"; used only on entry rows without `take_profit` and without a per-row `risk_reward` |
| `parameters` | Dictionary passed to `strategy` as `params`; use `{}` if no adjustable parameters are needed |
| `start_time` | Optional inclusive nonnegative Unix seconds UTC; omission uses the dataset start |
| `end_time` | Optional exclusive nonnegative Unix seconds UTC; must follow `start_time` when both are present; omission uses the dataset end |

One basis point is 0.01%. These settings do not supply stop prices or a hidden target: the returned DataFrame must provide absolute bracket information. Legacy `stop_pct`, `target_pct` and `risk_pct` are not accepted `BACKTEST` keys. Absolute stops and targets belong in the returned DataFrame, and trade direction is encoded by `signal`. Select the instrument/timeframe on the chart rather than inventing input columns or unsupported settings.

Module source executes in a separate process to inspect BACKTEST, then again for the strategy. Keep it deterministic and free of side effects. Saved resolved settings are authoritative in analyses, so cost scenarios/walk-forward cash are not overwritten by re-reading module defaults. Source is limited to 100,000 characters; each worker has a 60-second timeout.

Funding cashflows, exact exchange margin/liquidation, queue priority, partial fills, market-depth impact, volume participation, historical product-rule changes and tick rounding are not modeled. Account equity exhaustion fails the run rather than pretending exact liquidation is known. The engine is single-instrument and uses USD quote research P&L.

## Errors, simulations and exports

Noctra displays syntax/runtime exceptions with strategy.py line numbers, invalid/missing signal/stop/target/ratio columns, malformed or incorrectly oriented levels at signal close, NaN/infinite values, common future leaks and timeouts. Do not catch errors and return zeros to hide a broken strategy. Raise descriptive ValueError for invalid strategy inputs; use 0 only for genuine neutral/warm-up rows.

The prefix check compares entry signals AND entry bracket values using full history versus an earlier prefix. It is a diagnostic, not proof of causality. Avoid negative shifts, centered windows, full-sample normalization, future-dependent stops and unseeded/global state. Per-row ratios can depend only on information available by that signal candle's close.

Bootstrap needs at least 12 daily observations, 100–2,000 paths and a 2–100-day block. Parameter search varies the first two numeric non-boolean entries in BACKTEST['parameters']; place a tunable ratio parameter there and use it in returned risk_reward if you want to explore it. Walk-forward freezes Python rules/parameters across chronological windows; boundary closes are marked test-end exits. Cost sensitivity reruns actual fills and fees, including their effect on realized R. No analysis forces a 100% winning outcome.

Saved exports retain the exact source, resolved settings, all trade/ledger/equity records, skipped-entry reasons and attempted fills (also in `skipped-entries.csv` in the ZIP), R statistics, bracket prices, dataset provenance, estimate warnings and saved simulations. Missing simulations are not invented or run by the export action. Old signal-only/bracket runs remain readable with their original saved execution mode; this guide defines the new ratio workflow.

