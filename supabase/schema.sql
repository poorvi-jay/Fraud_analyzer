-- Fraud Investigation Squad -- Supabase (Postgres) schema.
-- Mirrors docs/ARCHITECTURE.md's ER diagram exactly and is what
-- backend/app/models.py targets once DATABASE_URL points at a real
-- Supabase project instead of local SQLite -- no application code changes
-- needed, just the connection string.
--
-- Run this in the Supabase SQL editor (or `psql` against the project's
-- connection string) once a real project exists.

create extension if not exists pgcrypto;

create table if not exists user_profiles (
    user_id text primary key,
    account_created date not null,
    home_country text not null,
    typical_transaction_amount double precision not null,
    travel_frequency text not null
);

create table if not exists transactions (
    id text primary key default gen_random_uuid()::text,
    user_id text not null references user_profiles(user_id),
    amount double precision not null,
    transaction_type text not null,
    origin_balance_before double precision not null,
    origin_balance_after double precision not null,
    location_country text not null,
    -- Nullable: populated for the PaySim-derived evaluation set, null for
    -- anything reviewed live through the demo (no ground truth exists then).
    is_fraud_ground_truth boolean,
    occurred_at timestamptz not null
);
create index if not exists idx_transactions_user_id on transactions(user_id);
create index if not exists idx_transactions_occurred_at on transactions(occurred_at desc);

create table if not exists agent_opinions (
    id text primary key default gen_random_uuid()::text,
    transaction_id text not null references transactions(id),
    agent_name text not null,
    score double precision not null,
    flag boolean not null,
    reasoning text not null
);
create index if not exists idx_agent_opinions_transaction_id on agent_opinions(transaction_id);

create table if not exists review_results (
    id text primary key default gen_random_uuid()::text,
    transaction_id text not null unique references transactions(id),
    final_verdict text not null check (final_verdict in ('allow', 'escalate', 'block')),
    coordinator_reasoning text not null
);

-- Phase 2 (human override). Table created now so the schema is stable and
-- demonstrable end-to-end later; nothing writes to it yet in the MVP.
create table if not exists human_reviews (
    id text primary key default gen_random_uuid()::text,
    review_result_id text not null references review_results(id),
    reviewer_id text not null, -- Supabase auth.users.id of the signed-in reviewer
    decision text not null,
    note text not null
);
create index if not exists idx_human_reviews_review_result_id on human_reviews(review_result_id);

-- Row Level Security: queue/detail/analytics stay publicly readable
-- (demo purpose, matches the unauthenticated GET endpoints in
-- docs/ARCHITECTURE.md); writes only ever happen through the backend
-- service role, never directly from the frontend.
alter table user_profiles enable row level security;
alter table transactions enable row level security;
alter table agent_opinions enable row level security;
alter table review_results enable row level security;
alter table human_reviews enable row level security;

create policy "public read user_profiles" on user_profiles for select using (true);
create policy "public read transactions" on transactions for select using (true);
create policy "public read agent_opinions" on agent_opinions for select using (true);
create policy "public read review_results" on review_results for select using (true);
-- human_reviews intentionally has no public read policy: override notes are
-- only exposed via the backend API (Phase 2), not directly queryable.
