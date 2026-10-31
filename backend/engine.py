"""Deterministic, closed-bar strategy signals with next-open execution.

User Python runs in a separate local process. Funding and exact
exchange liquidation are deliberately excluded from this first bar engine.
"""
import math
from datetime import datetime, timezone
import numpy as np

from .models import StrategyConfig

ENGINE_VERSION = 'bar-ledger-0.8.0'


def ema(values, period):
    output = np.empty(len(values), dtype=float)
    output[0] = values[0]
    alpha = 2 / (period + 1)
    for i in range(1, len(values)):
        output[i] = alpha * values[i] + (1 - alpha) * output[i - 1]
    return output


def signals(rows, cfg):
    closes = np.array([r['close'] for r in rows])
    fast, slow = (np.array([], dtype=float), np.array([], dtype=float)) if cfg.strategy == 'python' else (ema(closes, cfg.fast), ema(closes, cfg.slow))
    targets = np.zeros(len(rows), dtype=int)
    if cfg.strategy == 'python':
        from .strategies import python_signals
        targets = python_signals(rows, cfg)
    elif cfg.strategy == 'ema_cross':
        targets = np.where(fast > slow, 1, -1)
    elif cfg.strategy == 'breakout':
        target = 0
        for i in range(cfg.slow, len(rows)):
            past_high = max(r['high'] for r in rows[i-cfg.slow:i])
            past_low = min(r['low'] for r in rows[i-cfg.slow:i])
            if closes[i] > past_high:
                target = 1
            elif closes[i] < past_low:
                target = -1
            targets[i] = target
    else:
        delta = np.diff(closes, prepend=closes[0])
        gain, loss = ema(np.maximum(delta, 0), cfg.fast), ema(np.maximum(-delta, 0), cfg.fast)
        rsi = np.divide(gain, gain + loss, out=np.full(len(rows), .5), where=(gain + loss) > 0) * 100
        target = 0
        for i, value in enumerate(rsi):
            if value < 30:
                target = 1
            elif value > 70:
                target = -1
            elif 45 <= value <= 55:
                target = 0
            targets[i] = target
    if cfg.strategy != 'python':
        targets[:cfg.slow] = 0
    if cfg.direction == 'long_only':
        targets = np.maximum(targets, 0)
    if cfg.direction == 'short_only':
        targets = np.minimum(targets, 0)
    return targets, fast, slow


