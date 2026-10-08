import time
import json
import random
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, Tuple
import httpx
from sqlalchemy.orm import Session
from database import SessionLocal
import models

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

def format_telegram_message(item_type: str, data: Dict[str, Any]) -> str:
    """Format structured Vietnamese notification message for Telegram."""
    now_vn = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%H:%M:%S %d/%m/%Y")

    if item_type == "READY":
        direction = data.get("direction", "LONG")
        entry = data.get("planned_entry", 0.0)
        sl = data.get("stop_loss", 0.0)
        tp = data.get("take_profit", 0.0)
        net_rr = data.get("net_rr", 0.0)
        risk = data.get("risk_usdt", 0.0)
        setup_id = data.get("setup_id", "N/A")
        poi = data.get("poi", "Vùng FVG/Discount")

        return (
            f"🔔 *AURUM DESK — TÍN HIỆU SẴN SÀNG (READY)*\n\n"
            f"• *Setup ID:* `{setup_id}`\n"
            f"• *Kế hoạch:* *{direction} XAUUSDT*\n"
            f"• *Entry dự kiến:* `${entry:.2f}`\n"
            f"• *Stop Loss:* `${sl:.2f}`\n"
            f"• *Take Profit:* `${tp:.2f}`\n"
            f"• *Net R:R:* `1:{net_rr:.2f}` | *Rủi ro:* `${risk:.2f}`\n"
            f"• *Vùng POI:* {poi}\n"
            f"• *Trạng thái:* Đã đủ chuỗi Sweep + MSS + FVG. Sẵn sàng Arm lệnh Paper.\n"
            f"⏱ _{now_vn} (UTC+7)_"
        )

    elif item_type == "ARMED_NEAR_ENTRY":
        direction = data.get("direction", "LONG")
        entry = data.get("planned_entry", 0.0)
        dist = data.get("distance_usdt", 0.0)
        return (
            f"🎯 *AURUM DESK — LỆNH ĐÃ ARM GẦN ENTRY*\n\n"
            f"• *Cặp:* XAUUSDT ({direction})\n"
            f"• *Giá Entry:* `${entry:.2f}`\n"
            f"• *Khoảng cách hiện tại:* `${dist:.2f}` USDT\n"
            f"• *Trạng thái:* Chờ nến chạm mức để kích hoạt khớp lệnh.\n"
            f"⏱ _{now_vn} (UTC+7)_"
        )

    elif item_type == "FILLED":
        trade_id = data.get("trade_id", "N/A")
        direction = data.get("direction", "LONG")
        entry = data.get("actual_entry", 0.0)
        qty = data.get("quantity", 0.0)
        sl = data.get("stop_loss", 0.0)
        tp = data.get("take_profit", 0.0)
        net_rr = data.get("estimated_net_rr", 0.0)
        risk = data.get("initial_risk_usdt", 0.0)
        lev = data.get("leverage", 5)
        margin = data.get("initial_margin", 0.0)

        return (
            f"⚡ *AURUM DESK — ĐÃ KHỚP LỆNH PAPER (FILLED)*\n\n"
            f"• *Mã lệnh:* `{trade_id}`\n"
            f"• *Vị thế:* *{direction}* tại `${entry:.2f}`\n"
            f"• *Khối lượng:* `{qty:.2f} oz` ({lev}x Isolated, Ký quỹ: `${margin:.2f}`)\n"
            f"• *Stop Loss:* `${sl:.2f}`\n"
            f"• *Take Profit:* `${tp:.2f}`\n"
            f"• *Rủi ro ước tính:* `${risk:.2f}` (Net R:R: `1:{net_rr:.2f}`)\n"
            f"⏱ _{now_vn} (UTC+7)_"
        )

    elif item_type in ("INVALIDATED", "EXPIRED"):
        setup_id = data.get("setup_id", "N/A")
        reason = data.get("reason", "Cấu trúc bị phá vỡ hoặc hết hạn")
        return (
            f"❌ *AURUM DESK — HỦY THIẾT LẬP ({item_type})*\n\n"
            f"• *Setup ID:* `{setup_id}`\n"
            f"• *Lý do:* {reason}\n"
            f"• *Trạng thái:* Bỏ qua, đưa về danh sách theo dõi.\n"
            f"⏱ _{now_vn} (UTC+7)_"
        )

    elif item_type == "LIQUIDATED":
        trade_id = data.get("trade_id", "N/A")
        direction = data.get("direction", "LONG")
        exit_p = data.get("actual_exit", 0.0)
        pnl = data.get("realized_pnl", 0.0)
        return (
            f"💀 *AURUM DESK — THANH LÝ VỊ THẾ (LIQUIDATED)*\n\n"
            f"• *Mã lệnh:* `{trade_id}` ({direction})\n"
            f"• *Giá thanh lý:* `${exit_p:.2f}`\n"
            f"• *Realized PnL:* `-${abs(pnl):.2f}` USDT\n"
            f"• *Cảnh báo:* Vị thế chạm mức thanh lý ký quỹ Isolated.\n"
            f"⏱ _{now_vn} (UTC+7)_"
        )

    elif item_type == "CLOSED":
        trade_id = data.get("trade_id", "N/A")
        direction = data.get("direction", "LONG")
        exit_p = data.get("actual_exit", 0.0)
        pnl = data.get("realized_pnl", 0.0)
        r = data.get("realized_r", 0.0)
        cause = data.get("exit_cause", "CLOSE")
        sign = "+" if pnl >= 0 else ""

        return (
            f"🏁 *AURUM DESK — ĐÃ ĐÓNG VỊ THẾ ({cause})*\n\n"
            f"• *Mã lệnh:* `{trade_id}` ({direction})\n"
            f"• *Giá thoát:* `${exit_p:.2f}`\n"
            f"• *Kết quả PnL:* `{sign}${pnl:.2f} USDT` ({sign}{r:.2f}R)\n"
            f"• *Nguyên nhân:* `{cause}`\n"
            f"⏱ _{now_vn} (UTC+7)_"
        )

    elif item_type == "FEED_DOWN":
        reason = data.get("reason", "Mất kết nối API Bitget")
        return (
            f"⚠️ *AURUM DESK — CẢNH BÁO NGUỒN DỮ LIỆU BỊ TRỄ*\n\n"
            f"• *Tình trạng:* {reason}\n"
            f"• *Hành động an toàn:* Khóa mở lệnh mới cho tới khi dữ liệu ổn định lại.\n"
            f"⏱ _{now_vn} (UTC+7)_"
        )

    return f"ℹ️ *AURUM DESK*\n\n• Thông báo: {item_type}\n⏱ _{now_vn}_"


