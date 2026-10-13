from __future__ import annotations

from datetime import datetime
import hmac
import os
from typing import Any

from flask import abort, flash, redirect, render_template, request, Response, send_from_directory, url_for

from .constants import DISABLED, ENABLED
from .ui_text import JS_UI_TEXT, MESSAGES, UI_TEXT, display_workbook_filename
from .web_cashflow_routes import register_cashflow_routes
from .web_airdrop_routes import register_airdrop_routes
from .web_cycle_routes import register_cycle_routes
from .web_member_routes import register_member_routes
from .web_score_routes import register_score_routes
from .web_sync_routes import register_sync_routes


def register_routes(app, store) -> None:
    read_only_mode = bool(getattr(store, 'read_only', False))

    _app_password = os.environ.get("BINANCE_ALPHA_PASSWORD") or os.environ.get("BM2_PASSWORD", "")
    if _app_password:
        @app.before_request
        def _require_auth():
            auth = request.authorization
            if not auth or not hmac.compare_digest(str(auth.password), _app_password):
                return Response(
                    "币安 Alpha 多号管理系统需要登录。",
                    401,
                    {"WWW-Authenticate": 'Basic realm="Binance Alpha"'},
                )

    if 'asset_file' not in app.view_functions and app.static_folder:
        @app.get('/assets/<path:filename>')
        def asset_file(filename: str):
            asset_root = app.static_folder
            if not asset_root or not os.path.isfile(os.path.join(asset_root, filename)):
                abort(404)
            return send_from_directory(asset_root, filename)

    def _flash_remote_sync_needed() -> None:
        if store.is_supabase_configured():
            flash(MESSAGES['remote_sync_needed'], 'warning')
        else:
            flash(MESSAGES['remote_sync_needed_no_config'], 'warning')

    @app.context_processor
    def inject_shared_data() -> dict[str, Any]:
        return {
            'excel_filename': display_workbook_filename(store.workbook_path.name),
            'quick_scores': store.get_quick_scores(),
            'asset_version': '20260712-01',
            'ui': UI_TEXT,
            'js_ui_text': JS_UI_TEXT,
            'enabled_status': ENABLED,
            'disabled_status': DISABLED,
            'read_only_mode': read_only_mode,
        }

    @app.after_request
    def disable_cache(response):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        if response.mimetype == 'text/html':
            response.headers['Content-Type'] = 'text/html; charset=utf-8'
        return response

    @app.route('/wear')
    def wear_entry():
        if read_only_mode:
            return render_template(
                'wear.html',
                wear_sheet=store.get_online_wear_sheet_view(),
                cycle_wear=store.get_online_cycle_wear_summary(),
            )
        cycle_id_arg = (request.args.get('cycle') or '').strip()
        cycles = store.get_all_cycles()
        # ``cycle=all`` ⇒ no date filter (entire history). Anything else ⇒ that
        # cycle's window (default: current/latest cycle).
        if cycle_id_arg == 'all':
            cycle_window = store.get_cycle_window()  # for hint text only
            start_iso, end_iso = '', ''
            selected_cycle_id = 'all'
            cycle_wear = store.get_current_cycle_wear_summary(None)
        else:
            cycle_window = store.get_cycle_window(cycle_id_arg or None)
            start_iso = cycle_window.get('start_date', '')
            end_iso = cycle_window.get('end_date', '')
            selected_cycle_id = cycle_window.get('cycle_id', '')
            cycle_wear = store.get_current_cycle_wear_summary(cycle_id_arg or None)
        return render_template(
            'wear.html',
            wear_sheet=store.get_wear_sheet_view_for_cycle(start_iso=start_iso, end_iso=end_iso),
            cycle_wear=cycle_wear,
            cycles=cycles,
            selected_cycle_id=selected_cycle_id,
        )

    @app.get('/airdrops')
    def airdrop_preview():
        if read_only_mode:
            return redirect(url_for('score_overview'))
        ledger = getattr(store, 'airdrop_ledger_service', None)
        return render_template('airdrops.html', title='空投领取与卖出',
                               airdrop_bootstrap=ledger.view() if ledger else {'mode': 'demo'})

    def _render_value_chart(sheet_type, page_title, page_hint, total_label, marked_hint, marker_class):
        cycle_id_arg = (request.args.get('cycle') or '').strip()
        cycles = store.get_all_cycles()
        # ``cycle=all`` ⇒ no filter (entire history). Otherwise resolve to the
        # chosen cycle's window (default: current/latest).
        if cycle_id_arg == 'all':
            cycle_window = store.get_cycle_window()
            start_iso, end_iso = '', ''
            selected_cycle_id = 'all'
        else:
            cycle_window = store.get_cycle_window(cycle_id_arg or None)
            if not cycle_window.get('has_cycle'):
                start_iso, end_iso = '', ''
                selected_cycle_id = 'all'
            else:
                start_iso = cycle_window.get('start_date', '')
                end_iso = cycle_window.get('end_date', '')
                selected_cycle_id = cycle_window.get('cycle_id', '')
        # Build chart + preview from SQLite so BOTH respect the selected
        # cycle (the legacy snapshot-based path only filtered chart bars).
        value_sheet = store.get_value_sheet_view_for_cycle(
            sheet_type, start_iso=start_iso, end_iso=end_iso,
        )
        return render_template(
            'value_chart.html',
            value_sheet=value_sheet,
            page_title=page_title,
            page_hint=page_hint,
            total_label=total_label,
            marked_hint=marked_hint,
            marker_class=marker_class,
            cycles=cycles,
            selected_cycle_id=selected_cycle_id,
            cycle_window=cycle_window,
        )

    @app.route('/income-chart')
    def income_chart():
        if read_only_mode:
            return redirect(url_for('score_overview'))
        return _render_value_chart(
            'income',
            UI_TEXT['income_chart_title'],
            UI_TEXT['income_chart_hint'],
            UI_TEXT['chart_total_income'],
            UI_TEXT['chart_marked_income_hint'],
            'income',
        )

    @app.route('/expense-chart')
    def expense_chart():
        if read_only_mode:
            return redirect(url_for('score_overview'))
        return _render_value_chart(
            'expense',
            UI_TEXT['expense_chart_title'],
            UI_TEXT['expense_chart_hint'],
            UI_TEXT['chart_total_expense'],
            UI_TEXT['chart_marked_expense_hint'],
            'expense',
        )

    @app.post('/wear/threshold')
    def save_wear_threshold():
        if read_only_mode:
            abort(403)
        threshold_text = request.form.get('wear_abnormal_threshold', '').strip()
        try:
            threshold = store.set_wear_abnormal_threshold(threshold_text)
            flash(MESSAGES['wear_threshold_saved'].format(threshold=threshold), 'success')
        except ValueError as exc:
            error_text = str(exc).strip()
            if error_text:
                flash(error_text, 'error')
            else:
                flash(MESSAGES['wear_threshold_invalid'], 'error')
        return redirect(url_for('wear_entry'))

    @app.route('/profit-calendar')
    def profit_calendar():
        if read_only_mode:
            return redirect(url_for('score_overview'))
        members = store.member_service.get_members()
        if not members:
            abort(404)
        member_names = [item['name'] for item in members]
        selected_name = request.args.get('name', '').strip() or 'all'
        if selected_name not in ['all', *member_names]:
            selected_name = 'all'

        # Unified month-grid + cycle-overlay payload.
        # - year/month from URL drives the visible calendar grid
        # - cycle drives stats / 盈亏榜 + which days are "focused"
        # - cycle=all ⇒ every day fully opaque, stats span all history
        cycle_id_arg = (request.args.get('cycle') or '').strip()
        all_cycles = store.get_all_cycles()
        year = request.args.get('year', type=int) or datetime.now().year
        month = request.args.get('month', type=int) or datetime.now().month
        if cycle_id_arg == 'all':
            cycle_window = None  # 全部历史
        else:
            cycle_window = store.get_cycle_window(cycle_id_arg or None)
            if not cycle_window.get('has_cycle'):
                cycle_window = None  # 无周期 ⇒ 退化为全部
        calendar_data = store.get_calendar_combo(
            name=selected_name, year=year, month=month,
            cycle_window=cycle_window, all_cycles=all_cycles,
        )
        return render_template(
            'profit_calendar.html',
            members=members,
            selected_name=selected_name,
            calendar_data=calendar_data,
        )

    register_score_routes(
        app,
        store,
        read_only_mode=read_only_mode,
        flash_remote_sync_needed=_flash_remote_sync_needed,
    )
    register_member_routes(
        app,
        store,
        read_only_mode=read_only_mode,
        flash_remote_sync_needed=_flash_remote_sync_needed,
    )
    register_cycle_routes(app, store, read_only_mode=read_only_mode)
    register_cashflow_routes(app, store, read_only_mode=read_only_mode)
    register_airdrop_routes(app, getattr(store, 'airdrop_ledger_service', None))
    register_sync_routes(app, store, read_only_mode=read_only_mode)
