"""下载并解包 GAME 权重到 `app/worker/game/weights/<体积>/`。

用法：

    uv run python scripts/fetch_game.py            # small（默认，48 MB，实测够用）
    uv run python scripts/fetch_game.py --model medium
    uv run python scripts/fetch_game.py --force    # 已存在也重下

只认 v1.0.0：那是上游唯一发布 PyTorch `.pt` 的版本，1.0.1–1.0.3 只有 ONNX / `.oudep`。
体积与精度的取舍见 `docs/05-game.md` 的 M0 实测。权重不进 git（见 .gitignore）。
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402

_TAG = "v1.0.0"
_URL = "https://github.com/openvpi/GAME/releases/download/{tag}/GAME-1.0-{model}.zip"
_SIZES = {"small": 45, "medium": 176, "large": 349}  # MB，只用于提示


def _download(url: str, target: Path) -> None:
    print(f"下载 {url}")
    print(f"  → {target}（约 {_SIZES.get(target.stem.split('-')[-1], '?')} MB，走公网，卡住时先解决网络）")
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, target.open("wb") as handle:
        total = 0
        while chunk := response.read(1 << 20):
            handle.write(chunk)
            total += len(chunk)
            print(f"\r  {total / 2**20:.1f} MB", end="", flush=True)
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 GAME 权重")
    parser.add_argument("--model", choices=sorted(_SIZES), default="small")
    parser.add_argument("--force", action="store_true", help="已解包也重新下载")
    args = parser.parse_args()

    target = config.GAME_WEIGHTS_DIR / f"GAME-1.0-{args.model}"
    if (target / "model.pt").exists() and not args.force:
        print(f"已存在：{target / 'model.pt'}")
        return 0

    archive = config.GAME_WEIGHTS_DIR / f"GAME-1.0-{args.model}.zip"
    if not archive.exists() or args.force:
        _download(_URL.format(tag=_TAG, model=args.model), archive)

    print(f"解包 → {config.GAME_WEIGHTS_DIR}")
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(config.GAME_WEIGHTS_DIR)

    model_file = target / "model.pt"
    if not model_file.exists():
        print(f"解包里没有 {model_file}，检查上游 release 结构是否变了", file=sys.stderr)
        return 1
    print(f"完成：{model_file}（{model_file.stat().st_size / 2**20:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
