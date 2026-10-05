"""Local airdrop records, saved atomically with optimistic concurrency."""
from contextlib import closing


class SQLiteAirdropRepository:
    def __init__(self, connect):
        self._connect = connect
        with closing(connect()) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS airdrop_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    revision INTEGER NOT NULL DEFAULT 0, selected_id TEXT NOT NULL DEFAULT ''
                );
                INSERT OR IGNORE INTO airdrop_state(singleton) VALUES(1);
                CREATE TABLE IF NOT EXISTS airdrop_events (
                    id TEXT PRIMARY KEY, token TEXT NOT NULL, event_date TEXT NOT NULL,
                    amount REAL NOT NULL, price REAL, price_at TEXT, history_id TEXT,
                    quote_mode TEXT NOT NULL, quote_source TEXT, market_time TEXT, position INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS airdrop_claims (
                    event_id TEXT NOT NULL, account_id TEXT NOT NULL, account_name TEXT NOT NULL,
                    claimed INTEGER NOT NULL, amount REAL NOT NULL, position INTEGER NOT NULL,
                    PRIMARY KEY(event_id, account_id)
                );
                CREATE TABLE IF NOT EXISTS airdrop_sales (
                    id TEXT PRIMARY KEY, event_id TEXT NOT NULL, account_id TEXT NOT NULL,
                    quantity REAL NOT NULL, price REAL NOT NULL, fee REAL NOT NULL, sold_at TEXT NOT NULL
                );
            ''')
            db.commit()

    def members(self):
        with closing(self._connect()) as db:
            return [dict(row) for row in db.execute('SELECT id,name,status,deleted FROM members WHERE deleted=0 ORDER BY sort_order,name')]

    def read(self):
        with closing(self._connect()) as db:
            db.execute('BEGIN')
            meta = db.execute('SELECT * FROM airdrop_state WHERE singleton=1').fetchone()
            events = []
            for row in db.execute('SELECT * FROM airdrop_events ORDER BY position'):
                event = dict(id=row['id'], token=row['token'], date=row['event_date'], amount=row['amount'],
                             price=row['price'], priceAt=row['price_at'], historyId=row['history_id'],
                             quoteMode=row['quote_mode'], quoteSource=row['quote_source'], marketTime=row['market_time'])
                event['accounts'] = [dict(id=a['account_id'], name=a['account_name'], claimed=bool(a['claimed']), amount=a['amount'])
                                     for a in db.execute('SELECT * FROM airdrop_claims WHERE event_id=? ORDER BY position', (row['id'],))]
                event['sales'] = [dict(id=s['id'], account=s['account_id'], quantity=s['quantity'], price=s['price'], fee=s['fee'], time=s['sold_at'])
                                  for s in db.execute('SELECT * FROM airdrop_sales WHERE event_id=? ORDER BY rowid', (row['id'],))]
                events.append(event)
            return {'revision': meta['revision'], 'state': {'selected': meta['selected_id'], 'events': events}}

    def delete_event(self, event_id, revision):
        with closing(self._connect()) as db:
            with db:
                db.execute('BEGIN IMMEDIATE')
                meta = db.execute('SELECT revision,selected_id FROM airdrop_state WHERE singleton=1').fetchone()
                if meta['revision'] != revision:
                    raise RuntimeError('其他页面已经更新了记录，请重新打开页面后再删除。')
                event_ids = [row['id'] for row in db.execute('SELECT id FROM airdrop_events ORDER BY position')]
                if event_id not in event_ids:
                    raise ValueError('这次空投已不存在，请重新打开页面。')
                index = event_ids.index(event_id)
                event_ids.remove(event_id)
                selected = meta['selected_id']
                if selected == event_id:
                    selected = event_ids[min(index, len(event_ids) - 1)] if event_ids else ''
                db.execute('DELETE FROM airdrop_sales WHERE event_id=?', (event_id,))
                db.execute('DELETE FROM airdrop_claims WHERE event_id=?', (event_id,))
                db.execute('DELETE FROM airdrop_events WHERE id=?', (event_id,))
                db.execute('UPDATE airdrop_state SET revision=?,selected_id=? WHERE singleton=1', (revision + 1, selected))
                return {'revision': revision + 1, 'selected': selected}

    def save(self, state, revision):
        with closing(self._connect()) as db:
            with db:
                db.execute('BEGIN IMMEDIATE')
                actual = db.execute('SELECT revision FROM airdrop_state WHERE singleton=1').fetchone()[0]
                if actual != revision:
                    raise RuntimeError('其他页面已经保存了新记录，请刷新后再操作。当前填写已保留为草稿。')
                db.execute('DELETE FROM airdrop_sales')
                db.execute('DELETE FROM airdrop_claims')
                db.execute('DELETE FROM airdrop_events')
                for index, e in enumerate(state['events']):
                    db.execute('INSERT INTO airdrop_events VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                               (e['id'], e['token'], e['date'], e['amount'], e['price'], e.get('priceAt'), e.get('historyId'),
                                e['quoteMode'], e.get('quoteSource'), e.get('marketTime'), index))
                    db.executemany('INSERT INTO airdrop_claims VALUES(?,?,?,?,?,?)',
                                   [(e['id'], a['id'], a['name'], int(a['claimed']), a['amount'], i) for i, a in enumerate(e['accounts'])])
                    db.executemany('INSERT INTO airdrop_sales VALUES(?,?,?,?,?,?,?)',
                                   [(s['id'], e['id'], s['account'], s['quantity'], s['price'], s['fee'], s['time']) for s in e['sales']])
                db.execute('UPDATE airdrop_state SET revision=?, selected_id=? WHERE singleton=1', (actual + 1, state['selected']))
                return actual + 1
