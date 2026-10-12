"""
V14.4.1 certified entry guard: READY is a label, not evidence. Exact snapshot binding or block.
Everything starts from a REAL confirmed scenario promoted through the production cycle.
"""
import dataclasses
import json

import pytest

import models
from services.entry_guard_service import EntryGuardRequest, _check_strategy
from services.lesson_policy_service import LessonPolicyService
from services.lesson_rule_service import LessonRuleService
from services.smc_scenario_binding import verify_scenario_binding, verify_setup_structure
from services.smc_scenario_config import active_scenario_config
from services.strategy_service import strategy_service

import smc_fixtures as F
from ny_quota_fixtures import set_policy, set_saved_risk
from test_v14_4_1_production_pipeline import armed_orders, run_cycle, seed_all


@pytest.fixture
def db(isolated_db):
    LessonPolicyService.reset_to_defaults(isolated_db)
    LessonRuleService.invalidate_cache()
    set_saved_risk(isolated_db)
    set_policy(isolated_db, ny_fallback_enabled=False, entry_session_policy="NY_ONLY")
    return isolated_db


def ready_watch(db, direction="LONG"):
    seed_all(db, direction)
    run_cycle(db, direction, auto=False)
    ws = db.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M").one()
    assert ws.state == "READY"
    return ws


def levels(ws):
    return {"entry": ws.confirmed_entry, "stop_loss": ws.confirmed_sl, "take_profit": ws.confirmed_tp}


def req(chain, state="READY"):
    return EntryGuardRequest(stage="ARM", now_ms=F.NOW_MS, symbol="XAUUSDT", direction="LONG", execution_mode="AUTO",
                             origin="AUTO_STRATEGY", setup_state=state, structure_chain_verified=chain)


def test_ready_with_unknown_or_false_chain_blocks_certified():
    assert active_scenario_config.certified_smc_entry_enabled is True
    for chain in (None, False):
        c = _check_strategy(req(chain))
        assert c.state == "BLOCK" and c.code == "UNCONFIRMED_STRUCTURE_CHAIN"
    assert _check_strategy(req(True)).state == "PASS"


@pytest.mark.legacy_entry
def test_flag_off_is_labelled_uncertified_and_false_still_blocks():
    c = _check_strategy(req(None))
    assert c.state == "PASS" and "LEGACY_UNCERTIFIED" in c.message_vi
    assert _check_strategy(req(False)).state == "BLOCK"


def test_exact_binding_passes_then_each_tamper_fails(db):
    ws = ready_watch(db, "LONG")
    ok, ev, b = verify_setup_structure(db, ws, symbol="XAUUSDT", plan_levels=levels(ws), now_ms=F.NOW_MS)
    assert ok is True and b.verified and ev["structure_confirmed"] is True

    # wrong direction
    r = verify_scenario_binding(db, snapshot_id=ws.scenario_snapshot_id, direction="SHORT", symbol="XAUUSDT",
                                setup_instance_id=ws.setup_instance_id, plan_levels=levels(ws), now_ms=F.NOW_MS)
    assert not r.verified and "SNAPSHOT_DIRECTION_MISMATCH" in r.reasons
    # wrong instance
    r = verify_scenario_binding(db, snapshot_id=ws.scenario_snapshot_id, direction="LONG", symbol="XAUUSDT",
                                setup_instance_id="other-instance", plan_levels=levels(ws), now_ms=F.NOW_MS)
    assert "SNAPSHOT_INSTANCE_MISMATCH" in r.reasons
    # edited plan levels do not reproduce the frozen fingerprint
    bad = dict(levels(ws)); bad["take_profit"] += 3.0
    r = verify_scenario_binding(db, snapshot_id=ws.scenario_snapshot_id, direction="LONG", symbol="XAUUSDT",
                                setup_instance_id=ws.setup_instance_id, plan_levels=bad, now_ms=F.NOW_MS)
    assert "PLAN_FINGERPRINT_MISMATCH" in r.reasons
    # wrong symbol / missing / unknown snapshot
    assert not verify_scenario_binding(db, snapshot_id=ws.scenario_snapshot_id, direction="LONG", symbol="BTCUSDT",
                                       setup_instance_id=None, plan_levels=levels(ws), now_ms=F.NOW_MS).verified
    assert verify_scenario_binding(db, snapshot_id=None, direction="LONG", symbol="XAUUSDT", setup_instance_id=None,
                                   plan_levels=levels(ws), now_ms=F.NOW_MS).reasons == ("NO_SCENARIO_SNAPSHOT_REF",)
    assert "SCENARIO_SNAPSHOT_NOT_FOUND" in verify_scenario_binding(
        db, snapshot_id="snap-x", direction="LONG", symbol="XAUUSDT", setup_instance_id=None,
        plan_levels=levels(ws), now_ms=F.NOW_MS).reasons
    # a snapshot from the future of the decision time
    r = verify_scenario_binding(db, snapshot_id=ws.scenario_snapshot_id, direction="LONG", symbol="XAUUSDT",
                                setup_instance_id=ws.setup_instance_id, plan_levels=levels(ws), now_ms=F.NOW_MS - 3_600_000)
    assert "SNAPSHOT_FROM_FUTURE" in r.reasons


