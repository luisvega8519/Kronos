"""Conservative bar-based MES research harness (standard library only)."""
import argparse
import csv
import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')

@dataclass(frozen=True)
class Config:
    initial_balance: float = 10000.0
    point_value: float = 5.0
    tick: float = 0.25
    commission_per_side: float = 1.25
    slippage_ticks: int = 1
    risk_fraction: float = 0.005
    daily_loss_fraction: float = 0.015
    atr_multiplier: float = 1.5
    horizon: int = 3
    max_contracts: int = 10


def load_bars(path):
    with open(path, newline='') as f:
        rows = list(csv.DictReader(f))
    bars = []
    for row in rows:
        t = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
        if t.tzinfo is None:
            raise ValueError('Timestamps must include timezone; timestamp means bar CLOSE.')
        b = {k: float(row[k]) for k in ('open', 'high', 'low', 'close', 'volume')}
        if not all(math.isfinite(v) for v in b.values()) or b['volume'] < 0:
            raise ValueError('Nonfinite data or negative volume')
        if not (0 < b['low'] <= min(b['open'], b['close']) <= max(b['open'], b['close']) <= b['high']):
            raise ValueError('Invalid OHLC bar')
        b.update(timestamp=t, contract=row['contract'], expiry_date=datetime.fromisoformat(row['expiry_date']).date(), forecast_close=None)
        if not b['contract']:
            raise ValueError('Explicit individual contract is required')
        if row.get('forecast_close', '') != '':
            b['forecast_close'] = float(row['forecast_close'])
            if not math.isfinite(b['forecast_close']) or b['forecast_close'] <= 0:
                raise ValueError('Invalid forecast')
        if bars and t <= bars[-1]['timestamp']:
            raise ValueError('Timestamps must be strictly increasing')
        bars.append(b)
    return bars


def indicators(bars, i):
    window = bars[i-49:i+1]
    if len(window) != 50 or any(b['contract'] != bars[i]['contract'] for b in window):
        return None
    if any(window[j]['timestamp'] - window[j-1]['timestamp'] != timedelta(minutes=5) for j in range(1, 50)):
        return None
    sma = sum(b['close'] for b in window) / 50
    # Explicit simple average true range, not Wilder smoothing.
    tr = [max(bars[j]['high']-bars[j]['low'], abs(bars[j]['high']-bars[j-1]['close']),
              abs(bars[j]['low']-bars[j-1]['close'])) for j in range(i-13, i+1)]
    return sma, sum(tr) / 14


