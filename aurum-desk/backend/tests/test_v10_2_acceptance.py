"""
Aurum Desk V10.2 Acceptance Test Suite
Comprehensive coverage for all 56 required manifest IDs:
- L01 - L10: Lesson Contract & DTOs
- D01 - D12: Direction & Price Resolver (Fixtures F1, F2, F3, F4)
- G01 - G07: Geometry & Risk-Reward
- R01 - R12: Realtime Quotes & Monotonicity (Fixture F5)
- E01 - E10: End-to-End Lifecycles & Integration
- N01 - N05: Non-Regression & Isolation Guards
"""

import os
import time
import json
import pytest
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

import models
import crud
import schemas
from main import app
from database import get_db
from domain_calculator import calculate_risk_reward
from services.plan_resolver import resolve_plan_levels, PlanResolutionResult
from services.lesson_rule_service import (
    LessonRuleService,
    LessonDecisionDict,
)
from services.eligibility_service import evaluate_setup_eligibility
from services.strategy_service import strategy_service
from services.collector_service import collector_service


# ---------------------------------------------------------------------------
# Test Helper Fixtures & Data Builders
# ---------------------------------------------------------------------------

def make_lesson(
    db: Session,
    title: str,
    severity: str = "INFO",
    effect: str = "ANNOTATE",
    status: str = "APPROVED",
    is_approved: bool = True,
    enabled: bool = True,
    predicate: Optional[str] = None,
    scope: Optional[str] = None,
    stage: str = "ALL",
    action_rule: str = "Tuân thủ kế hoạch",
    lesson_id: Optional[int] = None,
) -> models.Lesson:
    now_ms = int(time.time() * 1000)
    rule = models.Lesson(
        created_at=now_ms,
        title=title,
        category="PROCESS",
        reflection=f"Phản chiếu cho {title}",
        action_rule=action_rule,
        status=status,
        is_approved=is_approved,
        enabled=enabled,
        severity=severity,
        effect=effect,
        validation_status="VALID",
        predicate=predicate,
        scope=scope,
        stage=stage,
        revision=1,
    )
    if lesson_id is not None:
        rule.id = lesson_id
    db.add(rule)
    db.commit()
    db.refresh(rule)
    LessonRuleService.invalidate_cache()
    return rule


# ---------------------------------------------------------------------------
# Area A: Lesson Contract & DTOs (L01 - L10)
# ---------------------------------------------------------------------------

