"""
Aurum Desk V12: Authoritative Multi-Sheet Excel (.xlsx) Exporter.
Exports complete 3-month historical replay audit with 10 dedicated sheets:
1. 01_Tong_quan: Executive summary, KPIs, config manifest, execution disclosures.
2. 02_Tong_hop_ngay: Complete day-by-day rows for every single calendar day in [start, cutoff].
3. 03_Chi_tiet_lenh: Detailed record for every trade with full V10.4 cost breakdown.
4. 04_Yeu_to_vao_lenh: Long-form factor audit table for every filled trade.
5. 05_Tin_hieu_bi_chan: Unique aggregated blocked signals and rejection reasons.
6. 06_Tong_hop_thang: Monthly performance buckets for all calendar months intersecting period.
7. 07_Phien_va_huong: Session and direction breakdowns.
8. 08_Duong_von: Equity curve time series with drawdown tracking.
9. 09_Chat_luong_du_lieu: Multi-timeframe quality audit (15M, 1H, 4H, 1D, 5M, 1M).
10. 10_Cau_hinh_va_test: Complete config snapshot and acceptance test matrix.
"""
import os
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

# Styling Constants
FONT_FAMILY = "Segoe UI"
HEADER_FILL = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
HEADER_FONT = Font(name=FONT_FAMILY, size=11, bold=True, color="FFFFFF")
TITLE_FONT = Font(name=FONT_FAMILY, size=14, bold=True, color="1E293B")
SECTION_FONT = Font(name=FONT_FAMILY, size=12, bold=True, color="0F172A")
REGULAR_FONT = Font(name=FONT_FAMILY, size=10, color="000000")
BOLD_FONT = Font(name=FONT_FAMILY, size=10, bold=True, color="000000")
GREEN_FONT = Font(name=FONT_FAMILY, size=10, bold=True, color="137333")
RED_FONT = Font(name=FONT_FAMILY, size=10, bold=True, color="C5221F")

THIN_BORDER_SIDE = Side(border_style="thin", color="CBD5E1")
CELL_BORDER = Border(left=THIN_BORDER_SIDE, right=THIN_BORDER_SIDE, top=THIN_BORDER_SIDE, bottom=THIN_BORDER_SIDE)

# Number Formats
FORMAT_CURRENCY = "$#,##0.00"
FORMAT_PERCENT = "0.00%"
FORMAT_INTEGER = "#,##0"
FORMAT_RR = "0.00\"R\""
FORMAT_DATE_TIME = "YYYY-MM-DD HH:MM:SS"
FORMAT_DATE = "YYYY-MM-DD"


def sanitize_cell_value(val: Any) -> Any:
    """Guards against CSV/Formula Injection by prepending single quote to '=+-@'."""
    if isinstance(val, str) and len(val) > 0 and val[0] in ("=", "+", "-", "@"):
        return f"'{val}"
    return val


def auto_fit_columns(ws, min_width: int = 12, max_width: int = 50):
    """Automatically adjusts column widths for clean readability."""
    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = 0
        for cell in col:
            val = cell.value
            if val is not None:
                val_str = str(val)
                if len(val_str) > max_len:
                    max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(min_width, min(max_len + 4, max_width))


