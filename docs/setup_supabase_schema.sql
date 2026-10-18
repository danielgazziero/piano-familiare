-- ══════════════════════════════════════════════════════════
-- Piano Finanziario Familiare — Schema Supabase
-- Incolla ed esegui tutto questo file nell'SQL Editor di Supabase
-- Una sola volta, al primo avvio
-- ══════════════════════════════════════════════════════════

-- 0. Catalogo asset (fonte di verità — sostituisce le liste hardcoded)
CREATE TABLE IF NOT EXISTS asset_catalog (
    isin             TEXT PRIMARY KEY,
    nome             TEXT NOT NULL,
    tipo             TEXT NOT NULL CHECK (tipo IN ('fondo','etf','azione')),
    ticker_yf        TEXT,
    ticker_bi        TEXT,
    fallback_tickers JSONB DEFAULT '[]',
    ter              NUMERIC,
    proprietario     TEXT DEFAULT 'persona1',
    stato            TEXT DEFAULT 'attivo',
    valore_quota_ref NUMERIC,
    data_ref         TEXT,
    valore_iniziale  NUMERIC DEFAULT 0,
    target_pct       NUMERIC DEFAULT 0,
    note             TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);
-- Migration: aggiungi target_pct se asset_catalog esiste già
ALTER TABLE asset_catalog ADD COLUMN IF NOT EXISTS target_pct NUMERIC DEFAULT 0;
ALTER TABLE asset_catalog ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY "service_only" ON asset_catalog
        FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
  EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE INDEX IF NOT EXISTS idx_asset_catalog_tipo ON asset_catalog(tipo);

-- 1. Snapshot giornaliero patrimonio
CREATE TABLE IF NOT EXISTS patrimonio_log (
    id                   BIGSERIAL PRIMARY KEY,
    data                 DATE NOT NULL DEFAULT CURRENT_DATE,
    fondi_bancari        NUMERIC,
    generali             NUMERIC,
    etf_persona1         NUMERIC,
    etf_figlio           NUMERIC,
    azioni_acn_usd       NUMERIC,
    liquidita            NUMERIC,
    totale_eur           NUMERIC,
    totale_netto_fiscale NUMERIC,
    tassa_latente_fondi  NUMERIC,
    note                 TEXT,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(data)
);

-- 2. Quote fondi aggiornate manualmente
CREATE TABLE IF NOT EXISTS quote_fondi (
    id         BIGSERIAL PRIMARY KEY,
    data       DATE NOT NULL DEFAULT CURRENT_DATE,
    isin       TEXT NOT NULL,
    nome       TEXT,
    quota      NUMERIC NOT NULL,
    valore     NUMERIC,
    quantita   NUMERIC,
    fonte      TEXT DEFAULT 'manuale',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(data, isin)
);

