'use client';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Activity, ArrowDownToLine, ArrowUpRight, Braces, Check, ChevronDown, CircleHelp, Database, FlaskConical, Grid2X2, Layers3, LoaderCircle, Maximize2, Pause, Play, RefreshCw, Search, ShieldCheck, Terminal, X } from 'lucide-react';
import type { Data, Layout } from 'plotly.js';
import { api, date, money, signed } from '@/lib/api';
import { validateStrategy } from '@/lib/strategy';
import { DEFAULT_CONFIG, type Analysis, type AnalysisKind, type Candle, type Config, type Job, type Manifest, type Resolution, type Run, type RunSummary, type SymbolName } from '@/lib/types';
import MarketChart, {type ChartFocus} from './MarketChart';
import Plot from './Plot';
import StrategyWorkbench from './StrategyWorkbench';
import ExportDialog from './ExportDialog';
import { BrandMark, BrandWordmark } from './Brand';

type Page = 'Research' | 'Strategies' | 'Backtests' | 'Simulations' | 'Data explorer';
const NAV = [['Research', Grid2X2], ['Strategies', Braces], ['Backtests', Activity], ['Simulations', FlaskConical], ['Data explorer', Database]] as const;
const RESOLUTIONS = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '1d', '1w'] as const;
const TITLES: Record<Page, string> = { Research: 'Research terminal', Strategies: 'Strategy workbench', Backtests: 'Experiment journal', Simulations: 'Simulation lab', 'Data explorer': 'Market data explorer' };
const SURFACE_LAYOUT: Partial<Layout> = {
  margin: { l: 0, r: 0, t: 5, b: 0 }, scene: {
    bgcolor: '#070707', xaxis: { title: { text: 'Parameter 1' }, gridcolor: '#272727', backgroundcolor: '#070707' },
    yaxis: { title: { text: 'Parameter 2' }, gridcolor: '#272727', backgroundcolor: '#070707' },
    zaxis: { title: { text: 'Sharpe' }, gridcolor: '#272727', backgroundcolor: '#070707' },
    camera: { eye: { x: 1.6, y: 1.6, z: 1.1 } },
  },
};
const HEAT_LAYOUT: Partial<Layout> = { margin: { l: 45, r: 12, t: 8, b: 34 }, xaxis: { title: { text: 'Parameter 1' }, color: '#888' }, yaxis: { title: { text: 'Parameter 2' }, color: '#888' } };

function Panel({ title, sub, action, children, className = '' }: { title: string; sub?: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}><div className="panel-head"><div className="panel-heading"><h2>{title}</h2>{sub && <span className="sub">{sub}</span>}</div><div className="panel-actions">{action}</div></div>{children}</section>;
}
function Empty({ title, detail, action }: { title: string; detail?: string; action?: ReactNode }) {
  return <div className="empty"><div className="empty-icon"><Activity size={24} strokeWidth={1} /></div><h3>{title}</h3>{detail && <p>{detail}</p>}{action}</div>;
}
function Field({ label, children }: { label: string; children: ReactNode }) { return <label className="field"><span>{label}</span>{children}</label>; }
function fmt(n: number | null | undefined, suffix = '') { return n == null ? '—' : `${money(n)}${suffix}`; }

