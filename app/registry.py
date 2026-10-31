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
    # 落在 models/ 下的子目录（社区模型归档用）
    subdir: str = ""
    # 已知哈希就钉死；未知则首次登记到 manifest 后比对
    expected_sha256: str | None = None


@dataclass(frozen=True)
class BundleSpec:
    """整包权重：下载 zip → 校验 → 解包。用于 GAME 这种多文件一体的发行包。"""

    key: str
    url: str
    filename: str
    expected_size: int
    expected_sha256: str
    unpack_dir: str
    required: tuple[str, ...]


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
    # 人声提纯：Mel-Band RoFormer karaoke 模型，一次前向出复数掩码，
    # 宿主端做 STFT/iSTFT 得到「人声主体 lead」与「伴奏残差 backing」。
    # 来源 HF bdsqlsz/mel_band_roformer_karaoke_aufr33-ONNX，原始 checkpoint
    # mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956（UVR 社区作品）。
    # 上游未标注许可（仓库无 license 字段、模型无 metadata），此处按「仅自用」处理。
    "karaoke_lead": ModelSpec(
        "karaoke_lead", "bdsqlsz/mel_band_roformer_karaoke_aufr33-ONNX",
        "model.onnx", 918_401_012,
        subdir="community/mel_band_roformer_karaoke_aufr33",
        expected_sha256="78d868338ab2f1bcab3aa8706137396bc48554f85c01e37f15d2a30e920259b8",
    ),
}

# GAME 1.0.3 medium ONNX：5 个图 + config.json，一次 zip 发。
# 许可：代码 MIT，权重 CC BY-NC-SA 4.0（个人自用 OK，不对外分发，权重不入库）。
BUNDLES: dict[str, BundleSpec] = {
    "game_medium": BundleSpec(
        key="game_medium",
        url=(
            "https://github.com/openvpi/GAME/releases/download/v1.0.3/"
            "GAME-1.0.3-medium-onnx.zip"
        ),
        filename="game/GAME-1.0.3-medium-onnx.zip",
        expected_size=179_775_226,
        expected_sha256="5b7a21e64c6310efac399f5d12838fffa70565be162436b5a4a65f290721e7d8",
        unpack_dir="game/GAME-1.0.3-medium-onnx",
        required=(
            "config.json", "encoder.onnx", "segmenter.onnx",
            "estimator.onnx", "bd2dur.onnx", "dur2bd.onnx",
        ),
    ),
}

PITCH_MODEL_KEY = "fcpe"
GAME_BUNDLE_KEY = "game_medium"
KARAOKE_MODEL_KEY = "karaoke_lead"


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

    digest = digest_of(path)
    # 钉死了哈希就直接比对；没钉死则靠 manifest 首次登记后比对
    if spec and spec.expected_sha256:
        if digest != spec.expected_sha256:
            raise ModelError(
                f"模型 {key} 哈希不符：期望 {spec.expected_sha256[:16]}…，实际 {digest[:16]}…。"
                "文件可能已损坏或被替换，请删除后重新下载。"
            )
        return

    manifest = _load_manifest()
    recorded = manifest.get(key)
    if recorded and recorded.get("sha256"):
        if recorded.get("size") != size:
            raise ModelError(
                f"模型 {key} 体积与登记值不符（登记 {recorded.get('size')}，实际 {size}）。"
            )
        if digest != recorded["sha256"]:
            raise ModelError(f"模型 {key} 哈希不符，文件可能已损坏，请删除后重启重新下载。")
        return

    manifest[key] = {"size": size, "sha256": digest, "file": path.name}
    _save_manifest(manifest)
    logger.info("登记模型 %s size=%d", key, size)


def _download(url: str, target: Path, progress=None) -> None:
    """流式下载（918 MB 那种体积不能用 read_bytes 一次性进内存）。"""
    import urllib.request

    target.parent.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(f"下载 {target.name}")
    logger.info("下载 %s -> %s", url, target)
    with urllib.request.urlopen(url, timeout=180) as response, target.open("wb") as handle:
        while True:
            block = response.read(1 << 20)
            if not block:
                break
            handle.write(block)


def ensure(key: str, progress=None) -> Path:
    """确保模型就绪，返回本地路径。"""
    spec = SPECS.get(key)
    if spec is None:
        raise ModelError(f"未登记的模型 key：{key}")

    target = MODELS_DIR / spec.subdir / spec.filename
    if not target.exists():
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
            raise ModelError(f"模型 {key} 下载失败：{exc}") from exc

        # 复制到 models/ 下，避免 HF 缓存目录被清理后重复下载（分块拷，别一次性进内存）
        target.parent.mkdir(parents=True, exist_ok=True)
        with Path(downloaded).open("rb") as source, target.open("wb") as sink:
            while True:
                block = source.read(1 << 22)
                if not block:
                    break
                sink.write(block)

    verify(key, target)
    return target


def ensure_bundle(key: str, progress=None) -> Path:
    """确保整包权重就绪（下载 → 校验 → 解包），返回解包后的目录。"""
    spec = BUNDLES.get(key)
    if spec is None:
        raise ModelError(f"未登记的权重包 key：{key}")

    unpack_dir = MODELS_DIR / spec.unpack_dir
    if all((unpack_dir / name).exists() for name in spec.required):
        return unpack_dir

    archive = MODELS_DIR / spec.filename
    if not archive.exists():
        try:
            _download(spec.url, archive, progress)
        except Exception as exc:
            raise ModelError(f"权重包 {key} 下载失败：{exc}") from exc

    size = archive.stat().st_size
    if size != spec.expected_size:
        raise ModelError(
            f"权重包 {key} 体积不符：期望 {spec.expected_size} 字节，实际 {size} 字节。"
        )
    digest = digest_of(archive)
    if digest != spec.expected_sha256:
        raise ModelError(
            f"权重包 {key} 哈希不符：期望 {spec.expected_sha256[:16]}…，实际 {digest[:16]}…。"
            "请删除后重新下载。"
        )

    import zipfile

    logger.info("解包 %s -> %s", archive, unpack_dir)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(MODELS_DIR / spec.unpack_dir.rsplit("/", 1)[0])
    missing = [name for name in spec.required if not (unpack_dir / name).exists()]
    if missing:
        raise ModelError(f"权重包 {key} 解包后缺少文件：{'、'.join(missing)}")
    return unpack_dir
