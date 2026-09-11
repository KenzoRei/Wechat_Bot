"""
Side-effect-free U-Choice authorization constants. Core validation code must
not import a scheduled-job module
(jobs/uchoice_daily.py has a bound WeChat client), so the shared warehouse
set lives here instead, with the job importing from this module rather than
the other way around.
"""

# The platform's warehouses. No group-to-warehouse grant table exists in
# the schema (role_service_permission grants services, not warehouses; group_config
# .context is a location preset, not an authorization catalog) -- this is the
# platform-wide set, confirmed against jobs/uchoice_daily.py's prior local
# WAREHOUSES list before extraction.
VALID_WAREHOUSE_CODES = frozenset({"JFK", "DE", "NJ"})

# ASSIGNABLE_ROLE_NAMES moved to core/role_registry.py -- it isn't a
# U-Choice-specific concept (label_agent, customer, and Kefu role
# assignment all depend on it too), and it deserved a home that could
# carry more than a bare set of strings. Import from there instead.

# Shared between jobs/uchoice_daily.py's push digest and the on-demand
# view_pending_digest service (handlers/uchoice/queries.py) so both agree on
# exactly when a pending request is flagged/retired -- same reasoning as the
# warehouse set above, one source of truth instead of two copies drifting.
STALE_THRESHOLD_DAYS = 7
