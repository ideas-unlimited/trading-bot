import pandas as pd
for c in ['BTCUSDT','SOLUSDT']:
    d = pd.read_csv(f'data/crypto/{c}_1h.csv')
    t = pd.to_datetime(d.timestamp, unit='s', utc=True)
    print(c, len(d), t.min(), t.max())