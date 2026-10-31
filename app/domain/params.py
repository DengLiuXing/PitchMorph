"""参数三分类与校验（docs/09-contract.md §3）。

工程安全参数用实测定，听感参数必须盲听定，显示参数不进产物——所以显示参数不在这里。
"""

from __future__ import annotations

import hashlib
import json

# category: safe（工程安全，实测可定）/ taste（听感，必须盲听）
SPECS: dict[str, dict] = {
    "tier": {"cat": "taste", "type": "enum", "values": ["vocals", "four"], "default": "vocals"},
    "slice_max_sec": {"cat": "safe", "type": "number", "min": 5, "max": 60, "default": 30},
    "onset_threshold_db": {"cat": "taste", "type": "number", "min": -70, "max": -20, "default": -40},
    "f0_min": {"cat": "safe", "type": "number", "min": 40, "max": 200, "default": 80},
    "f0_max": {"cat": "safe", "type": "number", "min": 400, "max": 2000, "default": 880},
    "bpm": {"cat": "taste", "type": "number", "min": 40, "max": 240, "default": 120},
    "export_smooth": {"cat": "taste", "type": "number", "min": 0, "max": 5, "default": 0},
}

# 每种 job 认哪些参数；不在表里的键直接报错，避免拼写错误被静默忽略
KIND_PARAMS: dict[str, tuple[str, ...]] = {
    "separate": ("tier",),
    "pitch": ("f0_min", "f0_max"),
    "notes": ("slice_max_sec", "onset_threshold_db"),
    "export": ("bpm", "export_smooth"),
}


def schema() -> dict:
    """给前端表单用：唯一来源，别在 Vue/React 里再写一份默认值。"""
    return {
        "specs": SPECS,
        "kinds": {kind: [k for k in keys] for kind, keys in KIND_PARAMS.items()},
    }


def normalize(kind: str, raw: dict | None) -> dict:
    if kind not in KIND_PARAMS:
        raise ValueError(f"unknown kind: {kind}")
    allowed = KIND_PARAMS[kind]
    unknown = set(raw or {}) - set(allowed)
    if unknown:
        raise ValueError(f"{kind} 不接受参数: {sorted(unknown)}")
    out = {}
    for key in allowed:
        spec = SPECS[key]
        value = (raw or {}).get(key, spec["default"])
        if spec["type"] == "enum":
            if value not in spec["values"]:
                raise ValueError(f"{key} 只能是 {spec['values']}")
        else:
            value = float(value)
            if not spec["min"] <= value <= spec["max"]:
                raise ValueError(f"{key} 超出范围 [{spec['min']}, {spec['max']}]")
        out[key] = value
    return out


def fingerprint(params: dict) -> str:
    blob = json.dumps(params, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()[:12]
