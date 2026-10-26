"""
V14.4.1 repository: idempotent immutable snapshots, revision binding, persisted state is read back (terminal
scenarios stay terminal), migrations run twice without touching open positions.
"""
import dataclasses
import json

import pytest

import models
from database import run_schema_migrations
from services.smc_scenario_config import active_scenario_config as CFG
from services.smc_scenario_repository import (
    get_previous_scenarios, save_immutable_scenario_snapshot, snapshot_from_row, upsert_scenario_revision,
)
from services.smc_scenario_service import evaluate_scenario_cycle, evaluate_scenarios, load_closed_context
from services.smc_scenario_types import ScenarioState

import smc_fixtures as F


def confirmed(direction="LONG", previous=()):
    ctx = load_closed_context(F.make_candles_by_tf(direction), F.NOW_MS, CFG)
    snaps = evaluate_scenarios("XAUUSDT", ctx, list(previous), CFG, F.NOW_MS, settings=F.default_settings())
    return next(s for s in snaps if s.direction == direction)


def test_idempotent_snapshot_and_immutability_conflict(isolated_db):
    snap = confirmed()
    r1 = save_immutable_scenario_snapshot(isolated_db, snap)
    isolated_db.commit()
    r2 = save_immutable_scenario_snapshot(isolated_db, snap)
    assert r1.snapshot_id == r2.snapshot_id and isolated_db.query(models.SMCScenarioSnapshot).count() == 1
    forged = dataclasses.replace(snap, summary_vi="same id, different content")
    forged.digest = "0" * 16
    with pytest.raises(ValueError, match="SNAPSHOT_IMMUTABILITY_CONFLICT"):
        save_immutable_scenario_snapshot(isolated_db, forged)


def test_row_round_trip_is_the_exact_snapshot(isolated_db):
    snap = confirmed("SHORT")
    row = save_immutable_scenario_snapshot(isolated_db, snap)
    back = snapshot_from_row(row)
    assert back.to_dict() == snap.to_dict() and back.plan_fingerprint == row.plan_fingerprint


def test_revision_binding_material_change_creates_new_revision(isolated_db):
    snap = confirmed()
    upsert_scenario_revision(isolated_db, snap)
    isolated_db.commit()
    prev = get_previous_scenarios(isolated_db, "XAUUSDT")
    assert [p.snapshot_id for p in prev] == [snap.snapshot_id]
    again = confirmed(previous=prev)
    assert again.revision == snap.revision and again.digest == snap.digest   # no material change -> same revision
    moved = dataclasses.replace(snap, revision=snap.revision + 1, summary_vi="changed")
    moved.digest = moved.compute_digest()
    upsert_scenario_revision(isolated_db, moved)
    isolated_db.commit()
    assert isolated_db.query(models.SMCScenarioSnapshot).filter_by(instance_id=snap.instance_id).count() == 2
    assert isolated_db.query(models.SMCScenario).filter_by(scenario_id=snap.scenario_id).one().current_snapshot_id == moved.snapshot_id


def test_cycle_persists_once_and_a_repeat_is_not_a_new_revision(isolated_db):
    cbt = F.make_candles_by_tf("LONG")
    r1 = evaluate_scenario_cycle(isolated_db, "XAUUSDT", cbt, None, F.NOW_MS, settings=F.default_settings())
    isolated_db.commit()
    n = isolated_db.query(models.SMCScenarioSnapshot).count()
    assert n >= 1 and r1.changed
    r2 = evaluate_scenario_cycle(isolated_db, "XAUUSDT", cbt, None, F.NOW_MS, settings=F.default_settings())
    isolated_db.commit()
    assert r2.changed == [] and isolated_db.query(models.SMCScenarioSnapshot).count() == n


def test_terminal_state_is_read_back_and_never_resurrected(isolated_db):
    snap = confirmed()
    dead = dataclasses.replace(snap, state=ScenarioState.INVALIDATED, revision=snap.revision + 1)
    dead.digest = dead.compute_digest()
    upsert_scenario_revision(isolated_db, dead)
    isolated_db.commit()
    prev = get_previous_scenarios(isolated_db, "XAUUSDT")
    again = confirmed(previous=prev)
    assert again.state == ScenarioState.INVALIDATED and again.revision == dead.revision


def test_migration_twice_preserves_open_and_closed_orders(isolated_db):
    o = models.PaperOrder(id="keep-1", direction="LONG", state="paper_open", planned_entry=2650.0, stop_loss=2640.0,
                          take_profit=2680.0, quantity=0.1, actual_entry=2650.1, initial_risk_usdt=1.0, created_at=1,
                          armed_at=2, opened_at=3)
    c = models.PaperOrder(id="keep-2", direction="SHORT", state="closed", planned_entry=2660.0, stop_loss=2670.0,
                          take_profit=2630.0, quantity=0.1, actual_entry=2660.0, initial_risk_usdt=1.0, created_at=1,
                          armed_at=2, opened_at=3, closed_at=9, realized_pnl_net=1.25)
    isolated_db.add_all([o, c]); isolated_db.commit()
    engine = isolated_db.get_bind()
    run_schema_migrations(engine)
    run_schema_migrations(engine)
    isolated_db.expire_all()
    a, b = isolated_db.query(models.PaperOrder).filter_by(id="keep-1").one(), isolated_db.query(models.PaperOrder).filter_by(id="keep-2").one()
    assert (a.state, a.planned_entry, a.stop_loss, a.take_profit, a.actual_entry, a.opened_at) == ("paper_open", 2650.0, 2640.0, 2680.0, 2650.1, 3)
    assert (b.state, b.realized_pnl_net, b.closed_at) == ("closed", 1.25, 9)
    assert a.scenario_snapshot_id is None and a.quality_snapshot_id is None   # legacy rows are never backfilled
