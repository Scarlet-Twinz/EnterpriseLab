-- Run only against a disposable Enterprise Lab upstream PostgreSQL database
-- using a database administrator. Application traffic must use Proxima Engine.
-- The Engine's default role prefix maps tenant_a/b/c to proxima_tenant_tenant_a/b/c.
\set ON_ERROR_STOP on

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'proxima_tenant_tenant_a') THEN
        CREATE ROLE proxima_tenant_tenant_a NOLOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'proxima_tenant_tenant_b') THEN
        CREATE ROLE proxima_tenant_tenant_b NOLOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'proxima_tenant_tenant_c') THEN
        CREATE ROLE proxima_tenant_tenant_c NOLOGIN NOSUPERUSER NOBYPASSRLS;
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

CREATE TABLE IF NOT EXISTS employees (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    department TEXT NOT NULL,
    job_title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'inactive', 'leave')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, email)
);

CREATE TABLE IF NOT EXISTS projects (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    budget_cents BIGINT NOT NULL CHECK (budget_cents >= 0),
    status TEXT NOT NULL CHECK (status IN ('planned', 'active', 'paused', 'completed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
ALTER TABLE employees ENABLE ROW LEVEL SECURITY;
ALTER TABLE employees FORCE ROW LEVEL SECURITY;
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects FORCE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS customers_tenant_boundary ON customers;
CREATE POLICY customers_tenant_boundary ON customers
    USING (current_user = 'proxima_tenant_' || tenant_id)
    WITH CHECK (current_user = 'proxima_tenant_' || tenant_id);

DROP POLICY IF EXISTS invoices_tenant_boundary ON invoices;
CREATE POLICY invoices_tenant_boundary ON invoices
    USING (current_user = 'proxima_tenant_' || tenant_id)
    WITH CHECK (current_user = 'proxima_tenant_' || tenant_id);

DROP POLICY IF EXISTS employees_tenant_boundary ON employees;
CREATE POLICY employees_tenant_boundary ON employees
    USING (current_user = 'proxima_tenant_' || tenant_id)
    WITH CHECK (current_user = 'proxima_tenant_' || tenant_id);

DROP POLICY IF EXISTS projects_tenant_boundary ON projects;
CREATE POLICY projects_tenant_boundary ON projects
    USING (current_user = 'proxima_tenant_' || tenant_id)
    WITH CHECK (current_user = 'proxima_tenant_' || tenant_id);

DROP POLICY IF EXISTS documents_tenant_boundary ON documents;
CREATE POLICY documents_tenant_boundary ON documents
    USING (current_user = 'proxima_tenant_' || tenant_id)
    WITH CHECK (current_user = 'proxima_tenant_' || tenant_id);

GRANT USAGE ON SCHEMA public TO proxima_tenant_tenant_a, proxima_tenant_tenant_b, proxima_tenant_tenant_c;
GRANT SELECT, INSERT, UPDATE, DELETE ON customers, invoices, employees, projects, documents TO proxima_tenant_tenant_a, proxima_tenant_tenant_b, proxima_tenant_tenant_c;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO proxima_tenant_tenant_a, proxima_tenant_tenant_b, proxima_tenant_tenant_c;

-- Synthetic records for proving tenant separation. These are fictional.
INSERT INTO customers (tenant_id, name, email, status)
VALUES
    ('tenant_a', 'Northstar Operations Ltd', 'finance@northstar.example', 'active'),
    ('tenant_b', 'Bluehaven Logistics Ltd', 'accounts@bluehaven.example', 'active'),
    ('tenant_c', 'Cedarstone Health Systems', 'security@cedarstone.example', 'active')
ON CONFLICT (tenant_id, email) DO NOTHING;

INSERT INTO invoices (tenant_id, customer_id, amount_cents, currency, description, status)
SELECT c.tenant_id, c.id, seed.amount_cents, 'USD', seed.description, seed.status
FROM (VALUES
    ('tenant_a', 'finance@northstar.example', 125000, 'Quarterly advisory retainer', 'issued'),
    ('tenant_b', 'accounts@bluehaven.example', 289900, 'Warehouse systems support', 'draft'),
    ('tenant_c', 'security@cedarstone.example', 76000, 'Records governance review', 'paid')
) AS seed(tenant_id, customer_email, amount_cents, description, status)
JOIN customers c ON c.tenant_id = seed.tenant_id AND c.email = seed.customer_email
WHERE NOT EXISTS (
    SELECT 1 FROM invoices i
    WHERE i.tenant_id = seed.tenant_id AND i.description = seed.description
);

INSERT INTO employees (tenant_id, name, email, department, job_title, status)
VALUES
    ('tenant_a', 'Maya Okafor', 'maya.okafor@northstar.example', 'Finance', 'Finance Manager', 'active'),
    ('tenant_b', 'Daniel Mensah', 'daniel.mensah@bluehaven.example', 'Operations', 'Operations Lead', 'active'),
    ('tenant_c', 'Amina Bello', 'amina.bello@cedarstone.example', 'Compliance', 'Compliance Officer', 'active')
ON CONFLICT (tenant_id, email) DO NOTHING;

INSERT INTO projects (tenant_id, name, description, budget_cents, status)
SELECT seed.tenant_id, seed.name, seed.description, seed.budget_cents, seed.status
FROM (VALUES
    ('tenant_a', 'Quarterly Close', 'Synthetic financial close workflow.', 1250000, 'active'),
    ('tenant_b', 'Warehouse Modernization', 'Synthetic inventory and warehouse workflow.', 4800000, 'planned'),
    ('tenant_c', 'Records Governance', 'Synthetic compliance and records workflow.', 2200000, 'active')
) AS seed(tenant_id, name, description, budget_cents, status)
WHERE NOT EXISTS (
    SELECT 1 FROM projects p
    WHERE p.tenant_id = seed.tenant_id AND p.name = seed.name
);

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
