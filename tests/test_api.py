"""HTTP 层不碰推理的路径：路由、校验、404。真推理走 scripts/smoke_http.py。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_reports_limits():
    payload = client.get("/api/health").json()
    assert payload["ok"] is True
    assert payload["max_upload_mb"] > 0
    assert payload["max_duration_sec"] > 0


def test_models_puts_default_first():
    payload = client.get("/api/models").json()
    # 前端会选中列表首项，默认档必须排在第一个（v1 踩过）
    assert payload["tiers"][0]["id"] == payload["default"]


def test_jobs_returns_list():
    assert isinstance(client.get("/api/jobs").json(), list)


def test_unknown_tier_is_rejected():
    response = client.post(
        "/api/jobs",
        files={"file": ("a.wav", b"RIFF0000WAVE", "audio/wav")},
        data={"tier": "不存在的档"},
    )
    assert response.status_code == 400
    assert "档位" in response.json()["detail"]


def test_missing_job_pitch_is_404():
    assert client.get("/api/jobs/doesnotexist/pitch").status_code == 404


def test_missing_job_cancel_is_404():
    assert client.post("/api/jobs/doesnotexist/cancel").status_code == 404


def test_bad_stem_name_is_404():
    assert client.get("/api/jobs/doesnotexist/stems/evil").status_code == 404
