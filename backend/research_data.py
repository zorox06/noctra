"""Immutable, causal research inputs assembled from the existing public archive.

Candles align exactly. Hourly auxiliary closes become visible at their close
time, and expire before the next missing hourly close would become available.
No orders, account data, network downloads or reconstructed liquidation tape.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import threading

import polars as pl

from . import lake, market, repair, store
from .models import SECONDS

VERSION = 'research-inputs-v1'
LOCK = threading.RLock()
BASE = ['time', 'open', 'high', 'low', 'close', 'volume', 'source', 'is_proxy']
AUXILIARY = {'mark': 'mark_price', 'funding': 'funding_rate',
             'open_interest': 'open_interest', 'index': 'index_price'}


def folder():
    path = store.ROOT / 'strategy-inputs'
    path.mkdir(parents=True, exist_ok=True)
    return path


def metadata(identifier):
    if not re.fullmatch('[a-f0-9]{16}', identifier):
        raise ValueError('Invalid research input snapshot ID')
    path = folder() / f'{identifier}.json'
    if not path.exists():
        raise ValueError('Research input snapshot is unavailable; prepare a new backtest')
    return json.loads(path.read_text(encoding='utf-8'))


def verified_path(identifier):
    meta = metadata(identifier)
    path = folder() / f'{identifier}.parquet'
    info = path.stat()
    market.verify_file(str(path), info.st_mtime_ns, info.st_size, meta['sha256'])
    return path


def inventory():
    return sorted((json.loads(path.read_text(encoding='utf-8'))
                   for path in folder().glob('*.json')),
                  key=lambda item: item['created_at'], reverse=True)


def _archive_files(symbol, kind, start, end):
    paths = []
    for path in (lake.root() / symbol / kind).glob('*/*.parquet'):
        left, right = map(int, path.stem.split('-'))
        if right > start and left < end:
            paths.append(path)
    return sorted(paths)


def prepare(manifest, start=None, end=None, progress=lambda *args: None):
    """Freeze all inputs for a new UI run; saved reanalyses reuse this ID."""
    with LOCK:
        return _prepare(manifest, start, end, progress)


def _prepare(manifest, start, end, progress):
    step = SECONDS[manifest['resolution']]
    primary_path = store.ROOT / 'datasets' / f'{manifest["id"]}.parquet'
    info = primary_path.stat()
    market.verify_file(str(primary_path), info.st_mtime_ns, info.st_size, manifest['sha256'])
    base = repair.normalized(pl.read_parquet(primary_path))
    if start is not None:
        base = base.filter(pl.col('time') >= start)
    if end is not None:
        base = base.filter(pl.col('time') < end)
    if base.is_empty():
        raise ValueError('No candles in the selected research interval')
    base = base.sort('time')
    left, right = int(base['time'][0]), int(base['time'][-1]) + step
    other = 'ETHUSD' if manifest['symbol'] == 'BTCUSD' else 'BTCUSD'
    kind = 'trade' if step == 60 else 'trade_' + manifest['resolution']
    cross_files = _archive_files(other, kind, left, right)
    aggregate_cross = not cross_files and step != 60
    if aggregate_cross:
        cross_files = _archive_files(other, 'trade', left, right)
    auxiliary_files = {(symbol, series): _archive_files(symbol, series, left - 7200, right)
                       for symbol in ('BTCUSD', 'ETHUSD') for series in AUXILIARY}
    patch_files = sorted(repair.folder(other, manifest['resolution']).glob('*'))
    paths = sorted(set(cross_files + patch_files +
                       [p for values in auxiliary_files.values() for p in values]))
    sources = [{'path': str(p.relative_to(store.ROOT)), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
               for p in paths if p.is_file()]
    identity = {'version': VERSION, 'primary_dataset_id': manifest['id'],
                'primary_sha256': manifest['sha256'], 'start': left, 'end': right,
                'symbol': manifest['symbol'], 'resolution': manifest['resolution'], 'sources': sources}
    identifier = store.identifier(identity)
    meta_path = folder() / f'{identifier}.json'
    if meta_path.exists():
        verified_path(identifier)
        return metadata(identifier)
    progress(3, 'Joining completed BTC/ETH candles and archived hourly research data')
    cross = (pl.read_parquet(cross_files).unique('time', keep='last').sort('time')
             if cross_files else pl.DataFrame(schema=lake.SCHEMA))
    if aggregate_cross:
        cross = lake.aggregate(cross, manifest['resolution'])
    cross = cross.filter((pl.col('time') >= left) & (pl.col('time') < right))
    if not cross.is_empty():
        cross, _ = repair.overlay(cross, other, manifest['resolution'])
    cross = repair.normalized(cross)
    table = base.with_columns((pl.col('time') + step).alias('bar_close_time'))
    coverage = {}
    for symbol in ('BTCUSD', 'ETHUSD'):
        prefix = symbol[:3].lower()
        candles = base if symbol == manifest['symbol'] else cross
        columns = [pl.col(name).alias(prefix + '_' + name) for name in BASE if name != 'time']
        table = table.join(candles.select('time', *columns), on='time', how='left', maintain_order='left')
        available = prefix + '_available'
        table = table.with_columns(pl.col(prefix + '_close').is_not_null().alias(available))
        coverage[available] = {'available_rows': int(table[available].sum()), 'total_rows': table.height}
        for series, label in AUXILIARY.items():
            value = prefix + '_' + label
            available_at = value + '_available_at'
            files = auxiliary_files[symbol, series]
            if files:
                aux = pl.read_parquet(files).unique('time', keep='last').sort('time')
                aux = aux.select((pl.col('time') + 3600).alias(available_at),
                                 pl.col('close').cast(pl.Float64).alias(value))
                table = table.join_asof(aux, left_on='bar_close_time', right_on=available_at,
                                        strategy='backward', tolerance=3599, check_sortedness=False)
            else:
                table = table.with_columns(pl.lit(None, dtype=pl.Float64).alias(value),
                                           pl.lit(None, dtype=pl.Int64).alias(available_at))
            coverage[value] = {'available_rows': table[value].len() - table[value].null_count(),
                               'total_rows': table.height}
    table = table.with_columns((pl.col('btc_available') & pl.col('eth_available')).alias('cross_asset_available'))
    table = table.with_columns(pl.when(pl.col('cross_asset_available') & (pl.col('eth_close') > 0))
                               .then(pl.col('btc_close') / pl.col('eth_close')).otherwise(None).alias('btc_eth_ratio'))
    path = folder() / f'{identifier}.parquet'
    temp = path.with_suffix('.tmp')
    table.write_parquet(temp, compression='zstd', statistics=True)
    temp.replace(path)
    meta = {**identity, 'id': identifier, 'created_at': datetime.now(timezone.utc).isoformat(),
            'rows': table.height, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'columns': {name: str(dtype) for name, dtype in table.schema.items()}, 'coverage': coverage,
            'alignment': 'Exact candle timestamps; backward hourly close join with age <3600 seconds; no future/backfilled/stale observations.',
            'unavailable': ['historical liquidation tape', 'historical order-book depth', 'historical aggressor flow/CVD'],
            'funding_note': 'Hourly funding-rate close is a signal input, not an applied settlement cashflow.',
            'execution_note': 'Cross-asset features are available; execution remains one instrument with fixed ratio brackets.'}
    store.atomic_json(meta_path, meta)
    return meta