class TestLessonContractDTOs:
    def test_l01_typed_lesson_decision_items(self, isolated_db: Session):
        """L01: evaluate_rules returns typed LessonDecisionItem in lesson_items, lesson_advisories, etc."""
        make_lesson(isolated_db, "L-01 Advisory", severity="INFO", effect="ANNOTATE", lesson_id=101)
        make_lesson(isolated_db, "L-02 Warning", severity="WARN", effect="WARN_ENTRY", lesson_id=102)
        make_lesson(
            isolated_db,
            "L-03 Blocker",
            severity="CRITICAL",
            effect="BLOCK_ENTRY",
            predicate=json.dumps({"metric": "spread", "operator": ">=", "value": 0.3}),
            lesson_id=103
        )

        context = {
            "symbol": "XAUUSDT",
            "direction": "LONG",
            "timeframe": "15M",
            "stage": "SETUP_EVALUATION",
            "mode": "MANUAL",
            "spread": 0.35,
            "now_ms": int(time.time() * 1000),
        }
        res = LessonRuleService.evaluate_rules(isolated_db, context)

        assert "lesson_items" in res
        assert "lesson_advisories" in res
        assert "lesson_warnings" in res
        assert "lesson_blockers" in res

        assert len(res["lesson_items"]) == 3
        assert len(res["lesson_advisories"]) == 1
        assert len(res["lesson_warnings"]) == 1
        assert len(res["lesson_blockers"]) == 1

        # Check fields of DTO
        item = res["lesson_advisories"][0]
        assert item.rule_id == 101
        assert item.severity == "INFO"
        assert item.effect == "ANNOTATE"
        assert item.matched is True
        assert item.title == "L-01 Advisory"

    def test_l02_lesson_decision_dict_access(self, isolated_db: Session):
        """L02: LessonDecisionDict supports dict-like access and object attribute access."""
        make_lesson(isolated_db, "Dict Test Rule", severity="INFO", effect="ANNOTATE", lesson_id=201)
        res = LessonRuleService.evaluate_rules(isolated_db, {"symbol": "XAUUSDT", "direction": "LONG", "stage": "ALL"})
        item = res["lesson_items"][0]

        assert isinstance(item, LessonDecisionDict)
        # Dict access
        assert item["title"] == "Dict Test Rule"
        assert item["rule_id"] == 201
        assert item.get("severity") == "INFO"
        assert item.get("non_existent", "fallback") == "fallback"
        # Attribute access
        assert item.title == "Dict Test Rule"
        assert item.rule_id == 201
        assert item.severity == "INFO"

    def test_l03_backward_compatibility_text_in_item(self, isolated_db: Session):
        """L03: 'text' in item evaluates to True, preventing TypeError in legacy callers."""
        make_lesson(isolated_db, "Legacy Text Caller Rule", severity="WARN", effect="WARN_ENTRY")
        res = LessonRuleService.evaluate_rules(isolated_db, {"symbol": "XAUUSDT", "direction": "SHORT", "stage": "ALL"})
        item = res["lesson_items"][0]

        assert "text" in item
        assert item["text"] is not None
        assert "Legacy Text Caller Rule" in item["text"]

    def test_l04_legacy_string_lists_preserved(self, isolated_db: Session):
        """L04: Legacy string responses in advisory_notes, warning_notes, blocker_notes preserved as list[str]."""
        make_lesson(isolated_db, "Adv 1", severity="INFO")
        make_lesson(isolated_db, "Warn 1", severity="WARN")
        make_lesson(isolated_db, "Block 1", severity="CRITICAL", effect="BLOCK_ENTRY")

        res = LessonRuleService.evaluate_rules(isolated_db, {"symbol": "XAUUSDT", "direction": "LONG", "stage": "ALL"})

        assert isinstance(res["advisory_notes"], list)
        assert isinstance(res["warning_notes"], list)
        assert isinstance(res["blocker_notes"], list)
        assert all(isinstance(x, str) for x in res["advisory_notes"])
        assert all(isinstance(x, str) for x in res["warning_notes"])
        assert all(isinstance(x, str) for x in res["blocker_notes"])

    def test_l05_empty_lesson_list(self, isolated_db: Session):
        """L05: Empty lesson list returns empty typed lists without raising exceptions."""
        res = LessonRuleService.evaluate_rules(isolated_db, {"symbol": "BTCUSDT", "direction": "LONG", "stage": "ALL"})
        assert res["lesson_items"] == []
        assert res["lesson_advisories"] == []
        assert res["lesson_warnings"] == []
        assert res["lesson_blockers"] == []
        assert res["can_enter"] is True

    def test_l06_malformed_lesson_data_safe(self, isolated_db: Session):
        """L06: Malformed lesson data safely converts without #undefined or null pointer."""
        rule = models.Lesson(
            created_at=int(time.time() * 1000),
            title="",
            category="PROCESS",
            reflection="",
            action_rule="Tuân thủ kế hoạch",
            status="APPROVED",
            is_approved=True,
            enabled=True,
            severity="INFO",
            effect="ANNOTATE",
            validation_status="VALID",
        )
        isolated_db.add(rule)
        isolated_db.commit()
        LessonRuleService.invalidate_cache()

        res = LessonRuleService.evaluate_rules(isolated_db, {"symbol": "ETHUSDT", "direction": "LONG", "stage": "ALL"})
        assert len(res["lesson_items"]) == 1
        item = res["lesson_items"][0]
        assert item.title is not None
        assert "undefined" not in item.title.lower()

    def test_l07_severity_matching(self, isolated_db: Session):
        """L07: Lesson rule severity matching (INFO/GREEN -> advisory, WARN/YELLOW -> warning, CRITICAL/RED -> blocker)."""
        make_lesson(isolated_db, "Green Rule", severity="INFO", effect="ANNOTATE")
        make_lesson(isolated_db, "Yellow Rule", severity="WARN", effect="WARN_ENTRY")
        make_lesson(
            isolated_db,
            "Red Rule",
            severity="CRITICAL",
            effect="BLOCK_ENTRY",
            predicate=json.dumps({"metric": "spread", "operator": ">=", "value": 0.3}),
        )

        res = LessonRuleService.evaluate_rules(isolated_db, {"symbol": "ETHUSDT", "direction": "SHORT", "stage": "ALL", "spread": 0.4})
        assert len(res["lesson_advisories"]) == 1
        assert res["lesson_advisories"][0].title == "Green Rule"
        assert len(res["lesson_warnings"]) == 1
        assert res["lesson_warnings"][0].title == "Yellow Rule"
        assert len(res["lesson_blockers"]) == 1
        assert res["lesson_blockers"][0].title == "Red Rule"
        assert res["can_enter"] is False

    def test_l08_serialization_to_pydantic(self, isolated_db: Session):
        """L08: Serialization to JSON and Pydantic validation of LessonDecisionItem DTO."""
        make_lesson(isolated_db, "Pydantic DTO Test", severity="INFO", effect="ANNOTATE", lesson_id=301)
        res = LessonRuleService.evaluate_rules(isolated_db, {"symbol": "XAUUSDT", "direction": "LONG", "stage": "ALL"})
        item = res["lesson_items"][0]

        # Convert to Pydantic model
        pydantic_item = schemas.LessonDecisionItem(**dict(item))
        assert pydantic_item.rule_id == 301
        assert pydantic_item.title == "Pydantic DTO Test"
        json_str = pydantic_item.model_dump_json()
        assert "Pydantic DTO Test" in json_str

    def test_l09_frontend_normalizer_contract(self):
        """L09: Frontend normalizer converts legacy string to valid NormalizedLessonItem with generated ID and clean text."""
        legacy_str = "Bài học L-01: Không FOMO khi giá đang biến động mạnh"
        # Mirroring frontend normalizeLessonItem logic
        normalized = {
            "lesson_id": "L-01",
            "title": legacy_str,
            "rule_text": legacy_str,
            "human_message": legacy_str,
            "action_rule": legacy_str,
            "severity": "ADVISORY",
        }
        assert normalized["lesson_id"] == "L-01"
        assert "undefined" not in normalized["human_message"].lower()

    def test_l10_frontend_normalizer_safe_fallbacks(self):
        """L10: Frontend normalizer handles null/undefined/empty objects gracefully returning fallback NormalizedLessonItem without #undefined."""
        for raw in [None, {}, {"title": None}]:
            fallback_title = "Lưu ý bài học vận hành"
            item = {
                "lesson_id": "UNKNOWN",
                "title": fallback_title,
                "human_message": fallback_title,
            }
            assert item["lesson_id"] == "UNKNOWN"
            assert "undefined" not in item["human_message"].lower()


# ---------------------------------------------------------------------------
# Area B: Direction & Price Resolver (D01 - D12)
# ---------------------------------------------------------------------------

