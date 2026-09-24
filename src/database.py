"""
Database layer — Supabase (Postgres hosted).
Gestisce la persistenza automatica di tutti i dati tra sessioni.
Funziona identicamente su Mac e Windows senza installare nulla in locale.
"""

import os
import json
from datetime import datetime, date
from typing import Optional, Dict, List, Any
import pandas as pd
from supabase import create_client, Client

# ─────────────────────────────────────────────────────────────
# CONNESSIONE
# ─────────────────────────────────────────────────────────────

def _get_credentials():
    """Legge credenziali da st.secrets (Streamlit) o variabili d'ambiente."""
    try:
        import streamlit as st
        url    = st.secrets["SUPABASE_URL"]
        key    = st.secrets["SUPABASE_KEY"]
        secret = st.secrets["SUPABASE_SECRET"]
        return url, key, secret
    except Exception:
        url    = os.environ.get("SUPABASE_URL", "")
        key    = os.environ.get("SUPABASE_KEY", "")
        secret = os.environ.get("SUPABASE_SECRET", "")
        if not url:
            raise RuntimeError(
                "Credenziali Supabase mancanti. "
                "Configura SUPABASE_URL, SUPABASE_KEY, SUPABASE_SECRET "
                "in .streamlit/secrets.toml o come variabili d'ambiente."
            )
        return url, key, secret


def _make_client(use_secret: bool) -> Client:
    url, key, secret = _get_credentials()
    return create_client(url, secret if use_secret else key)


def _cached_client():
    try:
        import streamlit as st
        @st.cache_resource
        def _build():
            return _make_client(use_secret=True)
        return _build()
    except Exception:
        return _make_client(use_secret=True)


def _cached_public_client():
    try:
        import streamlit as st
        @st.cache_resource
        def _build_pub():
            return _make_client(use_secret=False)
        return _build_pub()
    except Exception:
        return _make_client(use_secret=False)


def get_client() -> Client:
    """Client Supabase con secret key (singleton per sessione Streamlit)."""
    return _cached_client()


def get_public_client() -> Client:
    """Client Supabase con publishable key (singleton per sessione Streamlit)."""
    return _cached_public_client()


# ─────────────────────────────────────────────────────────────
# SCHEMA SQL — eseguire una volta su Supabase SQL Editor
# ─────────────────────────────────────────────────────────────

SCHEMA_SQL = """
-- Snapshot giornaliero patrimonio
CREATE TABLE IF NOT EXISTS patrimonio_log (
    id          BIGSERIAL PRIMARY KEY,
    data        DATE NOT NULL DEFAULT CURRENT_DATE,
    fondi_bancari       NUMERIC,
    generali            NUMERIC,
    etf_daniel          NUMERIC,
    etf_flor            NUMERIC,
    azioni_acn_usd      NUMERIC,
    liquidita           NUMERIC,
    totale_eur          NUMERIC,
    totale_netto_fiscale NUMERIC,
    tassa_latente_fondi NUMERIC,
    note                TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(data)
);

-- Quote fondi (aggiornate ad ogni import XLS o manualmente)
CREATE TABLE IF NOT EXISTS quote_fondi (
    id          BIGSERIAL PRIMARY KEY,
    data        DATE NOT NULL DEFAULT CURRENT_DATE,
    isin        TEXT NOT NULL,
    nome        TEXT,
    quota       NUMERIC NOT NULL,
    valore      NUMERIC,
    quantita    NUMERIC,
    fonte       TEXT DEFAULT 'manuale',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(data, isin)
);

-- Transazioni bancarie (da XLS importati)
CREATE TABLE IF NOT EXISTS transazioni (
    id          BIGSERIAL PRIMARY KEY,
    data        DATE NOT NULL,
    importo     NUMERIC NOT NULL,
    descrizione TEXT,
    categoria   TEXT,
    conto       TEXT,
    raw_categoria TEXT,
    hash_tx     TEXT UNIQUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Config e parametri (key-value store)
CREATE TABLE IF NOT EXISTS config_params (
    chiave      TEXT PRIMARY KEY,
    valore      JSONB NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- RLS: abilita ma permetti tutto (uso personale, chiave segreta)
ALTER TABLE patrimonio_log     ENABLE ROW LEVEL SECURITY;
ALTER TABLE quote_fondi        ENABLE ROW LEVEL SECURITY;
ALTER TABLE transazioni        ENABLE ROW LEVEL SECURITY;
ALTER TABLE config_params      ENABLE ROW LEVEL SECURITY;

-- Policy: accesso completo con service key
CREATE POLICY "service_full_access" ON patrimonio_log
    FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "service_full_access" ON quote_fondi
    FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "service_full_access" ON transazioni
    FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "service_full_access" ON config_params
    FOR ALL USING (true) WITH CHECK (true);
"""


