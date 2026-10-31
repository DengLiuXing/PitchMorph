"""骨架冒烟：1 s 合成 wav + 一个假阶段，验证状态机、meta 与端点。

真实阶段的分轨/音高/音符在各自落地时补测（它们要下权重，不能进单元测试）。
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import runner
from app.core import config
from app.domain import params as param_mod
from app.domain.artifact import Artifact
from app.domain.job import IllegalTransition, Job
from app.main import app
from app.stages import REGISTRY
from app.storage import jobs as store


@pytest.fixture()
def temp_world(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "jobs")
    config.DATA_DIR.mkdir(parents=True)
    wav = tmp_path / "tone.wav"
    sr = 44100
    t = np.arange(sr) / sr
    import soundfile as sf

    sf.write(wav, (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), sr)
    return tmp_path, wav


def test_state_machine_rejects_illegal_transition():
    job = Job(id="x", kind="separate", params={})
    job.transition("running")
    job.transition("succeeded")
    with pytest.raises(IllegalTransition):
        job.transition("running")


def test_params_validation_and_schema():
    with pytest.raises(ValueError):
        param_mod.normalize("separate", {"tier": "six"})
    with pytest.raises(ValueError):
        param_mod.normalize("separate", {"tier": "four", "bogus": 1})
    normalized = param_mod.normalize("pitch", {})
    assert set(normalized) == {"f0_min", "f0_max"}
    assert "specs" in param_mod.schema()


def test_empty_pipeline_runs_and_meta_explains_artifacts(temp_world):
    tmp_path, wav = temp_world

    def fake_separate(job: Job) -> list[Artifact]:
        runner.progress(job, 0.5, "fake")
        return [
            Artifact(
                path="stems/vocals.wav",
                role="stem",
                model="fake@test",
                ep="cpu",
                params_hash=param_mod.fingerprint(job.params),
            )
        ]

    REGISTRY["separate"] = fake_separate
    try:
        job = Job(id=store.new_id(), kind="separate", params=param_mod.normalize("separate", {}))
        store.job_dir(job.id).mkdir(parents=True)
        runner.submit(job)
        done = runner.wait(job.id)
    finally:
        REGISTRY.pop("separate", None)

    assert done.state == "succeeded", done.error
    assert done.artifacts[0].model == "fake@test"
    meta = json.loads(store.meta_path(job.id).read_text(encoding="utf-8"))
    assert meta["artifacts"][0]["params_hash"] == done.artifacts[0].params_hash


def test_endpoints_smoke(temp_world):
    client = TestClient(app)
    assert client.get("/api/health").json()["ok"] is True
    assert "specs" in client.get("/api/params/schema").json()
    assert client.get("/api/runtime").json()["detected"] in ("winml", "dml", "cpu")
    _, wav = temp_world
    created = client.post(
        "/api/jobs",
        json={"kind": "separate", "params": {"tier": "vocals"}, "source": str(wav)},
    )
    assert created.status_code == 200
    job_id = created.json()["id"]
    assert client.get(f"/api/jobs/{job_id}").json()["kind"] == "separate"
    assert any(j["id"] == job_id for j in client.get("/api/jobs").json())
    assert client.delete(f"/api/jobs/{job_id}").json()["deleted"] == job_id
