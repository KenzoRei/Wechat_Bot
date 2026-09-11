-- V29: company_warehouse -- the company's own physical warehouse/shipping-
-- origin directory (company name, address, contact, phone, email per
-- location).
--
-- Deliberately separate from core.uchoice_constants.VALID_WAREHOUSE_CODES
-- (JFK/DE/NJ only, U-Choice's own inventory-tracking warehouse_code
-- concept) -- this table is company-wide shipping-origin info used by the
-- label pipeline (fedex_label/ups_label shipper_* fields), covering every
-- physical location (including LAX/ORD, which never held U-Choice
-- inventory) rather than just the ones U-Choice's inventory system knows
-- about. Global, not scoped by group_id, same reasoning as `customer`.
--
-- Seeded with the 5 real warehouses provided directly by the business
-- (2026-09-12). warehouse_abbr is the natural key customers/staff already
-- use in conversation (e.g. "从LAX到DE"). company_name is the legal/
-- billing entity operating that location -- NOT always the same across
-- warehouses (JFK/DE/LAX/ORD are all "TWF-*", NJ is the separate "TWW"
-- entity) -- maps to shipper_corp_name on a label.
--
-- Idempotent for both an existing deployment and a fresh database.

CREATE TABLE IF NOT EXISTS company_warehouse (
    warehouse_abbr VARCHAR(10)  PRIMARY KEY,
    company_name   VARCHAR(200) NOT NULL,
    addr           VARCHAR(300) NOT NULL,
    city           VARCHAR(100) NOT NULL,
    state          VARCHAR(50)  NOT NULL,
    zip_code       VARCHAR(20)  NOT NULL,
    contact        VARCHAR(200),
    phone          VARCHAR(50),
    email          VARCHAR(200),
    created_by     VARCHAR(128) NOT NULL DEFAULT 'system',
    updated_by     VARCHAR(128),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

INSERT INTO company_warehouse (warehouse_abbr, company_name, addr, city, state, zip_code, contact, phone, email) VALUES
    ('JFK', 'TWF-JFK', '145-02 156th St',          'Jamaica',    'NY', '11434', 'Jeff',       '6462435122', NULL),
    ('DE',  'TWF-DE',  '201 Gabor Dr',              'Newark',     'DE', '19711', 'Zorro Zhang','3472040602', 'zt13353824884@gmail.com'),
    ('LAX', 'TWF-LAX', '293 E Redondo Beach Blvd',  'Gardena',    'CA', '90248', 'Paul Yang',  '6262425505', 'lax@transworldus.com'),
    ('ORD', 'TWF-ORD', '905 AEC Dr',                'Wood Dale',  'IL', '60191', 'Qi Feng',    '3123588261', 'chicago@transworldus.com'),
    ('NJ',  'TWW',     '120 Raskulinecz Rd',        'Carteret',   'NJ', '07008', 'Kevin',      '3478117879', 'info@twwnj.com')
ON CONFLICT (warehouse_abbr) DO NOTHING;
