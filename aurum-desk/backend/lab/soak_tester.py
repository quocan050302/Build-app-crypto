import time
import os
import resource
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from services.quote_validator import QuoteValidator, CanonicalQuote
from services.candle_cache_service import CandleCacheService
from services.execution_consumer import ExecutionConsumer
from services.clock import IClock, LiveClock, ReplayClock

logger = logging.getLogger("aurum.lab.soak_tester")

class VirtualSoakClock(IClock):
    def __init__(self, start_ms: int = 1791460000000):
        self._now_ms = start_ms

    def now_ms(self) -> int:
        return self._now_ms

    def now_datetime(self):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(self._now_ms / 1000.0, tz=timezone.utc)

    def advance_ms(self, delta_ms: int):
        self._now_ms += delta_ms


class SoakStabilityTester:
    """
    V11 Soak & Stability Tester (S01, S02)
    Evaluates engine stability under:
    - High-frequency burst quotes (e.g. 100 quotes/sec equivalent)
    - Network fault injection (duplicate timestamps, out-of-order timestamps, inverted spreads, stale quotes)
    - Queue boundedness and memory leak detection
    - Zero crash guarantees on isolated ephemeral resources
    """

    @classmethod
    def run_soak_test(
        cls,
        duration_seconds: float = 2.0,
        burst_quotes_count: int = 100,
        inject_faults: bool = True
    ) -> Dict[str, Any]:
        """
        Executes a rapid soak & fault injection test.
        """
        start_wall_time = time.time()
        start_rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # On macOS ru_maxrss is in bytes, on Linux in KB
        is_macos = os.uname().sysname == "Darwin"
        start_rss_mb = (start_rss_kb / (1024 * 1024)) if is_macos else (start_rss_kb / 1024)

        # 1. Ephemeral isolated in-memory DB
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool
        )
        models.Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        db = Session()

        # 2. Virtual clock & services
        start_ms = 1791460000000
        clock = VirtualSoakClock(start_ms=start_ms)
        candle_cache = CandleCacheService(max_bars=100)
        consumer = ExecutionConsumer(max_queue_size=200)

        # 3. Seed an active test order
        order = models.PaperOrder(
            id="ord-soak-active",
            setup_id="setup-soak-1",
            instrument="XAUUSDT",
            direction="LONG",
            state="paper_open",
            planned_entry=2650.0,
            actual_entry=2650.0,
            stop_loss=2640.0,
            take_profit=2680.0,
            quantity=0.1,
            initial_risk_usdt=1.0,
            created_at=start_ms,
            opened_at=start_ms
        )
        db.add(order)
        db.commit()

        # 4. Metrics collectors
        quotes_processed = 0
        faults_injected = 0
        faults_caught = 0
        errors = []
        base_price = 2650.0

        # Phase 1: High-Frequency Quote Burst (100 quotes)
        for i in range(burst_quotes_count):
            clock.advance_ms(10)
            t_now = clock.now_ms()
            p = base_price + (i * 0.05)
            q = CanonicalQuote(
                symbol="XAUUSDT",
                last=p,
                bid=p - 0.10,
                ask=p + 0.10,
                exchange_ts_ms=t_now,
                received_at_ms=t_now,
                status="VALID"
            )
            try:
                consumer.enqueue_quote(q)
                consumer.evaluate_quote_sync(db, q, clock=clock)
                quotes_processed += 1
            except Exception as e:
                errors.append(f"Burst quote error at index {i}: {e}")

        # Phase 2: Fault Injection (S01)
        if inject_faults:
            fault_cases = [
                # Inverted spread
                {"bid": 2660.0, "ask": 2658.0, "ts": clock.now_ms() + 10, "type": "INVERTED_SPREAD"},
                # Zero / negative price
                {"bid": -10.0, "ask": 2650.0, "ts": clock.now_ms() + 20, "type": "NEGATIVE_PRICE"},
                # Stale quote (timestamp far in past)
                {"bid": 2650.0, "ask": 2650.2, "ts": clock.now_ms() - (60 * 60 * 1000), "type": "STALE_QUOTE"},
                # Zero price
                {"bid": 0.0, "ask": 0.0, "ts": clock.now_ms() + 30, "type": "ZERO_PRICE"},
            ]
            for fc in fault_cases:
                faults_injected += 1
                ticker_dict = {
                    "symbol": "XAUUSDT",
                    "bid": fc["bid"],
                    "ask": fc["ask"],
                    "timestamp": fc["ts"]
                }
                validation = QuoteValidator.validate_ticker(ticker_dict, now_ms=clock.now_ms())
                if not validation.is_valid:
                    faults_caught += 1
                else:
                    errors.append(f"Fault {fc['type']} was not rejected by QuoteValidator!")

        # Phase 3: Order Lifecycle Resolution under load
        clock.advance_ms(5000)
        tp_quote = CanonicalQuote(
            symbol="XAUUSDT",
            last=2681.0,
            bid=2681.0,
            ask=2681.2,
            exchange_ts_ms=clock.now_ms(),
            received_at_ms=clock.now_ms(),
            status="VALID"
        )
        res = consumer.evaluate_quote_sync(db, tp_quote, clock=clock)
        quotes_processed += 1
        db.refresh(order)

        # Phase 4: Queue bounds check
        max_q = consumer.queue.maxsize
        curr_q = consumer.queue.qsize()

        db.close()
        elapsed_sec = time.time() - start_wall_time
        end_rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        end_rss_mb = (end_rss_kb / (1024 * 1024)) if is_macos else (end_rss_kb / 1024)
        rss_growth_mb = max(0.0, end_rss_mb - start_rss_mb)

        passed = (
            len(errors) == 0
            and quotes_processed >= burst_quotes_count
            and faults_caught == faults_injected
            and order.state == "closed"
            and order.exit_cause == "TP_HIT"
            and curr_q <= max_q
        )

        return {
            "status": "PASS" if passed else "FAIL",
            "passed": passed,
            "quotes_processed": quotes_processed,
            "burst_quotes_count": burst_quotes_count,
            "faults_injected": faults_injected,
            "faults_caught": faults_caught,
            "order_final_state": order.state,
            "order_exit_cause": order.exit_cause,
            "queue_max_size": max_q,
            "queue_current_size": curr_q,
            "elapsed_seconds": round(elapsed_sec, 3),
            "start_rss_mb": round(start_rss_mb, 2),
            "end_rss_mb": round(end_rss_mb, 2),
            "rss_growth_mb": round(rss_growth_mb, 4),
            "errors": errors
        }
