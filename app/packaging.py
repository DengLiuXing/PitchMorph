"""打包下载：音轨一个包，工程文件一个包。

- WAV 用 `ZIP_STORED`：PCM 本来就压不动，deflate 只是白烧 CPU
- 写到任务目录再返回，不把整首歌读进内存（整曲四轨约 270 MB）
- 源文件的修改时间没变就复用上次的包，重复点「打包」不重新压
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path


def build_zip(job_dir: Path, name: str, entries: list[tuple[Path, str]]) -> Path | None:
    """`entries` 是 (磁盘路径, 包内路径)。一个文件都不存在时返回 None。"""
    present = [(path, arcname) for path, arcname in entries if path.exists()]
    if not present:
        return None

    target = job_dir / name
    newest = max(path.stat().st_mtime for path, _ in present)
    if target.exists() and target.stat().st_mtime >= newest:
        return target

    tmp = target.with_name(target.name + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as archive:
        for path, arcname in present:
            archive.write(path, arcname)
    os.replace(tmp, target)
    return target
