from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..excel import ExpenseSheet, IncomeSheet, ScoreSheet, WearSheet
from ..excel.workbook_reader import ExcelWorkbookReader
from ..excel.workbook_writer import ExcelWorkbookWriter
from ..excel.workbook_repository import WorkbookRepository
from ..local_database import LocalDatabase
from ..presenters import CycleProfitPresenter, ProfitCalendarPresenter, ScorePresenter, ValueSheetPresenter, WearPresenter
from ..repositories import ConfigRepository, SupabaseClient
from ..sqlite_to_excel_exporter import SQLiteToExcelExporter
from .application_service import ApplicationService
from .airdrop_ledger_service import AirdropLedgerService
from ..repositories.sqlite_airdrop_repository import SQLiteAirdropRepository
from .cashflow_service import CashFlowService
from .cycle_service import CycleService
from .daily_entry_service import DailyEntryService
from .excel_export_service import ExcelExportService
from .member_service import MemberService
from .excel_import_service import ExcelImportService
from .sqlite_entry_writer import SQLiteEntryWriter
from .store_command_service import StoreCommandService
from .store_context import StoreContext
from .store_query_service import StoreQueryService
from .supabase_entry_writer import SupabaseEntryWriter
from .sync_service import SyncService


class StoreBootstrapService:
    def timestamp(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def create_context(self, base_dir, *, read_only: bool = False) -> StoreContext:
        context = StoreContext()
        context.base_dir = Path(base_dir)
        if not read_only:
            context.base_dir.mkdir(parents=True, exist_ok=True)
        context.read_only = read_only
        context.bootstrap_service = self
        context.config_repository = ConfigRepository(context.base_dir / "system_config.json", read_only=read_only)
        context.config_repository.ensure_member_config(self.timestamp)
        context.workbook_repository = WorkbookRepository(context.base_dir, context.config_repository, read_only=read_only)
        context.workbook_path = context.workbook_repository.workbook_path
        context.score_sheet = ScoreSheet(context)
        context.wear_sheet = WearSheet(context)
        context.income_sheet = IncomeSheet(context)
        context.expense_sheet = ExpenseSheet(context)
        context.workbook_reader = ExcelWorkbookReader(context)
        context.workbook_writer = ExcelWorkbookWriter(context)
        context.query_service = StoreQueryService(context)
        context.supabase = SupabaseClient(context.base_dir)
        if read_only:
            context.local_db = None
            context.excel_exporter = None
            context.export_service = ExcelExportService(context)
            context.excel_import_service = None
            context.sync_service = SyncService(context.local_db, context.supabase)
            context.application_service = ApplicationService(
                context.local_db,
                context.supabase,
                lambda: datetime.now().strftime("%Y-%m-%d"),
            )
            entry_writer = SupabaseEntryWriter(context.supabase)
            context.sqlite_entry_writer = None
        else:
            context.local_db = LocalDatabase(context.base_dir, read_only=read_only)
            context.ensure_workbook()
            context.excel_exporter = SQLiteToExcelExporter(context.local_db, context.workbook_writer)
            context.export_service = ExcelExportService(context)
            context.excel_import_service = ExcelImportService(
                context.local_db,
                context,
                context.query_service.config_members,
                context.excel_exporter,
            )
            context.excel_import_service.bootstrap_from_excel_if_empty()
            context.sync_service = SyncService(context.local_db, context.supabase, after_pull=context.excel_import_service.after_supabase_pull)
            context.application_service = self.create_online_application_service(context)
            context.sqlite_entry_writer = SQLiteEntryWriter(context.local_db)
            entry_writer = context.sqlite_entry_writer
        context.daily_entry_service = DailyEntryService(entry_writer)
        member_after_change = None if read_only else context.export_service.sync_member_visibility
        context.member_service = MemberService(context.get_members, context.local_db, after_change=member_after_change)
        context.airdrop_ledger_service = None if read_only else AirdropLedgerService(
            SQLiteAirdropRepository(context.local_db._connect),
        )
        context.command_service = StoreCommandService(context)
        context.score_presenter = ScorePresenter(context)
        context.wear_presenter = WearPresenter(context)
        context.value_sheet_presenter = ValueSheetPresenter(context)
        context.profit_calendar_presenter = ProfitCalendarPresenter(context)
        if read_only:
            context.cycle_service = None
            context.cycle_profit_presenter = None
            context.cashflow_service = None
        else:
            context.cycle_service = CycleService(context.local_db, context.query_service.get_active_members)
            context.cycle_profit_presenter = CycleProfitPresenter(context.cycle_service)
            context.cycle_service._on_overview_regen = self._make_overview_regen(context)
            self._sync_cycle_overview_on_startup(context)
            context.cashflow_service = CashFlowService(
                context.local_db,
                context.workbook_repository,
                today_provider=lambda: datetime.now().strftime("%Y-%m-%d"),
            )
        return context

    @staticmethod
    def _sync_cycle_overview_on_startup(context) -> None:
        """Force one overview regen at boot so the Excel sheet matches DB.

        Without this, a user who just upgraded the code keeps seeing the old
        per-cycle tabs until they perform some cycle action. If Excel is
        locked at startup we skip silently — the next user-triggered change
        will regenerate cleanly.
        """
        try:
            cycles_data = context.cycle_service.get_all_cycles_data()
        except (OSError, ValueError):
            return
        if not cycles_data:
            return
        try:
            context.cycle_service._on_overview_regen(cycles_data)
        except (OSError, ValueError):
            return

    def create_online_application_service(self, context) -> ApplicationService:
        return ApplicationService(context.local_db, context.supabase, context.query_service.get_next_score_date)

    def _make_overview_regen(self, context):
        from ..excel.cycle_sheet import regenerate_overview_sheet

        def regen(cycles_data: list) -> None:
            # 1. Refresh the cycle-profit overview tab.
            regenerate_overview_sheet(context.workbook_repository, cycles_data)
            # 2. Rebuild wear/income/expense with the cycle-block layout so a
            #    newly-settled cycle becomes visible as a historical block.
            #    Excel-busy errors propagate so settle_and_create_next can roll
            #    back DB state and the user can retry with Excel closed.
            context.excel_exporter.export_missing_dates(context)

        return regen
