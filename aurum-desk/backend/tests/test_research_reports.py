import pytest

def test_research_reports_create_and_list(client):
    # 1. Test POST /api/v1/reports/generate with query param
    resp1 = client.post("/api/v1/reports/generate?report_type=SESSION_REPORT")
    assert resp1.status_code == 200, f"Expected 200, got {resp1.status_code}: {resp1.text}"
    data1 = resp1.json()
    assert data1["status"] == "success"
    assert "report_id" in data1
    assert "content_markdown" in data1
    assert "# BÁO CÁO PHÂN TÍCH XAUUSDT" in data1["content_markdown"]

    # 2. Test POST /api/v1/reports (alias) with JSON body
    resp2 = client.post("/api/v1/reports", json={"report_type": "PREMARKET"})
    assert resp2.status_code == 200, f"Expected 200, got {resp2.status_code}: {resp2.text}"
    data2 = resp2.json()
    assert data2["status"] == "success"

    # 3. Test GET /api/v1/reports
    resp3 = client.get("/api/v1/reports")
    assert resp3.status_code == 200
    data3 = resp3.json()
    assert "reports" in data3
    assert "session_info" in data3
    assert isinstance(data3["reports"], list)
    assert len(data3["reports"]) >= 2
    assert data3["reports"][0]["id"] == data2["report_id"]

