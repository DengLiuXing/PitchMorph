"""ONNX Runtime execution providers: a real CUDA probe with CPU fallback.

``ort.get_available_providers()`` lists ``CUDAExecutionProvider`` whenever the
GPU wheel is installed — even on a machine with no CUDA DLLs at all. The
failure then surfaces much later, as an opaque DLL error while building the
first session, which is exactly the trap the old ``config.providers()``
docstring warned about.

So availability is not evidence. We preload the wheel's CUDA/cuDNN libraries,
build a 92-byte Identity graph on the CUDA EP and run it once. Only a graph
that allocates and executes on the GPU counts as usable; anything else keeps
the CPU provider and records a reason for the UI.

Sessions live here rather than in ``demucs_onnx.SessionPool`` for one reason:
that pool keys on ``(path, "", tuple(providers))`` — provider *names*, not
options — so there is no way to pass ``arena_extend_strategy`` through it. On
CUDA the default arena strategy doubles its reservation on every growth, which
on an 8 GB card turns one specialist into 3+ GB and four into an OOM. Owning
the cache here buys the place to pass those options.

Sessions are **not** kept resident between batches: a grown arena is only
returned when the session object dies, and a rebuild costs ~4 s. See
``separator._release_sessions``.
"""

from __future__ import annotations

import logging
import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger("pitchmorph.runtime")

CUDA = "CUDAExecutionProvider"
CPU = "CPUExecutionProvider"

#: Guards the session cache so parallel stems never duplicate a build.
_session_lock = threading.Lock()

#: ``{path: InferenceSession}`` for the current batch; emptied by release_sessions().
_SESSIONS: dict[str, Any] = {}

#: Defaults chosen for a single model on a laptop GPU, not for a long-lived server.
_CUDA_DEFAULTS: dict[str, str] = {
    # Grow the arena by exactly what was asked for. The ORT default
    # (kNextPowerOfTwo) rounds every request up to the next power of two and
    # never shrinks, which is how one specialist reaches 3 GB and the second
    # chunk doubles it again.
    "arena_extend_strategy": "kSameAsRequested",
    # Take cuDNN's recommended algorithm instead of benchmarking every one of
    # them. EXHAUSTIVE keeps the largest workspace it tries, so the peak follows
    # the worst candidate rather than the one actually used.
    "cudnn_conv_algo_search": "HEURISTIC",
}

#: Handles returned by ``os.add_dll_directory``; dropping them unhooks the dir.
_DLL_HANDLES: list[Any] = []


def _register_windows_dll_dirs() -> None:
    """Make a plain CUDA Toolkit install visible to the DLL loader.

    Python 3.8+ dropped PATH from the DLL search path, so a Toolkit whose
    ``bin\\x64`` is not on PATH still fails to load even though the files are
    right there. Registering the directory explicitly is what makes
    ``scripts/setup.py`` able to report GPU without editing system PATH.

    CUDA 13 puts the DLLs in ``bin\\x64``; older layouts use ``bin``.
    """
    if os.name != "nt":
        return

    roots: list[Path] = []
    env = os.environ.get("CUDA_PATH")
    if env:
        roots.append(Path(env))
    toolkit = Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA")
    if toolkit.is_dir():
        try:
            roots.extend(sorted((item for item in toolkit.iterdir() if item.is_dir()), reverse=True))
        except OSError:
            pass

    for root in roots:
        for sub in ("bin/x64", "bin"):
            candidate = root / sub
            if not candidate.is_dir():
                continue
            try:
                _DLL_HANDLES.append(os.add_dll_directory(str(candidate)))
            except OSError:
                continue


_register_windows_dll_dirs()


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


def _explicit() -> list[str] | None:
    """``PITCHMORPH_PROVIDERS`` when set, else ``None``.

    An explicit list always wins: it is the escape hatch for forcing CUDA
    without a probe, and for forcing CPU on a machine that would otherwise
    pick the GPU.
    """
    raw = os.environ.get("PITCHMORPH_PROVIDERS", "").strip()
    if not raw:
        return None
    return [entry.strip() for entry in raw.split(",") if entry.strip()]


def _brief(exc: BaseException) -> str:
    """First line of an ORT error, short enough for a tooltip."""
    text = str(exc).strip() or exc.__class__.__name__
    first = text.splitlines()[0].strip()
    return first[:200]