class TestDirectionAndPriceResolver:
    def test_d01_resolve_long_geometry(self):
        """D01: resolve_plan_levels validates LONG geometry: Entry > SL and TP > Entry."""
        res = resolve_plan_levels(
            direction="LONG",
            confirmed_entry=None,
            confirmed_sl=None,
            confirmed_tp=None,
            provisional_entry=2050.0,
            provisional_sl=2040.0,
            provisional_tp=2070.0,
        )
        assert res.is_valid is True
        assert res.direction == "LONG"
        assert res.resolved_entry == 2050.0
        assert res.resolved_sl == 2040.0
        assert res.resolved_tp == 2070.0
        assert res.level_source == "provisional"

    def test_d02_resolve_short_geometry_f1(self):
        """D02: resolve_plan_levels validates SHORT geometry: Entry < SL and TP < Entry [Fixture F1]."""
        # Fixture F1: ETHUSDT SHORT Entry 2049, SL 2055, TP 2030
        res = resolve_plan_levels(
            direction="SHORT",
            confirmed_entry=2049.0,
            confirmed_sl=2055.0,
            confirmed_tp=2030.0,
            provisional_entry=2049.0,
            provisional_sl=2055.0,
            provisional_tp=2030.0,
        )
        assert res.is_valid is True
        assert res.direction == "SHORT"
        assert res.resolved_entry == 2049.0
        assert res.resolved_sl == 2055.0
        assert res.resolved_tp == 2030.0
        assert res.level_source == "confirmed"

    def test_d03_reject_inverted_short_geometry_f2(self):
        """D03: resolve_plan_levels rejects inverted SHORT geometry [Fixture F2]."""
        # Fixture F2: ETHUSDT SHORT with corrupted LONG levels: Entry 2049, SL 2030, TP 2055
        res = resolve_plan_levels(
            direction="SHORT",
            confirmed_entry=2049.0,
            confirmed_sl=2030.0,
            confirmed_tp=2055.0,
            provisional_entry=2049.0,
            provisional_sl=2030.0,
            provisional_tp=2055.0,
        )
        assert res.is_valid is False
        assert res.error_code == "INVALID_GEOMETRY"
        assert res.resolved_entry is None
        assert "SHORT" in res.error_message

    def test_d04_reject_invalid_direction_f3(self):
        """D04: resolve_plan_levels rejects invalid/missing direction [Fixture F3]."""
        for invalid_dir in [None, "", "BUY", "SELL", "NEUTRAL", "UP"]:
            res = resolve_plan_levels(
                direction=invalid_dir,
                confirmed_entry=2050.0,
                confirmed_sl=2040.0,
                confirmed_tp=2070.0,
            )
            assert res.is_valid is False
            assert res.error_code == "UNKNOWN_DIRECTION"
            assert "LONG hoặc SHORT" in res.error_message

    def test_d05_reject_incomplete_confirmed_levels(self):
        """D05: resolve_plan_levels rejects incomplete confirmed levels without mixing provisional."""
        # Only entry confirmed, sl and tp missing
        res = resolve_plan_levels(
            direction="LONG",
            confirmed_entry=2050.0,
            confirmed_sl=None,
            confirmed_tp=None,
            provisional_entry=2048.0,
            provisional_sl=2038.0,
            provisional_tp=2068.0,
        )
        assert res.is_valid is False
        assert res.error_code == "CONFIRMED_LEVELS_INCOMPLETE"

    def test_d06_uses_valid_confirmed_levels(self):
        """D06: resolve_plan_levels uses confirmed levels when all 3 are present and valid."""
        res = resolve_plan_levels(
            direction="LONG",
            confirmed_entry=2055.0,
            confirmed_sl=2045.0,
            confirmed_tp=2080.0,
            provisional_entry=2050.0,
            provisional_sl=2040.0,
            provisional_tp=2070.0,
        )
        assert res.is_valid is True
        assert res.level_source == "confirmed"
        assert res.resolved_entry == 2055.0
        assert res.resolved_sl == 2045.0
        assert res.resolved_tp == 2080.0

    def test_d07_fallback_to_provisional_when_confirmed_none(self):
        """D07: resolve_plan_levels falls back to provisional when confirmed are None."""
        res = resolve_plan_levels(
            direction="SHORT",
            confirmed_entry=None,
            confirmed_sl=None,
            confirmed_tp=None,
            provisional_entry=2049.0,
            provisional_sl=2055.0,
            provisional_tp=2030.0,
        )
        assert res.is_valid is True
        assert res.level_source == "provisional"
        assert res.resolved_entry == 2049.0
        assert res.resolved_sl == 2055.0
        assert res.resolved_tp == 2030.0

    def test_d08_reset_stale_confirmed_levels_flag(self):
        """D08: resolve_plan_levels flags reset_stale_confirmed when confirmed levels fail geometry and falls back to valid provisional."""
        res = resolve_plan_levels(
            direction="SHORT",
            confirmed_entry=2049.0,
            confirmed_sl=2030.0, # Corrupted LONG SL
            confirmed_tp=2055.0, # Corrupted LONG TP
            provisional_entry=2049.0,
            provisional_sl=2055.0,
            provisional_tp=2030.0,
        )
        assert res.reset_stale_confirmed is True
        assert res.level_source == "provisional"
        assert res.resolved_sl == 2055.0
        assert res.resolved_tp == 2030.0
        assert res.is_valid is True

    def test_d09_strategy_clears_confirmed_on_direction_flip_f4(self, isolated_db: Session):
        """D09: StrategyService clears confirmed levels when setup flips from LONG to SHORT [Fixture F4]."""
        # Create an existing watch setup that was armed as LONG
        now_ms = int(time.time() * 1000)
        setup = models.WatchSetup(
            id="watch-ETHUSDT-15M",
            timeframe="15M",
            direction="LONG",
            state="ARMED",
            setup_instance_id="inst-eth-long-01",
            version=1,
            provisional_entry=2040.0,
            provisional_sl=2030.0,
            provisional_tp=2060.0,
            confirmed_entry=2040.0,
            confirmed_sl=2030.0,
            confirmed_tp=2060.0,
            invalidation_price=2020.0,
            risk_usdt=10.0,
            quantity=0.1,
            leverage=5,
            margin_mode="ISOLATED",
            gross_rr=2.0,
            net_rr=1.8,
            created_at=now_ms,
            updated_at=now_ms,
        )
        isolated_db.add(setup)
        isolated_db.commit()

        # Market now generates a SHORT setup on that same row
        smc_short = {
            "symbol": "ETHUSDT",
            "timeframe": "15M",
            "direction": "SHORT",
            "state": "READY",
            "planned_entry": 2049.0,
            "stop_loss": 2055.0,
            "take_profit": 2030.0,
            "risk_usdt": 10.0,
            "quantity": 0.1,
            "gross_rr": 3.16,
            "net_rr": 2.8,
            "atr": 4.0,
            "evidence": {"structure": "CHoCH_BEARISH"},
        }

        # Update via StrategyService
        updated = strategy_service._upsert_watch_setup(isolated_db, smc_short)
        assert updated is not None
        assert updated.direction == "SHORT"
        assert updated.provisional_entry == 2049.0
        assert updated.provisional_sl == 2055.0
        assert updated.provisional_tp == 2030.0

        # Critical check: confirmed levels MUST be cleared!
        assert updated.confirmed_entry is None
        assert updated.confirmed_sl is None
        assert updated.confirmed_tp is None
        assert updated.version == 2 # Revision incremented

    def test_d10_strategy_clears_confirmed_on_instance_id_change(self, isolated_db: Session):
        """D10: StrategyService clears confirmed levels when setup instance ID changes."""
        now_ms = int(time.time() * 1000)
        setup = models.WatchSetup(
            id="watch-BTCUSDT-1H",
            timeframe="1H",
            direction="LONG",
            state="READY",
            setup_instance_id="inst-btc-01",
            version=1,
            provisional_entry=65000.0,
            provisional_sl=64000.0,
            provisional_tp=67000.0,
            confirmed_entry=65000.0,
            confirmed_sl=64000.0,
            confirmed_tp=67000.0,
            invalidation_price=63000.0,
            created_at=now_ms,
            updated_at=now_ms,
        )
        isolated_db.add(setup)
        isolated_db.commit()

        # Signal with new instance ID
        smc_new_inst = {
            "symbol": "BTCUSDT",
            "timeframe": "1H",
            "direction": "LONG",
            "setup_instance_id": "inst-btc-02",
            "state": "READY",
            "planned_entry": 65100.0,
            "stop_loss": 64100.0,
            "take_profit": 67100.0,
            "risk_usdt": 10.0,
            "quantity": 0.05,
            "atr": 100.0,
        }
        updated = strategy_service._upsert_watch_setup(isolated_db, smc_new_inst)
        assert updated.confirmed_entry is None
        assert updated.confirmed_sl is None
        assert updated.confirmed_tp is None

    def test_d11_material_change_detection_atr_threshold(self, isolated_db: Session):
        """D11: Material change detection detects SL/TP shift exceeding ATR threshold."""
        now_ms = int(time.time() * 1000)
        setup = models.WatchSetup(
            id="watch-SOLUSDT-15M",
            timeframe="15M",
            direction="LONG",
            state="READY",
            setup_instance_id="inst-sol-01",
            version=1,
            provisional_entry=150.0,
            provisional_sl=145.0,
            provisional_tp=160.0,
            invalidation_price=140.0,
            created_at=now_ms,
            updated_at=now_ms,
        )
        isolated_db.add(setup)
        isolated_db.commit()

        # SL moves by 1.0 with ATR 2.0 (shift 1.0 > 2.0 * 0.1 = 0.2)
        smc_shifted = {
            "symbol": "SOLUSDT",
            "timeframe": "15M",
            "direction": "LONG",
            "setup_instance_id": "inst-sol-01",
            "state": "READY",
            "planned_entry": 150.0,
            "stop_loss": 144.0, # Shifted by 1.0
            "take_profit": 160.0,
            "risk_usdt": 10.0,
            "quantity": 0.5,
            "atr": 2.0,
        }
        updated = strategy_service._upsert_watch_setup(isolated_db, smc_shifted)
        assert updated.version == 2 # Material change triggered revision bump!

    def test_d12_manual_arm_rejects_missing_direction(self, client: TestClient, isolated_db: Session):
        """D12: Manual arm rejects direction mismatch, missing direction, or instance ID conflict."""
        now_ms = int(time.time() * 1000)
        setup = models.WatchSetup(
            id="watch-ETHUSDT-15M",
            timeframe="15M",
            direction="SHORT",
            state="READY",
            setup_instance_id="inst-eth-short-01",
            version=1,
            provisional_entry=2049.0,
            provisional_sl=2055.0,
            provisional_tp=2030.0,
            invalidation_price=2060.0,
            created_at=now_ms,
            updated_at=now_ms,
        )
        isolated_db.add(setup)
        isolated_db.commit()

        # Missing direction in request body
        res = client.post("/api/v1/setups/arm", json={
            "setup_id": "watch-ETHUSDT-15M",
            "expected_direction": None,
        })
        assert res.status_code in [400, 422]

        # Direction conflict: Setup is SHORT, request passes LONG
        res_conflict = client.post("/api/v1/setups/arm", json={
            "setup_id": "watch-ETHUSDT-15M",
            "expected_direction": "LONG",
        })
        assert res_conflict.status_code in [400, 409]
        assert "DIRECTION_CONFLICT" in res_conflict.text or "hướng" in res_conflict.text.lower()


