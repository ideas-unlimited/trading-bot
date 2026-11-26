import json, urllib.request
url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=12&page=1"
req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
try:
    data = json.loads(urllib.request.urlopen(req, timeout=25).read())
    for c in data:
        rank = c['market_cap_rank']; sym = c['symbol'].upper()
        print(f"{rank:>2} {sym:8s} {c['current_price']:>12.4f}  mcap {c['market_cap']/1e9:7.1f}B  24h vol {c['total_volume']/1e9:5.2f}B")
except Exception as e:
    print("FAIL", repr(e))
