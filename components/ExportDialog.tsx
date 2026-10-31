'use client';
import { useEffect, useState } from 'react';
import { Archive, ArrowDownToLine, Braces, FileText, Table2, X, LoaderCircle } from 'lucide-react';
import type { Run } from '@/lib/types';

const FORMATS = [
  { key: 'zip', title: 'Complete research package', tag: 'RECOMMENDED', icon: Archive, detail: 'Offline report, interactive graphs, Python source, parameters, full run, simulations and CSV ledgers.' },
  { key: 'html', title: 'Offline strategy report', tag: '.HTML', icon: FileText, detail: 'One portable file with graphs, strategy code, statistics and saved analyses. Open it to print or save as PDF.' },
  { key: 'json', title: 'Full run data', tag: '.JSON', icon: Braces, detail: 'Every equity observation, trade, cash-ledger record and the exact saved configuration.' },
  { key: 'csv', title: 'Trade ledger', tag: '.CSV', icon: Table2, detail: 'All closed trades with fill prices, position sizes, fees, net P&L and exit reasons.' },
] as const;

export default function ExportDialog({ run, onClose }: { run: Run; onClose: () => void }) {
  const [analysisCount, setAnalysisCount] = useState<number | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/runs/${run.id}/analyses`, {signal:controller.signal}).then(response => response.ok ? response.json() : Promise.reject()).then(value => setAnalysisCount(value.length)).catch(() => {});
    return () => controller.abort();
  }, [run.id]);
  const [format, setFormat] = useState<string>('zip');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  async function download() {
    setBusy(true); setError(''); setSuccess('');
    try {
      const response = await fetch(`/api/runs/${run.id}/export?format=${format}`);
      if (!response.ok) { const value = await response.json(); throw new Error(value.detail || 'Export failed. Please retry.'); }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = `noctra-${run.id}${format === 'csv' ? '-trades' : ''}.${format}`;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setSuccess('Download prepared. Your saved experiment is ready to keep or share.');
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="modal-backdrop" onClick={() => { if (!busy) onClose(); }}><section className="modal export-dialog" role="dialog" aria-modal="true" aria-label="Export strategy research" onClick={e => e.stopPropagation()} onKeyDown={e => { if (e.key === 'Escape' && !busy) { e.stopPropagation(); onClose(); } }}>
    <div className="modal-head"><div><span className="eyebrow">TAKE YOUR RESEARCH WITH YOU</span><h2>Export experiment</h2></div><button autoFocus aria-label="Close export" disabled={busy} className="icon-button" onClick={onClose}><X size={18}/></button></div>
    <div className="export-body"><div className="export-snapshot"><div><strong>{run.config.name}</strong><span>{run.symbol} · {run.resolution} · #{run.id.slice(0, 8)}</span></div><span className="tag">{run.config.leverage}× CAP</span></div>
      <p className="export-context">Exports use this saved run, including its original code and settings. Unsaved edits are not included. {analysisCount == null ? 'Saved analyses are included when available.' : `${analysisCount} saved ${analysisCount === 1 ? 'analysis' : 'analyses'} available.`}</p>
      <fieldset className="export-formats"><legend>Choose an export format</legend>{FORMATS.map(({ key, title, tag, icon: Icon, detail }) => <label className={`export-choice ${format === key ? 'selected' : ''}`} key={key}><input type="radio" name="export-format" value={key} checked={format === key} disabled={busy} onChange={() => { setFormat(key); setSuccess(''); }}/><Icon size={19}/><span><strong>{title}<small>{tag}</small></strong><span>{detail}</span></span></label>)}</fieldset>
      <div className="export-includes"><span>Exact saved settings</span><span>Full-resolution records</span>{(format === 'zip' || format === 'html') && <><span>Saved analysis graphs</span><span>Works offline</span></>}</div><p className="export-context">Reports include available saved simulations; no new analyses are run during export. Source candle files stay in your local data library.</p>
      {error && <p className="export-feedback" role="alert">{error}</p>}{success && <p className="export-feedback" role="status">{success}</p>}
      <button className="button primary export-download" disabled={busy} onClick={download}>{busy ? <LoaderCircle className="spin" size={16}/> : <ArrowDownToLine size={16}/>} {busy ? 'Preparing research export…' : 'Download export'}</button>
    </div>
  </section></div>;
}
