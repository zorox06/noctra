"""Local trusted-code worker. Process isolation/timeout, not a security sandbox."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import traceback

import numpy as np
import pandas as pd

# Load only our fixed application helper; -I still isolates environment paths.
spec=importlib.util.spec_from_file_location('noctra_risk_ratio',Path(__file__).with_name('risk_ratio.py'))
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
parse_ratio=helper.parse_ratio


def normalize(output,count,ratio_mode,default_ratio):
    frame=output if isinstance(output,pd.DataFrame) else None
    if frame is not None:
        if 'signal' not in frame:raise ValueError('Returned DataFrame needs a column named signal.')
        values=np.asarray(frame['signal'],dtype=float)
    else:values=np.asarray(output,dtype=float)
    if values.ndim!=1 or len(values)!=count:
        raise ValueError(f'Return exactly {count:,} signals (one per candle); got shape {values.shape}.')
    if not np.isfinite(values).all():
        first=int(np.flatnonzero(~np.isfinite(values))[0])
        raise ValueError(f'Signal at row {first} is NaN/infinite. Fill warm-up signals with 0.')
    if not np.isin(values,[-1,0,1]).all():
        raise ValueError('Signals must be -1 (short), 0 (flat), or 1 (long), not prices or order sizes.')
    result={'signals':values.astype(int).tolist()}
    if not ratio_mode:return result
    if frame is None or 'stop_loss' not in frame:
        raise ValueError('Ratio strategies must return a DataFrame with signal and stop_loss (an absolute price), plus take_profit (an absolute price) or risk_reward (e.g. 2.5). Percent stops are not used.')
    if 'take_profit' not in frame and 'risk_reward' not in frame and default_ratio is None:
        raise ValueError('Supply take_profit as an absolute price, or risk_reward per row / in BACKTEST (e.g. "1:2.5").')
    result.update(stop_loss=[None]*count,take_profit=[None]*count,risk_reward=[None]*count)
    previous=np.concatenate(([0],values[:-1]))
    entries=np.flatnonzero((values!=0)&(values!=previous))
    for i in entries:
        stop=frame['stop_loss'].iloc[i]
        if isinstance(stop,(bool,np.bool_)):raise ValueError(f'stop_loss at signal row {i} must be an absolute price, not a boolean.')
        try:stop=float(stop)
        except (ValueError,TypeError):raise ValueError(f'stop_loss at signal row {i} must be an absolute price, not a percentage.')
        if not np.isfinite(stop) or stop<=0:raise ValueError(f'stop_loss at signal row {i} must be a positive finite absolute price.')
        result['stop_loss'][i]=stop
        target=frame['take_profit'].iloc[i] if 'take_profit' in frame else None
        if target is not None and not pd.isna(target):
            if isinstance(target,(bool,np.bool_)):raise ValueError(f'take_profit at signal row {i} must be an absolute price, not a boolean.')
            try:target=float(target)
            except (ValueError,TypeError):raise ValueError(f'take_profit at signal row {i} must be an absolute price.')
            if not np.isfinite(target) or target<=0:raise ValueError(f'take_profit at signal row {i} must be a positive finite absolute price.')
            result['take_profit'][i]=target
        else:
            ratio=frame['risk_reward'].iloc[i] if 'risk_reward' in frame else None
            if ratio is None or pd.isna(ratio):ratio=default_ratio
            try:result['risk_reward'][i]=parse_ratio(ratio)
            except ValueError as error:raise ValueError(f'Risk/reward at signal row {i}: {error}') from error
    return result


def execute(input_path,output_path):
    request=json.loads(Path(input_path).read_text(encoding='utf-8'))
    def ema(values,period):return pd.Series(values).ewm(span=int(period),adjust=False).mean()
    def sma(values,period):return pd.Series(values).rolling(int(period)).mean()
    def rsi(values,period=14):
        changes=pd.Series(values).diff()
        gain=changes.clip(lower=0).ewm(alpha=1/int(period),adjust=False).mean()
        loss=(-changes.clip(upper=0)).ewm(alpha=1/int(period),adjust=False).mean()
        return (100-100/(1+gain/loss.replace(0,np.nan))).where(loss.ne(0),100).fillna(50)
    scope={'np':np,'pd':pd,'ema':ema,'sma':sma,'rsi':rsi,'__name__':'__quantpad_strategy__'}
    log=io.StringIO()
    with contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
        exec(compile(request['code'],'strategy.py','exec'),scope)
        fn=scope.get('strategy')
        if not callable(fn):raise ValueError('Missing function: define strategy(data, params) and return one -1/0/1 signal per candle.')
        if request.get('mode')=='settings':
            settings=scope.get('BACKTEST',{})
            if not isinstance(settings,dict):raise ValueError('BACKTEST must be a Python dictionary of optional simulation settings.')
            return {'settings':settings}
        if 'input_path' in request:
            import polars as pl
            times = request['times']
            requested = pl.DataFrame({'time':times}, schema={'time':pl.Int64})
            stored = pl.scan_parquet(request['input_path']).filter(
                (pl.col('time') >= min(times)) & (pl.col('time') <= max(times))).collect()
            table = requested.join(stored, on='time', how='left', maintain_order='left')
            if table.height != len(times) or table['close'].null_count():
                raise ValueError('Frozen research inputs do not match the requested candle timestamps.')
            data = pd.DataFrame({name: table[name].to_numpy() for name in table.columns})
        else:
            data=pd.DataFrame(request['rows'])
        if 'source' not in data:data['source']='delta'
        if 'is_proxy' not in data:data['is_proxy']=False
        data['datetime']=pd.to_datetime(data['time'],unit='s',utc=True)
        data=data.set_index('datetime',drop=False)
        mode=request.get('exit_mode')=='risk_reward'
        result=normalize(fn(data.copy(),request['params']),len(data),mode,request.get('risk_reward'))
        cut=max(3,int(len(data)*.75))
        prefix=normalize(fn(data.iloc[:cut].copy(),request['params']),cut,mode,request.get('risk_reward'))
        if any(prefix[key]!=value[:cut] for key,value in result.items()):
            raise ValueError('Future-data check failed: earlier signals or stop/target/ratio levels change when later candles are removed. Avoid negative shift, centered rolling, or full-dataset statistics.')
    return {**result,'log':log.getvalue()[-8000:]}


if __name__=='__main__':
    try:
        result=execute(sys.argv[1],sys.argv[2]);json.dumps(result,allow_nan=False)
    except BaseException as error:
        result={'error':f'{type(error).__name__}: {error}','traceback':traceback.format_exc(limit=12)[-12000:]}
    Path(sys.argv[2]).write_text(json.dumps(result,allow_nan=False),encoding='utf-8')
