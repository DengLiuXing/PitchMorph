"""请求/响应模型，字段与 docs/09-contract.md 一一对应。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["separate", "pitch", "notes", "export"]


class CreateJob(BaseModel):
    kind: Kind
    parent: str | None = None
    target: str | None = None
    source: str | None = None
    params: dict = Field(default_factory=dict)


class ArtifactOut(BaseModel):
    path: str
    role: str
    model: str
    ep: str
    params_hash: str
    source_job: str | None = None


class JobOut(BaseModel):
    id: str
    kind: Kind
    parent: str | None
    target: str | None
    state: str
    warnings: list[str]
    error: dict | None
    progress: float
    artifacts: list[ArtifactOut]


class RuntimeOut(BaseModel):
    detected: str
    available: list[str]
    ort: str
