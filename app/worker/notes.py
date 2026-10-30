"""音符阶段：用 GAME 的 encoder + segmenter 取音符边界，写 `features/notes_game.json`。

**只借边界，不借音高**（`docs/05-game.md` M1）：每段音高仍由 FCPE 的中位数决定，
所以这个文件里只有 `start_sec` / `dur_sec`。

边界为什么不由 GAME 自己写成 MIDI：MIDI 会把边界量化到 tick（120 bpm 下 1 tick ≈ 2 ms），
再反解回帧就是二次误差。这里在 callback 里直接收秒值，不落 MIDI。

上游 `infer_model` 走 Lightning，一次 `Predicting` 就是全部片段；这个阶段的进度曲线
没有块级信息，只报「开始 / 完成」两个点，剩下的交给上层的时间估计。
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from app import config, protocol


def _model_path(request: dict) -> Path:
    """请求里的 `model` 可以是体积名（small / medium / large），也可以是 .pt 路径。"""
    explicit = request.get("model")
    if explicit and str(explicit) in config.GAME_SIZES:
        found = config.game_model_path(str(explicit))
        if found is None:
            raise FileNotFoundError(
                f"没有 {explicit} 体积的权重：先跑 scripts/fetch_game.py --model {explicit}"
            )
        return found
    if explicit:
        return Path(str(explicit))
    found = config.game_model_path()
    if found is None:
        raise FileNotFoundError(
            f"找不到 GAME 权重。按 {config.ROOT / 'app' / 'worker' / 'game' / 'VENDOR.md'} 下载，"
            f"解包到 {config.GAME_WEIGHTS_DIR / config.GAME_DEFAULT_MODEL}/"
        )
    return found


class _CappedSlicer:
    """在上游 Slicer 之上加一条硬约束：单片不超过 `max_sec`。

    segmenter 的注意力是 O(T²)：188 s 整段要 5.6 GB，直接 OOM。上游只按静音切，
    连续演唱的长歌可能切不出短片（实测 188 s 素材在 -40 dB 下最长片已 45.8 s），
    所以这里兜底，并且**在能量最低处切**——按固定时长切会把长音从中间劈开。
    """

    def __init__(self, slicer, *, sr: int, max_sec: float) -> None:
        self._slicer = slicer
        # 上游 dataset 会直接读 `slicer.sr`，所以这里必须暴露同名字段
        self.sr = sr
        self._sr = sr
        self._max = max(sr, int(max_sec * sr))

    def slice(self, waveform):  # noqa: ANN001, ANN201  与上游 Slicer.slice 同签名
        pieces = []
        for chunk in self._slicer.slice(waveform):
            pieces.extend(self._split(chunk))
        return pieces

    def _split(self, chunk: dict) -> list[dict]:
        data = chunk["waveform"]
        if len(data) <= self._max:
            return [chunk]
        pieces: list[dict] = []
        start = 0
        while len(data) - start > self._max:
            # 只在窗口后 1/4 找切点：越靠后越少引入额外片段，同时留足上下文
            search_from = start + int(self._max * 0.75)
            cut = self._quietest_cut(data, search_from, start + self._max)
            pieces.append(
                {
                    "waveform": data[start:cut],
                    "offset": float(chunk["offset"]) + start / self._sr,
                }
            )
            start = cut
        pieces.append(
            {
                "waveform": data[start:],
                "offset": float(chunk["offset"]) + start / self._sr,
            }
        )
        return pieces

    def _quietest_cut(self, data, search_from: int, search_to: int) -> int:
        """在 [search_from, search_to) 里找 10 ms 内能量最低的帧边界。"""
        import numpy as np

        step = max(1, self._sr // 100)
        best_index, best_energy = search_to, None
        for index in range(search_from, max(search_from + 1, search_to - step), step):
            energy = float(np.abs(data[index : index + step]).mean())
            if best_energy is None or energy < best_energy:
                best_energy, best_index = energy, index
        return int(best_index)


def _write_json(path: Path, payload: dict) -> None:
    """先 `.part` 再 `os.replace`：取消时不会留下半个 JSON 让导出层读崩。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def run(request: dict[str, Any]) -> dict[str, str]:
    import app.worker.game  # noqa: F401  把 vendored 上游代码放进 sys.path

    import lightning.pytorch.callbacks
    from inference.api import infer_model, load_inference_model
    from inference.callbacks import SaveCombinedFileCallback
    from inference.data import SlicedAudioFileIterableDataset
    from inference.slicer2 import Slicer
    from lib.config.schema import ValidationConfig

    source = Path(request["input"])
    out = Path(request["out"])
    model_path = _model_path(request)

    protocol.progress(0.05, "加载 GAME")
    # 上游会往 stdout 打印配置（`format_model`）和 tqdm 进度条，直接跑会污染协议通道。
    # 推理期间把 stdout 整个换成 stderr；协议事件只在块外发，因此不受影响。
    with contextlib.redirect_stdout(sys.stderr):
        model, lang_map = load_inference_model(model_path)

    class _Collect(SaveCombinedFileCallback):
        """不写文件，只把每个片段的结果收起来。父类负责排序与去重叠。"""

        def __init__(self) -> None:
            super().__init__(output_dir=out.parent)
            self.segments: list[tuple[float, float, float]] = []

        def flush(self, key, notes, logger_fn) -> None:  # noqa: ANN001
            # note.pitch 是 estimator 给的浮点 MIDI。M1 只借边界时它被丢弃，
            # 但实测 FCPE 段内中位数的音高一致率比它低 14 个点，所以一并带上。
            self.segments.extend((note.onset, note.offset, note.pitch) for note in notes)

    collector = _Collect()

    language = request.get("language")
    if language:
        if not lang_map or language not in lang_map:
            raise ValueError(f"该权重不支持语言 {language!r}，可选 {sorted(lang_map or {})}")
        language_id = lang_map[language]
    else:
        language_id = 0

    sr = model.inference_config.features.audio_sample_rate
    threshold = float(request.get("slice_threshold", config.GAME_SLICE_THRESHOLD_DB))
    max_slice = float(request.get("max_slice_sec", config.GAME_MAX_SLICE_SEC))
    slicer = _CappedSlicer(
        Slicer(sr=sr, threshold=threshold, min_length=1000, min_interval=200, max_sil_kept=100),
        sr=sr,
        max_sec=max_slice,
    )
    dataset = SlicedAudioFileIterableDataset(
        filemap={source.name: source},
        samplerate=sr,
        slicer=slicer,
        language=language_id,
    )

    protocol.progress(0.15, "GAME 推理")
    with contextlib.redirect_stdout(sys.stderr):
        infer_model(
            model=model,
            dataset=dataset,
            config=ValidationConfig(
                d3pm_sample_steps=int(request.get("nsteps", 8)),
                boundary_decoding_threshold=float(request.get("seg_threshold", 0.2)),
                boundary_decoding_radius=round(float(request.get("seg_radius", 0.02)) / model.timestep),
                note_presence_threshold=float(request.get("est_threshold", 0.2)),
            ),
            callbacks=[collector],
            batch_size=1,
            num_workers=0,
            precision="32-true",
        )

    segments = [
        {
            "start_sec": round(onset, 4),
            "dur_sec": round(offset - onset, 4),
            "midi": round(pitch, 3),
        }
        for onset, offset, pitch in collector.segments
        if offset > onset
    ]
    _write_json(
        out,
        {
            "version": 1,
            "model": model_path.parent.name,
            "hop_sec": model.timestep,
            "source": source.name,
            "segments": segments,
        },
    )
    protocol.progress(0.92, f"{len(segments)} 个音符边界")
    return {"notes_game": out.relative_to(out.parent.parent).as_posix()}