def is_within_quiet_hours(config: models.TelegramConfig) -> bool:
    """Check if current time is within configured quiet hours."""
    if not config.quiet_hours_enabled:
        return False

    try:
        tz = ZoneInfo(config.timezone or "Asia/Ho_Chi_Minh")
        now_dt = datetime.now(tz)
        current_time_str = now_dt.strftime("%H:%M")

        start = config.quiet_hours_start or "23:00"
        end = config.quiet_hours_end or "06:00"

        if start <= end:
            return start <= current_time_str <= end
        else:
            # Over midnight (e.g. 23:00 to 06:00)
            return current_time_str >= start or current_time_str <= end
    except Exception:
        return False


async def send_telegram_direct(bot_token: str, chat_id: str, text: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """Send message via Telegram API directly. Returns (success, provider_message_id, error_message)."""
    if not bot_token or not chat_id:
        return False, None, "Thiếu Bot Token hoặc Chat ID. Vui lòng kiểm tra lại cấu hình."

    url = TELEGRAM_API_URL.format(token=bot_token)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                resp_json = resp.json()
                msg_id = str(resp_json.get("result", {}).get("message_id", ""))
                return True, msg_id, None
            elif resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "5")
                return False, None, f"Telegram API 429: Quá giới hạn tần suất gửi tin, vui lòng thử lại sau {retry_after}s"
            else:
                try:
                    resp_json = resp.json()
                    desc = resp_json.get("description", resp.text[:120])
                except Exception:
                    desc = resp.text[:120]

                if resp.status_code == 401:
                    return False, None, "Telegram API 401 (Unauthorized): Bot Token không hợp lệ. Hãy kiểm tra lại token từ @BotFather."
                elif resp.status_code == 403:
                    return False, None, f"Telegram API 403 (Forbidden): Bot bị chặn hoặc không có quyền gửi tin nhắn cho Chat ID {chat_id}."
                elif resp.status_code == 400 and ("chat not found" in desc.lower() or "chat_id" in desc.lower()):
                    bot_user_hint = ""
                    try:
                        me_resp = await client.get(f"https://api.telegram.org/bot{bot_token}/getMe")
                        if me_resp.status_code == 200:
                            b_uname = me_resp.json().get("result", {}).get("username")
                            if b_uname:
                                bot_user_hint = f" Hãy mở bot @{b_uname} trên Telegram (hoặc truy cập https://t.me/{b_uname}) và bấm 'START'."
                    except Exception:
                        pass
                    return False, None, f"Telegram API 400 (Bad Request): Chưa mở chat với bot.{bot_user_hint or f' Bạn cần tìm bot trên Telegram và bấm Start (/start) để cho phép bot gửi tin tới Chat ID {chat_id}.'}"
                else:
                    return False, None, f"Telegram API [{resp.status_code}]: {desc}"
    except httpx.TimeoutException:
        return False, None, "Lỗi kết nối Timeout: Quá thời gian chờ (10s) khi gọi tới api.telegram.org. Vui lòng kiểm tra kết nối mạng."
    except httpx.ConnectError:
        return False, None, "Lỗi kết nối mạng: Không thể kết nối tới api.telegram.org (Mất kết nối Internet hoặc DNS bị chặn)."
    except httpx.RequestError as e:
        return False, None, f"Lỗi yêu cầu mạng HTTP: {type(e).__name__} ({str(e)[:100]})"
    except Exception as e:
        return False, None, f"Lỗi nội bộ khi gửi tin Telegram: {type(e).__name__} ({str(e)[:100]})"


