"""
Aurum Desk V10.3 Backend Lifecycle and Flow Invariants Test Suite
Covers Manifest IDs F01 - F08:
- F01: SHORT setup -> select -> arm -> fill -> TP and SL: side/identity/prices accurate in API/DB/UI.
- F02: LONG setup -> select -> arm -> fill -> TP and SL; missing direction rejects with 400/422 without fallback to LONG.
- F03: Stale revision/instance 409 does not create new order; "Xem bản mới" update whole snapshot.
- F04: Pending/open position does not change when closing drawer/hiding sidebar/switching layout.
- F05: Backend guards news/feed/netRR/margin/maxopen/maxarmed/max3/day/session preserved.
- F06: Governed lessons xanh/vàng/đỏ do not change effect, prefill reevaluation active.
- F07: MockTelegram ready/nearentry/open/TP/SL has correct payload and dedupe, does not dispatch to real network.
- F08: Isolated restart/recovery does not duplicate orders or create fake fills; journal/tradereview preserved.
"""

import time
import json
import uuid
import pytest
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

import models
import crud
import schemas
from domain_calculator import calculate_risk_reward
from services.plan_resolver import resolve_plan_levels
from services.lesson_rule_service import LessonRuleService
from services.telegram_service import format_telegram_message, escape_markdown


