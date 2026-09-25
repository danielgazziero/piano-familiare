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
        secret = st.secrets["SUPABASE_SECRET"]
        return url, secret
    except Exception:
        url    = os.environ.get("SUPABASE_URL", "")
        secret = os.environ.get("SUPABASE_SECRET", "")
        if not url:
            raise RuntimeError(
                "Credenziali Supabase mancanti. "
                "Configura SUPABASE_URL e SUPABASE_SECRET "
                "in .streamlit/secrets.toml o come variabili d'ambiente."
            )
        return url, secret


def _make_client() -> Client:
    url, secret = _get_credentials()
    return create_client(url, secret)


try:
    import streamlit as _st_db
    @_st_db.cache_resource
    def _build_client() -> Client:
        return _make_client()
except Exception:
    _build_client = _make_client


def get_client() -> Client:
    """Client Supabase con service key (singleton per sessione Streamlit)."""
    return _build_client()


# ─────────────────────────────────────────────────────────────
# SCHEMA SQL — eseguire una volta su Supabase SQL Editor
# ─────────────────────────────────────────────────────────────

ASSET_CATALOG_SCHEMA_SQL = """
-- Catalogo master di tutti gli asset (fonte di verità per l'app)
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
    note             TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE asset_catalog ENABLE ROW LEVEL SECURITY;
CREATE POLICY IF NOT EXISTS "service_only" ON asset_catalog
    FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
"""

