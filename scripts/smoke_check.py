from __future__ import annotations

import importlib
import json
import re
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
TEST_TEMP_ROOT = PROJECT_ROOT / ".tmp_test_workspaces"


MODULES_TO_IMPORT = [
    "binance_alpha.services.store_application",
    "binance_alpha.services.daily_entry_service",
    "binance_alpha.presenters.score_presenter",
    "binance_alpha.presenters.wear_presenter",
    "binance_alpha.presenters.profit_calendar_presenter",
]


class SmokeCheckRunner:
    def __init__(self) -> None:
        self.results: list[tuple[bool, str, str]] = []
        self._store = None
        self._temp_root: Path | None = None

    def check(self, name: str, func) -> None:
        try:
            detail = func()
            self.results.append((True, name, "" if detail is None else str(detail)))
        except Exception as exc:  # pragma: no cover - smoke output path
            self.results.append((False, name, f"{type(exc).__name__}: {exc}"))

    def build_store(self):
        if self._store is not None:
            return self._store
        from binance_alpha.services.store_application import StoreApplication

        temp_dir = TEST_TEMP_ROOT / "smoke"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir(parents=True)
        self._temp_root = temp_dir
        self._store = StoreApplication(temp_dir)
        return self._store

    def check_module_imports(self) -> None:
        for module_name in MODULES_TO_IMPORT:
            self.check(f"import {module_name}", lambda module_name=module_name: importlib.import_module(module_name).__name__)

    def check_workbook_branding_compatibility(self) -> None:
        def _run():
            from binance_alpha.constants import DATA_FILE_PATTERNS, WORKBOOK_FILENAME_PREFIX
            from binance_alpha.ui_text import display_workbook_filename

            if WORKBOOK_FILENAME_PREFIX != "币安Alpha记录_":
                raise ValueError("new workbooks do not use the Binance Alpha name")
            if "BM2记录_*.xlsx" not in DATA_FILE_PATTERNS:
                raise ValueError("legacy workbooks are no longer discoverable")
            displayed = display_workbook_filename("BM2记录_2026-07-12.xlsx")
            if displayed != "币安Alpha记录_2026-07-12.xlsx":
                raise ValueError("legacy workbook name is still visible")
            return displayed

        self.check("workbook branding keeps legacy data compatible", _run)

    def check_application_init(self) -> None:
        def _run():
            store = self.build_store()
            return f"workbook={store.workbook_path.name}"

        self.check("StoreApplication 初始化", _run)

    def check_store_read_entry(self) -> None:
        def _run():
            store = self.build_store()
            next_date = store.get_next_score_date()
            wear_records = store.get_member_wear_records("bb")
            return f"next_date={next_date}, wear_records={len(wear_records)}"

        self.check("repository 入口", _run)

    def check_presenter_entry(self) -> None:
        def _run():
            store = self.build_store()
            summary = store.get_score_summary()
            required_keys = {"latest_column", "window_size", "rankings"}
            missing = required_keys - set(summary.keys())
            if missing:
                raise ValueError(f"missing keys: {sorted(missing)}")
            return f"summary_keys={sorted(summary.keys())}"

        self.check("presenter 入口", _run)

    def check_score_overview_sorting_and_low_score(self) -> None:
        def _run():
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "score_overview_sorting"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                members = store.get_active_members()
                high_member = members[0]["name"]
                low_member = members[1]["name"]
                target_date = store.get_next_score_date()
                form_data = {f"score_{member['name']}": "0" for member in members}
                form_data[f"score_{high_member}"] = "17"
                form_data[f"score_{low_member}"] = "9"
                result = store.daily_entry_service.process_submission(members, form_data, target_date)
                if not result.get("ok"):
                    raise ValueError(result.get("error"))

                score_sheet = store.get_score_sheet_view()
                name_index = next(
                    index
                    for index, header in enumerate(score_sheet["headers"])
                    if header["kind"] == "name"
                )
                latest_index = next(
                    index
                    for index, header in enumerate(score_sheet["headers"])
                    if header["value"] == target_date[5:]
                )
                rows = score_sheet["rows"]
                if rows[0][name_index]["value"] != high_member:
                    raise ValueError("score overview is not sorted by total score")
                low_row = next(row for row in rows if row[name_index]["value"] == low_member)
                high_row = next(row for row in rows if row[name_index]["value"] == high_member)
                if not low_row[latest_index].get("is_low_score"):
                    raise ValueError("low daily score is not marked")
                if high_row[latest_index].get("is_low_score"):
                    raise ValueError("normal daily score was marked low")
                return f"{high_member}>{low_member}"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("score overview sorting and low score", _run)

    def check_online_score_overview_sorting_and_low_score(self) -> None:
        def _run():
            from binance_alpha.constants import ENABLED
            from binance_alpha.services.application_service import ApplicationService

            class FakeSupabase:
                def is_configured(self):
                    return True

                def pull_members(self):
                    return [
                        {"name": "low", "status": ENABLED, "deleted": 0, "sort_order": 1},
                        {"name": "high", "status": ENABLED, "deleted": 0, "sort_order": 2},
                    ]

                def pull_score_entries(self):
                    return [
                        {"member_name": "low", "score_date": "2026-05-12", "score": 9, "deleted": 0},
                        {"member_name": "high", "score_date": "2026-05-12", "score": 17, "deleted": 0},
                    ]

            service = ApplicationService(None, FakeSupabase(), lambda: "2026-05-12")
            view = service.mobile_overview()["score_sheet_view"]
            headers = view["headers"]
            name_index = next(index for index, header in enumerate(headers) if header["kind"] == "name")
            latest_index = next(index for index, header in enumerate(headers) if header["value"] == "05-12")
            rows = view["rows"]
            if rows[0][name_index]["value"] != "high":
                raise ValueError("online score overview is not sorted by total score")
            low_row = next(row for row in rows if row[name_index]["value"] == "low")
            high_row = next(row for row in rows if row[name_index]["value"] == "high")
            if not low_row[latest_index].get("is_low_score"):
                raise ValueError("online low daily score is not marked")
            if high_row[latest_index].get("is_low_score"):
                raise ValueError("online normal daily score was marked low")
            return "high>low"

        self.check("online score overview sorting and low score", _run)

    def check_online_wear_view_uses_synced_wear(self) -> None:
        def _run():
            from binance_alpha.constants import ENABLED
            from binance_alpha.services.application_service import ApplicationService

            class FakeSupabase:
                def is_configured(self):
                    return True

                def pull_members(self):
                    return [
                        {"name": "manual", "status": ENABLED, "deleted": 0, "sort_order": 1},
                        {"name": "balance", "status": ENABLED, "deleted": 0, "sort_order": 2},
                    ]

                def pull_score_entries(self):
                    return [
                        {
                            "member_name": "manual",
                            "score_date": "2026-05-12",
                            "score": 8,
                            "manual_wear": "3",
                            "before_balance": "",
                            "after_balance": "",
                            "deleted": 0,
                        },
                        {
                            "member_name": "balance",
                            "score_date": "2026-05-12",
                            "score": 8,
                            "manual_wear": "",
                            "before_balance": "10",
                            "after_balance": "8",
                            "deleted": 0,
                        },
                    ]

            service = ApplicationService(None, FakeSupabase(), lambda: "2026-05-12")
            view = service.online_wear_sheet_view()
            date_index = view["headers"].index("05-12")
            name_index = view["headers"].index("姓名")
            manual_row = next(row for row in view["rows"] if row[name_index]["value"] == "manual")
            balance_row = next(row for row in view["rows"] if row[name_index]["value"] == "balance")
            if manual_row[date_index]["value"] != 3.0:
                raise ValueError("manual wear is not shown online")
            if not manual_row[date_index]["is_abnormal"]:
                raise ValueError("online abnormal wear is not marked")
            if balance_row[date_index]["value"] != 2.0:
                raise ValueError("balance-derived wear is not shown online")
            return "manual=3 balance=2"

        self.check("online wear view uses synced wear", _run)

    def check_service_entry(self) -> None:
        def _run():
            store = self.build_store()
            active_members = store.get_active_members()
            selected_date = store.get_next_score_date()
            result = store.daily_entry_service.process_submission(active_members, {}, selected_date)
            if "ok" not in result:
                raise ValueError("missing ok flag")
            if result["ok"]:
                raise ValueError("empty submission should not save")
            if store.get_score_rows_for_date(selected_date):
                raise ValueError("empty submission created rows")
            return f"ok={result['ok']}"

        self.check("service 入口", _run)

    def check_application_entry(self) -> None:
        def _run():
            store = self.build_store()
            summary = store.get_score_summary()
            wear_view = store.get_wear_sheet_view()
            if "rankings" not in summary:
                raise ValueError("score summary missing rankings")
            if "rows" not in wear_view:
                raise ValueError("wear view missing rows")
            return f"rankings={len(summary['rankings'])}, wear_rows={wear_view['row_count']}"

        self.check("application 入口", _run)

    def check_static_assets(self) -> None:
        def _run():
            base_template = (PROJECT_ROOT / "templates" / "base.html").read_text(encoding="utf-8")
            referenced_assets = re.findall(r"filename='([^']+)'", base_template)
            missing = [asset for asset in referenced_assets if not (PROJECT_ROOT / "static" / asset).exists()]
            if missing:
                raise FileNotFoundError(", ".join(missing))
            app_js_path = PROJECT_ROOT / "static" / "app.js"
            if not app_js_path.exists():
                raise FileNotFoundError("static/app.js")
            app_js = app_js_path.read_text(encoding="utf-8")
            if "initSubmitLock" not in app_js or "aria-busy" not in app_js:
                raise ValueError("submit lock script is missing")
            return f"assets={len(referenced_assets)}"

        self.check("模板静态资源", _run)

    def check_page_routes(self) -> None:
        def _run():
            from flask import Flask

            from binance_alpha.services.store_application import StoreApplication
            from binance_alpha.web import register_routes

            temp_dir = TEST_TEMP_ROOT / "routes"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                app = Flask(
                    __name__,
                    template_folder=str(PROJECT_ROOT / "templates"),
                    static_folder=str(PROJECT_ROOT / "static"),
                )
                app.secret_key = "smoke"
                register_routes(app, StoreApplication(temp_dir))
                client = app.test_client()
                routes = ["/scores", "/score-overview", "/wear", "/income-chart", "/expense-chart", "/profit-calendar", "/members"]
                failures = []
                for route in routes:
                    response = client.get(route)
                    if response.status_code != 200:
                        failures.append(f"{route}={response.status_code}")
                    if route == "/scores":
                        if response.data.count(b"data-risk-confirm") != 4:
                            failures.append("/scores risk confirmation buttons missing")
                if failures:
                    raise ValueError(", ".join(failures))
                return f"routes={len(routes)}"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("页面路由", _run)

    def check_read_only_routes_do_not_open_excel(self) -> None:
        def _run():
            from flask import Flask

            from binance_alpha.services.store_application import StoreApplication
            from binance_alpha.web import register_routes

            temp_dir = TEST_TEMP_ROOT / "read_only_routes"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir, read_only=True)
                if not store.read_only:
                    raise ValueError("read_only was not exposed")
                store.get_online_next_score_date = lambda: "2026-05-14"
                store.get_score_rows_for_date = lambda _date: []
                store.get_online_active_members = lambda: []
                store.get_online_score_summary = lambda: {"rankings": [], "latest_column": "", "window_size": 15}

                app = Flask(
                    __name__,
                    template_folder=str(PROJECT_ROOT / "templates"),
                    static_folder=str(PROJECT_ROOT / "static"),
                )
                app.secret_key = "smoke"
                register_routes(app, store)
                response = app.test_client().get("/scores")
                if response.status_code != 200:
                    raise ValueError(f"/scores={response.status_code}")
                return "read_only=/scores"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("read only routes avoid excel", _run)

    def check_excel_date_notes_import(self) -> None:
        def _run():
            from openpyxl import load_workbook

            from binance_alpha.constants import NAME_HEADER, SCORE_SHEET
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "excel_date_notes_import"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                members = store.get_active_members()
                target_date = store.get_next_score_date()
                first_name = members[0]["name"]
                form_data = {f"score_{member['name']}": "1" for member in members}
                form_data["date_note_1"] = "old note"
                result = store.daily_entry_service.process_submission(
                    members,
                    form_data,
                    target_date,
                    existing_notes=store.get_score_date_notes(target_date),
                )
                if not result.get("ok"):
                    raise ValueError(result.get("error"))
                store.export_to_excel(target_date)

                def _note_cells():
                    workbook = load_workbook(store.workbook_path)
                    try:
                        sheet = workbook[SCORE_SHEET]
                        date_col = next(
                            col
                            for col in range(1, sheet.max_column + 1)
                            if str(sheet.cell(1, col).value or "") == target_date[5:]
                        )
                        name_col = next(
                            col
                            for col in range(1, sheet.max_column + 1)
                            if str(sheet.cell(1, col).value or "") == NAME_HEADER
                        )
                        named_rows = [
                            row
                            for row in range(2, sheet.max_row + 1)
                            if str(sheet.cell(row, name_col).value or "").strip()
                        ]
                        if not named_rows:
                            raise ValueError("no member rows")
                        if not any(str(sheet.cell(row, name_col).value or "") == first_name for row in named_rows):
                            raise ValueError("member row missing")
                        return workbook, sheet, date_col, max(named_rows) + 1
                    except (KeyError, StopIteration, ValueError):
                        workbook.close()
                        raise

                workbook, sheet, date_col, note_row = _note_cells()
                try:
                    sheet.cell(note_row, date_col, "manual note 1")
                    sheet.cell(note_row + 1, date_col, "manual note 2")
                    sheet.cell(note_row + 2, date_col, "manual note 3")
                    workbook.save(store.workbook_path)
                finally:
                    workbook.close()

                refresh_result = store.refresh_local_database()
                if not refresh_result.get("changed"):
                    raise ValueError("manual Excel note change was not reported")
                if store.get_score_date_notes(target_date) != {"note1": "manual note 1", "note2": "manual note 2", "note3": "manual note 3"}:
                    raise ValueError("manual Excel notes were not imported")

                workbook, sheet, date_col, note_row = _note_cells()
                try:
                    sheet.cell(note_row, date_col, "")
                    sheet.cell(note_row + 1, date_col, "")
                    sheet.cell(note_row + 2, date_col, "")
                    workbook.save(store.workbook_path)
                finally:
                    workbook.close()

                refresh_result = store.refresh_local_database()
                if not refresh_result.get("changed"):
                    raise ValueError("blank Excel note change was not reported")
                if store.get_score_date_notes(target_date) != {"note1": "", "note2": "", "note3": ""}:
                    raise ValueError("blank Excel notes did not clear local notes")
                return target_date
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("excel date notes import", _run)

    def check_excel_refresh_side_effect_guards(self) -> None:
        def _run():
            from openpyxl import load_workbook

            from binance_alpha.constants import NAME_HEADER, SCORE_SHEET
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "excel_refresh_side_effects"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                members = store.get_active_members()
                target_date = store.get_next_score_date()
                first_name = members[0]["name"]
                form_data = {f"score_{member['name']}": "12" for member in members}
                form_data[f"before_{first_name}"] = "100"
                form_data[f"after_{first_name}"] = "90"
                form_data[f"income_{first_name}"] = "33"
                form_data[f"other_expense_{first_name}"] = "2"
                result = store.daily_entry_service.process_submission(members, form_data, target_date)
                if not result.get("ok"):
                    raise ValueError(result.get("error"))
                store.export_to_excel(target_date)

                first_refresh = store.refresh_local_database()
                second_refresh = store.refresh_local_database()
                if first_refresh.get("changed") or second_refresh.get("changed"):
                    raise ValueError("unchanged Excel refresh was reported as changed")
                preserved = next(row for row in store.get_score_rows_for_date(target_date) if row["member_name"] == first_name)
                expected = {
                    "before_balance": "100",
                    "after_balance": "90",
                    "manual_wear": "",
                    "income": "33",
                    "other_expense": "2",
                }
                actual = {key: str(preserved.get(key, "")) for key in expected}
                if actual != expected:
                    raise ValueError(f"visible fields changed during refresh: {actual}")

                def _score_cells():
                    workbook = load_workbook(store.workbook_path)
                    try:
                        sheet = workbook[SCORE_SHEET]
                        date_col = next(
                            col
                            for col in range(1, sheet.max_column + 1)
                            if str(sheet.cell(1, col).value or "") == target_date[5:]
                        )
                        name_col = next(
                            col
                            for col in range(1, sheet.max_column + 1)
                            if str(sheet.cell(1, col).value or "") == NAME_HEADER
                        )
                        first_row = next(
                            row
                            for row in range(2, sheet.max_row + 1)
                            if str(sheet.cell(row, name_col).value or "").strip() == first_name
                        )
                        return workbook, sheet, date_col, name_col, first_row
                    except (KeyError, StopIteration, ValueError):
                        workbook.close()
                        raise

                workbook, sheet, date_col, name_col, first_row = _score_cells()
                try:
                    unknown_row = sheet.max_row + 1
                    sheet.cell(unknown_row, name_col, "not-a-member")
                    sheet.cell(unknown_row, date_col, 99)
                    workbook.save(store.workbook_path)
                finally:
                    workbook.close()
                store.refresh_local_database()
                names = {row["member_name"] for row in store.get_score_rows_for_date(target_date)}
                if "not-a-member" in names:
                    raise ValueError("unknown Excel member was imported")

                workbook, sheet, date_col, name_col, first_row = _score_cells()
                try:
                    sheet.cell(first_row, date_col, 18)
                    workbook.save(store.workbook_path)
                finally:
                    workbook.close()
                changed_refresh = store.refresh_local_database()
                changed_row = next(row for row in store.get_score_rows_for_date(target_date) if row["member_name"] == first_name)
                if not changed_refresh.get("changed") or int(changed_row["score"]) != 18:
                    raise ValueError("real Excel score change was not imported")
                return target_date
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("excel refresh side effect guards", _run)

    def check_excel_refresh_does_not_delete_missing_snapshot_rows(self) -> None:
        def _run():
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "excel_refresh_missing_rows"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                db = store._context.local_db
                members = store.get_active_members()
                old_name = members[0]["name"]
                date_one = "2026-05-01"
                date_two = "2026-05-02"
                date_three = "2026-05-03"
                db.record_score_entries(date_one, [{"name": old_name, "score": "7"}])
                for date_text in (date_one, date_two, date_three):
                    db.record_score_entries(date_text, [{"name": members[1]["name"], "score": "1"}])

                db.replace_score_entries_from_snapshot([
                    {"member_name": members[1]["name"], "score_date": date_one, "score": 1},
                    {"member_name": members[1]["name"], "score_date": date_three, "score": 1},
                ])

                rows = db.get_score_rows(include_deleted=True)
                deleted_by_key = {
                    (row["member_name"], row["score_date"]): int(row.get("deleted", 0) or 0)
                    for row in rows
                }
                if deleted_by_key[(old_name, date_one)]:
                    raise ValueError("missing member row was deleted during refresh")
                if deleted_by_key[(members[1]["name"], date_two)]:
                    raise ValueError("missing date column was deleted during refresh")
                return "missing snapshot rows preserved"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("excel refresh missing rows are preserved", _run)

    def check_cross_year_score_date_metadata(self) -> None:
        def _run():
            from datetime import date, timedelta

            from openpyxl import load_workbook

            from binance_alpha.constants import META_SHEET
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "cross_year_score_meta"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                members = store.get_active_members()
                start = date(2025, 12, 28)
                dates = [(start + timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(9)]
                for offset, target_date in enumerate(dates, start=1):
                    form_data = {f"score_{member['name']}": str(offset) for member in members}
                    result = store.daily_entry_service.process_submission(members, form_data, target_date)
                    if not result.get("ok"):
                        raise ValueError(result.get("error"))
                store.export_to_excel(dates[-1])

                workbook = load_workbook(store.workbook_path)
                try:
                    date_map = store._context.excel_import_service._score_date_map_from_workbook(workbook)
                    actual = [date_map[col] for col in sorted(date_map)]
                    if actual != dates:
                        raise ValueError(f"cross-year score dates mismatch: {actual}")
                finally:
                    workbook.close()

                broken = load_workbook(store.workbook_path)
                try:
                    if META_SHEET in broken.sheetnames:
                        del broken[META_SHEET]
                    broken.save(store.workbook_path)
                finally:
                    broken.close()
                try:
                    store.refresh_local_database()
                except ValueError as exc:
                    if "D1" not in str(exc):
                        raise ValueError(f"unexpected missing-meta error: {exc}") from exc
                    return "cross-year meta ok; missing meta rejected"
                raise ValueError("cross-year score sheet without metadata was accepted")
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("cross-year score date metadata", _run)

    def check_historical_cycle_block_refresh_updates_values(self) -> None:
        def _run():
            from openpyxl import load_workbook

            from binance_alpha.constants import WEAR_NAME_HEADER, WEAR_SHEET, WEAR_TOTAL_HEADER
            from binance_alpha.excel.cycle_block_sheets import iter_blocks
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "historical_cycle_block_refresh"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                members = store.get_active_members()
                member_name = members[0]["name"]
                cycle_id = store.create_settlement_cycle("2026-05-01")
                form_data = {f"score_{member['name']}": "1" for member in members}
                form_data[f"manual_wear_{member_name}"] = "5"
                result = store.daily_entry_service.process_submission(members, form_data, "2026-05-01")
                if not result.get("ok"):
                    raise ValueError(result.get("error"))
                store.settle_and_create_next_cycle(cycle_id, "2026-05-01", {})
                store.export_to_excel("2026-05-02")

                def _edit_historical_wear(value: int) -> None:
                    workbook = load_workbook(store.workbook_path)
                    try:
                        sheet = workbook[WEAR_SHEET]
                        block = next(
                            block for block in iter_blocks(sheet, WEAR_NAME_HEADER, WEAR_TOTAL_HEADER)
                            if block["start_iso"] == "2026-05-01"
                        )
                        date_col = next(col for col, date_text in block["date_by_col"].items() if date_text == "2026-05-01")
                        member_row = next(
                            row for row in block["data_rows"]
                            if str(sheet.cell(row, block["name_col"]).value or "").strip() == member_name
                        )
                        sheet.cell(member_row, date_col, value)
                        workbook.save(store.workbook_path)
                    finally:
                        workbook.close()

                _edit_historical_wear(7)
                store.refresh_local_database()
                row = next(row for row in store.get_score_rows_for_date("2026-05-01") if row["member_name"] == member_name)
                if float(row["manual_wear"]) != 7.0:
                    raise ValueError(f"historical wear edit not imported: {row['manual_wear']}")

                _edit_historical_wear(0)
                store.refresh_local_database()
                row = next(row for row in store.get_score_rows_for_date("2026-05-01") if row["member_name"] == member_name)
                if float(row["manual_wear"]) != 0.0:
                    raise ValueError(f"historical wear zero edit not imported: {row['manual_wear']}")
                return "historical block edits imported"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("historical cycle block refresh updates values", _run)

    def check_deleted_score_column_does_not_delete_database_rows(self) -> None:
        def _run():
            from openpyxl import load_workbook

            from binance_alpha.constants import SCORE_SHEET
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "deleted_score_column"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                members = store.get_active_members()
                target_date = "2026-05-02"
                for date_text in ("2026-05-01", target_date, "2026-05-03"):
                    form_data = {f"score_{member['name']}": "5" for member in members}
                    result = store.daily_entry_service.process_submission(members, form_data, date_text)
                    if not result.get("ok"):
                        raise ValueError(result.get("error"))
                store.export_to_excel("2026-05-03")
                before_count = len(store.get_score_rows_for_date(target_date))

                workbook = load_workbook(store.workbook_path)
                try:
                    sheet = workbook[SCORE_SHEET]
                    target_col = next(
                        col for col in range(1, sheet.max_column + 1)
                        if str(sheet.cell(1, col).value or "") == target_date[5:]
                    )
                    sheet.delete_cols(target_col, 1)
                    workbook.save(store.workbook_path)
                finally:
                    workbook.close()

                try:
                    store.refresh_local_database()
                except ValueError:
                    pass
                after_count = len(store.get_score_rows_for_date(target_date))
                if after_count != before_count:
                    raise ValueError("deleted Excel score column deleted database rows")
                return "deleted score column caused no data loss"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("deleted score column does not delete database rows", _run)

    def check_member_rename_refresh_preserves_old_rows(self) -> None:
        def _run():
            from binance_alpha.constants import DISABLED
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "member_rename_refresh"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                members = store.get_active_members()
                old_name = members[0]["name"]
                new_name = f"{old_name}新"
                target_date = "2026-05-01"
                store._context.local_db.record_score_entries(target_date, [{"name": old_name, "score": "9"}])
                store.member_service.update_member(old_name, "", status=DISABLED)
                store.member_service.add_member(new_name)
                store.export_to_excel(target_date)
                store.refresh_local_database()
                rows = store._context.local_db.get_score_rows_for_date(target_date, include_deleted=True)
                old_row = next(row for row in rows if row["member_name"] == old_name)
                if int(old_row.get("deleted", 0) or 0):
                    raise ValueError("disabled old-name row was deleted by refresh")
                return f"{old_name}->{new_name}"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("member rename refresh preserves old rows", _run)

    def check_member_status_survives_excel_refresh(self) -> None:
        def _run():
            from openpyxl import load_workbook

            from binance_alpha.constants import DISABLED, NAME_HEADER, SCORE_SHEET
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "member_status_refresh"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                member_name = store.get_active_members()[0]["name"]
                store.member_service.update_member(member_name, "", status=DISABLED)
                if any(row["name"] == member_name for row in store.get_active_members()):
                    raise ValueError("member is still active after disable")
                workbook = load_workbook(store.workbook_path)
                try:
                    sheet = workbook[SCORE_SHEET]
                    name_col = next(
                        col
                        for col in range(1, sheet.max_column + 1)
                        if str(sheet.cell(1, col).value or "") == NAME_HEADER
                    )
                    member_row = next(
                        row
                        for row in range(2, sheet.max_row + 1)
                        if str(sheet.cell(row, name_col).value or "").strip() == member_name
                    )
                    if not bool(sheet.row_dimensions[member_row].hidden):
                        raise ValueError("disabled member row is still visible in Excel")
                    sheet.row_dimensions[member_row].hidden = False
                    workbook.save(store.workbook_path)
                finally:
                    workbook.close()

                store = StoreApplication(temp_dir)
                workbook = load_workbook(store.workbook_path)
                try:
                    sheet = workbook[SCORE_SHEET]
                    name_col = next(
                        col
                        for col in range(1, sheet.max_column + 1)
                        if str(sheet.cell(1, col).value or "") == NAME_HEADER
                    )
                    member_row = next(
                        row
                        for row in range(2, sheet.max_row + 1)
                        if str(sheet.cell(row, name_col).value or "").strip() == member_name
                    )
                    if not bool(sheet.row_dimensions[member_row].hidden):
                        raise ValueError("startup did not restore disabled member row hiding")
                finally:
                    workbook.close()

                if any(
                    any(str(cell.get("value", "")).strip() == member_name for cell in row)
                    for row in store.get_wear_sheet_view()["rows"]
                ):
                    raise ValueError("disabled member is still visible in wear preview")

                store.refresh_local_database()
                after = next(row for row in store.get_members() if row["name"] == member_name)
                if after["status"] != DISABLED:
                    raise ValueError("Excel refresh restored disabled member")
                if any(row["name"] == member_name for row in store.get_active_members()):
                    raise ValueError("disabled member returned to active members")
                return member_name
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("member disable survives excel refresh", _run)

    def check_existing_config_members_are_not_reseeded(self) -> None:
        def _run():
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "config_members_no_reseed"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                config_path = temp_dir / "system_config.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "members": [
                                {
                                    "name": "only-one",
                                    "status": "启用",
                                    "note": "",
                                    "created_at": "2026-05-12 00:00:00",
                                    "disabled_at": "",
                                    "sort_order": 1,
                                }
                            ],
                            "quick_scores": [17],
                            "wear_abnormal_threshold": 2.5,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                store = StoreApplication(temp_dir)
                names = [member["name"] for member in store.get_members()]
                if names != ["only-one"]:
                    raise ValueError(f"default members were reseeded: {names[:5]}")
                return names[0]
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("existing config members are not reseeded", _run)

    def check_supabase_profit_uses_formal_column(self) -> None:
        def _run():
            from binance_alpha.constants import ENABLED
            from binance_alpha.local_database import LocalDatabase

            class FakeSupabase:
                def __init__(self) -> None:
                    self.score_rows: list[dict] = []

                def ensure_score_entries_schema(self) -> None:
                    return None

                def push_members(self, rows):
                    return len(rows)

                def push_score_entries(self, rows):
                    self.score_rows = rows
                    return len(rows)

                def push_settlement_cycles(self, rows):
                    return len(rows)

                def push_settlement_entries(self, rows):
                    return len(rows)

            temp_dir = TEST_TEMP_ROOT / "supabase_profit_column"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                db = LocalDatabase(temp_dir)
                db.sync_members(
                    [
                        {
                            "name": "profit-user",
                            "status": ENABLED,
                            "note": "",
                            "created_at": "2026-05-12 00:00:00",
                            "disabled_at": "",
                            "sort_order": 1,
                        }
                    ]
                )
                db.record_score_entries("2026-05-12", [{"name": "profit-user", "score": "12"}])
                fake = FakeSupabase()
                db.push_to_supabase(fake, force_full=False, score_profit_map={"profit-user": 12.34})
                if not fake.score_rows:
                    raise ValueError("no score rows were pushed")
                row = fake.score_rows[0]
                if row.get("profit") != "12.3":
                    raise ValueError(f"profit column not set: {row}")
                if "profit=" in str(row.get("source", "")):
                    raise ValueError("profit leaked into source")
                return row["profit"]
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("supabase profit formal column", _run)

    def check_online_entry_reports_supabase_request_failure(self) -> None:
        def _run():
            import httpx

            from binance_alpha.services.supabase_entry_writer import SupabaseEntryWriter

            class FailingSupabase:
                def is_configured(self) -> bool:
                    return True

                def fetch_score_entries_by_ids(self, ids):
                    raise httpx.TimeoutException("forced timeout")

            writer = SupabaseEntryWriter(FailingSupabase())
            try:
                writer.save_scores_and_wear(
                    "2026-05-12",
                    [{"name": "online-user", "score": "12"}],
                )
            except ValueError as exc:
                if "线上数据库暂时连接失败" not in str(exc):
                    raise ValueError(f"unexpected error text: {exc}") from exc
                return "request failure reported"
            raise ValueError("supabase request failure was not reported")

        self.check("online entry request failure", _run)

    def check_wear_threshold_uses_saved_config(self) -> None:
        def _run():
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "wear_threshold_config"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                threshold = store.set_wear_abnormal_threshold("1.5")
                if threshold != 1.5:
                    raise ValueError("threshold return value is wrong")
                view = store.get_wear_sheet_view()
                if float(view["abnormal_threshold"]) != 1.5:
                    raise ValueError("wear sheet did not read saved threshold")
                return threshold
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("wear threshold saved config", _run)

    def check_wear_daily_member_average_row(self) -> None:
        def _run():
            from openpyxl import load_workbook

            from binance_alpha.constants import WEAR_NAME_HEADER, WEAR_SHEET, WEAR_TOTAL_HEADER
            from binance_alpha.excel.value_normalizer import normalize_wear
            from binance_alpha.services.store_application import StoreApplication
            from binance_alpha.ui_text import UI_TEXT

            temp_dir = TEST_TEMP_ROOT / "wear_daily_member_average"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                members = store.get_active_members()
                target_date = store.get_next_score_date()
                wear_values = [float(index + 1) for index, _ in enumerate(members)]
                form_data = {
                    f"manual_wear_{member['name']}": str(wear_values[index])
                    for index, member in enumerate(members)
                }
                result = store.daily_entry_service.process_submission(members, form_data, target_date)
                if not result.get("ok"):
                    raise ValueError(result.get("error"))
                store.export_to_excel(target_date)

                expected_average = normalize_wear(sum(wear_values) / len(wear_values))
                wear_date_header = target_date[5:].replace("-", "")
                view = store.get_wear_sheet_view()
                label = UI_TEXT["wear_daily_member_avg"]
                date_index = view["headers"].index(wear_date_header)
                name_index = view["headers"].index(WEAR_NAME_HEADER)
                total_index = view["headers"].index(WEAR_TOTAL_HEADER)
                summary_row = view["daily_average_row"]
                if summary_row[name_index]["value"] != label:
                    raise ValueError("wear average row label missing from page view")
                if summary_row[date_index]["value"] != expected_average:
                    raise ValueError("page view wear daily average is wrong")
                if summary_row[total_index]["value"] < expected_average:
                    raise ValueError("page view wear daily average total is wrong")

                workbook = load_workbook(store.workbook_path)
                try:
                    sheet = workbook[WEAR_SHEET]
                    headers = [sheet.cell(1, col).value for col in range(1, sheet.max_column + 1)]
                    date_col = headers.index(wear_date_header) + 1
                    name_col = headers.index(WEAR_NAME_HEADER) + 1
                    summary_rows = [
                        row
                        for row in range(2, sheet.max_row + 1)
                        if str(sheet.cell(row, name_col).value or "").strip() == label
                    ]
                    if summary_rows != [sheet.max_row]:
                        raise ValueError("Excel wear average row is not at the bottom")
                    if sheet.cell(summary_rows[0], date_col).value != expected_average:
                        raise ValueError("Excel wear daily average is wrong")
                finally:
                    workbook.close()
                return expected_average
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("wear daily member average row", _run)

    def check_daily_entry_rejects_incomplete_balance_pair(self) -> None:
        def _run():
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "daily_incomplete_balance"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                members = store.get_active_members()
                first_name = members[0]["name"]
                target_date = "2026-05-02"
                form_data = {f"score_{member['name']}": "1" for member in members}
                form_data[f"before_{first_name}"] = "10"
                result = store.daily_entry_service.process_submission(members, form_data, target_date)
                if result.get("ok"):
                    raise ValueError("incomplete balance pair was accepted")
                if first_name not in str(result.get("error", "")):
                    raise ValueError(f"error did not name member: {result.get('error')}")
                if store.get_score_rows_for_date(target_date):
                    raise ValueError("rejected incomplete entry was saved")
                return result.get("error")
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("daily entry incomplete balance rejected", _run)

    def check_invalid_save_date_is_rejected(self) -> None:
        def _run():
            from binance_alpha.services.sqlite_entry_writer import SQLiteEntryWriter

            class FakeLocalDb:
                def record_score_entries(self, saved_date, entries, *, source="local"):
                    raise ValueError("writer should reject date before saving")

            writer = SQLiteEntryWriter(FakeLocalDb())
            try:
                writer.save_scores_and_wear("2026-99-99", [{"name": "a", "score": "1"}])
            except ValueError as exc:
                if "日期" not in str(exc):
                    raise ValueError(f"unexpected error: {exc}") from exc
                return "invalid date rejected"
            raise ValueError("invalid save date was accepted")

        self.check("invalid save date rejected", _run)

    def check_cycle_block_dates_and_reserved_member_names(self) -> None:
        def _run():
            from openpyxl import Workbook

            from binance_alpha.constants import NAME_HEADER, RESERVED_MEMBER_NAMES
            from binance_alpha.excel.cycle_block_sheets import CYCLE_BANNER_PREFIX, build_cycle_blocks, iter_blocks
            from binance_alpha.services.store_application import StoreApplication

            workbook = Workbook()
            sheet = workbook.active
            sheet.cell(1, 1, f"{CYCLE_BANNER_PREFIX} test 2023-12-25 ~ 2024-03-15")
            sheet.cell(2, 1, "0229")
            sheet.cell(2, 2, NAME_HEADER)
            sheet.cell(3, 1, 5)
            sheet.cell(3, 2, RESERVED_MEMBER_NAMES[0])
            blocks = iter_blocks(sheet, NAME_HEADER, None)
            if blocks[0]["date_by_col"].get(1) != "2024-02-29":
                raise ValueError("leap-year 0229 did not resolve inside cycle window")
            if blocks[0]["data_rows"] != [3]:
                raise ValueError("member name colliding with summary label was skipped")

            bad_workbook = Workbook()
            bad_sheet = bad_workbook.active
            bad_sheet.cell(1, 1, f"{CYCLE_BANNER_PREFIX} test 2025-12-25 ~ 2026-03-15")
            bad_sheet.cell(2, 1, "0229")
            bad_sheet.cell(2, 2, NAME_HEADER)
            bad_sheet.cell(3, 1, 5)
            bad_sheet.cell(3, 2, "alice")
            try:
                iter_blocks(bad_sheet, NAME_HEADER, None)
            except ValueError as exc:
                if "0229" not in str(exc):
                    raise ValueError(f"unexpected invalid date error: {exc}") from exc
            else:
                raise ValueError("invalid 0229 column was silently accepted")

            temp_dir = TEST_TEMP_ROOT / "reserved_member_names"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                try:
                    store.member_service.add_member(RESERVED_MEMBER_NAMES[0])
                except ValueError:
                    pass
                else:
                    raise ValueError("reserved global member name was accepted")
                cycle_id = store.create_settlement_cycle("2026-05-01")
                try:
                    store.add_cycle_member(cycle_id, RESERVED_MEMBER_NAMES[1])
                except ValueError:
                    pass
                else:
                    raise ValueError("reserved cycle member name was accepted")

                class FakeLocalDb:
                    def get_settlement_cycles(self):
                        return [{"id": "cycle", "start_date": "2026-05-01", "settle_date": "2026-05-01", "settled": 1}]

                    def get_score_rows(self):
                        return [
                            {
                                "member_name": "alice",
                                "score_date": "2026-05-01",
                                "income": "0",
                                "manual_wear": "",
                                "before_balance": "",
                                "after_balance": "",
                                "other_expense": "",
                            }
                        ]

                block = build_cycle_blocks(local_db=FakeLocalDb(), sheet_type="income", member_order=["alice"])[0]
                if block["member_values"]["alice"].get("2026-05-01") != 0.0:
                    raise ValueError("explicit zero value was hidden from cycle block")
                return "cycle dates and reserved names ok"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("cycle block dates and reserved member names", _run)

    def check_excel_refresh_rejects_unknown_score_date(self) -> None:
        def _run():
            from openpyxl import load_workbook

            from binance_alpha.constants import NAME_HEADER, SCORE_SHEET
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "excel_unknown_score_date"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                members = store.get_active_members()
                target_date = store.get_next_score_date()
                form_data = {f"score_{member['name']}": "11" for member in members}
                result = store.daily_entry_service.process_submission(members, form_data, target_date)
                if not result.get("ok"):
                    raise ValueError(result.get("error"))
                store.export_to_excel(target_date)

                workbook = load_workbook(store.workbook_path)
                try:
                    sheet = workbook[SCORE_SHEET]
                    name_col = next(
                        col
                        for col in range(1, sheet.max_column + 1)
                        if str(sheet.cell(1, col).value or "") == NAME_HEADER
                    )
                    first_row = next(
                        row
                        for row in range(2, sheet.max_row + 1)
                        if str(sheet.cell(row, name_col).value or "").strip() == members[0]["name"]
                    )
                    sheet.cell(1, 1, "not-a-date")
                    sheet.cell(first_row, 1, 11)
                    workbook.save(store.workbook_path)
                finally:
                    workbook.close()

                try:
                    store.refresh_local_database()
                except ValueError as exc:
                    if "D1" not in str(exc):
                        raise ValueError(f"unexpected error text: {exc}") from exc
                    return "D1 rejected"
                raise ValueError("invalid score date column was accepted")
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("excel unknown score date rejected", _run)

    def check_excel_refresh_rejects_missing_score_date_meta(self) -> None:
        def _run():
            from openpyxl import load_workbook

            from binance_alpha.constants import META_SHEET
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "excel_missing_score_meta"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                members = store.get_active_members()
                target_date = "2025-12-28"
                form_data = {f"score_{member['name']}": "3" for member in members}
                result = store.daily_entry_service.process_submission(members, form_data, target_date)
                if not result.get("ok"):
                    raise ValueError(result.get("error"))
                store.export_to_excel(target_date)

                workbook = load_workbook(store.workbook_path)
                try:
                    if META_SHEET in workbook.sheetnames:
                        del workbook[META_SHEET]
                    workbook.save(store.workbook_path)
                finally:
                    workbook.close()

                try:
                    store.refresh_local_database()
                except ValueError as exc:
                    if "D1" not in str(exc):
                        raise ValueError(f"unexpected error text: {exc}") from exc
                    return "missing score metadata rejected"
                raise ValueError("score date without metadata was accepted")
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("excel missing score date metadata rejected", _run)

    def check_delete_score_date(self) -> None:
        def _run():
            from openpyxl import load_workbook

            from binance_alpha.constants import EXPENSE_SHEET, INCOME_SHEET, SCORE_SHEET, WEAR_SHEET

            store = self.build_store()
            target_date = "2026-05-02"
            active_members = store.get_active_members()
            first_name = active_members[0]["name"]
            form_data = {f"score_{member['name']}": "1" for member in active_members}
            form_data[f"manual_wear_{first_name}"] = "9"
            form_data[f"income_{first_name}"] = "30"
            form_data[f"other_expense_{first_name}"] = "4"
            result = store.daily_entry_service.process_submission(active_members, form_data, target_date)
            if not result.get("ok"):
                raise ValueError(result.get("error"))
            store.export_to_excel(target_date)
            deleted = store.delete_score_date(target_date)
            if not deleted.get("deleted_rows"):
                raise ValueError("no rows deleted")
            if store.get_score_rows_for_date(target_date):
                raise ValueError("database rows still visible")

            workbook = load_workbook(store.workbook_path)
            try:
                score_headers = [
                    str(workbook[SCORE_SHEET].cell(1, col).value or "")
                    for col in range(1, workbook[SCORE_SHEET].max_column + 1)
                ]
                if "05-02" in score_headers:
                    raise ValueError("score date still in workbook")
                for sheet_name in (WEAR_SHEET, INCOME_SHEET, EXPENSE_SHEET):
                    headers = [
                        str(workbook[sheet_name].cell(1, col).value or "").zfill(4)
                        for col in range(1, workbook[sheet_name].max_column + 1)
                    ]
                    if "0502" in headers:
                        raise ValueError(f"{sheet_name} date still in workbook")
            finally:
                workbook.close()
            return f"deleted_rows={deleted['deleted_rows']}"

        self.check("delete score date", _run)

    def check_delete_score_date_rolls_back_on_export_error(self) -> None:
        def _run():
            from binance_alpha.services.store_application import StoreApplication

            class FailingExportService:
                def export_to_excel(self, date_text: str | None = None) -> int:
                    raise ValueError("forced export failure")

            temp_dir = TEST_TEMP_ROOT / "delete_date_rollback"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                target_date = store.get_next_score_date()
                active_members = store.get_active_members()
                form_data = {f"score_{member['name']}": "7" for member in active_members}
                form_data["date_note_1"] = "rollback note"
                result = store.daily_entry_service.process_submission(active_members, form_data, target_date)
                if not result.get("ok"):
                    raise ValueError(result.get("error"))
                store.export_to_excel(target_date)

                original_export_service = store._context.export_service
                store._context.export_service = FailingExportService()
                try:
                    failed_as_expected = False
                    try:
                        store.delete_score_date(target_date)
                    except ValueError:
                        failed_as_expected = True
                    if not failed_as_expected:
                        raise ValueError("delete did not fail")
                finally:
                    store._context.export_service = original_export_service

                if not store.get_score_rows_for_date(target_date):
                    raise ValueError("score rows were not restored")
                if store.get_score_date_notes(target_date).get("note1") != "rollback note":
                    raise ValueError("date note was not restored")
                return target_date
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("delete score date rollback on export error", _run)

    def check_empty_delete_score_date_uses_info_flash(self) -> None:
        def _run():
            from flask import Flask

            from binance_alpha.services.store_application import StoreApplication
            from binance_alpha.web import register_routes

            temp_dir = TEST_TEMP_ROOT / "delete_date_empty_flash"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                app = Flask(
                    __name__,
                    template_folder=str(PROJECT_ROOT / "templates"),
                    static_folder=str(PROJECT_ROOT / "static"),
                )
                app.secret_key = "smoke"
                register_routes(app, store)
                client = app.test_client()
                response = client.post("/scores/delete-date", data={"date": "2026-05-13"})
                if response.status_code != 302:
                    raise ValueError(f"unexpected status: {response.status_code}")
                with client.session_transaction() as session:
                    flashes = session.get("_flashes", [])
                if not flashes or flashes[-1][0] != "info":
                    raise ValueError(f"empty delete did not flash info: {flashes}")
                return "empty delete info flash"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("empty delete score date uses info flash", _run)

    def check_supabase_push_saves_posted_entry_once(self) -> None:
        def _run():
            from flask import Flask

            from binance_alpha.services.store_application import StoreApplication
            from binance_alpha.web import register_routes

            class FakeSyncService:
                def __init__(self) -> None:
                    self.push_calls = 0

                def is_configured(self) -> bool:
                    return True

                def push(self, *, force_full: bool = True, score_profit_map=None):
                    self.push_calls += 1
                    return {"members": 0, "score_entries": 1, "score_entries_deleted": 0}

                def pull(self):
                    return {"members": 0, "score_entries": 0}

            temp_dir = TEST_TEMP_ROOT / "supabase_push_saves_posted_entry"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                fake_sync = FakeSyncService()
                store._context.sync_service = fake_sync
                app = Flask(
                    __name__,
                    template_folder=str(PROJECT_ROOT / "templates"),
                    static_folder=str(PROJECT_ROOT / "static"),
                )
                app.secret_key = "smoke"
                register_routes(app, store)
                client = app.test_client()

                target_date = store.get_next_score_date()
                first_member = store.get_active_members()[0]["name"]
                response = client.post(
                    "/supabase-push",
                    data={
                        "date": target_date,
                        f"score_{first_member}": "13",
                        "date_note_1": "push note",
                    },
                )
                if response.status_code != 302:
                    raise ValueError(f"unexpected status: {response.status_code}")
                if fake_sync.push_calls != 1:
                    raise ValueError("push was not called once")
                saved_rows = store.get_score_rows_for_date(target_date)
                saved_row = next(row for row in saved_rows if row["member_name"] == first_member)
                if int(saved_row["score"]) != 13:
                    raise ValueError("posted score was not saved before push")
                if store.get_score_date_notes(target_date).get("note1") != "push note":
                    raise ValueError("posted note was not saved before push")
                return target_date
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("supabase push saves posted entry once", _run)

    def check_online_manual_wear_syncs_to_local(self) -> None:
        def _run():
            from binance_alpha.entry_helpers import member_id, score_entry_id
            from binance_alpha.local_database import LocalDatabase
            from binance_alpha.services.supabase_entry_writer import SupabaseEntryWriter

            class FakeSupabase:
                def __init__(self) -> None:
                    self.rows = []

                def is_configured(self) -> bool:
                    return True

                def fetch_score_entries_by_ids(self, ids):
                    return []

                def push_score_entries(self, rows):
                    self.rows.extend(rows)
                    return len(rows)

            class FakePullSupabase:
                def __init__(self, row) -> None:
                    self.row = row

                def pull_members(self, since_updated_at=None):
                    return [
                        {
                            "id": member_id("online-user"),
                            "name": "online-user",
                            "status": "启用",
                            "note": "",
                            "sort_order": 1,
                            "created_at": "2026-05-13 00:00:00",
                            "disabled_at": "",
                            "updated_at": self.row["updated_at"],
                            "version": 1,
                            "deleted": 0,
                        }
                    ]

                def pull_score_entries(self, since_updated_at=None):
                    return [self.row]

                def pull_settlement_cycles(self, since_updated_at=None):
                    return []

                def pull_settlement_entries(self, since_updated_at=None):
                    return []

            fake = FakeSupabase()
            writer = SupabaseEntryWriter(fake)
            writer.save_scores_and_wear(
                "2026-05-13",
                [{"name": "online-user", "score": "8", "manual_wear": "3", "income": "", "other_expense": ""}],
            )
            if not fake.rows:
                raise ValueError("online save did not push a row")
            pushed_row = fake.rows[0]
            if pushed_row["manual_wear"] != "3":
                raise ValueError("online manual wear was not saved")
            if pushed_row["id"] != score_entry_id("online-user", "2026-05-13"):
                raise ValueError("unexpected online row id")

            temp_dir = TEST_TEMP_ROOT / "online_manual_wear_sync"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                db = LocalDatabase(temp_dir)
                db.pull_from_supabase(FakePullSupabase(pushed_row))
                saved = db.get_score_rows_for_date("2026-05-13")
                if not saved:
                    raise ValueError("online row was not pulled into local database")
                if str(saved[0].get("manual_wear", "")) != "3":
                    raise ValueError("manual wear did not sync to local database")
                return saved[0]["manual_wear"]
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("online manual wear syncs to local", _run)

    def check_blank_online_detail_fields_do_not_churn(self) -> None:
        def _run():
            from binance_alpha.local_database import LocalDatabase

            temp_dir = TEST_TEMP_ROOT / "blank_online_detail_churn"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                db = LocalDatabase(temp_dir)
                db.record_score_entries("2026-05-13", [{"name": "online-user", "score": "8"}], source="online")
                before = db.get_score_rows_for_date("2026-05-13")[0]
                db.record_score_entries("2026-05-13", [{"name": "online-user", "score": "8"}], source="local")
                after = db.get_score_rows_for_date("2026-05-13")[0]
                for key in ("manual_wear", "income", "other_expense"):
                    if after[key] != "":
                        raise ValueError(f"blank {key} became {after[key]!r}")
                if after["version"] != before["version"]:
                    raise ValueError("unchanged blank details incremented version")

                db.record_score_entries(
                    "2026-05-13",
                    [{"name": "online-user", "score": "8", "income": "0"}],
                    source="local",
                )
                explicit_zero = db.get_score_rows_for_date("2026-05-13")[0]
                if explicit_zero["income"] != "0":
                    raise ValueError("explicit zero income was not saved")
                return "blank details stable; explicit zero saved"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("blank online detail fields do not churn", _run)

    def check_supabase_remote_reentry_restores_local_tombstone(self) -> None:
        def _run():
            from binance_alpha.entry_helpers import member_id, score_entry_id
            from binance_alpha.local_database import LocalDatabase

            class FakePullSupabase:
                def __init__(self, row) -> None:
                    self.row = row

                def pull_members(self, since_updated_at=None):
                    return []

                def pull_score_entries(self, since_updated_at=None):
                    return [self.row]

                def pull_settlement_cycles(self, since_updated_at=None):
                    return []

                def pull_settlement_entries(self, since_updated_at=None):
                    return []

            temp_dir = TEST_TEMP_ROOT / "supabase_remote_reentry"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                db = LocalDatabase(temp_dir)
                db.record_score_entries("2026-05-13", [{"name": "online-user", "score": "1"}])
                db.delete_score_entries_for_date("2026-05-13")
                remote_row = {
                    "id": score_entry_id("online-user", "2026-05-13"),
                    "member_id": member_id("online-user"),
                    "member_name": "online-user",
                    "score_date": "2026-05-13",
                    "score": 8,
                    "before_balance": "",
                    "after_balance": "",
                    "manual_wear": "3",
                    "income": "",
                    "other_expense": "",
                    "profit": "0",
                    "updated_at": "2026-05-13 12:00:00",
                    "version": 99,
                    "deleted": 0,
                    "source": "online",
                }
                db.pull_from_supabase(FakePullSupabase(remote_row), force_full=True)
                saved = db.get_score_rows_for_date("2026-05-13")
                if not saved:
                    raise ValueError("remote re-entry did not restore local row")
                if int(saved[0]["score"]) != 8 or str(saved[0]["manual_wear"]) != "3":
                    raise ValueError("restored row did not use remote data")
                return saved[0]["score"]
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("supabase remote re-entry restores local tombstone", _run)

    def check_supabase_pull_overlap_and_force_full(self) -> None:
        def _run():
            from binance_alpha.local_database import LocalDatabase

            class CapturingSupabase:
                def __init__(self) -> None:
                    self.calls: list[str | None] = []

                def pull_members(self, since_updated_at=None):
                    self.calls.append(since_updated_at)
                    return []

                def pull_score_entries(self, since_updated_at=None):
                    self.calls.append(since_updated_at)
                    return []

                def pull_settlement_cycles(self, since_updated_at=None):
                    self.calls.append(since_updated_at)
                    return []

                def pull_settlement_entries(self, since_updated_at=None):
                    self.calls.append(since_updated_at)
                    return []

            temp_dir = TEST_TEMP_ROOT / "supabase_pull_overlap"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                db = LocalDatabase(temp_dir)
                db._set_sync_state("supabase_last_pull", "2026-05-13 10:00:00")
                incremental = CapturingSupabase()
                db.pull_from_supabase(incremental)
                if set(incremental.calls) != {"2026-05-13 09:00:00"}:
                    raise ValueError(f"pull overlap not applied: {incremental.calls}")

                full = CapturingSupabase()
                db.pull_from_supabase(full, force_full=True)
                if any(call is not None for call in full.calls):
                    raise ValueError(f"force_full pull used incremental since: {full.calls}")
                return "overlap and full pull ok"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("supabase pull overlap and force full", _run)

    def check_supabase_clock_skew_row_is_pulled(self) -> None:
        def _run():
            from binance_alpha.entry_helpers import member_id, score_entry_id
            from binance_alpha.local_database import LocalDatabase

            class FilteringSupabase:
                def __init__(self, row) -> None:
                    self.row = row

                def pull_members(self, since_updated_at=None):
                    return []

                def pull_score_entries(self, since_updated_at=None):
                    if since_updated_at is None or self.row["updated_at"] > since_updated_at:
                        return [self.row]
                    return []

                def pull_settlement_cycles(self, since_updated_at=None):
                    return []

                def pull_settlement_entries(self, since_updated_at=None):
                    return []

            temp_dir = TEST_TEMP_ROOT / "supabase_clock_skew"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                db = LocalDatabase(temp_dir)
                db._set_sync_state("supabase_last_pull", "2026-05-13 10:00:00")
                row = {
                    "id": score_entry_id("mobile-user", "2026-05-13"),
                    "member_id": member_id("mobile-user"),
                    "member_name": "mobile-user",
                    "score_date": "2026-05-13",
                    "score": 8,
                    "before_balance": "",
                    "after_balance": "",
                    "manual_wear": "",
                    "income": "",
                    "other_expense": "",
                    "profit": "0",
                    "updated_at": "2026-05-13 09:55:00",
                    "version": 2,
                    "deleted": 0,
                    "source": "online",
                }
                db.pull_from_supabase(FilteringSupabase(row))
                saved = db.get_score_rows_for_date("2026-05-13")
                if not saved or saved[0]["member_name"] != "mobile-user":
                    raise ValueError("clock-skewed row was missed by incremental pull")
                return saved[0]["updated_at"]
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("supabase clock skew row is pulled", _run)

    def check_supabase_delete_push_reinsert_pull_roundtrip(self) -> None:
        def _run():
            from binance_alpha.entry_helpers import score_entry_id
            from binance_alpha.local_database import LocalDatabase

            class MemorySupabase:
                def __init__(self) -> None:
                    self.members = {}
                    self.scores = {}

                def ensure_score_entries_schema(self):
                    return None

                def push_members(self, rows):
                    for row in rows:
                        self.members[row["id"]] = dict(row)
                    return len(rows)

                def push_score_entries(self, rows):
                    for row in rows:
                        self.scores[row["id"]] = dict(row)
                    return len(rows)

                def push_settlement_cycles(self, rows):
                    return len(rows)

                def push_settlement_entries(self, rows):
                    return len(rows)

                def pull_score_entries(self, since_updated_at=None):
                    return list(self.scores.values())

                def pull_members(self, since_updated_at=None):
                    return list(self.members.values())

                def pull_settlement_cycles(self, since_updated_at=None):
                    return []

                def pull_settlement_entries(self, since_updated_at=None):
                    return []

                def mark_score_entries_deleted(self, rows):
                    for row in rows:
                        self.scores[row["id"]] = dict(row)
                    return len(rows)

            temp_dir = TEST_TEMP_ROOT / "supabase_delete_reinsert"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                db = LocalDatabase(temp_dir)
                remote = MemorySupabase()
                member_name = "mobile-user"
                target_date = "2026-05-13"
                entry_id = score_entry_id(member_name, target_date)
                db.record_score_entries(target_date, [{"name": member_name, "score": "1"}])
                db.push_to_supabase(remote, force_full=True)
                db.delete_score_entries_for_date(target_date)
                db.push_to_supabase(remote, force_full=True)
                if int(remote.scores[entry_id]["deleted"]) != 1:
                    raise ValueError("local delete was not pushed as tombstone")

                remote_row = dict(remote.scores[entry_id])
                remote_row.update({
                    "score": 9,
                    "manual_wear": "3",
                    "updated_at": "2026-05-13 12:00:00",
                    "version": int(remote_row["version"]) + 1,
                    "deleted": 0,
                    "source": "online",
                })
                remote.scores[entry_id] = remote_row
                db.pull_from_supabase(remote, force_full=True)
                saved = db.get_score_rows_for_date(target_date)
                if not saved or int(saved[0]["score"]) != 9 or saved[0]["manual_wear"] != "3":
                    raise ValueError("mobile reinsert did not restore local row")
                return "delete push reinsert pull ok"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("supabase delete push reinsert pull roundtrip", _run)

    def check_supabase_env_file_reloads(self) -> None:
        def _run():
            import time

            from binance_alpha.repositories.supabase_client import SupabaseClient

            temp_dir = TEST_TEMP_ROOT / "supabase_env_reload"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                client = SupabaseClient(temp_dir)
                if client.is_configured():
                    raise ValueError("empty env dir should not be configured")
                env_path = temp_dir / ".env.local"
                env_path.write_text(
                    "SUPABASE_URL=https://example.supabase.co\n"
                    "SUPABASE_SERVICE_ROLE_KEY=service-role-key\n",
                    encoding="utf-8",
                )
                time.sleep(0.02)
                if not client.is_configured():
                    raise ValueError("updated .env.local was not reloaded")
                return "env reloaded"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("supabase env file reloads", _run)

    def check_cycle_extra_member_reactivation_normalizes_flag(self) -> None:
        def _run():
            from binance_alpha.constants import DISABLED, ENABLED
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "cycle_extra_reactivation"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                member_name = store.get_active_members()[0]["name"]
                cycle_id = store.create_settlement_cycle("2026-05-01")
                store.member_service.update_member(member_name, "", status=DISABLED)
                store.add_cycle_member(cycle_id, member_name)
                extra_entry = store._context.local_db.get_settlement_entries(cycle_id)[member_name]
                if int(extra_entry.get("is_extra", 0) or 0) != 1:
                    raise ValueError("disabled member was not added as cycle extra")

                store.member_service.update_member(member_name, "", status=ENABLED)
                store.save_cycle_settlement(cycle_id, {})
                restored_entry = store._context.local_db.get_settlement_entries(cycle_id)[member_name]
                if int(restored_entry.get("is_extra", 0) or 0) != 0:
                    raise ValueError("reactivated member kept stale extra flag")
                row = next(
                    row for row in store.get_cycle_profit_view(cycle_id)["rows"]
                    if row["member_name"] == member_name
                )
                if row["is_extra"]:
                    raise ValueError("reactivated member is still displayed as extra")
                return member_name
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("cycle extra member reactivation normalizes flag", _run)

    def check_cycle_extra_member_carries_to_next_until_removed(self) -> None:
        def _run():
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "cycle_extra_carry_next"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                extra_name = "carry-extra"
                cycle_id = store.create_settlement_cycle("2026-05-01")
                store.add_cycle_member(cycle_id, extra_name)

                next_cycle_id = store.settle_and_create_next_cycle(cycle_id, "2026-05-01", {})
                next_entries = store._context.local_db.get_settlement_entries(next_cycle_id)
                carried = next_entries.get(extra_name)
                if not carried or int(carried.get("is_extra", 0) or 0) != 1:
                    raise ValueError("extra member did not carry to next cycle")
                next_row = next(
                    row for row in store.get_cycle_profit_view(next_cycle_id)["rows"]
                    if row["member_name"] == extra_name
                )
                if not next_row["is_extra"]:
                    raise ValueError("carried extra member is not displayed as removable")

                store.remove_cycle_member(next_cycle_id, extra_name)
                third_cycle_id = store.settle_and_create_next_cycle(next_cycle_id, "2026-05-02", {})
                third_names = {
                    row["member_name"]
                    for row in store.get_cycle_profit_view(third_cycle_id)["rows"]
                }
                if extra_name in third_names:
                    raise ValueError("removed extra member still carried to later cycle")
                return "carried until removed"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("cycle extra member carries to next until removed", _run)

    def check_cycle_settle_confirmation_rendered(self) -> None:
        def _run():
            from flask import Flask

            from binance_alpha.services.store_application import StoreApplication
            from binance_alpha.web import register_routes

            temp_dir = TEST_TEMP_ROOT / "cycle_settle_confirm"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                cycle_id = store.create_settlement_cycle("2026-05-01")
                app = Flask(
                    __name__,
                    template_folder=str(PROJECT_ROOT / "templates"),
                    static_folder=str(PROJECT_ROOT / "static"),
                )
                app.secret_key = "smoke"
                register_routes(app, store)
                response = app.test_client().get(f"/cycle-profit?cycle={cycle_id}")
                if response.status_code != 200:
                    raise ValueError(f"unexpected status: {response.status_code}")
                text = response.get_data(as_text=True)
                if "cycle_settle_next_confirm_message" in text:
                    raise ValueError("raw template key leaked")
                if "confirm(" not in text:
                    raise ValueError("settle confirmation is missing")
                return "settle confirmation rendered"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("cycle settle confirmation rendered", _run)

    def check_online_pull_retries_transient_and_caches(self) -> None:
        def _run():
            from postgrest.exceptions import APIError

            from binance_alpha.repositories import supabase_client as sc
            from binance_alpha.services.application_service import ApplicationService

            # --- transient APIError (gateway blip) is retried, then succeeds ---
            attempts = {"n": 0}

            def flaky():
                attempts["n"] += 1
                if attempts["n"] < 3:
                    raise APIError({"message": "connection reset by peer", "code": "503"})
                return "ok"

            original_delay = sc._RETRY_DELAY_SECONDS
            sc._RETRY_DELAY_SECONDS = 0  # keep the test fast
            try:
                if sc._retry_on_transient(flaky, label="test") != "ok" or attempts["n"] != 3:
                    raise ValueError("transient APIError was not retried to success")

                # --- permanent APIError (schema) fails fast, NOT retried ---
                perm = {"n": 0}

                def permanent():
                    perm["n"] += 1
                    raise APIError({"message": 'relation "x" does not exist', "code": "42P01"})

                try:
                    sc._retry_on_transient(permanent, label="test")
                    raise ValueError("permanent APIError should have raised")
                except APIError:
                    pass
                if perm["n"] != 1:
                    raise ValueError(f"permanent error retried {perm['n']}x; must fail fast")
            finally:
                sc._RETRY_DELAY_SECONDS = original_delay

            # --- per-request pull cache collapses repeat pulls into one call ---
            class FakeClient:
                def __init__(self):
                    self.member_pulls = 0
                    self.score_pulls = 0

                def is_configured(self):
                    return True

                def pull_members(self):
                    self.member_pulls += 1
                    return [{"name": "a", "status": "x", "deleted": 0, "sort_order": 0}]

                def pull_score_entries(self):
                    self.score_pulls += 1
                    return []

            fake = FakeClient()
            svc = ApplicationService(None, fake, lambda: "2026-01-01")
            svc._online_member_rows()
            svc._online_member_rows()
            svc._online_score_rows()
            svc._online_score_rows()
            if fake.member_pulls != 1 or fake.score_pulls != 1:
                raise ValueError(
                    f"pull cache miss: members={fake.member_pulls} scores={fake.score_pulls} (expected 1/1)"
                )
            return "transient retried, permanent fast-failed, pulls cached"

        self.check("online pull retries transient + caches", _run)

    def check_cash_flow_entry_persists_all_fields(self) -> None:
        def _run():
            from openpyxl import load_workbook

            from binance_alpha.excel.cash_flow_sheet import CASH_FLOW_SHEET_NAME
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "cash_flow_persist"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                out_form = {
                    "entry_date": "2026-07-12", "direction": "out", "amount": "300",
                    "category": "红包支出", "note": "群里发的红包",
                }
                store.add_cash_flow(out_form)
                store.add_cash_flow({
                    "entry_date": "2026-07-10", "direction": "in", "amount": "1000",
                    "category": "入金", "note": "充值",
                })

                # Match the test clock to its July fixtures, regardless of the run date.
                store._context.cashflow_service._today = lambda: '2026-07-12'

                # Every field must survive the round-trip to SQLite — a dropped
                # field here is exactly the class of data loss this project has hit before.
                rows = store._context.local_db.list_cash_flows()
                by_note = {r["note"]: r for r in rows}
                saved = by_note.get("群里发的红包")
                if saved is None:
                    raise ValueError("out entry not persisted")
                for field, expected in (
                    ("entry_date", "2026-07-12"), ("direction", "out"),
                    ("amount", "300"), ("category", "红包支出"),
                ):
                    if saved[field] != expected:
                        raise ValueError(f"{field} not persisted: {saved[field]!r} != {expected!r}")

                summary = store.get_cash_flow_view()["summary"]
                if (summary["total_out"], summary["total_in"], summary["net_out"], summary["month_out"]) != (
                    "300", "1000", "-700", "300"
                ):
                    raise ValueError(f"summary math wrong: {summary}")

                # Excel sheet mirrors the ledger.
                wb = load_workbook(store.workbook_path)
                try:
                    if CASH_FLOW_SHEET_NAME not in wb.sheetnames:
                        raise ValueError("资金流水 sheet missing from workbook")
                    headers = [wb[CASH_FLOW_SHEET_NAME].cell(1, c).value for c in range(1, 6)]
                    if headers != ["日期", "方向", "金额(U)", "分类", "备注"]:
                        raise ValueError(f"unexpected excel headers: {headers}")
                finally:
                    wb.close()

                # Soft delete removes it from the view but keeps history.
                store.delete_cash_flow(saved["id"])
                if any(r["id"] == saved["id"] for r in store.get_cash_flow_view()["rows"]):
                    raise ValueError("deleted entry still visible")
                if not any(
                    r["id"] == saved["id"]
                    for r in store._context.local_db.list_cash_flows(include_deleted=True)
                ):
                    raise ValueError("soft delete hard-removed the row")
                return "all fields persisted; excel + delete ok"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("cash flow entry persists all fields", _run)

    def check_cash_flow_rejects_bad_input(self) -> None:
        def _run():
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "cash_flow_bad_input"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                bad_inputs = [
                    {"entry_date": "", "direction": "out", "amount": "10", "category": "其他", "note": ""},
                    {"entry_date": "2026-07-12", "direction": "out", "amount": "abc", "category": "其他", "note": ""},
                    {"entry_date": "2026-07-12", "direction": "out", "amount": "0", "category": "其他", "note": ""},
                    {"entry_date": "2026-07-12", "direction": "out", "amount": "-5", "category": "其他", "note": ""},
                ]
                for form in bad_inputs:
                    try:
                        store.add_cash_flow(form)
                    except ValueError:
                        continue
                    raise ValueError(f"bad input accepted: {form}")
                if store.get_cash_flow_view()["rows"]:
                    raise ValueError("rejected input still created a ledger row")
                return "bad input rejected, no rows created"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("cash flow rejects bad input", _run)

    def check_settle_and_create_next_rolls_back_on_regen_error(self) -> None:
        def _run():
            import logging

            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "settle_next_rollback"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                member_name = store.get_active_members()[0]["name"]
                cycle_id = store.create_settlement_cycle("2026-05-01")
                store.save_cycle_settlement(cycle_id, {f"end_balance_{member_name}": "old"})

                def _fail_regen(cycles_data):
                    raise PermissionError("workbook locked")

                store._context.cycle_service._on_overview_regen = _fail_regen
                cycle_logger = logging.getLogger("binance_alpha.services.cycle_service")
                previous_disabled = cycle_logger.disabled
                cycle_logger.disabled = True
                try:
                    try:
                        store.settle_and_create_next_cycle(cycle_id, "2026-05-02", {member_name: "100"})
                    except PermissionError:
                        pass
                    else:
                        raise ValueError("settle-and-next succeeded despite regen failure")
                finally:
                    cycle_logger.disabled = previous_disabled

                cycle = store._context.local_db.get_settlement_cycle(cycle_id)
                if int(cycle.get("settled", 0) or 0) != 0:
                    raise ValueError("failed settle left original cycle settled")
                cycles = store._context.local_db.get_settlement_cycles()
                if len(cycles) != 1:
                    raise ValueError(f"failed settle left extra cycles: {len(cycles)}")
                entry = store._context.local_db.get_settlement_entries(cycle_id)[member_name]
                if str(entry.get("end_balance", "")) != "old":
                    raise ValueError("failed settle did not restore end balance")
                return "rollback ok"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("settle and create next rollback on regen error", _run)

    def check_new_cycle_first_day_entry_counts_in_new_window(self) -> None:
        def _run():
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "new_cycle_first_day"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                members = store.get_active_members()
                member_name = members[0]["name"]
                old_cycle_id = store.create_settlement_cycle("2026-05-01")
                new_cycle_id = store.settle_and_create_next_cycle(old_cycle_id, "2026-05-01", {})
                form_data = {f"score_{member['name']}": "1" for member in members}
                form_data[f"manual_wear_{member_name}"] = "2"
                form_data[f"income_{member_name}"] = "10"
                form_data[f"other_expense_{member_name}"] = "4"
                result = store.daily_entry_service.process_submission(members, form_data, "2026-05-02")
                if not result.get("ok"):
                    raise ValueError(result.get("error"))

                new_view = store.get_cycle_profit_view(new_cycle_id)
                old_view = store.get_cycle_profit_view(old_cycle_id)
                if "2026-05-02" not in new_view["redpacket_dates"]:
                    raise ValueError("new cycle first day redpacket date missing")
                if "2026-05-02" in old_view["redpacket_dates"]:
                    raise ValueError("new cycle first day leaked into old cycle")
                row = next(row for row in new_view["rows"] if row["member_name"] == member_name)
                if row["wear_total"] != 2.0 or row["income_total"] != 10.0 or row["redpacket_total"] != 4.0:
                    raise ValueError("new cycle first day totals are wrong")
                return "new cycle first day counted"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("new cycle first day entry counts in new window", _run)

    def check_mobile_row_then_desktop_completion_updates_cycle_profit(self) -> None:
        def _run():
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "mobile_then_desktop_cycle"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                member_name = store.get_active_members()[0]["name"]
                cycle_id = store.create_settlement_cycle("2026-05-01")
                db = store._context.local_db
                db.record_score_entries("2026-05-01", [{"name": member_name, "score": "8"}], source="online")
                view = store.get_cycle_profit_view(cycle_id)
                row = next(row for row in view["rows"] if row["member_name"] == member_name)
                if row["wear_total"] != 0.0:
                    raise ValueError(f"blank mobile wear should count as 0, got {row['wear_total']}")
                before = db.get_score_rows_for_date("2026-05-01")[0]

                db.record_score_entries(
                    "2026-05-01",
                    [{"name": member_name, "score": "8", "before_balance": "10", "after_balance": "7"}],
                    source="local",
                )
                after = db.get_score_rows_for_date("2026-05-01")[0]
                if int(after["version"]) != int(before["version"]) + 1:
                    raise ValueError("desktop completion did not increment version once")
                view = store.get_cycle_profit_view(cycle_id)
                row = next(row for row in view["rows"] if row["member_name"] == member_name)
                if row["wear_total"] != 3.0:
                    raise ValueError(f"completed balance wear not reflected: {row['wear_total']}")
                return "mobile blank then desktop completion ok"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("mobile row then desktop completion updates cycle profit", _run)

    def check_cycle_push_pull_roundtrip(self) -> None:
        def _run():
            from binance_alpha.local_database import LocalDatabase

            class FakeSupabase:
                def __init__(self) -> None:
                    self.pushed_cycles: list[dict] = []
                    self.pushed_entries: list[dict] = []

                def ensure_score_entries_schema(self) -> None:
                    return None

                def push_members(self, rows):
                    return len(rows)

                def push_score_entries(self, rows):
                    return len(rows)

                def push_settlement_cycles(self, rows):
                    self.pushed_cycles = list(rows)
                    return len(rows)

                def push_settlement_entries(self, rows):
                    self.pushed_entries = list(rows)
                    return len(rows)

                def pull_score_entries(self, since_updated_at=None):
                    return []

                def mark_score_entries_deleted(self, rows):
                    return len(rows)

            class FakePullSupabase:
                def __init__(self, cycles, entries) -> None:
                    self._cycles = cycles
                    self._entries = entries

                def pull_members(self, since_updated_at=None):
                    return []

                def pull_score_entries(self, since_updated_at=None):
                    return []

                def pull_settlement_cycles(self, since_updated_at=None):
                    return self._cycles

                def pull_settlement_entries(self, since_updated_at=None):
                    return self._entries

            temp_dir = TEST_TEMP_ROOT / "cycle_push_pull_roundtrip"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            (temp_dir / "source").mkdir(parents=True)
            (temp_dir / "sink").mkdir(parents=True)
            try:
                # Source DB writes a cycle locally and pushes to fake Supabase.
                source_db = LocalDatabase(temp_dir / "source")
                cycle_id = source_db.create_settlement_cycle("2026-05-01")
                source_db.save_settlement_entries(
                    cycle_id,
                    [{"member_name": "alice", "start_balance": "100", "end_balance": ""}],
                )
                fake = FakeSupabase()
                source_db.push_to_supabase(fake, force_full=True)
                if not fake.pushed_cycles:
                    raise ValueError("cycles were not pushed")
                if not fake.pushed_entries:
                    raise ValueError("cycle entries were not pushed")
                pushed_cycle = fake.pushed_cycles[0]
                for required in ("id", "name", "start_date", "version", "deleted", "source"):
                    if required not in pushed_cycle:
                        raise ValueError(f"cycle missing column: {required}")
                pushed_entry = fake.pushed_entries[0]
                for required in (
                    "cycle_id", "member_name", "start_balance",
                    "version", "deleted", "source",
                ):
                    if required not in pushed_entry:
                        raise ValueError(f"cycle entry missing column: {required}")

                # Sink DB pulls the same payload and verifies LWW merge.
                sink_db = LocalDatabase(temp_dir / "sink")
                sink_db.pull_from_supabase(
                    FakePullSupabase(fake.pushed_cycles, fake.pushed_entries)
                )
                pulled_cycles = sink_db.get_settlement_cycles()
                if not pulled_cycles:
                    raise ValueError("cycle was not merged into sink DB")
                if pulled_cycles[0]["start_date"] != "2026-05-01":
                    raise ValueError("cycle start_date did not survive sync")
                pulled_entries = sink_db.get_settlement_entries(cycle_id)
                if "alice" not in pulled_entries:
                    raise ValueError("cycle entry was not merged into sink DB")
                if pulled_entries["alice"]["start_balance"] != "100":
                    raise ValueError("cycle entry start_balance did not sync")
                return f"cycle={pulled_cycles[0]['start_date']} balance={pulled_entries['alice']['start_balance']}"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("cycle push pull roundtrip", _run)

    def check_online_wear_uses_cycle_window(self) -> None:
        def _run():
            from datetime import date, timedelta
            from binance_alpha.services.application_service import ApplicationService

            cycle_start = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
            today = date.today().strftime("%Y-%m-%d")

            class FakeSupabase:
                def is_configured(self) -> bool:
                    return True

                def pull_members(self, since_updated_at=None):
                    return [
                        {
                            "id": "member:alice",
                            "name": "alice",
                            "status": "启用",
                            "note": "",
                            "sort_order": 1,
                            "created_at": "2026-04-01 00:00:00",
                            "disabled_at": "",
                            "updated_at": "2026-04-01 00:00:00",
                            "version": 1,
                            "deleted": 0,
                        }
                    ]

                def pull_score_entries(self, since_updated_at=None):
                    return [
                        {
                            "id": f"score:{today}:alice",
                            "member_id": "member:alice",
                            "member_name": "alice",
                            "score_date": today,
                            "score": 17,
                            "manual_wear": "2",
                            "updated_at": f"{today} 00:00:00",
                            "version": 1,
                            "deleted": 0,
                        }
                    ]

                def pull_settlement_cycles(self, since_updated_at=None):
                    return [
                        {
                            "id": "cycle-1",
                            "name": "周期05-01",
                            "start_date": cycle_start,
                            "settle_date": "",
                            "settled": 0,
                            "created_at": "2026-05-01 00:00:00",
                            "updated_at": "2026-05-01 00:00:00",
                            "version": 1,
                            "deleted": 0,
                            "source": "local",
                        }
                    ]

            service = ApplicationService(None, FakeSupabase(), lambda: today)
            view = service.online_wear_sheet_view()
            # Cycle window is 30+ days; legacy fallback is exactly 15 columns.
            if view["column_count"] <= 17:  # 15 dates + total + name + avg = 18
                raise ValueError(
                    f"wear view still uses 15-day window (column_count={view['column_count']})"
                )
            summary = service.online_cycle_wear_summary()
            if not summary["has_cycle"]:
                raise ValueError("cycle summary did not detect synced cycle")
            if summary["start_date"] != cycle_start:
                raise ValueError(
                    f"cycle summary start_date mismatch: {summary['start_date']} vs {cycle_start}"
                )
            return f"cols={view['column_count']} start={summary['start_date']}"

        self.check("online wear uses cycle window", _run)

    def check_recent_low_score_format(self) -> None:
        def _run():
            from datetime import datetime, timedelta
            from openpyxl import load_workbook

            from binance_alpha.constants import NAME_HEADER, SCORE_LOW_DAILY_FONT_COLOR, SCORE_SHEET
            from binance_alpha.services.store_application import StoreApplication

            temp_dir = TEST_TEMP_ROOT / "low_score_format"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True)
            try:
                store = StoreApplication(temp_dir)
                members = store.get_active_members()
                low_member = members[0]["name"]
                start = datetime.strptime(store.get_next_score_date(), "%Y-%m-%d").date()
                for offset in range(16):
                    current = (start + timedelta(days=offset)).strftime("%Y-%m-%d")
                    form_data = {f"score_{member['name']}": "12" for member in members}
                    form_data[f"score_{low_member}"] = "9"
                    result = store.daily_entry_service.process_submission(members, form_data, current)
                    if not result.get("ok"):
                        raise ValueError(result.get("error"))
                store.export_to_excel("2026-05-16")
                workbook = load_workbook(store.workbook_path)
                try:
                    sheet = workbook[SCORE_SHEET]
                    name_col = next(
                        col
                        for col in range(1, sheet.max_column + 1)
                        if str(sheet.cell(1, col).value or "") == NAME_HEADER
                    )
                    target_row = next(
                        row
                        for row in range(2, sheet.max_row + 1)
                        if str(sheet.cell(row, name_col).value or "") == low_member
                    )
                    by_header = {
                        str(sheet.cell(1, col).value or ""): col
                        for col in range(1, sheet.max_column + 1)
                    }

                    def color_text(header: str) -> str:
                        color = sheet.cell(target_row, by_header[header]).font.color
                        return str(getattr(color, "rgb", "") or "")

                    recent_header = (start + timedelta(days=1)).strftime("%m-%d")
                    old_header = start.strftime("%m-%d")
                    if color_text(recent_header)[-6:] != SCORE_LOW_DAILY_FONT_COLOR:
                        raise ValueError("recent low score is not red")
                    if color_text(old_header)[-6:] == SCORE_LOW_DAILY_FONT_COLOR:
                        raise ValueError("older low score should not be red")
                    high_member = members[1]["name"]
                    high_row = next(
                        row
                        for row in range(2, sheet.max_row + 1)
                        if str(sheet.cell(row, name_col).value or "") == high_member
                    )
                    high_cell = sheet.cell(high_row, by_header[recent_header])
                    high_fill = str(getattr(high_cell.fill.fgColor, "rgb", "") or "")[-6:]
                    if int(high_cell.value or 0) != 12:
                        raise ValueError("score value changed during formatting")
                    if high_fill not in ("", "000000"):
                        raise ValueError("normal score should not have special fill")
                finally:
                    workbook.close()
                return "recent low score red"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.check("recent low score format", _run)

    def check_architecture_rules(self) -> None:
        def _run():
            from scripts.architecture_check import run_checks

            failures = run_checks()
            if failures:
                raise ValueError("; ".join(failures))
            return "rules=3"

        self.check("架构依赖规则", _run)

    def run(self) -> int:
        try:
            self.check_module_imports()
            self.check_workbook_branding_compatibility()
            self.check_application_init()
            self.check_store_read_entry()
            self.check_presenter_entry()
            self.check_score_overview_sorting_and_low_score()
            self.check_online_score_overview_sorting_and_low_score()
            self.check_online_wear_view_uses_synced_wear()
            self.check_service_entry()
            self.check_application_entry()
            self.check_static_assets()
            self.check_page_routes()
            self.check_read_only_routes_do_not_open_excel()
            self.check_excel_date_notes_import()
            self.check_excel_refresh_side_effect_guards()
            self.check_excel_refresh_does_not_delete_missing_snapshot_rows()
            self.check_cross_year_score_date_metadata()
            self.check_historical_cycle_block_refresh_updates_values()
            self.check_deleted_score_column_does_not_delete_database_rows()
            self.check_member_rename_refresh_preserves_old_rows()
            self.check_member_status_survives_excel_refresh()
            self.check_existing_config_members_are_not_reseeded()
            self.check_supabase_profit_uses_formal_column()
            self.check_online_entry_reports_supabase_request_failure()
            self.check_wear_threshold_uses_saved_config()
            self.check_wear_daily_member_average_row()
            self.check_daily_entry_rejects_incomplete_balance_pair()
            self.check_invalid_save_date_is_rejected()
            self.check_cycle_block_dates_and_reserved_member_names()
            self.check_excel_refresh_rejects_unknown_score_date()
            self.check_excel_refresh_rejects_missing_score_date_meta()
            self.check_delete_score_date()
            self.check_delete_score_date_rolls_back_on_export_error()
            self.check_empty_delete_score_date_uses_info_flash()
            self.check_supabase_push_saves_posted_entry_once()
            self.check_online_manual_wear_syncs_to_local()
            self.check_blank_online_detail_fields_do_not_churn()
            self.check_supabase_remote_reentry_restores_local_tombstone()
            self.check_supabase_pull_overlap_and_force_full()
            self.check_supabase_clock_skew_row_is_pulled()
            self.check_supabase_delete_push_reinsert_pull_roundtrip()
            self.check_supabase_env_file_reloads()
            self.check_cycle_extra_member_reactivation_normalizes_flag()
            self.check_cycle_extra_member_carries_to_next_until_removed()
            self.check_cycle_settle_confirmation_rendered()
            self.check_online_pull_retries_transient_and_caches()
            self.check_cash_flow_entry_persists_all_fields()
            self.check_cash_flow_rejects_bad_input()
            self.check_settle_and_create_next_rolls_back_on_regen_error()
            self.check_new_cycle_first_day_entry_counts_in_new_window()
            self.check_mobile_row_then_desktop_completion_updates_cycle_profit()
            self.check_cycle_push_pull_roundtrip()
            self.check_online_wear_uses_cycle_window()
            self.check_recent_low_score_format()
            self.check_architecture_rules()
        finally:
            if self._temp_root and self._temp_root.exists():
                shutil.rmtree(self._temp_root, ignore_errors=True)
            if TEST_TEMP_ROOT.exists():
                shutil.rmtree(TEST_TEMP_ROOT, ignore_errors=True)

        passed = 0
        failed = 0
        for ok, name, detail in self.results:
            if ok:
                passed += 1
                print(f"[PASS] {name}")
                if detail:
                    print(f"       {detail}")
            else:
                failed += 1
                print(f"[FAIL] {name}")
                print(f"       {detail}")

        print()
        print(f"总计: {passed} 通过, {failed} 失败")
        return 1 if failed else 0


def main() -> int:
    runner = SmokeCheckRunner()
    return runner.run()


if __name__ == "__main__":
    raise SystemExit(main())
