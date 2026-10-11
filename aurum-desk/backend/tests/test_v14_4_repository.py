import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, SMCScenario, SMCScenarioSnapshot
from database import run_schema_migrations
from services.smc_scenario_repository import (
    save_immutable_scenario_snapshot,
    upsert_scenario_revision,
    get_scenario,
    get_scenario_snapshot,
)
from services.smc_scenario_types import ScenarioSnapshot, ScenarioState

@pytest.fixture
def test_db():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)
    run_schema_migrations(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session, engine
    finally:
        session.close()

def test_idempotent_snapshot(test_db):
    session, _ = test_db
    snap = ScenarioSnapshot(
        scenario_id='scen-1',
        instance_id='inst-1',
        revision=1,
        symbol='XAUUSDT',
        setup_timeframe='5M',
        context_timeframes=['D', '4H'],
        family='STANDARD_SMC',
        direction='LONG',
        state=ScenarioState.STRUCTURE_CONFIRMED,
        as_of_ms=1000,
        evidence_cutoff_ms=1000,
        evidence_ids=['sw-1'],
        liquidity_map={},
        trigger_chain={},
        target=None,
        invalidation=None,
        summary_vi='Test snapshot',
        config_version=1,
        detector_version='v14.4',
    )

    # First save
    row1 = save_immutable_scenario_snapshot(session, snap)
    assert row1 is not None
    assert row1.snapshot_id == snap.snapshot_id
    session.commit()

    # Second save with exact same digest -> returns existing row idempotently
    row2 = save_immutable_scenario_snapshot(session, snap)
    assert row2.snapshot_id == row1.snapshot_id

def test_migration_twice_is_idempotent(test_db):
    session, engine = test_db
    # Run migration a 2nd time
    run_schema_migrations(engine)
    # Check tables still exist and work
    count = session.query(SMCScenario).count()
    assert count == 0
