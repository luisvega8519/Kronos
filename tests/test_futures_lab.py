import unittest
from datetime import datetime, timedelta, timezone
from futures_lab.backtest import Config, simulate, indicators


def fixture():
    t = datetime(2026, 1, 5, 13, 0, tzinfo=timezone.utc)
    bars=[]
    for i in range(60):
        close=100+i*0.25
        bars.append(dict(timestamp=t+timedelta(minutes=5*i), contract='MESH26', expiry_date=datetime(2026,3,20).date(),
                         open=close-0.25, high=close+0.5, low=close-0.5, close=close,
                         volume=100, forecast_close=None))
    bars[49]['forecast_close']=130
    return bars

class SimulationTests(unittest.TestCase):
    def test_no_forecast_no_trade(self):
        b=fixture(); b[49]['forecast_close']=None
        self.assertEqual(simulate(b,'fixed')['trade_count'],0)

    def test_costs_and_target(self):
        b=fixture(); b[50].update(high=120, low=112, open=112.25, close=115)
        r=simulate(b,'fixed'); t=r['trades'][0]
        self.assertEqual(t['reason'],'target')
        self.assertAlmostEqual(t['pnl'],t['contracts']*((t['exit']-t['entry'])*5-2.5))
        self.assertLessEqual(t['contracts']*(1.5*5+5),50)

    def test_ambiguous_bar_stop_first(self):
        b=fixture(); b[50].update(high=120, low=100)
        self.assertEqual(simulate(b,'fixed')['trades'][0]['reason'],'stop')

    def test_gap_fill(self):
        b=fixture(); b[50].update(open=100, low=99, close=100, high=101)
        # Entry is at this open; gap later must be below stop.
        b[51].update(open=90, low=89, close=90, high=91)
        t=simulate(b,'fixed')['trades'][0]
        self.assertEqual(t['reason'],'gap_stop')
        self.assertEqual(t['exit'],89.75)

    def test_trailing_effective_next_bar(self):
        b=fixture(); b[50].update(high=116, low=112, close=115)
        b[51].update(open=115, high=115.5, low=113, close=114)
        t=simulate(b,'trailing')['trades'][0]
        self.assertEqual(t['reason'],'stop')
        self.assertEqual(t['exit'],114.25)

    def test_insufficient_capital(self):
        self.assertEqual(simulate(fixture(),'fixed',Config(initial_balance=100))['trade_count'],0)

    def test_contract_and_gap_boundaries(self):
        b=fixture(); b[51]['contract']='MESM26'
        self.assertEqual(simulate(b,'fixed')['trade_count'],0)
        b=fixture(); b[51]['timestamp']+=timedelta(minutes=1)
        self.assertEqual(simulate(b,'fixed')['trade_count'],0)

    def test_expiry_day_blocked(self):
        b=fixture()
        for bar in b: bar['expiry_date']=datetime(2026,1,5).date()
        self.assertEqual(simulate(b,'fixed')['trade_count'],0)

    def test_indicators_ignore_future(self):
        b=fixture(); before=indicators(b,49)
        b[50]['close']=1000000
        self.assertEqual(before,indicators(b,49))

if __name__ == '__main__': unittest.main()