# ─────────────────────────────────────────────────────────────
# PATRIMONIO LOG
# ─────────────────────────────────────────────────────────────

def salva_snapshot_patrimonio(snapshot: dict, note: str = None) -> bool:
    """
    Salva snapshot patrimonio odierno.
    Se esiste già uno snapshot per oggi, lo sovrascrive (upsert).
    """
    try:
        client = get_client()
        record = {
            'data': date.today().isoformat(),
            'fondi_bancari': float(snapshot.get('fondi_bancari', 0)),
            'generali': float(snapshot.get('generali', 0)),
            'etf_daniel': float(snapshot.get('etf_daniel', 0)),
            'etf_flor': float(snapshot.get('etf_flor', 0)),
            'azioni_acn_usd': float(snapshot.get('azioni_acn_usd', 0)),
            'liquidita': float(snapshot.get('liquidita', 0)),
            'totale_eur': float(snapshot.get('totale_eur', 0)),
            'totale_netto_fiscale': float(snapshot.get('totale_netto_fiscale', 0)),
            'tassa_latente_fondi': float(snapshot.get('tassa_latente_fondi', 0)),
            'note': note
        }
        client.table('patrimonio_log').upsert(record, on_conflict='data').execute()
        return True
    except Exception as e:
        print(f"  [!] Errore salvataggio patrimonio: {e}")
        return False


