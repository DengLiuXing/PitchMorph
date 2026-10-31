"""端到端：上传 -> 分轨 -> 波形 -> 音高 -> 音频流（仅人声档）。"""
from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

SAMPLES = Path(__file__).resolve().parent.parent / "temp-dev" / "samples" / "vocadito"
CLIP = SAMPLES / "Audio" / "vocadito_1.wav"

pytestmark = pytest.mark.skipif(not CLIP.exists(), reason="缺少 vocadito 样例素材")


def _wait(client: TestClient, job_id: str, states: set[str], timeout: float = 240.0) -> dict:
    deadline = time.time() + timeout
    meta: dict = {}
    while time.time() < deadline:
        meta = client.get(f"/api/jobs/{job_id}").json()
        if meta.get("state") in states:
            return meta
        time.sleep(0.5)
    pytest.fail(f"等待超时，最后状态：{meta.get('state')} / {meta.get('failure')}")


def test_vocals_pipeline() -> None:
    client = TestClient(app)
    with CLIP.open("rb") as handle:
        response = client.post(
            "/api/jobs",
            files={"file": ("vocadito_1.wav", handle, "audio/wav")},
            data={"mode": "vocals"},
        )
    assert response.status_code == 200, response.text
    job_id = response.json()["id"]

    meta = _wait(client, job_id, {"ready", "failed", "cancelled"})
    assert meta["state"] == "ready", meta.get("failure")
    assert [stem["name"] for stem in meta["stems"]] == ["vocals"]
    assert meta["duration"] > 30

    audio = client.get(f"/api/jobs/{job_id}/audio/vocals")
    assert audio.status_code == 200
    assert len(audio.content) > 100_000

    ranged = client.get(f"/api/jobs/{job_id}/audio/vocals", headers={"Range": "bytes=0-1023"})
    assert ranged.status_code == 206
    assert len(ranged.content) == 1024

    peaks = client.get(f"/api/jobs/{job_id}/peaks/vocals").json()
    assert len(peaks["min"]) == len(peaks["max"]) > 100

    # 导出：命名规范 (源文件名)_(音轨类型)，且只含音轨
    single = client.get(f"/api/jobs/{job_id}/audio/vocals")
    assert "vocadito_1_vocals.wav" in single.headers["content-disposition"]

    bundle = client.get(f"/api/jobs/{job_id}/export")
    assert bundle.status_code == 200
    assert bundle.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        assert archive.namelist() == ["vocadito_1_vocals.wav"]

    started = client.post(f"/api/jobs/{job_id}/pitch/vocals")
    assert started.status_code == 200
    for _ in range(120):
        curve = client.get(f"/api/jobs/{job_id}/pitch/vocals").json()
        if curve.get("f0"):
            break
        time.sleep(0.5)
    assert curve["hop_ms"] == 10
    assert len(curve["f0"]) == len(curve["voiced"]) == len(curve["confidence"])
    assert any(curve["voiced"])

    client.delete(f"/api/jobs/{job_id}")
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_rejects_unsupported_suffix() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/jobs",
        files={"file": ("note.txt", b"hello", "text/plain")},
        data={"mode": "vocals"},
    )
    assert response.status_code == 400
