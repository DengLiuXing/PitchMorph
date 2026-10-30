"""把 V2 里不可再生的资产一次性迁移到 V3。

默认行为：
- `models/`（权重与 ONNX）**移动**过去——这些是已经下载/导出好的，重新获取要花时间和流量；
- `samples/`（vocadito 与 OpenUtau 参考）**移动**过去——V3 的评测基准；
- `docs/`（设计结论）**复制**过去——V2 仓库仍要保留文档，代码才是被取代的部分。

目标已存在则跳过（幂等）；`--force` 覆盖，`--copy` 改为复制（V2 侧保留副本）。
资产都不进 git（V3 的 `.gitignore` 已忽略 `models/` 与 `samples/`）。

用法：

    python tools/migrate_from_v2.py                    # 移动资产 + 复制文档
    python tools/migrate_from_v2.py --dry-run          # 只看会做什么
    python tools/migrate_from_v2.py --copy             # 全部复制，不动源
    python tools/migrate_from_v2.py --source D:\\PitchMorph-V2
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
V3 = HERE.parent
DEFAULT_SOURCE = V3.parent / "PitchMorph-V2"

# (V2 相对路径, V3 相对路径, 动作, 说明)。动作只影响默认值，--copy 会把 move 降级成 copy。
PLAN: list[tuple[str, str, str, str]] = [
    # 权重：已下载/已导出的 ONNX，直接搬；V3 侧统一放 models/
    ("temp-dev/spike/models/fcpe_net.onnx", "models/fcpe_net.onnx", "move", "FCPE 网络（已验证与 PyTorch 等价）"),
    ("temp-dev/spike/models/fcpe_net_opset19.onnx", "models/fcpe_net_opset19.onnx", "move", "FCPE 网络 opset19 备用版"),
    ("temp-dev/spike/models/fcpe_latent.onnx", "models/fcpe_latent.onnx", "move", "FCPE 纯主干（DML 定位用）"),
    # GAME 不再迁移：官方有 ONNX release，按 docs/weights.md 运行时下载（含哈希校验）
    ("temp-dev/spike/models/fcpe_net.npz", "models/fcpe_net.npz", "move", "数值回归参考（mel/f0）"),
    ("temp-dev/spike/models/fcpe_latent.npz", "models/fcpe_latent.npz", "move", "数值回归参考（latent）"),
    # 样本：评测基准，V3 侧放 samples/
    ("temp-dev/samples/vocadito", "samples/vocadito", "move", "40 首人工标注（含双标注者）"),
    ("temp-dev/samples/openutau_official", "samples/openutau_official", "move", "两首 OpenUtau 官方转写"),
    # 文档：设计结论，复制（V2 仍保留）
    ("docs/01-decisions.md", "docs/01-decisions.md", "copy", "D1–D19 决策与重开条件"),
    ("docs/05-game.md", "docs/05-game.md", "copy", "GAME 实测与 ONNX 可行性"),
    ("docs/07-spike.md", "docs/07-spike.md", "copy", "运行时选型实测"),
    ("docs/08-v3-migration.md", "docs/08-v3-migration.md", "copy", "迁移原则"),
]


def size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def human(value: int) -> str:
    return f"{value / 2**20:7.1f} MB"


def transfer(src: Path, dst: Path, action: str, force: bool, dry: bool) -> tuple[str, int]:
    if dst.exists() and not force:
        return "skip", 0
    size = size_of(src)
    if dry:
        return action, size

    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()

    if action == "move":
        shutil.move(str(src), str(dst))
    elif src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
    return action, size


def main() -> int:
    parser = argparse.ArgumentParser(description="把 V2 资产迁移到 V3")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--copy", action="store_true", help="全部改复制，不移动源")
    parser.add_argument("--force", action="store_true", help="目标已存在也覆盖")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source = args.source.resolve()
    if not (source / "app").exists():
        print(f"源目录不像 PitchMorph-V2：{source}", file=sys.stderr)
        return 2

    print(f"源  {source}")
    print(f"目标 {V3}\n")
    print(f"{'动作':6s} {'体积':>10s}  路径")
    print("-" * 78)

    totals = {"move": 0, "copy": 0, "skip": 0}
    missing: list[str] = []
    for src_rel, dst_rel, default_action, note in PLAN:
        src = source / src_rel
        dst = V3 / dst_rel
        if not src.exists():
            if not dst.exists():
                missing.append(relative)
            continue
        action = "copy" if args.copy and default_action == "move" else "copy" if default_action == "copy" else "move"
        done, size = transfer(src, dst, action, args.force, args.dry_run)
        totals[done] += size
        print(f"{done:6s} {human(size)}  {dst_rel}  — {note}")

    print("-" * 78)
    print(f"移动 {human(totals['move'])} · 复制 {human(totals['copy'])} · 跳过 {human(totals['skip'])}")
    if missing:
        print("\n源里没有（已忽略）：")
        for item in missing:
            print("  -", item)
    if args.dry_run:
        print("\n这是 --dry-run，未做任何改动")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
