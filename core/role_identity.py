"""
Tagged target-identity contract for role_change. Identifiers can collide across
GroupMember and kefu_staff, so dispatch must be explicit and never inferred
by checking which table happens to contain a matching raw string).

A candidate's identity is always one of:
  {kind: "smart_robot", key: <wechat_openid>}
  {kind: "kefu",         key: <staff_id (uuid str)>}

Encoded as a single tagged string so the existing target_openid field/
prompt-extraction mechanism needs no schema change: a bare string is
always a Smart Robot wechat_openid (identical to every pre-Kefu identifier
that has ever flowed through this field -- zero behavior change for that
case), and a "kefu:<staff_id>" string is always a kefu_staff target.
"""
from dataclasses import dataclass

_KEFU_PREFIX = "kefu:"


@dataclass(frozen=True)
class TargetIdentity:
    kind: str  # "smart_robot" | "kefu"
    key: str


def tag_kefu_identity(staff_id) -> str:
    return f"{_KEFU_PREFIX}{staff_id}"


def parse_target_identity(raw: str | None) -> TargetIdentity | None:
    if not raw:
        return None
    if raw.startswith(_KEFU_PREFIX):
        return TargetIdentity(kind="kefu", key=raw[len(_KEFU_PREFIX):])
    return TargetIdentity(kind="smart_robot", key=raw)
