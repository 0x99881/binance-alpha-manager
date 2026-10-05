"""Focused checks for public source matching and quote failures; no real ledger."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from binance_alpha.services.airdrop_market_service import AirdropMarketService
from binance_alpha.web_airdrop_routes import register_airdrop_routes


class PublicSource:
    def __init__(self):
        self.items = [{'token': 'CT', 'date': '2026-09-30', 'time': '16:00', 'amount': '200',
                       'chain_id': '56', 'contract_address': '0xAbC', 'phase': 1},
                      {'token': 'CT', 'date': '2026-10-01', 'amount': '150',
                       'chain_id': '56', 'contract_address': '0xabc'}]
        self.price = '0.499'
        self.market_tokens = [{'symbol': 'CT', 'chainId': '56', 'contractAddress': '0xwrong', 'alphaId': 'ALPHA_9'},
                              {'symbol': 'CT', 'chainId': '56', 'contractAddress': '0xabc', 'alphaId': 'ALPHA_1228'}]

    def history(self):
        return {'airdrops': self.items}, '2026-10-04T10:00:00+00:00'

    def tokens(self):
        return {'data': self.market_tokens}, ''

    def ticker(self, alpha_id):
        assert alpha_id == 'ALPHA_1228', 'same-name token must not supply the quote'
        return {'data': {'symbol': alpha_id + 'USDT', 'lastPrice': self.price, 'closeTime': 1791108093986}}, '2026-10-04T10:00:00+00:00'


class MarketChecks(unittest.TestCase):
    def setUp(self):
        self.source = PublicSource()
        self.service = AirdropMarketService(self.source)

    def test_history_keeps_separate_events_and_normalizes_quantity(self):
        items = self.service.catalog()['items']
        self.assertEqual([x['amount'] for x in items], [150, 200])
        self.assertNotEqual(items[0]['id'], items[1]['id'])

    def test_price_matches_chain_and_address_not_symbol(self):
        quote = self.service.quote(self.service.catalog()['items'][0]['id'])
        self.assertEqual(quote['price'], 0.499)
        self.assertEqual(quote['currency'], 'USDT')
        self.source.market_tokens[1]['chainId'] = '1'
        with self.assertRaises(LookupError):
            self.service.quote(self.service.catalog()['items'][0]['id'])

    def test_unknown_and_ambiguous_assets_fail_without_guessing(self):
        with self.assertRaises(LookupError):
            self.service.quote('0' * 24)
        self.source.market_tokens.append(dict(self.source.market_tokens[1]))
        with self.assertRaises(LookupError):
            self.service.quote(self.service.catalog()['items'][0]['id'])

    def test_invalid_prices_never_become_zero_estimates(self):
        event_id = self.service.catalog()['items'][0]['id']
        for value in ('NaN', 'Infinity', '-1', '0', ''):
            self.source.price = value
            with self.assertRaises(ValueError):
                self.service.quote(event_id)

    def test_routes_and_outage(self):
        app = Flask(__name__)
        with patch('binance_alpha.web_airdrop_routes.AirdropMarketService', return_value=self.service):
            register_airdrop_routes(app)
        app.logger.disabled = True
        client = app.test_client()
        self.assertEqual(client.get('/api/airdrops/catalog').status_code, 200)
        event_id = self.service.catalog()['items'][0]['id']
        self.assertEqual(client.get('/api/airdrops/quote?id=' + event_id).json['price'], 0.499)
        self.assertEqual(client.get('/api/airdrops/quote?id=bad').status_code, 400)
        with patch.object(self.source, 'ticker', side_effect=TimeoutError):
            self.assertEqual(client.get('/api/airdrops/quote?id=' + event_id).status_code, 503)


if __name__ == '__main__':
    unittest.main()
