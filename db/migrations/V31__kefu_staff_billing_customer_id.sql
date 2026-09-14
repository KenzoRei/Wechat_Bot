-- V31: kefu_staff gains billing_customer_id, mirroring group_member's
-- column of the same name (V27).
--
-- Closes a real gap: KefuStaff previously had no way to bind a Kefu-side
-- customer identity role (customer, fedex_label_agent) to their own
-- customer record, so resolve_billing_customer_id() unconditionally
-- rejected them, and handlers/uchoice/role_change.py explicitly refused to
-- ever assign a customer-identity role to a Kefu identity at all. See
-- core/role_registry.py's CUSTOMER_IDENTITY_ROLE_NAMES docstring for the
-- full reasoning on which roles need this binding and why it's still
-- code-level, not admin-panel-editable, for this version.
--
-- Idempotent for both an existing deployment and a fresh database.

ALTER TABLE kefu_staff
    ADD COLUMN IF NOT EXISTS billing_customer_id VARCHAR(7) REFERENCES customer(customer_id);