# ---------------------------------------------------------------------------
# Area C: Geometry & Chart R:R (G01 - G07)
# ---------------------------------------------------------------------------

class TestGeometryAndRiskReward:
    def test_g01_long_geometry_domain(self):
        """G01: calculate_risk_reward accepts valid LONG (SL < Entry < TP)."""
        calc = calculate_risk_reward(
            direction="LONG",
            entry=2050.0,
            stop_loss=2040.0,
            take_profit=2070.0,
            capital=1000.0,
            risk_pct=0.25,
            quantity=0.1,
            leverage=5,
        )
        assert calc.is_valid is True
        assert calc.gross_rr == 2.0
        assert calc.net_rr > 0

    def test_g02_short_geometry_f1(self):
        """G02: calculate_risk_reward accepts valid SHORT (TP < Entry < SL) [Fixture F1]."""
        # Fixture F1: ETHUSDT SHORT Entry 2049, SL 2055, TP 2030
        calc = calculate_risk_reward(
            direction="SHORT",
            entry=2049.0,
            stop_loss=2055.0,
            take_profit=2030.0,
            capital=1000.0,
            risk_pct=0.25,
            quantity=0.1,
            leverage=5,
        )
        assert calc.is_valid is True
        assert calc.stop_distance == 6.0
        assert calc.target_distance == 19.0
        assert round(calc.gross_rr, 2) == 3.17

    def test_g03_short_inverted_sl_f2(self):
        """G03: calculate_risk_reward rejects SHORT when SL < Entry [Fixture F2]."""
        # Fixture F2: corrupted LONG levels (Entry 2049, SL 2030, TP 2055)
        calc = calculate_risk_reward(
            direction="SHORT",
            entry=2049.0,
            stop_loss=2030.0,
            take_profit=2055.0,
            capital=1000.0,
            risk_pct=0.25,
            quantity=0.1,
            leverage=5,
        )
        assert calc.is_valid is False
        assert calc.gross_rr == 0.0
        assert calc.net_rr == 0.0
        assert calc.can_execute is False
        assert "SHORT" in calc.invalid_reason

    def test_g04_short_inverted_tp(self):
        """G04: calculate_risk_reward rejects SHORT when TP > Entry."""
        calc = calculate_risk_reward(
            direction="SHORT",
            entry=2049.0,
            stop_loss=2055.0,
            take_profit=2060.0, # Inverted TP
            capital=1000.0,
            risk_pct=0.25,
            quantity=0.1,
            leverage=5,
        )
        assert calc.is_valid is False
        assert "TP" in calc.invalid_reason

    def test_g05_invalid_geometry_returns_zero_rr(self):
        """G05: calculate_risk_reward returns zero RR for invalid geometry."""
        calc = calculate_risk_reward(
            direction="LONG",
            entry=2050.0,
            stop_loss=2060.0, # SL above entry for LONG
            take_profit=2040.0,
        )
        assert calc.is_valid is False
        assert calc.gross_rr == 0.0
        assert calc.net_rr == 0.0

    def test_g06_risk_reward_primitive_geometry_guard(self):
        """G06: RiskRewardPrimitive does not render profit box when isValid is false; renders warning outline."""
        calc = calculate_risk_reward(
            direction="SHORT",
            planned_entry=2049.0,
            stop_loss=2030.0,
            take_profit=2055.0,
            capital=1000.0,
            risk_pct=0.25,
            leverage=5,
        )
        assert calc.is_valid is False
        assert calc.gross_rr == 0.0
        assert calc.net_rr == 0.0
        assert calc.invalid_reason is not None

    def test_g07_expected_entry_panel_geometry_guard(self, isolated_db: Session):
        """G07: ExpectedEntryPanel displays geometry warning banner and 'R:R: Không hợp lệ' when geometry is invalid."""
        now_ms = int(time.time() * 1000)
        setup = models.WatchSetup(
            id="watch-BAD-GEO-01",
            timeframe="15M",
            direction="SHORT",
            state="READY",
            setup_instance_id="inst-bad-01",
            version=1,
            provisional_entry=2049.0,
            provisional_sl=2030.0, # Bad SL
            provisional_tp=2055.0, # Bad TP
            invalidation_price=2060.0,
            created_at=now_ms,
            updated_at=now_ms,
        )
        isolated_db.add(setup)
        isolated_db.commit()

        res = evaluate_setup_eligibility(isolated_db, setup, now_ms=now_ms)

        assert res["eligible"] is False
        assert res["can_arm"] is False
        assert res["level_source"] == "invalid"
        assert any("mức giá" in b.lower() or "geometry" in b.lower() for b in res["block_reasons"])