@lru_cache(maxsize=1)
def gpu_status() -> tuple[bool, str | None]:
    """``(cuda_usable, reason)``. Probed once per process."""
    import onnxruntime as ort

    available = list(ort.get_available_providers())
    if CUDA not in available:
        return False, (
            "当前安装的是 onnxruntime CPU 版，未包含 CUDA 执行器。"
            "运行 scripts/setup.py 可切换到 GPU 版。"
        )

    preload = getattr(ort, "preload_dlls", None)
    if preload is not None:
        try:
            preload(cuda=True, cudnn=True)
        except Exception as exc:
            return False, f"CUDA 运行库加载失败：{_brief(exc)}"

    absent = _missing_cudnn()
    if absent:
        return False, absent

    probe = _probe_cuda(ort)
    if probe is None:
        return True, None
    return False, probe


def _missing_cudnn() -> str | None:
    """Check cuDNN separately: the Identity probe never touches a convolution.

    A machine with the CUDA runtime but no cuDNN will happily build and run the
    Identity graph, so ``_probe_cuda`` alone reports CUDA as usable — and then
    the first real model dies inside its first Conv. That is exactly what
    happened when ``uv sync`` pruned a hand-installed ``nvidia-cudnn-cu13``.

    Only meaningful on Windows: Linux gets cuDNN from the identical wheel set,
    and its shared object name differs.
    """
    if os.name != "nt":
        return None
    import ctypes

    try:
        ctypes.WinDLL("cudnn64_9.dll")
    except OSError:
        return (
            "缺少 cuDNN 9（cudnn64_9.dll）。真实模型的卷积层需要它，"
            "而最小探针图不需要，所以这一步单独检查。"
            "运行 uv sync --extra gpu --no-install-package onnxruntime 可装上。"
        )
    return None


def _probe_cuda(ort: Any) -> str | None:
    """Run one tiny Identity graph on CUDA. Returns a failure reason or None."""
    import numpy as np

    try:
        options = ort.SessionOptions()
        options.log_severity_level = 3
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        session = ort.InferenceSession(
            _PROBE_MODEL, sess_options=options, providers=[CUDA],
        )
        if CUDA not in session.get_providers():
            return "CUDA 执行器未能注册，ONNX Runtime 已静默回落到 CPU"
        session.run(None, {"x": np.ones((1,), dtype=np.float32)})
    except Exception as exc:
        return f"CUDA 推理不可用：{_brief(exc)}"
    return None


def providers() -> list[str]:
    """Execution providers for this process: explicit → probe → CPU."""
    explicit = _explicit()
    if explicit:
        return explicit
    usable, _ = gpu_status()
    return [CUDA, CPU] if usable else [CPU]


def using_cuda() -> bool:
    """Whether inference will actually land on the GPU right now."""
    return CUDA in providers()


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def _cuda_options() -> dict[str, str]:
    """Defaults merged with ``PITCHMORPH_CUDA_OPTS`` (``k=v,k=v``).

    The escape hatch exists so an A/B run of e.g.
    ``cudnn_conv_use_max_workspace=0`` costs an environment line instead of a
    code edit. Malformed entries are dropped with a warning: a typo in a shell
    variable must not fail an otherwise healthy job.
    """
    options = dict(_CUDA_DEFAULTS)
    raw = os.environ.get("PITCHMORPH_CUDA_OPTS", "").strip()
    if not raw:
        return options
    for entry in raw.split(","):
        key, separator, value = entry.strip().partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not key or not value:
            log.warning("ignoring malformed PITCHMORPH_CUDA_OPTS entry: %r", entry)
            continue
        options[key] = value
    return options


def _provider_specs() -> list[Any]:
    """``providers()`` with CUDA's options attached.

    ORT accepts ``[(name, options), name]``; the plain-string shape is what
    ``using_cuda()`` and the health payload want, so both stay available.
    """
    names = providers()
    if CUDA not in names:
        return list(names)
    options = _cuda_options()
    return [(name, options) if name == CUDA else name for name in names]


