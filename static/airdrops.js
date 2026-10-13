(() => {
    'use strict';
    const $ = (id) => document.getElementById(id);
    if (!$('airdrop-app')) return;
    const bootstrap = JSON.parse($('airdrop-bootstrap')?.textContent || '{"mode":"demo"}');
    const localLedger = bootstrap.mode === 'local';
    const storageKey = 'alpha-airdrop-preview-v1';
    const draftKey = 'alpha-airdrop-unsaved-draft-v1';
    let ledgerRevision = bootstrap.revision || 0, pendingSave = null, saving = false, saveBlocked = false, deletingEvent = false;
    const fmt = (n, digits = 2) => Number(n).toLocaleString('en-US', {minimumFractionDigits: digits, maximumFractionDigits: digits});
    const qty = (n) => Number(n).toLocaleString('en-US', {maximumFractionDigits: 8});
    const esc = (value) => String(value).replace(/[&<>"']/g, (c) => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
    const now = () => new Date(Date.now() + 8 * 3600000).toISOString().slice(0,16);
    const id = () => crypto.randomUUID();
    const finite = (n) => typeof n === 'number' && Number.isFinite(n) && n >= 0;
    const accounts = () => localLedger ? bootstrap.members.map(a => ({...a, claimed:false, amount:200})) : Array.from({length:6}, (_, i) => ({id:`account-${i + 1}`, name:`账户 ${String(i + 1).padStart(2, '0')}`, claimed:false, amount:200}));
    function seed() {
        const members = accounts();
        members.forEach((a, i) => { a.claimed = i < 4; });
        return {selected:'ct-demo', events:[{id:'ct-demo', token:'CT', date:'2026-09-30', amount:200, price:null, priceAt:null, quoteMode:'auto', accounts:members, sales:[
            {id:'sale-1', account:'account-1', quantity:200, price:0.10, fee:0, time:'2026-09-30T16:12'},
            {id:'sale-2', account:'account-2', quantity:200, price:0.12, fee:0, time:'2026-09-30T16:18'},
            {id:'sale-3', account:'account-3', quantity:200, price:0.115, fee:0, time:'2026-09-30T16:25'}
        ]}]};
    }
    // Production loads SQLite state; demo storage is never imported into it.
    function validState(s) {
        return s && Array.isArray(s.events) && ((!s.events.length && !s.selected) || s.events.some(e => e.id === s.selected)) && s.events.every(e =>
            typeof e.id === 'string' && typeof e.token === 'string' && typeof e.date === 'string' && finite(e.amount) &&
            (e.price === null || finite(e.price)) && Array.isArray(e.accounts) && Array.isArray(e.sales) &&
            e.accounts.every(a => typeof a.id === 'string' && typeof a.name === 'string' && typeof a.claimed === 'boolean' && finite(a.amount)) &&
            e.sales.every(t => typeof t.id === 'string' && typeof t.time === 'string' && finite(t.quantity) && finite(t.price) && finite(t.fee) && e.accounts.some(a => a.id === t.account)));
    }
    let state = localLedger ? bootstrap.state : seed();
    let storageWarning = '';
    try {
        const saved = localStorage.getItem(localLedger ? draftKey : storageKey);
        if (saved) {
            const parsed = JSON.parse(saved);
            if (localLedger) {
                if (validState(parsed.state) && parsed.revision === ledgerRevision) { state = parsed.state; pendingSave = {state:structuredClone(state), message:'上次未保存的填写已保存到本机'}; }
                else { saveBlocked = true; storageWarning = '有一份未保存草稿，但本机记录已更新。请先导出草稿，再放弃草稿以重新读取。'; }
            } else if (validState(parsed)) state = parsed;
            else storageWarning = '旧体验记录无法读取，已显示示例。';
        }
    }
    catch (_) { storageWarning = '浏览器存储不可用，本次操作可能无法保留。'; }
    state.events.forEach(e => { if (!e.quoteMode) e.quoteMode = e.priceAt ? 'manual' : 'auto'; if (e.id === 'ct-demo' && !e.priceAt) e.price = null; });
    let catalog = [], catalogPromise = null, selectedHistory = null;
    const quoteRequests = new Set();
    const displayTime = value => new Date(value).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', hour12:false});
    const current = () => state.events.find(e => e.id === state.selected);
    const saleInputDrafts = new Map();
    const saleInputKey = input => JSON.stringify([current().id, input.dataset.inlineSaleEdit || input.dataset.inlineSalePrice, input.hasAttribute('data-inline-sale-edit') ? 'edit' : 'sale']);
    // Refresh membership without replacing the ledger or interrupting a form edit.
    let refreshingMembers = false, draggingAccount = null;
    function reconcileMembers() {
        const activeIds = new Set(bootstrap.members.map(m => m.id));
        for (const event of state.events) {
            const existing = new Map(event.accounts.map(a => [a.id, a]));
            const sold = new Set(event.sales.map(s => s.account));
            const members = new Map(bootstrap.members.map(m => [m.id, m]));
            const retained = event.accounts.filter(a => activeIds.has(a.id) || a.claimed || sold.has(a.id)).map(a => {
                const account = {...a, ...(members.get(a.id) || {})};
                if (activeIds.has(a.id)) delete account.inactive;
                else account.inactive = true;
                return account;
            });
            const added = bootstrap.members.filter(m => !existing.has(m.id)).map(m => ({...m,claimed:false,amount:event.amount}));
            event.accounts = [...retained, ...added];
        }
    }
    function membersEditBusy() {
        return draggingAccount || saving || pendingSave || saveBlocked || deletingEvent || $('event-dialog').open || $('confirm-dialog').open || document.activeElement?.matches('#account-rows input, #reference-price');
    }
    async function refreshMembers() {
        if (!localLedger || refreshingMembers || membersEditBusy()) return;
        refreshingMembers = true;
        try {
            const data = await window.BinanceAlpha.requestJson('/api/airdrops/members', {cache:'no-store', signal:AbortSignal.timeout(10000)});
            if (!Array.isArray(data.members) || !data.members.every(m => typeof m.id === 'string' && typeof m.name === 'string')) throw new Error('Invalid members');
            if (membersEditBusy()) return;
            const before = JSON.stringify(state.events.map(e => e.accounts));
            bootstrap.members = data.members;
            reconcileMembers();
            if (before !== JSON.stringify(state.events.map(e => e.accounts))) render();
            $('member-refresh-status').hidden = true;
        } catch (_) {
            $('member-refresh-status').textContent = '成员名单暂未更新，正在重试';
            $('member-refresh-status').hidden = false;
        } finally { refreshingMembers = false; }
    }
    function totals(a) {
        const trades = current().sales.filter(s => s.account === a.id);
        const sold = trades.reduce((n,s) => n + s.quantity, 0);
        const gross = trades.reduce((n,s) => n + s.quantity * s.price, 0);
        return {sold, gross, cash:gross - trades.reduce((n,s) => n + s.fee, 0), remaining:Math.max(0,(a.claimed ? a.amount : 0) - sold)};
    }
    const sortModes = new Map();
    function sortMode() {
        const eventId = current().id;
        if (!sortModes.has(eventId)) {
            let saved;
            try { saved = localStorage.getItem(`alpha-airdrop-sort-${eventId}`); } catch (_) {}
            sortModes.set(eventId, ['manual','price-asc','price-desc'].includes(saved) ? saved : 'price-desc');
        }
        return sortModes.get(eventId);
    }
    function orderedAccounts() {
        const event = current(), rows = [...event.accounts], mode = sortMode();
        if (mode !== 'manual') rows.sort((a,b) => {
            const left = totals(a), right = totals(b);
            if (Boolean(left.sold) !== Boolean(right.sold)) return Number(right.sold > 0) - Number(left.sold > 0);
            if (!left.sold) {
                if (a.claimed !== b.claimed) return Number(b.claimed) - Number(a.claimed);
                if (event.price === null) return 0;
                // Stable sort keeps the manual order when current values match.
                return right.remaining * event.price - left.remaining * event.price;
            }
            return mode === 'price-desc' ? right.gross - left.gross : left.gross - right.gross;
        });
        return rows;
    }
    function accountValue(a, t = totals(a)) {
        if (t.sold) return fmt(t.gross);
        if (!a.claimed) return '—';
        return `${current().price === null ? '待获取价格' : fmt(t.remaining * current().price)}<small class="ad-unit-price">当前估值</small>`;
    }
    function refreshAccountValues() {
        const rows = new Map(Array.from($('account-rows').children, row => [row.dataset.accountRow, row]));
        for (const a of current().accounts) {
            const cell = rows.get(a.id)?.querySelector('[data-account-value]');
            if (cell) cell.innerHTML = accountValue(a);
        }
        if (sortMode() === 'manual' || draggingAccount || document.activeElement?.closest('#account-rows')) return;
        const ordered = orderedAccounts().map(a => rows.get(a.id)).filter(Boolean);
        if (ordered.some((row, index) => $('account-rows').children[index] !== row)) {
            ordered.forEach(row => $('account-rows').appendChild(row));
        }
    }
    function useVisibleOrderForManual() {
        if (sortMode() === 'manual') return;
        const event = current(), byId = new Map(event.accounts.map(a => [a.id, a]));
        const visible = Array.from($('account-rows').children).map(row => byId.get(row.dataset.accountRow)).filter(Boolean);
        if (visible.length === event.accounts.length) event.accounts = visible;
        sortModes.set(event.id, 'manual');
        try { localStorage.setItem(`alpha-airdrop-sort-${event.id}`, 'manual'); } catch (_) {}
    }
    function moveAccount(accountId, target) {
        useVisibleOrderForManual();
        const rows = current().accounts, index = rows.findIndex(a => a.id === accountId);
        const targetIndex = typeof target === 'number' ? target : target?.offset !== undefined ? index + target.offset : rows.findIndex(a => a.id === target);
        if (index < 0 || targetIndex < 0 || targetIndex >= rows.length) return;
        if (index !== targetIndex) rows.splice(targetIndex, 0, rows.splice(index, 1)[0]);
        render(); persist('手动顺序已保存');
    }
    $('account-sort-options').addEventListener('click', ev => {
        const mode = ev.target.closest('[data-sort-mode]')?.dataset.sortMode;
        if (!['price-asc','price-desc'].includes(mode)) return;
        sortModes.set(current().id, mode);
        try { localStorage.setItem(`alpha-airdrop-sort-${current().id}`, mode); } catch (_) {}
        render();
    });
    $('account-rows').addEventListener('dragstart', ev => {
        const handle = ev.target.closest('[data-drag]');
        if (!handle) { ev.preventDefault(); return; }
        useVisibleOrderForManual();
        draggingAccount = handle.dataset.drag;
        ev.dataTransfer.effectAllowed = 'move';
        ev.dataTransfer.setData('text/plain', draggingAccount);
    });
    function clearDropTarget() { document.querySelectorAll('.ad-drop-target').forEach(row => row.classList.remove('ad-drop-target')); }
    $('account-rows').addEventListener('dragover', ev => {
        const row = ev.target.closest('[data-account-row]');
        if (!draggingAccount || !row) return;
        ev.preventDefault(); ev.dataTransfer.dropEffect = 'move';
        clearDropTarget(); row.classList.add('ad-drop-target');
    });
    $('account-rows').addEventListener('drop', ev => {
        const row = ev.target.closest('[data-account-row]'), source = draggingAccount;
        if (!source || !row) return;
        ev.preventDefault(); draggingAccount = null; clearDropTarget();
        moveAccount(source, row.dataset.accountRow);
    });
    $('account-rows').addEventListener('dragend', () => { draggingAccount = null; clearDropTarget(); });
    let toastTimer;
    function toast(message) { $('ad-toast').textContent = message; $('ad-toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('ad-toast').hidden = true; }, 4000); }
    function persist(message) {
        if (localLedger) {
            if (saveBlocked) { showSaveError('记录已在其他页面更新，请先处理未保存草稿。'); return; }
            pendingSave = {state:structuredClone(state), message};
            keepDraft();
            flushLedger();
            return;
        }
        try { localStorage.setItem(storageKey, JSON.stringify(state)); $('save-status').textContent = '已保存到当前浏览器 · ' + now().slice(11); }
        catch (_) { $('save-status').textContent = '未能保存，请勿关闭页面'; toast('浏览器无法保存体验记录，请勿关闭页面。'); return; }
        if (message) toast(message);
    }
    function keepDraft() {
        try { localStorage.setItem(draftKey, JSON.stringify({revision:ledgerRevision, state:pendingSave?.state || state})); }
        catch (_) { /* SQLite remains the durable store even if browser storage is unavailable. */ }
    }
    function showSaveError(message) {
        $('ledger-save-warning').hidden = false;
        $('ledger-save-error').textContent = message;
        $('retry-ledger-save').disabled = saveBlocked;
        $('save-status').textContent = '尚未保存到本机';
    }
    async function flushLedger() {
        if (saving || saveBlocked || deletingEvent || !pendingSave) return;
        saving = true;
        while (pendingSave && !saveBlocked) {
            const next = pendingSave;
            pendingSave = null;
            $('save-status').textContent = '正在保存到本机…';
            try {
                const result = await window.BinanceAlpha.requestJson('/api/airdrops/save', {method:'POST', signal:AbortSignal.timeout(15000), body:JSON.stringify({revision:ledgerRevision,state:next.state})});
                if (!result.ok) { saveBlocked = Boolean(result.conflict); throw new Error(result.error || '保存失败，请重试。'); }
                ledgerRevision = result.revision;
                if (pendingSave) keepDraft();
                else { try { localStorage.removeItem(draftKey); } catch (_) {} }
                $('ledger-save-warning').hidden = true;
                $('save-status').textContent = '已保存到本机 · ' + now().slice(11);
                if (next.message && !pendingSave) toast(next.message);
            } catch (error) {
                pendingSave = pendingSave || next;
                keepDraft();
                showSaveError(error.message.startsWith('Request failed') ? '本机暂时无法保存，请重试。当前填写已保留为草稿。' : error.message);
                break;
            }
        }
        saving = false;
    }
    function renderMarket() {
        const e = current();
        if (!e) return;
        if (document.activeElement !== $('reference-price')) $('reference-price').value = e.price ?? '';
        $('refresh-quote').disabled = quoteRequests.has(e.id);
        $('refresh-quote').textContent = quoteRequests.has(e.id) ? '更新中…' : '更新价格 ↻';
        $('market-price').textContent = e.price === null ? '—' : qty(e.price);
        let priceStatus = e.quoteMode === 'manual' ? '手动价格' : (e.quoteSource ? `自动 · ${displayTime(e.marketTime || e.priceAt)}` : '等待报价');
        if (e.quoteError) priceStatus += e.price === null ? ' · 更新失败，可手动填价' : ' · 更新失败，旧价保留';
        if (e.quoteSource && Date.now() - Date.parse(e.marketTime || e.priceAt) > 180000) priceStatus += ' · 行情较旧';
        $('price-status').textContent = priceStatus;
        const sum = e.accounts.reduce((n,a) => { const t = totals(a); return {sold:n.sold+t.sold, gross:n.gross+t.gross, cash:n.cash+t.cash, remaining:n.remaining+t.remaining}; }, {sold:0,gross:0,cash:0,remaining:0});
        const remainingValue = e.price === null && sum.remaining > 0 ? null : sum.remaining * (e.price || 0);
        $('stat-cash').textContent = fmt(sum.cash);
        $('stat-average').textContent = `每枚 ${sum.sold ? qty(sum.gross / sum.sold) : '—'} U`;
        $('stat-average-lot').textContent = e.sales.length ? fmt(sum.gross / e.sales.length) : '—';
        $('stat-remaining').textContent = remainingValue === null ? '待填价' : fmt(remainingValue);
        $('stat-total').textContent = remainingValue === null ? '待填价' : fmt(sum.cash + remainingValue);
        $('stat-sold').textContent = `已卖 ${e.accounts.filter(a => a.claimed && totals(a).remaining === 0).length} 份`;
        $('stat-quantity').textContent = `待卖 ${e.accounts.filter(a => totals(a).remaining > 0).length} 份`;
        refreshAccountValues();
    }
    function render() {
        closeEventMenu();
        const e = current();
        $('airdrop-empty').hidden = Boolean(e);
        $('airdrop-content').hidden = !e;
        if (!e) return;
        $('event-options').innerHTML = state.events.map(x => `<button type="button" class="ad-event-choice" data-event="${esc(x.id)}" aria-pressed="${x.id === e.id}" aria-label="${esc(x.token)} · ${esc(x.date)}" title="${esc(x.token)} · ${esc(x.date)} · ${qty(x.amount)} 枚 / 份"><span class="ad-token" aria-hidden="true">${esc(x.token.slice(0,4))}</span></button>`).join('');
        renderMarket();
        const claimed = e.accounts.filter(a => a.claimed).length;
        $('claim-summary').textContent = `已领 ${claimed} / ${e.accounts.length} 份`;
        document.querySelectorAll('[data-sort-mode]').forEach(button => {
            button.setAttribute('aria-pressed', String(button.dataset.sortMode === sortMode()));
        });
        const ordered = orderedAccounts();
        $('account-rows').innerHTML = ordered.map((a) => {
            const t = totals(a);
            const inlineSale = !a.claimed ? '<span class="ad-muted">—</span>' : t.remaining <= 0 ? `<div class="ad-inline-sale"><input type="text" inputmode="decimal" autocomplete="off" value="${esc(String(Number(t.gross.toFixed(8))))}" data-inline-sale-edit="${esc(a.id)}" aria-label="${esc(a.name)}整份卖出价格"><button type="button" data-inline-sale-update="${esc(a.id)}">修改</button></div>` : `<div class="ad-inline-sale"><input type="text" inputmode="decimal" autocomplete="off" placeholder="填卖出价" data-inline-sale-price="${esc(a.id)}" aria-label="${esc(a.name)}整份卖出价格"><button type="button" data-inline-sale-save="${esc(a.id)}">保存</button></div>`;
            return `<tr data-account-row="${esc(a.id)}"><td><div class="ad-sort-controls"><button type="button" class="ad-drag-handle" draggable="true" data-drag="${esc(a.id)}" aria-label="拖动${esc(a.name)}排序"><span class="ad-drag-grip" aria-hidden="true"></span></button></div></td><td><label class="ad-claim-toggle"><input type="checkbox" data-claim="${esc(a.id)}" aria-label="${esc(a.name)}已领取" ${a.claimed ? 'checked' : ''}><span class="ad-claim-mark" aria-hidden="true"></span></label></td><td class="ad-account-name">${esc(a.name)}<small>${a.inactive ? '已停用 · ' : ''}${!a.claimed ? '尚未领取' : t.remaining <= 0 ? '已卖整份' : t.sold ? '还有剩余' : '待卖出'}</small></td><td data-account-value>${accountValue(a, t)}</td><td class="ad-inline-sale-cell">${inlineSale}</td></tr>`;
        }).join('');
        $('account-rows').querySelectorAll('[data-inline-sale-price], [data-inline-sale-edit]').forEach(input => {
            const draft = saleInputDrafts.get(saleInputKey(input));
            if (draft !== undefined) input.value = draft;
        });
        $('sale-count').textContent = `${e.sales.length} 笔`;
        const accountRank = new Map(ordered.map((a,i) => [a.id,i]));
        $('sale-list').innerHTML = e.sales.length ? [...e.sales].sort((a,b) => accountRank.get(a.account) - accountRank.get(b.account) || b.time.localeCompare(a.time)).map(s => {
            const a = e.accounts.find(a => a.id === s.account);
            return `<article class="ad-sale"><div class="ad-sale-top"><span>${esc(a.name)}</span><strong>${fmt(s.quantity*s.price-s.fee)} U</strong></div><div class="ad-sale-bottom"><time>${esc(s.time.replace('T',' '))}</time><button class="ad-text-btn" data-undo="${esc(s.id)}">撤销</button></div></article>`;
        }).join('') : '<div class="ad-empty">暂无卖出记录</div>';
    }
    function updateAccount(ev) {
        if (!ev.target.isConnected || !ev.target.dataset.claim) return;
        const a = current().accounts.find(a => a.id === ev.target.dataset.claim);
        if (!a) return;
        if (!ev.target.checked && totals(a).sold > 0) { toast('请先撤销卖出，再取消领取。'); render(); return; }
        a.claimed = ev.target.checked;
        render(); persist();
    }
    $('account-rows').addEventListener('change', updateAccount);
    $('account-rows').addEventListener('input', ev => {
        if (ev.target.matches('[data-inline-sale-price], [data-inline-sale-edit]')) saleInputDrafts.set(saleInputKey(ev.target), ev.target.value);
    });
    function saveInlineSale(accountId, input) {
        const account = current().accounts.find(a => a.id === accountId), remaining = account ? totals(account).remaining : 0;
        const lotPrice = Number(input?.value.trim());
        if (!account || !account.claimed || remaining <= 0 || !input) return;
        if (!Number.isFinite(lotPrice) || lotPrice <= 0) { toast('请输入这一整份的卖出总价。'); input.focus(); return; }
        current().sales.push({id:id(), account:account.id, quantity:remaining, price:lotPrice / remaining, fee:0, time:now()});
        saleInputDrafts.delete(saleInputKey(input));
        render(); persist('已保存整份卖出记录');
    }
    function saveInlineSaleEdit(accountId, input) {
        const trades = current().sales.filter(s => s.account === accountId);
        const totalPrice = Number(input?.value.trim());
        const currentGross = trades.reduce((sum, trade) => sum + trade.quantity * trade.price, 0);
        if (!trades.length || !input) return;
        if (!Number.isFinite(totalPrice) || totalPrice <= 0) { toast('请输入有效的卖出总价。'); input.focus(); return; }
        if (!Number.isFinite(currentGross) || currentGross <= 0) { toast('原卖出记录无效，无法修改。'); return; }
        const ratio = totalPrice / currentGross;
        trades.forEach(trade => { trade.price = Number((trade.price * ratio).toFixed(12)); });
        saleInputDrafts.delete(saleInputKey(input));
        render(); persist('已修改卖出价格');
    }
    $('account-rows').addEventListener('click', ev => {
        const button = ev.target.closest('[data-inline-sale-save]');
        if (button) saveInlineSale(button.dataset.inlineSaleSave, button.closest('.ad-inline-sale')?.querySelector('[data-inline-sale-price]'));
        const editButton = ev.target.closest('[data-inline-sale-update]');
        if (editButton) saveInlineSaleEdit(editButton.dataset.inlineSaleUpdate, editButton.closest('.ad-inline-sale')?.querySelector('[data-inline-sale-edit]'));
    });
    $('account-rows').addEventListener('keydown', ev => {
        if (ev.key !== 'Enter') return;
        if (ev.target.dataset.inlineSaleEdit) {
            ev.preventDefault(); saveInlineSaleEdit(ev.target.dataset.inlineSaleEdit, ev.target); return;
        }
        if (!ev.target.dataset.inlineSalePrice) return;
        ev.preventDefault(); saveInlineSale(ev.target.dataset.inlineSalePrice, ev.target);
    });
    $('event-options').addEventListener('click', ev => {
        const eventId = ev.target.closest('[data-event]')?.dataset.event;
        if (!eventId || eventId === state.selected || !state.events.some(e => e.id === eventId)) return;
        state.selected = eventId; render(); persist();
        $('event-options').querySelector('[aria-pressed=true]')?.focus({preventScroll:true});
        if (current().quoteMode !== 'manual') refreshQuote(current().id);
    });
    let contextEventId = null, contextTrigger = null;
    function closeEventMenu(restoreFocus = false) {
        $('event-context-menu').hidden = true;
        if (restoreFocus && contextTrigger?.isConnected) contextTrigger.focus({preventScroll:true});
        contextEventId = null; contextTrigger = null;
    }
    function openEventMenu(button, x, y) {
        closeEventMenu();
        contextEventId = button.dataset.event; contextTrigger = button;
        const menu = $('event-context-menu');
        menu.hidden = false;
        const rect = menu.getBoundingClientRect();
        menu.style.left = `${Math.max(8, Math.min(x, innerWidth - rect.width - 8))}px`;
        menu.style.top = `${Math.max(8, Math.min(y, innerHeight - rect.height - 8))}px`;
        $('delete-event').focus({preventScroll:true});
    }
    $('event-options').addEventListener('contextmenu', ev => {
        const button = ev.target.closest('[data-event]');
        if (!button) return;
        ev.preventDefault(); openEventMenu(button, ev.clientX, ev.clientY);
    });
    $('event-options').addEventListener('keydown', ev => {
        if (ev.key !== 'ContextMenu' && !(ev.shiftKey && ev.key === 'F10')) return;
        const button = ev.target.closest('[data-event]');
        if (!button) return;
        ev.preventDefault();
        const rect = button.getBoundingClientRect(); openEventMenu(button, rect.left, rect.bottom);
    });
    document.addEventListener('pointerdown', ev => { if (!$('event-context-menu').contains(ev.target)) closeEventMenu(); });
    document.addEventListener('keydown', ev => {
        if (contextEventId && (ev.key === 'Escape' || ev.key === 'Tab')) { closeEventMenu(ev.key === 'Escape'); if (ev.key === 'Escape') ev.preventDefault(); }
    });
    window.addEventListener('scroll', () => closeEventMenu(), true);
    window.addEventListener('resize', () => closeEventMenu());
    $('delete-event').addEventListener('click', () => {
        const event = state.events.find(e => e.id === contextEventId);
        closeEventMenu();
        if (!event) return;
        confirmAction(`删除 ${event.token}（${event.date}）？这次空投的领取和卖出记录也会删除。`, () => deleteEvent(event.id), {title:'删除空投', label:'删除'});
    });
    async function deleteEvent(eventId) {
        const index = state.events.findIndex(e => e.id === eventId);
        if (index < 0 || deletingEvent) return;
        if (localLedger && (saving || pendingSave || saveBlocked)) { toast('请先完成当前记录的保存，再删除空投。'); return; }
        deletingEvent = true;
        let resultConfirmed = !localLedger;
        try {
            let selected = state.selected;
            if (localLedger) {
                const result = await window.BinanceAlpha.requestJson('/api/airdrops/delete', {method:'POST', signal:AbortSignal.timeout(15000), body:JSON.stringify({event_id:eventId, revision:ledgerRevision})});
                if (typeof result.ok !== 'boolean' || (result.ok && (!Number.isInteger(result.revision) || typeof result.selected !== 'string'))) throw new Error('删除结果暂时无法确认，请重新打开页面核对。');
                resultConfirmed = true;
                if (!result.ok) {
                    if (result.conflict) { saveBlocked = true; showSaveError(result.error); }
                    throw new Error(result.error || '删除失败，请重试。');
                }
                ledgerRevision = result.revision; selected = result.selected;
            }
            state.events.splice(index, 1);
            if (state.selected === eventId) state.selected = localLedger ? selected : (state.events[Math.min(index, state.events.length - 1)]?.id || '');
            for (const key of saleInputDrafts.keys()) { if (JSON.parse(key)[0] === eventId) saleInputDrafts.delete(key); }
            sortModes.delete(eventId);
            try { localStorage.removeItem(`alpha-airdrop-sort-${eventId}`); } catch (_) {}
            if (pendingSave) { pendingSave.state = structuredClone(state); keepDraft(); }
            render();
            if (localLedger) { $('save-status').textContent = '已保存到本机 · ' + now().slice(11); toast('已删除空投'); }
            else persist('已删除空投');
            if (current()?.quoteMode !== 'manual' && current()) refreshQuote(current().id);
        } catch (error) {
            if (!resultConfirmed) { saveBlocked = true; keepDraft(); showSaveError('删除结果暂时无法确认，当前填写已保留。请重新打开页面核对后再操作。'); }
            toast(error.message.startsWith('Request failed') ? '暂时无法确认删除结果，请重新打开页面核对。' : error.message);
        }
        finally { deletingEvent = false; if (pendingSave) flushLedger(); }
    }
    $('price-form').addEventListener('submit', ev => { ev.preventDefault(); const price = Number($('reference-price').value); if (!Number.isFinite(price) || price < 0 || $('reference-price').value === '') return; Object.assign(current(), {price, priceAt:now(), quoteMode:'manual', quoteSource:null, marketTime:null, quoteError:false}); renderMarket(); persist('已使用手动价格，自动更新已暂停'); });
    let confirmedAction = null;
    function confirmAction(message, action, {title = '确认操作', label = '确认'} = {}) {
        $('confirm-title').textContent = title;
        $('confirm-action').textContent = label;
        $('confirm-message').textContent = message;
        confirmedAction = action;
        $('confirm-dialog').showModal();
    }
    $('confirm-action').addEventListener('click', async () => {
        const action = confirmedAction;
        confirmedAction = null;
        const cancel = document.querySelector('[data-close="confirm-dialog"]');
        $('confirm-action').disabled = true; cancel.disabled = true;
        try { if (action) await action(); }
        finally { $('confirm-dialog').close(); $('confirm-action').disabled = false; cancel.disabled = false; }
    });
    $('confirm-dialog').addEventListener('cancel', ev => { if (deletingEvent) ev.preventDefault(); });
    $('sale-list').addEventListener('click', ev => {
        const saleId = ev.target.dataset.undo;
        if (!saleId) return;
        confirmAction('撤销这笔卖出？对应数量会回到剩余持仓。', () => {
            current().sales = current().sales.filter(s => s.id !== saleId); render(); persist('已撤销卖出，剩余数量已恢复');
        });
    });
    document.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => $(b.dataset.close).close()));
    $('new-event').addEventListener('click', () => { refreshMembers(); $('event-form').reset(); selectedHistory = null; $('history-choice').hidden = true; $('event-date').value = now().slice(0,10); $('event-error').textContent = ''; renderHistory(); $('event-dialog').showModal(); if (!catalog.length) loadCatalog(); });
    $('empty-new-event').addEventListener('click', () => $('new-event').click());
    $('event-form').addEventListener('submit', ev => {
        ev.preventDefault();
        const token = $('event-token').value.trim().toUpperCase(), date = $('event-date').value, amount = Number($('event-amount').value);
        const price = $('event-price').value === '' ? null : Number($('event-price').value);
        if (!token || !date || !finite(amount) || amount <= 0 || (price !== null && !finite(price))) { $('event-error').textContent = '请填写币种、日期和有效的数量、价格。'; return; }
        if (localLedger && !bootstrap.members.length) { $('event-error').textContent = '请先在成员管理里添加或启用账户。'; return; }
        const existing = selectedHistory && state.events.find(e => e.historyId === selectedHistory.id);
        if (existing) { state.selected = existing.id; $('event-dialog').close(); render(); persist('这次空投已有记录，已为你打开'); return; }
        const event = {id:id(), token, date, amount, price, priceAt:price === null ? null : now(), quoteMode:price === null ? 'auto' : 'manual', historyId:selectedHistory?.id || null, accounts:accounts().map(a => ({...a, amount})), sales:[]};
        state.events.push(event); state.selected = event.id; $('event-dialog').close(); render(); persist('空投已创建，勾选账户开始领取');
        if (event.quoteMode === 'auto') refreshQuote(event.id);
    });
    $('retry-ledger-save').addEventListener('click', flushLedger);
    $('export-ledger-draft').addEventListener('click', () => {
        const text = localStorage.getItem(draftKey) || JSON.stringify({revision:ledgerRevision,state});
        const url = URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'}));
        const link = document.createElement('a'); link.href=url; link.download='空投未保存记录.txt'; link.click(); URL.revokeObjectURL(url);
    });
    $('discard-ledger-draft').addEventListener('click', () => confirmAction('放弃未保存的填写，重新读取本机已保存记录？', () => { localStorage.removeItem(draftKey); location.reload(); }));
    window.addEventListener('beforeunload', ev => { if (localLedger && (saving || pendingSave || deletingEvent)) { ev.preventDefault(); ev.returnValue=''; } });
    function renderHistory() {
        const query = $('history-search').value.trim().toLowerCase();
        const matches = catalog.filter(e => `${e.token} ${e.name} ${e.date}`.toLowerCase().includes(query));
        $('history-results').innerHTML = matches.slice(0, 30).map(e => `<button type="button" class="ad-history-choice" data-history="${esc(e.id)}" aria-pressed="${selectedHistory?.id === e.id}"><span><strong>${esc(e.token)}</strong> · ${esc(e.name)}<small>${esc(e.date)} ${esc(e.time)}${e.phase ? ` · 第 ${esc(e.phase)} 阶段` : ''} · ${e.amount === null ? '数量待补充' : `${qty(e.amount)} 枚 / 账户`}</small></span><span>选用</span></button>`).join('') || `<div class="ad-empty">${catalog.length ? '没有匹配记录，可以换个名称或手动填写。' : '暂无历史资料，请点击重新读取。'}</div>`;
    }
    async function loadCatalog() {
        if (catalogPromise) return catalogPromise;
        $('history-status').textContent = '正在读取 Alpha123 历史…';
        $('reload-history').disabled = true;
        catalogPromise = (async () => {
            try {
                const data = await window.BinanceAlpha.requestJson('/api/airdrops/catalog', {signal:AbortSignal.timeout(20000)});
                if (!Array.isArray(data.items)) throw new Error('Invalid history');
                catalog = data.items;
                let linked = false;
                state.events.forEach(event => {
                    if (event.historyId) return;
                    const matches = catalog.filter(e => e.token.toUpperCase() === event.token.toUpperCase() && e.date === event.date);
                    if (matches.length === 1) { event.historyId = matches[0].id; linked = true; }
                });
                if (linked) persist();
                $('history-status').textContent = `Alpha123 · ${catalog.length} 条记录`;
                renderHistory();
            } catch (_) { $('history-status').textContent = 'Alpha123 暂时读取失败，请稍后重试。'; }
            finally { $('reload-history').disabled = false; catalogPromise = null; }
        })();
        return catalogPromise;
    }
    async function refreshQuote(eventId, explicit = false) {
        const event = state.events.find(e => e.id === eventId);
        if (!event || quoteRequests.has(eventId) || (!explicit && event.quoteMode === 'manual')) return;
        if (explicit) event.quoteMode = 'auto';
        quoteRequests.add(eventId);
        if (current()?.id === eventId) renderMarket();
        try {
            if (!catalog.length) await loadCatalog();
            if (!event.historyId) {
                const matches = catalog.filter(e => e.token.toUpperCase() === event.token.toUpperCase() && e.date === event.date);
                if (matches.length !== 1) throw new Error('No unique history');
                event.historyId = matches[0].id;
            }
            const data = await window.BinanceAlpha.requestJson(`/api/airdrops/quote?id=${encodeURIComponent(event.historyId)}`, {signal:AbortSignal.timeout(45000)});
            if (data.event_id !== event.historyId || !finite(data.price) || data.price <= 0 || data.currency !== 'USDT') throw new Error('Invalid quote');
            // An in-flight quote must never overwrite a manual edit or a reset.
            if (deletingEvent || event.quoteMode === 'manual' || !state.events.includes(event)) return;
            Object.assign(event, {price:data.price, priceAt:data.fetched_at, quoteSource:data.source, marketTime:data.market_time, quoteError:false});
            persist(explicit ? '已更新币安参考价' : '');
        } catch (_) {
            if (event.quoteMode !== 'manual') event.quoteError = true;
            if (explicit) toast('暂时未获取到明确报价，已保留原价格。请从历史选择空投，或稍后重试。');
        } finally {
            quoteRequests.delete(eventId);
            if (current() === event) renderMarket();
        }
    }
    $('history-search').addEventListener('input', renderHistory);
    $('reload-history').addEventListener('click', loadCatalog);
    $('history-results').addEventListener('click', ev => {
        const button = ev.target.closest('[data-history]');
        if (!button) return;
        selectedHistory = catalog.find(e => e.id === button.dataset.history);
        $('event-token').value = selectedHistory.token;
        $('event-date').value = selectedHistory.date;
        $('event-amount').value = selectedHistory.amount ?? '';
        $('event-price').value = '';
        $('history-choice').textContent = `已选 ${selectedHistory.token} · ${selectedHistory.date}`;
        $('history-choice').hidden = false;
        renderHistory();
    });
    ['event-token','event-date'].forEach(key => $(key).addEventListener('input', () => { selectedHistory = null; $('history-choice').hidden = true; renderHistory(); }));
    $('refresh-quote').addEventListener('click', () => refreshQuote(current().id, true));
    render();
    if (localLedger) {
        window.addEventListener('focus', () => refreshMembers());
        document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshMembers(); });
        window.setInterval(() => { if (!document.hidden) refreshMembers(); }, 10000);
        refreshMembers();
    }
    if (pendingSave) flushLedger();
    loadCatalog().then(() => { if (current()) refreshQuote(current().id); });
    window.setInterval(() => { if (current() && !document.hidden && !$('event-dialog').open && !$('confirm-dialog').open && document.activeElement !== $('reference-price')) refreshQuote(current().id); }, 60000);
    if (storageWarning) { toast(storageWarning); $('save-status').textContent = storageWarning; if (localLedger) showSaveError(storageWarning); }
})();
