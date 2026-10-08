import time
import json
import random
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, Tuple, List
import httpx
from sqlalchemy.orm import Session
from sqlalchemy import case
from database import SessionLocal
import models

logger = logging.getLogger(__name__)
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

def escape_markdown(text: Any) -> str:
    """Escape Telegram Markdown special characters (*, _, `, [)."""
    if text is None:
        return "Chưa có dữ liệu"
    s = str(text)
    for ch in ("_", "*", "`", "["):
        s = s.replace(ch, f"\\{ch}")
    return s


def get_formatted_time(epoch_ms: Optional[int], timezone_str: str = "Asia/Ho_Chi_Minh") -> str:
    """Format epoch milliseconds to Vietnam local time string."""
    try:
        tz = ZoneInfo(timezone_str or "Asia/Ho_Chi_Minh")
        if epoch_ms:
            dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz)
        else:
            dt = datetime.now(tz)
        return dt.strftime("%H:%M:%S %d/%m/%Y")
    except Exception:
        return datetime.now().strftime("%H:%M:%S %d/%m/%Y")


def format_duration(start_ms: Optional[int], end_ms: Optional[int]) -> str:
    """Format duration between two timestamps."""
    if not start_ms or not end_ms or end_ms <= start_ms:
        return "Chưa có dữ liệu"
    diff_sec = int((end_ms - start_ms) / 1000)
    minutes = diff_sec // 60
    hours = minutes // 60
    rem_min = minutes % 60
    if hours > 0:
        return f"{hours}h {rem_min}m"
    return f"{minutes}m"


