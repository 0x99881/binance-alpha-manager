from __future__ import annotations

from .store_bootstrap_service import StoreBootstrapService


class StoreApplication:
    def __init__(self, base_dir, read_only: bool = False):
        self._context = StoreBootstrapService().create_context(base_dir, read_only=read_only)

    @property
    def workbook_path(self):
        return self._context.workbook_path

    @workbook_path.setter
    def workbook_path(self, value) -> None:
        self._context.workbook_path = value

    @property
    def read_only(self) -> bool:
        return bool(self._context.read_only)

    @property
    def daily_entry_service(self):
        return self._context.daily_entry_service

    @property
    def member_service(self):
        return self._context.member_service

    @property
    def airdrop_ledger_service(self):
        return self._context.airdrop_ledger_service

    # ---- queries ----------------------------------------------------------
    def get_quick_scores(self): return self._context.query_service.get_quick_scores()
    def get_members(self): return self._context.query_service.get_members()
    def get_active_members(self): return self._context.query_service.get_active_members()
    def get_member_wear_records(self, name, year_hint=None, workbook=None): return self._context.query_service.get_member_wear_records(name, year_hint=year_hint, workbook=workbook)
    def get_score_summary(self): return self._context.query_service.get_score_summary()
    def get_score_sheet_view(self): return self._context.query_service.get_score_sheet_view()
    def get_wear_sheet_view(self): return self._context.query_service.get_wear_sheet_view()
    def get_wear_sheet_view_for_cycle(self, *, start_iso, end_iso): return self._context.query_service.get_wear_sheet_view_for_cycle(start_iso=start_iso, end_iso=end_iso)
    def get_value_sheet_view(self, sheet_type: str, *, cycle_window=None): return self._context.query_service.get_value_sheet_view(sheet_type, cycle_window=cycle_window)
    def get_value_sheet_view_for_cycle(self, sheet_type: str, *, start_iso, end_iso): return self._context.query_service.get_value_sheet_view_for_cycle(sheet_type, start_iso=start_iso, end_iso=end_iso)
    def get_member_profit_calendar(self, name, year, month): return self._context.query_service.get_member_profit_calendar(name, year, month)
    def get_member_profit_calendar_for_cycle(self, name, cycle_window, cycles): return self._context.query_service.get_member_profit_calendar_for_cycle(name, cycle_window, cycles)
    def get_calendar_combo(self, *, name, year, month, cycle_window, all_cycles): return self._context.query_service.get_calendar_combo(name=name, year=year, month=month, cycle_window=cycle_window, all_cycles=all_cycles)
    def get_all_cycles(self): return self._context.query_service.get_all_cycles()
    def get_cycle_profit_view(self, cycle_id=None): return self._context.query_service.get_cycle_profit_view(cycle_id)
    def get_cycle_window(self, cycle_id=None): return self._context.cycle_service.get_window(cycle_id)
    def get_current_cycle_wear_summary(self, cycle_id=None): return self._context.cycle_service.get_current_cycle_wear_summary(cycle_id)
    def get_next_score_date(self): return self._context.query_service.get_next_score_date()
    def get_score_rows_for_date(self, score_date): return self._context.query_service.get_score_rows_for_date(score_date)
    def get_score_date_notes(self, score_date): return self._context.query_service.get_score_date_notes(score_date)
    def get_online_active_members(self): return self._context.query_service.get_online_active_members()
    def get_online_score_summary(self): return self._context.query_service.get_online_score_summary()
    def get_online_wear_sheet_view(self): return self._context.query_service.get_online_wear_sheet_view()
    def get_online_cycle_wear_summary(self): return self._context.query_service.get_online_cycle_wear_summary()
    def get_online_next_score_date(self): return self._context.query_service.get_online_next_score_date()
    def get_mobile_overview(self): return self._context.query_service.get_mobile_overview()
    def is_supabase_configured(self): return self._context.command_service.is_supabase_configured()
    def get_cash_flow_view(self): return self._context.cashflow_service.get_view()

    # ---- commands ---------------------------------------------------------
    def create_settlement_cycle(self, start_date): return self._context.command_service.create_settlement_cycle(start_date)
    def save_cycle_settlement(self, cycle_id, form_data): return self._context.command_service.save_cycle_settlement(cycle_id, form_data)
    def settle_cycle(self, cycle_id, settle_date): return self._context.command_service.settle_cycle(cycle_id, settle_date)
    def settle_and_create_next_cycle(self, cycle_id, settle_date, end_balances): return self._context.command_service.settle_and_create_next_cycle(cycle_id, settle_date, end_balances)
    def delete_cycle(self, cycle_id): return self._context.command_service.delete_cycle(cycle_id)
    def add_cycle_member(self, cycle_id, member_name): return self._context.command_service.add_cycle_member(cycle_id, member_name)
    def remove_cycle_member(self, cycle_id, member_name): return self._context.command_service.remove_cycle_member(cycle_id, member_name)
    def reorder_cycle_members(self, cycle_id, ordered_names): return self._context.command_service.reorder_cycle_members(cycle_id, ordered_names)
    def set_wear_abnormal_threshold(self, value_text): return self._context.command_service.set_wear_abnormal_threshold(value_text)
    def refresh_local_database(self): return self._context.command_service.refresh_local_database()
    def save_daily_entry(self, form_data, selected_date, *, existing_notes=None, export_to_excel=True): return self._context.command_service.save_daily_entry(form_data, selected_date, existing_notes=existing_notes, export_to_excel=export_to_excel)
    def export_to_excel(self, date_text=None): return self._context.export_service.export_to_excel(date_text)
    def delete_score_date(self, date_text): return self._context.command_service.delete_score_date(date_text)
    def add_cash_flow(self, form_data): return self._context.cashflow_service.add(form_data)
    def delete_cash_flow(self, flow_id): return self._context.cashflow_service.delete(flow_id)
    def supabase_push(self, *, force_full=True): return self._context.command_service.supabase_push(force_full=force_full)
    def supabase_pull(self, *, force_full=False): return self._context.command_service.supabase_pull(force_full=force_full)


__all__ = ["StoreApplication"]