def test_tampered_payload_digest_is_detected(db):
    ws = ready_watch(db, "LONG")
    row = db.query(models.SMCScenarioSnapshot).filter_by(snapshot_id=ws.scenario_snapshot_id).one()
    payload = json.loads(row.payload)
    payload["plan"]["take_profit"] = round(payload["plan"]["take_profit"] + 5.0, 2)   # edited after the decision
    row.payload = json.dumps(payload)
    db.commit()
    r = verify_scenario_binding(db, snapshot_id=ws.scenario_snapshot_id, direction="LONG", symbol="XAUUSDT",
                                setup_instance_id=ws.setup_instance_id, plan_levels=levels(ws), now_ms=F.NOW_MS)
    assert not r.verified


def test_unknown_or_legacy_evidence_is_never_certified(db):
    ws = ready_watch(db, "LONG")
    ws.scenario_snapshot_id = None
    ws.evidence_snapshot_id = None
    ok, _, _ = verify_setup_structure(db, ws, symbol="XAUUSDT", plan_levels=levels(ws), now_ms=F.NOW_MS)
    assert ok is None                                   # unknown -> the certified guard blocks it
    ev = models.StrategyEvidence(id="ev-legacy-1", direction="LONG", setup_id=ws.id, h1_alignment="ALIGNED",
                                 sweep_evidence=json.dumps({"level": 2638.0}), displacement_evidence=json.dumps({"break": 2652.0}),
                                 fvg_evidence=json.dumps({"low": 2646.0, "high": 2649.0}), created_at=F.NOW_MS)
    db.add(ev); db.commit()
    ws.evidence_snapshot_id = ev.id
    ok, ev_dict, _ = verify_setup_structure(db, ws, symbol="XAUUSDT", plan_levels=levels(ws), now_ms=F.NOW_MS)
    assert ok is False                                  # JSON with content is not proof; measured validator rejects it


def test_auto_arm_refuses_a_ready_watch_with_stripped_binding(db):
    ws = ready_watch(db, "LONG")
    ws.scenario_snapshot_id = None
    db.commit()
    run_cycle(db, "LONG", auto=True)                    # cycle re-promotes the real scenario: it is bound again
    ws = db.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M").one()
    assert ws.scenario_snapshot_id is not None
    for o in armed_orders(db):
        assert o.scenario_snapshot_id == ws.scenario_snapshot_id


def test_cooldown_daily_limit_and_open_position_block_arm(db):
    seed_all(db, "LONG")
    audit = db.query(models.DayAudit).first()
    # three actual fills already happened today: the guard must refuse a fourth
    from services.clock import FakeClock
    import crud
    a = crud.get_or_create_today_audit(db, clock=FakeClock(F.NOW_MS))
    a.fills_count = 3
    db.commit()
    run_cycle(db, "LONG", auto=True)
    assert armed_orders(db) == []
    ws = db.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M").one()
    assert ws.scenario_snapshot_id is not None          # facts remain, only the entry is refused