def format_telegram_message(
    item_type: str,
    data: Dict[str, Any],
    occurred_at: Optional[int] = None,
    timezone_str: str = "Asia/Ho_Chi_Minh",
    base_chart_url: Optional[str] = None
) -> str:
    """
    Format structured Vietnamese notification message for Telegram.
    Includes PAPER disclaimer and prevents confusing fake default values.
    """
    event_time_vn = get_formatted_time(occurred_at, timezone_str)

    if item_type == "NEAR_ENTRY":
        direction = data.get("direction", "Chưa có dữ liệu")
        setup_id = escape_markdown(data.get("setup_instance_id") or data.get("setup_id", "N/A"))
        ref_side = data.get("executable_side", "ASK" if direction == "LONG" else "BID")
        ref_p = data.get("reference_price", 0.0)
        z_low = data.get("entry_zone_low", 0.0)
        z_high = data.get("entry_zone_high", 0.0)
        target = data.get("entry_target", 0.0)
        dist_price = data.get("distance_price", 0.0)
        dist_atr = data.get("distance_atr", 0.0)
        atr = data.get("atr", 0.0)
        sl = data.get("stop_loss", 0.0)
        tp = data.get("take_profit", 0.0)
        net_rr = data.get("net_rr", 0.0)
        risk = data.get("risk_usdt", 0.0)
        auto_str = "Đang BẬT" if data.get("auto_paper_enabled") else "Đang TẮT"

        cond_met = data.get("conditions_met", [])
        cond_rem = data.get("conditions_remaining", [])
        met_str = "\n".join([f"  ✓ {escape_markdown(c)}" for c in cond_met[:3]]) if cond_met else "  ✓ Đang theo dõi cấu trúc"
        rem_str = "\n".join([f"  ⏳ {escape_markdown(c)}" for c in cond_rem[:2]]) if cond_rem else "  ⏳ Chờ nến xác nhận chạm vùng"

        return (
            f"🔔 *AURUM DESK — SẮP TIẾP CẬN VÙNG ENTRY (PAPER)*\n\n"
            f"• *Kế hoạch:* {direction} XAUUSDT\n"
            f"• *Setup Instance:* `{setup_id}`\n"
            f"• *Giá tham chiếu:* `${ref_p:.2f}` (Khớp theo {ref_side})\n"
            f"• *Vùng Entry:* `${z_low:.2f} — ${z_high:.2f}` (Mục tiêu: `${target:.2f}`)\n"
            f"• *Khoảng cách tới vùng:* `${dist_price:.2f} USDT` (`{dist_atr:.2f} ATR`)\n"
            f"• *ATR 15M xác nhận:* `${atr:.2f}`\n"
            f"• *SL dự kiến:* `${sl:.2f}` | *TP dự kiến:* `${tp:.2f}`\n"
            f"• *Net R:R:* `1:{net_rr:.2f}` | *Rủi ro tài khoản:* `${risk:.2f} USDT`\n"
            f"• *Điều kiện đã đạt:*\n{met_str}\n"
            f"• *Điều kiện còn thiếu:*\n{rem_str}\n"
            f"• *Auto Paper:* {auto_str}\n"
            f"• *Lưu ý:* Cảnh báo gần Entry chỉ để theo dõi, *KHÔNG* tự động vào lệnh nếu chưa đủ xác nhận.\n\n"
            f"⏱ _{event_time_vn} (UTC+7)_"
        )

    elif item_type == "READY":
        direction = data.get("direction", "Chưa có dữ liệu")
        entry = data.get("planned_entry", 0.0)
        sl = data.get("stop_loss", 0.0)
        tp = data.get("take_profit", 0.0)
        net_rr = data.get("net_rr", 0.0)
        risk = data.get("risk_usdt", 0.0)
        setup_id = escape_markdown(data.get("setup_id", "N/A"))
        poi = escape_markdown(data.get("poi", "Vùng FVG/Discount"))

        return (
            f"🔔 *AURUM DESK — TÍN HIỆU SẴN SÀNG (READY — PAPER)*\n\n"
            f"• *Setup ID:* `{setup_id}`\n"
            f"• *Kế hoạch:* {direction} XAUUSDT\n"
            f"• *Entry dự kiến:* `${entry:.2f}`\n"
            f"• *Stop Loss:* `${sl:.2f}`\n"
            f"• *Take Profit:* `${tp:.2f}`\n"
            f"• *Net R:R:* `1:{net_rr:.2f}` | *Rủi ro:* `${risk:.2f} USDT`\n"
            f"• *Vùng POI:* {poi}\n"
            f"• *Trạng thái:* Đã đủ chuỗi xác nhận SMC. Sẵn sàng Arm lệnh Paper.\n\n"
            f"⏱ _{event_time_vn} (UTC+7)_"
        )

    elif item_type == "ARMED":
        order_id = escape_markdown(data.get("order_id", "N/A"))
        setup_id = escape_markdown(data.get("setup_id", "N/A"))
        direction = data.get("direction", "Chưa có dữ liệu")
        order_type = data.get("order_type", "MARKET")
        entry = data.get("planned_entry", 0.0)
        sl = data.get("stop_loss", 0.0)
        tp = data.get("take_profit", 0.0)
        net_rr = data.get("net_rr", 0.0)

        trigger_desc = (
            "Lệnh thị trường (MARKET): Chờ execution guards để khớp ngay theo giá thị trường."
            if order_type == "MARKET"
            else f"Lệnh chờ giới hạn (LIMIT): Chờ nến chạm mức ${entry:.2f} để khớp."
        )

        return (
            f"🎯 *AURUM DESK — ĐÃ ARM LỆNH CHỜ (PAPER)*\n\n"
            f"• *Mã lệnh:* `{order_id}`\n"
            f"• *Setup liên kết:* `{setup_id}`\n"
            f"• *Hướng:* {direction} XAUUSDT\n"
            f"• *Loại lệnh:* {order_type}\n"
            f"• *Điều kiện kích hoạt:* {trigger_desc}\n"
            f"• *Giá Entry dự kiến:* `${entry:.2f}`\n"
            f"• *Stop Loss:* `${sl:.2f}` | *Take Profit:* `${tp:.2f}`\n"
            f"• *Net R:R dự kiến:* `1:{net_rr:.2f}`\n\n"
            f"⏱ _{event_time_vn} (UTC+7)_"
        )

    elif item_type == "FILLED":
        trade_id = escape_markdown(data.get("trade_id", "N/A"))
        source = escape_markdown(data.get("source", "AUTO"))
        direction = data.get("direction", "Chưa có dữ liệu")
        entry = data.get("actual_entry", 0.0)
        planned = data.get("planned_entry", entry)
        qty = data.get("quantity", 0.0)
        sl = data.get("stop_loss", 0.0)
        tp = data.get("take_profit", 0.0)
        net_rr = data.get("estimated_net_rr", 0.0)
        risk = data.get("initial_risk_usdt", 0.0)
        lev = data.get("leverage", 5)
        margin_mode = data.get("margin_mode", "ISOLATED")
        margin = data.get("initial_margin", 0.0)

        chart_line = f"\n• *Biểu đồ:* {base_chart_url}" if base_chart_url else ""

        return (
            f"⚡ *AURUM DESK — ĐÃ KHỚP LỆNH (FILLED — PAPER)*\n\n"
            f"• *Mã vị thế:* `{trade_id}` ({source})\n"
            f"• *Vị thế:* {direction} XAUUSDT\n"
            f"• *Giá khớp thực tế:* `${entry:.2f}` (Dự kiến: `${planned:.2f}`)\n"
            f"• *Khối lượng:* `{qty:.2f} oz` ({lev}x {margin_mode}, Ký quỹ: `${margin:.2f}`)\n"
            f"• *Stop Loss:* `${sl:.2f}`\n"
            f"• *Take Profit:* `${tp:.2f}`\n"
            f"• *Rủi ro thực tế:* `${risk:.2f} USDT` (Net R:R: `1:{net_rr:.2f}`){chart_line}\n\n"
            f"⏱ _{event_time_vn} (UTC+7)_"
        )

    elif item_type == "TP_HIT":
        trade_id = escape_markdown(data.get("trade_id", "N/A"))
        direction = data.get("direction", "Chưa có dữ liệu")
        entry = data.get("actual_entry", 0.0)
        exit_p = data.get("actual_exit", 0.0)
        pnl = data.get("realized_pnl", 0.0)
        r = data.get("realized_r", 0.0)
        opened_at = data.get("opened_at")
        closed_at = data.get("closed_at", occurred_at)
        duration_str = format_duration(opened_at, closed_at)

        return (
            f"🏆 *AURUM DESK — CHỐT LỜI TP (PAPER)*\n\n"
            f"• *Mã lệnh:* `{trade_id}` ({direction})\n"
            f"• *Giá khớp vào:* `${entry:.2f}`\n"
            f"• *Giá chốt lời (TP):* `${exit_p:.2f}`\n"
            f"• *Kết quả PnL:* `+${pnl:.2f} USDT` (+{r:.2f}R)\n"
            f"• *Thời gian giữ lệnh:* `{duration_str}`\n"
            f"• *Nhật ký:* Đã tự động ghi nhận bài học thành công vào Journal.\n\n"
            f"⏱ _{event_time_vn} (UTC+7)_"
        )

    elif item_type == "SL_HIT":
        trade_id = escape_markdown(data.get("trade_id", "N/A"))
        direction = data.get("direction", "Chưa có dữ liệu")
        entry = data.get("actual_entry", 0.0)
        exit_p = data.get("actual_exit", 0.0)
        pnl = data.get("realized_pnl", 0.0)
        r = data.get("realized_r", 0.0)
        cause = data.get("exit_cause", "SL_HIT")
        opened_at = data.get("opened_at")
        closed_at = data.get("closed_at", occurred_at)
        duration_str = format_duration(opened_at, closed_at)

        ambiguous_note = ""
        if cause == "AMBIGUOUS_BAR_SL_FIRST":
            ambiguous_note = "\n• *Lưu ý nến mơ hồ:* Cùng một nến chạm cả TP và SL; hệ thống áp dụng nguyên tắc thận trọng chọn SL trước."

        return (
            f"🛑 *AURUM DESK — CẮT LỖ SL (PAPER)*\n\n"
            f"• *Mã lệnh:* `{trade_id}` ({direction})\n"
            f"• *Giá khớp vào:* `${entry:.2f}`\n"
            f"• *Giá thoát (SL):* `${exit_p:.2f}`\n"
            f"• *Kết quả PnL:* `-${abs(pnl):.2f} USDT` ({r:.2f}R)\n"
            f"• *Nguyên nhân:* `{cause}`{ambiguous_note}\n"
            f"• *Thời gian giữ lệnh:* `{duration_str}`\n"
            f"• *Bảo vệ:* Kích hoạt thời gian nghỉ (Cooldown) để bảo vệ tài khoản.\n\n"
            f"⏱ _{event_time_vn} (UTC+7)_"
        )

    elif item_type == "MANUAL_CLOSED":
        trade_id = escape_markdown(data.get("trade_id", "N/A"))
        direction = data.get("direction", "Chưa có dữ liệu")
        exit_p = data.get("actual_exit", 0.0)
        pnl = data.get("realized_pnl", 0.0)
        r = data.get("realized_r", 0.0)
        sign = "+" if pnl >= 0 else ""

        return (
            f"🏁 *AURUM DESK — ĐÓNG VỊ THẾ THỦ CÔNG (PAPER)*\n\n"
            f"• *Mã lệnh:* `{trade_id}` ({direction})\n"
            f"• *Giá đóng:* `${exit_p:.2f}`\n"
            f"• *Kết quả PnL:* `{sign}${pnl:.2f} USDT` ({sign}{r:.2f}R)\n"
            f"• *Thao tác:* Đóng chủ động từ Dashboard của người dùng.\n\n"
            f"⏱ _{event_time_vn} (UTC+7)_"
        )

    elif item_type == "LIQUIDATED":
        trade_id = escape_markdown(data.get("trade_id", "N/A"))
        direction = data.get("direction", "Chưa có dữ liệu")
        exit_p = data.get("actual_exit", 0.0)
        pnl = data.get("realized_pnl", 0.0)

        return (
            f"💀 *AURUM DESK — THANH LÝ VỊ THẾ (LIQUIDATED — PAPER)*\n\n"
            f"• *Mã lệnh:* `{trade_id}` ({direction})\n"
            f"• *Giá thanh lý:* `${exit_p:.2f}`\n"
            f"• *Realized PnL:* `-${abs(pnl):.2f} USDT`\n"
            f"• *Cảnh báo:* Vị thế chạm mức thanh lý ký quỹ Isolated. Cần kiểm tra lại đòn bẩy và buffer SL.\n\n"
            f"⏱ _{event_time_vn} (UTC+7)_"
        )

    elif item_type == "REJECTED":
        order_id = escape_markdown(data.get("order_id", "N/A"))
        reason = escape_markdown(data.get("reason", "Không đạt kiểm tra rủi ro"))

        return (
            f"🚫 *AURUM DESK — LỆNH BỊ TỪ CHỐI (REJECTED — PAPER)*\n\n"
            f"• *Mã lệnh:* `{order_id}`\n"
            f"• *Lý do từ chối:* {reason}\n"
            f"• *Bảo vệ:* Không tăng số lần fill lệnh trong ngày.\n\n"
            f"⏱ _{event_time_vn} (UTC+7)_"
        )

    elif item_type in ("INVALIDATED", "EXPIRED"):
        setup_id = escape_markdown(data.get("setup_id", "N/A"))
        reason = escape_markdown(data.get("reason", "Cấu trúc bị phá vỡ hoặc hết hạn thời gian"))

        return (
            f"❌ *AURUM DESK — HỦY THIẾT LẬP ({item_type} — PAPER)*\n\n"
            f"• *Setup ID:* `{setup_id}`\n"
            f"• *Lý do:* {reason}\n"
            f"• *Trạng thái:* Chuyển về danh sách theo dõi tiếp.\n\n"
            f"⏱ _{event_time_vn} (UTC+7)_"
        )

    elif item_type == "FEED_DOWN":
        reason = escape_markdown(data.get("reason", "Mất kết nối API sàn hoặc nến bị trễ"))
        return (
            f"⚠️ *AURUM DESK — CẢNH BÁO NGUỒN DỮ LIỆU BỊ TRỄ*\n\n"
            f"• *Tình trạng:* {reason}\n"
            f"• *Hành động an toàn:* Khóa mở lệnh mới cho tới khi dữ liệu nến ổn định lại.\n\n"
            f"⏱ _{event_time_vn} (UTC+7)_"
        )

    elif item_type == "RECOVERED":
        return (
            f"✅ *AURUM DESK — NGUỒN DỮ LIỆU ĐÃ PHỤC HỒI*\n\n"
            f"• *Trạng thái:* Nguồn cấp giá và nến đã hoạt động bình thường trở lại.\n\n"
            f"⏱ _{event_time_vn} (UTC+7)_"
        )

    # Legacy CLOSED fallback
    elif item_type == "CLOSED":
        trade_id = escape_markdown(data.get("trade_id", "N/A"))
        direction = data.get("direction", "Chưa có dữ liệu")
        exit_p = data.get("actual_exit", 0.0)
        pnl = data.get("realized_pnl", 0.0)
        r = data.get("realized_r", 0.0)
        cause = escape_markdown(data.get("exit_cause", "CLOSE"))
        sign = "+" if pnl >= 0 else ""

        return (
            f"🏁 *AURUM DESK — ĐÃ ĐÓNG VỊ THẾ ({cause} — PAPER)*\n\n"
            f"• *Mã lệnh:* `{trade_id}` ({direction})\n"
            f"• *Giá thoát:* `${exit_p:.2f}`\n"
            f"• *Kết quả PnL:* `{sign}${pnl:.2f} USDT` *({sign}{r:.2f}R)*\n"
            f"• *Nguyên nhân:* `{cause}`\n\n"
            f"⏱ _{event_time_vn} (UTC+7)_"
        )

    return f"ℹ️ *AURUM DESK — THÔNG BÁO ({item_type} — PAPER)*\n\n⏱ _{event_time_vn}_"


