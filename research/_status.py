import pandas as pd, glob
for p in sorted(glob.glob('data/crypto/*_1d.csv')):
    d = pd.read_csv(p)
    t = pd.to_datetime(d.timestamp, unit='s', utc=True)
    print(f'{p:38s} {len(d):6d}  {t.iloc[0].date()} -> {t.iloc[-1].date()}')
print()
for p in sorted(glob.glob('data/crypto/*_4h.csv')):
    d = pd.read_csv(p)
    t = pd.to_datetime(d.timestamp, unit='s', utc=True)
    print(f'{p:38s} {len(d):6d}  {t.iloc[0].date()} -> {t.iloc[-1].date()}')