"""Downloadable strategy contract plus a truthful local-archive inventory."""
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
import polars as pl
from . import lake

def markdown():
    text=(Path(__file__).parent/'resources'/'strategy-guide.md').read_text(encoding='utf-8')
    stamp=datetime.now(timezone.utc).isoformat()
    text+=f'\n\n## Local availability snapshot\n\nGenerated {stamp}. Package versions: pandas {version("pandas")}, NumPy {version("numpy")}, Polars {version("polars")}.\n\n'
    text+='| Instrument | Stored series | Native frame | Rows | First candle open · UTC | End exclusive · UTC | Missing internal bars | State |\n| --- | --- | --- | ---: | --- | --- | ---: | --- |\n'
    status=lake.status()
    schemas={}
    def utc(value):return datetime.fromtimestamp(value,timezone.utc).strftime('%Y-%m-%d %H:%M') if value is not None else 'Unavailable'
    for stream in status.get('streams',[]):
        text+=f'| {stream["symbol"]} | {stream["kind"]} | {stream["resolution"]} | {stream["rows"]} | {utc(stream.get("start"))} | {utc(stream.get("end"))} | {stream.get("missing_internal_bars",0)} | {stream["state"]} |\n'
        if stream['kind'] not in schemas:
            sample=next((lake.root()/stream['symbol']/stream['kind']).glob('*/*.parquet'),None)
            if sample is not None:
                schemas[stream['kind']]=', '.join(f'`{key}`: {value}' for key,value in pl.read_parquet_schema(sample).items())
    if not status.get('streams'):
        text+='| — | No local archive yet | — | 0 | — | — | 0 | empty |\n'
    text+='\n### Observed Parquet column schemas\n\nRead from an actual stored batch per available series kind. These are disk schemas; only the standard strategy columns documented above enter `data`.\n\n'
    for kind,schema in schemas.items():text+=f'- **{kind}**: {schema}\n'
    from . import store
    text+='\n### Active candle snapshots used by the app\n\nThe raw-archive table above is unchanged Delta history. These latest full-history snapshots include separate, labeled gap repairs where available.\n\n'
    text+='| Instrument | Frame | Candles | Reference estimates | Internal gaps | Snapshot |\n| --- | --- | ---: | ---: | ---: | --- |\n'
    seen=set()
    for item in store.datasets():
        key=item['symbol'],item['resolution']
        if item.get('days')!=0 or key in seen:continue
        seen.add(key)
        text+=f'| {item["symbol"]} | {item["resolution"]} | {item["rows"]} | {item.get("repair_info",{}).get("proxy_bars",0)} | {len(item.get("gaps",[]))} | `{item["id"]}` |\n'
    from . import research_data
    text+='\n### Frozen research inputs available for download\n\nNew UI runs automatically prepare immutable research inputs. The following snapshots have already been prepared; download the Parquet locally and attach it separately if the cloud LLM needs actual observations. An AGENTS.md inventory alone contains no candle records.\n\n'
    for item in research_data.inventory()[:12]:
        text+=f'- **{item["symbol"]} {item["resolution"]}**: {item["rows"]:,} rows; {utc(item["start"])} to {utc(item["end"])} UTC (end exclusive). [Download data](http://127.0.0.1:3000/api/strategy-inputs/{item["id"]}/download); SHA-256 `{item["sha256"]}`.\n'
        for column, count in item['coverage'].items():
            text+=f'  - `{column}`: {count["available_rows"]:,} / {count["total_rows"]:,} rows available.\n'
    return text+'\n'
