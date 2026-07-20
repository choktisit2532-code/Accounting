-- Smart Finance 2.0 - Supabase/PostgreSQL + Storage (complete fresh install)
-- WARNING: this script removes only existing Smart Finance tables and their data,
-- then recreates them. Use it for a new installation or after making a backup.
-- Run the entire file once in Supabase Dashboard > SQL Editor.

BEGIN;

DROP TABLE IF EXISTS
    public.pending_line_transactions,
    public.budgets,
    public.transactions,
    public.line_events,
    public.line_pair_codes,
    public.savings_goals,
    public.categories,
    public.accounts,
    public.users,
    public.alembic_version
CASCADE;

CREATE TABLE public.users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    full_name VARCHAR(150) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    line_user_id VARCHAR(100),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    CONSTRAINT uq_users_email UNIQUE (email),
    CONSTRAINT uq_users_line_user_id UNIQUE (line_user_id)
);

CREATE TABLE public.accounts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name VARCHAR(100) NOT NULL,
    type VARCHAR(30) NOT NULL,
    balance NUMERIC(14, 2) NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    CONSTRAINT fk_accounts_user
        FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE,
    CONSTRAINT account_type_check
        CHECK (type IN ('cash', 'bank', 'credit_card', 'investment', 'other'))
);

CREATE TABLE public.categories (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,
    name VARCHAR(100) NOT NULL,
    type VARCHAR(20) NOT NULL,
    icon VARCHAR(50),
    color VARCHAR(20),
    CONSTRAINT fk_categories_user
        FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE,
    CONSTRAINT category_type_check
        CHECK (type IN ('income', 'expense')),
    CONSTRAINT uq_category_user_name_type
        UNIQUE (user_id, name, type)
);

CREATE TABLE public.savings_goals (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name VARCHAR(150) NOT NULL,
    target_amount NUMERIC(14, 2) NOT NULL,
    current_amount NUMERIC(14, 2) NOT NULL,
    target_date DATE NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    CONSTRAINT fk_savings_goals_user
        FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE,
    CONSTRAINT savings_target_positive
        CHECK (target_amount > 0),
    CONSTRAINT savings_current_nonnegative
        CHECK (current_amount >= 0)
);

CREATE TABLE public.line_pair_codes (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    code VARCHAR(20) NOT NULL,
    expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    used_at TIMESTAMP WITHOUT TIME ZONE,
    CONSTRAINT fk_line_pair_codes_user
        FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE,
    CONSTRAINT uq_line_pair_codes_code UNIQUE (code)
);

CREATE TABLE public.line_events (
    id SERIAL PRIMARY KEY,
    event_key VARCHAR(150) NOT NULL,
    status VARCHAR(20) NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    processed_at TIMESTAMP WITHOUT TIME ZONE,
    CONSTRAINT uq_line_events_event_key UNIQUE (event_key)
);

CREATE TABLE public.transactions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    type VARCHAR(20) NOT NULL,
    amount NUMERIC(14, 2) NOT NULL,
    category_id INTEGER,
    account_id INTEGER NOT NULL,
    to_account_id INTEGER,
    date DATE NOT NULL,
    note TEXT,
    receipt_path VARCHAR(500),
    source VARCHAR(20) NOT NULL,
    external_id VARCHAR(120),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    CONSTRAINT fk_transactions_user
        FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE,
    CONSTRAINT fk_transactions_category
        FOREIGN KEY (category_id) REFERENCES public.categories(id) ON DELETE SET NULL,
    CONSTRAINT fk_transactions_account
        FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT,
    CONSTRAINT fk_transactions_to_account
        FOREIGN KEY (to_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT,
    CONSTRAINT transaction_type_check
        CHECK (type IN ('income', 'expense', 'transfer')),
    CONSTRAINT transaction_amount_positive
        CHECK (amount > 0),
    CONSTRAINT transaction_transfer_accounts_check
        CHECK (
            (
                type = 'transfer'
                AND to_account_id IS NOT NULL
                AND to_account_id <> account_id
            )
            OR
            (
                type <> 'transfer'
                AND to_account_id IS NULL
            )
        ),
    CONSTRAINT uq_transaction_source_external
        UNIQUE (source, external_id)
);

