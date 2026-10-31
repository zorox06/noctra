import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np

def run_worker(payload, row_count=0):
    # Explicitly local trusted-code execution; no cloud and no exchange credentials.
    with tempfile.TemporaryDirectory(prefix='quantpad-strategy-') as folder:
        source, output = Path(folder) / 'input.json', Path(folder) / 'output.json'
        source.write_text(json.dumps(payload, allow_nan=False), encoding='utf-8')
        try:
            result = subprocess.run([sys.executable, '-I', str(Path(__file__).with_name('python_worker.py')), str(source), str(output)],
                                    cwd=folder, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=60, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except subprocess.TimeoutExpired:
            raise ValueError('Strategy timed out after 60 seconds. Check for infinite loops or reduce the test date range.')
        if result.returncode or not output.exists():
            raise ValueError(f'Strategy process exited without a result (exit {result.returncode}). Check imports, memory use, or process-exit calls.')
        if output.stat().st_size > max(1000000, row_count*96):
            raise ValueError('Strategy returned an unexpectedly large result.')
        value = json.loads(output.read_text(encoding='utf-8'))
        if 'error' in value:
            raise ValueError(value['error'] + '\n\n' + value.get('traceback', ''))
        return value


def config_from_source(code, require_ratio=False):
    from .models import SourceSettings, StrategyConfig
    value = run_worker({'code': code, 'mode': 'settings'})
    try:
        settings = SourceSettings(**value['settings'])
    except ValueError as error:
        raise ValueError(f'Invalid BACKTEST settings in strategy.py: {error}') from error
    return StrategyConfig(**settings.model_dump(), strategy='python', code=code,
                          fast=0, slow=0, entry_gap_policy='skip' if require_ratio else 'error',
                          exit_mode='risk_reward' if require_ratio or settings.risk_reward is not None else 'signals', risk_pct=0,
                          stop_pct=0, target_pct=0, direction='both')


def python_orders(rows,cfg):
    payload = {'code':cfg.code,'params':cfg.parameters if cfg.fast==cfg.slow==0 else {'fast':cfg.fast,'slow':cfg.slow,**cfg.parameters},
               'exit_mode':cfg.exit_mode,'risk_reward':cfg.risk_reward}
    if cfg.research_input_id:
        from . import research_data
        payload.update(input_path=str(research_data.verified_path(cfg.research_input_id)),
                       times=[row['time'] for row in rows])
    else:
        payload['rows'] = rows
    value=run_worker(payload,len(rows))
    return {**value,'signals':np.array(value['signals'],dtype=int)}


def python_signals(rows,cfg):
    return python_orders(rows,cfg)['signals']