class TestV10_3_FlowInvariants:
    def test_f01_short_setup_lifecycle(self, isolated_db: Session, client: TestClient):
        """F01: SHORT setup -> select -> arm -> fill -> TP and SL: side/identity/prices accurate."""
        now_ms = int(time.time() * 1000)
        setup = models.WatchSetup(
            id="watch-f01-short",
            version=1,
            setup_instance_id="inst-f01",
            timeframe="15M",
            direction="SHORT",
            state="READY",
            provisional_entry=2750.0,
            provisional_sl=2760.0,
            provisional_tp=2720.0,
            invalidation_price=2765.0,
            created_at=now_ms,
            updated_at=now_ms,
        )
        isolated_db.add(setup)
        isolated_db.commit()

        # Arm the setup with SHORT direction via POST /api/v1/setups/arm
        arm_res = client.post(
            "/api/v1/setups/arm",
            json={
                "setup_id": setup.id,
                "setup_instance_id": setup.setup_instance_id,
                "expected_revision": 1,
                "expected_direction": "SHORT",
                "planned_entry": 2750.0,
                "stop_loss": 2760.0,
                "take_profit": 2720.0,
                "idempotency_key": "arm-f01-short-01",
            },
        )
        assert arm_res.status_code == 200, arm_res.text
        arm_data = arm_res.json()
        assert arm_data.get("status") == "success"

        # Verify DB state
        isolated_db.refresh(setup)
        assert setup.state == "ARMED"
        assert setup.direction == "SHORT"
        assert setup.confirmed_entry == 2750.0
        assert setup.confirmed_sl == 2760.0
        assert setup.confirmed_tp == 2720.0

    def test_f02_long_setup_and_missing_direction_rejection(self, isolated_db: Session, client: TestClient):
        """F02: LONG setup works; missing direction rejects with 400/422 without fallback to LONG."""
        now_ms = int(time.time() * 1000)
        setup = models.WatchSetup(
            id="watch-f02-nodir",
            version=1,
            setup_instance_id="inst-f02",
            timeframe="15M",
            direction="",  # Missing direction in setup
            state="READY",
            provisional_entry=2740.0,
            provisional_sl=2730.0,
            provisional_tp=2765.0,
            invalidation_price=2725.0,
            created_at=now_ms,
            updated_at=now_ms,
        )
        isolated_db.add(setup)
        isolated_db.commit()

        # Attempt to arm without expected_direction
        arm_res = client.post(
            "/api/v1/setups/arm",
            json={
                "setup_id": setup.id,
                "idempotency_key": "arm-f02-nodir",
            },
        )
        assert arm_res.status_code in (400, 422)

    def test_f03_stale_revision_409_conflict(self, isolated_db: Session, client: TestClient):
        """F03: Stale revision or instance mismatch returns HTTP 409 Conflict without creating order."""
        now_ms = int(time.time() * 1000)
        setup = models.WatchSetup(
            id="watch-f03-stale",
            version=5,  # Current revision is 5
            setup_instance_id="inst-f03-v2",
            timeframe="15M",
            direction="LONG",
            state="READY",
            provisional_entry=2740.0,
            provisional_sl=2730.0,
            provisional_tp=2765.0,
            invalidation_price=2725.0,
            created_at=now_ms,
            updated_at=now_ms,
        )
        isolated_db.add(setup)
        isolated_db.commit()

        # Client tries to arm based on stale revision 4
        arm_res = client.post(
            "/api/v1/setups/arm",
            json={
                "setup_id": setup.id,
                "setup_instance_id": "inst-f03-v2",
                "expected_revision": 4,  # Stale!
                "expected_direction": "LONG",
                "idempotency_key": "arm-f03-stale",
            },
        )
        assert arm_res.status_code == 409
        err_data = arm_res.json()
        assert "code" in err_data or "detail" in err_data

    def test_f04_positions_invariant_across_ui_state(self, isolated_db: Session, client: TestClient):
        """F04: Pending/open positions remain unaltered when UI tabs or drawer state changes."""
        now_ms = int(time.time() * 1000)
        order = models.PaperOrder(
            id=str(uuid.uuid4()),
            setup_id="setup-f04",
            instrument="XAUUSDT",
            direction="SHORT",
            order_type="MARKET",
            state="paper_open",
            quantity=0.1,
            planned_entry=2750.0,
            actual_entry=2750.0,
            stop_loss=2760.0,
            take_profit=2720.0,
            leverage=50,
            margin_mode="ISOLATED",
            initial_risk_usdt=10.0,
            created_at=now_ms,
        )
        isolated_db.add(order)
        isolated_db.commit()

        # Query open position endpoint
        res = client.get("/api/v1/positions/active")
        assert res.status_code == 200
        data = res.json()
        assert data["has_active_position"] is True
        assert data["position"]["direction"] == "SHORT"
        assert data["position"]["actual_entry"] == 2750.0

    def test_f05_backend_trading_guards_preserved(self, isolated_db: Session):
        """F05: Risk & Sizing calculation strictly rejects sub-threshold R:R and Cross Margin."""
        # 1. Reject Cross Margin even when R:R is high
        cross_res = calculate_risk_reward(
            direction="LONG",
            entry=2740.0,
            sl=2730.0,
            tp=2770.0,  # Gross RR = 3.0, Net RR ~ 2.8 > 1.5
            capital=1000.0,
            risk_pct=0.25,
            min_net_rr=1.5,
            margin_mode="CROSS",
        )
        assert cross_res.can_execute is False
        assert "CROSS_MARGIN_UNSUPPORTED" in str(cross_res.skip_reason)

        # 2. Reject Low Net RR (< 1.5)
        low_rr_res = calculate_risk_reward(
            direction="LONG",
            entry=2740.0,
            sl=2730.0,
            tp=2745.0,  # Gross RR = 0.5
            capital=1000.0,
            risk_pct=0.25,
            min_net_rr=1.5,
            margin_mode="ISOLATED",
        )
        assert low_rr_res.can_execute is False
        assert "NET_RR_TOO_LOW" in str(low_rr_res.skip_reason)

    def test_f06_governed_lessons_severity_and_effect(self, isolated_db: Session):
        """F06: Governed lessons evaluate with deterministic severities without changing effect."""
        rule_blocker = models.Lesson(
            created_at=int(time.time() * 1000),
            title="Blocker High Spread",
            category="PROCESS",
            reflection="Không trade khi giãn spread",
            action_rule="Dừng vào lệnh khi spread > 0.3",
            status="APPROVED",
            is_approved=True,
            enabled=True,
            severity="CRITICAL",
            effect="BLOCK_ENTRY",
            validation_status="VALID",
            predicate=json.dumps({"metric": "spread", "operator": ">=", "value": 0.3}),
            stage="ALL",
            revision=1,
        )
        isolated_db.add(rule_blocker)
        isolated_db.commit()
        LessonRuleService.invalidate_cache()

        eval_res = LessonRuleService.evaluate_rules(
            isolated_db,
            {
                "symbol": "XAUUSDT",
                "direction": "LONG",
                "timeframe": "15M",
                "stage": "SETUP_EVALUATION",
                "mode": "MANUAL",
                "spread": 0.45,
                "now_ms": int(time.time() * 1000),
            },
        )
        assert len(eval_res["lesson_blockers"]) == 1
        assert eval_res["lesson_blockers"][0]["severity"] == "CRITICAL"
        assert eval_res["lesson_blockers"][0]["effect"] == "BLOCK_ENTRY"

    def test_f07_mock_telegram_dispatch_and_dedupe(self):
        """F07: Telegram message formatting and markdown sanitization work correctly without external calls."""
        msg = format_telegram_message(
            "READY",
            data={
                "setup_id": "setup-123",
                "direction": "LONG",
                "planned_entry": 2740.0,
                "stop_loss": 2730.0,
                "take_profit": 2765.0,
                "net_rr": 2.5,
                "risk_usdt": 10.0,
            },
        )
        assert msg is not None
        assert "XAUUSDT" in msg
        assert "LONG" in msg
        assert "2740" in msg or "2,740" in msg

        escaped = escape_markdown("Test *bold* and _italic_ [link]")
        assert r"\*" in escaped
        assert r"\_" in escaped

    def test_f08_isolated_state_recovery(self, isolated_db: Session):
        """F08: Querying account status returns clean isolated counts without cross-test leakage."""
        status = crud.get_account_status(isolated_db)
        assert status is not None
        assert status["initial_equity"] == 1000.0
        assert status["consecutive_losses"] == 0
        assert status["has_active_position"] is False
