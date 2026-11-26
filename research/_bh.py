import pandas as pd, numpy as np, glob
end = pd.Timestamp('2026-11-26', tz='UTC')
start = end - pd.Timedelta(days=365)
print('window', start.date(), '->', end.date())
for p in sorted(glob.glob('data/crypto/*_1d.csv')):
    d = pd.read_csv(p)
    d['t'] = pd.to_datetime(d.timestamp, unit='s', utc=True)
    w = d[d.t >= start].reset_index(drop=True)
    c = w.close.values
    ret = c[-1]/c[0]-1
    peak = np.maximum.accumulate(c)
    dd = (c/peak-1).min()
    r = np.diff(np.log(c))
    sharpe = r.mean()/r.std()*np.sqrt(365) if r.std() else 0
    print(f'{p.split(chr(92))[-1][:7]:8s} n={len(w):4d}  B&H {ret*100:+7.1f}%  maxDD {dd*100:6.1f}%  sharpe {sharpe:5.2f}  last {c[-1]:.2f}')