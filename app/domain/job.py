"""Job 与状态机。非法转移直接抛错，别让「已取消的任务又变成成功」这种状态被写出来。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

from app.domain.artifact import Artifact

Kind = Literal["separate", "pitch", "notes", "export"]
State = Literal["queued", "running", "succeeded", "failed", "cancelled"]

_ALLOWED: dict[str, set[str]] = {
    "queued": {"running", "cancelled"},
    "running": {"succeeded", "failed", "cancelled"},
    "succeeded": set(),
    "failed": set(),
    "cancelled": set(),
}


class IllegalTransition(RuntimeError):
    pass


@dataclass
class Job:
    id: str
    kind: Kind
    params: dict
    parent: str | None = None
    target: str | None = None
    state: State = "queued"
    warnings: list[str] = field(default_factory=list)
    error: dict | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    progress: float = 0.0

    def transition(self, state: State) -> None:
        if state not in _ALLOWED[self.state]:
            raise IllegalTransition(f"{self.kind} {self.id}: {self.state} -> {state}")
        self.state = state
        self.touch()

    def touch(self) -> None:
        self.updated_at = time.time()

    def warn(self, code: str) -> None:
        if code not in self.warnings:
            self.warnings.append(code)

    def fail(self, code: str, message: str) -> None:
        self.error = {"code": code, "message": message}
        if self.state in ("queued", "running"):
            self.transition("failed")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "parent": self.parent,
            "target": self.target,
            "params": self.params,
            "state": self.state,
            "warnings": self.warnings,
            "error": self.error,
            "progress": round(self.progress, 4),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "artifacts": [a.to_dict() for a in self.artifacts],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        job = cls(
            id=data["id"],
            kind=data["kind"],
            params=data.get("params") or {},
            parent=data.get("parent"),
            target=data.get("target"),
            state=data.get("state", "queued"),
            warnings=list(data.get("warnings") or []),
            error=data.get("error"),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            progress=data.get("progress", 0.0),
        )
        job.artifacts = [Artifact.from_dict(a) for a in data.get("artifacts") or []]
        return job
