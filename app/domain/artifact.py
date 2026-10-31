"""Artifact：产物必须能回答「这个文件是谁算的」。字段定义见 docs/09-contract.md §2。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

Role = Literal["stem", "pitch", "notes", "midi", "ustx"]


@dataclass(frozen=True)
class Artifact:
    path: str
    role: Role
    model: str
    ep: str
    params_hash: str
    source_job: str | None = None
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Artifact":
        return cls(
            path=data["path"],
            role=data["role"],
            model=data["model"],
            ep=data["ep"],
            params_hash=data["params_hash"],
            source_job=data.get("source_job"),
            meta=data.get("meta") or {},
        )
