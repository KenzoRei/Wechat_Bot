# Review workspace

`reviews/active/` is temporary workspace for ongoing multi-review audits. It is
ignored because working prompts may contain unverified or sensitive material.

At review closeout:

1. Remove all credential literals and temporary commands.
2. Retain one signed final plan and only the supporting reviews needed to
   understand it.
3. Move the sanitized record to `docs/archive/collaboration/YYYY-MM-topic/`.
4. Update durable architectural decisions as ADRs instead of requiring readers
   to search transcripts.
5. Delete redundant working prompts and status snapshots.

