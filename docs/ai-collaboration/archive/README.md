# Collaboration archives

These files are immutable snapshots retained for lossless historical review:

- `discussion-rounds-001-102.md` — the exact former `discussion.md`, including
  its original round numbering and authorship. The historical record itself
  contains two entries numbered 62 (one Claude Code correction and one Codex
  checkpoint); this pre-existing duplicate is intentionally preserved rather
  than silently renumbered.
- `status-through-round-102.md` — the exact former `status.md` at the end of
  round 102.
- `discussion-rounds-103-124.md` — the exact former `discussion.md` covering
  the "deterministic Kefu operational responses" phase (rounds 103-124):
  goal confirmation, plan v2, implementation, mutual cross-review, and the
  round-124 `customer_id` domain correction. Includes the round-112/114
  correction (rounds 103/105/107/109/111 were actually written by a
  mislabeled Codex subagent, not genuine Claude Code).
- `status-through-round-124.md` — the exact former `status.md` at the end of
  round 124.

SHA-256 checksums at archival time:

```text
discussion-rounds-001-102.md   7BE4AAFDE1037F14A15B07988A48A65102B39FD266084DA0CBCFE15F0FCECC0C
status-through-round-102.md    1785B6787EA5A728D43778111451F318757458C3C41B01EFDCA9A3ACDB272671
discussion-rounds-103-124.md   68B8116D0BC33E961DD3C3BBC622B8C317B3BBBD33A8D3E0A8358DD976249C0E
status-through-round-124.md    F49A40940F4DB7BEF03D8E29B98FB5F39036CB59B0B29693673682A677DC8577
```

Because the snapshots are byte-for-byte copies moved one directory deeper,
their old relative links are retained verbatim. Resolve those links against
the parent `docs/ai-collaboration/` directory. The active indexes provide
working links for current navigation.

Rounds 103-124's phase shipped 8 commits directly authorized by the user
(`f945063` through the phase's implementation/cross-review commits) but the
formal discussion never recorded a final "commit/push authorized" round —
the user's authorization happened live, out-of-band, rather than as a
discussion.md entry. Recorded here rather than silently implied.
