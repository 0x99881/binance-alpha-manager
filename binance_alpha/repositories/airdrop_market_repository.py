"""Read public market sources. No account data or credentials are transmitted."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from threading import RLock
from time import monotonic
from urllib.request import Request, urlopen


class AirdropMarketRepository:
    HISTORY_URL = 'https://alpha123.uk/api/historydata'
    TOKENS_URL = 'https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list'
    TICKER_URL = 'https://www.binance.com/bapi/defi/v1/public/alpha-trade/ticker?symbol='

    def __init__(self):
        self._cache = {}
        self._lock = RLock()

    def _read(self, url, ttl):
        with self._lock:
            cached = self._cache.get(url)
            if cached and monotonic() - cached[0] < ttl:
                return cached[1], cached[2]
            request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'AlphaLedger/1.0'})
            with urlopen(request, timeout=12) as response:
                raw = response.read(5_000_001)
            if len(raw) > 5_000_000:
                raise ValueError('Source response too large')
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError('Source response is not an object')
            if url == self.HISTORY_URL:
                if not isinstance(data.get('airdrops'), list):
                    raise ValueError('History is missing')
            elif data.get('success') is not True:
                raise ValueError('Market source reported failure')
            fetched_at = datetime.now(timezone.utc).isoformat()
            self._cache[url] = (monotonic(), data, fetched_at)
            return data, fetched_at

    def history(self):
        return self._read(self.HISTORY_URL, 300)

    def tokens(self):
        return self._read(self.TOKENS_URL, 300)

    def ticker(self, alpha_id):
        if not re.fullmatch(r'ALPHA_\d+', alpha_id):
            raise ValueError('Invalid market identifier')
        return self._read(self.TICKER_URL + alpha_id + 'USDT', 30)
