from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import math
from ..constants import ENABLED


class AirdropLedgerService:
    def __init__(self, repository, member_provider=None):
        self._repository = repository
        self._members = member_provider or repository.members

    def view(self):
        data = self._repository.read()
        data['mode'] = 'local'
        data['members'] = self.active_members()
        self._refresh_accounts(data['state'], data['members'])
        return data

    def active_members(self):
        return [{'id': m['id'], 'name': m['name']} for m in self._members() if not m.get('deleted') and m.get('status') == ENABLED]

    @staticmethod
    def _refresh_accounts(state, members):
        active_members = {m['id']: m for m in members}
        for event in state['events']:
            existing = {a['id']: a for a in event['accounts']}
            sold = {s['account'] for s in event['sales']}
            accounts = []
            for account in event['accounts']:
                member = active_members.get(account['id'])
                if member:
                    account.update(member)
                    account.pop('inactive', None)
                    accounts.append(account)
                elif account['claimed'] or account['id'] in sold:
                    accounts.append({**account, 'inactive': True})
            accounts.extend({**m, 'claimed': False, 'amount': event['amount']} for m in members if m['id'] not in existing)
            event['accounts'] = accounts

    def save(self, payload):
        if not isinstance(payload, dict) or type(payload.get('revision')) is not int:
            raise ValueError('保存内容不完整，请重新打开页面。')
        state = deepcopy(payload.get('state'))
        if not isinstance(state, dict) or not isinstance(state.get('events'), list) or len(state['events']) > 2000:
            raise ValueError('空投记录格式不正确。')
        previous = self._repository.read()['state']
        known = {m['id']: m['name'] for m in self._members()}
        known.update({a['id']: a['name'] for e in previous['events'] for a in e['accounts'] if a['id'] not in known})
        event_ids, sale_ids = set(), set()
        for e in state['events']:
            self._identifier(e['id'], event_ids)
            if not isinstance(e['token'], str) or not e['token'].strip() or len(e['token']) > 40:
                raise ValueError('请填写币种名称。')
            date.fromisoformat(e['date'])
            self._number(e['amount'], positive=True)
            if e['price'] is not None:
                self._number(e['price'])
            if e.get('quoteMode') not in ('auto', 'manual'):
                raise ValueError('参考价格设置不正确。')
            for key in ('priceAt', 'historyId', 'quoteSource', 'marketTime'):
                if e.get(key) is not None and (not isinstance(e[key], str) or len(e[key]) > 200):
                    raise ValueError('行情资料格式不正确。')
            account_ids, accounts = set(), {}
            if not isinstance(e['accounts'], list) or len(e['accounts']) > 5000 or not isinstance(e['sales'], list):
                raise ValueError('账户记录格式不正确。')
            for a in e['accounts']:
                self._identifier(a['id'], account_ids)
                if a['id'] not in known or type(a['claimed']) is not bool:
                    raise ValueError('账户不存在，请重新打开页面。')
                a['name'] = known[a['id']]
                self._number(a['amount'], positive=True)
                accounts[a['id']] = a
            sold_accounts = set()
            for s in e['sales']:
                self._identifier(s['id'], sale_ids)
                a = accounts.get(s['account'])
                if not a or not a['claimed'] or a['id'] in sold_accounts:
                    raise ValueError('请先领取；同一账户的一份空投只能卖出一次。')
                sold_accounts.add(a['id'])
                self._number(s['quantity'], positive=True)
                self._number(s['price'], positive=True)
                self._number(s['fee'])
                if Decimal(str(s['quantity'])) != Decimal(str(a['amount'])):
                    raise ValueError('每次须卖出所选账户的一整份。')
                if Decimal(str(s['fee'])) > Decimal(str(s['quantity'])) * Decimal(str(s['price'])):
                    raise ValueError('手续费不能超过卖出金额。')
                datetime.fromisoformat(s['time'])
        if state.get('selected') not in event_ids and (event_ids or state.get('selected')):
            raise ValueError('没有找到当前选择的空投。')
        if not {e['id'] for e in previous['events']}.issubset(event_ids):
            raise ValueError('已有空投不能被覆盖或清空，请重新打开页面。')
        self._refresh_accounts(state, self.active_members())
        return self._repository.save(state, payload['revision'])

    def delete(self, payload):
        if not isinstance(payload, dict) or type(payload.get('revision')) is not int:
            raise ValueError('删除内容不完整，请重新打开页面。')
        event_id = payload.get('event_id')
        self._identifier(event_id, set())
        return self._repository.delete_event(event_id, payload['revision'])

    @staticmethod
    def _identifier(value, seen):
        if not isinstance(value, str) or not value or len(value) > 100 or value in seen:
            raise ValueError('记录重复或编号不正确。')
        seen.add(value)

    @staticmethod
    def _number(value, positive=False):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or value > 1e15 or (positive and value == 0):
            raise ValueError('请填写有效的数量和金额。')
