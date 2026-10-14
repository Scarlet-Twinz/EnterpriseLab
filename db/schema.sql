-- Run only against a disposable Enterprise Lab upstream PostgreSQL database
-- using a database administrator. Application traffic must use Proxima Engine.
-- The Engine's tenant-to-role mapping must select tenant_a, tenant_b, or tenant_c.
\set ON_ERROR_STOP on

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tenant_a') THEN
        CREATE ROLE tenant_a NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tenant_b') THEN
        CREATE ROLE tenant_b NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tenant_c') THEN
        CREATE ROLE tenant_c NOLOGIN;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS customers (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'prospect', 'suspended')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, email),
    UNIQUE (tenant_id, id)
);

CREATE TABLE IF NOT EXISTS invoices (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    customer_id BIGINT NOT NULL,
    amount_cents BIGINT NOT NULL CHECK (amount_cents > 0),
    currency CHAR(3) NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'issued', 'paid', 'void')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT invoices_customer_same_tenant
        FOREIGN KEY (tenant_id, customer_id) REFERENCES customers (tenant_id, id)
);

CREATE TABLE IF NOT EXISTS documents (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    title TEXT NOT NULL,
    classification TEXT NOT NULL CHECK (classification IN ('internal', 'confidential', 'restricted')),
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers FORCE ROW LEVEL SECURITY;
ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE invoices FORCE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS customers_tenant_boundary ON customers;
CREATE POLICY customers_tenant_boundary ON customers
    USING (tenant_id = current_user)
    WITH CHECK (tenant_id = current_user);

DROP POLICY IF EXISTS invoices_tenant_boundary ON invoices;
CREATE POLICY invoices_tenant_boundary ON invoices
    USING (tenant_id = current_user)
    WITH CHECK (tenant_id = current_user);

DROP POLICY IF EXISTS documents_tenant_boundary ON documents;
CREATE POLICY documents_tenant_boundary ON documents
    USING (tenant_id = current_user)
    WITH CHECK (tenant_id = current_user);

GRANT USAGE ON SCHEMA public TO tenant_a, tenant_b, tenant_c;
GRANT SELECT, INSERT, UPDATE, DELETE ON customers, invoices, documents TO tenant_a, tenant_b, tenant_c;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tenant_a, tenant_b, tenant_c;

-- Synthetic records for proving tenant separation. These are fictional.
INSERT INTO customers (tenant_id, name, email, status)
VALUES
    ('tenant_a', 'Northstar Operations Ltd', 'finance@northstar.example', 'active'),
    ('tenant_b', 'Bluehaven Logistics Ltd', 'accounts@bluehaven.example', 'active'),
    ('tenant_c', 'Cedarstone Health Systems', 'security@cedarstone.example', 'active')
ON CONFLICT (tenant_id, email) DO NOTHING;

INSERT INTO documents (tenant_id, title, classification, body)
SELECT seed.tenant_id, seed.title, seed.classification, seed.body
FROM (VALUES
    ('tenant_a', 'Quarterly Finance Summary', 'confidential', 'Synthetic finance data for tenant A.'),
    ('tenant_b', 'Warehouse Access Procedures', 'restricted', 'Synthetic logistics procedures for tenant B.'),
    ('tenant_c', 'Records Handling Policy', 'restricted', 'Synthetic health-sector policy data for tenant C.')
) AS seed(tenant_id, title, classification, body)
WHERE NOT EXISTS (
    SELECT 1 FROM documents d
    WHERE d.tenant_id = seed.tenant_id AND d.title = seed.title
);
