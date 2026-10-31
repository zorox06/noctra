"""Absolute-price brackets, fixed for each trade; no percentage-price stops."""
import math
import numpy as np


def entry_bracket(rows, orders, index, side, fill, policy):
    """Reject a formerly valid setup crossed before entry; malformed levels error."""
    signal = rows[index - 1]
    stop = orders['stop_loss'][index - 1]
    target = orders['take_profit'][index - 1]
    requested = orders['risk_reward'][index - 1] if target is None else None
    distance = side * (fill - stop)

    def rejected(reason):
        return {'reason': reason, 'signal_row': index - 1, 'signal_time': signal['time'],
                'entry_time': rows[index]['time'], 'side': side, 'signal_close': signal['close'],
                'entry_open': rows[index]['open'], 'entry_fill': fill, 'stop_loss': stop,
                'take_profit': target, 'risk_reward': requested}

    if not math.isfinite(distance) or distance <= 0:
        if (policy == 'skip' and math.isfinite(distance) and side * (signal['close'] - stop) > 0
                and (target is None or side * (target - signal['close']) > 0)):
            return None, rejected('stop_crossed_before_entry')
        name = 'below' if side > 0 else 'above'
        raise ValueError(f'Invalid absolute stop at signal row {index-1}: stop_loss={stop:g} must be {name} the actual entry fill {fill:g}. Signal close={signal["close"]:g}. Check the trade direction and stop-price calculation; malformed levels are not repaired.')
    if target is None:
        target = fill + side * distance * requested
    reward = side * (target - fill)
    if not math.isfinite(target) or target <= 0 or reward <= 0:
        explicit = orders['take_profit'][index - 1] is not None
        if (policy == 'skip' and explicit and math.isfinite(target) and target > 0
                and side * (target - signal['close']) > 0 and side * (signal['close'] - stop) > 0):
            return None, rejected('target_crossed_before_entry')
        name = 'above' if side > 0 else 'below'
        raise ValueError(f'Invalid absolute target at signal row {index-1}: take_profit={target:g} must be positive and {name} the actual entry fill {fill:g}. Signal close={signal["close"]:g}. Check the strategy target-price calculation.')
    return {'stop': stop, 'target': target, 'distance': distance,
            'requested': requested, 'planned': reward / distance}, None


