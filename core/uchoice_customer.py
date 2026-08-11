"""
Customer selection/locking for Kefu-originated, customer-scoped requests
(kefu-migration-plan.md Sec 6.2, docs/ai-collaboration/discussion.md round
98). A shared Kefu account works cases for many different customers in one
conversation channel, so which customer a case is for has to be explicitly
resolved from the AI's candidate match and then LOCKED on the case for the
rest of its life -- never re-resolved or overwritten by a later turn, even
if the AI extracts a different customer_id by mistake.
"""
from uuid import UUID


def resolve_and_lock_customer(session, collected_fields: dict, candidates: list[dict]) -> str | None:
    """
    Returns the case's locked customer_id (as a string), or None if it
    isn't resolved yet.

    Once `session.customer_id` is set, it is authoritative and returned
    as-is -- any customer_id the AI extracts on a later turn is ignored,
    by design (round 98: locked once, never drifts). Only while it is
    still unset does a freshly-extracted customer_id get validated against
    the real candidate list (never trusted blindly -- the AI can
    hallucinate an id that isn't in the directory) and, if valid, written
    onto `session.customer_id` to lock it in for every subsequent turn.

    `session` may be a brand-new, not-yet-flushed ConversationSession (the
    caller is expected to have already set it on the object before calling
    this) or an existing one being continued.
    """
    if session.customer_id is not None:
        return str(session.customer_id)

    candidate_id = collected_fields.get("customer_id")
    if not candidate_id:
        return None

    valid_ids = {c["customer_id"] for c in candidates}
    if candidate_id not in valid_ids:
        return None

    session.customer_id = UUID(candidate_id)
    return candidate_id
