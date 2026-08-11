"""
Side-effect-free U-Choice authorization constants — Phase 4
(docs/ai-collaboration/phase4-self-registration.md Sec 4/5), per Codex
round-41: core validation code must not import a scheduled-job module
(jobs/uchoice_daily.py has a bound WeChat client), so the shared warehouse
set lives here instead, with the job importing from this module rather than
the other way around.
"""

# The platform's two warehouses. No group-to-warehouse grant table exists in
# the schema (group_service_role grants services, not warehouses; group_config
# .context is a location preset, not an authorization catalog) -- this is the
# platform-wide set, confirmed against jobs/uchoice_daily.py's prior local
# WAREHOUSES list before extraction.
VALID_WAREHOUSE_CODES = frozenset({"JFK", "DE"})

# Explicit positive allowlist for role_change's new_role target, per Codex
# round-41: an exclusion rule ("every role except pending") would silently
# expose any future internal/system role the moment it's added. pending is
# deliberately absent -- system-assigned only, via self-registration.
ASSIGNABLE_ROLE_NAMES = frozenset({"admin", "customer", "warehouseman", "accountant"})
