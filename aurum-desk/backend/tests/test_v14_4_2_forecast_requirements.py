import pytest
from services.smc_session_forecast_service import SessionForecast

def test_forecast_relation_status():
    f = SessionForecast(
        session="NY",
        session_id="NY",
        as_of_ms=1000,
        data_status="OK",
        is_partial=False,
        hypothesis_type="CONTINUATION_WATCH",
        relation_status="SUPPORTS"
    )
    assert f.relation_status == "SUPPORTS"
    d = f.to_dict()
    assert "relation_status" in d
    assert d["relation_status"] == "SUPPORTS"
