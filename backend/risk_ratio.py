"""Risk/reward parsing. Distances are price units; ratios are dimensionless."""
import math


def parse_ratio(value):
    if isinstance(value, bool):
        raise ValueError('Risk/reward must be a positive number or a ratio such as 1:2.5')
    try:
        if isinstance(value,str) and ':' in value:
            parts=value.split(':')
            if len(parts)!=2:raise ValueError()
            risk,reward=(float(part.strip()) for part in parts)
            if not all(math.isfinite(x) and x>0 for x in (risk,reward)):raise ValueError()
            result=reward/risk
        else:result=float(value)
        if not math.isfinite(result) or result<=0:raise ValueError()
        return result
    except (ValueError,TypeError,OverflowError) as error:
        raise ValueError('Risk/reward must be positive and finite, e.g. 2.5 or "1:2.5". It is not a percentage.') from error