def simulate(bars, variant, config=Config()):
    if variant not in ('fixed', 'trailing'):
        raise ValueError('Unknown exit variant')
    c = config
    if c.initial_balance <= 0 or c.tick <= 0 or c.point_value <= 0 or c.atr_multiplier <= 0 or c.horizon < 1 or c.max_contracts < 1:
        raise ValueError('Invalid configuration')
    if not (0 < c.risk_fraction <= 1 and 0 < c.daily_loss_fraction <= 1) or c.commission_per_side < 0 or c.slippage_ticks < 0:
        raise ValueError('Invalid risk or costs')
    balance = c.initial_balance
    peak = balance
    max_dd = 0.0
    day = None
    day_start = balance
    trades = []
    signals = []
    slip = c.tick * c.slippage_ticks
    cost_points = 2 * slip + 2 * c.commission_per_side / c.point_value
    i = 49
    while i + c.horizon < len(bars):
        b = bars[i]
        local = b['timestamp'].astimezone(NY)
        if local.date() != day:
            day, day_start = local.date(), balance
        values = indicators(bars, i)
        # Deliberately restrict to US cash session, finish well before daily close.
        minute = local.hour * 60 + local.minute
        if (values is None or b['forecast_close'] is None or not 570 <= minute <= 945
                or local.date() >= b['expiry_date'] or balance <= day_start * (1-c.daily_loss_fraction)):
            i += 1
            continue
        sma, atr = values
        distance = math.ceil(c.atr_multiplier * atr / c.tick) * c.tick
        if distance <= 0 or b['close'] <= sma or b['forecast_close'] - b['close'] <= distance + cost_points:
            i += 1
            continue
        future = bars[i+1:i+1+c.horizon]
        # Future metadata validates availability, never used to select price signals.
        if any(x['contract'] != b['contract'] or x['timestamp'] != b['timestamp'] + timedelta(minutes=5*(j+1))
               for j, x in enumerate(future)):
            i += 1
            continue
        qty = min(c.max_contracts, int(balance*c.risk_fraction / (distance*c.point_value + cost_points*c.point_value)))
        if qty < 1:
            i += 1
            continue
        entry = future[0]['open'] + slip
        stop = entry-distance
        target = entry+2*distance
        high_water = entry
        reason = 'time'
        exit_price = future[-1]['close']-slip
        exit_index = i+c.horizon
        for j, bar in enumerate(future, start=i+1):
            # Open gaps are processed first; stop wins if high/low touch both levels.
            if bar['open'] <= stop:
                exit_price, reason, exit_index = bar['open']-slip, 'gap_stop', j
                break
            if variant == 'fixed' and bar['open'] >= target:
                exit_price, reason, exit_index = target-slip, 'target', j
                break
            if bar['low'] <= stop:
                exit_price, reason, exit_index = stop-slip, 'stop', j
                break
            if variant == 'fixed' and bar['high'] >= target:
                exit_price, reason, exit_index = target-slip, 'target', j
                break
            if variant == 'trailing':
                high_water = max(high_water, bar['high'])
                if high_water >= entry+distance:
                    # New stop effective NEXT bar: OHLC cannot establish event order.
                    stop = max(stop, math.floor((high_water-distance)/c.tick + 1e-9)*c.tick)
        pnl = qty*((exit_price-entry)*c.point_value-2*c.commission_per_side)
        balance += pnl
        peak = max(peak, balance)
        max_dd = max(max_dd, peak-balance)
        trades.append(dict(signal_time=b['timestamp'].isoformat(), entry_time=(future[0]['timestamp']-timedelta(minutes=5)).isoformat(),
                           exit_bar_close=bars[exit_index]['timestamp'].isoformat(), contract=b['contract'],
                           contracts=qty, entry=entry, exit=exit_price, initial_stop=entry-distance,
                           pnl=pnl, balance=balance, reason=reason))
        signals.append(dict(timestamp=b['timestamp'].isoformat(), forecast_close=b['forecast_close'], sma=sma, atr=atr))
        i += c.horizon  # Common cooldown keeps entry opportunities aligned across variants.
    wins = sum(t['pnl'] > 0 for t in trades)
    gross_win = sum(max(0, t['pnl']) for t in trades)
    gross_loss = -sum(min(0, t['pnl']) for t in trades)
    return dict(variant=variant, config=asdict(c), trade_count=len(trades), net_pnl=balance-c.initial_balance,
                final_balance=balance, win_rate=wins/len(trades) if trades else None,
                profit_factor=gross_win/gross_loss if gross_loss else None, max_closed_equity_drawdown=max_dd,
                average_pnl=(balance-c.initial_balance)/len(trades) if trades else None, trades=trades, signals=signals)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', help='MES individual-contract bars, timestamp at bar CLOSE')
    parser.add_argument('--output', default='futures-results')
    parser.add_argument('--balance', type=float, default=10000)
    parser.add_argument('--commission', type=float, default=1.25, help='USD per contract per side; replace with broker costs')
    parser.add_argument('--slippage-ticks', type=int, default=1)
    args = parser.parse_args()
    bars = load_bars(args.csv)
    if not any(b['forecast_close'] is not None for b in bars):
        raise ValueError('No forecasts: generate causal Kronos forecasts first')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    c = Config(initial_balance=args.balance, commission_per_side=args.commission, slippage_ticks=args.slippage_ticks)
    for variant in ('fixed', 'trailing'):
        result = simulate(bars, variant, c)
        (output / (variant+'.json')).write_text(json.dumps(result, indent=2, allow_nan=False))
        print(json.dumps({k:v for k,v in result.items() if k not in ('trades', 'signals', 'config')}))

if __name__ == '__main__':
    main()
