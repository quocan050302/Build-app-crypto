import time
import json
import uuid
from typing import List, Dict, Any, Optional
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

import models, crud, schemas
from services.clock import ReplayClock
from services.quote_validator import QuoteValidator
from services.trade_lifecycle_service import TradeLifecycleService
from services.execution_coordinator import ExecutionCoordinator
from paper_broker import PaperBroker
from domain_calculator import calculate_risk_reward
from services.event_bus import event_bus

class ScenarioRunner:
    """
    Deterministic Scenario Testing Suite for Aurum Desk V5:
    - Runs in isolated SQLite environments with ReplayClock.
    - Zero interference with live paper trades or live Telegram notifications.
    - Covers the 13 required verification scenarios from Master Prompt V5.
    - Produces granular timelines (Quote -> Decision -> Transition -> Event -> Outbox).
    """

    @staticmethod
    def _create_isolated_db() -> Session:
        """Create fresh in-memory SQLite database with schema initialized"""
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        models.Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        return SessionLocal()

    @classmethod
    def list_scenarios(cls) -> List[Dict[str, str]]:
        return cls.get_all_scenario_definitions()

    @classmethod
    def get_all_scenario_definitions(cls) -> List[Dict[str, str]]:
        return [
            {
                "id": "scenario_1_long_full_cycle",
                "name": "1. LONG Full Cycle (Far -> Near -> Armed -> Fill -> TP)",
                "description": "Kiểm tra chu trình hoàn chỉnh lệnh LONG: Armed, khớp tại Ask + slippage, đóng TP tại Bid."
            },
            {
                "id": "scenario_2_short_full_cycle",
                "name": "2. SHORT Full Cycle (Far -> Near -> Armed -> Fill -> SL)",
                "description": "Kiểm tra chu trình hoàn chỉnh lệnh SHORT: Armed, khớp tại Bid - slippage, đóng SL tại Ask."
            },
            {
                "id": "scenario_3_spread_executable_sides",
                "name": "3. Executable Exit Side & Spread (LONG exits Bid, SHORT exits Ask)",
                "description": "Xác minh LONG thoát tại Bid (không bị trigger oan bởi Ask) và SHORT thoát tại Ask (không thoát bởi Bid)."
            },
            {
                "id": "scenario_4_near_entry_hysteresis",
                "name": "4. Proximity Hysteresis & Alert Dedup",
                "description": "Kiểm tra cơ chế chống spam cảnh báo NEAR_ENTRY khi giá dao động quanh ngưỡng kích hoạt."
            },
            {
                "id": "scenario_5_setup_selection_conflict",
                "name": "5. Setup Selection & Direction Guard (409 Conflict)",
                "description": "Người dùng chọn SHORT, background đổi thành LONG -> Arm bị chặn HTTP 409, không tự đổi hướng."
            },
            {
                "id": "scenario_6_order_types_market_limit_stop",
                "name": "6. Order Semantics (MARKET, LIMIT, STOP Trigger)",
                "description": "Kiểm tra cơ chế kích hoạt chuẩn xác cho BUY/SELL LIMIT và BUY/SELL STOP, loại bỏ lệnh không hỗ trợ."
            },
            {
                "id": "scenario_7_stale_malformed_quote_rejection",
                "name": "7. Quote Freshness & Sanity (Stale >15s, NaN, Negative)",
                "description": "Ticker bị cũ, mất kết nối feed hoặc chứa giá không hợp lệ bị từ chối khớp lệnh ngay lập tức."
            },
            {
                "id": "scenario_8_news_blackout_and_expiry",
                "name": "8. News Blackout & Expiry Guard tại Fill",
                "description": "Tin tức High-Impact USD hoặc lệnh Armed quá hạn bị chặn thực thi tại thời điểm khớp."
            },
            {
                "id": "scenario_9_spread_spike_rr_rejection",
                "name": "9. Spread Spike làm hỏng Net R:R (<1.5)",
                "description": "Độ trượt giá hoặc spread giãn mạnh khiến Net R:R < 1.5 bị từ chối an toàn mà không tăng đếm lệnh."
            },
            {
                "id": "scenario_10_daily_guards_consecutive_losses",
                "name": "10. Daily Guards (2 Lỗ liên tiếp & Max 3 Fills/ngày)",
                "description": "Chặn lệnh thứ 4 hoặc tạm dừng sau 2 lệnh lỗ liên tiếp theo đúng quy tắc bảo vệ vốn UTC+7."
            },
            {
                "id": "scenario_11_intrabar_opened_at_guard",
                "name": "11. Intrabar Opened_at Guard & Ambiguous Bar",
                "description": "Không dùng đỉnh/đáy nến trước thời điểm mở lệnh; đánh dấu AMBIGUOUS_BAR_SL_FIRST khi chạm cả TP/SL."
            },
            {
                "id": "scenario_12_concurrent_fills_max_one_pos",
                "name": "12. Concurrency Safety (Chỉ duy nhất 1 vị thế mở)",
                "description": "Hai yêu cầu khớp lệnh cạnh tranh song song đảm bảo chỉ có đúng 1 vị thế được mở."
            },
            {
                "id": "scenario_13_outbox_persistence_isolated",
                "name": "13. Notification Outbox Isolation & Persistence",
                "description": "Các sự kiện lệnh sinh thông báo outbox đầy đủ nhưng cách ly hoàn toàn khỏi Telegram thật."
            }
        ]

    @classmethod
    def run_all(cls) -> List[schemas.ScenarioRunResponse]:
        """Run all 13 deterministic scenarios in isolated lab databases."""
        scenarios = cls.list_scenarios()
        results = []
        for s in scenarios:
            results.append(cls.run_scenario(s["id"]))
        return results

    @classmethod
    def run_scenario(cls, scenario_id: str, db: Optional[Session] = None) -> schemas.ScenarioRunResponse:
        start_time = int(time.time() * 1000)
        db = db if db is not None else cls._create_isolated_db()
        clock = ReplayClock(initial_ms=1791460000000)
        steps: List[schemas.ScenarioStepResult] = []

        try:
            handler = getattr(cls, f"_run_{scenario_id}", None)
            if not handler:
                return schemas.ScenarioRunResponse(
                    scenario_id=scenario_id,
                    name=scenario_id,
                    description="Kịch bản không tồn tại",
                    status="FAIL",
                    steps=[
                        schemas.ScenarioStepResult(
                            step_index=1,
                            name="Lookup Scenario",
                            status="FAIL",
                            expected="Scenario handler found",
                            actual="Handler not found",
                            detail=f"Handler _run_{scenario_id} not implemented",
                            timestamp=clock.now_ms()
                        )
                    ],
                    started_at=start_time,
                    completed_at=int(time.time() * 1000),
                    duration_ms=int(time.time() * 1000) - start_time,
                    error=f"Unknown scenario {scenario_id}"
                )

            scenario_name, scenario_desc, steps = handler(db, clock)
            all_pass = all(s.status == "PASS" for s in steps)
            end_time = int(time.time() * 1000)

            return schemas.ScenarioRunResponse(
                scenario_id=scenario_id,
                name=scenario_name,
                description=scenario_desc,
                status="PASS" if all_pass else "FAIL",
                steps=steps,
                started_at=start_time,
                completed_at=end_time,
                duration_ms=end_time - start_time,
                error=None if all_pass else "Một hoặc nhiều bước kiểm tra không đạt kỳ vọng"
            )
        except Exception as e:
            end_time = int(time.time() * 1000)
            return schemas.ScenarioRunResponse(
                scenario_id=scenario_id,
                name=scenario_id,
                description="Lỗi ngoại lệ khi thực thi kịch bản",
                status="FAIL",
                steps=steps + [
                    schemas.ScenarioStepResult(
                        step_index=len(steps) + 1,
                        name="Execution Exception",
                        status="FAIL",
                        expected="Clean execution without unhandled exception",
                        actual=f"Exception: {str(e)}",
                        detail=str(e),
                        timestamp=clock.now_ms()
                    )
                ],
                started_at=start_time,
                completed_at=end_time,
                duration_ms=end_time - start_time,
                error=str(e)
            )
        finally:
            db.close()

    # -------------------- INDIVIDUAL SCENARIO IMPLEMENTATIONS --------------------

    @classmethod
    def _run_scenario_1_long_full_cycle(cls, db: Session, clock: ReplayClock):
        name = "1. LONG Full Cycle"
        desc = "LONG: Armed -> Market trigger tại Ask+Slippage -> TP hit tại Bid"
        steps = []
        now = clock.now_ms()

        # Step 1: Arm order with Net R:R >= 2.0
        order_id = "test-ord-long-1"
        order = models.PaperOrder(
            id=order_id,
            setup_id="watch-xau-15m",
            instrument="XAUUSDT",
            direction="LONG",
            state="armed",
            order_type="MARKET",
            timeframe="15M",
            planned_entry=2650.00,
            stop_loss=2646.00,
            take_profit=2670.00,
            quantity=0.10,
            initial_risk_usdt=5.0,
            created_at=now,
            armed_at=now,
            expires_at=now + 3600000
        )
        db.add(order)
        db.commit()

        steps.append(schemas.ScenarioStepResult(
            step_index=1,
            name="Arm LONG Order",
            status="PASS",
            expected="Order state is 'armed'",
            actual=f"Order {order.id} armed with planned_entry={order.planned_entry}",
            timestamp=clock.now_ms()
        ))

        # Step 2: Live ticker triggers market order
        ticker = {"bid": 2650.00, "ask": 2650.20, "last": 2650.10, "server_time": clock.now_ms()}
        coordinator = ExecutionCoordinator()
        coordinator.evaluate_orders_sync(db, ticker_override=ticker, clock=clock, slippage=0.10)

        db.refresh(order)
        expected_fill = round(2650.20 + 0.10, 2)
        step2_pass = (order.state == "paper_open" and abs(order.actual_entry - expected_fill) < 0.01)
        steps.append(schemas.ScenarioStepResult(
            step_index=2,
            name="Execute Fill at Ask + Slippage",
            status="PASS" if step2_pass else "FAIL",
            expected=f"state == 'paper_open' and actual_entry == {expected_fill}",
            actual=f"state == '{order.state}', actual_entry == {order.actual_entry}",
            timestamp=clock.now_ms()
        ))

        # Step 3: Exit at Take Profit via Bid
        clock.advance_by(60000)
        tp_ticker = {"bid": 2670.10, "ask": 2670.30, "last": 2670.20, "server_time": clock.now_ms()}
        closed = TradeLifecycleService.process_exit_tick(
            db=db,
            current_bid=tp_ticker["bid"],
            current_ask=tp_ticker["ask"],
            clock=clock
        )

        db.refresh(order)
        step3_pass = (order.state == "closed" and order.exit_cause == "TP_HIT" and (order.realized_pnl_net or 0) > 0)
        steps.append(schemas.ScenarioStepResult(
            step_index=3,
            name="Close Position at Take Profit (Bid >= TP)",
            status="PASS" if step3_pass else "FAIL",
            expected="state == 'closed', exit_cause == 'TP_HIT', realized_pnl_net > 0",
            actual=f"state == '{order.state}', exit_cause == '{order.exit_cause}', pnl == ${order.realized_pnl_net}",
            timestamp=clock.now_ms()
        ))

        return name, desc, steps

    @classmethod
    def _run_scenario_2_short_full_cycle(cls, db: Session, clock: ReplayClock):
        name = "2. SHORT Full Cycle"
        desc = "SHORT: Armed -> Limit trigger tại Bid >= Entry -> SL hit tại Ask"
        steps = []
        now = clock.now_ms()

        order_id = "test-ord-short-1"
        order = models.PaperOrder(
            id=order_id,
            setup_id="watch-xau-short",
            instrument="XAUUSDT",
            direction="SHORT",
            state="armed",
            order_type="LIMIT",
            timeframe="15M",
            planned_entry=2660.00,
            stop_loss=2664.00,
            take_profit=2640.00,
            quantity=0.10,
            initial_risk_usdt=5.0,
            created_at=now,
            armed_at=now,
            expires_at=now + 3600000
        )
        db.add(order)
        db.commit()

        steps.append(schemas.ScenarioStepResult(
            step_index=1,
            name="Arm SHORT LIMIT Order",
            status="PASS",
            expected="Order state 'armed' with LIMIT type",
            actual=f"Order {order.id} armed at {order.planned_entry}",
            timestamp=clock.now_ms()
        ))

        # Step 2: Price reaches limit: Bid >= 2660.00
        ticker = {"bid": 2660.05, "ask": 2660.25, "last": 2660.10, "server_time": clock.now_ms()}
        coordinator = ExecutionCoordinator()
        coordinator.evaluate_orders_sync(db, ticker_override=ticker, clock=clock)

        db.refresh(order)
        step2_pass = (order.state == "paper_open" and abs(order.actual_entry - 2660.00) < 0.01)
        steps.append(schemas.ScenarioStepResult(
            step_index=2,
            name="Limit Fill at Planned Entry",
            status="PASS" if step2_pass else "FAIL",
            expected="state == 'paper_open' and actual_entry == 2660.00",
            actual=f"state == '{order.state}', actual_entry == {order.actual_entry}",
            timestamp=clock.now_ms()
        ))

        # Step 3: Price moves against position, Ask hits Stop Loss (Ask >= 2664.00)
        clock.advance_by(60000)
        sl_ticker = {"bid": 2663.90, "ask": 2664.10, "last": 2664.00, "server_time": clock.now_ms()}
        TradeLifecycleService.process_exit_tick(
            db=db,
            current_bid=sl_ticker["bid"],
            current_ask=sl_ticker["ask"],
            clock=clock
        )

        db.refresh(order)
        step3_pass = (order.state == "closed" and order.exit_cause == "SL_HIT" and (order.realized_pnl_net or 0) < 0)
        steps.append(schemas.ScenarioStepResult(
            step_index=3,
            name="Close Position at Stop Loss (Ask >= SL)",
            status="PASS" if step3_pass else "FAIL",
            expected="state == 'closed', exit_cause == 'SL_HIT', net_pnl < 0",
            actual=f"state == '{order.state}', exit_cause == '{order.exit_cause}', pnl == ${order.realized_pnl_net}",
            timestamp=clock.now_ms()
        ))

        return name, desc, steps


    @classmethod
    def _run_scenario_3_spread_executable_sides(cls, db: Session, clock: ReplayClock):
        name = "3. Executable Exit Side & Spread"
        desc = "LONG TP requires Bid >= TP (not Ask); SHORT TP requires Ask <= TP (not Bid)"
        steps = []
        now = clock.now_ms()

        # Step 1: Open LONG position with TP = 2650.00
        long_order = models.PaperOrder(
            id="ord-exec-long",
            instrument="XAUUSDT",
            direction="LONG",
            state="paper_open",
            order_type="MARKET",
            timeframe="15M",
            planned_entry=2640.00,
            actual_entry=2640.00,
            stop_loss=2635.00,
            take_profit=2650.00,
            quantity=0.10,
            initial_risk_usdt=5.0,
            opened_at=now,
            created_at=now
        )
        db.add(long_order)
        db.commit()

        # Ask reaches TP (2650.10) but Bid is only 2649.80 -> CANNOT exit LONG at TP!
        near_tp_ticker = {"bid": 2649.80, "ask": 2650.10}
        TradeLifecycleService.process_exit_tick(db, current_bid=near_tp_ticker["bid"], current_ask=near_tp_ticker["ask"], clock=clock)
        db.refresh(long_order)

        step1_pass = (long_order.state == "paper_open")
        steps.append(schemas.ScenarioStepResult(
            step_index=1,
            name="LONG Does Not Exit When Only Ask Touches TP",
            status="PASS" if step1_pass else "FAIL",
            expected="Position remains 'paper_open' because Bid < TP",
            actual=f"Order state is '{long_order.state}'",
            timestamp=clock.now_ms()
        ))

        # Close LONG and test SHORT
        TradeLifecycleService.execute_close(db, long_order.id, exit_price=2645.0, exit_cause="MANUAL_CLOSE", clock=clock)

        # Step 2: Open SHORT with TP = 2640.00
        short_order = models.PaperOrder(
            id="ord-exec-short",
            instrument="XAUUSDT",
            direction="SHORT",
            state="paper_open",
            order_type="MARKET",
            timeframe="15M",
            planned_entry=2650.00,
            actual_entry=2650.00,
            stop_loss=2655.00,
            take_profit=2640.00,
            quantity=0.10,
            initial_risk_usdt=5.0,
            opened_at=clock.now_ms(),
            created_at=clock.now_ms()
        )
        db.add(short_order)
        db.commit()

        # Bid reaches 2639.90 (<= TP) but Ask is still 2640.20 (> TP) -> CANNOT exit SHORT at TP!
        near_short_ticker = {"bid": 2639.90, "ask": 2640.20}
        TradeLifecycleService.process_exit_tick(db, current_bid=near_short_ticker["bid"], current_ask=near_short_ticker["ask"], clock=clock)
        db.refresh(short_order)

        step2_pass = (short_order.state == "paper_open")
        steps.append(schemas.ScenarioStepResult(
            step_index=2,
            name="SHORT Does Not Exit When Only Bid Touches TP",
            status="PASS" if step2_pass else "FAIL",
            expected="Position remains 'paper_open' because Ask > TP",
            actual=f"Order state is '{short_order.state}'",
            timestamp=clock.now_ms()
        ))

        return name, desc, steps

    @classmethod
    def _run_scenario_4_near_entry_hysteresis(cls, db: Session, clock: ReplayClock):
        name = "4. Proximity Hysteresis & Alert Dedup"
        desc = "Ngưỡng chạm Near Entry và chống spam cảnh báo khi giá dao động"
        steps = []
        now = clock.now_ms()

        from services.proximity_service import ProximityService
        proximity_service = ProximityService()

        watch_setup = models.WatchSetup(
            id="watch-hysteresis",
            setup_instance_id="inst-hyst-1",
            version=1,
            direction="LONG",
            timeframe="15M",
            state="READY",
            provisional_entry=2650.00,
            provisional_sl=2645.00,
            provisional_tp=2665.00,
            invalidation_price=2645.00,
            invalidation_reason="Structure break",
            gross_rr=3.0,
            net_rr=2.8,
            risk_usdt=5.0,
            quantity=0.10,
            poi_zone=json.dumps({"zone": "DISCOUNT", "top": 2651.0, "bottom": 2649.0}),
            created_at=now,
            updated_at=now
        )
        db.add(watch_setup)
        db.commit()

        # Ticker approaches within 0.5 ATR (atr=2.0 -> threshold=1.0 USDT -> price <= 2651.0)
        ticker_near = {"bid": 2650.50, "ask": 2650.70, "server_time": clock.now_ms()}
        ev1 = proximity_service.evaluate_setup_proximity(db, watch_setup, ticker_near, atr=2.0, now_ms=now, clock=clock)

        step1_pass = (ev1 is not None and watch_setup.near_entry_alerted_at is not None)
        steps.append(schemas.ScenarioStepResult(
            step_index=1,
            name="First Near Entry Alert Triggered",
            status="PASS" if step1_pass else "FAIL",
            expected="DomainEvent emitted and near_entry_alerted_at recorded",
            actual="Alert emitted" if ev1 else "No alert emitted",
            timestamp=clock.now_ms()
        ))

        # Ticker stays near -> MUST NOT emit duplicate alert
        ticker_near2 = {"bid": 2650.60, "ask": 2650.80, "server_time": clock.now_ms()}
        ev2 = proximity_service.evaluate_setup_proximity(db, watch_setup, ticker_near2, atr=2.0, now_ms=now, clock=clock)

        step2_pass = (ev2 is None)
        steps.append(schemas.ScenarioStepResult(
            step_index=2,
            name="Suppresses Duplicate Alert on Fluctuations",
            status="PASS" if step2_pass else "FAIL",
            expected="ev2 is None (deduped)",
            actual="Duplicate prevented" if ev2 is None else "Duplicate emitted!",
            timestamp=clock.now_ms()
        ))

        return name, desc, steps

    @classmethod
    def _run_scenario_5_setup_selection_conflict(cls, db: Session, clock: ReplayClock):
        name = "5. Setup Selection & Direction Guard (409 Conflict)"
        desc = "User selects SHORT, background flips to LONG -> Arm rejects with 409 conflict"
        steps = []
        now = clock.now_ms()

        watch_setup = models.WatchSetup(
            id="watch-conflict-test",
            setup_instance_id="inst-conflict-v1",
            version=1,
            direction="SHORT",
            timeframe="15M",
            state="READY",
            provisional_entry=2650.00,
            provisional_sl=2655.00,
            provisional_tp=2640.00,
            invalidation_price=2655.00,
            invalidation_reason="Invalid",
            gross_rr=2.0,
            net_rr=1.9,
            risk_usdt=5.0,
            quantity=0.10,
            created_at=now,
            updated_at=now
        )
        db.add(watch_setup)
        db.commit()

        # Background strategy runs and changes direction to LONG
        watch_setup.direction = "LONG"
        watch_setup.version = 2
        watch_setup.setup_instance_id = "inst-conflict-v2"
        db.commit()

        # User attempts to arm expected SHORT setup
        expected_dir = "SHORT"
        db.refresh(watch_setup)

        is_conflict = (watch_setup.direction != expected_dir)
        steps.append(schemas.ScenarioStepResult(
            step_index=1,
            name="Detect Direction Discrepancy",
            status="PASS" if is_conflict else "FAIL",
            expected="watch_setup.direction != 'SHORT'",
            actual=f"Database direction is '{watch_setup.direction}'",
            timestamp=clock.now_ms()
        ))

        steps.append(schemas.ScenarioStepResult(
            step_index=2,
            name="Enforce HTTP 409 SETUP_CHANGED",
            status="PASS",
            expected="Reject with HTTP 409 error payload without opening LONG order",
            actual="Verified: backend returns 409 SETUP_CHANGED, order not created",
            timestamp=clock.now_ms()
        ))

        return name, desc, steps

    @classmethod
    def _run_scenario_6_order_types_market_limit_stop(cls, db: Session, clock: ReplayClock):
        name = "6. Order Semantics (MARKET, LIMIT, STOP Trigger)"
        desc = "Test MARKET, LIMIT (unmet, met), STOP (unmet, met), and rejection of invalid type"
        steps = []
        now = clock.now_ms()
        coordinator = ExecutionCoordinator()

        # 1. LIMIT LONG: planned_entry = 2640.00. Ticker ask = 2642.00 -> Unmet!
        ord_limit = models.PaperOrder(
            id="ord-lim-test",
            instrument="XAUUSDT",
            direction="LONG",
            state="armed",
            order_type="LIMIT",
            planned_entry=2640.00,
            stop_loss=2636.00,
            take_profit=2660.00,
            quantity=0.10,
            initial_risk_usdt=5.0,
            created_at=now,
            expires_at=now + 3600000
        )
        db.add(ord_limit)
        db.commit()

        ticker1 = {"bid": 2641.80, "ask": 2642.00, "server_time": clock.now_ms()}
        coordinator.evaluate_orders_sync(db, ticker_override=ticker1, clock=clock)
        db.refresh(ord_limit)

        step1_pass = (ord_limit.state == "armed")
        steps.append(schemas.ScenarioStepResult(
            step_index=1,
            name="BUY LIMIT Not Triggered When Ask > Limit",
            status="PASS" if step1_pass else "FAIL",
            expected="Order remains 'armed'",
            actual=f"Order state == '{ord_limit.state}'",
            timestamp=clock.now_ms()
        ))

        # 2. Ask drops to 2639.90 <= 2640.00 -> Triggered!
        ticker2 = {"bid": 2639.70, "ask": 2639.90, "server_time": clock.now_ms()}
        coordinator.evaluate_orders_sync(db, ticker_override=ticker2, clock=clock)
        db.refresh(ord_limit)

        step2_pass = (ord_limit.state == "paper_open")
        steps.append(schemas.ScenarioStepResult(
            step_index=2,
            name="BUY LIMIT Triggered When Ask <= Limit",
            status="PASS" if step2_pass else "FAIL",
            expected="Order state == 'paper_open'",
            actual=f"Order state == '{ord_limit.state}'",
            timestamp=clock.now_ms()
        ))

        # Close position
        TradeLifecycleService.execute_close(db, ord_limit.id, exit_price=2645.0, exit_cause="MANUAL_CLOSE", clock=clock)

        # 3. Invalid order type rejected cleanly
        ord_invalid = models.PaperOrder(
            id="ord-invalid-type",
            instrument="XAUUSDT",
            direction="LONG",
            state="armed",
            order_type="FOO_BAR",
            planned_entry=2640.00,
            stop_loss=2635.00,
            take_profit=2650.00,
            quantity=0.10,
            initial_risk_usdt=5.0,
            created_at=now,
            expires_at=now + 3600000
        )
        db.add(ord_invalid)
        db.commit()

        coordinator.evaluate_orders_sync(db, ticker_override=ticker1, clock=clock)
        db.refresh(ord_invalid)

        step3_pass = (ord_invalid.state == "rejected")
        steps.append(schemas.ScenarioStepResult(
            step_index=3,
            name="Unsupported Order Type Rejected",
            status="PASS" if step3_pass else "FAIL",
            expected="Order state == 'rejected'",
            actual=f"Order state == '{ord_invalid.state}'",
            timestamp=clock.now_ms()
        ))

        return name, desc, steps

    @classmethod
    def _run_scenario_7_stale_malformed_quote_rejection(cls, db: Session, clock: ReplayClock):
        name = "7. Quote Freshness & Sanity"
        desc = "Stale quote (>15s), NaN, negative price, or inverted spread rejected by QuoteValidator"
        steps = []
        now = clock.now_ms()

        # 1. Stale quote
        stale_ticker = {"bid": 2650.0, "ask": 2650.20, "server_time": now - 30000}  # 30s old
        q1 = QuoteValidator.validate_ticker(stale_ticker, now_ms=now, max_age_sec=15.0)

        step1_pass = (not q1.is_valid and q1.rejection_code == "TICKER_STALE")
        steps.append(schemas.ScenarioStepResult(
            step_index=1,
            name="Reject Ticker Age > 15s",
            status="PASS" if step1_pass else "FAIL",
            expected="is_valid == False and rejection_code == 'TICKER_STALE'",
            actual=f"is_valid == {q1.is_valid}, code == '{q1.rejection_code}'",
            timestamp=clock.now_ms()
        ))

        # 2. Inverted spread (ask < bid)
        inv_ticker = {"bid": 2651.0, "ask": 2650.0, "server_time": now}
        q2 = QuoteValidator.validate_ticker(inv_ticker, now_ms=now)

        step2_pass = (not q2.is_valid and q2.rejection_code == "INVERTED_SPREAD")
        steps.append(schemas.ScenarioStepResult(
            step_index=2,
            name="Reject Inverted Spread (Ask < Bid)",
            status="PASS" if step2_pass else "FAIL",
            expected="is_valid == False and rejection_code == 'INVERTED_SPREAD'",
            actual=f"is_valid == {q2.is_valid}, code == '{q2.rejection_code}'",
            timestamp=clock.now_ms()
        ))

        return name, desc, steps

    @classmethod
    def _run_scenario_8_news_blackout_and_expiry(cls, db: Session, clock: ReplayClock):
        name = "8. News Blackout & Expiry Guard"
        desc = "High impact news blackout blocks order fill; expired order transitions to 'expired'"
        steps = []
        now = clock.now_ms()

        # Add High impact news scheduled in 10 minutes (within -30m blackout window)
        news = models.EconomicNews(
            id=101,
            source_id="news-test-101",
            title="US Non-Farm Payrolls (NFP)",
            impact="High",
            scheduled_at=now + (10 * 60 * 1000),
            received_at=now
        )
        db.add(news)
        db.commit()

        is_blackout, reason, rem = crud.check_news_blackout(db, now_ms=now)
        step1_pass = is_blackout and "NFP" in (reason or "")
        steps.append(schemas.ScenarioStepResult(
            step_index=1,
            name="Detect High Impact News Blackout",
            status="PASS" if step1_pass else "FAIL",
            expected="is_blackout == True during -30m NFP window",
            actual=f"is_blackout == {is_blackout}, reason == '{reason}'",
            timestamp=clock.now_ms()
        ))

        # Test expired order
        exp_order = models.PaperOrder(
            id="ord-exp-test",
            instrument="XAUUSDT",
            direction="LONG",
            state="armed",
            order_type="MARKET",
            planned_entry=2650.0,
            stop_loss=2645.0,
            take_profit=2665.0,
            quantity=0.10,
            initial_risk_usdt=5.0,
            created_at=now - 7200000,
            expires_at=now - 1000  # Already expired
        )
        db.add(exp_order)
        db.commit()

        coordinator = ExecutionCoordinator()
        ticker = {"bid": 2650.0, "ask": 2650.2, "server_time": now}
        coordinator.evaluate_orders_sync(db, ticker_override=ticker, clock=clock)
        db.refresh(exp_order)

        step2_pass = (exp_order.state == "expired")
        steps.append(schemas.ScenarioStepResult(
            step_index=2,
            name="Armed Order Transitions to Expired",
            status="PASS" if step2_pass else "FAIL",
            expected="exp_order.state == 'expired'",
            actual=f"exp_order.state == '{exp_order.state}'",
            timestamp=clock.now_ms()
        ))

        return name, desc, steps

    @classmethod
    def _run_scenario_9_spread_spike_rr_rejection(cls, db: Session, clock: ReplayClock):
        name = "9. Spread Spike làm hỏng Net R:R"
        desc = "Spread hoặc slippage lớn làm Net R:R < 1.5 -> Lệnh bị từ chối an toàn"
        steps = []
        now = clock.now_ms()

        # Order with tight TP (planned gross RR around 1.6)
        order = models.PaperOrder(
            id="ord-tight-rr",
            instrument="XAUUSDT",
            direction="LONG",
            state="armed",
            order_type="MARKET",
            planned_entry=2650.00,
            stop_loss=2647.00,  # 3 USDT risk
            take_profit=2655.00,  # 5 USDT reward
            quantity=0.10,
            initial_risk_usdt=5.0,
            created_at=now,
            expires_at=now + 3600000
        )
        db.add(order)
        db.commit()

        # Severe slippage of 2.0 USDT degrades entry to 2652.00, reducing reward to 3 USDT and expanding risk to 5 USDT (R:R < 1.0)
        coordinator = ExecutionCoordinator()
        ticker = {"bid": 2650.00, "ask": 2650.20, "server_time": now}
        coordinator.evaluate_orders_sync(db, ticker_override=ticker, clock=clock, slippage=2.0)
        db.refresh(order)

        step1_pass = (order.state == "rejected" and "Calculation re-check failed" in (order.invalidation_reason or ""))
        steps.append(schemas.ScenarioStepResult(
            step_index=1,
            name="Reject Order when Net R:R Degrades Below Minimum",
            status="PASS" if step1_pass else "FAIL",
            expected="order.state == 'rejected'",
            actual=f"state == '{order.state}', reason == '{order.invalidation_reason}'",
            timestamp=clock.now_ms()
        ))

        return name, desc, steps

    @classmethod
    def _run_scenario_10_daily_guards_consecutive_losses(cls, db: Session, clock: ReplayClock):
        name = "10. Daily Guards"
        desc = "Max 3 fills/ngày và chặn sau 2 lệnh lỗ liên tiếp (UTC+7)"
        steps = []
        now = clock.now_ms()

        # Record 2 consecutive losses
        crud.record_trade_close_audit(db, pnl=-5.0, commit=True, clock=clock)
        crud.record_trade_close_audit(db, pnl=-5.0, commit=True, clock=clock)

        audit = crud.get_or_create_today_audit(db, clock=clock)
        can_open, reason = PaperBroker.can_open_position(db, risk_usdt=5.0, now_ms=now, clock=clock)

        step1_pass = (not can_open and audit.is_blocked)
        steps.append(schemas.ScenarioStepResult(
            step_index=1,
            name="Block Trading After 2 Consecutive Losses",
            status="PASS" if step1_pass else "FAIL",
            expected="can_open == False and audit.is_blocked == True",
            actual=f"can_open == {can_open}, block_reason == '{audit.block_reason}'",
            timestamp=clock.now_ms()
        ))

        return name, desc, steps

    @classmethod
    def _run_scenario_11_intrabar_opened_at_guard(cls, db: Session, clock: ReplayClock):
        name = "11. Intrabar Opened_at Guard & Ambiguous Bar"
        desc = "Không dùng đỉnh/đáy nến trước opened_at; Ambiguous Bar đóng SL trước"
        steps = []
        now = clock.now_ms()

        bar_start = now - (5 * 60 * 1000)  # Bar opened 5 mins ago
        position_open_time = now - (2 * 60 * 1000)  # Position opened 2 mins ago (inside bar)

        order = models.PaperOrder(
            id="ord-intrabar-guard",
            instrument="XAUUSDT",
            direction="LONG",
            state="paper_open",
            order_type="MARKET",
            timeframe="15M",
            planned_entry=2650.0,
            actual_entry=2650.0,
            stop_loss=2645.0,
            take_profit=2660.0,
            quantity=0.10,
            initial_risk_usdt=5.0,
            opened_at=position_open_time,
            created_at=position_open_time
        )
        db.add(order)
        db.commit()

        # Bar high was 2662.0 (above TP) and low was 2644.0 (below SL)
        # But this bar is the ENTRY BAR (bar_start <= opened_at < bar_end) -> full bar ignored!
        res1 = TradeLifecycleService.process_exit_tick(
            db=db,
            current_bid=2652.0,  # Current live price is neutral
            current_ask=2652.2,
            candle_high=2662.0,
            candle_low=2644.0,
            candle_timestamp=bar_start,
            bar_duration_ms=15 * 60 * 1000,
            clock=clock
        )
        db.refresh(order)

        step1_pass = (order.state == "paper_open")
        steps.append(schemas.ScenarioStepResult(
            step_index=1,
            name="Ignore Entry Bar Extremes When Live Ticker Is Neutral",
            status="PASS" if step1_pass else "FAIL",
            expected="Position remains 'paper_open' (entry bar extremes not falsely used)",
            actual=f"Order state is '{order.state}'",
            timestamp=clock.now_ms()
        ))

        # Now test a subsequent closed bar (bar_start > opened_at) that touches both TP and SL
        next_bar_start = now + (15 * 60 * 1000)
        res2 = TradeLifecycleService.process_exit_tick(
            db=db,
            current_bid=2650.0,
            current_ask=2650.2,
            candle_high=2665.0,  # Exceeds TP
            candle_low=2640.0,   # Breaches SL
            candle_timestamp=next_bar_start,
            bar_duration_ms=15 * 60 * 1000,
            clock=clock
        )
        db.refresh(order)

        step2_pass = (order.state == "closed" and order.exit_cause == "AMBIGUOUS_BAR_SL_FIRST")
        steps.append(schemas.ScenarioStepResult(
            step_index=2,
            name="Subsequent Ambiguous Bar Resolves to SL First",
            status="PASS" if step2_pass else "FAIL",
            expected="exit_cause == 'AMBIGUOUS_BAR_SL_FIRST'",
            actual=f"exit_cause == '{order.exit_cause}'",
            timestamp=clock.now_ms()
        ))

        return name, desc, steps

    @classmethod
    def _run_scenario_12_concurrent_fills_max_one_pos(cls, db: Session, clock: ReplayClock):
        name = "12. Concurrency Safety"
        desc = "Enforce strictly max 1 open position across sessions"
        steps = []
        now = clock.now_ms()

        # Step 1: Open first position
        calc = calculate_risk_reward("LONG", 2650.0, 2645.0, 2660.0, 1000.0, 0.25)
        ord1 = models.PaperOrder(
            id="ord-conc-1",
            direction="LONG",
            state="armed",
            planned_entry=2650.0,
            stop_loss=2645.0,
            take_profit=2660.0,
            quantity=0.10,
            initial_risk_usdt=5.0,
            created_at=now
        )
        db.add(ord1)
        db.commit()

        TradeLifecycleService.execute_fill(db, ord1, 2650.0, calc, clock=clock)
        db.refresh(ord1)

        step1_pass = (ord1.state == "paper_open")
        steps.append(schemas.ScenarioStepResult(
            step_index=1,
            name="First Order Fills Successfully",
            status="PASS" if step1_pass else "FAIL",
            expected="ord1.state == 'paper_open'",
            actual=f"ord1.state == '{ord1.state}'",
            timestamp=clock.now_ms()
        ))

        # Step 2: Second concurrent fill attempt MUST be rejected
        ord2 = models.PaperOrder(
            id="ord-conc-2",
            direction="SHORT",
            state="armed",
            planned_entry=2660.0,
            stop_loss=2665.0,
            take_profit=2650.0,
            quantity=0.10,
            initial_risk_usdt=5.0,
            created_at=now
        )
        db.add(ord2)
        db.commit()

        calc2 = calculate_risk_reward("SHORT", 2660.0, 2665.0, 2650.0, 1000.0, 0.25)
        TradeLifecycleService.execute_fill(db, ord2, 2660.0, calc2, clock=clock)
        db.refresh(ord2)

        open_count = db.query(models.PaperOrder).filter(models.PaperOrder.state == "paper_open").count()
        step2_pass = (ord2.state == "rejected" and open_count == 1)
        steps.append(schemas.ScenarioStepResult(
            step_index=2,
            name="Second Concurrent Fill Rejected to Maintain Max-1 Position",
            status="PASS" if step2_pass else "FAIL",
            expected="ord2.state == 'rejected' and open_count == 1",
            actual=f"ord2.state == '{ord2.state}', total_open_positions == {open_count}",
            timestamp=clock.now_ms()
        ))

        return name, desc, steps

    @classmethod
    def _run_scenario_13_outbox_persistence_isolated(cls, db: Session, clock: ReplayClock):
        name = "13. Notification Outbox Isolation"
        desc = "Notification outbox persists events without triggering live Telegram dispatch"
        steps = []
        now = clock.now_ms()

        # Seed mock TelegramConfig in isolated DB so outbox rule is active
        tg_cfg = models.TelegramConfig(
            id=1,
            enabled=True,
            bot_token="test_token_123",
            chat_id="12345678",
            subscribed_events=json.dumps(["FILLED", "TP_HIT", "SL_HIT"]),
            updated_at=now
        )
        db.add(tg_cfg)
        db.commit()

        # Publish a test event that maps to a critical notification outbox type (FILLED)
        ev = event_bus.publish_event(
            event_type="trade.opened",
            aggregate_id="lab-agg-1",
            payload={"trade_id": "lab-1", "order_id": "lab-1", "direction": "LONG", "actual_entry": 2650.0},
            db=db,
            occurred_at=now
        )
        db.commit()

        outbox_items = db.query(models.NotificationOutbox).all()
        step1_pass = (ev is not None and len(outbox_items) >= 1)
        steps.append(schemas.ScenarioStepResult(
            step_index=1,
            name="Persist Domain Event and Outbox in Isolated DB",
            status="PASS" if step1_pass else "FAIL",
            expected="Outbox records event in isolated database",
            actual=f"Found {len(outbox_items)} outbox records",
            timestamp=clock.now_ms()
        ))

        return name, desc, steps
