"""Materialize fast, immutable native-timeframe views after a full download."""
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend import lake
from backend.models import SECONDS

for symbol in ['BTCUSD','ETHUSD']:
    product=json.loads((lake.root()/f'{symbol}-product.json').read_text())
    for resolution in SECONDS:
        result=lake.snapshot(symbol,resolution,0,product)
        print(f"{symbol} {resolution}: {result['rows']:,} candles, {len(result['gaps'])} gap intervals",flush=True)
    lake.snapshot(symbol,'5m',30,product)