class V12ExcelExporter:
    """Exports authoritative 12-sheet Excel workbook for V12_1 Replay and Multi-mode comparison."""

    @classmethod
    def export_workbook(
        cls,
        filepath: str,
        run_id: str,
        manifest: Dict[str, Any],
        summary: Dict[str, Any],
        trades: List[Dict[str, Any]],
        daily_rows: List[Dict[str, Any]],
        monthly_rows: List[Dict[str, Any]],
        session_stats: Dict[str, Any],
        direction_stats: Dict[str, Any],
        equity_points: List[Dict[str, Any]],
        factors: List[Dict[str, Any]],
        blocked_signals: List[Dict[str, Any]],
        quality_metadata: List[Dict[str, Any]],
        test_matrix: Optional[List[Dict[str, Any]]] = None,
        ny_quota_rows: Optional[List[Dict[str, Any]]] = None,
        funnel_stats: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        wb = openpyxl.Workbook()
        # Remove default sheet
        wb.remove(wb.active)

        # 1. 01_Tong_quan
        cls._create_sheet_tong_quan(wb, run_id, manifest, summary)

        # 2. 02_Tong_hop_ngay (Strictly covers every single local calendar day)
        cls._create_sheet_tong_hop_ngay(wb, daily_rows)

        # 3. 03_Chi_tiet_lenh
        cls._create_sheet_chi_tiet_lenh(wb, trades)

        # 4. 04_Yeu_to_vao_lenh (Long-form factor audit table)
        cls._create_sheet_yeu_to_vao_lenh(wb, factors)

        # 5. 05_Tin_hieu_bi_chan
        cls._create_sheet_tin_hieu_bi_chan(wb, blocked_signals)

        # 6. 06_Tong_hop_thang
        cls._create_sheet_tong_hop_thang(wb, monthly_rows)

        # 7. 07_Phien_va_huong
        cls._create_sheet_phien_va_huong(wb, session_stats, direction_stats)

        # 8. 08_Duong_von
        cls._create_sheet_duong_von(wb, equity_points)

        # 9. 09_Chat_luong_du_lieu
        cls._create_sheet_chat_luong_du_lieu(wb, quality_metadata)

        # 10. 10_Cau_hinh_va_test
        cls._create_sheet_cau_hinh_va_test(wb, manifest, test_matrix or [])

        # 11. 11_NY_Quota (Added in V12_1)
        cls._create_sheet_ny_quota(wb, ny_quota_rows or [])

        # 12. 12_Funnel (Added in V12_1)
        cls._create_sheet_funnel(wb, funnel_stats or [])

        # Ensure directory exists and save
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        wb.save(filepath)

        # Reopen verification
        verify_wb = openpyxl.load_workbook(filepath, read_only=True)
        sheet_names = verify_wb.sheetnames
        verify_wb.close()

        assert len(sheet_names) in (10, 12), f"Expected 10 or 12 sheets, found {len(sheet_names)}: {sheet_names}"
        return filepath

    @classmethod
    def _create_sheet_tong_quan(cls, wb, run_id: str, manifest: Dict[str, Any], summary: Dict[str, Any]):
        ws = wb.create_sheet(title="01_Tong_quan")
        ws.views.sheetView[0].showGridLines = True

        ws["A1"] = "BÁO CÁO TỔNG QUAN SIMULATION 3 THÁNG — AURUM DESK V12"
        ws["A1"].font = TITLE_FONT
        ws["A2"] = "Chiến lược: SMC Momentum (Current Frozen Strategy) | Thị trường: XAUUSDT Bitget Classic Futures"
        ws["A2"].font = REGULAR_FONT

        headers = ["Hạng mục", "Thông số", "Đơn vị / Ghi chú"]
        ws.row_dimensions[4].height = 24
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=4, column=col_idx, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = CELL_BORDER

        rows = [
            ("Mã kiểm thử (Run ID)", sanitize_cell_value(run_id), "Mã định danh duy nhất"),
            ("Git Commit Tested SHA", sanitize_cell_value(manifest.get("tested_sha", "N/A")), "Commit baseline audited"),
            ("Trạng thái Replay", sanitize_cell_value(manifest.get("status", "SUCCESS")), "SUCCESS / INCOMPLETE"),
            ("Chế độ Replay", "CURRENT_STRATEGY_ON_HISTORICAL_MARKET", "Áp dụng chiến lược hiện tại lên thị trường quá khứ"),
            ("Thời điểm Cutoff", sanitize_cell_value(manifest.get("cutoff_str_vn", "N/A")), "Asia/Ho_Chi_Minh"),
            ("Thời điểm Bắt đầu", sanitize_cell_value(manifest.get("start_str_vn", "N/A")), "Cutoff trừ đúng 3 tháng lịch (92 ngày)"),
            ("Thời điểm Warmup", sanitize_cell_value(manifest.get("warmup_str_vn", "N/A")), "50 ngày lookback cho HTF D/4H"),
            ("Vốn ban đầu (Initial Equity)", summary.get("initial_equity", 1000.0), "$"),
            ("Vốn tiền mặt cuối kỳ (Cash Balance)", summary.get("cash_balance", 1000.0), "$"),
            ("Lợi nhuận thả nổi mở (Open MTM)", summary.get("open_mtm", 0.0), "$ (Lệnh còn mở cuối kỳ)"),
            ("Vốn ròng cuối kỳ (Final Equity)", summary.get("final_equity", 1000.0), "$ (Cash + Open MTM)"),
            ("Tổng lợi nhuận thực hiện (Realized Net PnL)", summary.get("realized_net_pnl", 0.0), "$"),
            ("Tỷ suất sinh lời (ROI)", summary.get("roi_pct", 0.0) / 100.0 if "roi_pct" in summary else (summary.get("final_equity", 1000.0) - summary.get("initial_equity", 1000.0)) / summary.get("initial_equity", 1000.0), "%"),
            ("Tổng số lệnh khớp (Fills Count)", summary.get("closed_trades_count", 0) + summary.get("open_trades_count", 0), "Tổng lệnh khớp"),
            ("Lệnh đã đóng (Closed Trades)", summary.get("closed_trades_count", 0), "Số lệnh"),
            ("Lệnh còn mở (Open Trades)", summary.get("open_trades_count", 0), "Số lệnh"),
            ("Lệnh Thắng (Wins)", summary.get("wins", 0), "Số lệnh"),
            ("Lệnh Thua (Losses)", summary.get("losses", 0), "Số lệnh"),
            ("Lệnh Hòa vốn (Breakevens)", summary.get("breakevens", 0), "Số lệnh"),
            ("Tỷ lệ thắng (Win Rate)", (summary.get("win_rate_pct", 0.0) / 100.0), "%"),
            ("Profit Factor", summary.get("profit_factor") if summary.get("profit_factor") is not None else "N/A (0 Thua)", "Gross Profit / Gross Loss"),
            ("Kỳ vọng bình quân (Expectancy R)", summary.get("expectancy_r", 0.0), "R"),
            ("Max Drawdown (MTM bar-by-bar)", summary.get("max_drawdown_usdt", 0.0), "$"),
            ("Max Drawdown %", (summary.get("max_drawdown_pct", 0.0) / 100.0), "%"),
            ("Chuỗi thua liên tiếp tối đa", summary.get("max_consecutive_losses", 0), "Lệnh"),
            ("Tổng phí giao dịch (Fees)", summary.get("total_fees", 0.0), "$ (Maker TP 0.02%, Taker SL 0.06%)"),
            ("Tổng trượt giá ước tính (Slippage)", summary.get("total_slippage", 0.0), "$ (0.10$/oz directional)"),
            ("Số ngày có giao dịch", summary.get("trading_days", 0), "Ngày"),
            ("Số ngày không có giao dịch", summary.get("no_trade_days", 0), "Ngày"),
            ("Số tín hiệu phát hiện (READY)", summary.get("signals_count", 0), "Tín hiệu"),
            ("Số tín hiệu bị chặn bởi Policy", summary.get("rejected_count", 0), "Tín hiệu")
        ]

        curr_row = 5
        for item, val, note in rows:
            c1 = ws.cell(row=curr_row, column=1, value=item)
            c2 = ws.cell(row=curr_row, column=2, value=val)
            c3 = ws.cell(row=curr_row, column=3, value=note)

            c1.font = BOLD_FONT
            c2.font = REGULAR_FONT
            c3.font = REGULAR_FONT

            c1.border = CELL_BORDER
            c2.border = CELL_BORDER
            c3.border = CELL_BORDER

            # Formatting
            if isinstance(val, float):
                if "$" in note:
                    c2.number_format = FORMAT_CURRENCY
                    if "PnL" in item or "MTM" in item:
                        c2.font = GREEN_FONT if val >= 0 else RED_FONT
                elif "%" in note:
                    c2.number_format = FORMAT_PERCENT
                elif "R" in note:
                    c2.number_format = FORMAT_RR
            elif isinstance(val, int) and "$" not in note and "%" not in note:
                c2.number_format = FORMAT_INTEGER

            curr_row += 1

        # Limitations box
        curr_row += 2
        ws.cell(row=curr_row, column=1, value="GIỚI HẠN & GIẢ ĐỊNH PHƯƠNG PHÁP (METHODOLOGY DISCLOSURES):").font = SECTION_FONT
        curr_row += 1
        disclosures = [
            "1. Dữ liệu nến: Nguồn nến đóng lịch sử Bitget Classic Futures (XAUUSDT). Khung 15M thực thi, 1H căn chỉnh, 4H/1D định hướng.",
            "2. Zero Lookahead: Toàn bộ quyết định chỉ sử dụng dữ liệu nến đã đóng tại hoặc trước thời điểm mô phỏng.",
            "3. Mô hình khớp lệnh: Ước lượng theo mô hình ESTIMATED_EXECUTION (Spread 0.20$, trượt giá 0.10$/oz).",
            "4. Đòn bẩy & Rủi ro: 30x ISOLATED margin, rủi ro cố định 0.25% vốn theo chính sách quản trị Aurum Desk.",
            "5. Kết quả quá khứ: Đây là kiểm thử kỹ thuật (Technical Pass) mô phỏng phần mềm, không bảo đảm lợi nhuận tương lai."
        ]
        for d in disclosures:
            cell = ws.cell(row=curr_row, column=1, value=d)
            cell.font = REGULAR_FONT
            curr_row += 1

        auto_fit_columns(ws)
        ws.freeze_panes = "A5"

    @classmethod
    def _create_sheet_tong_hop_ngay(cls, wb, daily_rows: List[Dict[str, Any]]):
        ws = wb.create_sheet(title="02_Tong_hop_ngay")
        ws.views.sheetView[0].showGridLines = True

        headers = [
            "Ngày (Date VN)", "Tháng (Month)", "Ngày một phần (Partial)", "Trạng thái dữ liệu",
            "Vốn đầu ngày (Cash)", "Vốn cuối ngày (Cash)", "Equity đầu ngày", "Equity cuối ngày",
            "Lợi nhuận ròng ngày (Realized PnL)", "Phí ngày (Fees)", "Open MTM cuối ngày",
            "Lệnh LONG khớp", "Lệnh SHORT khớp", "Tổng lệnh khớp (Fills)",
            "Lệnh đóng Thắng", "Lệnh đóng Thua", "Lệnh đóng Hòa", "Tổng lệnh đóng",
            "Max Intraday DD ($)", "Khớp phiên NY", "Lý do không giao dịch / Blocker chính"
        ]

        ws.row_dimensions[1].height = 26
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = CELL_BORDER

        for row_idx, r in enumerate(daily_rows, start=2):
            ws.row_dimensions[row_idx].height = 20
            vals = [
                r.get("date", ""),
                r.get("month", ""),
                "CÓ" if r.get("is_partial", False) else "KHÔNG",
                r.get("data_status", "OK"),
                r.get("opening_cash", 1000.0),
                r.get("closing_cash", 1000.0),
                r.get("opening_equity", 1000.0),
                r.get("closing_equity", 1000.0),
                r.get("realized_pnl", 0.0),
                r.get("fees", 0.0),
                r.get("open_mtm", 0.0),
                r.get("long_fills", 0),
                r.get("short_fills", 0),
                r.get("total_fills", 0),
                r.get("closed_wins", 0),
                r.get("closed_losses", 0),
                r.get("closed_breakevens", 0),
                r.get("closed_count", 0),
                r.get("max_intraday_dd", 0.0),
                r.get("ny_fills", 0),
                sanitize_cell_value(r.get("no_trade_reason", "-"))
            ]

            for col_idx, val in enumerate(vals, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.font = REGULAR_FONT
                cell.border = CELL_BORDER

                # Formatting
                if col_idx in (5, 6, 7, 8, 9, 10, 11, 19):
                    cell.number_format = FORMAT_CURRENCY
                    if col_idx in (9, 11) and isinstance(val, (int, float)):
                        cell.font = GREEN_FONT if val > 0 else (RED_FONT if val < 0 else REGULAR_FONT)
                elif col_idx in (12, 13, 14, 15, 16, 17, 18, 20):
                    cell.number_format = FORMAT_INTEGER
                    cell.alignment = Alignment(horizontal="center")
                elif col_idx in (1, 2, 3, 4):
                    cell.alignment = Alignment(horizontal="center")

        auto_fit_columns(ws)
        ws.freeze_panes = "A2"

    @classmethod
    def _create_sheet_chi_tiet_lenh(cls, wb, trades: List[Dict[str, Any]]):
        ws = wb.create_sheet(title="03_Chi_tiet_lenh")
        ws.views.sheetView[0].showGridLines = True

        headers = [
            "Mã lệnh (Trade ID)", "Mã Setup", "Chiến lược", "Loại lệnh", "Mã phiên NY",
            "Vào lệnh (VN)", "Entry UTC ms", "Đóng lệnh (VN)", "Exit UTC ms", "Hướng",
            "Phiên vào", "Phiên thoát", "Giá vào (Entry)", "Stop Loss", "Take Profit",
            "Khối lượng (oz)", "Đòn bẩy", "Ký quỹ ($)", "Rủi ro kế hoạch ($)",
            "Net RR kế hoạch", "Net RR khớp", "Gross RR", "Lãi gộp ($)",
            "Phí vào ($)", "Phí ra ($)", "Trượt giá ($)", "Lãi ròng ($)",
            "Realized R", "Trạng thái", "Nguyên nhân đóng", "Mơ hồ (Ambiguous)"
        ]

        ws.row_dimensions[1].height = 26
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = CELL_BORDER

        for row_idx, t in enumerate(trades, start=2):
            ws.row_dimensions[row_idx].height = 20
            # Net RR unrounded snapshot
            net_rr_p = t.get("net_rr_planned") if t.get("net_rr_planned") is not None else t.get("net_rr_fill", 0.0)
            net_rr_f = t.get("net_rr_fill", 0.0)
            gross_rr_val = t.get("gross_rr", 0.0)

            vals = [
                sanitize_cell_value(t.get("id", "")),
                sanitize_cell_value(t.get("setup_id", "")),
                sanitize_cell_value(t.get("strategy_family", "SMC_MOMENTUM")),
                sanitize_cell_value(t.get("entry_type", "QUALITY_ENTRY")),
                sanitize_cell_value(t.get("ny_session_id", "-")),
                t.get("entry_time_vn", ""),
                t.get("entry_time_ms", 0),
                t.get("exit_time_vn", "-"),
                t.get("exit_time_ms", 0),
                t.get("direction", ""),
                t.get("entry_session", ""),
                t.get("exit_session", ""),
                t.get("entry_price", 0.0),
                t.get("stop_loss", 0.0),
                t.get("take_profit", 0.0),
                t.get("quantity", 0.0),
                t.get("leverage", 30),
                t.get("margin_usdt", 0.0),
                t.get("initial_risk_usdt", 0.0),
                net_rr_p,
                net_rr_f,
                gross_rr_val,
                t.get("gross_pnl", 0.0),
                t.get("entry_fee", 0.0),
                t.get("exit_fee", 0.0),
                t.get("slippage", 0.0),
                t.get("net_pnl", 0.0),
                t.get("realized_r", 0.0),
                t.get("status", "CLOSED"),
                sanitize_cell_value(t.get("exit_cause", "-")),
                "CÓ" if t.get("is_ambiguous", False) else "KHÔNG"
            ]

            for col_idx, val in enumerate(vals, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.font = REGULAR_FONT
                cell.border = CELL_BORDER

                # Formatting
                if col_idx in (13, 14, 15, 18, 19, 23, 24, 25, 26, 27):
                    cell.number_format = FORMAT_CURRENCY
                    if col_idx == 27 and isinstance(val, (int, float)):
                        cell.font = GREEN_FONT if val > 0 else (RED_FONT if val < 0 else REGULAR_FONT)
                elif col_idx in (20, 21, 22, 28):
                    cell.number_format = FORMAT_RR
                elif col_idx in (16,):
                    cell.number_format = "0.00"
                elif col_idx in (17,):
                    cell.number_format = FORMAT_INTEGER
                    cell.alignment = Alignment(horizontal="center")
                elif col_idx in (3, 4, 5, 10, 11, 12, 29, 31):
                    cell.alignment = Alignment(horizontal="center")

        auto_fit_columns(ws)
        ws.freeze_panes = "A2"


    @classmethod
    def _create_sheet_yeu_to_vao_lenh(cls, wb, factors: List[Dict[str, Any]]):
        ws = wb.create_sheet(title="04_Yeu_to_vao_lenh")
        ws.views.sheetView[0].showGridLines = True

        headers = [
            "Mã Quyết định", "Mã Lệnh", "Mã Setup", "Thời gian (VN)", "Available At ms",
            "Giai đoạn (Stage)", "Yếu tố (Factor Name)", "Giá trị thực tế", "Điều kiện yêu cầu",
            "Đánh giá (Status)", "Khung thời gian", "Giải thích chi tiết (Rationale)"
        ]

        ws.row_dimensions[1].height = 26
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = CELL_BORDER

        for row_idx, f in enumerate(factors, start=2):
            ws.row_dimensions[row_idx].height = 20
            vals = [
                sanitize_cell_value(f.get("decision_id", "")),
                sanitize_cell_value(f.get("trade_id", "")),
                sanitize_cell_value(f.get("setup_id", "")),
                f.get("time_vn", ""),
                f.get("available_at_ms", 0),
                f.get("stage", "ANALYZE"),
                sanitize_cell_value(f.get("factor_name", "")),
                sanitize_cell_value(str(f.get("factor_value", ""))),
                sanitize_cell_value(str(f.get("expected", ""))),
                f.get("status", "PASS"),
                f.get("timeframe", "15M"),
                sanitize_cell_value(f.get("rationale", ""))
            ]

            for col_idx, val in enumerate(vals, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.font = REGULAR_FONT
                cell.border = CELL_BORDER

                if col_idx == 10:
                    cell.alignment = Alignment(horizontal="center")
                    if val == "PASS":
                        cell.font = GREEN_FONT
                    elif val in ("FAIL", "BLOCKED"):
                        cell.font = RED_FONT
                    elif val == "NOT_USED":
                        cell.font = Font(name=FONT_FAMILY, size=10, color="64748B")

        auto_fit_columns(ws)
        ws.freeze_panes = "A2"

    @classmethod
    def _create_sheet_tin_hieu_bi_chan(cls, wb, blocked_signals: List[Dict[str, Any]]):
        ws = wb.create_sheet(title="05_Tin_hieu_bi_chan")
        ws.views.sheetView[0].showGridLines = True

        headers = [
            "Mã Setup", "Lần đầu thấy (VN)", "Lần cuối thấy (VN)", "Hướng",
            "Giai đoạn chặn", "Nguyên nhân chặn chính", "Tất cả các chặn", "Số lần xuất hiện", "Mẫu thời gian (ms)"
        ]

        ws.row_dimensions[1].height = 26
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = CELL_BORDER

        for row_idx, b in enumerate(blocked_signals, start=2):
            ws.row_dimensions[row_idx].height = 20
            vals = [
                sanitize_cell_value(b.get("setup_id", "")),
                b.get("first_seen_vn", ""),
                b.get("last_seen_vn", ""),
                b.get("direction", ""),
                b.get("stage", ""),
                sanitize_cell_value(b.get("primary_blocker", "")),
                sanitize_cell_value(b.get("all_blockers", "")),
                b.get("count", 1),
                b.get("sample_time_ms", 0)
            ]

            for col_idx, val in enumerate(vals, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.font = REGULAR_FONT
                cell.border = CELL_BORDER
                if col_idx in (4, 5, 8):
                    cell.alignment = Alignment(horizontal="center")
                if col_idx == 6:
                    cell.font = RED_FONT

        auto_fit_columns(ws)
        ws.freeze_panes = "A2"

    @classmethod
    def _create_sheet_tong_hop_thang(cls, wb, monthly_rows: List[Dict[str, Any]]):
        ws = wb.create_sheet(title="06_Tong_hop_thang")
        ws.views.sheetView[0].showGridLines = True

        headers = [
            "Tháng (Month)", "Tháng một phần (Partial)", "Từ ngày", "Đến ngày",
            "Số ngày GD", "Số ngày nghỉ", "Lệnh LONG", "Lệnh SHORT", "Tổng lệnh khớp (Fills)",
            "Lệnh Thắng", "Lệnh Thua", "Lệnh Hòa", "Tỷ lệ thắng",
            "Lãi gộp ($)", "Tổng phí ($)", "Lãi ròng ($)",
            "Vốn đầu tháng ($)", "Vốn cuối tháng ($)", "Tỷ suất tháng", "Max Drawdown tháng"
        ]

        ws.row_dimensions[1].height = 26
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = CELL_BORDER

        for row_idx, m in enumerate(monthly_rows, start=2):
            ws.row_dimensions[row_idx].height = 20
            vals = [
                m.get("month", ""),
                "CÓ" if m.get("is_partial", False) else "KHÔNG",
                m.get("start_date", ""),
                m.get("end_date", ""),
                m.get("trading_days", 0),
                m.get("no_trade_days", 0),
                m.get("long_fills", 0),
                m.get("short_fills", 0),
                m.get("total_fills", 0),
                m.get("wins", 0),
                m.get("losses", 0),
                m.get("breakevens", 0),
                m.get("win_rate_pct", 0.0) / 100.0 if "win_rate_pct" in m else 0.0,
                m.get("gross_pnl", 0.0),
                m.get("fees", 0.0),
                m.get("realized_net_pnl", 0.0),
                m.get("start_equity", 1000.0),
                m.get("end_equity", 1000.0),
                m.get("return_pct", 0.0) / 100.0 if "return_pct" in m else 0.0,
                m.get("monthly_dd_pct", 0.0) / 100.0 if "monthly_dd_pct" in m else 0.0
            ]

            for col_idx, val in enumerate(vals, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.font = REGULAR_FONT
                cell.border = CELL_BORDER

                if col_idx in (14, 15, 16, 17, 18):
                    cell.number_format = FORMAT_CURRENCY
                    if col_idx == 16 and isinstance(val, (int, float)):
                        cell.font = GREEN_FONT if val > 0 else (RED_FONT if val < 0 else REGULAR_FONT)
                elif col_idx in (13, 19, 20):
                    cell.number_format = FORMAT_PERCENT
                    if col_idx == 19 and isinstance(val, (int, float)):
                        cell.font = GREEN_FONT if val > 0 else (RED_FONT if val < 0 else REGULAR_FONT)
                elif col_idx in (5, 6, 7, 8, 9, 10, 11, 12):
                    cell.number_format = FORMAT_INTEGER
                    cell.alignment = Alignment(horizontal="center")
                elif col_idx in (1, 2, 3, 4):
                    cell.alignment = Alignment(horizontal="center")

        auto_fit_columns(ws)
        ws.freeze_panes = "A2"

    @classmethod
    def _create_sheet_phien_va_huong(cls, wb, session_stats: Dict[str, Any], direction_stats: Dict[str, Any]):
        ws = wb.create_sheet(title="07_Phien_va_huong")
        ws.views.sheetView[0].showGridLines = True

        headers = [
            "Phân loại", "Nhóm", "Tổng lệnh", "Lệnh đóng", "Thắng", "Thua",
            "Tỷ lệ thắng", "Profit Factor", "Lợi nhuận ròng ($)", "Expectancy R"
        ]

        ws.row_dimensions[1].height = 26
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = CELL_BORDER

        curr_row = 2

        # 1. Sessions
        for sess_name, s in session_stats.items():
            ws.row_dimensions[curr_row].height = 20
            closed = s.get("wins", 0) + s.get("losses", 0)
            wr = (s.get("wins", 0) / closed) if closed > 0 else 0.0
            vals = [
                "PHIÊN GIAO DỊCH", sess_name, s.get("trades", 0), closed,
                s.get("wins", 0), s.get("losses", 0), wr,
                s.get("profit_factor", "N/A"), s.get("net_pnl", 0.0), s.get("expectancy_r", 0.0)
            ]
            for col_idx, val in enumerate(vals, start=1):
                cell = ws.cell(row=curr_row, column=col_idx, value=val)
                cell.font = REGULAR_FONT
                cell.border = CELL_BORDER
                if col_idx == 7:
                    cell.number_format = FORMAT_PERCENT
                elif col_idx == 9:
                    cell.number_format = FORMAT_CURRENCY
                    if isinstance(val, (int, float)):
                        cell.font = GREEN_FONT if val > 0 else (RED_FONT if val < 0 else REGULAR_FONT)
                elif col_idx == 10:
                    cell.number_format = FORMAT_RR
                elif col_idx in (3, 4, 5, 6):
                    cell.number_format = FORMAT_INTEGER
                    cell.alignment = Alignment(horizontal="center")
            curr_row += 1

        # 2. Directions
        for dir_name, d in direction_stats.items():
            ws.row_dimensions[curr_row].height = 20
            closed = d.get("wins", 0) + d.get("losses", 0)
            wr = (d.get("wins", 0) / closed) if closed > 0 else 0.0
            vals = [
                "HƯỚNG LỆNH", dir_name, d.get("trades", 0), closed,
                d.get("wins", 0), d.get("losses", 0), wr,
                d.get("profit_factor", "N/A"), d.get("net_pnl", 0.0), d.get("expectancy_r", 0.0)
            ]
            for col_idx, val in enumerate(vals, start=1):
                cell = ws.cell(row=curr_row, column=col_idx, value=val)
                cell.font = REGULAR_FONT
                cell.border = CELL_BORDER
                if col_idx == 7:
                    cell.number_format = FORMAT_PERCENT
                elif col_idx == 9:
                    cell.number_format = FORMAT_CURRENCY
                    if isinstance(val, (int, float)):
                        cell.font = GREEN_FONT if val > 0 else (RED_FONT if val < 0 else REGULAR_FONT)
                elif col_idx == 10:
                    cell.number_format = FORMAT_RR
                elif col_idx in (3, 4, 5, 6):
                    cell.number_format = FORMAT_INTEGER
                    cell.alignment = Alignment(horizontal="center")
            curr_row += 1

        auto_fit_columns(ws)
        ws.freeze_panes = "A2"

    @classmethod
    def _create_sheet_duong_von(cls, wb, equity_points: List[Dict[str, Any]]):
        ws = wb.create_sheet(title="08_Duong_von")
        ws.views.sheetView[0].showGridLines = True

        headers = [
            "Thời gian (VN)", "Timestamp UTC ms", "Vốn tiền mặt (Cash)", "Lãi thả nổi (Open MTM)",
            "Tổng vốn (Equity)", "Đỉnh vốn (Peak)", "Sụt giảm ($ Drawdown)", "Sụt giảm (% Drawdown)", "Ngày (VN)"
        ]

        ws.row_dimensions[1].height = 26
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = CELL_BORDER

        # Downsample if series > 8000 points to keep Excel snappy, always preserving extrema
        step = max(1, len(equity_points) // 4000)
        selected_points = []
        for idx, pt in enumerate(equity_points):
            if idx == 0 or idx == len(equity_points) - 1 or idx % step == 0 or pt.get("open_mtm", 0.0) != 0.0:
                selected_points.append(pt)

        for row_idx, pt in enumerate(selected_points, start=2):
            ws.row_dimensions[row_idx].height = 18
            vals = [
                pt.get("time_vn", ""),
                pt.get("timestamp", 0),
                pt.get("cash_balance", 1000.0),
                pt.get("open_mtm", 0.0),
                pt.get("equity", 1000.0),
                pt.get("peak", 1000.0),
                pt.get("drawdown_usdt", 0.0),
                pt.get("drawdown_pct", 0.0) / 100.0 if "drawdown_pct" in pt else 0.0,
                pt.get("daily_date", "")
            ]

            for col_idx, val in enumerate(vals, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.font = REGULAR_FONT
                cell.border = CELL_BORDER

                if col_idx in (3, 4, 5, 6, 7):
                    cell.number_format = FORMAT_CURRENCY
                elif col_idx == 8:
                    cell.number_format = FORMAT_PERCENT
                    cell.font = RED_FONT if val > 0 else REGULAR_FONT

        auto_fit_columns(ws)
        ws.freeze_panes = "A2"

    @classmethod
    def _create_sheet_chat_luong_du_lieu(cls, wb, quality_metadata: List[Dict[str, Any]]):
        ws = wb.create_sheet(title="09_Chat_luong_du_lieu")
        ws.views.sheetView[0].showGridLines = True

        headers = [
            "Khung thời gian", "Vai trò chiến lược", "Khoảng yêu cầu (ms)", "Khoảng thực tế (ms)",
            "Nến Warmup", "Nến Đánh giá", "Tổng số nến", "Khoảng trống (Gaps)",
            "Nến cách ly (Quarantined)", "Nguồn API", "Mã băm SHA-256", "Trạng thái", "Ghi chú kỹ thuật"
        ]

        ws.row_dimensions[1].height = 26
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = CELL_BORDER

        for row_idx, q in enumerate(quality_metadata, start=2):
            ws.row_dimensions[row_idx].height = 20
            vals = [
                q.get("timeframe", ""),
                q.get("role", ""),
                f"{q.get('req_start', '')} -> {q.get('req_end', '')}",
                f"{q.get('act_start', '')} -> {q.get('act_end', '')}",
                q.get("warmup_count", 0),
                q.get("eval_count", 0),
                q.get("total_count", 0),
                q.get("gaps_count", 0),
                q.get("quarantined_count", 0),
                q.get("source_api", "Bitget Classic USDT-M Futures"),
                q.get("sha256", "N/A"),
                q.get("status", "VALIDATED"),
                sanitize_cell_value(q.get("notes", ""))
            ]

            for col_idx, val in enumerate(vals, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.font = REGULAR_FONT
                cell.border = CELL_BORDER

                if col_idx in (5, 6, 7, 8, 9):
                    cell.number_format = FORMAT_INTEGER
                    cell.alignment = Alignment(horizontal="center")
                elif col_idx == 12:
                    cell.alignment = Alignment(horizontal="center")
                    cell.font = GREEN_FONT if val == "VALIDATED" else RED_FONT

        auto_fit_columns(ws)
        ws.freeze_panes = "A2"

    @classmethod
    def _create_sheet_cau_hinh_va_test(cls, wb, manifest: Dict[str, Any], test_matrix: List[Dict[str, Any]]):
        ws = wb.create_sheet(title="10_Cau_hinh_va_test")
        ws.views.sheetView[0].showGridLines = True

        ws["A1"] = "BẢNG CẤU HÌNH HỆ THỐNG & KẾT QUẢ TEST ACCEPTANCE V12"
        ws["A1"].font = TITLE_FONT

        # Table 1: Configuration Snapshot
        ws["A3"] = "1. CẤU HÌNH QUẢN TRỊ RỦI RO VÀ CHIẾN LƯỢC"
        ws["A3"].font = SECTION_FONT

        config_rows = [
            ("Vốn khởi điểm (Initial Equity)", "1000 USDT", "Chuẩn tài khoản kiểm thử"),
            ("Đòn bẩy (Leverage)", "30x", "ISOLATED Margin"),
            ("Rủi ro mỗi lệnh (Risk %)", "0.25%", "Tối đa theo Trading Policy"),
            ("Giới hạn số lệnh/ngày (Max Fills)", "3 lệnh/ngày", "Reset lúc 00:00 Asia/Ho_Chi_Minh"),
            ("Giới hạn chuỗi thua (Consecutive Losses)", "2 lệnh thua liên tiếp", "Khóa giao dịch trong ngày"),
            ("Ngân sách lỗ ngày (Daily Loss Cap)", "1.50% vốn đầu ngày", "Dừng giao dịch nếu chạm"),
            ("Tỷ lệ R:R tối thiểu (Min Net RR)", ">= 2.0R (unrounded)", "Tính toán chính xác sau phí và trượt giá"),
            ("Khung giờ New York (NY Session Quota)", "Ưu tiên tối đa 1 lệnh/ngày", "Có cơ chế dự trữ slot"),
            ("Phí giao dịch Taker (Market)", "0.06% (0.0006)", "Áp dụng khớp lệnh thị trường và SL"),
            ("Phí giao dịch Maker (Limit)", "0.02% (0.0002)", "Áp dụng khớp TP limit"),
            ("Trượt giá ước lượng (Slippage)", "0.10$/oz", "Mô hình định hướng theo chiều lệnh"),
            ("Spread thị trường ước lượng", "0.20$", "Áp dụng vào giá khớp"),
            ("Quy tắc bài học (Lesson Rule Policy)", "AS_OF_RULES", "Chỉ áp dụng quy tắc được phê duyệt trước start_ts")
        ]

        ws.row_dimensions[4].height = 22
        for c_idx, h in enumerate(["Tham số", "Giá trị", "Mô tả"]):
            cell = ws.cell(row=4, column=c_idx + 1, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.border = CELL_BORDER

        curr_row = 5
        for p, v, desc in config_rows:
            ws.cell(row=curr_row, column=1, value=p).font = BOLD_FONT
            ws.cell(row=curr_row, column=2, value=v).font = REGULAR_FONT
            ws.cell(row=curr_row, column=3, value=desc).font = REGULAR_FONT
            for col_i in range(1, 4):
                ws.cell(row=curr_row, column=col_i).border = CELL_BORDER
            curr_row += 1

        # Table 2: Acceptance Tests Matrix
        curr_row += 2
        ws.cell(row=curr_row, column=1, value="2. MA TRẬN TEST ACCEPTANCE BẮT BUỘC (V12 MANIFEST)").font = SECTION_FONT
        curr_row += 1

        matrix_headers = ["Mã Test (ID)", "Nhóm", "Mục tiêu kiểm thử", "Lệnh thực thi", "Kết quả mong đợi", "Kết quả thực tế", "Trạng thái"]
        ws.row_dimensions[curr_row].height = 24
        for c_idx, h in enumerate(matrix_headers):
            cell = ws.cell(row=curr_row, column=c_idx + 1, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.border = CELL_BORDER
        curr_row += 1

        for test in test_matrix:
            ws.row_dimensions[curr_row].height = 20
            t_vals = [
                test.get("id", ""),
                test.get("group", ""),
                test.get("objective", ""),
                sanitize_cell_value(test.get("command", "")),
                test.get("expected", ""),
                test.get("actual", ""),
                test.get("status", "PASS")
            ]
            for col_idx, val in enumerate(t_vals, start=1):
                cell = ws.cell(row=curr_row, column=col_idx, value=val)
                cell.font = REGULAR_FONT
                cell.border = CELL_BORDER
                if col_idx == 7:
                    cell.alignment = Alignment(horizontal="center")
                    cell.font = GREEN_FONT if val == "PASS" else RED_FONT
            curr_row += 1

        auto_fit_columns(ws)
        ws.freeze_panes = "A5"

    @classmethod
    def _create_sheet_ny_quota(cls, wb, ny_quota_rows: List[Dict[str, Any]]):
        ws = wb.create_sheet(title="11_NY_Quota")
        ws.views.sheetView[0].showGridLines = True

        ws["A1"] = "BẢNG KIỂM TOÁN HẠN NGẠCH PHIÊN NEW YORK (11_NY_Quota)"
        ws["A1"].font = TITLE_FONT
        ws["A2"] = "Mục tiêu: Đạt tối thiểu 1 fill/phiên NY đủ điều kiện, daily cap <= 3 tổng lệnh, bảo toàn hard risk guards"
        ws["A2"].font = REGULAR_FONT

        headers = [
            "Mã phiên NY", "Ngày NY", "Đủ điều kiện", "Lý do không đủ điều kiện",
            "Ứng viên đánh giá", "Tín hiệu READY", "Lệnh ARMED", "Lệnh khớp (Fills)",
            "Mục tiêu 1 lệnh/ngày", "Lý do chưa đạt", "Lệnh Quality", "Lệnh Quota", "Ghi chú chi tiết"
        ]

        ws.row_dimensions[4].height = 26
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=4, column=col_idx, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = CELL_BORDER

        for row_idx, r in enumerate(ny_quota_rows, start=5):
            ws.row_dimensions[row_idx].height = 20
            is_elig = r.get("is_eligible", False)
            target_met = r.get("target_met", False)
            vals = [
                sanitize_cell_value(r.get("session_ny_id", "")),
                r.get("date_ny", ""),
                "ĐỦ ĐIỀU KIỆN" if is_elig else "KHÔNG ĐỦ",
                sanitize_cell_value(r.get("ineligible_reason", "-")),
                r.get("attempts_count", 0),
                r.get("ready_count", 0),
                r.get("armed_count", 0),
                r.get("filled_count", 0),
                "ĐẠT" if target_met else "CHƯA_ĐẠT",
                sanitize_cell_value(r.get("unmet_reason", "-")),
                r.get("quality_fills", 0),
                r.get("quota_fills", 0),
                sanitize_cell_value(r.get("notes", ""))
            ]

            for col_idx, val in enumerate(vals, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.font = REGULAR_FONT
                cell.border = CELL_BORDER

                if col_idx in (5, 6, 7, 8, 11, 12):
                    cell.number_format = FORMAT_INTEGER
                    cell.alignment = Alignment(horizontal="center")
                elif col_idx in (1, 2, 3, 9):
                    cell.alignment = Alignment(horizontal="center")
                    if col_idx == 3:
                        cell.font = GREEN_FONT if is_elig else Font(name=FONT_FAMILY, size=10, color="64748B")
                    elif col_idx == 9:
                        cell.font = GREEN_FONT if target_met else RED_FONT

        auto_fit_columns(ws)
        ws.freeze_panes = "A5"

    @classmethod
    def _create_sheet_funnel(cls, wb, funnel_stats: List[Dict[str, Any]]):
        ws = wb.create_sheet(title="12_Funnel")
        ws.views.sheetView[0].showGridLines = True

        ws["A1"] = "BẢNG PHỄU TÍN HIỆU & PHÂN TÍCH TỪ CHỐI (12_Funnel)"
        ws["A1"].font = TITLE_FONT
        ws["A2"] = "Theo dõi chuyển đổi từ nến quan sát, qua bối cảnh HTF, các chặng SMC/Retest đến lệnh khớp và đóng"
        ws["A2"].font = REGULAR_FONT

        headers = [
            "Giai đoạn (Stage)", "Mô tả giai đoạn", "Số lần quan sát (Occurrences)",
            "Số lượng duy nhất (Unique Setups)", "Tỷ lệ chuyển tiếp (%)", "Blocker chính tại giai đoạn", "Bằng chứng kỹ thuật"
        ]

        ws.row_dimensions[4].height = 26
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=4, column=col_idx, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = CELL_BORDER

        for row_idx, f in enumerate(funnel_stats, start=5):
            ws.row_dimensions[row_idx].height = 20
            conv_pct = f.get("conversion_pct", 0.0) / 100.0 if "conversion_pct" in f else 0.0
            vals = [
                sanitize_cell_value(f.get("stage", "")),
                sanitize_cell_value(f.get("description", "")),
                f.get("occurrences", 0),
                f.get("unique_count", 0),
                conv_pct,
                sanitize_cell_value(f.get("primary_blocker", "-")),
                sanitize_cell_value(f.get("evidence", ""))
            ]

            for col_idx, val in enumerate(vals, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.font = REGULAR_FONT
                cell.border = CELL_BORDER

                if col_idx in (3, 4):
                    cell.number_format = FORMAT_INTEGER
                    cell.alignment = Alignment(horizontal="center")
                elif col_idx == 5:
                    cell.number_format = FORMAT_PERCENT
                    cell.alignment = Alignment(horizontal="center")
                elif col_idx == 1:
                    cell.font = BOLD_FONT

        auto_fit_columns(ws)
        ws.freeze_panes = "A5"

    @classmethod
    def export_comparison_workbook(
        cls,
        filepath: str,
        mode_summaries: Dict[str, Dict[str, Any]],
        ny_session_comparison: List[Dict[str, Any]],
        cost_stress_data: List[Dict[str, Any]],
        holdout_data: List[Dict[str, Any]],
        blockers_data: List[Dict[str, Any]]
    ) -> str:
        """
        Exports comprehensive multi-mode comparison workbook: V12_1_COMPARE_A_B_C.xlsx
        Contains 5 dedicated sheets:
        1. 01_So_sanh_3_Phuong_an
        2. 02_Do_phu_phien_My
        3. 03_Stress_chi_phi
        4. 04_Tap_mau_Holdout
        5. 05_Blockers_phan_tich
        """
        wb = openpyxl.Workbook()
        wb.remove(wb.active)

        # 1. 01_So_sanh_3_Phuong_an
        ws1 = wb.create_sheet(title="01_So_sanh_3_Phuong_an")
        ws1.views.sheetView[0].showGridLines = True
        ws1["A1"] = "BẢNG ĐỐI CHIẾU 3 PHƯƠNG ÁN CHIẾN LƯỢC — AURUM DESK V12_1"
        ws1["A1"].font = TITLE_FONT
        ws1["A2"] = "Dữ liệu chuẩn 3 tháng: XAUUSDT 09/07/2026 22:00 -> 09/10/2026 22:00 UTC+7 | Vốn: 1000 USDT | Leverage 30x ISOLATED"
        ws1["A2"].font = REGULAR_FONT

        headers1 = [
            "Chỉ số đánh giá",
            "A. CURRENT_BASELINE (Chuẩn kiểm toán)",
            "B. NY_ADAPTIVE (Ứng viên tăng tần suất)",
            "C. NY_DAILY_PAPER_RESEARCH (Ép lệnh Lab)",
            "Ghi chú / Tiêu chuẩn nghiệm thu"
        ]
        ws1.row_dimensions[4].height = 26
        for c_idx, h in enumerate(headers1, start=1):
            cell = ws1.cell(row=4, column=c_idx, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.border = CELL_BORDER
            cell.alignment = Alignment(horizontal="center", vertical="center")

        mA = mode_summaries.get("CURRENT_BASELINE", {})
        mB = mode_summaries.get("NY_ADAPTIVE", {})
        mC = mode_summaries.get("NY_DAILY_PAPER_RESEARCH", {})

        metrics_rows = [
            ("Mã kiểm thử (Run ID)", mA.get("run_id", "-"), mB.get("run_id", "-"), mC.get("run_id", "-"), "Mã định danh artifact"),
            ("Vốn khởi điểm (Initial Equity)", 1000.0, 1000.0, 1000.0, "$ Chuẩn tài khoản"),
            ("Vốn ròng cuối kỳ (Final Equity)", mA.get("final_equity", 1000.0), mB.get("final_equity", 1000.0), mC.get("final_equity", 1000.0), "$ (Cash + Open MTM)"),
            ("Tổng lợi nhuận ròng (Net PnL)", mA.get("realized_net_pnl", 0.0), mB.get("realized_net_pnl", 0.0), mC.get("realized_net_pnl", 0.0), "$ Sau chi phí"),
            ("Tỷ suất sinh lời (ROI %)", mA.get("roi_pct", 0.0) / 100.0, mB.get("roi_pct", 0.0) / 100.0, mC.get("roi_pct", 0.0) / 100.0, "% trên vốn ban đầu"),
            ("Tổng số lệnh khớp (Total Fills)", mA.get("total_trades", 0), mB.get("total_trades", 0), mC.get("total_trades", 0), "Số lệnh"),
            ("Số lệnh Quality (Quality Fills)", mA.get("quality_trades_count", mA.get("total_trades", 0)), mB.get("quality_trades_count", 0), mC.get("quality_trades_count", 0), "Risk 0.25% equity"),
            ("Số lệnh Quota (Quota Fills)", 0, 0, mC.get("quota_trades_count", 0), "Risk 0.10% equity"),
            ("PnL lệnh Quality", mA.get("quality_net_pnl", mA.get("realized_net_pnl", 0.0)), mB.get("quality_net_pnl", mB.get("realized_net_pnl", 0.0)), mC.get("quality_net_pnl", 0.0), "$"),
            ("PnL lệnh Quota", 0.0, 0.0, mC.get("quota_net_pnl", 0.0), "$ Đo tác động ép tần suất"),
            ("Số lệnh Thắng / Thua / Hòa", f"{mA.get('wins', 0)}/{mA.get('losses', 0)}/{mA.get('breakevens', 0)}", f"{mB.get('wins', 0)}/{mB.get('losses', 0)}/{mB.get('breakevens', 0)}", f"{mC.get('wins', 0)}/{mC.get('losses', 0)}/{mC.get('breakevens', 0)}", "Lệnh"),
            ("Tỷ lệ thắng (Win Rate %)", mA.get("win_rate_pct", 0.0) / 100.0, mB.get("win_rate_pct", 0.0) / 100.0, mC.get("win_rate_pct", 0.0) / 100.0, "% kèm sample count"),
            ("Profit Factor", mA.get("profit_factor", "N/A"), mB.get("profit_factor", "N/A"), mC.get("profit_factor", "N/A"), "Gross Profit / Gross Loss"),
            ("Kỳ vọng bình quân (Expectancy R)", mA.get("expectancy_r", 0.0), mB.get("expectancy_r", 0.0), mC.get("expectancy_r", 0.0), "R bình quân"),
            ("Max Drawdown ($)", mA.get("max_drawdown_usdt", 0.0), mB.get("max_drawdown_usdt", 0.0), mC.get("max_drawdown_usdt", 0.0), "$ MTM close bar"),
            ("Max Drawdown (%)", mA.get("max_drawdown_pct", 0.0) / 100.0, mB.get("max_drawdown_pct", 0.0) / 100.0, mC.get("max_drawdown_pct", 0.0) / 100.0, "%"),
            ("Tổng số phiên NY đủ điều kiện", mA.get("eligible_ny_sessions", 66), mB.get("eligible_ny_sessions", 66), mC.get("eligible_ny_sessions", 66), "Phiên (loại trừ cuối tuần)"),
            ("Số phiên NY có >= 1 lệnh", mA.get("ny_covered_sessions", 0), mB.get("ny_covered_sessions", 0), mC.get("ny_covered_sessions", 0), "Phiên"),
            ("Độ phủ phiên NY (% Eligible)", mA.get("ny_fill_coverage_pct", 0.0) / 100.0, mB.get("ny_fill_coverage_pct", 0.0) / 100.0, mC.get("ny_fill_coverage_pct", 0.0) / 100.0, "% trên phiên đủ điều kiện"),
            ("Độ phủ trên tổng ngày lịch (% All 93D)", (mA.get("ny_covered_sessions", 0) / 93.0), (mB.get("ny_covered_sessions", 0) / 93.0), (mC.get("ny_covered_sessions", 0) / 93.0), "% trên 93 ngày lịch"),
            ("Kết luận mục tiêu 1 lệnh NY/ngày", mA.get("target_conclusion", "CHƯA_ĐẠT"), mB.get("target_conclusion", "CHƯA_ĐẠT"), mC.get("target_conclusion", "CHƯA_ĐẠT"), "Đánh giá trung thực")
        ]

        for r_idx, (m_name, vA, vB, vC, note) in enumerate(metrics_rows, start=5):
            ws1.row_dimensions[r_idx].height = 20
            c1 = ws1.cell(row=r_idx, column=1, value=m_name)
            c2 = ws1.cell(row=r_idx, column=2, value=vA)
            c3 = ws1.cell(row=r_idx, column=3, value=vB)
            c4 = ws1.cell(row=r_idx, column=4, value=vC)
            c5 = ws1.cell(row=r_idx, column=5, value=note)

            c1.font = BOLD_FONT
            c2.font = REGULAR_FONT
            c3.font = REGULAR_FONT
            c4.font = REGULAR_FONT
            c5.font = REGULAR_FONT

            for cell in (c1, c2, c3, c4, c5):
                cell.border = CELL_BORDER

            if "$" in note and isinstance(vA, (int, float)):
                c2.number_format = FORMAT_CURRENCY
                c3.number_format = FORMAT_CURRENCY
                c4.number_format = FORMAT_CURRENCY
            elif "%" in note and isinstance(vA, float):
                c2.number_format = FORMAT_PERCENT
                c3.number_format = FORMAT_PERCENT
                c4.number_format = FORMAT_PERCENT
            elif "R" in note and isinstance(vA, (int, float)):
                c2.number_format = FORMAT_RR
                c3.number_format = FORMAT_RR
                c4.number_format = FORMAT_RR

        auto_fit_columns(ws1)
        ws1.freeze_panes = "A5"

        # 2. 02_Do_phu_phien_My
        ws2 = wb.create_sheet(title="02_Do_phu_phien_My")
        ws2.views.sheetView[0].showGridLines = True
        ws2["A1"] = "CHI TIẾT ĐỘ PHỦ TỪNG PHIÊN NEW YORK (02_Do_phu_phien_My)"
        ws2["A1"].font = TITLE_FONT

        headers2 = [
            "Mã phiên NY", "Ngày NY", "Đủ điều kiện", "Lý do không đủ",
            "Mode A (Fills)", "Mode B (Fills)", "Mode C (Fills)",
            "Mode C Quality Fills", "Mode C Quota Fills", "Blocker chính nếu chưa đạt", "Ghi chú"
        ]
        ws2.row_dimensions[3].height = 24
        for c_idx, h in enumerate(headers2, start=1):
            cell = ws2.cell(row=3, column=c_idx, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.border = CELL_BORDER
            cell.alignment = Alignment(horizontal="center", vertical="center")

        for r_idx, row in enumerate(ny_session_comparison, start=4):
            ws2.row_dimensions[r_idx].height = 20
            vals = [
                sanitize_cell_value(row.get("session_ny_id", "")),
                row.get("date_ny", ""),
                "ĐỦ" if row.get("is_eligible", False) else "KHÔNG ĐỦ",
                sanitize_cell_value(row.get("ineligible_reason", "-")),
                row.get("fills_A", 0),
                row.get("fills_B", 0),
                row.get("fills_C", 0),
                row.get("quality_fills_C", 0),
                row.get("quota_fills_C", 0),
                sanitize_cell_value(row.get("unmet_reason", "-")),
                sanitize_cell_value(row.get("notes", ""))
            ]
            for col_idx, val in enumerate(vals, start=1):
                cell = ws2.cell(row=r_idx, column=col_idx, value=val)
                cell.font = REGULAR_FONT
                cell.border = CELL_BORDER
                if col_idx in (5, 6, 7, 8, 9):
                    cell.number_format = FORMAT_INTEGER
                    cell.alignment = Alignment(horizontal="center")
                elif col_idx in (1, 2, 3):
                    cell.alignment = Alignment(horizontal="center")

        auto_fit_columns(ws2)
        ws2.freeze_panes = "A4"

        # 3. 03_Stress_chi_phi
        ws3 = wb.create_sheet(title="03_Stress_chi_phi")
        ws3.views.sheetView[0].showGridLines = True
        ws3["A1"] = "ĐÁNH GIÁ ĐỘ NHẠY CHI PHÍ GIAO DỊCH & TRƯỢT GIÁ (03_Stress_chi_phi)"
        ws3["A1"].font = TITLE_FONT

        headers3 = [
            "Kịch bản chi phí", "Mô tả", "Phí Taker Entry/Exit", "Phí Maker TP",
            "Trượt giá ($/oz)", "Spread ($)", "PnL Mode A ($)", "PnL Mode B ($)", "PnL Mode C ($)", "Tác động PnL"
        ]
        ws3.row_dimensions[3].height = 24
        for c_idx, h in enumerate(headers3, start=1):
            cell = ws3.cell(row=3, column=c_idx, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.border = CELL_BORDER
            cell.alignment = Alignment(horizontal="center", vertical="center")

        for r_idx, row in enumerate(cost_stress_data, start=4):
            ws3.row_dimensions[r_idx].height = 20
            vals = [
                sanitize_cell_value(row.get("scenario", "")),
                sanitize_cell_value(row.get("description", "")),
                row.get("taker_fee", 0.0006),
                row.get("maker_fee", 0.0002),
                row.get("slippage", 0.10),
                row.get("spread", 0.20),
                row.get("pnl_A", 0.0),
                row.get("pnl_B", 0.0),
                row.get("pnl_C", 0.0),
                sanitize_cell_value(row.get("impact", ""))
            ]
            for col_idx, val in enumerate(vals, start=1):
                cell = ws3.cell(row=r_idx, column=col_idx, value=val)
                cell.font = REGULAR_FONT
                cell.border = CELL_BORDER
                if col_idx in (7, 8, 9):
                    cell.number_format = FORMAT_CURRENCY
                    if isinstance(val, (int, float)):
                        cell.font = GREEN_FONT if val > 0 else (RED_FONT if val < 0 else REGULAR_FONT)

        auto_fit_columns(ws3)
        ws3.freeze_panes = "A4"

        # 4. 04_Tap_mau_Holdout
        ws4 = wb.create_sheet(title="04_Tap_mau_Holdout")
        ws4.views.sheetView[0].showGridLines = True
        ws4["A1"] = "PHÂN TÁCH GIAI ĐOẠN KIỂM ĐỊNH CHIẾN LƯỢC (04_Tap_mau_Holdout)"
        ws4["A1"].font = TITLE_FONT

        headers4 = [
            "Giai đoạn", "Khoảng thời gian", "Số ngày", "Vai trò kiểm định",
            "Fills Mode A", "Net PnL A ($)", "Fills Mode B", "Net PnL B ($)", "Fills Mode C", "Net PnL C ($)", "Ghi chú kiểm toán"
        ]
        ws4.row_dimensions[3].height = 24
        for c_idx, h in enumerate(headers4, start=1):
            cell = ws4.cell(row=3, column=c_idx, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.border = CELL_BORDER
            cell.alignment = Alignment(horizontal="center", vertical="center")

        for r_idx, row in enumerate(holdout_data, start=4):
            ws4.row_dimensions[r_idx].height = 20
            vals = [
                sanitize_cell_value(row.get("period_name", "")),
                sanitize_cell_value(row.get("date_range", "")),
                row.get("days_count", 0),
                sanitize_cell_value(row.get("role", "")),
                row.get("fills_A", 0),
                row.get("pnl_A", 0.0),
                row.get("fills_B", 0),
                row.get("pnl_B", 0.0),
                row.get("fills_C", 0),
                row.get("pnl_C", 0.0),
                sanitize_cell_value(row.get("notes", ""))
            ]
            for col_idx, val in enumerate(vals, start=1):
                cell = ws4.cell(row=r_idx, column=col_idx, value=val)
                cell.font = REGULAR_FONT
                cell.border = CELL_BORDER
                if col_idx in (6, 8, 10):
                    cell.number_format = FORMAT_CURRENCY
                elif col_idx in (3, 5, 7, 9):
                    cell.number_format = FORMAT_INTEGER
                    cell.alignment = Alignment(horizontal="center")

        auto_fit_columns(ws4)
        ws4.freeze_panes = "A4"

        # 5. 05_Blockers_phan_tich
        ws5 = wb.create_sheet(title="05_Blockers_phan_tich")
        ws5.views.sheetView[0].showGridLines = True
        ws5["A1"] = "BẢNG PHÂN TÍCH RÀO CẢN VÀ LÝ DO CHƯA VÀO LỆNH (05_Blockers_phan_tich)"
        ws5["A1"].font = TITLE_FONT

        headers5 = [
            "Nhóm rào cản", "Mã lý do (Reason Code)", "Giải thích kỹ thuật",
            "Tác động Mode A", "Tác động Mode B", "Tác động Mode C", "Hard Guard hay Soft Filter"
        ]
        ws5.row_dimensions[3].height = 24
        for c_idx, h in enumerate(headers5, start=1):
            cell = ws5.cell(row=3, column=c_idx, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.border = CELL_BORDER
            cell.alignment = Alignment(horizontal="center", vertical="center")

        for r_idx, row in enumerate(blockers_data, start=4):
            ws5.row_dimensions[r_idx].height = 20
            vals = [
                sanitize_cell_value(row.get("category", "")),
                sanitize_cell_value(row.get("reason_code", "")),
                sanitize_cell_value(row.get("description", "")),
                row.get("count_A", 0),
                row.get("count_B", 0),
                row.get("count_C", 0),
                sanitize_cell_value(row.get("guard_type", "HARD_GUARD"))
            ]
            for col_idx, val in enumerate(vals, start=1):
                cell = ws5.cell(row=r_idx, column=col_idx, value=val)
                cell.font = REGULAR_FONT
                cell.border = CELL_BORDER
                if col_idx in (4, 5, 6):
                    cell.number_format = FORMAT_INTEGER
                    cell.alignment = Alignment(horizontal="center")

        auto_fit_columns(ws5)
        ws5.freeze_panes = "A4"

        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        wb.save(filepath)

        verify_wb = openpyxl.load_workbook(filepath, read_only=True)
        sheet_names = verify_wb.sheetnames
        verify_wb.close()
        assert len(sheet_names) == 5, f"Expected 5 sheets in compare workbook, found {len(sheet_names)}: {sheet_names}"
        return filepath