def normalize_hh_mm(val: Optional[str], default: str = "00:00") -> str:
    """Normalize time string to canonical HH:MM (00:00 - 23:59). Handles legacy 24:00 by normalizing to 00:00."""
    if not val:
        return default
    val = val.strip()
    if val in ("24:00", "24:0"):
        logger.warning("Normalizing legacy 24:00 quiet hours time to 00:00")
        return "00:00"
    parts = val.split(":")
    if len(parts) == 2:
        try:
            h, m = int(parts[0]), int(parts[1])
            if h == 24 and m == 0:
                logger.warning("Normalizing legacy 24:00 quiet hours time to 00:00")
                return "00:00"
            if 0 <= h <= 23 and 0 <= m <= 59:
                return f"{h:02d}:{m:02d}"
        except ValueError:
            pass
    return default


def is_within_quiet_hours(config: models.TelegramConfig) -> bool:
    """Check if current time is within configured quiet hours."""
    if not config.quiet_hours_enabled:
        return False

    try:
        tz = ZoneInfo(config.timezone or "Asia/Ho_Chi_Minh")
        now_dt = datetime.now(tz)
        current_time_str = now_dt.strftime("%H:%M")

        start = normalize_hh_mm(config.quiet_hours_start, "23:00")
        end = normalize_hh_mm(config.quiet_hours_end, "06:00")

        if start <= end:
            return start <= current_time_str <= end
        else:
            # Over midnight (e.g. 23:00 to 06:00)
            return current_time_str >= start or current_time_str <= end
    except Exception:
        return False


