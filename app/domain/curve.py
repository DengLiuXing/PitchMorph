"""音高曲线的纯函数层：FCPE 输出的逐帧 F0 → 可往返的曲线对象与音分工具。

约定（与全项目一致）：
- `None` 表示无声，**绝不用 0 Hz**：0 Hz 在前端是合法低音，会画出贴着底边的假线。
- 音分用 `1200*log2(f/440)`，八度等距，方便做 ±1200 音分的跳变修正。
- 本模块不 import torch、不做磁盘 IO，纯函数：同样输入同样输出。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# A4 = 440 Hz = MIDI 69，作为音分与 MIDI 号的换算锚点
REF_HZ = 440.0
MIDIS_A4 = 69

# 八度 = 1200 音分；半音 = 100 音分
CENTS_PER_OCTAVE = 1200.0


@dataclass(frozen=True)
class PitchCurve:
    """逐帧基频曲线。`f0[i]` 对应第 i 帧，帧 i 的时间是 `i * hop_sec` 秒。"""

    hop_sec: float
    f0: tuple[float | None, ...]

    @property
    def frames(self) -> int:
        return len(self.f0)

    @property
    def duration_sec(self) -> float:
        """曲线覆盖的时长。帧数 × 帧间隔，最后一帧之后还留一帧长。"""
        return self.frames * self.hop_sec

    @classmethod
    def from_payload(cls, payload: dict) -> "PitchCurve":
        """解析上游 `features/pitch.json`。

        校验放在入口：缺字段、`frames` 与 `f0` 长度不符、出现负频率都在这里抛
        `ValueError`，避免错误一路漏到算法层才炸。
        """
        if not isinstance(payload, dict):
            raise ValueError("音高曲线载荷必须是对象")

        for key in ("hop_sec", "frames", "f0"):
            if key not in payload:
                raise ValueError(f"音高曲线载荷缺少字段：{key}")

        hop_sec = payload["hop_sec"]
        frames = payload["frames"]
        raw_f0 = payload["f0"]

        if not isinstance(hop_sec, (int, float)) or isinstance(hop_sec, bool):
            raise ValueError("hop_sec 必须是数字")
        if hop_sec <= 0:
            raise ValueError("hop_sec 必须为正数")
        if not isinstance(frames, int) or isinstance(frames, bool):
            raise ValueError("frames 必须是整数")
        if not isinstance(raw_f0, (list, tuple)):
            raise ValueError("f0 必须是数组")

        if frames != len(raw_f0):
            raise ValueError(f"frames({frames}) 与 f0 长度({len(raw_f0)}) 不一致")

        curve: list[float | None] = []
        for index, value in enumerate(raw_f0):
            if value is None:
                curve.append(None)
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"f0[{index}] 必须是数字或 null")
            number = float(value)
            if not math.isfinite(number):
                raise ValueError(f"f0[{index}] 必须是有限数字")
            if number < 0:
                raise ValueError(f"f0[{index}] 不能为负数：{number}")
            curve.append(number)

        return cls(hop_sec=float(hop_sec), f0=tuple(curve))

    def to_payload(self, *, model: str = "fcpe", sample_rate: int = 16000) -> dict:
        """输出与上游一致的结构，保证 `payload → 曲线 → payload` 可往返。"""
        return {
            "version": 1,
            "model": model,
            "sample_rate": sample_rate,
            "hop_sec": self.hop_sec,
            "frames": self.frames,
            "f0": list(self.f0),
        }


def to_cents(f0: tuple[float | None, ...], ref_hz: float = REF_HZ) -> tuple[float | None, ...]:
    """Hz → 音分。`1200*log2(f/ref)`，八度等距；`None` 保持 `None`。

    用音分而不是 Hz 做阈值判断，因为人对音高的感知是对数尺度的。
    """
    if ref_hz <= 0:
        raise ValueError("ref_hz 必须为正数")
    cents: list[float | None] = []
    for value in f0:
        if value is None:
            cents.append(None)
            continue
        if value <= 0:
            cents.append(None)
            continue
        cents.append(CENTS_PER_OCTAVE * math.log2(value / ref_hz))
    return tuple(cents)


def median_smooth(values: tuple[float | None, ...], window: int = 3) -> tuple[float | None, ...]:
    """中位数滤波：抹掉孤立尖峰（八度误判、爆破音），保住真实的长音阶。

    `None` 不参与中位数计算；窗口内全是 `None` 时结果仍是 `None`。
    """
    if window < 1 or window % 2 == 0:
        raise ValueError("window 必须是 >=1 的奇数")

    half = window // 2
    smoothed: list[float | None] = []
    for index in range(len(values)):
        lo = max(0, index - half)
        hi = min(len(values), index + half + 1)
        chunk = [v for v in values[lo:hi] if v is not None]
        if not chunk:
            smoothed.append(None)
            continue
        chunk.sort()
        mid = len(chunk) // 2
        if len(chunk) % 2 == 1:
            smoothed.append(chunk[mid])
        else:
            smoothed.append((chunk[mid - 1] + chunk[mid]) / 2.0)
    return tuple(smoothed)


def fix_octave_jumps(
    f0: tuple[float | None, ...],
    *,
    jump_cents: float = 600.0,
    window: int = 5,
) -> tuple[float | None, ...]:
    """修正孤立的八度误判（OCTC 跳变）。

    只动「孤立点」：该帧与前一个有声帧的音分差超过阈值，而下一个有声帧又回到了
    之前的水平，说明这一帧是模型跳错了一个八度，把它拉回 ±1200 音分的邻域。
    连续的真实八度（比如副歌突然翻高八度）前后不一致，因此不会被改动。
    """
    if window < 1:
        raise ValueError("window 必须 >=1")

    cents = to_cents(f0)
    result: list[float | None] = list(f0)

    # 相邻的有声帧索引，方便找「前一个」和「后一个」
    voiced = [i for i, c in enumerate(cents) if c is not None]
    if len(voiced) < 3:
        return tuple(result)

    for pos in range(1, len(voiced) - 1):
        index = voiced[pos]
        prev_index = voiced[pos - 1]
        next_index = voiced[pos + 1]
        # 离前后有声帧太远就不认为是「孤立点」
        if index - prev_index > window or next_index - index > window:
            continue

        base = cents[prev_index]
        assert base is not None
        after = cents[next_index]
        assert after is not None

        delta = cents[index] - base
        assert delta is not None
        # 不是够大的跳变，不管
        if abs(delta) <= jump_cents:
            continue
        # 后面的帧没回到原位 → 是真实跳变，不动
        if abs(after - base) > jump_cents / 2.0:
            continue

        # 跳变里最接近的「整八度」份数：只有八度误判才会这么整
        octaves = round(delta / CENTS_PER_OCTAVE)
        if octaves == 0:
            continue
        residual = delta - octaves * CENTS_PER_OCTAVE
        # 减去八度后仍差很多 → 不是八度误判（比如滑音、快速经过的装饰音），不动
        if abs(residual) > jump_cents / 2.0:
            continue

        # 去掉八度误判，保留真正的音分偏差
        fixed_cents = base + residual
        result[index] = REF_HZ * (2.0 ** (fixed_cents / CENTS_PER_OCTAVE))
    return tuple(result)
