"""Typed boundaries between the Kefu transport and case-service owners."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Protocol, TypeAlias


@dataclass(frozen=True)
class KefuIdentity:
    open_kfid: str
    external_userid: str


@dataclass(frozen=True)
class KefuInboundTurn:
    identity: KefuIdentity
    msgid: str
    received_at: datetime
    msgtype: str
    content: str | None
    payload: dict
    case_number_hint: str | None = None


@dataclass(frozen=True)
class Artifact:
    content: bytes
    filename: str
    content_type: str
    artifact_key: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Artifact":
        content = value.get("bytes")
        if not isinstance(content, bytes):
            raise ValueError("artifact bytes must be bytes")
        filename = value.get("filename")
        content_type = value.get("content_type")
        artifact_key = value.get("artifact_key")
        if not all(isinstance(item, str) and item for item in (filename, content_type, artifact_key)):
            raise ValueError("artifact metadata is incomplete")
        return cls(content, filename, content_type, artifact_key)


ArtifactLike: TypeAlias = Artifact | Mapping[str, Any]


def coerce_artifact(value: ArtifactLike) -> Artifact:
    return value if isinstance(value, Artifact) else Artifact.from_mapping(value)


@dataclass(frozen=True)
class CaseTurnSuccess:
    reply_text: str
    customer_copy_text: str | None
    case_number: str
    new_revision: int
    artifacts: tuple[ArtifactLike, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CaseTurnStale:
    current_revision: int
    current_state_summary: str


@dataclass(frozen=True)
class CaseTurnDenied:
    reason: str


@dataclass(frozen=True)
class CaseTurnError:
    message: str


CaseTurnResult: TypeAlias = CaseTurnSuccess | CaseTurnStale | CaseTurnDenied | CaseTurnError


class CaseTurnProcessor(Protocol):
    def __call__(
        self,
        *,
        identity: KefuIdentity,
        message_content: str,
        message_meta: dict[str, Any],
        case_number_hint: str | None,
    ) -> CaseTurnResult: ...
