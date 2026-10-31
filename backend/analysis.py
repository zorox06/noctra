import numpy as np
from .engine import backtest
from .models import StrategyConfig


def bootstrap(run, seed, paths, block_size, progress):
    daily = {}
    for p in run['equity']:
        daily[p['time'] // 86400] = p['equity']
    capital = run['config']['capital']
    values = np.array([capital, *daily.values()], dtype=float)
    returns = values[1:] / values[:-1] - 1
    n = len(returns)
    if n < 12:
        raise ValueError('Bootstrap needs at least 12 daily observations. Download a longer history.')
    block = min(block_size, max(2, n // 3))
    rng = np.random.default_rng(seed)
    samples = np.empty((paths, n + 1), dtype=float)
    samples[:, 0] = capital
    for i in range(paths):
        starts = rng.integers(0, n - block + 1, size=(n + block - 1) // block)
        sampled = np.concatenate([returns[s:s+block] for s in starts])[:n]
        samples[i, 1:] = capital * np.cumprod(1 + sampled)
        if i % 50 == 0:
            progress(5 + 85 * i / paths, f'Bootstrapping path {i + 1} of {paths}')
    terminal = (samples[:, -1] / capital - 1) * 100
    drawdowns = (samples / np.maximum.accumulate(samples, axis=1) - 1).min(axis=1) * 100
    return {'kind': 'bootstrap', 'seed': seed, 'paths': paths, 'block_size': block, 'days': n,
            'x': list(range(n + 1)), 'p05': np.quantile(samples, .05, axis=0).tolist(),
            'p50': np.quantile(samples, .5, axis=0).tolist(), 'p95': np.quantile(samples, .95, axis=0).tolist(),
            'samples': samples[:24].tolist(), 'terminal_returns': terminal.tolist(),
            'loss_probability_pct': float(np.mean(terminal < 0) * 100),
            'median_return_pct': float(np.median(terminal)),
            'drawdown_p05': float(np.quantile(drawdowns, .05)),
            'assumptions': 'Overlapping blocks of daily strategy returns; path variability only. This does not rerun a strategy on new market prices.'}


def optimization(rows, unit, cfg, progress):
    if cfg.strategy == 'python':
        numeric = [(k,v) for k,v in cfg.parameters.items() if isinstance(v,(int,float)) and not isinstance(v,bool)]
        if len(numeric)<2:
            raise ValueError('Python parameter search needs at least two numeric entries in Parameters JSON. Those first two entries define the plotted axes.')
        (xkey,xvalue),(ykey,yvalue)=numeric[:2]
        def grid(value):
            return sorted(set(int(round(value*f)) if isinstance(value,int) else round(value*f,6) for f in [.5,.75,1,1.25,1.5]))
        xs,ys=grid(xvalue),grid(yvalue)
        trials,z=[],[]
        for y in ys:
            line=[]
            for x in xs:
                progress(5+len(trials)/(len(xs)*len(ys))*88,f'Testing {xkey}={x}, {ykey}={y}')
                params={**cfg.parameters,xkey:x,ykey:y}
                candidate=StrategyConfig(**{**cfg.model_dump(),'parameters':params})
                try:
                    stats=backtest(rows,candidate,unit,include_indicators=False)['metrics']
                    line.append(stats['sharpe']); trials.append({xkey:x,ykey:y,'status':'succeeded',**stats})
                except ValueError as error:
                    line.append(None);trials.append({xkey:x,ykey:y,'status':'failed','error':str(error)})
            z.append(line)
        return {'kind':'optimization','x':xs,'y':ys,'z':z,'trials':trials,'x_label':xkey,'y_label':ykey,
                'assumptions':'In-sample exploration of the first two numeric Python parameters, scaled from 0.5× to 1.5× their entered values. Failed trials are retained as gaps.'}
    fasts, slows = [5, 10, 15, 20, 25, 30], [35, 50, 65, 80, 100]
    trials, z = [], []
    total = len(fasts) * len(slows)
    for slow in slows:
        line = []
        for fast in fasts:
            progress(5 + len(trials) / total * 88, f'Evaluating periods {fast}/{slow}')
            trial_cfg = StrategyConfig(**{**cfg.model_dump(), 'fast': fast, 'slow': slow})
            if len(rows) <= slow + 2:
                line.append(None)
                trials.append({'fast': fast, 'slow': slow, 'status': 'insufficient_data'})
                continue
            result = backtest(rows, trial_cfg, unit, include_indicators=False)['metrics']
            value = result['sharpe']
            line.append(value)
            trials.append({'fast': fast, 'slow': slow, 'status': 'succeeded', **result})
        z.append(line)
    return {'kind': 'optimization', 'x': fasts, 'y': slows, 'z': z, 'trials': trials,
            'assumptions': 'Exploratory in-sample search over this saved dataset. These results are not out-of-sample evidence.'}


def cost_stress(rows, unit, cfg, progress):
    fees, slips = [0, 2, 5, 10, 20], [0, 1, 2, 5, 10]
    trials, z = [], []
    for slip in slips:
        line = []
        for fee in fees:
            progress(5 + len(trials) / 25 * 88, f'Stressing fee {fee} bps / slippage {slip} bps')
            test_cfg = StrategyConfig(**{**cfg.model_dump(), 'fee_bps': fee, 'slippage_bps': slip})
            stats = backtest(rows, test_cfg, unit, include_indicators=False)['metrics']
            line.append(stats['net_return_pct'])
            trials.append({'fee_bps': fee, 'slippage_bps': slip, **stats})
        z.append(line)
    return {'kind': 'cost_stress', 'x': fees, 'y': slips, 'z': z, 'trials': trials,
            'assumptions': 'Reruns the strategy with each fee/slippage scenario. Funding remains excluded.'}


def walk_forward(rows, unit, cfg, progress):
    n = len(rows)
    first = max(int(n * .4), cfg.slow + 30, 130)
    if n - first < 80:
        raise ValueError('Walk-forward needs a longer dataset. Use at least 500 candles.')
    boundaries = np.linspace(first, n, 5, dtype=int)
    capital = cfg.capital
    folds, curve = [], []
    for fold, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        progress(5 + fold * 22, f'Tuning fold {fold + 1} using past data only')
        best, best_score = None, -float('inf')
        for fast in ([] if cfg.strategy == 'python' else [10, 20, 30]):
            for slow in [40, 60, 100]:
                candidate = StrategyConfig(**{**cfg.model_dump(), 'fast': fast, 'slow': slow})
                stats = backtest(rows[:start], candidate, unit, include_indicators=False)['metrics']
                score = stats['sharpe'] if stats['sharpe'] is not None and stats['trades'] >= 3 else -1e9
                if score > best_score:
                    best, best_score = candidate, score
        best = best or cfg
        frozen = StrategyConfig(**{**best.model_dump(), 'capital': capital})
        result = backtest(rows[:end], frozen, unit, include_indicators=False, trade_start=int(start))
        capital = result['metrics']['final_equity']
        curve.extend(result['equity'])
        folds.append({'fold': fold + 1, 'train_start': rows[0]['time'], 'train_end': rows[start - 1]['time'],
                      'test_start': rows[start]['time'], 'test_end': rows[end - 1]['time'],
                      'fast': best.fast, 'slow': best.slow, 'metrics': result['metrics']})
    return {'kind': 'walk_forward', 'folds': folds, 'equity': curve,
            'net_return_pct': (capital / cfg.capital - 1) * 100,
            'assumptions': ('Four chronological out-of-sample windows with your Python code and parameters frozen; no automatic code tuning. Flat at each test boundary. ' if cfg.strategy=='python' else 'Four expanding training folds; nine candidates per fold; next-window testing. Flat at each test boundary. ')+'This is a validation protocol, not an untouched final holdout.'}
