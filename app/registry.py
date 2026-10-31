"""模型清单、下载与校验。

权重一律落在 models/ 下（.gitignore 已忽略），首次运行自动从 Hugging Face 拉取。
校验策略：首次下载记录 size + sha256 到 manifest.json，之后每次启动比对；
不一致直接抛 ModelError，对应文档里"权重大小或哈希不符"这一失败原因。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .config import MODELS_DIR

logger = logging.getLogger("pitchmorph.registry")

MANIFEST = MODELS_DIR / "manifest.json"
HF_CACHE = MODELS_DIR / "hf"


class ModelError(RuntimeError):
    """模型缺失、体积或哈希不符。"""


@dataclass(frozen=True)
class ModelSpec:
    key: str
    repo_id: str
    filename: str
    expected_size: int | None = None


# 体积为 2026-10-31 实测值；未知的留 None，靠 manifest 记录后比对
SPECS: dict[str, ModelSpec] = {
    # 快速四轨档：单文件 htdemucs，一次前向出 4 轨（成本 = 一个专家）
    "demucs_htdemucs": ModelSpec(
        "demucs_htdemucs", "StemSplitio/htdemucs-onnx",
        "htdemucs_fp16weights.onnx", 165_612_636,
    ),
    "demucs_vocals": ModelSpec(
        "demucs_vocals", "StemSplitio/htdemucs-ft-vocals-onnx",
        "htdemucs_ft_vocals_fp16weights.onnx", 165_612_636,
    ),
    "demucs_drums": ModelSpec(
        "demucs_drums", "StemSplitio/htdemucs-ft-drums-onnx",
        "htdemucs_ft_drums_fp16weights.onnx", 165_612_636,
    ),
    "demucs_bass": ModelSpec(
        "demucs_bass", "StemSplitio/htdemucs-ft-bass-onnx",
        "htdemucs_ft_bass_fp16weights.onnx", 165_612_636,
    ),
    "demucs_other": ModelSpec(
        "demucs_other", "StemSplitio/htdemucs-ft-other-onnx",
        "htdemucs_ft_other_fp16weights.onnx", 165_612_636,
    ),
    # 社区版：图内自带 16 kHz 波形 -> mel 前端，输入 [1,N]，输出 salience [1,T,360]
    # 体积随上游更新可能变动，这里不钉死，由 manifest 首次登记后比对
    "fcpe": ModelSpec("fcpe", "Scicom-intl/fcpe-onnx", "fcpe.onnx", None),
}

PITCH_MODEL_KEY = "fcpe"
LOCAL_FCPE = MODELS_DIR / "fcpe_net.onnx"


def _load_manifest() -> dict[str, dict[str, object]]:
    if not MANIFEST.exists():
        return {}
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("manifest.json 损坏，按空清单处理")
        return {}


def _save_manifest(data: dict[str, dict[str, object]]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


# (路径, 体积, 修改时间) -> sha256。模型文件不动就不必重算 165 MB，
# 连续处理多首歌时这一项从 4×0.14 s 降到接近 0。
_HASH_CACHE: dict[str, tuple[int, int, str]] = {}


def digest_of(path: Path) -> str:
    stat = path.stat()
    key = str(path)
    cached = _HASH_CACHE.get(key)
    if cached and cached[0] == stat.st_size and cached[1] == stat.st_mtime_ns:
        return cached[2]
    digest = sha256_of(path)
    _HASH_CACHE[key] = (stat.st_size, stat.st_mtime_ns, digest)
    return digest


def verify(key: str, path: Path) -> None:
    """体积 + 哈希双重校验；首次见到则登记。"""
    if not path.exists():
        raise ModelError(f"缺少模型文件：{path}")
    size = path.stat().st_size
    spec = SPECS.get(key)
    if spec and spec.expected_size and size != spec.expected_size:
        raise ModelError(
            f"模型 {key} 体积不符：期望 {spec.expected_size} 字节，实际 {size} 字节。"
            "请删除该文件后重启，让程序重新下载。"
        )

    manifest = _load_manifest()
    recorded = manifest.get(key)
    if recorded and recorded.get("sha256"):
        if recorded.get("size") != size:
            raise ModelError(
                f"模型 {key} 体积与登记值不符（登记 {recorded.get('size')}，实际 {size}）。"
            )
        digest = digest_of(path)
        if digest != recorded["sha256"]:
            raise ModelError(f"模型 {key} 哈希不符，文件可能已损坏，请删除后重启重新下载。")
        return

    digest = digest_of(path)
    manifest[key] = {"size": size, "sha256": digest, "file": path.name}
    _save_manifest(manifest)
    logger.info("登记模型 %s size=%d", key, size)


def ensure(key: str, progress=None) -> Path:
    """确保模型就绪，返回本地路径。"""
    spec = SPECS.get(key)
    if spec is None:
        raise ModelError(f"未登记的模型 key：{key}")

    local = MODELS_DIR / spec.filename
    path = local if local.exists() else None

    if path is None:
        if progress:
            progress(f"下载权重 {spec.filename}")
        logger.info("下载 %s/%s", spec.repo_id, spec.filename)
        try:
            from huggingface_hub import hf_hub_download

            HF_CACHE.mkdir(parents=True, exist_ok=True)
            downloaded = hf_hub_download(
                repo_id=spec.repo_id, filename=spec.filename, cache_dir=str(HF_CACHE)
            )
        except Exception as exc:
            if key == PITCH_MODEL_KEY and LOCAL_FCPE.exists():
                logger.warning("社区 FCPE 下载失败（%s），回落仓库内自建图", exc)
                return LOCAL_FCPE
            raise ModelError(f"模型 {key} 下载失败：{exc}") from exc

        # 复制到 models/ 根目录，避免 HF 缓存目录被清理后重复下载
        target = MODELS_DIR / spec.filename
        if not target.exists():
            target.write_bytes(Path(downloaded).read_bytes())
        path = target

    verify(key, path)
    return path
