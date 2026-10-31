from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

Symbol = Literal['BTCUSD', 'ETHUSD']
Resolution = Literal['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '1d', '1w']
SECONDS = {'1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '2h': 7200, '4h': 14400, '6h': 21600, '1d': 86400, '1w': 604800}


class SyncRequest(BaseModel):
    symbol: Symbol = 'BTCUSD'
    resolution: Resolution = '1h'
    days: int = Field(default=30, ge=0, le=5000)


class StrategyConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    name: str = Field(default='Adaptive trend', min_length=1, max_length=100)
    strategy: Literal['ema_cross', 'breakout', 'rsi_reversion', 'python'] = 'ema_cross'
    code: str = Field(default='', max_length=100000)
    parameters: dict = Field(default_factory=dict)
    start_time: int | None = Field(default=None, ge=0)
    end_time: int | None = Field(default=None, ge=0)
    fast: int = Field(default=20, ge=0, le=200)
    slow: int = Field(default=50, ge=0, le=500)
    capital: float = Field(default=100000, ge=100, le=100000000)
    research_input_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{16}$')
    entry_gap_policy: Literal['error', 'skip'] = 'error'
    exit_mode: Literal['brackets', 'signals', 'risk_reward'] = 'brackets'
    risk_reward: float | None = Field(default=None, gt=0)
    allocation_pct: float = Field(default=100, gt=0, le=100)
    risk_pct: float = Field(default=1, ge=0, le=5)
    stop_pct: float = Field(default=1.5, ge=0, le=20)
    target_pct: float = Field(default=3, ge=0, le=50)
    fee_bps: float = Field(default=5, ge=0, le=100)
    slippage_bps: float = Field(default=2, ge=0, le=100)
    leverage: float = Field(default=1, ge=0.1, le=5)
    direction: Literal['both', 'long_only', 'short_only'] = 'both'
    @model_validator(mode='after')
    def periods(self):
        if self.exit_mode == 'brackets' and (self.risk_pct < .05 or self.stop_pct < .1 or self.target_pct < .1):
            raise ValueError('Legacy bracket runs require positive risk, stop and target settings')
        if self.exit_mode in ('signals','risk_reward') and (self.strategy != 'python' or self.risk_pct or self.stop_pct or self.target_pct):
            raise ValueError('Code-only exits require Python code and zero legacy percentage risk/stop/target fields')
        if self.strategy != 'python' and (self.fast < 2 or self.slow < 3 or self.fast >= self.slow):
            raise ValueError('Fast period must be smaller than slow period, with valid legacy warm-up periods')
        if self.start_time is not None and self.end_time is not None and self.start_time >= self.end_time:
            raise ValueError('Test start must be before test end')
        if self.strategy == 'python' and not self.code.strip():
            raise ValueError('Paste Python code defining strategy(data, params) first')
        return self


class ArchiveRequest(BaseModel):
    symbols: list[Symbol] = Field(default_factory=lambda: ['BTCUSD', 'ETHUSD'], min_length=1, max_length=2)
    auxiliary: bool = True
    timeframes: list[Resolution] = Field(default_factory=lambda: ['1m','5m','3m','15m','30m','1h','2h','4h','6h','1d','1w'], min_length=1, max_length=11)


class RunRequest(BaseModel):
    dataset_id: str = Field(pattern=r'^[a-f0-9]{16}$')
    config: StrategyConfig

    @model_validator(mode='before')
    @classmethod
    def explicit_settings(cls, value):
        cfg = value.get('config', {}) if isinstance(value, dict) else {}
        required = ('name','strategy','code','parameters','capital','risk_pct','stop_pct','target_pct','fee_bps','slippage_bps','leverage','direction')
        if isinstance(cfg, dict) and any(key not in cfg for key in required):
            raise ValueError('Provide your own Python code, name, Parameters JSON and all execution settings; no strategy defaults are inserted.')
        return value

    @model_validator(mode='after')
    def custom_only(self):
        if self.config.strategy != 'python':
            raise ValueError('Built-in strategies have been removed. Supply your own Python strategy.')
        if self.config.fast != 0 or self.config.slow != 0:
            raise ValueError('Set legacy fast/slow fields to zero. Custom strategy periods belong in Parameters JSON.')
        if not self.config.name.strip():
            raise ValueError('Enter a nonblank strategy name.')
        return self


class AnalysisRequest(BaseModel):
    run_id: str = Field(pattern=r'^[a-f0-9]{16}$')
    kind: Literal['bootstrap', 'optimization', 'walk_forward', 'cost_stress']
    seed: int = Field(default=0, ge=0, le=2147483647)
    paths: int = Field(default=0, ge=0, le=2000)
    block_size: int = Field(default=0, ge=0, le=100)

    @model_validator(mode='after')
    def bootstrap_settings(self):
        if self.kind == 'bootstrap' and (self.paths < 100 or self.block_size < 2):
            raise ValueError('Monte Carlo requires 100–2,000 paths and a block size of 2–100 days.')
        return self


class CodeRunRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dataset_id: str = Field(pattern=r'^[a-f0-9]{16}$')
    code: str = Field(min_length=1, max_length=100000)

    @model_validator(mode='after')
    def source_required(self):
        if not self.code.strip():
            raise ValueError('Paste Python code defining strategy(data, params) first')
        return self


class SourceSettings(BaseModel):
    """Optional BACKTEST dictionary in source. No UI execution settings."""
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    name: str = Field(default='Python strategy', min_length=1, max_length=100)
    capital: float = Field(default=10000, ge=100, le=100000000)
    leverage: float = Field(default=1, ge=.1, le=5)
    allocation_pct: float = Field(default=100, gt=0, le=100)
    fee_bps: float = Field(default=7, ge=0, le=100)
    slippage_bps: float = Field(default=1, ge=0, le=100)
    parameters: dict = Field(default_factory=dict)
    risk_reward: float | None = Field(default=None, gt=0)

    @field_validator('risk_reward', mode='before')
    @classmethod
    def ratio_value(cls,value):
        from .risk_ratio import parse_ratio
        return None if value is None else parse_ratio(value)
    start_time: int | None = Field(default=None, ge=0)
    end_time: int | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def coherent(self):
        if not self.name.strip():
            raise ValueError('BACKTEST.name must not be blank')
        if self.start_time is not None and self.end_time is not None and self.start_time >= self.end_time:
            raise ValueError('BACKTEST.start_time must be before end_time (exclusive)')
        return self
