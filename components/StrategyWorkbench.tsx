'use client';
import { useEffect, useRef, useState } from 'react';
import { Play, FileCode2, Save, ArrowDownToLine, RotateCcw, FolderOpen } from 'lucide-react';
import CodeEditor from './CodeEditor';
import { validateStrategy } from '@/lib/strategy';
import { DEFAULT_CONFIG, type Config, type Manifest, type Resolution, type SymbolName, type Run } from '@/lib/types';

const DRAFT_KEY = 'noctra-code-draft-v2';

export default function StrategyWorkbench({ config, update, symbol, resolution, manifest, busy, onRun, onReset, error, result }: {
  config: Config; update: (patch: Partial<Config>) => void; symbol: SymbolName;
  resolution: Resolution; manifest: Manifest | null; busy: boolean;
  onRun: (config: Config) => Promise<void>; onReset: () => void; error: string; result: Run | null;
}) {
  const output = useRef<HTMLElement>(null);
  const [localError, setLocalError] = useState('');
  const [saved, setSaved] = useState(false);
  const [hasDraft, setHasDraft] = useState(false);
  useEffect(() => { if (!busy && (error || localError || result)) output.current?.scrollIntoView({behavior:'smooth',block:'nearest'}); }, [busy,error,localError,result]);
  useEffect(() => { setHasDraft(!!localStorage.getItem(DRAFT_KEY)); }, []);
  function values(code = config.code ?? '') { return { ...DEFAULT_CONFIG, strategy: 'python' as const, code }; }
  function save() {
    try { localStorage.setItem(DRAFT_KEY, JSON.stringify({code: config.code ?? ''})); setHasDraft(true); setSaved(true); setLocalError(''); }
    catch (e) { setLocalError((e as Error).message); }
  }
  function load() {
    try {
      const value = JSON.parse(localStorage.getItem(DRAFT_KEY) ?? 'null');
      if (typeof value?.code !== 'string') throw new Error('No valid code draft has been saved yet.');
      update(values(value.code)); setSaved(true); setLocalError('');
    } catch (e) { setLocalError((e as Error).message); }
  }
  async function run() {
    if (busy) return;
    try { const value = values(); validateStrategy(value); update(value); setLocalError(''); await onRun(value); }
    catch (e) { setLocalError((e as Error).message); }
  }
  function clear() { onReset(); setLocalError(''); setSaved(false); }
  return <div className="python-workbench">
    <section className="panel python-editor">
      <div className="panel-head"><div className="panel-heading"><FileCode2 size={15}/><h2>Ratio strategy · Python</h2><span className="tag">1 : x</span></div><div className="panel-actions"><button className="text-button" disabled={busy} onClick={clear}><RotateCcw size={13}/>Clear</button><button className="text-button" disabled={busy} onClick={save}><Save size={13}/>{saved ? 'Draft saved' : 'Save draft'}</button><button className="button primary" disabled={busy} onClick={run}><Play size={14}/>{busy ? 'Running…' : 'Run backtest'}</button></div></div>
      <div className="python-toolbar"><div className="source-context">{symbol} · {resolution} · full archived history<small>{manifest ? `${manifest.rows.toLocaleString()} bars available` : 'Market data loads automatically'} · select market and timeframe in Market overview</small></div><a className="text-button" href="/api/strategy-guide" download="AGENTS.md"><ArrowDownToLine size={13}/>Download AGENTS.md</a>{hasDraft && <button className="text-button" disabled={busy} onClick={load}><FolderOpen size={13}/>Load draft</button>}<label className="text-button file-import"><FileCode2 size={13}/>Import .py<input type="file" accept=".py,.txt" disabled={busy} aria-label="Import Python strategy" onChange={async e => { const file=e.target.files?.[0]; if (!file) return; try { if(file.size>100000) throw new Error('Strategy file must be under 100 KB.'); update(values(await file.text())); setSaved(false); setLocalError(''); } catch(error) { setLocalError((error as Error).message); } e.target.value=''; }}/></label></div>
      <CodeEditor value={config.code ?? ''} onChange={code => { update({strategy:'python',code}); setSaved(false); }} onRun={() => void run()}/>
      <div className="editor-notes">Paste your Python code, then Run backtest · Ctrl + Enter. Absolute stop + target, or absolute stop + risk/reward ratio. Levels stay fixed per trade.
        <details><summary>Python contract & execution rules</summary><p>Define <code>strategy(data, params)</code> and return a DataFrame with <code>signal</code>, <code>stop_loss</code> (absolute price), and either <code>take_profit</code> (absolute price) or <code>risk_reward</code> (2, 2.5, 3, etc.). A new nonzero signal opens at the next candle open. The stop and target remain fixed until one is hit; later signals cannot close an open bracket.</p><p>For a short entered at 100, stop 100.5 and target 98.5 mean 1:3. With the same stop, a ratio of 1:2.5 sets the target at 98.75. These are price distances, not percentages. Fees, slippage, opening gaps and a position closed at test end can change realized R.</p><p>Choose capital, leverage and allocation explicitly in your <code>BACKTEST</code> dictionary. Your specified costs are 7 bps fee and 1 bp slippage per fill. A valid setup crossed by the next-open price or slippage is skipped and reported; levels are never moved. Full columns and execution rules are in AGENTS.md.</p><p>Python runs locally with your Windows file/network permissions. Each worker has a 60-second timeout. The future-data diagnostic checks prefixes; it does not prove causality.</p></details>
      </div>
    </section>
    {result?.config.exit_mode === 'risk_reward' && <section className="panel ratio-summary" aria-label="Risk-to-reward results"><div><span>Planned risk : reward</span><strong>{result.metrics.planned_rr_min == null ? '—' : `1:${Number(result.metrics.planned_rr_min.toPrecision(6))}${result.metrics.planned_rr_max != null && Math.abs(result.metrics.planned_rr_max-result.metrics.planned_rr_min)>.000001 ? ` – 1:${Number(result.metrics.planned_rr_max.toPrecision(6))}` : ''}`}</strong></div><div><span>Realized total · net</span><strong>{(result.metrics.net_r ?? 0).toFixed(2)}R</strong></div><div><span>Expectancy per trade · net</span><strong>{result.metrics.expectancy_r == null ? '—' : `${result.metrics.expectancy_r.toFixed(2)}R`}</strong></div><div><span>Target / stop / test end</span><strong>{result.metrics.target_hits ?? 0} / {result.metrics.stop_hits ?? 0} / {result.metrics.incomplete_ratio_trades ?? 0}</strong></div><p>1R = quantity × initial entry-to-stop price distance. Realized R includes fills and fees. Test-end exits are incomplete brackets; win rate is an outcome, not a target.</p></section>}
    {!!result?.metrics.skipped_entries && <section className="panel skipped-entry-notice" role="status" aria-label="Skipped entries"><strong>{result.metrics.skipped_entries.toLocaleString()} {result.metrics.skipped_entries === 1 ? 'entry' : 'entries'} skipped before execution</strong><p>The next-open price or slippage crossed a stop or target that was valid at signal close. No position opened, no fee was charged, and your levels were preserved. The backtest continued.</p>{result.skipped_entries?.[0] && <p className="mono">First: signal row {result.skipped_entries[0].signal_row.toLocaleString()} · {result.skipped_entries[0].side > 0 ? 'long' : 'short'} · entry {result.skipped_entries[0].entry_fill.toFixed(2)} · stop {result.skipped_entries[0].stop_loss.toFixed(2)} · {result.skipped_entries[0].reason === 'stop_crossed_before_entry' ? 'stop crossed' : 'target crossed'}</p>}<p>Full rejected-entry records are included in the research ZIP and JSON exports.</p></section>}
    <section ref={output} className={`panel execution-output ${(error||localError) ? 'has-error' : ''}`}><div className="panel-head"><h2>{error||localError ? 'Strategy error' : 'Execution output'}</h2><span className="tag">LOCAL PYTHON</span></div><pre role={error||localError ? 'alert' : undefined}>{localError||error||(busy ? 'Running your strategy locally…' : result ? `Completed #${result.id.slice(0,8)} · ${result.symbol} ${result.resolution} · ${result.metrics.trades} trades · net return ${result.metrics.net_return_pct.toFixed(2)}%.` : 'Paste your code and run a backtest. Results stay zero until execution.')}</pre>{result && <p className="run-assumptions">${result.config.capital.toLocaleString()} starting cash · {result.config.leverage}× leverage · {result.config.allocation_pct ?? 100}% allocation · {result.config.fee_bps} bps fee · {result.config.slippage_bps} bps slippage per fill · {result.config.exit_mode === 'risk_reward' ? 'Fixed absolute-price ratio brackets' : result.config.exit_mode === 'signals' ? 'Saved signal-only execution rules' : 'Saved legacy execution rules'}</p>}</section>
  </div>;
}

