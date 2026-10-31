# Noctra — local custom-strategy research

Local BTCUSD/ETHUSD research using Delta Exchange India public data, Python workers, compressed Parquet history, a deterministic bar backtester and interactive graphs. No exchange credentials, cloud compute or live orders.

## Run

Run `Setup-Noctra.cmd` on a fresh install, then `Start-Noctra.cmd`. Open http://127.0.0.1:3000 and keep the launcher open. The original QuantPad launcher filenames remain compatible.

## A blank workspace

The Python editor starts empty. Simulation controls and performance counters start at zero. Actual market prices/history remain available. No saved run or draft loads automatically. There are no built-in strategy templates, example buttons or demo-source endpoints.

The journal displays only new custom-workspace experiments. Prior artifacts remain archived in `data/runs/` and can still be retrieved through their saved IDs. No data-lake or historical run files are deleted by this interface change.

## Write or generate a strategy

Download **AGENTS.md** in the header or Strategies toolbar and give it to your LLM with your own strategy idea. It describes all worker columns, target-position encoding, timing, helper functions, validation rules, model limitations and actual archive availability. Stored hourly funding/mark/index/open-interest series are explicitly distinguished from the candle columns automatically passed to Python.

Select BTCUSD/ETHUSD and a timeframe in Market overview, open Strategies, paste/import Python and Run backtest. New runs use ratio brackets: return a DataFrame with `signal`, `stop_loss` (absolute price), and either `take_profit` (absolute price) or `risk_reward` (e.g. 2.5 / "1:2.5"). A per-file `BACKTEST["risk_reward"]` can provide a fallback. No percentage-price stop or separate setup form is used.

An entry filled at 100 with short stop 100.5 and target 98.5 is 1:3. With the same entry/stop, a multiplier of 2.5 produces target 98.75. Each trade can use different absolute levels and a different ratio. The actual next-open fill determines the initial risk and ratio. Brackets stay fixed; later signals cannot exit or reverse an open trade. A new nonzero signal edge is required for the next entry, so an unchanged old signal cannot cause repeat entries after a stop/target.

1R = quantity × initial entry-to-stop price distance. Realized gross/fees/net R, planned R:R, stops/targets, target hits, stop hits and unfinished end-of-test exits are saved and exported. Costs/gaps can prevent exact realized multiples. Known opening gaps are processed first; unresolved intrabar double touches use stop-first ordering. No winning outcome is fabricated to satisfy a ratio.

Other `BACKTEST` settings are unchanged: capital, leverage, allocation_pct, fees, slippage, parameters and optional dates. Existing saved runs preserve their original execution mode; old signal-only source must be adapted to return absolute bracket levels before starting a new UI run. See AGENTS.md for the exact input/return contract and data-source limitations.


## Checks

```
.\.venv\Scripts\python.exe -m pytest backend/tests -q
npm.cmd run typecheck
npm.cmd run build
```

Vector brand assets are in `public/brand/`. The browser favicon and Apple touch icon are served from `app/`.

## Missing candle timestamps

Reference repair is automatic on a code-only run if its selected interval has internal gaps. Separate immutable overlays fill only missing timestamps; original Delta candles and old snapshots remain intact. Binance BTCUSDT/ETHUSDT perpetual OHLCV is calibrated using earlier matching Delta candles, then marked `source=binance_proxy` and `is_proxy=True`. The volume is an estimated Delta-scale proxy, not observed Delta activity. Source data, comparison diagnostics, adjustment factors and sparse-fit warnings are retained under `data/repairs/`; manifests, run assumptions and reports disclose the mixed data. Yahoo could not supply the 2024 intraday gaps. See the downloadable AGENTS.md for the full estimation policy.