-- 3. Transazioni bancarie (da XLS importati)
CREATE TABLE IF NOT EXISTS transazioni (
    id            BIGSERIAL PRIMARY KEY,
    data          DATE NOT NULL,
    importo       NUMERIC NOT NULL,
    descrizione   TEXT,
    categoria     TEXT,
    conto         TEXT,
    raw_categoria TEXT,
    hash_tx       TEXT UNIQUE,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Parametri di configurazione (key-value)
CREATE TABLE IF NOT EXISTS config_params (
    chiave     TEXT PRIMARY KEY,
    valore     JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. Storico quantità per ogni asset (fondi, ETF, azioni)
CREATE TABLE IF NOT EXISTS posizioni (
    id          BIGSERIAL PRIMARY KEY,
    isin        TEXT NOT NULL,
    nome        TEXT,
    tipo        TEXT,
    quantita    NUMERIC NOT NULL,
    data_inizio DATE NOT NULL,
    data_fine   DATE,
    note        TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 6. Prezzi storici giornalieri (da Yahoo Finance)
CREATE TABLE IF NOT EXISTS prezzi_storici (
    id         BIGSERIAL PRIMARY KEY,
    isin       TEXT NOT NULL,
    data       DATE NOT NULL,
    prezzo     NUMERIC NOT NULL,
    fonte      TEXT DEFAULT 'yahoo',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(isin, data)
);

-- 7. Stato backfill (ultima data scaricata per asset)
CREATE TABLE IF NOT EXISTS backfill_stato (
    isin        TEXT PRIMARY KEY,
    ultima_data DATE NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 8. Asset illiquidi (immobili, TFR, previdenza complementare)
CREATE TABLE IF NOT EXISTS blocked_assets_log (
    id          BIGSERIAL PRIMARY KEY,
    data        DATE NOT NULL DEFAULT CURRENT_DATE,
    asset_id    TEXT NOT NULL,
    asset_nome  TEXT NOT NULL,
    asset_tipo  TEXT NOT NULL DEFAULT 'altro',
    valore      NUMERIC NOT NULL DEFAULT 0,
    nota        TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(data, asset_id)
);

-- ── Row Level Security ────────────────────────────────────────
ALTER TABLE patrimonio_log  ENABLE ROW LEVEL SECURITY;
ALTER TABLE blocked_assets_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE quote_fondi     ENABLE ROW LEVEL SECURITY;
ALTER TABLE transazioni     ENABLE ROW LEVEL SECURITY;
ALTER TABLE config_params   ENABLE ROW LEVEL SECURITY;
ALTER TABLE posizioni       ENABLE ROW LEVEL SECURITY;
ALTER TABLE prezzi_storici  ENABLE ROW LEVEL SECURITY;
ALTER TABLE backfill_stato  ENABLE ROW LEVEL SECURITY;

-- Policy: accesso solo service_role (la anon key non puo' leggere/scrivere).
-- Rimuove eventuali vecchie policy permissive "full_access" (USING true).
DROP POLICY IF EXISTS "full_access" ON patrimonio_log;
DO $$ BEGIN
  CREATE POLICY "service_only" ON patrimonio_log FOR ALL
    USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
  EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DROP POLICY IF EXISTS "full_access" ON quote_fondi;
DO $$ BEGIN
  CREATE POLICY "service_only" ON quote_fondi FOR ALL
    USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
  EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DROP POLICY IF EXISTS "full_access" ON transazioni;
DO $$ BEGIN
  CREATE POLICY "service_only" ON transazioni FOR ALL
    USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
  EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DROP POLICY IF EXISTS "full_access" ON config_params;
DO $$ BEGIN
  CREATE POLICY "service_only" ON config_params FOR ALL
    USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
  EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DROP POLICY IF EXISTS "full_access" ON posizioni;
DO $$ BEGIN
  CREATE POLICY "service_only" ON posizioni FOR ALL
    USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
  EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DROP POLICY IF EXISTS "full_access" ON prezzi_storici;
DO $$ BEGIN
  CREATE POLICY "service_only" ON prezzi_storici FOR ALL
    USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
  EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DROP POLICY IF EXISTS "full_access" ON backfill_stato;
DO $$ BEGIN
  CREATE POLICY "service_only" ON backfill_stato FOR ALL
    USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
  EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DROP POLICY IF EXISTS "full_access" ON blocked_assets_log;
DO $$ BEGIN
  CREATE POLICY "service_only" ON blocked_assets_log FOR ALL
    USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
  EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── Indici per performance ────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_prezzi_isin_data
    ON prezzi_storici(isin, data DESC);
CREATE INDEX IF NOT EXISTS idx_posizioni_isin
    ON posizioni(isin, data_inizio);
CREATE INDEX IF NOT EXISTS idx_transazioni_data
    ON transazioni(data DESC);
CREATE INDEX IF NOT EXISTS idx_patrimonio_data
    ON patrimonio_log(data DESC);
CREATE INDEX IF NOT EXISTS idx_blocked_assets_data
    ON blocked_assets_log(data DESC);
CREATE INDEX IF NOT EXISTS idx_blocked_assets_id
    ON blocked_assets_log(asset_id, data DESC);

-- ── Verifica ──────────────────────────────────────────────────
SELECT
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS dimensione
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;
