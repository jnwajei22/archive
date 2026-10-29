create table if not exists users (
  id uuid primary key default gen_random_uuid(),
  chatgpt_user_id text,
  timezone text default 'America/Chicago',
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists broker_accounts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references users(id),
  broker_name text not null,
  account_id text,
  environment text check (environment in ('paper', 'live')) default 'paper',
  api_key_encrypted text,
  api_secret_encrypted text,
  status text default 'inactive',
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists watchlist_pairs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references users(id),
  pair text not null,
  enabled boolean default true,
  max_spread numeric,
  allowed_timeframes jsonb default '["15m", "1h"]',
  allowed_sessions jsonb default '["london", "new_york"]',
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists signals (
  id uuid primary key default gen_random_uuid(),
  source text,
  pair text,
  timeframe text,
  strategy text,
  direction text,
  raw_payload jsonb,
  status text default 'untrusted',
  score int,
  created_at timestamptz default now(),
  expires_at timestamptz
);

create table if not exists analyses (
  id uuid primary key default gen_random_uuid(),
  signal_id uuid references signals(id),
  pair text,
  timeframe text,
  trend text,
  swing_high numeric,
  swing_low numeric,
  fib_levels_json jsonb,
  support_zones_json jsonb,
  resistance_zones_json jsonb,
  score int,
  summary text,
  chart_path text,
  created_at timestamptz default now()
);

create table if not exists order_previews (
  id text primary key,
  analysis_id uuid references analyses(id),
  pair text not null,
  side text not null,
  order_type text not null,
  entry numeric not null,
  stop_loss numeric not null,
  take_profit numeric not null,
  lot_size numeric not null,
  risk_amount numeric not null,
  risk_percent numeric not null,
  reward_risk numeric,
  status text default 'preview_only',
  expires_at timestamptz not null,
  created_at timestamptz default now()
);

create table if not exists trades (
  id uuid primary key default gen_random_uuid(),
  preview_id text references order_previews(id),
  broker_order_id text,
  pair text,
  side text,
  lot_size numeric,
  entry numeric,
  stop_loss numeric,
  take_profit numeric,
  status text,
  submitted_at timestamptz,
  closed_at timestamptz,
  pnl numeric
);

create table if not exists risk_events (
  id uuid primary key default gen_random_uuid(),
  event_type text,
  severity text,
  message text,
  related_preview_id text,
  related_trade_id uuid,
  created_at timestamptz default now()
);

create table if not exists audit_logs (
  id uuid primary key default gen_random_uuid(),
  actor text,
  action text,
  payload_json jsonb,
  result_json jsonb,
  created_at timestamptz default now()
);