CREATE TABLE public.budgets (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    limit_amount NUMERIC(14, 2) NOT NULL,
    month INTEGER NOT NULL,
    year INTEGER NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    CONSTRAINT fk_budgets_user
        FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE,
    CONSTRAINT fk_budgets_category
        FOREIGN KEY (category_id) REFERENCES public.categories(id) ON DELETE RESTRICT,
    CONSTRAINT budget_limit_positive
        CHECK (limit_amount > 0),
    CONSTRAINT budget_month_check
        CHECK (month BETWEEN 1 AND 12),
    CONSTRAINT budget_year_check
        CHECK (year BETWEEN 2000 AND 2200),
    CONSTRAINT uq_budget_period
        UNIQUE (user_id, category_id, month, year)
);

CREATE TABLE public.pending_line_transactions (
    id SERIAL PRIMARY KEY,
    event_key VARCHAR(150) NOT NULL,
    user_id INTEGER NOT NULL,
    line_user_id VARCHAR(100) NOT NULL,
    payload JSON NOT NULL,
    receipt_path VARCHAR(500),
    status VARCHAR(20) NOT NULL,
    expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    resolved_at TIMESTAMP WITHOUT TIME ZONE,
    CONSTRAINT fk_pending_line_transactions_user
        FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE,
    CONSTRAINT pending_line_status_check
        CHECK (status IN ('pending', 'confirmed', 'cancelled', 'expired')),
    CONSTRAINT uq_pending_line_transactions_event_key UNIQUE (event_key)
);

CREATE INDEX ix_accounts_user_id
    ON public.accounts (user_id);

CREATE INDEX ix_categories_user_id
    ON public.categories (user_id);

CREATE INDEX ix_savings_goals_user_id
    ON public.savings_goals (user_id);

CREATE INDEX ix_line_pair_codes_user_id
    ON public.line_pair_codes (user_id);

CREATE INDEX ix_line_pair_codes_expires_at
    ON public.line_pair_codes (expires_at);

CREATE INDEX ix_transactions_user_id
    ON public.transactions (user_id);

CREATE INDEX ix_transactions_account_id
    ON public.transactions (account_id);

CREATE INDEX ix_transactions_date
    ON public.transactions (date);

CREATE INDEX ix_budgets_user_id
    ON public.budgets (user_id);

CREATE INDEX ix_pending_line_transactions_user_id
    ON public.pending_line_transactions (user_id);

CREATE INDEX ix_pending_line_transactions_line_user_id
    ON public.pending_line_transactions (line_user_id);

CREATE INDEX ix_pending_line_transactions_status
    ON public.pending_line_transactions (status);

CREATE INDEX ix_pending_line_transactions_expires_at
    ON public.pending_line_transactions (expires_at);

-- These names match SQLAlchemy/Alembic metadata used by Smart Finance.
CREATE UNIQUE INDEX ix_users_email
    ON public.users (email);

CREATE UNIQUE INDEX ix_users_line_user_id
    ON public.users (line_user_id);

CREATE UNIQUE INDEX ix_line_pair_codes_code
    ON public.line_pair_codes (code);

CREATE UNIQUE INDEX ix_line_events_event_key
    ON public.line_events (event_key);

CREATE UNIQUE INDEX ix_pending_line_transactions_event_key
    ON public.pending_line_transactions (event_key);

CREATE TABLE public.alembic_version (
    version_num VARCHAR(32) NOT NULL PRIMARY KEY
);

INSERT INTO public.alembic_version (version_num)
VALUES ('20260716_0002');

COMMIT;

-- Verification: the result should show 10 tables including alembic_version.
SELECT tablename
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN (
    'users',
    'accounts',
    'categories',
    'transactions',
    'budgets',
    'savings_goals',
    'line_pair_codes',
    'line_events',
    'pending_line_transactions',
    'alembic_version'
  )
ORDER BY tablename;

-- Private receipt bucket. The FastAPI backend validates size/type, strips EXIF,
-- resizes the image, converts it to WebP, and accesses this bucket with the
-- Supabase secret/service-role key. No public storage policy is required.
INSERT INTO storage.buckets (
    id,
    name,
    public,
    file_size_limit,
    allowed_mime_types
)
VALUES (
    'receipts',
    'receipts',
    false,
    NULL,
    NULL
)
ON CONFLICT (id) DO UPDATE
SET
    public = false,
    file_size_limit = NULL,
    allowed_mime_types = NULL;

SELECT id, name, public, file_size_limit, allowed_mime_types
FROM storage.buckets
WHERE id = 'receipts';