@dataclass
class TelegramSendResult:
    success: bool
    provider_message_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    retry_after_sec: Optional[int] = None
    http_status: Optional[int] = None

    # Unpack support: works with 4-tuple unpack (success, provider_message_id, error_message, retry_after_sec)
    def __iter__(self):
        return iter((self.success, self.provider_message_id, self.error_message, self.retry_after_sec))

    def __getitem__(self, index):
        items = (self.success, self.provider_message_id, self.error_message, self.retry_after_sec)
        return items[index]

    def __len__(self):
        return 4


async def send_telegram_direct(
    bot_token: str,
    chat_id: str,
    text: str,
    timeout: float = 10.0
) -> TelegramSendResult:
    """
    Send message via Telegram API directly.
    Returns typed TelegramSendResult (supports 4-tuple unpack for backwards compatibility).
    Never logs or exposes the raw bot token.
    """
    if not bot_token or not chat_id:
        return TelegramSendResult(
            success=False,
            error_code="MISSING_CREDENTIALS",
            error_message="Thiếu Bot Token hoặc Chat ID."
        )

    url = TELEGRAM_API_URL.format(token=bot_token)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                resp_json = resp.json()
                msg_id = str(resp_json.get("result", {}).get("message_id", ""))
                return TelegramSendResult(
                    success=True,
                    provider_message_id=msg_id,
                    http_status=200
                )
            elif resp.status_code == 429:
                retry_header = resp.headers.get("Retry-After", "10")
                try:
                    retry_sec = int(retry_header)
                except ValueError:
                    retry_sec = 10
                return TelegramSendResult(
                    success=False,
                    error_code="RATE_LIMIT",
                    error_message=f"Telegram API 429: Quá giới hạn tần suất gửi tin, retry sau {retry_sec}s",
                    retry_after_sec=retry_sec,
                    http_status=429
                )
            else:
                try:
                    resp_json = resp.json()
                    desc = resp_json.get("description", resp.text[:120])
                except Exception:
                    desc = resp.text[:120]

                if resp.status_code == 401:
                    return TelegramSendResult(
                        success=False,
                        error_code="UNAUTHORIZED",
                        error_message="Telegram API 401 (Unauthorized): Bot Token không hợp lệ.",
                        http_status=401
                    )
                elif resp.status_code == 403:
                    return TelegramSendResult(
                        success=False,
                        error_code="FORBIDDEN",
                        error_message=f"Telegram API 403 (Forbidden): Bot bị chặn hoặc không có quyền gửi tin nhắn cho Chat ID {chat_id}.",
                        http_status=403
                    )
                elif resp.status_code == 400 and ("chat not found" in desc.lower() or "chat_id" in desc.lower()):
                    return TelegramSendResult(
                        success=False,
                        error_code="CHAT_NOT_FOUND",
                        error_message=f"Telegram API 400 (Bad Request): Chưa mở chat với bot cho Chat ID {chat_id}.",
                        http_status=400
                    )
                else:
                    return TelegramSendResult(
                        success=False,
                        error_code=f"HTTP_{resp.status_code}",
                        error_message=f"Telegram API [{resp.status_code}]: {desc}",
                        http_status=resp.status_code
                    )
    except httpx.TimeoutException:
        return TelegramSendResult(
            success=False,
            error_code="TIMEOUT",
            error_message="Lỗi kết nối Timeout: Quá thời gian chờ (10s) khi gọi tới api.telegram.org."
        )
    except httpx.ConnectError:
        return TelegramSendResult(
            success=False,
            error_code="CONNECT_ERROR",
            error_message="Lỗi kết nối mạng: Không thể kết nối tới api.telegram.org."
        )
    except httpx.RequestError as e:
        return TelegramSendResult(
            success=False,
            error_code="REQUEST_ERROR",
            error_message=f"Lỗi yêu cầu mạng HTTP: {type(e).__name__}"
        )
    except Exception as e:
        return TelegramSendResult(
            success=False,
            error_code="INTERNAL_ERROR",
            error_message=f"Lỗi nội bộ khi gửi tin Telegram: {type(e).__name__}"
        )