def metrics(equity, trades, capital):
    values = np.array([p['equity'] for p in equity])
    peaks = np.maximum.accumulate(np.concatenate(([capital], values)))[1:]
    dd = values / np.maximum(peaks, 1e-12) - 1
    daily = {}
    for point in equity:
        daily[point['time'] // 86400] = point['equity']
    day_values = np.array(list(daily.values()))
    returns = np.diff(np.concatenate(([capital], day_values))) / np.maximum(np.concatenate(([capital], day_values))[:-1], 1e-12)
    std = float(np.std(returns, ddof=1)) if len(returns) > 2 else 0
    sharpe = float(np.mean(returns) / std * math.sqrt(365)) if std > 0 else None
    wins = [t['net_pnl'] for t in trades if t['net_pnl'] > 0]
    losses = [-t['net_pnl'] for t in trades if t['net_pnl'] < 0]
    return {
        'net_return_pct': float((values[-1] / capital - 1) * 100),
        'net_pnl': float(values[-1] - capital), 'final_equity': float(values[-1]),
        'max_drawdown_pct': float(dd.min() * 100), 'sharpe': sharpe,
        'profit_factor': float(sum(wins) / sum(losses)) if losses else None,
        'win_rate_pct': 100 * len(wins) / len(trades) if trades else 0,
        'trades': len(trades), 'fees': sum(t['fees'] for t in trades),
        'expectancy': float(np.mean([t['net_pnl'] for t in trades])) if trades else 0,
        'annualized_volatility_pct': std * math.sqrt(365) * 100,
        'daily_observations': len(returns), 'funding': None,
    }, dd.tolist()


def backtest(rows, cfg: StrategyConfig, contract_unit, progress=None, include_indicators=True, trade_start=0):
    if cfg.exit_mode == 'risk_reward':
        from .ratio_engine import backtest_ratio
        return backtest_ratio(rows,cfg,contract_unit,progress,include_indicators,trade_start)
    if len(rows) <= (2 if cfg.strategy == 'python' else cfg.slow + 2):
        raise ValueError(f'Need more than {cfg.slow + 2} candles for this warm-up. Download more data or reduce the slow period.')
    targets, fast, slow = signals(rows, cfg)
    cash = cfg.capital
    position = None
    trades, equity, ledger = [], [], []
    slip, fee = cfg.slippage_bps / 10000, cfg.fee_bps / 10000

    def close(raw_price, ts, reason):
        nonlocal cash, position
        side = position['side']
        fill = raw_price * (1 - side * slip)
        exit_fee = position['quantity'] * fill * fee
        pnl = side * position['quantity'] * (fill - position['entry_price'])
        cash += pnl - exit_fee
        trade = {**position, 'exit_time': ts, 'exit_price': fill, 'reason': reason,
                 'gross_pnl': pnl, 'fees': position['entry_fee'] + exit_fee,
                 'net_pnl': pnl - position['entry_fee'] - exit_fee}
        trades.append(trade)
        ledger.append({'time': ts, 'kind': 'close', 'cash_change': pnl - exit_fee, 'balance': cash, 'trade_id': trade['id']})
        position = None

    for i, row in enumerate(rows):
        if progress and i % 256 == 0:
            progress(10 + i / len(rows) * 80, f'Simulating bar {i + 1:,} of {len(rows):,}')
        # Signals observed at prior close are first eligible at this open.
        desired = int(targets[i - 1]) if i and i >= trade_start else 0
        if position and desired != position['side']:
            close(row['open'], row['time'], 'signal_change')
        if not position and desired and cash > 0:
            fill = row['open'] * (1 + desired * slip)
            if cfg.exit_mode == 'signals':
                qty = cash * cfg.allocation_pct / 100 * cfg.leverage / fill
            else:
                risk_budget = cash * cfg.risk_pct / 100
                qty = min(risk_budget / (fill * cfg.stop_pct / 100), cash * cfg.leverage / fill)
            contracts = math.floor(qty / contract_unit)
            qty = contracts * contract_unit
            entry_fee = qty * fill * fee
            if contracts > 0 and entry_fee < cash:
                cash -= entry_fee
                position = {'id': len(trades) + 1, 'side': desired, 'contracts': contracts, 'quantity': qty,
                            'entry_time': row['time'], 'entry_price': fill, 'entry_fee': entry_fee,
                            'stop': fill * (1 - desired * cfg.stop_pct / 100) if cfg.exit_mode == 'brackets' else None,
                            'target': fill * (1 + desired * cfg.target_pct / 100) if cfg.exit_mode == 'brackets' else None}
                ledger.append({'time': row['time'], 'kind': 'open_fee', 'cash_change': -entry_fee, 'balance': cash, 'trade_id': position['id']})
        if position and cfg.exit_mode == 'brackets':
            side = position['side']
            stop, target = position['stop'], position['target']
            hit_stop = row['low'] <= stop if side == 1 else row['high'] >= stop
            hit_target = row['high'] >= target if side == 1 else row['low'] <= target
            if hit_stop:
                # Worst ordering when stop and target both touch; gap-through fills at open.
                price = min(stop, row['open']) if side == 1 else max(stop, row['open'])
                close(price, row['time'], 'ambiguous_stop_first' if hit_target else 'stop_loss')
            elif hit_target:
                close(target, row['time'], 'take_profit')
        unrealized = position['side'] * position['quantity'] * (row['close'] - position['entry_price']) if position else 0
        value = cash + unrealized
        if value <= 0:
            raise ValueError('Account equity exhausted under these scenario assumptions. Lower leverage or risk; exact liquidation is not modeled.')
        equity.append({'time': row['time'], 'equity': value, 'cash': cash,
                       'exposure': (position['side'] * position['quantity'] * row['close']) if position else 0})
    if position:
        close(rows[-1]['close'], rows[-1]['time'], 'end_of_test')
        equity[-1].update(equity=cash, cash=cash, exposure=0)
    equity = equity[trade_start:]
    stats, dd = metrics(equity, trades, cfg.capital)
    for item, drawdown in zip(equity, dd):
        item['drawdown_pct'] = drawdown * 100
    output = {'metrics': stats, 'equity': equity, 'trades': trades, 'ledger': ledger,
              'benchmark': [{'time': r['time'], 'equity': cfg.capital * r['close'] / rows[trade_start]['close']} for r in rows[trade_start:]]}
    if include_indicators:
        output['indicators'] = {'fast': fast.tolist(), 'slow': slow.tolist()}
    return output


def make_run(manifest, rows, cfg, progress=None):
    from .store import identifier
    from .models import SECONDS
    if not rows:
        raise ValueError('No candles in the selected test dates. Check the archived date range in Data explorer.')
    step = SECONDS[manifest['resolution']]
    if any(b['time']-a['time'] != step for a,b in zip(rows, rows[1:])):
        raise ValueError('This snapshot still has missing candle timestamps. Reference repair could not produce a complete interval; no missing values are silently fabricated.')
    created = datetime.now(timezone.utc).isoformat()
    result = backtest(rows, cfg, manifest['contract_unit'], progress)
    from . import research_data
    inputs = research_data.metadata(cfg.research_input_id) if cfg.research_input_id else None
    return {
        'id': identifier({'dataset': manifest['id'], 'config': cfg.model_dump(), 'engine': ENGINE_VERSION, 'created': created}),
        'workspace_version': 'custom-v1' if cfg.strategy == 'python' and cfg.fast == cfg.slow == 0 else 'legacy',
        'created_at': created, 'dataset_id': manifest['id'], 'symbol': manifest['symbol'],
        'resolution': manifest['resolution'], 'config': cfg.model_dump(), 'engine_version': ENGINE_VERSION,
        'test_start': rows[0]['time'], 'test_end': rows[-1]['time']+step,
        'dataset_sha256': manifest['sha256'], 'instrument_snapshot': manifest['product'],
        'strategy_input_snapshot': inputs,
        'data_quality': {'total_bars':len(rows), 'proxy_bars':sum(bool(r.get('is_proxy',False)) for r in rows), 'repair_info':manifest.get('repair_info')},
        'assumptions': [
            'Closed-bar signals; next-open execution with configured slippage.',
            *([f'Frozen research inputs {inputs["id"]}: exact BTC/ETH candle joins; hourly auxiliary data available only after close, stale observations remain missing. Cross-asset features do not execute a hedge leg.'] if inputs else []),
            *(['Ratio brackets use OHLC. Known opening gaps are handled first; otherwise a candle touching both levels exits stop-first. No repeat entry from an unchanged signal.',
               '1R is quantity × initial absolute stop distance. Realized R includes actual fills and fees; gaps can lose more than 1R. End-of-test exits are marked incomplete rather than forced to the target ratio.',
               ('Entry gaps: a setup valid at signal close but crossed at the next slipped open is skipped and recorded without fees or moved levels; a fresh signal edge is required for another attempt.' if cfg.entry_gap_policy == 'skip' else 'Saved strict entry-gap policy: invalid levels at the actual entry abort this simulation.')] if cfg.exit_mode == 'risk_reward' else []),
            ('Ratio mode: absolute stop and target are frozen at actual entry; target may be an absolute price or entry-to-stop distance × the chosen risk/reward. Entry signals cannot flatten or reverse an open bracket.' if cfg.exit_mode == 'risk_reward' else 'Exits are controlled by Python targets: 0 flattens, the opposite sign reverses. No automatic stop loss or take profit. The final position closes at test end.' if cfg.exit_mode == 'signals' else 'Stops and targets use last-trade OHLC; ambiguous bars use stop-first ordering.'),
            (f'Sizing: {cfg.allocation_pct:g}% of current cash × {cfg.leverage:g} leverage per entry, rounded down to whole contracts.' if cfg.exit_mode in ('signals','risk_reward') else 'Sizing uses stop-risk limits and a leverage cap, rounded down to whole contracts.'),
            f'Starting capital ${cfg.capital:g}; fee {cfg.fee_bps:g} bps and slippage {cfg.slippage_bps:g} bps per fill.', 
            'Funding excluded. P&L is a USD quote-currency research simulation, not an INR account statement.',
            'Historical fees and metadata are scenario assumptions using current contract specifications.',
            'No exact liquidation, partial fills, queue priority or volume constraints.',
            'Benchmark is normalized price return without costs or funding; it is not a matched perpetual strategy.',
            f'Dataset coverage {manifest["coverage_pct"]}%; {len(manifest["gaps"])} internal gap intervals.'
        ] + ([f"Data quality: {sum(bool(r.get('is_proxy',False)) for r in rows):,} candles use adjusted reference OHLC and estimated volume; these are not observed Delta trades. Sparse calibration and basis drift may affect results."] if any(r.get('is_proxy') for r in rows) else []), **result,
    }
