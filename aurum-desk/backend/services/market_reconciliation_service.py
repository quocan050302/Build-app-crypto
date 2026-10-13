"""
backend/services/market_reconciliation_service.py
V14.4.2 — Background Late Evidence Reconciliation Consumer.

Enforces:
1. Regularly drains late evidence from QuoteOrderingService.
2. Checks active paper_open orders against historical late quotes in a bounded interval.
3. If SL/TP was reached, executes single CAS close via TradeLifecycleService.
4. Acknowledges processed items using stable evidence_ids to prevent concurrent arrival loss.
5. Runs as background task wired into FastAPI lifespan.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

import models
from database import SessionLocal
from services.quote_ordering_service import quote_ordering_service
from services.trade_lifecycle_service import TradeLifecycleService

logger = logging.getLogger("market_reconciliation")


class MarketReconciliationService:
    def __init__(self):
        self._running = False

    def process_late_batch(self, db: Session, symbol: str = "XAUUSDT") -> int:
        """Processes one batch of late evidence for the symbol in chronological source order."""
        batch = quote_ordering_service.claim_late_evidence_batch(symbol, limit=50)
        if not batch:
            return 0

        # Sort batch by source_timestamp ascending to enforce market chronology (PHẦN 061, 217, 218)
        sorted_batch = sorted(batch, key=lambda r: r.get("source_timestamp") or 0)

        processed_ids = [r["evidence_id"] for r in batch if r.get("evidence_id")]
        open_orders = (
            db.query(models.PaperOrder)
            .filter(
                models.PaperOrder.instrument == symbol,
                models.PaperOrder.state == "paper_open"
            )
            .all()
        )

        closed_order_ids = set()

        for record in sorted_batch:
            ev_id = record.get("evidence_id")
            src_ts = record.get("source_timestamp") or 0
            bid = record.get("bid")
            ask = record.get("ask")
            last = record.get("last")

            if not open_orders:
                continue

            for order in open_orders:
                if order.id in closed_order_ids or order.state == "closed":
                    closed_order_ids.add(order.id)
                    continue

                if order.opened_at and src_ts < order.opened_at:
                    # Ignore quotes before order was opened
                    continue
                if order.closed_at and src_ts > order.closed_at:
                    # Ignore quotes after order was closed
                    continue

                exit_found = False
                exit_price = 0.0
                exit_cause = ""

                if order.direction == "LONG":
                    # Exit trigger: Bid <= SL or Bid >= TP
                    price_to_check = bid if bid is not None else last
                    if price_to_check is not None:
                        sl_hit = order.stop_loss and price_to_check <= order.stop_loss
                        tp_hit = order.take_profit and price_to_check >= order.take_profit
                        if sl_hit and tp_hit:
                            exit_found = True
                            exit_price = order.stop_loss
                            exit_cause = "AMBIGUOUS_BAR_SL_FIRST"
                        elif sl_hit:
                            exit_found = True
                            exit_price = order.stop_loss
                            exit_cause = "SL_HIT"
                        elif tp_hit:
                            exit_found = True
                            exit_price = order.take_profit
                            exit_cause = "TP_HIT"
                elif order.direction == "SHORT":
                    # Exit trigger: Ask >= SL or Ask <= TP
                    price_to_check = ask if ask is not None else last
                    if price_to_check is not None:
                        sl_hit = order.stop_loss and price_to_check >= order.stop_loss
                        tp_hit = order.take_profit and price_to_check <= order.take_profit
                        if sl_hit and tp_hit:
                            exit_found = True
                            exit_price = order.stop_loss
                            exit_cause = "AMBIGUOUS_BAR_SL_FIRST"
                        elif sl_hit:
                            exit_found = True
                            exit_price = order.stop_loss
                            exit_cause = "SL_HIT"
                        elif tp_hit:
                            exit_found = True
                            exit_price = order.take_profit
                            exit_cause = "TP_HIT"

                if exit_found:
                    logger.info(
                        f"[LATE_RECONCILIATION] Closing order {order.id} due to late tick at {src_ts}: "
                        f"{exit_cause} at {exit_price}"
                    )
                    TradeLifecycleService.execute_close(
                        db=db,
                        order_id=order.id,
                        exit_price=exit_price,
                        exit_cause=exit_cause,
                        occurred_at=src_ts,
                        session_tag="LATE_RECONCILIATION",
                        recovery_metadata={
                            "recovery_status": "RECOVERED",
                            "confidence": "CONFIRMED_TICK",
                            "evidence_id": ev_id,
                        }
                    )
                    closed_order_ids.add(order.id)

        if processed_ids:
            quote_ordering_service.acknowledge_late_evidence(symbol, evidence_ids=processed_ids)

        return len(processed_ids)

    async def run_late_reconciliation_loop(self, poll_interval_s: float = 2.0):
        """Continuous background reconciliation loop.
        Dispatches synchronous DB batch processing to a worker thread via asyncio.to_thread
        to keep the asyncio event loop completely non-blocking (PHẦN 221).
        """
        self._running = True
        logger.info("MarketReconciliationService background loop started.")
        while self._running:
            try:
                def _worker():
                    db = SessionLocal()
                    try:
                        return self.process_late_batch(db, symbol="XAUUSDT")
                    finally:
                        db.close()

                await asyncio.to_thread(_worker)
            except Exception as e:
                logger.error(f"Error in late reconciliation loop: {e}", exc_info=True)
            await asyncio.sleep(poll_interval_s)

    def stop(self):
        self._running = False


market_reconciliation_service = MarketReconciliationService()
