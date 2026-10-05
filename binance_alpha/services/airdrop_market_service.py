from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import math

from ..repositories.airdrop_market_repository import AirdropMarketRepository


def _positive(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (ValueError, TypeError):
        return None


def _address(value):
    text = str(value or '').strip()
    return text.lower() if text.lower().startswith('0x') else text


class AirdropMarketService:
    def __init__(self, repository=None):
        self._repository = repository or AirdropMarketRepository()

    def catalog(self):
        data, fetched_at = self._repository.history()
        rows = {}
        for item in data['airdrops']:
            if not isinstance(item, dict):
                continue
            token = str(item.get('token') or '').strip()
            event_date = str(item.get('date') or '')
            try:
                date.fromisoformat(event_date)
            except ValueError:
                continue
            if not token:
                continue
            identity = '|'.join(str(item.get(k) or '') for k in ('token', 'date', 'time', 'chain_id', 'contract_address', 'type', 'phase'))
            event_id = sha256(identity.encode()).hexdigest()[:24]
            rows[event_id] = {
                'id': event_id, 'token': token, 'name': str(item.get('name') or token),
                'date': event_date, 'time': str(item.get('time') or ''),
                'amount': _positive(item.get('amount')), 'points': str(item.get('points') or ''),
                'chain_id': str(item.get('chain_id') or ''),
                'contract_address': str(item.get('contract_address') or ''),
                'phase': str(item.get('phase') or ''), 'type': str(item.get('type') or ''),
            }
        return {'items': sorted(rows.values(), key=lambda row: (row['date'], row['time']), reverse=True),
                'source': 'Alpha123', 'source_url': 'https://alpha123.uk/zh/history.html', 'fetched_at': fetched_at}

    def quote(self, event_id):
        event = next((item for item in self.catalog()['items'] if item['id'] == event_id), None)
        if event is None:
            raise LookupError('没有找到这次空投，请重新从历史记录选择。')
        if not event['chain_id'] or not event['contract_address']:
            raise LookupError('这条历史记录缺少币种识别资料，暂时需要手动填价。')
        data, _ = self._repository.tokens()
        tokens = data.get('data')
        if not isinstance(tokens, list):
            raise ValueError('Invalid token list')
        matches = [token for token in tokens if isinstance(token, dict)
                   and str(token.get('chainId')) == event['chain_id']
                   and _address(token.get('contractAddress')) == _address(event['contract_address'])]
        if len(matches) != 1:
            raise LookupError('币安暂时没有这个币的明确报价，可先手动填价。')
        alpha_id = str(matches[0].get('alphaId') or '')
        ticker, fetched_at = self._repository.ticker(alpha_id)
        detail = ticker.get('data')
        if not isinstance(detail, dict) or detail.get('symbol') != alpha_id + 'USDT':
            raise ValueError('Mismatched quote')
        price = _positive(detail.get('lastPrice'))
        if price is None:
            raise ValueError('Invalid quote price')
        market_time = datetime.fromtimestamp(float(detail['closeTime']) / 1000, timezone.utc).isoformat()
        return {'event_id': event_id, 'price': price, 'currency': 'USDT', 'source': '币安 Alpha',
                'fetched_at': fetched_at, 'market_time': market_time,
                'source_url': 'https://www.binance.com/en/alpha/bsc/' + event['contract_address']
                if event['chain_id'] == '56' else 'https://www.binance.com/en/alpha'}
