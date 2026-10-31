"""权重下载：URL + 体积 + SHA256，断点续传，失败删残片。

清单口径见 docs/weights.md §5；只有「官方能下载的」走这里，FCPE 随仓库入库。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from app.core import config

MANIFEST: dict[str, dict] = {
    "game": {
        "url": config.GAME_ZIP,
        "size": config.GAME_ZIP_SIZE,
        "sha256": config.GAME_ZIP_SHA256,
        "filename": "GAME-1.0.3-medium-onnx.zip",
    },
}


class WeightIntegrityError(RuntimeError):
    code = "weight_integrity"


@dataclass(frozen=True)
class Entry:
    url: str
    size: int
    sha256: str
    filename: str


def entry(name: str) -> Entry:
    if name not in MANIFEST:
        raise KeyError(f"未登记的权重：{name}")
    return Entry(**MANIFEST[name])


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure(name: str, cache_dir: Path | None = None) -> Path:
    """本地已存在且哈希匹配就跳过；否则断点续传下载并校验。"""
    cache = Path(cache_dir or config.WEIGHTS_CACHE)
    cache.mkdir(parents=True, exist_ok=True)
    item = entry(name)
    target = cache / item.filename
    if target.exists() and target.stat().st_size == item.size and sha256_of(target) == item.sha256:
        return target
    part = target.with_suffix(target.suffix + ".part")
    _download(item.url, part)
    if part.stat().st_size != item.size:
        part.unlink(missing_ok=True)
        raise WeightIntegrityError(f"{name} 体积不符：{part.stat().st_size} != {item.size}")
    if sha256_of(part) != item.sha256:
        part.unlink(missing_ok=True)
        raise WeightIntegrityError(f"{name} SHA256 不匹配，已删除残片")
    part.replace(target)
    return target


def _download(url: str, dest: Path) -> None:
    mirror = os.environ.get("PITCHMORPH_MIRROR")  # 形如 https://gh-proxy.com/
    effective = f"{mirror.rstrip('/')}/{url}" if mirror else url
    offset = dest.stat().st_size if dest.exists() else 0
    request = urllib.request.Request(effective)
    if offset:
        request.add_header("Range", f"bytes={offset}-")
    with urllib.request.urlopen(request, timeout=60) as response, dest.open("ab") as handle:
        shutil.copyfileobj(response, handle, length=1 << 20)


def extract(name: str, cache_dir: Path | None = None) -> Path:
    """解包到同名目录，已解包则直接返回。"""
    archive = ensure(name, cache_dir)
    target = archive.with_suffix("")
    if not target.exists():
        shutil.unpack_archive(str(archive), str(target))
    return target
