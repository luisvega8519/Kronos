"""Create chronological forecasts with the existing Kronos API; no future prices."""
import argparse
from datetime import timedelta


def main():
    import pandas as pd
    import torch
    from model import Kronos, KronosTokenizer, KronosPredictor
    from futures_lab.backtest import load_bars
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('csv')
    p.add_argument('--output', default='mes-forecasts.csv')
    p.add_argument('--device', default='cpu')
    p.add_argument('--lookback', type=int, default=400)
    p.add_argument('--samples', type=int, default=5)
    args = p.parse_args()
    if not 50 <= args.lookback <= 512 or args.samples < 1:
        p.error('lookback must be 50..512 and samples positive')
    bars = load_bars(args.csv)
    df = pd.read_csv(args.csv)
    df['forecast_close'] = float('nan')
    tokenizer = KronosTokenizer.from_pretrained('NeoQuasar/Kronos-Tokenizer-base', revision='0e0117387f39004a9016484a186a908917e22426')
    model = Kronos.from_pretrained('NeoQuasar/Kronos-small', revision='901c26c1332695a2a8f243eb2f37243a37bea320')
    model.eval()
    tokenizer.eval()
    predictor = KronosPredictor(model, tokenizer, device=args.device, max_context=512)
    for i in range(args.lookback-1, len(bars)):
        window = bars[i-args.lookback+1:i+1]
        if any(b['contract'] != window[-1]['contract'] for b in window):
            continue
        if any(window[j]['timestamp']-window[j-1]['timestamp'] != timedelta(minutes=5) for j in range(1, len(window))):
            continue
        # UTC-naive series expected by the upstream timestamp feature builder.
        x_time = pd.Series(pd.to_datetime([b['timestamp'] for b in window], utc=True)).dt.tz_localize(None)
        y_time = pd.Series([x_time.iloc[-1]+pd.Timedelta(minutes=5*j) for j in (1, 2, 3)])
        x = pd.DataFrame([{k:b[k] for k in ('open','high','low','close','volume')} for b in window])
        # Preserve vendor amount when available; otherwise upstream uses its OHLC-volume proxy.
        if 'amount' in df.columns:
            x['amount'] = df['amount'].iloc[i-args.lookback+1:i+1].to_numpy()
        torch.manual_seed(42+i)
        with torch.no_grad():
            result = predictor.predict(df=x, x_timestamp=x_time, y_timestamp=y_time, pred_len=3,
                                   sample_count=args.samples, verbose=False)
        df.loc[i, 'forecast_close'] = float(result['close'].iloc[-1])
    df.to_csv(args.output, index=False)
    print('Saved causal forecasts:', args.output)

if __name__ == '__main__':
    main()