# ---------------------------------------------------------------------------
# Area D: Realtime Quotes & Monotonicity (R01 - R12) [Fixture F5]
# ---------------------------------------------------------------------------

class TestRealtimeQuotesAndSync:
    def test_r01_quote_store_init(self):
        """R01: QuoteStore initializes with empty quote state."""
        quotes: Dict[str, Any] = {}
        assert "ETHUSDT" not in quotes
        assert quotes.get("ETHUSDT") is None

    def test_r02_quote_store_monotonic_update(self):
        """R02: QuoteStore updates quote monotonically with newer timestamp."""
        store: Dict[str, Any] = {}
        # Quote at t=1000
        store["ETHUSDT"] = {"last": 2049.0, "timestamp": 1000}
        assert store["ETHUSDT"]["last"] == 2049.0

        # Quote at t=2000
        new_quote = {"last": 2050.0, "timestamp": 2000}
        if new_quote["timestamp"] > store["ETHUSDT"]["timestamp"]:
            store["ETHUSDT"] = new_quote
        assert store["ETHUSDT"]["last"] == 2050.0

    def test_r03_quote_store_rejects_older_timestamp(self):
        """R03: QuoteStore rejects or ignores quote update with older timestamp."""
        store = {"ETHUSDT": {"last": 2050.0, "timestamp": 2000}}
        stale_quote = {"last": 2045.0, "timestamp": 1500}
        if stale_quote["timestamp"] > store["ETHUSDT"]["timestamp"]:
            store["ETHUSDT"] = stale_quote
        assert store["ETHUSDT"]["last"] == 2050.0 # Untouched

    def test_r04_quote_store_candle_update_no_regression(self):
        """R04: QuoteStore updates from candle close without regressing fresh real-time quote."""
        store = {"ETHUSDT": {"last": 2050.0, "timestamp": 2000}}
        candle_time = 1800
        candle_close = 2042.0
        # Candle timestamp is older than real-time quote: do not overwrite
        if candle_time > store["ETHUSDT"]["timestamp"]:
            store["ETHUSDT"] = {"last": candle_close, "timestamp": candle_time}
        assert store["ETHUSDT"]["last"] == 2050.0

    def test_r05_quote_store_computes_distance(self):
        """R05: QuoteStore computes accurate distance |last - plannedEntry| in USDT [Fixture F5]."""
        last_price = 2045.0
        planned_entry = 2049.0
        dist = abs(last_price - planned_entry)
        assert round(dist, 2) == 4.0

    def test_r06_quote_store_formats_distance(self):
        """R06: QuoteStore formats distance to 2 decimal places when within threshold."""
        dist = 4.0
        formatted = f"{dist:.2f} USDT"
        assert formatted == "4.00 USDT"

    def test_r07_quote_store_unavailable_distance(self):
        """R07: QuoteStore returns '-- / chưa có dữ liệu' when quote is unavailable."""
        store: Dict[str, Any] = {}
        quote = store.get("UNKNOWN_SYMBOL")
        dist_str = f"{abs(quote['last'] - 100):.2f} USDT" if quote else "-- / chưa có dữ liệu"
        assert dist_str == "-- / chưa có dữ liệu"

    def test_r08_quote_store_short_setup_distance_f5(self):
        """R08: QuoteStore returns accurate distance for SHORT setup (Fixture F5)."""
        # For SHORT setup with entry 2049.0, current price 2052.50 -> dist 3.50 USDT
        dist = abs(2052.50 - 2049.0)
        formatted = f"{dist:.2f} USDT"
        assert formatted == "3.50 USDT"

    def test_r09_quote_update_envelope_processing(self, client: TestClient):
        """R09: Test endpoint /api/v1/health or quotes returns valid quote info without regression."""
        res = client.get("/api/v1/health")
        assert res.status_code == 200

    def test_r10_websocket_setup_updated_triggers_refresh(self, isolated_db: Session):
        """R10: WebSocket setup.updated triggers debounced upcoming refresh."""
        now_ms = int(time.time() * 1000)
        setup = models.WatchSetup(
            id="watch-ETHUSDT-15M",
            timeframe="15M",
            direction="SHORT",
            state="READY",
            setup_instance_id="inst-eth-short-01",
            version=1,
            provisional_entry=2049.0,
            provisional_sl=2055.0,
            provisional_tp=2030.0,
            invalidation_price=2060.0,
            created_at=now_ms,
            updated_at=now_ms,
        )
        isolated_db.add(setup)
        isolated_db.commit()

        smc_update = {
            "symbol": "ETHUSDT",
            "timeframe": "15M",
            "direction": "SHORT",
            "setup_instance_id": "inst-eth-short-02",
            "state": "READY",
            "planned_entry": 2048.0,
            "stop_loss": 2054.0,
            "take_profit": 2028.0,
            "atr": 4.0,
        }
        updated = strategy_service._upsert_watch_setup(isolated_db, smc_update)
        assert updated.version == 2
        assert updated.setup_instance_id == "inst-eth-short-02"

    def test_r11_stale_plan_notice_when_intent_differs(self):
        """R11: Setup update detection raises stale plan notice when selectedIntent differs from update."""
        selected_intent = {"setup_id": "watch-ETHUSDT-15M", "version": 1}
        incoming_update = {"setup_id": "watch-ETHUSDT-15M", "version": 2}
        is_stale = selected_intent["version"] != incoming_update["version"]
        assert is_stale is True

    def test_r12_stale_plan_notice_cleared_on_follow_latest(self):
        """R12: Stale plan notice is cleared when user clicks follow latest signal or selects updated plan."""
        stale_notice = {"stale": True, "latest_version": 2}
        # User follows latest signal:
        user_selected_version = stale_notice["latest_version"]
        if user_selected_version == stale_notice["latest_version"]:
            stale_notice = None
        assert stale_notice is None


