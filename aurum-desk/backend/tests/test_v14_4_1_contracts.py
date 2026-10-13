"""
V14.4.1 API / DTO / round-trip contracts over the REAL scenario engine output (no hand-built nodes).

arm -> GET order (serialized refs) -> chart evidence fetch by the exact frozen ids; 409 on identity mismatch;
read-only cached forecasts endpoint; object -> JSON -> object -> chart DTO round trip.
"""
import json

import pytest

import models
from services.lesson_policy_service import LessonPolicyService
from services.lesson_rule_service import LessonRuleService
from services.smc_scenario_config import active_scenario_config
from services.smc_scenario_repository import snapshot_from_row
from services.smc_scenario_service import evaluate_scenarios, load_closed_context
from services.smc_scenario_types import ScenarioSnapshot, ScenarioState
from services.chart_evidence_service import build_selected_chart_bundle
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
    strategy_service.session_forecasts_cache.clear()
    return isolated_db


def confirmed(direction):
    ctx = load_closed_context(F.make_candles_by_tf(direction), F.NOW_MS, active_scenario_config)
    snaps = evaluate_scenarios("XAUUSDT", ctx, [], active_scenario_config, F.NOW_MS, settings=F.default_settings())
    out = [s for s in snaps if s.direction == direction]
    assert len(out) == 1
    return out[0]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_service_contract_roundtrip(direction):
    """Real D/4H/1H/15M/5M context -> snapshot -> JSON -> snapshot -> chart DTO (digest stable, shapes aligned)."""
    snap = confirmed(direction)
    assert snap.state == ScenarioState.STRUCTURE_CONFIRMED
    payload = json.loads(json.dumps(snap.to_dict()))
    back = ScenarioSnapshot.from_dict(payload)
    assert back.digest == snap.digest == back.compute_digest()
    assert back.to_dict() == snap.to_dict()
    assert isinstance(snap.plan, dict) and snap.plan["plan_fingerprint"] == snap.plan_fingerprint
    bundle = build_selected_chart_bundle(back, source_type="SCENARIO", binding_id=back.scenario_id)
    kinds = {e["kind"] for e in snap.evidence}
    assert {"FVG", "RETEST", "MSS"} <= kinds
    assert bundle["binding"]["direction"] == direction
    assert bundle["binding"]["snapshot_id"] == snap.snapshot_id
    assert bundle["swings"], "related source swings must reach the chart DTO"
    assert bundle["displacements"] and bundle["displacements"][0]["is_qualified"] is True
    assert bundle["retests"]
    fvg = bundle["poi_zones"][0]
    assert fvg["top"] > fvg["bottom"] > 0
    assert "end_at_ms" not in fvg or fvg["end_at_ms"] is None   # no invented 2h end


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_fvg_geometry_comes_from_detector_not_midpoint(direction):
    snap = confirmed(direction)
    fvg = next(e for e in snap.evidence if e["kind"] == "FVG")
    top, bottom = fvg["extra"]["top"], fvg["extra"]["bottom"]
    if direction == "LONG":
        assert (bottom, top) == (2647.0, 2648.2)        # t14 high / t16 low in the fixture
    else:
        assert (bottom, top) == (round(5300 - 2648.2, 2), round(5300 - 2647.0, 2))
    assert top - bottom != 1.0                           # the old level +/- 0.5 fake


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_arm_get_order_fetch_chart_exact_frozen_ids(db, client, direction):
    seed_all(db, direction)
    run_cycle(db, direction)
    order = armed_orders(db)[0]

    listing = client.get("/api/v1/journal")
    assert listing.status_code == 200, listing.text
    row = next(o for o in listing.json() if o["id"] == order.id)
    assert row["scenario_snapshot_id"] == order.scenario_snapshot_id
    assert row["quality_snapshot_id"] == order.quality_snapshot_id
    assert row["quality_snapshot_id"].startswith("qsnap-")
    assert row["scenario_snapshot_id"].startswith("snap-")

    chart = client.get(f"/api/v1/orders/{order.id}/chart-evidence")
    assert chart.status_code == 200, chart.text
    b = chart.json()                                     # top-level bundle, not {"evidence": ...}
    assert "evidence" not in b or isinstance(b.get("evidence"), list)
    assert b["binding"]["source"] == "ORDER" and b["binding"]["snapshot_id"] == order.scenario_snapshot_id
    assert b["binding"]["direction"] == direction
    plan = b["risk_reward_plan"]
    assert plan["planned_entry"] == order.planned_entry and plan["stop_loss"] == order.stop_loss
    assert plan["take_profit"] == order.take_profit      # ACTUAL order geometry
    assert plan["net_reward_usdt"] is None or plan["net_reward_usdt"] > 0  # never a fake 0
    assert plan["net_reward_usdt"] != 0
    assert b["chain"]["verified"] is True