def _preload_cuda() -> None:
    """Load the wheel's CUDA/cuDNN DLLs before the first real session.

    ``gpu_status()`` normally does this, but ``PITCHMORPH_PROVIDERS=cuda``
    bypasses that probe entirely — without this call the DLLs would never be
    loaded and the first real model would die on an opaque load error.
    ``preload_dlls`` is idempotent, so calling it per build is harmless.
    """
    import onnxruntime as ort

    preload = getattr(ort, "preload_dlls", None)
    if preload is None:
        return
    try:
        preload(cuda=True, cudnn=True)
    except Exception as exc:  # the build below reports the real failure
        log.warning("CUDA DLL preload failed: %s", _brief(exc))


def _build_session(path: Path) -> Any:
    import onnxruntime as ort

    specs = _provider_specs()
    if CUDA in providers():
        _preload_cuda()
        log.info(
            "building %s on CUDA (%s)",
            Path(path).name,
            ", ".join(f"{key}={value}" for key, value in _cuda_options().items()),
        )
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    # Off on purpose. Every chunk has the identical input shape, which is exactly
    # the case memory patterns are meant to help — but the pattern reserves a
    # distinct block for every intermediate, including tensors whose lifetimes do
    # not overlap. Measured on the vocals specialist: with the ORT default the
    # footprint goes 3.2 GB after chunk 1 and 5.7 GB after chunk 2, then stays
    # there; disabled it stays at 3.2 GB from the first chunk on.
    options.enable_mem_pattern = False
    return ort.InferenceSession(str(path), sess_options=options, providers=specs)


def ensure_session(path: Path) -> Any:
    """Session for ``path``, built at most once until :func:`release_sessions`."""
    key = str(path)
    with _session_lock:
        session = _SESSIONS.get(key)
        if session is None:
            session = _build_session(path)
            _SESSIONS[key] = session
        return session


def release_sessions() -> None:
    """Drop every cached session so its arena goes back to the driver.

    The caller must drop its own references first: clearing this dict only
    removes the cache's hold, and a local ``{stem: session}`` would keep the
    session — with several GB of VRAM — alive into the next batch.
    """
    with _session_lock:
        _SESSIONS.clear()


def session_options_summary() -> dict[str, str]:
    """Effective CUDA options for the health payload; empty when on CPU."""
    return _cuda_options() if CUDA in providers() else {}


def reset_cache() -> None:
    """Drop the cached probe. Tests only."""
    gpu_status.cache_clear()


# ---------------------------------------------------------------------------
# Minimal ONNX graph used as the probe payload
# ---------------------------------------------------------------------------


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        chunk = value & 0x7F
        value >>= 7
        if value:
            out.append(chunk | 0x80)
        else:
            out.append(chunk)
            return bytes(out)


def _len_field(number: int, payload: bytes) -> bytes:
    return _varint((number << 3) | 2) + _varint(len(payload)) + payload


def _varint_field(number: int, value: int) -> bytes:
    return _varint((number << 3) | 0) + _varint(value)


def _identity_model() -> bytes:
    """A one-node ``Identity`` graph, hand-encoded.

    Built from protobuf primitives because ``onnx`` is not a dependency of
    this project (and pulling it in just for a probe would be absurd). 150
    bytes is enough for ORT to allocate on the target device and execute.
    """
    dimension = _len_field(1, _varint_field(1, 1))  # TensorShapeProto.dim[0].dim_value = 1
    tensor_type = _varint_field(1, 1) + _len_field(2, dimension)  # elem_type = FLOAT
    type_proto = _len_field(1, tensor_type)  # TypeProto.tensor_type

    def value_info(name: bytes) -> bytes:
        return _len_field(1, name) + _len_field(2, type_proto)

    node = (
        _len_field(1, b"x")
        + _len_field(2, b"y")
        + _len_field(3, b"probe")
        + _len_field(4, b"Identity")
    )
    graph = (
        _len_field(1, node)
        + _len_field(2, b"probe_graph")
        + _len_field(11, value_info(b"x"))
        + _len_field(12, value_info(b"y"))
    )
    opset = _varint_field(2, 13)
    return (
        _varint_field(1, 7)  # ir_version
        + _len_field(2, b"pitchmorph")
        + _len_field(7, graph)
        + _len_field(8, opset)
    )


_PROBE_MODEL = _identity_model()
