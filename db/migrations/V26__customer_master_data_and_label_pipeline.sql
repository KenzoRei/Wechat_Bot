\encoding UTF8
-- ============================================================
-- V26: customer master data (F###### YDD-issued identity) + label pipeline
-- fixes and cutover
-- Logistics WeChat Bot Platform
-- Date: 2026-09-10
--
-- See docs/reviews/active/2026-09-customer-service-and-label-pipeline/plan.md
-- (gitignored, not tracked) for full design rationale.
--
-- New tables:
--   customer            -- master record, keyed by the real YDD cust_id (F######)
--   customer_credential -- encrypted per-customer secrets (YiDiDa login, OMS keys)
--   label_shipment       -- companion ledger to request_log, one row per label
--
-- Existing-table changes:
--   group_member gains billing_customer_id (nullable -- not every member is
--     a customer; non-null enforced at the application layer when
--     role='customer', mirroring the existing warehouseman/warehouse_codes
--     validation pattern). Named billing_customer_id, not customer_id, to
--     avoid colliding with the unrelated, pre-existing customer_id concept
--     already used elsewhere (request_log.customer_id -> uchoice_customer).
--
--   ups_only workflow gains the oms_create_workorder step (previously only
--     fedex_workorder had it) -- ships in the SAME deploy as the handler
--     code that makes OMS credential-driven rather than carrier-driven
--     (core/session_manager.py is untouched by this; see
--     handlers/oms_create_workorder.py). Activating this step alone against
--     the old handler would break UPS labels needing OMS config that
--     doesn't exist for UPS-only customers -- this migration and that code
--     change are one coordinated release, not staggered.
--
--   fedex_label / ups_label service_type rows:
--     - group_config_schema cleared to '{}' -- these services no longer
--       read group-level YDD/OMS config (moved to customer/
--       customer_credential); leaving the old schema in place would still
--       force new grants through api/admin/services.py's _validate_config()
--       to supply now-unused group-level values.
--     - input_schema gains billing_customer_id as a required field -- who
--       to bill for this label, resolved by the application either from the
--       requester's own group_member.billing_customer_id (customer-role
--       members, auto-filled, never asked) or collected conversationally
--       (staff/admin/Kefu creating a label on behalf of a customer).
--
-- U-Choice is untouched: no uchoice_* service_type/workflow/workflow_step
-- row is read or written by this migration.
--
-- Idempotent for both an existing deployment and a fresh database.
-- ============================================================

-- ── customer: the master record ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS customer (
    customer_id      VARCHAR(7)   PRIMARY KEY,
    display_name     VARCHAR(200) NOT NULL,
    display_name_cn  VARCHAR(200),
    contact_name     VARCHAR(200),
    email            VARCHAR(200),
    phone            VARCHAR(50),
    addr_line1       VARCHAR(300),
    city             VARCHAR(100),
    state            VARCHAR(50),
    zip              VARCHAR(20),
    country          VARCHAR(50),
    bank_account     VARCHAR(200),
    status           VARCHAR(20)  NOT NULL DEFAULT 'active',
    rate_multiplier  JSONB        NOT NULL DEFAULT '{}'::jsonb,
    ydd_channel_id   JSONB        NOT NULL DEFAULT '{}'::jsonb,
    oms_wh_code      VARCHAR(50),
    toggles          JSONB        NOT NULL DEFAULT '{}'::jsonb,
    notes            TEXT,
    created_by       VARCHAR(128) NOT NULL,
    updated_by       VARCHAR(128),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_customer_id_format CHECK (customer_id ~ '^F\d{6}$'),
    CONSTRAINT ck_customer_status CHECK (status IN ('pending', 'active', 'inactive'))
);

-- ── customer_credential: encrypted secrets, separate from the profile ────
CREATE TABLE IF NOT EXISTS customer_credential (
    customer_id      VARCHAR(7)   NOT NULL REFERENCES customer(customer_id) ON DELETE CASCADE,
    credential_type  VARCHAR(30)  NOT NULL,
    encrypted_value  BYTEA        NOT NULL,
    key_version      INTEGER      NOT NULL DEFAULT 1,
    created_by       VARCHAR(128) NOT NULL,
    updated_by       VARCHAR(128),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (customer_id, credential_type),
    CONSTRAINT ck_customer_credential_type CHECK (
        credential_type IN ('oms_app_key', 'oms_app_secret', 'ydd_username', 'ydd_password')
    )
);

-- ── label_shipment: companion ledger to request_log, one row per label ───
CREATE TABLE IF NOT EXISTS label_shipment (
    shipment_id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    request_log_id        UUID        NOT NULL UNIQUE REFERENCES request_log(log_id) ON DELETE CASCADE,
    billing_customer_id    VARCHAR(7) REFERENCES customer(customer_id),
    carrier                 VARCHAR(20) NOT NULL,
    tracking_number          VARCHAR(100),
    oms_work_order            VARCHAR(100),
    oms_error                  TEXT,
    sales_amount                NUMERIC(12, 2),
    status                       VARCHAR(20) NOT NULL DEFAULT 'created',
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_label_shipment_carrier CHECK (carrier IN ('fedex', 'ups')),
    CONSTRAINT ck_label_shipment_status CHECK (status IN ('created', 'failed', 'voided'))
);

CREATE INDEX IF NOT EXISTS idx_label_shipment_billing_customer_id ON label_shipment (billing_customer_id);

-- ── group_member: link a customer-role member to their billing identity ──
ALTER TABLE group_member ADD COLUMN IF NOT EXISTS billing_customer_id VARCHAR(7) REFERENCES customer(customer_id);

-- ── ups_only workflow: add the OMS step, coordinated with the handler ────
-- release (see header). Idempotent: skip if step 2 already exists (re-run
-- safety), and only ever insert -- never touches fedex_workorder or any
-- other workflow.
DO $$
DECLARE
    ups_only_id UUID := 'af000001-0000-0000-0000-000000000004';
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM workflow_step WHERE workflow_id = ups_only_id AND step_type = 'oms_create_workorder'
    ) THEN
        UPDATE workflow_step SET step_order = 3
        WHERE workflow_id = ups_only_id AND step_type = 'reply_wechat' AND step_order = 2;

        INSERT INTO workflow_step (workflow_id, step_order, step_type, config)
        VALUES (ups_only_id, 2, 'oms_create_workorder', '{}'::jsonb);
    END IF;