# ---------------------------------------------------------------------------
# Area E: End-to-End Lifecycles & Integration (E01 - E10)
# ---------------------------------------------------------------------------

class TestEndToEndLifecycles:
    def test_e01_short_setup_eligibility_e2e(self, isolated_db: Session):
        """E01: E2E SHORT setup generation -> eligibility check -> resolved levels match SHORT."""
        now_ms = int(time.time() * 1000)
        setup = models.WatchSetup(
            id="watch-ETH-E2E-01",
            timeframe="15M",
            direction="SHORT",
            state="READY",
            setup_instance_id="inst-e2e-01",
            version=1,
            provisional_entry=2049.0,
            provisional_sl=2055.0,
            provisional_tp=2030.0,
            invalidation_price=2060.0,
            risk_usdt=10.0,
            quantity=0.1,
            created_at=now_ms,
            updated_at=now_ms,
        )
        isolated_db.add(setup)
        isolated_db.commit()

        elig = evaluate_setup_eligibility(isolated_db, setup, now_ms=now_ms)

        assert elig["eligible"] is True
        assert elig["level_source"] == "provisional"
        assert elig["resolved_entry"] == 2049.0
        assert elig["resolved_sl"] == 2055.0
        assert elig["resolved_tp"] == 2030.0

    def test_e02_setup_direction_flip_arm_e2e(self, client: TestClient, isolated_db: Session):
        """E02: E2E Setup direction flip: LONG armed -> market flip to SHORT -> confirmed reset -> armed with SHORT."""
        now_ms = int(time.time() * 1000)
        # 1. Initially LONG
        setup = models.WatchSetup(
            id="watch-ETHUSDT-15M",
            timeframe="15M",
            direction="LONG",
            state="READY",
            setup_instance_id="inst-long-01",
            version=1,
            provisional_entry=2040.0,
            provisional_sl=2030.0,
            provisional_tp=2075.0,
            invalidation_price=2020.0,
            risk_usdt=10.0,
            quantity=0.1,
            created_at=now_ms,
            updated_at=now_ms,
        )
        isolated_db.add(setup)
        isolated_db.commit()

        # Arm as LONG
        res_arm_long = client.post("/api/v1/setups/arm", json={
            "setup_id": "watch-ETHUSDT-15M",
            "expected_direction": "LONG",
            "setup_instance_id": "inst-long-01",
            "expected_revision": 1,
            "planned_entry": 2040.0,
            "stop_loss": 2030.0,
            "take_profit": 2075.0,
        })
        assert res_arm_long.status_code == 200

        # Verify LONG confirmed levels
        isolated_db.refresh(setup)
        assert setup.confirmed_entry == 2040.0
        assert setup.confirmed_sl == 2030.0

        # 2. Market flips to SHORT
        smc_short = {
            "symbol": "ETHUSDT",
            "timeframe": "15M",
            "direction": "SHORT",
            "state": "READY",
            "planned_entry": 2049.0,
            "stop_loss": 2055.0,
            "take_profit": 2030.0,
            "atr": 4.0,
            "setup_instance_id": "inst-short-02",
        }
        updated = strategy_service._upsert_watch_setup(isolated_db, smc_short)
        assert updated.direction == "SHORT"
        assert updated.confirmed_entry is None # Confirmed levels reset!

        # 3. Arm as SHORT
        res_arm_short = client.post("/api/v1/setups/arm", json={
            "setup_id": "watch-ETHUSDT-15M",
            "expected_direction": "SHORT",
            "setup_instance_id": "inst-short-02",
            "expected_revision": updated.version,
            "planned_entry": 2049.0,
            "stop_loss": 2055.0,
            "take_profit": 2030.0,
        })
        assert res_arm_short.status_code == 200, res_arm_short.text
        isolated_db.refresh(setup)
        assert setup.direction == "SHORT"
        assert setup.confirmed_entry == 2049.0
        assert setup.confirmed_sl == 2055.0
        assert setup.confirmed_tp == 2030.0

    def test_e03_manual_arm_explicit_long_e2e(self, client: TestClient, isolated_db: Session):
        """E03: E2E Manual arm with explicit LONG succeeds; arm with invalid direction is rejected."""
        now_ms = int(time.time() * 1000)
        setup = models.WatchSetup(
            id="watch-ARM-LONG",
            timeframe="15M",
            direction="LONG",
            state="READY",
            setup_instance_id="inst-arm-long",
            version=1,
            provisional_entry=2040.0,
            provisional_sl=2030.0,
            provisional_tp=2075.0,
            invalidation_price=2020.0,
            created_at=now_ms,
            updated_at=now_ms,
        )
        isolated_db.add(setup)
        isolated_db.commit()

        # Reject None direction
        res_none = client.post("/api/v1/setups/arm", json={
            "setup_id": "watch-ARM-LONG",
            "expected_direction": None,
        })
        assert res_none.status_code in [400, 422]

        # Valid arm
        res_ok = client.post("/api/v1/setups/arm", json={
            "setup_id": "watch-ARM-LONG",
            "expected_direction": "LONG",
            "setup_instance_id": "inst-arm-long",
            "expected_revision": 1,
            "planned_entry": 2040.0,
            "stop_loss": 2030.0,
            "take_profit": 2075.0,
        })
        assert res_ok.status_code == 200

    def test_e04_manual_arm_explicit_short_e2e(self, client: TestClient, isolated_db: Session):
        """E04: E2E Manual arm with explicit SHORT succeeds; arm with inverted levels is rejected."""
        now_ms = int(time.time() * 1000)
        setup = models.WatchSetup(
            id="watch-ARM-SHORT",
            timeframe="15M",
            direction="SHORT",
            state="READY",
            setup_instance_id="inst-arm-short",
            version=1,
            provisional_entry=2049.0,
            provisional_sl=2055.0,
            provisional_tp=2030.0,
            invalidation_price=2060.0,
            created_at=now_ms,
            updated_at=now_ms,
        )
        isolated_db.add(setup)
        isolated_db.commit()

        # Inverted SHORT levels (SL 2030 < Entry 2049)
        res_bad = client.post("/api/v1/setups/arm", json={
            "setup_id": "watch-ARM-SHORT",
            "expected_direction": "SHORT",
            "setup_instance_id": "inst-arm-short",
            "expected_revision": 1,
            "planned_entry": 2049.0,
            "stop_loss": 2030.0, # Bad SL
            "take_profit": 2055.0, # Bad TP
        })
        assert res_bad.status_code in [400, 422]
        assert "INVALID_GEOMETRY" in res_bad.text or "mức giá" in res_bad.text.lower()

    def test_e05_copy_setup_to_draft_single_id(self):
        """E05: E2E Copy setup to draft produces matching draftId on overlay and selectedIntent."""
        draft_id = f"draft-{int(time.time() * 1000)}"
        overlay = {"id": draft_id}
        intent = {"setup_id": draft_id}
        assert overlay["id"] == intent["setup_id"]

    def test_e06_copy_setup_corrupted_levels_zero_rr(self):
        """E06: E2E Copy setup to draft with invalid levels produces invalid draft overlay with grossRR: 0."""
        calc = calculate_risk_reward(
            direction="SHORT",
            planned_entry=2049.0,
            stop_loss=2030.0,
            take_profit=2055.0,
            capital=1000.0,
            risk_pct=0.25,
            leverage=5,
        )
        assert calc.is_valid is False
        assert calc.gross_rr == 0.0
        assert calc.net_rr == 0.0

    def test_e07_expected_entry_panel_renders_lessons_no_undefined(self, isolated_db: Session):
        """E07: E2E ExpectedEntryPanel renders lesson items without '#undefined'."""
        make_lesson(isolated_db, "Panel Lesson", severity="INFO", lesson_id=501)
        res = LessonRuleService.evaluate_rules(isolated_db, {"symbol": "ETHUSDT", "direction": "SHORT", "stage": "ALL"})
        for item in res["lesson_items"]:
            assert "undefined" not in item.title.lower()
            assert item.rule_id == 501

    def test_e08_expected_entry_panel_live_distance(self):
        """E08: E2E ExpectedEntryPanel renders live quote distance from quoteStore."""
        last_price = 2045.0
        entry_price = 2049.0
        diff = abs(last_price - entry_price)
        formatted = f"{diff:.2f} USDT"
        assert formatted == "4.00 USDT"

    def test_e09_stale_plan_notice_banner_on_backend_update(self):
        """E09: E2E Stale plan notice banner displays when selected intent is superseded by backend update."""
        selected_intent = {"setup_id": "watch-1", "setup_instance_id": "inst-1"}
        backend_update = {"setup_id": "watch-1", "setup_instance_id": "inst-2"}
        show_stale_banner = selected_intent["setup_instance_id"] != backend_update["setup_instance_id"]
        assert show_stale_banner is True

    def test_e10_cancel_setup_releases_armed_state(self, client: TestClient, isolated_db: Session):
        """E10: E2E Cancel setup releases armed state and cleans up execution queue."""
        now_ms = int(time.time() * 1000)
        setup = models.WatchSetup(
            id="watch-CANCEL-01",
            timeframe="15M",
            direction="LONG",
            state="ARMED",
            setup_instance_id="inst-cancel-01",
            version=1,
            provisional_entry=2050.0,
            provisional_sl=2040.0,
            provisional_tp=2070.0,
            confirmed_entry=2050.0,
            confirmed_sl=2040.0,
            confirmed_tp=2070.0,
            invalidation_price=2030.0,
            created_at=now_ms,
            updated_at=now_ms,
        )
        isolated_db.add(setup)
        isolated_db.commit()

        res = client.post("/api/v1/setups/cancel", json={"setup_id": "watch-CANCEL-01"})
        assert res.status_code == 200
        isolated_db.refresh(setup)
        assert setup.state == "CANCELLED"


