"""诊断本机能不能跑 CUDA，并（可选）执行一次 uv sync。

    uv run python scripts/setup.py            # 只诊断，打印结论与建议
    uv run python scripts/setup.py --apply    # 诊断后跑一遍 uv sync

两次探测分工不同：这里查的是**系统层**——驱动 UMD 版本、CUDA Toolkit 里的
cudart / cublas / cufft / curand / nvrtc、cuDNN 9，这些在 Windows 上没法靠 pip
装；`app/runtime.py` 查的是**运行层**——能不能用 CUDA EP 真建一个 session 并跑通。
两边都会在失败时给出可读原因。

onnxruntime-gpu 现在是 pyproject 的常规依赖，`onnxruntime` 那条被
`[tool.uv] override-dependencies` 改写成仅 macOS 生效，所以 --apply 只跑
`uv sync`，不再需要 --gpu / --cpu 之类的分支。

CUDA 需要 13.x（onnxruntime-gpu 1.31 就是按 CUDA 13 编译的），驱动 UMD 版本是上限。
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: onnxruntime-gpu 1.31.1 ships as a CUDA 13.0 / cuDNN 9 build.
MIN_CUDA = (13, 0)
MIN_VRAM_MB = 4096
GPU_DOWNLOAD_MB = 1400


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def probe_gpu() -> dict[str, object] | None:
    """System-level GPU facts, or None when there is no NVIDIA driver."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None

    query = _run(
        [exe, "--query-gpu=name,driver_version,memory.total,compute_cap", "--format=csv,noheader"],
    )
    if query.returncode != 0 or not query.stdout.strip():
        return None

    fields = [item.strip() for item in query.stdout.strip().splitlines()[0].split(",")]
    while len(fields) < 4:
        fields.append("")
    name, driver, vram, compute_cap = fields[:4]

    # The banner calls it "CUDA Version" on older drivers and "CUDA UMD
    # Version" from the 5xx series on; accept both.
    banner = _run([exe])
    text = (banner.stdout or "") + (banner.stderr or "")
    match = re.search(r"CUDA (?:UMD )?Version:\s*(\d+)\.(\d+)", text)
    digits = re.sub(r"\D", "", vram)

    return {
        "name": name,
        "driver": driver,
        "vram_mb": int(digits) if digits else None,
        "compute_cap": compute_cap,
        "cuda": (int(match.group(1)), int(match.group(2))) if match else None,
    }


