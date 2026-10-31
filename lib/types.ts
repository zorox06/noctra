export type SymbolName = 'BTCUSD' | 'ETHUSD';
export type Resolution = '1m' | '3m' | '5m' | '15m' | '30m' | '1h' | '2h' | '4h' | '6h' | '1d' | '1w';
export type Candle = { source?: string; is_proxy?: boolean; time: number; open: number; high: number; low: number; close: number; volume: number };
export type Config = {
  name: string; strategy: 'ema_cross' | 'breakout' | 'rsi_reversion' | 'python'; code?: string; parameters?: Record<string, unknown>; start_time?: number | null; end_time?: number | null; fast: number; slow: number;
  exit_mode?: 'brackets' | 'signals' | 'risk_reward'; risk_reward?: number | null; allocation_pct?: number;
  capital: number; risk_pct: number; stop_pct: number; target_pct: number;
  fee_bps: number; slippage_bps: number; leverage: number; direction: 'both' | 'long_only' | 'short_only';
};
export type Manifest = { id: string; key: string; symbol: SymbolName; resolution: Resolution; days: number;
  repair_info?: { proxy_bars: number; recovered_delta_bars: number; warning: string };
  rows: number; expected_rows: number; coverage_pct: number; start: number; end: number; fetched_at: string;
  sha256: string; contract_unit: number; tick_size: number; source: string; gaps: { start: number; end: number; bars: number }[];
};
export type Trade = { stop?: number; target?: number; initial_risk_usd?: number; planned_reward_risk?: number; requested_reward_risk?: number | null; net_r?: number; gross_r?: number; fees_r?: number; bracket_closed?: boolean; gap_through_stop?: boolean; id: number; side: number; contracts: number; quantity: number; entry_time: number; exit_time: number;
  entry_price: number; exit_price: number; net_pnl: number; gross_pnl: number; fees: number; reason: string };
export type EquityPoint = { time: number; equity: number; cash: number; exposure: number; drawdown_pct: number };
export type SkippedEntry = { reason: string; signal_row: number; signal_time: number; entry_time: number; side: number; signal_close: number; entry_open: number; entry_fill: number; stop_loss: number; take_profit: number | null; risk_reward: number | null };
export type Metrics = { skipped_entries?: number; skipped_stop_entries?: number; skipped_target_entries?: number; net_r?: number; expectancy_r?: number | null; planned_rr_min?: number | null; planned_rr_max?: number | null; target_hits?: number; stop_hits?: number; incomplete_ratio_trades?: number; net_return_pct: number; net_pnl: number; final_equity: number; max_drawdown_pct: number;
  sharpe: number | null; profit_factor: number | null; win_rate_pct: number; trades: number; fees: number;
  expectancy: number; annualized_volatility_pct: number; daily_observations: number; funding: null };
export type Run = { id: string; created_at: string; dataset_id: string; symbol: SymbolName; resolution: Resolution; config: Config; indicator_times?:number[]; trade_count?:number; curve_rows?:number; chart_sampled?:boolean;
  metrics: Metrics; assumptions: string[]; equity: EquityPoint[]; trades: Trade[]; skipped_entries?: SkippedEntry[];
  indicators: { fast: number[]; slow: number[] }; benchmark: { time: number; equity: number }[] };
export type RunSummary = Omit<Run, 'equity' | 'trades' | 'indicators' | 'benchmark'>;
export type AnalysisKind = 'bootstrap' | 'optimization' | 'walk_forward' | 'cost_stress';
export type Analysis = { id: string; run_id: string; kind: AnalysisKind; assumptions: string;
  x_label?:string;y_label?:string;
  x?: number[]; y?: number[]; z?: (number | null)[][]; samples?: number[][]; p05?: number[]; p50?: number[]; p95?: number[];
  seed?: number; paths?: number; days?: number; loss_probability_pct?: number; median_return_pct?: number; drawdown_p05?: number;
  terminal_returns?: number[]; trials?: Record<string, number | string | null>[]; equity?: EquityPoint[];
  net_return_pct?: number; folds?: { fold: number; fast: number; slow: number; test_start: number; test_end: number; metrics: Metrics }[] };
export type Job = { id: string; kind: string; status: 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled' | 'interrupted';
  progress: number; message: string; error?: string; result?: unknown };

export const DEFAULT_CONFIG: Config = { name: '', strategy: 'python', code: '', parameters: {}, start_time: null, end_time: null, fast: 0, slow: 0,
  capital: 0, risk_pct: 0, stop_pct: 0, target_pct: 0, fee_bps: 0, slippage_bps: 0, leverage: 0, direction: 'both' };