END $$;

-- ── fedex_label / ups_label: drop the now-unused group-level config ──────
-- requirement (moved to customer/customer_credential) and add
-- billing_customer_id to input_schema.
UPDATE service_type
SET group_config_schema = '{}'::jsonb
WHERE name IN ('fedex_label', 'ups_label');

UPDATE service_type
SET input_schema = '{"optional": ["oms_outbound_order_no", "service_level", "shipper_corp_name", "shipper_country", "recipient_corp_name", "recipient_country", "length_in", "width_in", "height_in", "reference_number"], "required": ["billing_customer_id", "shipper_name", "shipper_phone", "shipper_street", "shipper_city", "shipper_state", "shipper_zip", "recipient_name", "recipient_phone", "recipient_street", "recipient_city", "recipient_state", "recipient_zip", "weight_lbs"], "field_hints": {"billing_customer_id": "The customer this label is billed to, format F followed by 6 digits (e.g. F000172). Auto-filled from the requester''s own identity when they are a customer themselves -- only ask when the requester is staff/admin creating a label on behalf of someone else.", "weight_lbs": "numeric value in pounds", "service_level": "e.g. PRIORITY_OVERNIGHT, STANDARD_OVERNIGHT, FEDEX_GROUND, default is FEDEX_GROUND", "shipper_country": "default is US", "reference_number": "Optional field that appears on the label for your reference (e.g. order number, customer name)", "recipient_country": "default is US", "oms_outbound_order_no": "OMS outbound order number (e.g. OBS0162604110RV) -- only collect if the customer volunteers it, never ask proactively. Links the created label to their existing OMS order."}}'::jsonb
WHERE name = 'fedex_label';

UPDATE service_type
SET input_schema = '{"optional": ["service_level", "shipper_corp_name", "shipper_country", "recipient_corp_name", "recipient_country", "length_in", "width_in", "height_in", "reference_number"], "required": ["billing_customer_id", "shipper_name", "shipper_phone", "shipper_street", "shipper_city", "shipper_state", "shipper_zip", "recipient_name", "recipient_phone", "recipient_street", "recipient_city", "recipient_state", "recipient_zip", "weight_lbs"], "field_hints": {"billing_customer_id": "The customer this label is billed to, format F followed by 6 digits (e.g. F000172). Auto-filled from the requester''s own identity when they are a customer themselves -- only ask when the requester is staff/admin creating a label on behalf of someone else.", "weight_lbs": "numeric value in pounds", "service_level": "e.g. UPS_GROUND, UPS_2ND_DAY_AIR, UPS_NEXT_DAY_AIR, default is UPS_GROUND", "shipper_country": "default is US", "reference_number": "Optional field that appears on the label for your reference (e.g. order number, customer name)", "recipient_country": "default is US"}}'::jsonb
WHERE name = 'ups_label';
