"""HTTP 层不碰推理的路径：路由、校验、404。真推理走 scripts/smoke_http.py。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app import config
from app.jobs import Stage, registry
from app.main import app
from app.exports import render_export
from app.schemas import ExportParams, JobParams, NotesParams, PitchParams

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


def test_missing_job_analyze_is_404():
    assert client.post("/api/jobs/doesnotexist/analyze").status_code == 404


def test_analyze_needs_vocals_stem(monkeypatch, tmp_path):
    # 没有 vocals 轨就没有分析输入，必须在入队前拒绝，不能白跑一次模型
    monkeypatch.setattr(config, "job_dir", lambda job_id: tmp_path)
    job = registry.create("a.wav", "vocals")
    registry.update(job.id, stage=Stage.DONE)  # 排队的任务本来就该 409，这里测的是别的分支
    try:
        response = client.post(f"/api/jobs/{job.id}/analyze")
        assert response.status_code == 400
        assert "vocals" in response.json()["detail"]
    finally:
        registry.remove(job.id)


def test_notes_params_pass_only_what_user_set():
    assert NotesParams().model_dump(exclude_none=True) == {}
    assert NotesParams(nsteps=4, seg_threshold=0.1).model_dump(exclude_none=True) == {
        "nsteps": 4,
        "seg_threshold": 0.1,
    }


def test_unknown_scope_is_400():
    response = client.post(
        "/api/jobs",
        files={"file": ("a.wav", b"RIFF0000WAVE", "audio/wav")},
        data={"scope": "随便"},
    )
    assert response.status_code == 400
    assert "范围" in response.json()["detail"]


def test_analyze_only_rejects_non_dry_filename():
    # 直接在混音上提音高没有意义：文件名看不出是干声就拒绝，并说清怎么做
    response = client.post(
        "/api/jobs",
        files={"file": ("一首歌.mp3", b"ID3", "audio/mpeg")},
        data={"scope": "analyze"},
    )
    assert response.status_code == 400
    assert "干声" in response.json()["detail"]


def test_bad_params_json_is_400():
    response = client.post(
        "/api/jobs",
        files={"file": ("song_vocals.wav", b"RIFF0000WAVE", "audio/wav")},
        data={"scope": "analyze", "params": "{不是 json"},
    )
    assert response.status_code == 400
    assert "JSON" in response.json()["detail"]


def test_jobs_can_be_filtered_by_scope():
    assert client.get("/api/jobs", params={"scope": "separate"}).status_code == 200
    assert client.get("/api/jobs", params={"scope": "乱写"}).status_code == 400


def test_export_smooth_is_normalized_at_the_boundary():
    # 偶数取下一个奇数、超界夹回 1..15：非法值不流到导出层
    assert ExportParams().smooth == 3
    assert ExportParams(smooth=4).smooth == 5
    assert ExportParams(smooth=99).smooth == 15
    assert JobParams().exports.smooth == 3


def test_export_uses_task_smooth_by_default(monkeypatch, tmp_path):
    # 导出是那一批产物的定义：没带 ?smooth 时取任务创建时定的窗口
    monkeypatch.setattr(config, "job_dir", lambda job_id: tmp_path)
    features = tmp_path / "features"
    features.mkdir()
    payload = {"version": 1, "hop_sec": 0.01, "frames": 100, "f0": [440.0] * 100}
    (features / "pitch.json").write_text(json.dumps(payload), encoding="utf-8")

    job = registry.create("a.wav", "vocals", "both", {"exports": {"smooth": 9}})
    registry.update(job.id, stage=Stage.DONE)
    try:
        response = client.get(f"/api/jobs/{job.id}/exports/notes_midi")
        assert response.status_code == 200
        assert response.content == render_export(tmp_path, "notes_midi", smooth=9)
    finally:
        registry.remove(job.id)


def test_game_model_path_accepts_size_names(monkeypatch, tmp_path):
    # 体积名是参数，路径是它的展开结果：缺哪个就返回 None，别猜
    monkeypatch.setattr(config, "GAME_WEIGHTS_DIR", tmp_path)
    assert config.game_model_path("small") is None
    target = tmp_path / "GAME-1.0-small"
    target.mkdir()
    (target / "model.pt").write_bytes(b"x")
    assert config.game_model_path("small") == target / "model.pt"
    assert config.game_model_path("medium") is None
    assert config.game_model_path() == target / "model.pt"  # 未指定体积时退化到任一已解包的


def test_bad_stem_name_is_404():
    assert client.get("/api/jobs/doesnotexist/stems/evil").status_code == 404