async def process_notification_outbox():
    """
    Background Outbox Worker:
    - Queries PENDING and RETRYING notifications from SQLite.
    - Honors Priority (CRITICAL processed ahead of STANDARD).
    - Respects quiet hours: Critical alerts (FILLED, TP, SL, LIQUIDATED) bypass quiet hours by default.
    - Suppresses stale/expired NEAR_ENTRY or READY setups before dispatch.
    - Sends message with exponential backoff + jitter and 429 Retry-After handling.
    - Updates notification status in DB.
    """
    while True:
        try:
            await asyncio.sleep(2.5)
            db: Session = SessionLocal()
            try:
                tg_cfg = db.query(models.TelegramConfig).first()
                if not tg_cfg or not tg_cfg.enabled or not tg_cfg.bot_token or not tg_cfg.chat_id:
                    continue

                in_quiet_hours = is_within_quiet_hours(tg_cfg)
                bypass_critical = getattr(tg_cfg, "bypass_critical_quiet_hours", True)

                now_ms = int(time.time() * 1000)

                # Base query for actionable outbox items
                query = (
                    db.query(models.NotificationOutbox)
                    .filter(models.NotificationOutbox.status.in_(["PENDING", "RETRYING"]))
                    .filter(models.NotificationOutbox.attempts < 5)
                    .filter((models.NotificationOutbox.next_attempt_at == None) | (models.NotificationOutbox.next_attempt_at <= now_ms))
                )

                if in_quiet_hours:
                    if bypass_critical:
                        # Only allow CRITICAL items to be sent during quiet hours
                        query = query.filter(models.NotificationOutbox.priority == "CRITICAL")
                    else:
                        # Full quiet hours pause
                        continue

                # Order: CRITICAL priority first, then oldest created_at first
                pending_items = (
                    query.order_by(
                        case((models.NotificationOutbox.priority == "CRITICAL", 0), else_=1),
                        models.NotificationOutbox.created_at.asc()
                    )
                    .limit(5)
                    .all()
                )

                for item in pending_items:
                    # Parse payload
                    try:
                        p_data = json.loads(item.payload)
                        notif_type = p_data.get("notif_type", item.message_type)
                        data_body = p_data.get("data", {})
                        occurred_at = p_data.get("occurred_at", item.occurred_at or item.created_at)
                    except Exception:
                        notif_type = item.message_type
                        data_body = {}
                        occurred_at = item.occurred_at or item.created_at

                    # Check for Stale / Invalidated / Expired suppression for actionable opportunity alerts
                    if notif_type in ("NEAR_ENTRY", "READY"):
                        setup_id = data_body.get("setup_id")
                        if setup_id:
                            watch_setup = db.query(models.WatchSetup).filter(models.WatchSetup.id == setup_id).first()
                            if watch_setup:
                                is_expired = watch_setup.expires_at and now_ms > watch_setup.expires_at
                                if is_expired or watch_setup.state in ("PAPER_OPEN", "CLOSED", "INVALIDATED", "EXPIRED", "CANCELLED"):
                                    item.status = "SUPPRESSED"
                                    item.error_message = f"Setup đã {watch_setup.state.lower()} trước khi gửi tin"
                                    db.commit()
                                    continue

                    # Format message text
                    msg_text = format_telegram_message(
                        item_type=notif_type,
                        data=data_body,
                        occurred_at=occurred_at,
                        timezone_str=tg_cfg.timezone or "Asia/Ho_Chi_Minh",
                        base_chart_url=tg_cfg.base_chart_url
                    )

                    # Append late notice if event occurred more than 2 minutes ago (for filled/closed)
                    if occurred_at and (now_ms - occurred_at) > 120000 and notif_type in ("FILLED", "TP_HIT", "SL_HIT", "MANUAL_CLOSED", "LIQUIDATED"):
                        occurred_vn = get_formatted_time(occurred_at, tg_cfg.timezone)
                        msg_text += f"\n\n⏱ _(Thông báo gửi trễ do hàng đợi: sự kiện diễn ra lúc {occurred_vn})_"

                    item.attempts += 1
                    item.last_attempt_at = now_ms

                    recipient = item.recipient or tg_cfg.chat_id

                    success, msg_id, err_msg, retry_after = await send_telegram_direct(
                        bot_token=tg_cfg.bot_token,
                        chat_id=recipient,
                        text=msg_text
                    )

                    if success:
                        item.status = "SENT"
                        item.provider_message_id = msg_id
                        item.error_message = None
                    else:
                        item.error_message = err_msg
                        is_permanent = any(code in (err_msg or "") for code in ["401", "403", "Bad Request", "Chưa mở chat"])
                        if is_permanent:
                            # Permanent error, do not retry
                            item.status = "FAILED"
                        elif retry_after:
                            # 429 Rate limit: schedule specific next attempt
                            item.status = "RETRYING"
                            item.next_attempt_at = now_ms + (retry_after * 1000)
                        elif item.attempts >= 5:
                            item.status = "FAILED"
                        else:
                            item.status = "RETRYING"
                            # Bounded exponential backoff with jitter (5s, 10s, 20s, 40s + jitter)
                            backoff_sec = min(120, (5 * (2 ** (item.attempts - 1)))) + random.randint(1, 4)
                            item.next_attempt_at = now_ms + (backoff_sec * 1000)

                    db.commit()

            finally:
                db.close()

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in process_notification_outbox loop: {e}")
            await asyncio.sleep(2.0)
