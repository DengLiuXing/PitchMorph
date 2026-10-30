"""HTTP 层不碰推理的路径：路由、校验、404。真推理走 scripts/smoke_http.py。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import PitchParams

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


def test_missing_job_onset_is_404():
    assert client.get("/api/jobs/doesnotexist/onset").status_code == 404


def test_onset_endpoint_serves_file(tmp_path, monkeypatch):
    from app import config

    features = tmp_path / "features"
    features.mkdir()
    payload = {"version": 1, "frames": 4, "onsets": [1, 3]}
    (features / "onset.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(config, "job_dir", lambda job_id: tmp_path)

    response = client.get("/api/jobs/whatever/onset")
    assert response.status_code == 200
    assert response.json()["onsets"] == [1, 3]


def test_pitch_params_keeps_explicit_false():
    # exclude_none 只剔除 None，false 必须活着传到 worker，否则「关掉起音」不生效
    assert PitchParams().model_dump(exclude_none=True) == {}
    assert PitchParams(onsets=False).model_dump(exclude_none=True) == {"onsets": False}


def test_bad_stem_name_is_404():
    assert client.get("/api/jobs/doesnotexist/stems/evil").status_code == 404