export default function Workspace() {
  const [page, setPage] = useState<Page>('Research');
  const [tab, setTab] = useState('Overview');
  const [symbol, setSymbol] = useState<SymbolName>('BTCUSD');
  const [resolution, setResolution] = useState<Resolution>('5m');
  const [days, setDays] = useState(0);
  const [archive, setArchive] = useState<{state:string;streams:{symbol:string;kind:string;resolution:string;rows:number;start:number|null;end:number|null;bytes:number;state:string;missing_internal_bars:number;errors:string[]}[]}>({state:'empty',streams:[]});
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [chartDate,setChartDate]=useState('');
  const [chartFocus,setChartFocus]=useState<ChartFocus>();
  const [chartVisible,setChartVisible]=useState<{from:number;to:number}|null>(null);
  const chartRequest=useRef(false);

  const [catalog, setCatalog] = useState<Manifest[]>([]);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [config, setConfig] = useState<Config>(DEFAULT_CONFIG);
  const [run, setRun] = useState<Run | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [archiveJob, setArchiveJob] = useState<Job|null>(null);
  const [tradeOffset, setTradeOffset] = useState(0);
  const archiveBusy = archiveJob?.status==='running'||archiveJob?.status==='queued';
  const [analyses, setAnalyses] = useState<Partial<Record<AnalysisKind, Analysis>>>({});
  const [three, setThree] = useState(true);
  const [selectedTrade, setSelectedTrade] = useState<number | null>(null);
  const [backend, setBackend] = useState<'connecting' | 'ready' | 'offline'>('connecting');
  const [ticker, setTicker] = useState<{ price: number; change: number | null; received: string } | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [showAssumptions, setShowAssumptions] = useState(false);
  const [exportRun, setExportRun] = useState<Run | null>(null);
  const [showSearch, setShowSearch] = useState(false);
  const [search, setSearch] = useState('');
  const [analysisKind, setAnalysisKind] = useState<AnalysisKind>('bootstrap');
  const [seed, setSeed] = useState(0);
  const [paths, setPaths] = useState(0);
  const [blockSize, setBlockSize] = useState(0);
  const [replayIndex, setReplayIndex] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [initializing, setInitializing] = useState(true);
  const busy = job?.status === 'running' || job?.status === 'queued';
  const live = useRef(true);
  const currentDataset = useRef<string | null>(null);
  const content = useRef<HTMLDivElement>(null);
  const selectTrade = useCallback((id: number) => setSelectedTrade(id), []);

  const updateConfig = useCallback((patch: Partial<Config>) => {
    setConfig(previous => ({ ...previous, ...patch }));
  }, []);

  useEffect(() => {
    live.current = true;
    Promise.all([api<{ status: string }>('/health'), api<Manifest[]>('/datasets'), api<RunSummary[]>('/runs?workspace=custom')])
      .then(([, datasets, saved]) => { if (live.current) { setBackend('ready'); setCatalog(datasets); setRuns(saved); } })

      .catch(e => { if (live.current) { setBackend('offline'); setError(`Local API unavailable. Start Noctra using Start-Noctra.cmd. ${e.message}`); } })
      .finally(() => { if (live.current) setInitializing(false); });
    return () => { live.current = false; };
  }, []);

  useEffect(() => {
    const refresh = () => api<typeof archive>('/archive').then(setArchive).catch(()=>{});
    void refresh(); const timer=setInterval(refresh,5000); return ()=>clearInterval(timer);
  }, []);

  useEffect(()=>{api<Job[]>('/jobs').then(jobs=>setArchiveJob(jobs.find(j=>j.kind==='archive')??null)).catch(()=>{});},[]);
  useEffect(()=>{
    if(!archiveJob || !archiveBusy) return;
    const timer=setInterval(()=>{api<Job>(`/jobs/${archiveJob.id}`).then(value=>{
      setArchiveJob(value);
      if(value.status==='succeeded'){void api<typeof archive>('/archive').then(setArchive);setNotice('Historical archive saved. Select a timeframe and Load to use it.');}
      if(value.status==='failed')setError(value.error??value.message);
    }).catch(()=>{});},1000);
    return()=>clearInterval(timer);
  },[archiveJob?.id,archiveBusy]);
  useEffect(()=>{setTradeOffset(0);},[run?.id]);
  async function tradePage(offset:number){if(!run)return;try{const result=await api<{trades:Run['trades'];total:number}>(`/runs/${run.id}/trades?offset=${offset}`);setRun(previous=>previous?{...previous,trades:result.trades,trade_count:result.total}:previous);setTradeOffset(offset);}catch(e){setError((e as Error).message);}}

  useEffect(() => {
    const match = catalog.find(d => d.symbol === symbol && d.resolution === resolution && d.days === days);
    let active = true;
    if (!match) { setManifest(null); setCandles([]); setRun(null); setAnalyses({}); currentDataset.current = null; return; }
    if (match.id === currentDataset.current) return;
    setManifest(null); setCandles([]);
    setRun(null); setAnalyses({}); setSelectedTrade(null); setPlaying(false); setReplayIndex(null);
    api<{ manifest: Manifest; candles: Candle[] }>(`/datasets/${match.id}`)
      .then(data => { if (active) { setManifest(data.manifest); setCandles(data.candles); currentDataset.current = match.id; } })
      .catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [catalog, symbol, resolution, days]);

  useEffect(() => {
    if (backend !== 'ready') return;
    let active = true;
    setTicker(null);
    async function load() {
      try {
        const result = await api<{ ticker: Record<string, string | number>; received_at: string }>(`/ticker/${symbol}`);
        const price = Number(result.ticker.close ?? result.ticker.mark_price);
        const change = Number(result.ticker.price_change_24h);
        if (active && Number.isFinite(price)) setTicker({ price, change: Number.isFinite(change) ? change : null, received: result.received_at });
      } catch { if (active) setTicker(null); }
    }
    void load();
    const timer = setInterval(load, 30000);
    return () => { active = false; clearInterval(timer); };
  }, [symbol, backend]);

  useEffect(() => {
    if (!run?.id) return;
    let active = true;
    api<Analysis[]>(`/runs/${run.id}/analyses`).then(saved => {
      if (active) setAnalyses(previous => ({ ...Object.fromEntries(saved.map(a => [a.kind, a])), ...previous }));
    }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [run?.id]);

  useEffect(() => {
    function keyboard(e: KeyboardEvent) { if ((e.ctrlKey || e.metaKey) && e.key === 'k') { e.preventDefault(); setShowSearch(v => !v); } if (e.key === 'Escape') { setShowSearch(false); setExpanded(false); setShowAssumptions(false); } }
    window.addEventListener('keydown', keyboard); return () => window.removeEventListener('keydown', keyboard);
  }, []);
  useEffect(() => { if (!notice) return; const t = setTimeout(() => setNotice(''), 5000); return () => clearTimeout(t); }, [notice]);
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => setReplayIndex(previous => {
      if (previous == null || previous >= candles.length - 1) { setPlaying(false); return previous; }
      return previous + 1;
    }), 250);
    return () => clearInterval(timer);
  }, [playing, candles.length]);

  async function execute(path: string, payload: unknown, done: (result: unknown) => Promise<string | void>) {
    setError('');
    setNotice('');
    try {
      let active = await api<Job>(path, payload);
      setJob(active);
      while (live.current && (active.status === 'queued' || active.status === 'running')) {
        await new Promise(resolve => setTimeout(resolve, 600));
        active = await api<Job>(`/jobs/${active.id}`);
        if (live.current) setJob(active);
      }
      if (active.status === 'succeeded') { setNotice((await done(active.result)) ?? 'Saved locally.'); }
      else if (active.status !== 'canceled') throw new Error(active.error ?? active.message);
    } catch (e) { setError(e instanceof Error ? e.message : 'Request failed'); setJob(null); }
  }

  async function download() {
    await execute('/datasets/sync', { symbol, resolution, days }, async () => { setCatalog(await api<Manifest[]>('/datasets')); });
  }
  async function runBacktest(override?: Config) {
    const selectedConfig = override ?? config;
    try { validateStrategy(selectedConfig); }
    catch (e) { setError((e as Error).message); setPage('Strategies'); setTab('Overview'); return; }
    const requestedDays = 0; // Source can select dates; always start with the full archived dataset.
    let dataset = manifest;
    if (!dataset || dataset.days !== requestedDays) {
      await execute('/datasets/sync', { symbol, resolution, days:requestedDays }, async result => {
        dataset = result as Manifest;
        const data = await api<{manifest:Manifest;candles:Candle[]}>(`/datasets/${dataset.id}`);
        currentDataset.current = dataset.id;
        setManifest(dataset); setDays(dataset.days); setCandles(data.candles); setCatalog(await api<Manifest[]>('/datasets'));
      });
    }
    if (!dataset) return;
    await execute('/code-runs', { dataset_id: dataset.id, code: selectedConfig.code }, async result => {
      const id = (result as { run_id: string }).run_id;
      const saved = await api<Run>(`/runs/${id}`);
      if (saved.dataset_id !== dataset?.id) {
        const updated = await api<{manifest:Manifest;candles:Candle[]}>(`/datasets/${saved.dataset_id}`);
        currentDataset.current = updated.manifest.id;
        setManifest(updated.manifest); setCandles(updated.candles); setDays(updated.manifest.days);
        setCatalog(await api<Manifest[]>('/datasets'));
      }
      setRun(saved); setConfig(saved.config); setRuns(await api<RunSummary[]>('/runs?workspace=custom')); setAnalyses({}); setSelectedTrade(null);
      return `Backtest complete: ${saved.metrics.trades} trades. Results appear below the editor.`;
    });
  }
  async function downloadAll() {
    try {setError('');setArchiveJob(await api<Job>('/archive',{symbols:['BTCUSD','ETHUSD'],auxiliary:true}));}
    catch(e){setError((e as Error).message);}
  }
  async function fullChartDataset() {
    let dataset=manifest?.days===0?manifest:catalog.find(d=>d.symbol===symbol&&d.resolution===resolution&&d.days===0);
    if(!dataset) await execute('/datasets/sync',{symbol,resolution,days:0},async result=>{dataset=result as Manifest;setCatalog(await api<Manifest[]>('/datasets'));});
    if(!dataset)throw new Error('Load the full archive in Data explorer before browsing history.');
    currentDataset.current=dataset.id;setManifest(dataset);setDays(0);
    return dataset;
  }
  async function pageCandles(direction:'older'|'newer') {
    if(chartRequest.current||!candles.length)return;
    chartRequest.current=true;setLoadingOlder(true);
    try {
      const edge=direction==='older'?candles[0].time:candles[candles.length-1].time;
      const dataset=await fullChartDataset();
      const result=await api<{candles:Candle[]}>(`/datasets/${dataset.id}?${direction==='older'?'before':'after'}=${edge}&limit=3000`);
      if(currentDataset.current!==dataset.id)return;
      setCandles(previous=>{const merged=[...new Map([...previous,...result.candles].map(row=>[row.time,row])).values()].sort((a,b)=>a.time-b.time);return direction==='older'?merged.slice(0,30000):merged.slice(-30000);});
    }catch(e){setError((e as Error).message);}finally{chartRequest.current=false;setLoadingOlder(false);}
  }
  async function olderCandles(){await pageCandles('older');}
  async function newerCandles(){await pageCandles('newer');}
  async function jumpChart(where:'earliest'|'latest'|'date') {
    if(chartRequest.current)return;
    if(where==='date'&&!chartDate){setError('Choose a chart date first.');return;}
    chartRequest.current=true;setLoadingOlder(true);setError('');
    try{
      const dataset=await fullChartDataset();
      const step=({ '1m':60,'3m':180,'5m':300,'15m':900,'30m':1800,'1h':3600,'2h':7200,'4h':14400,'6h':21600,'1d':86400,'1w':604800 } as const)[resolution];
      const target=where==='earliest'?dataset.start:where==='latest'?dataset.end-step:Date.parse(chartDate+'T00:00:00Z')/1000;
      if(target<Math.floor(dataset.start/86400)*86400||target>=dataset.end)throw new Error(`Archived ${symbol} prices are available from ${date(dataset.start)} to ${date(dataset.end-step)} UTC.`);
      const query=where==='earliest'?`after=${dataset.start-1}`:where==='latest'?'':`before=${Math.min(dataset.end,target+150*step)}`;
      const result=await api<{candles:Candle[]}>(`/datasets/${dataset.id}?limit=3000&${query}`);
      if(currentDataset.current!==dataset.id)return;
      if(!result.candles.length)throw new Error('Delta has no archived candles near that date. Choose another date.');
      setCandles(result.candles);setSelectedTrade(null);setReplayIndex(null);setPlaying(false);
      setChartFocus({time:target,token:Date.now()});
      if(where!=='date')setChartDate(new Date(target*1000).toISOString().slice(0,10));
    }catch(e){setError((e as Error).message);}finally{chartRequest.current=false;setLoadingOlder(false);}
  }
  async function analyze(kind: AnalysisKind) {
    if (!run) return;
    if (kind === 'bootstrap' && (paths < 100 || paths > 2000 || blockSize < 2 || blockSize > 100)) { setError('Choose 100–2,000 bootstrap paths and a block size of 2–100 days before running Monte Carlo.'); return; }
    setAnalysisKind(kind);
    await execute('/analyses', { run_id: run.id, kind, seed, paths, block_size: blockSize }, async result => { setAnalyses(previous => ({ ...previous, [kind]: result as Analysis })); });
  }
  async function loadRun(id: string) {
    setError('');
    try {
      const saved = await api<Run>(`/runs/${id}`);
      const data = await api<{ manifest: Manifest; candles: Candle[] }>(`/datasets/${saved.dataset_id}`);
      currentDataset.current = data.manifest.id;
      setSymbol(saved.symbol); setResolution(saved.resolution); setDays(data.manifest.days);
      setManifest(data.manifest); setCandles(data.candles); setRun(saved); setConfig(saved.config);
      setAnalyses({}); setSelectedTrade(null); setPage('Research'); setTab('Overview');
    } catch (e) { setError((e as Error).message); }
  }

  const equityData = useMemo<Data[]>(() => !run ? [] : [
    { x: run.benchmark.map(p => new Date(p.time * 1000)), y: run.benchmark.map(p => p.equity), type: 'scatter', mode: 'lines', name: 'Normalized price', line: { color: '#626262', width: 1, dash: 'dot' } },
    { x: run.equity.map(p => new Date(p.time * 1000)), y: run.equity.map(p => p.equity), type: 'scatter', mode: 'lines', name: 'Strategy', line: { color: '#eee', width: 1.6 } },
  ], [run]);
  const drawdownData = useMemo<Data[]>(() => !run ? [] : [{ x: run.equity.map(p => new Date(p.time * 1000)), y: run.equity.map(p => p.drawdown_pct), type: 'scatter', mode: 'lines', line: { color: '#aaa', width: 1 }, fill: 'tozeroy', fillcolor: '#ffffff0a', hovertemplate: '%{y:.2f}%<extra>Drawdown</extra>' }], [run]);
  const bootstrapResult = analyses.bootstrap;
  const monteData = useMemo<Data[]>(() => {
    if (!bootstrapResult?.x) return [];
    const result: Data[] = (bootstrapResult.samples ?? []).map(y => ({ x: bootstrapResult.x, y, type: 'scatter', mode: 'lines', line: { color: '#ffffff16', width: .6 }, hoverinfo: 'skip' }));
    result.push({ x: bootstrapResult.x, y: bootstrapResult.p05, type: 'scatter', mode: 'lines', line: { color: '#666', width: 1 }, name: 'P05' });
    result.push({ x: bootstrapResult.x, y: bootstrapResult.p95, type: 'scatter', mode: 'lines', line: { color: '#666', width: 1 }, fill: 'tonexty', fillcolor: '#ffffff05', name: 'P95' });
    result.push({ x: bootstrapResult.x, y: bootstrapResult.p50, type: 'scatter', mode: 'lines', line: { color: '#fff', width: 1.6 }, name: 'Median' });
    return result;
  }, [bootstrapResult]);
  const surface = analyses.optimization;
  const surfaceData = useMemo<Data[]>(() => !surface ? [] : [{ type: three ? 'surface' : 'heatmap', x: surface.x, y: surface.y, z: surface.z, colorscale: [[0, '#191919'], [.5, '#777'], [1, '#eee']], showscale: false, hovertemplate: `${surface.x_label ?? 'Parameter 1'} %{x}<br>${surface.y_label ?? 'Parameter 2'} %{y}<br>Sharpe %{z:.3f}<extra></extra>` } as Data], [surface, three]);
  const costsData = useMemo<Data[]>(() => !analyses.cost_stress ? [] : [{ type: 'heatmap', x: analyses.cost_stress.x, y: analyses.cost_stress.y, z: analyses.cost_stress.z, colorscale: [[0, '#171717'], [1, '#eee']], hovertemplate: 'Fee %{x} bps<br>Slippage %{y} bps<br>Return %{z:.2f}%<extra></extra>' }], [analyses.cost_stress]);
  const walkData = useMemo<Data[]>(() => !analyses.walk_forward?.equity ? [] : [{ x: analyses.walk_forward.equity.map(p => new Date(p.time * 1000)), y: analyses.walk_forward.equity.map(p => p.equity), type: 'scatter', mode: 'lines', line: { color: '#eee', width: 1.5 }, name: 'Out-of-sample equity' }], [analyses.walk_forward]);
  const pnlData = useMemo<Data[]>(() => !run ? [] : [{ x: run.trades.map(t => t.net_pnl), type: 'histogram', marker: { color: '#bbb', line: { color: '#070707', width: 1 } }, nbinsx: 25 }], [run]);
  const fullChartManifest=catalog.find(d=>d.symbol===symbol&&d.resolution===resolution&&d.days===0)??manifest;
  const canLoadOlder=!!candles.length&&!!fullChartManifest&&candles[0].time>fullChartManifest.start;
  const canLoadNewer=!!candles.length&&!!fullChartManifest&&candles[candles.length-1].time<fullChartManifest.end-1-(({ '1m':60,'3m':180,'5m':300,'15m':900,'30m':1800,'1h':3600,'2h':7200,'4h':14400,'6h':21600,'1d':86400,'1w':604800 } as const)[resolution]-1);
  const metric = run?.metrics;
  const last = candles[replayIndex ?? candles.length - 1];
  const selected = run?.trades.find(t => t.id === selectedTrade);
  const compatible = run?.dataset_id === manifest?.id;
  const parametersChanged = run && JSON.stringify(Object.entries({...DEFAULT_CONFIG,code:'',parameters:{},start_time:null,end_time:null,...run.config}).sort()) !== JSON.stringify(Object.entries({...DEFAULT_CONFIG,code:'',parameters:{},start_time:null,end_time:null,...config}).sort());
  const controlDisabled = !!busy || backend !== 'ready';

  const montePanel = <Panel title="Monte Carlo" sub={bootstrapResult ? `${bootstrapResult.paths} paths · seed ${seed}` : 'Block bootstrap'} action={bootstrapResult ? <span className="tag">P05–P95</span> : <button className="text-button" disabled={!run || busy} onClick={() => analyze('bootstrap')}>Simulate <ArrowUpRight size={12} /></button>}>
    <div className="research-plot">{monteData.length ? <Plot data={monteData} /> : <Empty title="Explore the range of outcomes" detail="Bootstrap daily returns from a completed run." />}</div>
    <div className="panel-foot"><span>Daily return blocks</span><span>{bootstrapResult ? `P(loss) ${fmt(bootstrapResult.loss_probability_pct, '%')}` : 'Seeded · reproducible'}</span></div>
  </Panel>;
  const surfacePanel = <Panel title="Parameter landscape" action={<><button className={`seg ${!three ? 'selected' : ''}`} onClick={() => setThree(false)}>2D</button><button className={`seg ${three ? 'selected' : ''}`} onClick={() => setThree(true)}>3D</button></>}>
    <div className="research-plot">{surfaceData.length ? <Plot data={surfaceData} layout={three ? {...SURFACE_LAYOUT,scene:{...SURFACE_LAYOUT.scene,xaxis:{...SURFACE_LAYOUT.scene?.xaxis,title:{text:surface?.x_label??'Parameter 1'}},yaxis:{...SURFACE_LAYOUT.scene?.yaxis,title:{text:surface?.y_label??'Parameter 2'}}}} : {...HEAT_LAYOUT,xaxis:{...HEAT_LAYOUT.xaxis,title:{text:surface?.x_label??'Parameter 1'}},yaxis:{...HEAT_LAYOUT.yaxis,title:{text:surface?.y_label??'Parameter 2'}}}} /> : <Empty title="Find a stable parameter region" detail={run?.config.strategy === 'python' ? 'Explore the first two numeric parameters of your saved Python strategy.' : 'Choose two numeric parameters in your own strategy to explore.'} action={<button className="text-button" disabled={!run || busy} onClick={() => analyze('optimization')}>Evaluate parameters <ArrowUpRight size={12} /></button>} />}</div>
    <div className="panel-foot"><span>{surface ? `${surface.trials?.length} actual trials` : 'No interpolated results'}</span><span>In-sample exploration</span></div>
  </Panel>;

  function clearStrategy() {
    setConfig({...DEFAULT_CONFIG, parameters:{}}); setRun(null); setAnalyses({}); setSelectedTrade(null);
    setError(''); setNotice(''); setSeed(0); setPaths(0); setBlockSize(0);
  }
  function navigate(next: Page) { setPage(next); setTab('Overview'); content.current?.scrollTo({ top: 0 }); }

  const tradesPanel = <Panel title="Execution ledger" sub={run ? `Run #${run.id.slice(0, 8)} · ${run.trade_count??run.trades.length} trades` : '0 trades'} action={run && <a className="text-button" href={`/api/runs/${run.id}/export?format=csv`}>Export CSV <ArrowDownToLine size={13} /></a>} className="ledger-panel">
    {!run ? <Empty title="Your trades will appear here" detail="Run a strategy against a Delta dataset to inspect each fill and its costs." /> : <div className="table-scroll"><table><thead><tr>{['Instrument', 'Direction', 'Entry · UTC', 'Entry price', ...(run?.config.exit_mode === 'risk_reward' ? ['Stop', 'Target', 'Planned R:R', 'Net R'] : []), 'Exit price', 'Net P&L', 'Exit reason'].map(s => <th key={s}>{s}</th>)}</tr></thead><tbody>{run.trades.length ? run.trades.map(t => <tr key={t.id} className={selectedTrade === t.id ? 'selected-row' : ''} onClick={() => { setSelectedTrade(t.id); if (page !== 'Research') setPage('Research'); }} tabIndex={0} onKeyDown={e => { if (e.key === 'Enter') setSelectedTrade(t.id); }} aria-label={`Select trade ${t.id}`}>
      <td><span className="instrument-icon">{run.symbol === 'BTCUSD' ? '₿' : 'Ξ'}</span>{run.symbol} <span className="muted">#{t.id}</span></td><td>{t.side > 0 ? '↗ Long' : '↘ Short'}</td><td>{date(t.entry_time, true)}</td><td>{money(t.entry_price)}</td>{run.config.exit_mode === 'risk_reward' && <><td>{fmt(t.stop)}</td><td>{fmt(t.target)}</td><td>{t.planned_reward_risk == null ? '—' : `1:${Number(t.planned_reward_risk.toPrecision(6))}`}</td><td>{t.net_r == null ? '—' : signed(t.net_r,'R')}</td></>}<td>{money(t.exit_price)}</td><td className="bright">{signed(t.net_pnl)}</td><td><span className="tag">{t.reason.replaceAll('_', ' ')}</span></td>
    </tr>) : <tr><td colSpan={run.config.exit_mode === 'risk_reward' ? 11 : 7}>No trades met this strategy&apos;s conditions. Adjust the parameters or date range.</td></tr>}</tbody></table></div>}
    {run&&(run.trade_count??run.trades.length)>500&&<div className="panel-foot"><button className="text-button" disabled={tradeOffset===0} onClick={()=>tradePage(Math.max(0,tradeOffset-500))}>Previous 500</button><span>{tradeOffset+1}–{tradeOffset+run.trades.length} / {run.trade_count}</span><button className="text-button" disabled={tradeOffset+500>=(run.trade_count??0)} onClick={()=>tradePage(tradeOffset+500)}>Next 500</button></div>}
  </Panel>;

  return <div className="workspace">
    <aside className="sidebar"><div className="brand" aria-label="Noctra research terminal" title="Noctra · local research"><span className="brand-mark"><BrandMark/></span><span className="brand-wordmark"><BrandWordmark/><span className="sr-only">NOCTRA</span></span></div><div className="sidebar-caption">SYSTEMATIC RESEARCH</div>
      <nav>{NAV.map(([label, Icon], i) => <button key={label} aria-label={label} title={label} className={`nav-button ${page === label ? 'active' : ''}`} onClick={() => navigate(label)}><Icon size={17} strokeWidth={1.4} /><span>{label}</span><kbd>{i + 1}</kbd></button>)}<div className="planned"><Play size={16} /><span>Paper trading</span><span className="soon">Later</span></div></nav>
      <div className="project-card"><span className="eyebrow">ACTIVE PROJECT</span><strong>Your custom strategies</strong><div><span className="tag bright">Local</span><span className="mono muted">Python</span></div></div>
      <div className="sidebar-bottom"><div className="engine-card"><div><span><i className={`dot ${backend !== 'ready' ? 'dim' : ''}`} />Local engine</span><strong>{backend === 'ready' ? 'Ready' : backend === 'offline' ? 'Offline' : 'Connecting'}</strong></div><p>Python <span>Polars · NumPy</span></p><div className="engine-line"><span className={busy ? 'working' : ''} /></div></div></div>
    </aside>
    <main className="main"><header className="topbar"><div className="breadcrumb"><Layers3 size={15} /><span>Workspace</span><span>/</span><strong>{page}</strong></div></header>
      <div className="content" ref={content}>
        <div className="page-heading"><div><div className="eyebrow">QUANTITATIVE RESEARCH / {String(NAV.findIndex(n => n[0] === page) + 1).padStart(2, '0')}</div><h1>{TITLES[page]}</h1><p>{page === 'Simulations' ? 'Challenge your assumptions. Explore the distribution of outcomes.' : 'From market observation to a testable edge.'}<span className="heading-divider">/</span> BTCUSD + ETHUSD perpetuals</p></div><div className="heading-actions">{page !== 'Strategies' && tab !== 'Strategy editor' && <a className="button" href="/api/strategy-guide" download="AGENTS.md"><ArrowDownToLine size={14}/>AGENTS.md</a>}<button className="button" disabled={!run} onClick={() => setExportRun(run)}><ArrowDownToLine size={14} />Export report</button>{page !== 'Strategies' && tab !== 'Strategy editor' && <button className="button primary" disabled={controlDisabled} onClick={()=>runBacktest()}>{busy ? <LoaderCircle size={14} className="spin" /> : <Play size={13} />}Run backtest<span className="button-shortcut">↵</span></button>}</div></div>
        {manifest?.repair_info && <div className="data-provenance" role="note">{manifest.repair_info.proxy_bars.toLocaleString()} missing candles filled with adjusted Binance prices and estimated volume. Original Delta candles are unchanged. These fills are reference estimates; sparse early trading makes some adjustments uncertain.</div>}
        {error && <div className="error-banner" role="alert"><CircleHelp size={17} /><pre>{error}</pre><button aria-label="Dismiss error" onClick={() => setError('')}><X size={15} /></button></div>}
        {parametersChanged && <div className="assumption-note">Code changed. Displayed results belong to saved run #{run.id.slice(0, 8)}. Run a new backtest to evaluate the updated code.</div>}
        {busy && job && <div className="job-banner" role="status"><LoaderCircle size={14} className="spin" /><span>{job.message}</span><div className="job-track"><span style={{ width: `${job.progress}%` }} /></div><span className="mono">{Math.round(job.progress)}%</span><button className="text-button" onClick={() => api(`/jobs/${job.id}/cancel`, {}).catch(e => setError(e.message))}>Cancel</button></div>}
        {archiveBusy&&archiveJob&&<div className="job-banner" role="status"><LoaderCircle size={14} className="spin"/><span>{archiveJob.message}</span><div className="job-track"><span style={{width:`${archiveJob.progress}%`}}/></div><span className="mono">{Math.round(archiveJob.progress)}%</span><button className="text-button" onClick={()=>api(`/jobs/${archiveJob.id}/cancel`,{}).catch(e=>setError(e.message))}>Pause archive</button></div>}
        {page !== 'Strategies' && <div className="workspace-tabs"><div>{['Overview', 'Strategy editor', 'Trade analysis', 'Walk-forward'].map(t => <button key={t} className={tab === t ? 'active' : ''} onClick={() => { setTab(t); if (page !== 'Research') setPage('Research'); }}>{t}</button>)}</div><div className="range-controls"><select aria-label="History range" value={days} disabled={controlDisabled} onChange={e => setDays(Number(e.target.value))}>{[7, 30, 90, 180, 365, 0].map(d => <option key={d} value={d}>{d===0?'All archived history':`${d} days`}</option>)}</select><button className="icon-button" aria-label="Refresh Delta dataset" title="Download a new immutable snapshot" disabled={controlDisabled} onClick={download}><RefreshCw size={14} className={busy && job?.kind === 'download' ? 'spin' : ''} /></button></div></div>}
        {((page !== 'Strategies' && tab !== 'Strategy editor') || run) && <div className="metrics">{[
          ['Net return', run ? signed(metric?.net_return_pct, '%') : '0.00%', 'After fees + slippage · funding excluded'],
          ['Sharpe ratio', run ? fmt(metric?.sharpe) : '0.00', metric ? `${metric.daily_observations} daily observations · 365-day basis` : 'Daily returns · 365-day basis'],
          ['Max drawdown', metric ? `${money(metric.max_drawdown_pct)}%` : '0.00%', 'Peak-to-trough equity decline'],
          ['Profit factor', run ? fmt(metric?.profit_factor) : '0.00', metric ? `${metric.trades} trades · ${money(metric.win_rate_pct, 1)}% win rate` : '0 trades · no backtest executed'],
        ].map(([label, value, detail]) => <div className="metric" key={label}><label>{label}<ArrowUpRight size={12} /></label><strong>{value}</strong><small>{detail}</small></div>)}</div>}

        {page === 'Data explorer' ? <div className="data-page"><Panel title="Full BTCUSD / ETHUSD history" sub="Resumable · compressed · local"><div className="archive-intro"><div><h3>Your local Delta data library</h3><p>Download native trade candles from product launch for all 11 supported timeframes (1m through 1w), plus hourly mark-price, funding-rate, open-interest and spot-index histories where Delta provides them. Native 5m candles are archived directly; local aggregation is available while a native timeframe downloads.</p><p>Historical order-book depth and every historical trade are not exposed by this candle endpoint. Missing or unavailable series remain explicit.</p></div><button className="button primary" disabled={controlDisabled||archiveBusy} onClick={downloadAll}><ArrowDownToLine size={14}/>{archiveBusy?'Downloading in background…':archive.streams.length?'Resume / update all history':'Download all BTC + ETH history'}</button></div><div className="table-scroll"><table><thead><tr><th>Instrument</th><th>Series</th><th>Native</th><th>Rows</th><th>Available interval · UTC</th><th>Compressed</th><th>Status</th></tr></thead><tbody>{archive.streams.map(stream=><tr key={stream.symbol+stream.kind}><td>{stream.symbol}</td><td>{stream.kind.replace('_',' ')}</td><td>{stream.resolution}</td><td>{stream.rows.toLocaleString()}</td><td>{stream.start?date(stream.start):'—'} – {stream.end?date(stream.end):'—'}</td><td>{(stream.bytes/1048576).toFixed(1)} MB</td><td>{stream.state}{stream.missing_internal_bars?` · ${stream.missing_internal_bars} missing bars`:''}{stream.errors?.length?` · ${stream.errors[0]}`:''}</td></tr>)}</tbody></table></div><div className="panel-foot"><span>Archive: {archive.state}</span><span>Zstandard Parquet · month folders · time-range pruning</span></div></Panel><Panel title="Immutable market snapshots" sub="Delta Exchange India · Parquet" action={<button className="text-button" disabled={controlDisabled} onClick={download}>Download selected range <ArrowDownToLine size={13} /></button>}>
          <div className="dataset-toolbar"><Field label="Instrument"><select aria-label="Dataset instrument" value={symbol} onChange={e => setSymbol(e.target.value as SymbolName)} disabled={controlDisabled}><option>BTCUSD</option><option>ETHUSD</option></select></Field><Field label="Resolution"><select aria-label="Dataset resolution" value={resolution} onChange={e => setResolution(e.target.value as Resolution)} disabled={controlDisabled}>{RESOLUTIONS.map(r => <option key={r}>{r}</option>)}</select></Field></div>
          <div className="table-scroll"><table><thead><tr>{['Instrument', 'Resolution', 'Coverage', 'Candles', 'Date range · UTC', 'Snapshot', ''].map(s => <th key={s}>{s}</th>)}</tr></thead><tbody>{catalog.map(d => <tr key={d.id}><td>{d.symbol}</td><td>{d.resolution}</td><td>{d.coverage_pct}% {d.gaps.length ? `· ${d.gaps.length} gaps` : '· no internal gaps'}</td><td>{money(d.rows, 0)}</td><td>{date(d.start)} – {date(d.end)}</td><td className="muted">{d.id.slice(0, 10)}</td><td><button className="text-button" onClick={() => { setSymbol(d.symbol); setResolution(d.resolution); setDays(d.days); setPage('Research'); }}>Open <ArrowUpRight size={13} /></button></td></tr>)}</tbody></table></div>{!catalog.length && <Empty title="Build your local market archive" detail="Closed Delta candles are validated, deduplicated and saved as immutable Parquet snapshots." />}
        </Panel>{manifest && <Panel title="Selected dataset manifest" sub={manifest.id}><div className="manifest-details"><div><span>Venue</span><strong>{manifest.source}</strong></div><div><span>Finalized candles</span><strong>{money(manifest.rows, 0)}</strong></div><div><span>Contract unit</span><strong>{manifest.contract_unit} {symbol === 'BTCUSD' ? 'BTC' : 'ETH'}</strong></div><div><span>Price tick</span><strong>{manifest.tick_size}</strong></div><div><span>Retrieved</span><strong>{new Date(manifest.fetched_at).toLocaleString('en-GB', { timeZone: 'UTC' })} UTC</strong></div><div><span>SHA-256</span><code>{manifest.sha256}</code></div></div>{manifest.gaps.length > 0 && <div className="assumption-note">Missing timestamps are repaired on Run backtest using separate reference overlays. Unavailable data or insufficient calibration remains an explicit error.</div>}</Panel>}</div>
        : page === 'Backtests' ? <Panel title="Saved experiments" sub={`${runs.length} local runs`}><div className="table-scroll"><table><thead><tr>{['Run', 'Instrument', 'Strategy', 'Parameters', 'Return', 'Drawdown', 'Created · UTC', ''].map(s => <th key={s}>{s}</th>)}</tr></thead><tbody>{runs.map(r => <tr key={r.id}><td>#{r.id.slice(0, 8)}</td><td>{r.symbol} · {r.resolution}</td><td>{r.config.name}</td><td>{r.config.strategy === 'python' ? Object.entries(r.config.parameters ?? {}).map(([key, value]) => `${key}=${String(value)}`).join(', ') || 'No custom parameters' : `${r.config.fast} / ${r.config.slow}`} <span className="tag">{r.config.leverage}×</span></td><td>{signed(r.metrics.net_return_pct, '%')}</td><td>{money(r.metrics.max_drawdown_pct)}%</td><td>{new Date(r.created_at).toLocaleString('en-GB', { timeZone: 'UTC' })}</td><td><button className="text-button" onClick={() => loadRun(r.id)}>Inspect <ArrowUpRight size={13} /></button><button className="text-button backtest-export" aria-label={`Export run ${r.id.slice(0, 8)}`} onClick={async () => { try { setExportRun(await api<Run>(`/runs/${r.id}`)); } catch (e) { setError((e as Error).message); } }}><ArrowDownToLine size={13}/>Export</button></td></tr>)}</tbody></table></div>{!runs.length && <Empty title="Every experiment leaves a trace" detail="Completed runs retain their dataset hash, strategy parameters, ledger and execution assumptions." />}</Panel>
        : page === 'Strategies' || tab === 'Strategy editor' ? <><StrategyWorkbench config={config} update={updateConfig} symbol={symbol} resolution={resolution} manifest={manifest} busy={controlDisabled} onRun={runBacktest} onReset={clearStrategy} error={error} result={run}/>{run && <div className="two-columns strategy-results"><Panel title="Strategy equity" sub={`Run #${run.id.slice(0,8)} · ${run.symbol} ${run.resolution}`}><div className="research-plot tall"><Plot data={equityData}/></div></Panel><Panel title="Drawdown" sub="Percent"><div className="research-plot tall"><Plot data={drawdownData}/></div></Panel></div>}{run && tradesPanel}</>
        : page === 'Simulations' || tab === 'Walk-forward' ? <div className="simulation-page"><div className="simulation-controls"><div><h3>Run a controlled experiment</h3><p>{run ? `Based on #${run.id.slice(0, 8)} · ${run.symbol} ${run.resolution}` : 'Complete a backtest to enable analyses.'}</p></div><label>Seed<input type="number" min={0} max={2147483647} value={seed} onChange={e => setSeed(Number(e.target.value))} /></label><label>Bootstrap paths<input aria-label="Bootstrap paths" type="number" min={0} max={2000} value={paths} onChange={e => setPaths(Number(e.target.value))}/></label><label>Block size · days<input aria-label="Bootstrap block size" type="number" min={0} max={100} value={blockSize} onChange={e => setBlockSize(Number(e.target.value))}/></label></div><div className="analysis-buttons">{([['bootstrap', 'Monte Carlo'], ['optimization', 'Parameter search'], ['walk_forward', 'Walk-forward'], ['cost_stress', 'Execution costs']] as const).map(([kind, title]) => <button className={`button ${analysisKind === kind ? 'selected' : ''}`} key={kind} disabled={!run || busy} onClick={() => { setAnalysisKind(kind); if (!analyses[kind]) void analyze(kind); }}>{analyses[kind] ? <Check size={13} /> : <Play size={12} />}{title}</button>)}<button className="text-button" disabled={!run || busy} onClick={() => analyze(analysisKind)}>Rerun selected <RefreshCw size={12} /></button></div>
          {analysisKind === 'bootstrap' ? <div className="simulation-charts">{montePanel}<Panel title="Terminal return distribution" sub="Percent"><div className="research-plot">{bootstrapResult ? <Plot data={[{ x: bootstrapResult.terminal_returns, type: 'histogram', marker: { color: '#aaa', line: { color: '#070707', width: 1 } } }]} /> : <Empty title="Waiting for a bootstrap run" />}</div></Panel></div>
          : analysisKind === 'optimization' ? <div className="large-surface">{surfacePanel}</div>
          : analysisKind === 'walk_forward' ? <Panel title="Out-of-sample fold results" sub="Expanding training · frozen next-window test"><div className="large-plot">{walkData.length ? <Plot data={walkData} /> : <Empty title="Evaluate through time" detail="Evaluate your frozen Python strategy across chronological unseen windows." action={<button className="button" disabled={!run || busy} onClick={() => analyze('walk_forward')}><Play size={12} />Run walk-forward</button>} />}</div>{analyses.walk_forward?.folds && <div className="table-scroll"><table><thead><tr><th>Fold</th><th>Test window · UTC</th><th>Frozen strategy</th><th>Return</th><th>Sharpe</th><th>Trades</th></tr></thead><tbody>{analyses.walk_forward.folds.map(f => <tr key={f.fold}><td>{f.fold}</td><td>{date(f.test_start)} – {date(f.test_end)}</td><td>{run?.config.strategy==='python'?'Python parameters':`${f.fast} / ${f.slow}`}</td><td>{signed(f.metrics.net_return_pct, '%')}</td><td>{fmt(f.metrics.sharpe)}</td><td>{f.metrics.trades}</td></tr>)}</tbody></table></div>}</Panel>
          : <Panel title="Execution cost sensitivity" sub="Fee × slippage · net return"><div className="large-plot">{costsData.length ? <Plot data={costsData} layout={{ ...HEAT_LAYOUT, xaxis: { title: { text: 'Fee per fill · bps' } }, yaxis: { title: { text: 'Slippage per fill · bps' } } }} /> : <Empty title="Find the cost of the apparent edge" action={<button className="button" disabled={!run || busy} onClick={() => analyze('cost_stress')}><Play size={12} />Stress execution costs</button>} />}</div></Panel>}
          <div className="assumption-note"><CircleHelp size={14} /><span>{analyses[analysisKind]?.assumptions ?? 'Analyses use the immutable dataset and settings of the selected run. Every tested parameter combination is retained.'}</span></div>
        </div>
        : tab === 'Trade analysis' ? <><div className="two-columns"><Panel title="Drawdown" sub="Percent"><div className="research-plot tall">{run ? <Plot data={drawdownData} /> : <Empty title="Complete a backtest first" />}</div></Panel><Panel title="Trade P&L distribution" sub="USD quote currency"><div className="research-plot tall">{run ? <Plot data={pnlData} /> : <Empty title="No trades to analyze" />}</div></Panel></div>{tradesPanel}</>
        : <>
          <div className="upper-grid"><Panel title="Market overview" sub="Delta perpetuals" className={expanded ? 'market-panel expanded-panel' : 'market-panel'} action={<><span className="tag">{manifest?.repair_info ? 'DELTA + ESTIMATES' : manifest ? 'CACHED' : 'EMPTY'}</span><button className="icon-button" aria-label="Expand market chart" onClick={() => setExpanded(v => !v)}>{expanded ? <X size={14} /> : <Maximize2 size={13} />}</button></>}>
            <div className="chart-toolbar"><div className="symbol-controls">{(['BTCUSD', 'ETHUSD'] as const).map(s => <button className={symbol === s ? 'selected-symbol' : ''} key={s} disabled={controlDisabled} onClick={() => setSymbol(s)}>{s}</button>)}<span className="vertical-rule" /><strong>{ticker ? money(ticker.price) : last ? money(last.close) : '—'}</strong><small>{ticker ? 'REST quote' : last ? 'Cached close' : 'No quote'}</small></div><div className="resolutions"><select aria-label="Chart timeframe" value={resolution} disabled={controlDisabled} onChange={e=>setResolution(e.target.value as Resolution)}>{RESOLUTIONS.map(r=><option key={r} value={r}>{r.toUpperCase()}</option>)}</select><button className="text-button" disabled={controlDisabled} onClick={download}>Load</button></div></div>
            <div className="history-navigation"><div className="archive-range">Archive · {fullChartManifest?`${date(fullChartManifest.start)} – ${date(fullChartManifest.end-1)} UTC`:'Not loaded'}<span>Drag chart right for older prices · history loads automatically</span></div><div className="history-actions"><button className="text-button" disabled={loadingOlder||controlDisabled} onClick={()=>jumpChart('earliest')}>Earliest</button><label>Jump to<input type="date" aria-label="Chart date" value={chartDate} onChange={e=>setChartDate(e.target.value)}/></label><button className="button history-go" disabled={loadingOlder||controlDisabled} onClick={()=>jumpChart('date')}>Go</button><button className="text-button" disabled={loadingOlder||controlDisabled} onClick={()=>jumpChart('latest')}>Latest</button></div></div>
            {last && <div className="ohlc">O <span>{money(last.open)}</span><em>H <span>{money(last.high)}</span></em><em>L <span>{money(last.low)}</span></em><em>C <span>{money(last.close)}</span></em><span className="chart-legend">{compatible && run && run.config.strategy !== 'python' ? `── EMA ${run.config.fast}　╌╌ EMA ${run.config.slow}` : 'UTC · finalized candles'}</span></div>}
            <div className="market-chart">{candles.length ? <MarketChart candles={candles} run={compatible ? run : null} selectedTrade={selectedTrade} onTradeSelect={selectTrade} replayIndex={replayIndex ?? undefined} datasetId={manifest?.id??''} focus={chartFocus} canLoadOlder={canLoadOlder} canLoadNewer={canLoadNewer} onLoadOlder={olderCandles} onLoadNewer={newerCandles} onVisibleRange={(from,to)=>setChartVisible(previous=>previous?.from===from&&previous?.to===to?previous:{from,to})} /> : <Empty title={initializing ? 'Connecting to your local workspace' : `Load ${symbol} market data`} detail="Download closed candles directly from Delta Exchange. No API key is needed." action={<button className="button" disabled={controlDisabled} onClick={download}>{busy ? <LoaderCircle size={13} className="spin" /> : <ArrowDownToLine size={14} />}{busy ? 'Downloading…' : days?`Download ${days} days`:'Load full archive'}</button>} />}</div>
            {candles.length > 0 && <div className="chart-bottom"><span aria-label="Visible chart dates">{chartVisible?`${new Date(chartVisible.from*1000).toISOString().slice(0,16).replace('T',' ')} – ${new Date(chartVisible.to*1000).toISOString().slice(0,16).replace('T',' ')} UTC`:date(candles[0].time)} · {money(candles.length,0)} loaded</span><button className="text-button" disabled={loadingOlder||!canLoadOlder} onClick={olderCandles}>{loadingOlder?'Loading history…':'Load older candles'}</button><div><button className="text-button" onClick={() => { setSelectedTrade(null); setReplayIndex(replayIndex == null ? Math.max(config.slow + 1, Math.floor(candles.length * .6)) : null); setPlaying(false); }}>{replayIndex == null ? 'Replay' : 'Exit replay'}</button>{replayIndex != null && <><button aria-label={playing ? 'Pause replay' : 'Play replay'} className="icon-button" onClick={() => setPlaying(v => !v)}>{playing ? <Pause size={12} /> : <Play size={12} />}</button><input aria-label="Replay position" type="range" min={0} max={candles.length - 1} value={replayIndex} onChange={e => { setReplayIndex(Number(e.target.value)); setPlaying(false); }} /><span>{replayIndex + 1}/{candles.length}</span></>}</div></div>}
          </Panel>
          </div>
          {selected && <div className="selected-trade"><div><strong>Trade #{selected.id} · {selected.side > 0 ? 'Long' : 'Short'}</strong><span>{date(selected.entry_time, true)} → {date(selected.exit_time, true)} UTC</span></div><div><span>{selected.contracts.toLocaleString()} contracts · {selected.quantity} base units</span><strong>Net {signed(selected.net_pnl)} · fees {money(selected.fees)}</strong></div><button className="icon-button" aria-label="Clear selected trade" onClick={() => setSelectedTrade(null)}><X size={14} /></button></div>}
          <div className="lower-grid"><Panel title="Portfolio equity" sub="Net of execution costs" action={<span className="mono">{metric ? signed(metric.net_return_pct, '%') : '0.00%'}</span>}><div className="research-plot">{run ? <Plot data={equityData} /> : <Empty title="Test your hypothesis" detail="Run the selected strategy to generate an equity curve." />}</div><div className="panel-foot"><span>━ Strategy　┄ Price benchmark</span><span>{metric ? `$${money(metric.final_equity)}` : '$0.00'}</span></div></Panel>{montePanel}{surfacePanel}</div>
          {tradesPanel}
          <div className="research-footer"><span><ShieldCheck size={13} />{run ? `Immutable run #${run.id.slice(0, 8)}` : 'Every result is traceable to its dataset and assumptions.'}</span><button className="text-button" onClick={() => setShowAssumptions(true)}>Review execution assumptions <CircleHelp size={13} /></button></div>
        </>}
      </div>
    </main>
    {exportRun && <ExportDialog run={exportRun} onClose={() => setExportRun(null)}/>}
    {notice && <div className="toast" role="status"><Check size={15} />{notice}<button aria-label="Dismiss notification" onClick={() => setNotice('')}><X size={13} /></button></div>}
    {showAssumptions && <div className="modal-backdrop" onClick={() => setShowAssumptions(false)}><section className="modal" role="dialog" aria-modal="true" aria-label="Execution assumptions" onClick={e => e.stopPropagation()}><div className="modal-head"><h2>Execution assumptions</h2><button aria-label="Close assumptions" className="icon-button" onClick={() => setShowAssumptions(false)}><X size={17} /></button></div><div className="prose"><p>These settings define what the result means.</p><ul>{(run?.assumptions ?? ['Closed-bar signals; next-open fills.', 'Fees and slippage are configurable scenarios.', 'Funding, exact exchange liquidation, partial fills and market depth are not modeled.', 'Public market data only; no account keys or order submission.']).map(s => <li key={s}>{s}</li>)}</ul>{manifest && <p className="mono break">Dataset SHA-256: {manifest.sha256}</p>}</div></section></div>}
    {showSearch && <div className="modal-backdrop" onClick={() => setShowSearch(false)}><section className="command-modal" role="dialog" aria-modal="true" aria-label="Workspace command search" onClick={e => e.stopPropagation()}><div className="command-input"><Search size={18} /><input autoFocus placeholder="Find a workspace or saved run…" value={search} onChange={e => setSearch(e.target.value)} /><kbd>ESC</kbd></div>{NAV.filter(([n]) => n.toLowerCase().includes(search.toLowerCase())).map(([n, Icon]) => <button key={n} onClick={() => { navigate(n); setShowSearch(false); }}><Icon size={15} />{n}<ArrowUpRight size={13} /></button>)}{runs.filter(r => `${r.id} ${r.symbol} ${r.config.name}`.toLowerCase().includes(search.toLowerCase())).slice(0, 5).map(r => <button key={r.id} onClick={() => { void loadRun(r.id); setShowSearch(false); }}><Terminal size={15} />#{r.id.slice(0, 8)} · {r.symbol} · {r.config.name}<ArrowUpRight size={13} /></button>)}</section></div>}
  </div>;
}