def backtest_ratio(rows,cfg,contract_unit,progress=None,include_indicators=True,trade_start=0):
    from .engine import metrics
    from .strategies import python_orders
    if len(rows)<=2:raise ValueError('Need at least 3 closed candles for a ratio backtest.')
    orders=python_orders(rows,cfg)
    targets=orders['signals']
    cash=cfg.capital;position=None
    trades,equity,ledger=[],[],[]
    skipped_entries=[]
    slip,fee=cfg.slippage_bps/10000,cfg.fee_bps/10000

    def close(raw,ts,reason,ambiguous=False,gap=False):
        nonlocal cash,position
        side=position['side'];fill=raw*(1-side*slip)
        exit_fee=position['quantity']*fill*fee
        pnl=side*position['quantity']*(fill-position['entry_price'])
        cash+=pnl-exit_fee
        fees=position['entry_fee']+exit_fee
        risk=position['initial_risk_usd']
        trade={**position,'exit_time':ts,'exit_price':fill,'reason':reason,
               'gross_pnl':pnl,'fees':fees,'net_pnl':pnl-fees,
               'gross_r':pnl/risk,'fees_r':fees/risk,'net_r':(pnl-fees)/risk,
               'bracket_closed':reason!='end_of_test','ambiguous_exit':ambiguous,'gap_through_stop':gap}
        trades.append(trade)
        ledger.append({'time':ts,'kind':'close','cash_change':pnl-exit_fee,'balance':cash,'trade_id':trade['id']})
        position=None

    for i,row in enumerate(rows):
        if progress and i%256==0:progress(10+i/len(rows)*80,f'Simulating ratio bracket {i+1:,} of {len(rows):,} bars')
        was_open=position is not None
        # The opening print is known to precede this bar's high/low. Handle gaps first.
        if position:
            side=position['side']
            if side*(row['open']-position['stop'])<=0:
                close(row['open'],row['time'],'stop_gap',gap=True)
            elif side*(row['open']-position['target'])>=0:
                # Conservative limit-like target fill: no assumed favorable improvement.
                close(position['target'],row['time'],'take_profit')
        desired=int(targets[i-1]) if i and i>=trade_start else 0
        prior=int(targets[i-2]) if i>1 else 0
        fresh=desired!=0 and desired!=prior
        # Entry intentions while a bracket is open are ignored, not queued.
        if not position and not was_open and fresh and cash>0:
            fill=row['open']*(1+desired*slip)
            bracket,rejection=entry_bracket(rows,orders,i,desired,fill,cfg.entry_gap_policy)
            if rejection:
                skipped_entries.append(rejection)
            else:
                stop,target,distance=bracket['stop'],bracket['target'],bracket['distance']
                planned,requested=bracket['planned'],bracket['requested']
                qty=cash*cfg.allocation_pct/100*cfg.leverage/fill
                contracts=math.floor(qty/contract_unit);qty=contracts*contract_unit
                entry_fee=qty*fill*fee
                if contracts>0 and entry_fee<cash:
                    cash-=entry_fee
                    position={'id':len(trades)+1,'side':desired,'contracts':contracts,'quantity':qty,
                              'entry_time':row['time'],'entry_price':fill,'entry_fee':entry_fee,
                              'stop':stop,'target':target,'stop_distance':distance,
                              'initial_risk_usd':qty*distance,'planned_reward_risk':planned,
                              'requested_reward_risk':requested,'signal_time':rows[i-1]['time']}
                    ledger.append({'time':row['time'],'kind':'open_fee','cash_change':-entry_fee,'balance':cash,'trade_id':position['id']})
        if position:
            side=position['side'];stop=position['stop'];target=position['target']
            hit_stop=row['low']<=stop if side==1 else row['high']>=stop
            hit_target=row['high']>=target if side==1 else row['low']<=target
            if hit_stop:
                price=min(stop,row['open']) if side==1 else max(stop,row['open'])
                gap=price!=stop
                close(price,row['time'],'stop_gap' if gap else 'ambiguous_stop_first' if hit_target else 'stop_loss',ambiguous=hit_target,gap=gap)
            elif hit_target:close(target,row['time'],'take_profit')
        unrealized=position['side']*position['quantity']*(row['close']-position['entry_price']) if position else 0
        value=cash+unrealized
        if value<=0:raise ValueError('Account equity exhausted. Gaps and costs can exceed planned risk; exact liquidation is not modeled.')
        equity.append({'time':row['time'],'equity':value,'cash':cash,
                       'exposure':position['side']*position['quantity']*row['close'] if position else 0})
    if position:
        close(rows[-1]['close'],rows[-1]['time'],'end_of_test')
        equity[-1].update(equity=cash,cash=cash,exposure=0)
    equity=equity[trade_start:]
    stats,drawdowns=metrics(equity,trades,cfg.capital)
    for point,dd in zip(equity,drawdowns):point['drawdown_pct']=dd*100
    multiples=[t['net_r'] for t in trades]
    planned=[t['planned_reward_risk'] for t in trades]
    stats.update(skipped_entries=len(skipped_entries),
                 skipped_stop_entries=sum(item['reason']=='stop_crossed_before_entry' for item in skipped_entries),
                 skipped_target_entries=sum(item['reason']=='target_crossed_before_entry' for item in skipped_entries),net_r=float(sum(multiples)),expectancy_r=float(np.mean(multiples)) if multiples else None,
                 planned_rr_min=min(planned) if planned else None,planned_rr_max=max(planned) if planned else None,
                 target_hits=sum(t['reason']=='take_profit' for t in trades),
                 stop_hits=sum(t['reason'] in ('stop_loss','stop_gap','ambiguous_stop_first') for t in trades),
                 incomplete_ratio_trades=sum(not t['bracket_closed'] for t in trades))
    result={'metrics':stats,'equity':equity,'trades':trades,'ledger':ledger,'skipped_entries':skipped_entries,
            'benchmark':[{'time':r['time'],'equity':cfg.capital*r['close']/rows[trade_start]['close']} for r in rows[trade_start:]]}
    if include_indicators:result['indicators']={'fast':[],'slow':[]}
    return result