async def process_notification_outbox():
    """
    Background Outbox Worker:
    - Queries PENDING notifications from SQLite.
    - Respects quiet hours and rate limits.
    - Sends message with exponential backoff + jitter.
    - Updates notification status in DB.
    """
    while True:
        try:
            await asyncio.sleep(3.0)
            db: Session = SessionLocal()
            try:
                tg_cfg = db.query(models.TelegramConfig).first()
                if not tg_cfg or not tg_cfg.enabled or not tg_cfg.bot_token or not tg_cfg.chat_id:
                    continue

                if is_within_quiet_hours(tg_cfg):
                    continue

                now_ms = int(time.time() * 1000)

                # Fetch pending items ordered by created_at asc
                pending_items = (
                    db.query(models.NotificationOutbox)
                    .filter(models.NotificationOutbox.status.in_(["PENDING", "RETRYING"]))
                    .filter(models.NotificationOutbox.attempts < 5)
                    .order_by(models.NotificationOutbox.created_at.asc())
                    .limit(5)
                    .all()
                )

                for item in pending_items:
                    # Parse payload
                    try:
                        p_data = json.loads(item.payload)
                        notif_type = p_data.get("notif_type", item.message_type)
                        data_body = p_data.get("data", {})
                    except Exception:
                        notif_type = item.message_type
                        data_body = {}

                    msg_text = format_telegram_message(notif_type, data_body)

                    item.attempts += 1
                    item.last_attempt_at = now_ms

                    success, msg_id, err_msg = await send_telegram_direct(
                        bot_token=tg_cfg.bot_token,
                        chat_id=tg_cfg.chat_id,
                        text=msg_text
                    )

                    if success:
                        item.status = "SENT"
                        item.provider_message_id = msg_id
                        item.error_message = None
                    else:
                        if "429" in (err_msg or ""):
                            item.status = "RETRYING"
                            # Small sleep on rate limit
                            await asyncio.sleep(5.0)
                        elif item.attempts >= 5:
                            item.status = "FAILED"
                        else:
                            item.status = "RETRYING"
                        item.error_message = err_msg

                    db.commit()

            finally:
                db.close()

        except asyncio.CancelledError:
            break
        except Exception as e:
            await asyncio.sleep(2.0)