SCHEMA_SQL = """
-- Snapshot giornaliero patrimonio
CREATE TABLE IF NOT EXISTS patrimonio_log (
    id          BIGSERIAL PRIMARY KEY,
    data        DATE NOT NULL DEFAULT CURRENT_DATE,
    fondi_bancari       NUMERIC,
    generali            NUMERIC,
    etf_persona1        NUMERIC,
    etf_figlio          NUMERIC,
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

-- RLS: abilita e permetti solo alla service key (la anon key non può accedere)
ALTER TABLE patrimonio_log     ENABLE ROW LEVEL SECURITY;
ALTER TABLE quote_fondi        ENABLE ROW LEVEL SECURITY;
ALTER TABLE transazioni        ENABLE ROW LEVEL SECURITY;
ALTER TABLE config_params      ENABLE ROW LEVEL SECURITY;

-- Policy: accesso esclusivo alla service key (bypassa RLS per definizione)
-- La anon key non soddisfa auth.role() = 'service_role' → accesso negato
CREATE POLICY "service_only" ON patrimonio_log
    FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "service_only" ON quote_fondi
    FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "service_only" ON transazioni
    FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "service_only" ON config_params
    FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');

-- MIGRAZIONE DB ESISTENTE: se le tabelle esistono già, esegui su Supabase SQL Editor:
-- DROP POLICY IF EXISTS "service_full_access" ON patrimonio_log;
-- DROP POLICY IF EXISTS "service_full_access" ON quote_fondi;
-- DROP POLICY IF EXISTS "service_full_access" ON transazioni;
-- DROP POLICY IF EXISTS "service_full_access" ON config_params;
-- CREATE POLICY "service_only" ON patrimonio_log FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
-- CREATE POLICY "service_only" ON quote_fondi   FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
-- CREATE POLICY "service_only" ON transazioni   FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
-- CREATE POLICY "service_only" ON config_params FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
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
            'etf_persona1': float(snapshot.get('etf_persona1', 0)),
            'etf_figlio': float(snapshot.get('etf_figlio', 0)),
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
        print(f"  [!] Errore salvataggio patrimonio: {type(e).__name__}")
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
        cols_num = ['fondi_bancari','generali','etf_persona1','etf_figlio',
                    'azioni_acn_usd','liquidita','totale_eur',
                    'totale_netto_fiscale','tassa_latente_fondi']
        for c in cols_num:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce')
        return df
    except Exception as e:
        print(f"  [!] Errore caricamento log patrimonio: {type(e).__name__}")
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
        print(f"  [!] Errore salvataggio quote fondi: {type(e).__name__}")
        return False


def carica_ultime_quote_fondi() -> pd.DataFrame:
    """
    Carica le ultime quote disponibili per ogni fondo
    (non necessariamente di oggi — prende l'ultimo valore noto).
    """
    try:
        client = get_client()
        # Prova prima via RPC get_latest_quotes() (DISTINCT ON lato DB — più efficiente)
        # SQL da creare su Supabase:
        #   CREATE OR REPLACE FUNCTION get_latest_quotes()
        #   RETURNS TABLE(isin text, nome text, quota numeric, valore numeric,
        #                 quantita numeric, data date, fonte text)
        #   LANGUAGE sql SECURITY INVOKER AS $$
        #     SELECT DISTINCT ON (isin) isin, nome, quota, valore, quantita, data, fonte
        #     FROM quote_fondi ORDER BY isin, data DESC;
        #   $$;
        try:
            res = client.rpc('get_latest_quotes', {}).execute()
            if res.data:
                df = pd.DataFrame(res.data)
                df['data'] = pd.to_datetime(df['data'])
                return df
        except Exception:
            pass
        # Fallback: query standard con groupby in Python
        data_dal = (date.today() - pd.Timedelta(days=365)).isoformat()
        res = (client.table('quote_fondi')
               .select('isin, nome, quota, valore, quantita, data, fonte')
               .gte('data', data_dal)
               .order('data', desc=True)
               .limit(5000)
               .execute())
        if not res.data:
            return pd.DataFrame()
        df = pd.DataFrame(res.data)
        df['data'] = pd.to_datetime(df['data'])
        df = df.groupby('isin').first().reset_index()
        return df
    except Exception as e:
        print(f"  [!] Errore caricamento quote fondi: {type(e).__name__}")
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
        print(f"  [!] Errore storico fondo {isin}: {type(e).__name__}")
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
        for row in df_tx.to_dict('records'):
            hash_str = f"{row['date'].date()}_{row['amount']}_{row['description'][:500]}_{row['account']}"
            hash_tx = hashlib.sha256(hash_str.encode()).hexdigest()
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
        print(f"  [!] Errore salvataggio transazioni: {type(e).__name__}")
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
        print(f"  [!] Errore caricamento transazioni: {type(e).__name__}")
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
        print(f"  [!] Errore salvataggio param {chiave}: {type(e).__name__}")
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
            raw = res.data[0]['valore']
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return raw  # valore inserito manualmente senza encoding JSON
        return default
    except Exception as e:
        print(f"  [!] carica_param({chiave!r}): {type(e).__name__}")
        return default


def carica_tutti_params() -> dict:
    """Carica tutti i parametri salvati."""
    try:
        client = get_client()
        res = client.table('config_params').select('chiave, valore').limit(2000).execute()
        if not res.data:
            return {}
        result = {}
        for r in res.data:
            raw = r['valore']
            try:
                result[r['chiave']] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                result[r['chiave']] = raw
        return result
    except Exception as e:
        print(f"  [!] carica_tutti_params: {type(e).__name__}")
        return {}


def hash_password(pwd: str) -> str:
    """PBKDF2-HMAC-SHA256 con salt casuale. Formato: 'pbkdf2$sha256$<salt>$<hash>'."""
    import hashlib, os as _os
    salt = _os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac('sha256', pwd.encode('utf-8'), salt.encode(), 260_000)
    return f"pbkdf2$sha256${salt}${h.hex()}"


def verify_password(pwd: str, stored: str) -> bool:
    """
    Verifica la password contro il valore salvato in DB.
    Supporta sia hash PBKDF2 (formato 'pbkdf2$...') sia plaintext legacy
    per consentire la migrazione trasparente al primo login.
    """
    import hashlib, hmac as _hmac
    if stored and stored.startswith('pbkdf2$'):
        parts = stored.split('$')
        if len(parts) != 4:
            return False
        _, algo, salt, expected = parts
        h = hashlib.pbkdf2_hmac(algo, pwd.encode('utf-8'), salt.encode(), 260_000)
        return _hmac.compare_digest(h.hex(), expected)
    # Fallback plaintext per migrazione (prima modifica password)
    import hmac as _hmac2
    return _hmac2.compare_digest(pwd, stored or '')


def salva_params_batch(params: dict) -> bool:
    """Salva più parametri in un unico batch upsert invece di N round-trip sequenziali."""
    if not params:
        return True
    try:
        client = get_client()
        now = datetime.now().isoformat()
        records = [{'chiave': k, 'valore': json.dumps(v), 'updated_at': now}
                   for k, v in params.items()]
        client.table('config_params').upsert(records, on_conflict='chiave').execute()
        return True
    except Exception as e:
        print(f"  [!] Errore salvataggio params batch: {type(e).__name__}")
        return False


def carica_param_o_errore(chiave: str) -> Any:
    """
    Come carica_param ma propaga l'eccezione se il DB non è raggiungibile.
    Usare per auth critica dove distinguere 'chiave assente' da 'DB down' è necessario.
    """
    client = get_client()
    res = (client.table('config_params')
           .select('valore')
           .eq('chiave', chiave)
           .execute())
    if res.data:
        raw = res.data[0]['valore']
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw
    return None


# ─────────────────────────────────────────────────────────────
# ASSET CATALOG — catalogo centralizzato degli asset
# ─────────────────────────────────────────────────────────────

def carica_asset_catalog() -> pd.DataFrame:
    """Carica tutti gli asset dal catalogo DB."""
    try:
        client = get_client()
        res = (client.table('asset_catalog')
               .select('*')
               .order('tipo')
               .order('nome')
               .execute())
        if not res.data:
            return pd.DataFrame()
        df = pd.DataFrame(res.data)
        if 'fallback_tickers' in df.columns:
            df['fallback_tickers'] = df['fallback_tickers'].apply(
                lambda x: x if isinstance(x, list) else (json.loads(x) if x else []))
        return df
    except Exception as e:
        print(f"  [!] Errore caricamento asset_catalog: {type(e).__name__}")
        return pd.DataFrame()


def upsert_asset(asset: dict) -> bool:
    """Inserisce o aggiorna un asset nel catalogo."""
    try:
        client = get_client()
        record = {k: v for k, v in asset.items()}
        fb = record.get('fallback_tickers', [])
        record['fallback_tickers'] = json.dumps(fb if isinstance(fb, list) else [])
        record['updated_at'] = datetime.now().isoformat()
        client.table('asset_catalog').upsert(record, on_conflict='isin').execute()
        return True
    except Exception as e:
        print(f"  [!] Errore upsert asset {asset.get('isin')}: {type(e).__name__}")
        return False


def elimina_asset(isin: str) -> bool:
    """Rimuove un asset dal catalogo."""
    try:
        client = get_client()
        client.table('asset_catalog').delete().eq('isin', isin).execute()
        return True
    except Exception as e:
        print(f"  [!] Errore eliminazione asset {isin}: {type(e).__name__}")
        return False


def carica_asset_tickers() -> tuple:
    """
    Restituisce (tickers_dict, fallbacks_dict) da asset_catalog.
    Fondi senza ticker_yf ottengono {isin}.MI come fallback.
    """
    df = carica_asset_catalog()
    if df.empty:
        return {}, {}
    tickers: dict = {}
    fallbacks: dict = {}
    for _, row in df.iterrows():
        isin = row.get('isin')
        if not isin:
            continue
        ticker = row.get('ticker_yf')
        if ticker:
            tickers[isin] = ticker
        elif row.get('tipo') == 'fondo':
            tickers[isin] = f'{isin}.MI'
        fb = row.get('fallback_tickers', [])
        if fb:
            fallbacks[isin] = fb
    return tickers, fallbacks


def inizializza_asset_catalog_da_config() -> bool:
    """Semina asset_catalog da config.yaml se la tabella è vuota (bootstrap una tantum)."""
    try:
        client = get_client()
        existing = client.table('asset_catalog').select('isin').limit(1).execute()
        if existing.data:
            return True

        import sys
        from pathlib import Path as _Path
        sys.path.insert(0, str(_Path(__file__).parent))
        from parser import load_config
        cfg = load_config()
        records = []

        for f in cfg.get('fondi_bancari', {}).get('titoli', []):
            if not f.get('isin'):
                continue
            records.append({
                'isin': f['isin'], 'nome': f['nome'], 'tipo': 'fondo',
                'ticker_yf': f.get('ticker_yf'),
                'fallback_tickers': json.dumps(f.get('fallback_tickers', [])),
                'ter': f.get('ter'), 'proprietario': 'persona1', 'stato': 'attivo',
                'valore_quota_ref': f.get('valore_quota_ref'),
                'data_ref': f.get('data_ref'), 'note': None,
            })

        for etf in cfg.get('etf', []):
            if not etf.get('isin'):
                continue
            records.append({
                'isin': etf['isin'], 'nome': etf['nome'], 'tipo': 'etf',
                'ticker_yf': etf.get('ticker_yf'), 'ticker_bi': etf.get('ticker_bi'),
                'fallback_tickers': '[]', 'ter': etf.get('ter'),
                'proprietario': etf.get('proprietario', 'persona1'),
                'stato': etf.get('stato', 'candidato'),
                'valore_iniziale': etf.get('valore_iniziale', 0),
                'note': etf.get('note'),
            })

        for az in cfg.get('azioni', []):
            if not az.get('isin'):
                continue
            records.append({
                'isin': az['isin'], 'nome': az['nome'], 'tipo': 'azione',
                'ticker_yf': az.get('ticker_yf'), 'fallback_tickers': '[]',
                'proprietario': 'persona1', 'stato': 'attivo',
                'note': az.get('note'),
            })

        if records:
            client.table('asset_catalog').insert(records).execute()
            print(f"  [+] asset_catalog: {len(records)} asset inizializzati da config.yaml")
        return True
    except Exception as e:
        print(f"  [!] Errore inizializzazione asset_catalog: {type(e).__name__}")
        return False


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
        print(f"  [!] Connessione Supabase FALLITA: {type(e).__name__}")
        return False


if __name__ == "__main__":
    print("=== Test connessione Supabase ===")
    print("\nSQL da eseguire su Supabase SQL Editor (una volta sola):")
    print(SCHEMA_SQL)