def _dll_dirs() -> list[Path]:
    """Where Windows would find CUDA DLLs: PATH, System32, the Toolkit bins.

    CUDA 13 moved the DLLs into ``bin\\x64`` / ``bin\\arm64`` so one installer
    can carry both architectures; older layouts kept everything in ``bin``.
    Both are searched.
    """
    dirs = [Path(entry) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    dirs.append(Path(r"C:\Windows\System32"))

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
        for sub in ("bin", "bin/x64"):
            candidate = root / sub
            if candidate.is_dir():
                dirs.append(candidate)
    return dirs


def find_system_dll(pattern: str) -> str | None:
    """First DLL matching ``pattern`` that the loader could reach."""
    for directory in _dll_dirs():
        if not directory.is_dir():
            continue
        try:
            hit = next(directory.glob(pattern), None)
        except OSError:
            continue
        if hit is not None:
            return hit.name
    return None


def _first_dll(patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        hit = find_system_dll(pattern)
        if hit:
            return hit
    return None


def _pip_cudnn() -> str | None:
    """cuDNN installed as a wheel, where ``preload_dlls(cudnn=True)`` finds it."""
    root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin"
    if not root.is_dir():
        return None
    hit = next(root.glob("cudnn64_9*.dll"), None)
    return hit.name if hit else None


#: Everything the CUDA EP links against, in the order it fails. The Toolkit
#: installs these as separate components, so selecting only "CUDA Runtime"
#: leaves four of them missing and the session never builds.
WINDOWS_CUDA_LIBS = (
    ("cublas64_13*.dll", "CuBLAS"),
    ("cufft64_*.dll", "CuFFT"),
    ("curand64_*.dll", "CuRAND"),
    ("nvrtc64_*.dll", "NVRTC"),
)


def windows_cuda_state() -> tuple[bool, bool, str]:
    """``(cuda_libs, cudnn, detail)`` for what ONNX Runtime needs on Windows.

    The CUDA runtime has to be system-installed: ``nvidia-cublas``, ``cufft``,
    ``curand`` and ``nvrtc`` publish manylinux wheels only. cuDNN is the lone
    exception — ``nvidia-cudnn-cu13`` does ship a ``win_amd64`` wheel, so it can
    come from pip and be picked up by ``preload_dlls(cudnn=True)``.

    Version matters for cudart. PhysX ships a CUDA 6.5 ``cudart64_65.dll`` on
    many gaming machines, and a bare ``cudart64_*.dll`` glob would happily
    mistake it for a usable runtime — ONNX Runtime 1.31 needs 13.x and would
    then fail at session build instead of here.
    """
    cudart = find_system_dll("cudart64_13*.dll")
    if not cudart:
        return False, False, "CUDA Toolkit 13.x（cudart64_13.dll）"

    missing = [label for pattern, label in WINDOWS_CUDA_LIBS if not find_system_dll(pattern)]
    cudnn = _first_dll(("cudnn64_9*.dll",)) or _pip_cudnn()
    if missing:
        return False, bool(cudnn), "、".join(missing)

    detail = f"{cudart} + {cudnn}" if cudnn else cudart
    return True, bool(cudnn), detail


def assess(info: dict[str, object] | None) -> tuple[bool, str]:
    """``(cuda_ready, reason)`` — a prediction, not a measurement.

    The real answer only comes from ``app.runtime.gpu_status()``, which builds
    and runs a session; this function just explains *why* a fallback is coming,
    before the server is started.
    """
    if info is None:
        return False, "未检测到 NVIDIA 驱动（nvidia-smi 不可用）"

    cuda = info["cuda"]
    if cuda is None:
        return False, "无法从驱动读出 CUDA 版本"
    if tuple(cuda) < MIN_CUDA:  # type: ignore[arg-type]
        major, minor = cuda  # type: ignore[misc]
        return False, f"驱动只支持 CUDA {major}.{minor}，onnxruntime-gpu 需要 ≥ 13.0"

    vram = info["vram_mb"]
    if isinstance(vram, int) and vram < MIN_VRAM_MB:
        return False, f"显存只有 {vram} MB，低于 {MIN_VRAM_MB} MB"

    major, minor = cuda  # type: ignore[misc]
    reason = f"驱动支持 CUDA {major}.{minor}"

    if platform.system() == "Windows":
        cuda_ok, cudnn_ok, detail = windows_cuda_state()
        if not cuda_ok:
            return False, (
                f"缺少 {detail}。Windows 的 CUDA 运行库不在 pip 里，"
                "需要装 CUDA Toolkit 13.x 并勾选 "
                "Runtime + CuBLAS + CuFFT + CuRAND + NVRTC"
            )
        reason += f"，已找到 {detail}"
        if not cudnn_ok:
            reason += "；cuDNN 缺失，跑一次 uv sync 会补上"

    return True, reason


def installed() -> dict[str, str] | None:
    """What the current environment actually has, if importable."""
    code = (
        "import onnxruntime as o;"
        "print(o.__version__);"
        "print(','.join(o.get_available_providers()))"
    )
    result = _run([sys.executable, "-c", code])
    if result.returncode != 0:
        return None
    lines = result.stdout.strip().splitlines()
    if not lines:
        return None
    return {
        "version": lines[0].strip(),
        "providers": lines[1].strip() if len(lines) > 1 else "",
    }


def describe(state: dict[str, str] | None) -> str:
    if state is None:
        return "未安装"
    kind = "GPU 版" if "CUDAExecutionProvider" in state["providers"] else "CPU 版"
    return f"{kind} {state['version']}"


def sync() -> int:
    """Make the environment match the lock file.

    ``onnxruntime-gpu`` is a regular dependency now, so a plain ``uv sync`` is
    the whole install — there is no CPU/GPU branch left to pick.
    """
    state = installed()
    if state is not None and "CUDAExecutionProvider" not in state["providers"]:
        # A leftover CPU wheel would delete the GPU wheel's DLLs on the way in:
        # both own the same module directory and uninstall by file manifest.
        print("· 清理残留的 CPU 版 onnxruntime")
        _run(["uv", "pip", "uninstall", "onnxruntime"], cwd=ROOT)

    print("· 执行 uv sync")
    proc = subprocess.run(["uv", "sync"], cwd=ROOT, check=False)
    if proc.returncode != 0:
        print("\n同步失败。")
        print("  国内网络可先换镜像：$env:UV_INDEX_URL = \"https://pypi.tuna.tsinghua.edu.cn/simple\"")
        return proc.returncode

    print("\n· 自检")
    check = _run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, '.');"
            "from app import runtime;"
            "ok, why = runtime.gpu_status();"
            "print('CUDA 可用' if ok else f'CPU 兜底：{why}')",
        ],
    )
    print(check.stdout.strip() or check.stderr.strip())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/setup.py",
        description="诊断本机 CUDA 是否可用，并按需同步依赖",
    )
    parser.add_argument("--apply", action="store_true", help="执行 uv sync（默认只诊断）")
    args = parser.parse_args(argv)

    if sys.prefix == sys.base_prefix:
        print("请在本项目的虚拟环境里运行：uv run python scripts/setup.py")
        return 2

    info = probe_gpu()
    ready, reason = assess(info)

    print("=== 本机环境 ===")
    if info is None:
        print("  GPU         : 未检测到 NVIDIA 驱动")
    else:
        cuda = info["cuda"]
        cuda_text = f"{cuda[0]}.{cuda[1]}" if cuda else "未知"  # type: ignore[index]
        print(f"  GPU         : {info['name']}")
        print(f"  驱动 / CUDA : {info['driver']} / {cuda_text}")
        print(f"  显存 / 算力 : {info['vram_mb']} MB / {info['compute_cap']}")
    print(f"  当前依赖    : {describe(installed())}")

    print("\n=== CUDA 判定 ===")
    print(f"  结论        : {'可用' if ready else '不可用，将回落 CPU'}")
    print(f"  依据        : {reason}")
    if ready:
        print("  说明        : 静态判断，启动时还会用最小图真探针一次")
    else:
        print("  说明        : 服务照常可用，只是推理走 CPU；装 CUDA Toolkit 13.x 后可启用")

    if not args.apply:
        print("\n只诊断，未改动环境。加 --apply 执行 uv sync。")
        return 0

    return sync()


if __name__ == "__main__":
    raise SystemExit(main())
