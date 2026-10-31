'use client';
import { useId } from 'react';
import type { Config } from '@/lib/types';

export default function LeverageControl({ config, update, disabled = false }: { config: Config; update: (patch: Partial<Config>) => void; disabled?: boolean }) {
  const id = useId();
  const exposure = config.stop_pct > 0 ? Math.min(config.leverage, config.risk_pct / config.stop_pct) : 0;
  return <div className="leverage-control">
    <div className="leverage-heading"><label htmlFor={id}>Leverage cap</label><strong>{config.leverage.toFixed(1)}<span>×</span></strong></div>
    <input id={id} aria-label="Leverage cap" type="range" min={0} max={5} step={0.1} value={config.leverage} disabled={disabled} onChange={e => update({ leverage: Number(e.target.value) })} />
    <div className="leverage-presets">{[.5, 1, 2, 3, 5].map(value => <button type="button" key={value} aria-pressed={config.leverage === value} disabled={disabled} onClick={() => update({ leverage: value })}>{value}×</button>)}</div>
    <div className="leverage-estimate"><span>Initial notional ceiling</span><b>${(config.capital * config.leverage).toLocaleString('en-US', { maximumFractionDigits: 0 })}</b><span>After stop-risk limit</span><b>≤ {exposure.toFixed(2)}× equity</b></div>
    <p>Position size also follows your risk budget and stop distance. This models exposure, not exchange margin or liquidation.</p>
  </div>;
}