def test_scenario_chart_endpoint_and_snapshot_mismatch(db, client):
    seed_all(db, "LONG")
    run_cycle(db, "LONG", auto=False)
    snap_row = db.query(models.SMCScenarioSnapshot).filter_by(state="STRUCTURE_CONFIRMED").one()
    ok = client.get(f"/api/v1/smc/scenarios/{snap_row.scenario_id}/chart-evidence",
                    params={"snapshot_id": snap_row.snapshot_id})
    assert ok.status_code == 200, ok.text
    assert ok.json()["binding"]["snapshot_id"] == snap_row.snapshot_id
    assert ok.json()["binding"]["source"] == "SCENARIO"
    other = db.query(models.SMCScenarioSnapshot).filter(models.SMCScenarioSnapshot.scenario_id != snap_row.scenario_id).first()
    if other is not None:
        bad = client.get(f"/api/v1/smc/scenarios/{snap_row.scenario_id}/chart-evidence",
                         params={"snapshot_id": other.snapshot_id})
        assert bad.status_code == 409
    assert client.get("/api/v1/smc/scenarios/nope/chart-evidence").status_code == 404


def test_setup_chart_endpoint_exact_binding_409(db, client):
    seed_all(db, "LONG")
    run_cycle(db, "LONG", auto=False)
    ws = db.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M").one()
    snap = snapshot_from_row(db.query(models.SMCScenarioSnapshot).filter_by(snapshot_id=ws.scenario_snapshot_id).one())
    url = f"/api/v1/setups/{ws.id}/chart-evidence"
    good = client.get(url, params={"setup_instance_id": ws.setup_instance_id, "expected_revision": ws.version,
                                   "plan_fingerprint": snap.plan_fingerprint})
    assert good.status_code == 200, good.text
    assert good.json()["binding"]["direction"] == "LONG"
    assert client.get(url, params={"setup_instance_id": "someone-else"}).status_code == 409
    assert client.get(url, params={"expected_revision": ws.version + 5}).status_code == 409
    assert client.get(url, params={"plan_fingerprint": "deadbeef"}).status_code == 409


def test_session_forecasts_endpoint_is_cached_readonly(db, client):
    before = client.get("/api/v1/smc/session-forecasts")
    assert before.status_code == 200 and before.json()["status"] == "NOT_READY"
    seed_all(db, "LONG")
    run_cycle(db, "LONG", auto=False)
    orders_before = db.query(models.PaperOrder).count()
    snaps_before = db.query(models.SMCScenarioSnapshot).count()
    res = client.get("/api/v1/smc/session-forecasts", params={"symbol": "XAUUSDT"})
    body = res.json()
    assert res.status_code == 200 and body["status"] == "OK", body
    assert set(body["forecasts"]) == {"ASIA", "LONDON", "NY"}
    for f in body["forecasts"].values():
        assert f["entry_eligible"] is False
        assert f["probability"]["p_session_event"] is None
    # GET never runs the engine / creates anything
    assert db.query(models.PaperOrder).count() == orders_before
    assert db.query(models.SMCScenarioSnapshot).count() == snaps_before
    other_day = client.get("/api/v1/smc/session-forecasts", params={"day": "2020-01-01"}).json()
    assert other_day["status"] == "NOT_CACHED" and other_day["forecasts"] == {}
    assert client.get("/api/v1/smc/session-forecasts", params={"symbol": "xau;drop"}).status_code == 422
    assert client.get("/api/v1/smc/session-forecasts", params={"day": "13-99-99"}).status_code == 422

def test_candles_history_window_is_bounded_and_validated(db, client):
    seed_all(db, "LONG")
    rows = (db.query(models.Candle).filter_by(symbol="XAUUSDT", timeframe="5M")
            .order_by(models.Candle.timestamp.asc()).limit(12).all())
    assert len(rows) >= 12
    lo, hi = rows[2].timestamp, rows[9].timestamp
    url = "/api/v1/candles/XAUUSDT/5M"
    ok = client.get(url, params={"start_ms": lo, "end_ms": hi})
    assert ok.status_code == 200, ok.text
    stamps = [c["timestamp"] for c in ok.json()["candles"]]
    assert stamps == sorted(stamps) and stamps[0] >= lo and stamps[-1] <= hi and len(stamps) == 8
    assert client.get(url, params={"start_ms": lo}).status_code == 422
    assert client.get(url, params={"end_ms": hi}).status_code == 422
    assert client.get(url, params={"start_ms": hi, "end_ms": lo}).status_code == 422
    far = 4_000_000_000_000
    assert client.get(url, params={"start_ms": lo, "end_ms": far}).status_code == 422