def carica_patrimonio_log(giorni: int = 365) -> pd.DataFrame:
    """Carica storico patrimonio degli ultimi N giorni."""
    try:
        client = get_client()
        data_inizio = (date.today() - pd.Timedelta(days=giorni)).isoformat()
        res = (client.table('patrimonio_log')
               .select('*')
               .gte('data', data_inizio)
               .order('data')
               .execute())
        if not res.data:
            return pd.DataFrame()
        df = pd.DataFrame(res.data)
        df['data'] = pd.to_datetime(df['data'])
        df = df.set_index('data')
        cols_num = ['fondi_bancari','generali','etf_daniel','etf_flor',
                    'azioni_acn_usd','liquidita','totale_eur',
                    'totale_netto_fiscale','tassa_latente_fondi']
        for c in cols_num:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce')
        return df
    except Exception as e:
        print(f"  [!] Errore caricamento log patrimonio: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────
# QUOTE FONDI
# ─────────────────────────────────────────────────────────────

def salva_quote_fondi(righe: List[dict], fonte: str = 'manuale') -> bool:
    """
    Salva quote fondi per la data odierna.
    righe: lista di dict con isin, nome, quota, valore, quantita.
    """
    try:
        client = get_client()
        records = [{
            'data': date.today().isoformat(),
            'isin': r['isin'],
            'nome': r.get('nome', ''),
            'quota': float(r['quota']),
            'valore': float(r.get('valore', 0)),
            'quantita': float(r.get('quantita', 0)),
            'fonte': fonte
        } for r in righe]
        client.table('quote_fondi').upsert(records, on_conflict='data,isin').execute()
        return True
    except Exception as e:
        print(f"  [!] Errore salvataggio quote fondi: {e}")
        return False


def carica_ultime_quote_fondi() -> pd.DataFrame:
    """
    Carica le ultime quote disponibili per ogni fondo
    (non necessariamente di oggi — prende l'ultimo valore noto).
    """
    try:
        client = get_client()
        # Ordina per data DESC e limita a 500 righe: per 7 fondi copre anni di storico
        # senza scansionare l'intera tabella. Il groupby estrae poi l'ultima per ISIN.
        res = (client.table('quote_fondi')
               .select('isin, nome, quota, valore, quantita, data, fonte')
               .order('data', desc=True)
               .limit(500)
               .execute())
        if not res.data:
            return pd.DataFrame()
        df = pd.DataFrame(res.data)
        df['data'] = pd.to_datetime(df['data'])
        df = df.groupby('isin').first().reset_index()
        return df
    except Exception as e:
        print(f"  [!] Errore caricamento quote fondi: {e}")
        return pd.DataFrame()


def storico_quote_fondo(isin: str, giorni: int = 180) -> pd.DataFrame:
    """Storico quote di un singolo fondo per grafici."""
    try:
        client = get_client()
        data_inizio = (date.today() - pd.Timedelta(days=giorni)).isoformat()
        res = (client.table('quote_fondi')
               .select('data, quota, valore')
               .eq('isin', isin)
               .gte('data', data_inizio)
               .order('data')
               .execute())
        if not res.data:
            return pd.DataFrame()
        df = pd.DataFrame(res.data)
        df['data'] = pd.to_datetime(df['data'])
        df['quota'] = pd.to_numeric(df['quota'], errors='coerce')
        df['valore'] = pd.to_numeric(df['valore'], errors='coerce')
        return df
    except Exception as e:
        print(f"  [!] Errore storico fondo {isin}: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────
# TRANSAZIONI BANCARIE
# ─────────────────────────────────────────────────────────────

def salva_transazioni(df_tx: pd.DataFrame) -> int:
    """
    Salva transazioni nel DB, evitando duplicati tramite hash.
    Restituisce il numero di nuove transazioni inserite.
    """
    try:
        import hashlib
        client = get_client()
        records = []
        for _, row in df_tx.iterrows():
            # Hash univoco per deduplicazione
            hash_str = f"{row['date'].date()}_{row['amount']}_{row['description'][:30]}_{row['account']}"
            hash_tx = hashlib.md5(hash_str.encode()).hexdigest()
            records.append({
                'data': row['date'].date().isoformat(),
                'importo': float(row['amount']),
                'descrizione': str(row['description'])[:500],
                'categoria': str(row['category']),
                'conto': str(row['account']),
                'raw_categoria': str(row.get('raw_category', '')),
                'hash_tx': hash_tx
            })

        if not records:
            return 0
        # Batch insert — ON CONFLICT DO NOTHING su hash_tx; response.data contiene solo le righe inserite
        resp = client.table('transazioni').insert(records, ignore_duplicates=True).execute()
        return len(resp.data) if resp.data else 0
    except Exception as e:
        print(f"  [!] Errore salvataggio transazioni: {e}")
        return 0


def carica_transazioni(mesi: int = 12) -> pd.DataFrame:
    """Carica transazioni degli ultimi N mesi dal DB."""
    try:
        client = get_client()
        data_inizio = (date.today() - pd.Timedelta(days=mesi*31)).isoformat()
        res = (client.table('transazioni')
               .select('data, importo, descrizione, categoria, conto')
               .gte('data', data_inizio)
               .order('data', desc=True)
               .execute())
        if not res.data:
            return pd.DataFrame()
        df = pd.DataFrame(res.data)
        df['date'] = pd.to_datetime(df['data'])
        df['amount'] = pd.to_numeric(df['importo'], errors='coerce')
        df['description'] = df['descrizione']
        df['category'] = df['categoria']
        df['account'] = df['conto']
        df['month'] = df['date'].dt.to_period('M')
        df['income'] = df['amount'].clip(lower=0)
        df['expense'] = df['amount'].clip(upper=0).abs()
        return df
    except Exception as e:
        print(f"  [!] Errore caricamento transazioni: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────
# CONFIG PARAMS (valori manuali persistenti)
# ─────────────────────────────────────────────────────────────

def salva_param(chiave: str, valore: Any) -> bool:
    """Salva un parametro di configurazione (es. liquidità aggiornata manualmente)."""
    try:
        client = get_client()
        client.table('config_params').upsert({
            'chiave': chiave,
            'valore': json.dumps(valore),
            'updated_at': datetime.now().isoformat()
        }, on_conflict='chiave').execute()
        return True
    except Exception as e:
        print(f"  [!] Errore salvataggio param {chiave}: {e}")
        return False


def carica_param(chiave: str, default: Any = None) -> Any:
    """Legge un parametro di configurazione dal DB."""
    try:
        client = get_client()
        res = (client.table('config_params')
               .select('valore')
               .eq('chiave', chiave)
               .execute())
        if res.data:
            return json.loads(res.data[0]['valore'])
        return default
    except Exception:
        return default


def carica_tutti_params() -> dict:
    """Carica tutti i parametri salvati."""
    try:
        client = get_client()
        res = client.table('config_params').select('chiave, valore').execute()
        if not res.data:
            return {}
        return {r['chiave']: json.loads(r['valore']) for r in res.data}
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────
# SETUP INIZIALE — eseguire una volta
# ─────────────────────────────────────────────────────────────

def test_connessione() -> bool:
    """Verifica che la connessione a Supabase funzioni."""
    try:
        client = get_client()
        client.table('config_params').select('chiave').limit(1).execute()
        print("  [+] Connessione Supabase OK")
        return True
    except Exception as e:
        print(f"  [!] Connessione Supabase FALLITA: {e}")
        return False


if __name__ == "__main__":
    print("=== Test connessione Supabase ===")
    print("\nSQL da eseguire su Supabase SQL Editor (una volta sola):")
    print(SCHEMA_SQL)
