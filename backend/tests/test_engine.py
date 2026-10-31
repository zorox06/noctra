import math
import numpy as np
import pytest
from backend.engine import backtest, signals
from backend.models import StrategyConfig
from backend.analysis import bootstrap


def candles(prices):
    return [{'time': 1704067200 + i * 3600, 'open': p, 'high': p + .1, 'low': p - .1, 'close': p, 'volume': 10000} for i, p in enumerate(prices)]


def test_signal_at_close_cannot_fill_before_next_open():
    rows = candles([100] * 8 + [105, 110, 115, 120])
    cfg = StrategyConfig(fast=2, slow=3, fee_bps=0, slippage_bps=0, stop_pct=20, target_pct=50, direction='long_only')
    target, *_ = signals(rows, cfg)
    first_signal = next(i for i, value in enumerate(target) if value > 0)
    run = backtest(rows, cfg, .001)
    assert run['trades'][0]['entry_time'] == rows[first_signal + 1]['time']
    assert run['trades'][0]['entry_price'] == rows[first_signal + 1]['open']
    # Hand-calculated: 45454 contracts of .001 bought at 110, sold at 120.
    assert run['trades'][0]['contracts'] == 45454
    assert run['metrics']['net_pnl'] == pytest.approx(454.54)


def test_ledger_reconciles_fees_and_net_pnl():
    cfg = StrategyConfig(fast=2, slow=3, fee_bps=5, slippage_bps=2, stop_pct=20, target_pct=50)
    run = backtest(candles([100 + math.sin(i / 5) * 8 + i * .1 for i in range(150)]), cfg, .01)
    delta = run['metrics']['final_equity'] - cfg.capital
    assert delta == pytest.approx(sum(t['net_pnl'] for t in run['trades']), abs=1e-7)
    assert delta == pytest.approx(sum(t['cash_change'] for t in run['ledger']), abs=1e-7)
    assert run['metrics']['fees'] > 0
    assert all(t['contracts'] == int(t['contracts']) and t['quantity'] == pytest.approx(t['contracts'] * .01) for t in run['trades'])


def test_ambiguous_stop_target_uses_adverse_ordering():
    rows = candles([100, 100, 100, 102, 104, 106, 108, 110])
    rows[4].update(low=50, high=150)
    cfg = StrategyConfig(fast=2, slow=3, fee_bps=0, slippage_bps=0, stop_pct=1, target_pct=2, direction='long_only')
    run = backtest(rows, cfg, .001)
    first = run['trades'][0]
    assert first['reason'] == 'ambiguous_stop_first'
    assert first['exit_price'] == pytest.approx(first['entry_price'] * .99)
    assert first['net_pnl'] < 0


def test_future_candles_do_not_change_past_signals():
    rows = candles([100 + math.sin(i / 4) * 8 for i in range(150)])
    cfg = StrategyConfig(fast=5, slow=15)
    before = signals(rows, cfg)[0]
    future = candles([2000 + i * 100 for i in range(20)])
    after = signals(rows + future, cfg)[0]
    np.testing.assert_array_equal(before, after[:len(rows)])


def test_bootstrap_repeats_with_seed_and_records_block_assumption():
    rows = candles([100 + math.sin(i / 20) * 8 + i * .01 for i in range(800)])
    cfg = StrategyConfig(fast=5, slow=20)
    result = backtest(rows, cfg, .001)
    run = {**result, 'config': cfg.model_dump()}
    a = bootstrap(run, 42, 100, 5, lambda *args: None)
    b = bootstrap(run, 42, 100, 5, lambda *args: None)
    assert a == b
    assert len(a['terminal_returns']) == 100
    assert all(a['p05'][i] <= a['p50'][i] <= a['p95'][i] for i in range(len(a['x'])))


def test_no_trade_statistics_remain_undefined():
    cfg = StrategyConfig(fast=2, slow=3, direction='long_only')
    run = backtest(candles([100] * 100), cfg, .001)
    assert run['trades'] == []
    assert run['metrics']['net_return_pct'] == 0
    assert run['metrics']['sharpe'] is None
    assert run['metrics']['profit_factor'] is None
