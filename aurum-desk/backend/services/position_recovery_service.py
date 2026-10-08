import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
import httpx

import models, crud
from services.trade_lifecycle_service import TradeLifecycleService
from services.clock import IClock
from services.event_bus import event_bus

logger = logging.getLogger(__name__)

VN_TZ = timezone(timedelta(hours=7))

def get_vn_date_str(epoch_ms: int) -> str:
    """Converts epoch ms to YYYY-MM-DD in UTC+7 (Asia/Ho_Chi_Minh)."""
    dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=VN_TZ)
    return dt.strftime("%Y-%m-%d")

class PositionRecoveryService:
    """
    V6.1 Offline Position Recovery & Reconciler Service.
    - Scans for active PAPER_OPEN positions after machine sleep, process restart, or network reconnect.
    - Identifies data gaps between position checkpoint (last_processed_market_timestamp or opened_at) and now.
    - Chronologically evaluates historical closed candles in the gap to determine first valid exit.
    - Resolves ambiguity (TP and SL in same bar) conservatively with ASSUMED_CONSERVATIVE.
    - Records exit atomically with historical occurred_at, discovery time, and correct VN date DayAudit.
    - Creates idempotent Telegram outbox notification without duplicates on restart.
    """

    @staticmethod
    def check_and_recover_offline_positions(
        db: Session,
        candles_override: Optional[List[models.Candle]] = None,
        clock: Optional[IClock] = None,
        now_ms: Optional[int] = None,
        gap_threshold_ms: int = 60000 # 60 seconds gap
    ) -> List[Dict[str, Any]]:
        current_time = now_ms if now_ms is not None else (clock.now_ms() if clock else int(time.time() * 1000))
        results: List[Dict[str, Any]] = []

        open_orders = db.query(models.PaperOrder).filter(models.PaperOrder.state == "paper_open").all()
        if not open_orders:
            return results

        for order in open_orders:
            lower_bound = order.last_processed_market_timestamp or order.opened_at or current_time
            gap_ms = current_time - lower_bound

            if gap_ms < gap_threshold_ms:
                # Up to date, no significant offline gap
                order.last_processed_market_timestamp = current_time
                order.recovery_status = "UP_TO_DATE"
                order.last_recovery_attempt = current_time
                db.commit()
                continue

            logger.info(f"[OFFLINE_RECOVERY] Found gap for open position {order.id}: {lower_bound} to {current_time} ({gap_ms/1000:.1f}s)")
            order.recovery_status = "RECOVERING"
            order.last_recovery_attempt = current_time
            db.commit()

            # Retrieve closed candles for evaluation
            candles: List[models.Candle] = []
            if candles_override is not None:
                candles = candles_override
            else:
                # Query local DB for 15M or 5M or 1M candles in the window
                db_candles = db.query(models.Candle).filter(
                    models.Candle.symbol == order.instrument,
                    models.Candle.timestamp > lower_bound,
                    models.Candle.timestamp <= current_time
                ).order_by(models.Candle.timestamp.asc()).all()
                candles = db_candles

            if not candles:
                logger.warning(f"[OFFLINE_RECOVERY] No candles available in range {lower_bound} - {current_time} for {order.id}")
                order.recovery_status = "RECOVERY_INCOMPLETE"
                db.commit()
                results.append({
                    "order_id": order.id,
                    "status": "RECOVERY_INCOMPLETE",
                    "reason": "MISSING_CANDLE_DATA"
                })
                continue

            # Sort ascending by timestamp
            sorted_candles = sorted(candles, key=lambda c: c.timestamp)

            exit_found = False
            exit_price = 0.0
            exit_cause = ""
            occurred_at = 0
            confidence = "CONFIRMED"
            resolved_through_ts = lower_bound

            for c in sorted_candles:
                resolved_through_ts = c.timestamp
                bar_start = c.timestamp
                # Assume 15M (900000ms) or 5M (300000ms) or 1M (60000ms) based on candle timeframe
                tf_ms = 15 * 60 * 1000 if c.timeframe == "15M" else (5 * 60 * 1000 if c.timeframe == "5M" else 60 * 1000)
                bar_end = bar_start + tf_ms

                # Guard 1: Bar was before order opened
                if order.opened_at and order.opened_at >= bar_end:
                    continue

                # Guard 2: Entry bar extremes cannot be assumed post-entry
                if order.opened_at and bar_start <= order.opened_at < bar_end:
                    continue

                high_val = c.high
                low_val = c.low

                if order.direction == "LONG":
                    hit_tp = high_val >= order.take_profit
                    hit_sl = low_val <= order.stop_loss

                    if hit_tp and hit_sl:
                        # Ambiguous bar: touched both in same candle -> conservative SL assumption
                        exit_found = True
                        exit_price = order.stop_loss
                        exit_cause = "AMBIGUOUS_BAR_CONSERVATIVE_SL"
                        confidence = "ASSUMED_CONSERVATIVE"
                        occurred_at = bar_start
                        break
                    elif hit_tp:
                        exit_found = True
                        exit_price = order.take_profit
                        exit_cause = "TP_HIT"
                        confidence = "CONFIRMED"
                        occurred_at = bar_start
                        break
                    elif hit_sl:
                        exit_found = True
                        exit_price = order.stop_loss
                        exit_cause = "SL_HIT"
                        confidence = "CONFIRMED"
                        occurred_at = bar_start
                        break

                elif order.direction == "SHORT":
                    hit_tp = low_val <= order.take_profit
                    hit_sl = high_val >= order.stop_loss

                    if hit_tp and hit_sl:
                        exit_found = True
                        exit_price = order.stop_loss
                        exit_cause = "AMBIGUOUS_BAR_CONSERVATIVE_SL"
                        confidence = "ASSUMED_CONSERVATIVE"
                        occurred_at = bar_start
                        break
                    elif hit_tp:
                        exit_found = True
                        exit_price = order.take_profit
                        exit_cause = "TP_HIT"
                        confidence = "CONFIRMED"
                        occurred_at = bar_start
                        break
                    elif hit_sl:
                        exit_found = True
                        exit_price = order.stop_loss
                        exit_cause = "SL_HIT"
                        confidence = "CONFIRMED"
                        occurred_at = bar_start
                        break

            if exit_found:
                hist_date_str = get_vn_date_str(occurred_at)
                logger.info(f"[OFFLINE_RECOVERY] Reconciled historical exit for {order.id}: {exit_cause} at {exit_price} (occurred_at={occurred_at}, date={hist_date_str})")

                # Atomically execute close via TradeLifecycleService
                closed_order = TradeLifecycleService.execute_close(
                    db=db,
                    order_id=order.id,
                    exit_price=exit_price,
                    exit_cause=exit_cause,
                    occurred_at=occurred_at,
                    clock=clock,
                    date_str=hist_date_str,
                    session_tag="OFFLINE_RECOVERY"
                )

                if closed_order:
                    closed_order.recovery_status = "RECOVERED"
                    closed_order.recovery_confidence = confidence
                    closed_order.discovered_at = current_time
                    closed_order.occurred_at = occurred_at
                    closed_order.resolved_through = resolved_through_ts

                    # Create dedicated Outbox notification with offline recovery badge
                    dedupe_key = f"offline-recovery-{order.id}-{occurred_at}"
                    existing_outbox = db.query(models.NotificationOutbox).filter(
                        models.NotificationOutbox.dedupe_key == dedupe_key
                    ).first()

                    if not existing_outbox:
                        dt_occurred = datetime.fromtimestamp(occurred_at / 1000.0, tz=VN_TZ).strftime("%H:%M:%S %d/%m/%Y")
                        dt_disc = datetime.fromtimestamp(current_time / 1000.0, tz=VN_TZ).strftime("%H:%M:%S %d/%m/%Y")
                        
                        outbox_item = models.NotificationOutbox(
                            event_id=f"evt-rec-{order.id}",
                            channel="TELEGRAM",
                            recipient="ALL",
                            message_type="OFFLINE_RECOVERY_EXIT",
                            dedupe_key=dedupe_key,
                            payload=str({
                                "badge": "KẾT QUẢ PAPER ĐƯỢC ĐỐI SOÁT SAU OFFLINE",
                                "order_id": order.id,
                                "direction": order.direction,
                                "exit_cause": exit_cause,
                                "confidence": confidence,
                                "exit_price": exit_price,
                                "net_pnl": closed_order.realized_pnl_net,
                                "occurred_at_str": dt_occurred,
                                "discovered_at_str": dt_disc,
                                "note": "Kết quả được khôi phục chính xác theo thứ tự thời gian từ nến lịch sử sàn Bitget."
                            }),
                            status="PENDING",
                            priority="CRITICAL",
                            created_at=current_time
                        )
                        db.add(outbox_item)

                    db.commit()

                    results.append({
                        "order_id": order.id,
                        "status": "RECOVERED",
                        "exit_cause": exit_cause,
                        "exit_price": exit_price,
                        "confidence": confidence,
                        "occurred_at": occurred_at,
                        "discovered_at": current_time,
                        "net_pnl": closed_order.realized_pnl_net
                    })

            else:
                # Position survived the offline period! Remains paper_open
                order.last_processed_market_timestamp = resolved_through_ts
                order.recovery_status = "UP_TO_DATE"
                order.resolved_through = resolved_through_ts
                order.recovery_confidence = "CONFIRMED"
                db.commit()
                results.append({
                    "order_id": order.id,
                    "status": "SURVIVED_UP_TO_DATE",
                    "resolved_through": resolved_through_ts
                })

        return results

position_recovery_service = PositionRecoveryService()
