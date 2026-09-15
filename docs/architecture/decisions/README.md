# Architecture decision records

ADRs are immutable decision records. When a decision changes, mark the old ADR
superseded and add a new ADR; do not rewrite the original rationale as though it
never existed.

Current records include ADR-008, which supersedes ADR-005's Railway production
hosting choice with Render, ADR-009, on why customer-identity roles
(customer, fedex_label_agent) are a code-level category rather than an
admin-panel-editable DB column, for this version, and ADR-010, on
consolidating role/service policy attributes (customer identity, warehouse
scope) into one shared code-owned declaration, deferring JSONB/EAV storage
until a real descriptive (non-behavioral) attribute is requested.

