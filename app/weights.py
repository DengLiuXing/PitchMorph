"""Pre-download ONNX weights without running a separation job.

    uv run python -m app.weights --list
    uv run python -m app.weights            # 默认档（仅人声，166 MB）
    uv run python -m app.weights vocals     # 只下 166 MB 的人声专家
    uv run python -m app.weights all

Downloads land in the same Hugging Face cache the app reads (`data/models`), so
once this finishes the web UI starts instantly and never touches the network.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

from . import config
from .errors import Canceled, SeparationError, friendly_error

log = logging.getLogger("pitchmorph.weights")

ProgressCallback = Callable[[float], None]
CancelCheck = Callable[[], bool]


def _remote(alias: str) -> tuple[str, str]:
    """Resolve a model alias to ``(repo_id, filename)`` for the fp16 weights."""
    from demucs_onnx import list_models

    url = list_models()[alias][config.PRECISION]
    head, marker, filename = url.partition("/resolve/main/")
    if not marker:
        raise SystemExit(f"无法从 {url} 解析出文件地址")
    return head.split("huggingface.co/", 1)[-1], filename


def required_files(model: str, stems: Sequence[str]) -> list[tuple[str, str, str]]:
    """``[(label, repo_id, filename)]`` needed to run ``model`` for ``stems``."""
    if model in config.SINGLE_FILE_MODELS:
        pairs = [(model, model)]
    elif model == "htdemucs_ft":
        pairs = [(stem, f"htdemucs_ft_{stem}") for stem in stems]
    else:
        # A single-specialist alias (``htdemucs_ft_vocals``) is keyed by its
        # stem so callers can look it up the same way as a bag member.
        pairs = [(model.replace("htdemucs_ft_", ""), model)]
    return [(label, *_remote(alias)) for label, alias in pairs]


def is_cached(filename: str) -> bool:
    return next(iter(config.MODEL_DIR.rglob(filename)), None) is not None


def missing_files(model: str, stems: Sequence[str]) -> list[tuple[str, str, str]]:
    return [entry for entry in required_files(model, stems) if not is_cached(entry[2])]


def weights_cached(model: str, stems: Sequence[str]) -> bool:
    try:
        return not missing_files(model, stems)
    except Exception:  # catalog lookup is best effort; assume not cached
        return False


def resolve_paths(model: str, stems: Sequence[str]) -> dict[str, Path]:
    """Local ONNX paths for ``model``, keyed by stem (or by model for single-file)."""
    from huggingface_hub import hf_hub_download

    return {
        label: Path(
            hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                cache_dir=str(config.MODEL_DIR),
                local_files_only=True,
            ),
        )
        for label, repo_id, filename in required_files(model, stems)
    }


def _cache_bytes() -> int:
    """Total size of the weight cache. Used to estimate download progress."""
    if not config.MODEL_DIR.exists():
        return 0
    total = 0
    for path in config.MODEL_DIR.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def ensure_weights(
    model: str,
    stems: Sequence[str],
    *,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> None:
    """Download any missing ONNX weights, cancellably.

    ``demucs-onnx`` downloads inside ``separate()``, which gives no progress hook
    and cannot be interrupted. Fetching here means we can report real bytes and
    abandon the transfer the moment the user hits cancel (the daemon thread
    keeps filling the cache, which is harmless).
    """
    pending = missing_files(model, stems)
    if not pending:
        return

    expected = max(1, len(pending) * config.WEIGHT_FILE_MB * 1024 * 1024)
    baseline = _cache_bytes()
    errors: list[BaseException] = []

    def fetch() -> None:
        from huggingface_hub import hf_hub_download

        try:
            for _, repo_id, filename in pending:
                hf_hub_download(
                    repo_id=repo_id,
                    filename=filename,
                    cache_dir=str(config.MODEL_DIR),
                )
        except BaseException as exc:  # surfaced on the worker thread below
            errors.append(exc)

    worker = threading.Thread(target=fetch, name="weights-download", daemon=True)
    worker.start()
    while worker.is_alive():
        if should_cancel and should_cancel():
            log.info("download abandoned after cancel request")
            raise Canceled("任务已取消")
        if on_progress:
            on_progress(min(0.99, max(0.0, (_cache_bytes() - baseline) / expected)))
        worker.join(0.6)

    if errors:
        raise SeparationError(friendly_error(errors[0]))
    if on_progress:
        on_progress(1.0)


def files_for(tier: config.ModelTier) -> list[tuple[str, str, str]]:
    """``[(label, repo_id, filename)]`` for one tier."""
    return required_files(tier.model, tier.stems)


def _plan(tiers: list[config.ModelTier]) -> list[tuple[str, str, str, str]]:
    seen: set[str] = set()
    plan: list[tuple[str, str, str, str]] = []
    for tier in tiers:
        for label, repo, filename in files_for(tier):
            if filename in seen:
                continue
            seen.add(filename)
            plan.append((tier.key, label, repo, filename))
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.weights",
        description="预下载 PitchMorph 的 ONNX 权重",
    )
    parser.add_argument(
        "tier",
        nargs="?",
        default=config.DEFAULT_TIER,
        choices=[*config.TIERS, "all"],
        help=f"档位（默认 {config.DEFAULT_TIER}）",
    )
    parser.add_argument("--list", action="store_true", help="只列出需要哪些文件")
    args = parser.parse_args(argv)

    tiers = list(config.TIERS.values()) if args.tier == "all" else [config.TIERS[args.tier]]
    plan = _plan(tiers)

    if args.list:
        print(f"{'tier':<10}{'stem':<10}file")
        for tier_key, label, repo, filename in plan:
            print(f"{tier_key:<10}{label:<10}{repo}/{filename}")
        return 0

    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co")
    print(f"权重目录 : {config.MODEL_DIR}")
    print(f"下载源   : {endpoint}")
    print(f"待处理   : {len(plan)} 个文件\n")

    from huggingface_hub import hf_hub_download

    failures = 0
    for index, (_, label, repo, filename) in enumerate(plan, start=1):
        print(f"[{index}/{len(plan)}] {label} ← {repo}")
        try:
            path = hf_hub_download(
                repo_id=repo,
                filename=filename,
                cache_dir=str(config.MODEL_DIR),
            )
        except KeyboardInterrupt:
            print("\n已中断。已下载的部分会保留，重跑本命令可续传。")
            return 130
        except Exception as exc:
            failures += 1
            print(f"    失败：{exc}", file=sys.stderr)
            continue
        print(f"    完成 → {path}\n")

    if failures:
        print(
            f"{failures} 个文件失败。可尝试：\n"
            "  $env:HF_HUB_DISABLE_XET = \"1\"\n"
            "  $env:HF_ENDPOINT = \"https://hf-mirror.com\"\n"
            "然后重跑本命令（已完成的文件会跳过）。",
            file=sys.stderr,
        )
        return 1

    ok = all(weights_cached(tier.model, tier.stems) for tier in tiers)
    print("全部完成，权重自检通过。" if ok else "下载结束，但自检未通过，请检查上方的错误输出。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
