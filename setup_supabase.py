"""
SETUP INIZIALE SUPABASE — eseguire UNA SOLA VOLTA.
Crea le tabelle e migra i dati esistenti.

Uso:
    python setup_supabase.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

import os
from supabase import create_client

SUPABASE_URL    = os.environ.get("SUPABASE_URL", "")
SUPABASE_SECRET = os.environ.get("SUPABASE_SECRET", "")
if not SUPABASE_URL or not SUPABASE_SECRET:
    raise RuntimeError(
        "Imposta le variabili d'ambiente SUPABASE_URL e SUPABASE_SECRET prima di eseguire."
    )

SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS patrimonio_log (
        id                   BIGSERIAL PRIMARY KEY,
        data                 DATE NOT NULL DEFAULT CURRENT_DATE,
        fondi_bancari        NUMERIC,
        generali             NUMERIC,
        etf_persona1         NUMERIC,
        etf_flor             NUMERIC,
        azioni_acn_usd       NUMERIC,
        liquidita            NUMERIC,
        totale_eur           NUMERIC,
        totale_netto_fiscale NUMERIC,
        tassa_latente_fondi  NUMERIC,
        note                 TEXT,
        created_at           TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(data)
    )
    """,
    """
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
    )
    """,
    """
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS config_params (
        chiave     TEXT PRIMARY KEY,
        valore     JSONB NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
]

RLS_SQL = [
    "ALTER TABLE patrimonio_log  ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE quote_fondi     ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE transazioni     ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE config_params   ENABLE ROW LEVEL SECURITY",
    """CREATE POLICY IF NOT EXISTS "full_access" ON patrimonio_log
        FOR ALL USING (true) WITH CHECK (true)""",
    """CREATE POLICY IF NOT EXISTS "full_access" ON quote_fondi
        FOR ALL USING (true) WITH CHECK (true)""",
    """CREATE POLICY IF NOT EXISTS "full_access" ON transazioni
        FOR ALL USING (true) WITH CHECK (true)""",
    """CREATE POLICY IF NOT EXISTS "full_access" ON config_params
        FOR ALL USING (true) WITH CHECK (true)""",
]


def setup():
    print("=" * 50)
    print("Setup Supabase — Piano Finanziario Familiare")
    print("=" * 50)

    client = create_client(SUPABASE_URL, SUPABASE_SECRET)

    # Test connessione
    print("\n[1/3] Test connessione...", end=" ")
    try:
        client.table('config_params').select('chiave').limit(1).execute()
        print("OK (tabelle già esistenti)")
        print("\nSetup già completato. Nessuna azione necessaria.")
        return
    except Exception:
        print("OK (primo setup)")

    # Crea tabelle via SQL Editor — Supabase non espone DDL via REST
    # Usiamo l'RPC per eseguire SQL raw
    print("\n[2/3] Creazione tabelle...")
    print("\n" + "─" * 50)
    project_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    print("AZIONE RICHIESTA: apri il browser sul tuo progetto Supabase:")
    print(f"  {project_url}/project/default/sql/new" if project_url else
          "  https://supabase.com (accedi al tuo progetto)")
    print("\nVai su: SQL Editor (menu a sinistra)")
    print("Incolla ed esegui questo SQL:")
    print("─" * 50)

    tutto_sql = "\n\n".join(SCHEMA_SQL) + "\n\n" + "\n".join(RLS_SQL) + ";"
    print(tutto_sql)
    print("─" * 50)

    input("\nPremi INVIO dopo aver eseguito l'SQL su Supabase...")

    # Verifica
    print("\n[3/3] Verifica tabelle...", end=" ")
    try:
        client.table('patrimonio_log').select('id').limit(1).execute()
        client.table('quote_fondi').select('id').limit(1).execute()
        client.table('transazioni').select('id').limit(1).execute()
        client.table('config_params').select('chiave').limit(1).execute()
        print("OK — tutte le tabelle create")
    except Exception as e:
        print(f"ERRORE: {e}")
        print("Riprova ad eseguire l'SQL su Supabase.")
        return

    # Inserisci quote fondi iniziali dal config
    print("\nInserimento quote fondi iniziali...")
    from parser import load_config
    config = load_config(Path(__file__).parent / 'config.yaml')
    from database import salva_quote_fondi
    righe = [{
        'isin': f['isin'],
        'nome': f['nome'],
        'quota': f['valore_quota_ref'],
        'quantita': f['quantita'],
        'valore': round(f['quantita'] * f['valore_quota_ref'], 2)
    } for f in config['fondi_bancari']['titoli']]
    if salva_quote_fondi(righe, fonte='config_iniziale'):
        print(f"  [+] {len(righe)} quote fondi inserite")

    # Inserisci params iniziali
    print("Inserimento parametri iniziali...")
    from database import salva_param
    p = config['patrimonio']
    params = {
        'liquidita_persona1': p['liquidita_persona1'],
        'liquidita_persona2': p['liquidita_persona2'],
        'conto_comune': p['conto_comune'],
        'affitto_accantonato': p['affitto_accantonato'],
        'fondi_bancari': p['fondi_bancari'],
        'gestione_separata_generali': p['gestione_separata_generali'],
    }
    for k, v in params.items():
        salva_param(k, v)
        print(f"  [+] {k}: {v}")

    print("\n" + "=" * 50)
    print("Setup completato!")
    print("Ora puoi avviare l'app con: streamlit run app.py")
    print("=" * 50)


if __name__ == "__main__":
    setup()


# ── SQL aggiuntivo per posizioni e prezzi ──────────────────
POSITIONS_SQL = """
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

CREATE TABLE IF NOT EXISTS prezzi_storici (
    id          BIGSERIAL PRIMARY KEY,
    isin        TEXT NOT NULL,
    data        DATE NOT NULL,
    prezzo      NUMERIC NOT NULL,
    fonte       TEXT DEFAULT 'yahoo',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(isin, data)
);

CREATE TABLE IF NOT EXISTS backfill_stato (
    isin        TEXT PRIMARY KEY,
    ultima_data DATE NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE posizioni       ENABLE ROW LEVEL SECURITY;
ALTER TABLE prezzi_storici  ENABLE ROW LEVEL SECURITY;
ALTER TABLE backfill_stato  ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  CREATE POLICY "full_access" ON posizioni       FOR ALL USING (true) WITH CHECK (true);
  EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE POLICY "full_access" ON prezzi_storici  FOR ALL USING (true) WITH CHECK (true);
  EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE POLICY "full_access" ON backfill_stato  FOR ALL USING (true) WITH CHECK (true);
  EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
"""
