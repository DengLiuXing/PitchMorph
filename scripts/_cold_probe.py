"""Throwaway: what does dropping residency actually cost per stem?"""

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import runtime, weights  # noqa: E402

from demucs_onnx import session_pool  # noqa: E402
from demucs_onnx.inference import N_SAMPLES  # noqa: E402

path = weights.resolve_paths("htdemucs_ft", ("vocals",))["vocals"]
mix = np.zeros((1, 2, N_SAMPLES), dtype=np.float32)
print("providers:", runtime.providers())

for round_index in range(3):
    started = time.perf_counter()
    session = runtime.ensure_session(path)
    built = time.perf_counter() - started

    started = time.perf_counter()
    session.run(["stems"], {"mix": mix})
    ran = time.perf_counter() - started

    print(f"round {round_index}: build {built:5.1f}s  first chunk {ran:5.1f}s  providers={session.get_providers()[0]}")
    session_pool().clear()
