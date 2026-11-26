import pandas as pd
for c in ['BTCUSDT','ETHUSDT','BNBUSDT','XRPUSDT','SOLUSDT']:
    p = f'data/crypto/{c}_1h.csv'
    d = pd.read_csv(p)
    d['timestamp'] = d.timestamp // 1000
    d.to_csv(p, index=False)
    print(c, len(d), pd.to_datetime(d.timestamp.iloc[0], unit='s', utc=True), pd.to_datetime(d.timestamp.iloc[-1], unit='s', utc=True))