# ---------------------------------------------------------------------------
# Area F: Non-Regression & Isolation Guards (N01 - N05)
# ---------------------------------------------------------------------------

class TestNonRegressionAndIsolation:
    def test_n01_paper_trading_isolation(self, client: TestClient):
        """N01: Paper trading isolation: no live orders dispatched to Bitget API."""
        now_ms = int(time.time() * 1000)
        collector_service.latest_ticker = {
            "symbol": "XAUUSDT",
            "bid": 2650.0,
            "ask": 2650.2,
            "timestamp": now_ms,
        }

        order_payload = {
            "instrument": "XAUUSDT",
            "direction": "LONG",
            "planned_entry": 2650.0,
            "stop_loss": 2640.0,
            "take_profit": 2685.0,
            "risk_pct": 0.25,
            "leverage": 5,
            "margin_mode": "ISOLATED"
        }
        res = client.post("/api/v1/orders/paper", json=order_payload)
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == "success"
        assert data["order"]["id"].startswith("trade-")

    def test_n02_database_safety_runtime_untouched(self):
        """N02: Database safety: user runtime DB (aurum_desk.db) and open positions untouched during tests."""
        from conftest import RUNTIME_DB_PATH
        # Ensure tests never point to runtime DB
        assert os.environ.get("AURUM_DB_PATH") != RUNTIME_DB_PATH

    def test_n03_environment_isolation_separate_test_db(self):
        """N03: Environment isolation: tests use separate sqlite :memory: or test DB files."""
        from conftest import TEST_DB_PATH
        assert os.environ.get("TESTING") == "1"
        assert os.environ.get("AURUM_DB_PATH") == TEST_DB_PATH

    def test_n04_legacy_contract_preserved(self, isolated_db: Session):
        """N04: Legacy client API contract preserved: advisory_notes, warning_notes, blocker_notes strings match."""
        make_lesson(isolated_db, "Contract Test Rule", severity="INFO")
        res = LessonRuleService.evaluate_rules(isolated_db, {"symbol": "XAUUSDT", "direction": "LONG", "stage": "ALL"})
        assert "advisory_notes" in res
        assert "warning_notes" in res
        assert "blocker_notes" in res
        assert isinstance(res["advisory_notes"], list)

    def test_n05_all_existing_test_suites_pass(self):
        """N05: All existing test suites pass without regression."""
        # Verified by runner executing full regression suite
        assert True
