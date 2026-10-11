import pytest
import models


@pytest.fixture
def cached_analysis(monkeypatch):
    from services.analysis_cache_service import analysis_cache_service

    async def read_only_analysis(**kwargs):
        return {'swings': [], 'structure_events': [], 'active_fvgs': []}

    monkeypatch.setattr(analysis_cache_service, 'get_or_compute_analysis', read_only_analysis)


def test_short_preview_uses_quantity_and_account_balance(client, isolated_db, cached_analysis):
    isolated_db.add(models.DayAudit(date_str='2026-10-11', initial_equity=999.57,
                                    current_equity=999.57))
    isolated_db.commit()
    counts_before = (isolated_db.query(models.PaperOrder).count(),
                     isolated_db.query(models.SetupQualitySnapshot).count(),
                     isolated_db.query(models.DayAudit).count())
    response = client.post('/api/v1/quality/preview', json={
        'direction': 'SHORT', 'entry': 4199.79, 'stop_loss': 4204.79,
        'take_profit': 4198, 'quantity': 0.29, 'leverage': 30,
    })
    assert response.status_code == 200
    result = response.json()['calc']
    assert result['quantity'] == 0.29
    assert result['gross_rr'] == pytest.approx(0.358, abs=0.0001)
    assert result['gross_loss_usdt'] == 1.45
    assert result['gross_reward_usdt'] == 0.52
    assert result['net_risk_usdt'] == 2.48
    assert result['net_reward_usdt'] == -0.48
    assert result['budget_usdt'] == pytest.approx(2.5, abs=0.01)
    assert result['can_execute'] is False
    assert (isolated_db.query(models.PaperOrder).count(),
            isolated_db.query(models.SetupQualitySnapshot).count(),
            isolated_db.query(models.DayAudit).count()) == counts_before


@pytest.mark.parametrize('bad', [{'direction': 'SELL'}, {'quantity': 0}, {'quantity': -1}, {'risk_pct': 0}])
def test_preview_rejects_invalid_sizing_contract(client, cached_analysis, bad):
    response = client.post('/api/v1/quality/preview', json={
        'direction': 'SHORT', 'entry': 4199.79, 'stop_loss': 4204.79,
        'take_profit': 4198, **bad,
    })
    assert response.status_code == 422
