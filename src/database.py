"""
Database layer — Supabase (Postgres hosted).
Gestisce la persistenza automatica di tutti i dati tra sessioni.
Funziona identicamente su Mac e Windows senza installare nulla in locale.
"""

import os
import json
import time
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
               .limit(giorni + 10)
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
# ASSET ILLIQUIDI (blocked_assets_log)
# ─────────────────────────────────────────────────────────────

def carica_blocked_assets_correnti() -> list:
    """Restituisce l'ultima valorizzazione per ciascun asset illiquido."""
    try:
        client = get_client()
        # un asset per volta: l'ultima valorizzazione di ognuno, senza troncare
        # per colpa delle righe piu' recenti degli altri asset
        ids = (client.table('blocked_assets_log').select('asset_id')
               .limit(5000).execute())
        out = []
        for aid in sorted({r['asset_id'] for r in (ids.data or [])}):
            r = (client.table('blocked_assets_log')
                 .select('asset_id, asset_nome, asset_tipo, valore, data, nota')
                 .eq('asset_id', aid).order('data', desc=True).limit(1).execute())
            if r.data:
                out.append(r.data[0])
        return out
    except Exception as e:
        print(f"  [!] Errore caricamento blocked assets: {type(e).__name__}")
        return []


def salva_blocked_asset(asset_id: str, asset_nome: str, asset_tipo: str,
                         valore: float, data: str = None, nota: str = None) -> bool:
    """Upsert valorizzazione asset illiquido (conflict su data + asset_id)."""
    try:
        client = get_client()
        record = {
            'data': data or date.today().isoformat(),
            'asset_id': asset_id,
            'asset_nome': asset_nome,
            'asset_tipo': asset_tipo,
            'valore': float(valore),
            'nota': nota,
        }
        client.table('blocked_assets_log').upsert(record, on_conflict='data,asset_id').execute()
        return True
    except Exception as e:
        print(f"  [!] Errore salvataggio blocked asset: {type(e).__name__}")
        return False


def carica_blocked_assets_log(giorni: int = 730) -> pd.DataFrame:
    """Carica storico valorizzazioni asset illiquidi degli ultimi N giorni."""
    try:
        client = get_client()
        data_inizio = (date.today() - pd.Timedelta(days=giorni)).isoformat()
        res = (client.table('blocked_assets_log')
               .select('data, asset_id, asset_nome, asset_tipo, valore, nota')
               .gte('data', data_inizio)
               .order('data')
               .limit(giorni * 10)
               .execute())
        if not res.data:
            return pd.DataFrame()
        df = pd.DataFrame(res.data)
        df['data'] = pd.to_datetime(df['data'])
        df['valore'] = pd.to_numeric(df['valore'], errors='coerce')
        return df
    except Exception as e:
        print(f"  [!] Errore caricamento log blocked assets: {type(e).__name__}")
        return pd.DataFrame()


def elimina_blocked_asset(asset_id: str) -> bool:
    """Elimina tutte le valorizzazioni di un asset illiquido (rimozione dal catalogo)."""
    try:
        client = get_client()
        client.table('blocked_assets_log').delete().eq('asset_id', asset_id).execute()
        return True
    except Exception as e:
        print(f"  [!] Errore eliminazione blocked asset: {type(e).__name__}")
        return False


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
               .limit(giorni + 5)
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
                # NULL: la categoria viene assegnata dalla Feature #16 (taxonomy);
                # l'etichetta legacy si ricalcola in lettura da raw_categoria + descrizione
                'categoria': None,
                'conto': str(row['account']),
                'raw_categoria': str(row.get('raw_category', '')),
                'hash_tx': hash_tx
            })

        if not records:
            return 0
        # Batch upsert con ignore_duplicates (ON CONFLICT DO NOTHING su hash_tx); response.data contiene solo le righe inserite
        # (supabase-py >= 2.x: insert() non accetta più ignore_duplicates)
        resp = (client.table('transazioni')
                .upsert(records, on_conflict='hash_tx', ignore_duplicates=True)
                .execute())
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
               .select('data, importo, descrizione, categoria, raw_categoria, conto')
               .gte('data', data_inizio)
               .order('data', desc=True)
               .limit(5000)
               .execute())
        if not res.data:
            return pd.DataFrame()
        df = pd.DataFrame(res.data)
        df['date'] = pd.to_datetime(df['data'])
        df['amount'] = pd.to_numeric(df['importo'], errors='coerce')
        df['description'] = df['descrizione']
        # categoria: nome taxonomy se assegnata, altrimenti etichetta legacy ricalcolata
        from adapters import categorizza_legacy
        cat_nomi = {c['id']: c['nome'] for c in carica_taxonomy().get('categorie', [])}
        df['category'] = [
            cat_nomi.get(cat, cat) if isinstance(cat, str) and cat
            else categorizza_legacy(raw if isinstance(raw, str) else '',
                                    desc if isinstance(desc, str) else '')
            for cat, raw, desc in zip(df['categoria'], df['raw_categoria'], df['descrizione'])
        ]
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


_PBKDF2_ITER        = 600_000   # OWASP 2023 per PBKDF2-HMAC-SHA256
_PBKDF2_ITER_LEGACY = 260_000   # hash a 4 campi senza iterazioni esplicite


def hash_password(pwd: str) -> str:
    """PBKDF2-HMAC-SHA256 con salt casuale. Formato: 'pbkdf2$sha256$<iter>$<salt>$<hash>'.
    Il formato legacy a 4 campi ('pbkdf2$sha256$<salt>$<hash>', 260k iter) resta verificabile."""
    import hashlib, os as _os
    salt = _os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac('sha256', pwd.encode('utf-8'), salt.encode(), _PBKDF2_ITER)
    return f"pbkdf2$sha256${_PBKDF2_ITER}${salt}${h.hex()}"


def verify_password(pwd: str, stored: str) -> bool:
    """
    Verifica la password contro il valore salvato in DB.
    Supporta sia hash PBKDF2 (formato 'pbkdf2$...') sia plaintext legacy
    per consentire la migrazione trasparente al primo login.
    """
    import hashlib, hmac as _hmac
    pwd = pwd[:1024]  # guard DoS: PBKDF2 scala con la lunghezza dell'input
    if stored and stored.startswith('pbkdf2$'):
        parts = stored.split('$')
        if len(parts) == 4:
            _, algo, salt, expected = parts
            iters = _PBKDF2_ITER_LEGACY
        elif len(parts) == 5 and parts[2].isdigit():
            _, algo, _it, salt, expected = parts
            iters = int(_it)
            if not 100_000 <= iters <= 2_000_000:   # guard DoS da hash manomesso
                return False
        else:
            return False
        if algo not in ('sha256', 'sha512'):
            return False
        h = hashlib.pbkdf2_hmac(algo, pwd.encode('utf-8'), salt.encode(), iters)
        return _hmac.compare_digest(h.hex(), expected)
    # Fallback plaintext per migrazione (prima modifica password)
    return _hmac.compare_digest(pwd.encode('utf-8'), (stored or '').encode('utf-8'))


_FAKE_HASH = f"pbkdf2$sha256${_PBKDF2_ITER}$fakesalt$" + "a" * 64


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
               .limit(200)
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
        record = dict(asset)
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
    for row in df.to_dict('records'):
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
# AI SPENDING ANALYSIS — Taxonomy (config_params.taxonomy)
# ─────────────────────────────────────────────────────────────

_DEFAULT_TAXONOMY: dict = {
    "categorie": [
        {"id": "casa", "nome": "Casa", "icona": "🏠", "attiva": True, "sottocategorie": [
            {"id": "mutuo", "nome": "Mutuo", "attiva": True},
            {"id": "utenze", "nome": "Utenze", "attiva": True},
            {"id": "condominio", "nome": "Condominio", "attiva": True},
            {"id": "manutenzione", "nome": "Manutenzione", "attiva": True},
            {"id": "pulizie", "nome": "Pulizie", "attiva": True},
        ]},
        {"id": "alimentari", "nome": "Alimentari", "icona": "🛒", "attiva": True, "sottocategorie": [
            {"id": "supermercato", "nome": "Supermercato", "attiva": True},
            {"id": "mercato", "nome": "Mercato", "attiva": True},
            {"id": "delivery_spesa", "nome": "Delivery spesa", "attiva": True},
        ]},
        {"id": "ristorazione", "nome": "Ristorazione", "icona": "🍽️", "attiva": True, "sottocategorie": [
            {"id": "ristorante", "nome": "Ristorante/Pizzeria", "attiva": True},
            {"id": "bar", "nome": "Bar/Caffè", "attiva": True},
            {"id": "delivery_cibo", "nome": "Delivery cibo", "attiva": True},
        ]},
        {"id": "trasporti", "nome": "Trasporti", "icona": "🚗", "attiva": True, "sottocategorie": [
            {"id": "carburante", "nome": "Carburante", "attiva": True},
            {"id": "assicurazione_auto", "nome": "Assicurazione/Bollo", "attiva": True},
            {"id": "manutenzione_auto", "nome": "Manutenzione auto", "attiva": True},
            {"id": "mezzi_pubblici", "nome": "Mezzi pubblici", "attiva": True},
            {"id": "taxi", "nome": "Taxi/Ride", "attiva": True},
            {"id": "parcheggio", "nome": "Parcheggio", "attiva": True},
        ]},
        {"id": "salute", "nome": "Salute", "icona": "🏥", "attiva": True, "sottocategorie": [
            {"id": "farmacia", "nome": "Farmacia", "attiva": True},
            {"id": "medico", "nome": "Medico/Specialista", "attiva": True},
            {"id": "dentista", "nome": "Dentista", "attiva": True},
            {"id": "palestra", "nome": "Palestra", "attiva": True},
        ]},
        {"id": "figlio", "nome": "Figlio", "icona": "👶", "attiva": True, "sottocategorie": [
            {"id": "asilo", "nome": "Asilo/Nido", "attiva": True},
            {"id": "pediatra", "nome": "Pediatra", "attiva": True},
            {"id": "abbigliamento_bimbo", "nome": "Abbigliamento bimbo", "attiva": True},
            {"id": "giocattoli", "nome": "Giocattoli", "attiva": True},
            {"id": "babysitter", "nome": "Baby sitter", "attiva": True},
        ]},
        {"id": "abbonamenti", "nome": "Abbonamenti", "icona": "📱", "attiva": True, "sottocategorie": [
            {"id": "streaming", "nome": "Streaming", "attiva": True},
            {"id": "telefono", "nome": "Telefono", "attiva": True},
            {"id": "internet", "nome": "Internet", "attiva": True},
            {"id": "assicurazioni", "nome": "Assicurazioni", "attiva": True},
        ]},
        {"id": "shopping", "nome": "Shopping", "icona": "🛍️", "attiva": True, "sottocategorie": [
            {"id": "abbigliamento", "nome": "Abbigliamento", "attiva": True},
            {"id": "elettronica", "nome": "Elettronica", "attiva": True},
            {"id": "casa_arredo", "nome": "Casa/Arredo", "attiva": True},
        ]},
        {"id": "vacanze", "nome": "Vacanze & Viaggi", "icona": "✈️", "attiva": True, "sottocategorie": [
            {"id": "voli_treni", "nome": "Voli/Treni", "attiva": True},
            {"id": "hotel", "nome": "Hotel/Alloggio", "attiva": True},
            {"id": "attivita_viaggio", "nome": "Attività", "attiva": True},
            {"id": "documenti_viaggio", "nome": "Documenti viaggio", "attiva": True},
        ]},
        {"id": "regali", "nome": "Regali & Donazioni", "icona": "🎁", "attiva": True, "sottocategorie": [
            {"id": "regali_persone", "nome": "Regali", "attiva": True},
            {"id": "donazioni", "nome": "Donazioni", "attiva": True},
        ]},
        {"id": "intrattenimento", "nome": "Intrattenimento", "icona": "🎬", "attiva": True, "sottocategorie": [
            {"id": "cinema_teatro", "nome": "Cinema/Teatro", "attiva": True},
            {"id": "sport_hobby", "nome": "Sport/Hobby", "attiva": True},
            {"id": "libri_corsi", "nome": "Libri/Corsi", "attiva": True},
        ]},
        {"id": "istruzione", "nome": "Istruzione", "icona": "📚", "attiva": True, "sottocategorie": [
            {"id": "corsi", "nome": "Corsi", "attiva": True},
            {"id": "materiale", "nome": "Materiale didattico", "attiva": True},
        ]},
        {"id": "risparmio_invest", "nome": "Risparmio/Investimenti", "icona": "💰", "attiva": True, "sottocategorie": [
            {"id": "pac_etf", "nome": "PAC/ETF", "attiva": True},
            {"id": "fondi", "nome": "Fondi", "attiva": True},
            {"id": "pensione", "nome": "Pensione integrativa", "attiva": True},
            {"id": "liquidita", "nome": "Liquidità", "attiva": True},
        ]},
        {"id": "trasferimento_interno", "nome": "Trasferimento interno", "icona": "🔄",
         "attiva": True, "sottocategorie": []},
        {"id": "altro", "nome": "Altro", "icona": "❓", "attiva": True, "sottocategorie": []},
    ],
    "tag": ["una_tantum", "rimborsabile", "lavoro", "straordinario"],
}


def carica_taxonomy() -> dict:
    """Carica taxonomy da config_params.taxonomy; usa default se non ancora salvata."""
    import copy
    val = carica_param('taxonomy')
    if val and isinstance(val, dict) and 'categorie' in val:
        return val
    return copy.deepcopy(_DEFAULT_TAXONOMY)


def salva_taxonomy(taxonomy: dict) -> bool:
    """Salva taxonomy in config_params.taxonomy."""
    return salva_param('taxonomy', taxonomy)


def aggiorna_categoria_tx(hash_tx: str, categoria: str,
                           sottocategoria: str = None,
                           tag: list = None,
                           confermata: bool = False) -> bool:
    """Aggiorna categoria, sottocategoria, tag e stato di conferma di una transazione."""
    try:
        client = get_client()
        client.table('transazioni').update({
            'categoria': categoria,
            'sottocategoria': sottocategoria,
            'tag': tag or [],
            'categoria_confermata': confermata,
        }).eq('hash_tx', hash_tx).execute()
        return True
    except Exception as e:
        print(f"  [!] Errore aggiornamento categoria tx: {type(e).__name__}")
        return False


def aggiorna_categoria_tx_bulk(righe: list) -> tuple:
    """
    Aggiorna in blocco categoria/sottocategoria/tag di più transazioni.
    `righe`: [{hash_tx, categoria, sottocategoria, tag}] — vengono raggruppate per
    valori identici e aggiornate con una sola UPDATE ... IN (...) per gruppo
    (invece di una richiesta per transazione). Imposta categoria_confermata=True.
    Restituisce (salvate, errori).
    """
    gruppi: dict = {}
    for r in righe:
        chiave = (r['categoria'], r.get('sottocategoria') or None,
                  tuple(r.get('tag') or ()))
        gruppi.setdefault(chiave, []).append(r['hash_tx'])

    salvate = errori = 0
    try:
        client = get_client()
    except Exception as e:
        print(f"  [!] Errore client bulk categoria: {type(e).__name__}")
        return 0, len(righe)
    for (cat, sub, tag), hashes in gruppi.items():
        for i in range(0, len(hashes), 100):  # chunk: limite lunghezza URL
            chunk = hashes[i:i + 100]
            try:
                client.table('transazioni').update({
                    'categoria': cat,
                    'sottocategoria': sub,
                    'tag': list(tag),
                    'categoria_confermata': True,
                }).in_('hash_tx', chunk).execute()
                salvate += len(chunk)
            except Exception as e:
                print(f"  [!] Errore aggiornamento categoria bulk: {type(e).__name__}")
                errori += len(chunk)
    return salvate, errori


# ─────────────────────────────────────────────────────────────
# AI SPENDING ANALYSIS — Profilo famiglia
# ─────────────────────────────────────────────────────────────

def carica_profilo_famiglia() -> list:
    """
    Carica lo storico del profilo famiglia da config_params.profilo_famiglia_storia.
    Restituisce lista di dict [{tipo_famiglia, area_geografica, num_adulti, num_figli,
    fascia_eta, dal, al}] ordinata per dal DESC. al=None significa periodo attivo.
    """
    val = carica_param('profilo_famiglia_storia')
    if val and isinstance(val, list):
        return sorted(val, key=lambda x: x.get('dal', ''), reverse=True)
    return []


def salva_profilo_famiglia(storia: list) -> bool:
    """Salva lo storico del profilo famiglia in config_params.profilo_famiglia_storia."""
    return salva_param('profilo_famiglia_storia', storia)


def get_profilo_attivo(storia: list, per_data: str = None) -> Optional[dict]:
    """
    Restituisce il profilo attivo per la data indicata (default oggi).
    'Attivo' = al è None, oppure dal <= per_data <= al.
    """
    if not storia:
        return None
    target = per_data or date.today().isoformat()
    for entry in sorted(storia, key=lambda x: x.get('dal', ''), reverse=True):
        dal = entry.get('dal', '')
        al  = entry.get('al')
        if dal <= target and (al is None or al >= target):
            return entry
    return None


# AI SPENDING ANALYSIS — Nomi da anonimizzare
def carica_nomi_anonimizzare() -> list:
    """Lista nomi (stringhe) da sostituire con [NOME] prima di ogni chiamata AI."""
    val = carica_param('nomi_da_anonimizzare')
    if val and isinstance(val, list):
        return [str(n) for n in val if n]
    return []


def salva_nomi_anonimizzare(nomi: list) -> bool:
    """Salva lista nomi in config_params.nomi_da_anonimizzare."""
    return salva_param('nomi_da_anonimizzare', [str(n) for n in nomi if n])


# AI SPENDING ANALYSIS — Carica transazioni senza categoria
def carica_tx_senza_categoria(limit: int = 500) -> list:
    """
    Restituisce le transazioni (dict) prive di categoria, ordinate per data DESC.
    Esclude importi positivi (accrediti) già classificati come reddito.
    """
    try:
        client = get_client()
        res = (client.table('transazioni')
               .select('hash_tx, data, importo, descrizione, conto')
               .is_('categoria', 'null')
               .order('data', desc=True)
               .limit(limit)
               .execute())
        return res.data or []
    except Exception as e:
        print(f"  [!] Errore carica_tx_senza_categoria: {type(e).__name__}")
        return []


UNCATEGORIZED_MONTHLY_RPC_SQL = """
-- Eseguire una volta nel SQL Editor di Supabase (opzionale, ottimizzazione)
CREATE OR REPLACE FUNCTION public.get_uncategorized_monthly()
RETURNS TABLE(anno int, mese int, conteggio bigint)
LANGUAGE sql STABLE SECURITY INVOKER AS $$
  SELECT EXTRACT(YEAR FROM data)::int, EXTRACT(MONTH FROM data)::int, COUNT(*)
  FROM transazioni WHERE categoria IS NULL AND importo < 0
  GROUP BY 1, 2;
$$;
"""


def carica_riepilogo_mensile_senza_categoria(limit: int = 5000) -> list:
    """
    Restituisce [{anno, mese, label, conteggio}] per mesi con uscite non categorizzate.
    Ordinate dalla più recente alla più vecchia.
    """
    rows = None
    try:
        # RPC lato DB (GROUP BY): vedi UNCATEGORIZED_MONTHLY_RPC_SQL
        res = get_client().rpc('get_uncategorized_monthly', {}).execute()
        if res.data is not None:
            rows = res.data
    except Exception:
        rows = None  # RPC non ancora creata → fallback

    conteggi: dict = {}
    if rows is not None:
        for r in rows:
            try:
                conteggi[(int(r['anno']), int(r['mese']))] = int(r['conteggio'])
            except Exception:
                continue
    else:
        try:
            res = (get_client().table('transazioni')
                   .select('data')
                   .is_('categoria', 'null')
                   .lt('importo', 0)
                   .order('data', desc=True)
                   .limit(limit)
                   .execute())
        except Exception as e:
            print(f"  [!] Errore carica_riepilogo_mensile_senza_categoria: {type(e).__name__}")
            return []
        for r in res.data or []:
            try:
                d = date.fromisoformat(str(r['data']))
                conteggi[(d.year, d.month)] = conteggi.get((d.year, d.month), 0) + 1
            except Exception:
                continue

    mesi_it = ['', 'Gen', 'Feb', 'Mar', 'Apr', 'Mag', 'Giu',
               'Lug', 'Ago', 'Set', 'Ott', 'Nov', 'Dic']
    return [
        {'anno': anno, 'mese': mese,
         'label': f"{mesi_it[mese]} {anno}",
         'conteggio': cnt}
        for (anno, mese), cnt in sorted(conteggi.items(), reverse=True)
    ]


def carica_tx_senza_categoria_per_mese(anno: int, mese: int, limit: int = 500) -> list:
    """Uscite senza categoria per un dato anno/mese, ordinate per data DESC."""
    import calendar
    data_da = f"{anno}-{mese:02d}-01"
    data_a  = f"{anno}-{mese:02d}-{calendar.monthrange(anno, mese)[1]:02d}"
    try:
        client = get_client()
        res = (client.table('transazioni')
               .select('hash_tx, data, importo, descrizione, conto')
               .is_('categoria', 'null')
               .lt('importo', 0)
               .gte('data', data_da)
               .lte('data', data_a)
               .order('data', desc=True)
               .limit(limit)
               .execute())
        return res.data or []
    except Exception as e:
        print(f"  [!] Errore carica_tx_senza_categoria_per_mese: {type(e).__name__}")
        return []


def carica_spese_categorizzate(limit: int = 2000) -> list:
    """Transazioni con categoria valorizzata, ordinate per data DESC."""
    try:
        client = get_client()
        res = (client.table('transazioni')
               .select('hash_tx, data, importo, categoria, sottocategoria, conto')
               .not_.is_('categoria', 'null')
               .order('data', desc=True)
               .limit(limit)
               .execute())
        return res.data or []
    except Exception as e:
        print(f"  [!] Errore carica_spese_categorizzate: {type(e).__name__}")
        return []


# ─────────────────────────────────────────────────────────────
# GESTIONE UTENTI — tabella app_users
# ─────────────────────────────────────────────────────────────

APP_USERS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS app_users (
    id                  BIGSERIAL PRIMARY KEY,
    username            TEXT UNIQUE NOT NULL,
    email               TEXT UNIQUE NOT NULL,
    password_hash       TEXT NOT NULL,
    is_admin            BOOLEAN DEFAULT FALSE,
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    last_login          TIMESTAMPTZ,
    reset_token_hash    TEXT,
    reset_token_expiry  TIMESTAMPTZ,
    invite_token_hash   TEXT,
    invite_token_expiry TIMESTAMPTZ,
    totp_secret         TEXT,
    totp_enabled        BOOLEAN DEFAULT FALSE
);
ALTER TABLE app_users ENABLE ROW LEVEL SECURITY;
CREATE POLICY IF NOT EXISTS "service_only" ON app_users
    FOR ALL USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');
"""


def conta_utenti() -> int:
    """Restituisce il numero di utenti presenti in app_users. Propaga eccezioni."""
    client = get_client()
    res = client.table('app_users').select('id', count='exact').execute()
    return res.count or 0


def crea_utente(username: str, email: str, password_hash: str,
                is_admin: bool = False) -> dict:
    """
    Crea un nuovo utente attivo. Restituisce il record creato.
    Propaga eccezioni su username/email duplicato.
    """
    client = get_client()
    record = {
        'username': username.strip().lower(),
        'email': email.strip().lower(),
        'password_hash': password_hash,
        'is_admin': is_admin,
        'is_active': True,
    }
    res = client.table('app_users').insert(record).execute()
    return res.data[0]


def verifica_credenziali(username: str, password: str) -> Optional[dict]:
    """
    Verifica username + password. Restituisce il record utente o None.
    Aggiorna last_login in caso di successo.
    """
    try:
        client = get_client()
        res = (client.table('app_users')
               .select('id, username, email, is_admin, is_active, password_hash, totp_enabled')
               .eq('username', username.strip().lower())
               .eq('is_active', True)
               .limit(1)
               .execute())
        # Timing guard: PBKDF2 runs unconditionally — real hash if user found,
        # fake hash otherwise — uniformizes user-found vs. user-not-found paths.
        user_data = res.data[0] if res.data else None
        stored = user_data['password_hash'] if user_data else _FAKE_HASH
        if user_data and not (stored or '').startswith('pbkdf2$'):
            # Legacy in chiaro: confronto costante + un solo PBKDF2 fittizio,
            # cosi' il costo resta pari a quello del percorso con hash.
            verify_password(password, _FAKE_HASH)
        if not verify_password(password, stored) or user_data is None:
            return None
        # S-NEW-06: auto-rehash plaintext legacy al primo login riuscito
        if user_data.get('password_hash') and not user_data['password_hash'].startswith('pbkdf2$'):
            try:
                aggiorna_password_utente(user_data['id'], hash_password(password))
            except Exception:
                pass
        aggiorna_ultimo_login(user_data['id'])
        return user_data
    except Exception as e:
        # Timing guard on exception path (masks fast-fail errors; not slow DB timeouts).
        verify_password(password, _FAKE_HASH)
        print(f"  [!] verifica_credenziali: {type(e).__name__}")
        return None


_FAIL_STATE_CLEAN = {"n": 0, "locked_until": 0.0}
_FAIL_STATE_LOCKED_ON_ERROR_S = 30
_FAIL_STATE_TTL_S = 24 * 3600


def _read_fail_state(key: str, label: str) -> dict:
    """
    Legge un contatore di fallimenti da config_params.
    Fail-closed: su errore DB restituisce un blocco breve (senza DB il login
    non puo' comunque riuscire, quindi non c'e' costo per l'utente legittimo).
    """
    import time as _time
    try:
        res = (get_client().table('config_params')
               .select('valore')
               .eq('chiave', key)
               .limit(1)
               .execute())
        if res.data:
            try:
                return json.loads(res.data[0]['valore'])
            except (json.JSONDecodeError, TypeError):
                pass
        return dict(_FAIL_STATE_CLEAN)
    except Exception as e:
        print(f"  [!] {label}: {type(e).__name__}")
        return {"n": 0, "locked_until": _time.time() + _FAIL_STATE_LOCKED_ON_ERROR_S}


def _write_fail_state(key: str, n: int, locked_until: float, label: str) -> None:
    try:
        get_client().table('config_params').upsert(
            {'chiave': key,
             'valore': json.dumps({"n": n, "locked_until": locked_until}),
             'updated_at': datetime.now().isoformat()},
            on_conflict='chiave'
        ).execute()
    except Exception as e:
        print(f"  [!] {label}: {type(e).__name__}")


def _prune_login_fail_rows() -> None:
    """Elimina i contatori login piu' vecchi di 24h (evita crescita illimitata
    di config_params per username inesistenti tentati da un attaccante)."""
    try:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(seconds=_FAIL_STATE_TTL_S)).isoformat()
        (get_client().table('config_params').delete()
         .like('chiave', r'\_auth\_fail\_%')
         .lt('updated_at', cutoff)
         .execute())
    except Exception as e:
        print(f"  [!] _prune_login_fail_rows: {type(e).__name__}")


def get_server_login_state(username: str) -> dict:
    """Contatore fallimenti login server-side per username (fail-closed)."""
    return _read_fail_state(f"_auth_fail_{username.strip().lower()[:50]}",
                            "get_server_login_state")


def set_server_login_state(username: str, n: int, locked_until: float) -> None:
    """Scrive il contatore di fallimenti login server-side."""
    _write_fail_state(f"_auth_fail_{username.strip().lower()[:50]}",
                      n, locked_until, "set_server_login_state")
    if n > 0:
        _prune_login_fail_rows()


def reset_server_login_state(username: str) -> None:
    """Azzera il contatore server-side dopo un login riuscito."""
    set_server_login_state(username, 0, 0.0)


def get_server_totp_state(user_id: int) -> dict:
    """Contatore fallimenti TOTP server-side per user_id (fail-closed)."""
    return _read_fail_state(f"_totp_fail_{user_id}", "get_server_totp_state")


def set_server_totp_state(user_id: int, n: int, locked_until: float) -> None:
    """Scrive il contatore di fallimenti TOTP server-side."""
    _write_fail_state(f"_totp_fail_{user_id}", n, locked_until,
                      "set_server_totp_state")


def reset_server_totp_state(user_id: int) -> None:
    """Azzera il contatore TOTP server-side dopo verifica riuscita."""
    set_server_totp_state(user_id, 0, 0.0)


def get_server_pwdchg_state(user_id: int) -> dict:
    """Contatore fallimenti 'password attuale' nel cambio password (fail-closed)."""
    return _read_fail_state(f"_pwdchg_fail_{user_id}", "get_server_pwdchg_state")


def set_server_pwdchg_state(user_id: int, n: int, locked_until: float) -> None:
    _write_fail_state(f"_pwdchg_fail_{user_id}", n, locked_until, "set_server_pwdchg_state")


def totp_code_valido(user_id: int, secret: str, code: str) -> bool:
    """
    Verifica un codice TOTP con protezione anti-replay: lo stesso time-step non e'
    accettabile due volte (ultimo step usato in config_params). Fail-closed su errore DB.
    """
    import pyotp
    try:
        totp = pyotp.TOTP(secret)
        code = (code or '').strip()
        if not totp.verify(code):
            return False
        step = totp.timecode(datetime.now())
        key = f"_totp_last_{user_id}"
        res = (get_client().table('config_params').select('valore')
               .eq('chiave', key).limit(1).execute())
        last = -1
        if res.data:
            try:
                last = int(json.loads(res.data[0]['valore']))
            except (ValueError, TypeError, json.JSONDecodeError):
                last = -1
        if step <= last:
            return False
        get_client().table('config_params').upsert(
            {'chiave': key, 'valore': json.dumps(step),
             'updated_at': datetime.now().isoformat()},
            on_conflict='chiave').execute()
        return True
    except Exception as e:
        print(f"  [!] totp_code_valido: {type(e).__name__}")
        return False


def invalida_sessioni(user_id: int) -> None:
    """Segna ora come 'epoca sessioni': le sessioni aperte prima vengono chiuse alla prossima rivalidazione."""
    try:
        get_client().table('config_params').upsert(
            {'chiave': f"_sess_epoch_{user_id}", 'valore': json.dumps(time.time()),
             'updated_at': datetime.now().isoformat()},
            on_conflict='chiave').execute()
    except Exception as e:
        print(f"  [!] invalida_sessioni: {type(e).__name__}")


def get_session_epoch(user_id: int) -> float:
    """Epoca sessioni dell'utente (0.0 se assente o DB irraggiungibile: fail-open come is_utente_attivo)."""
    try:
        res = (get_client().table('config_params').select('valore')
               .eq('chiave', f"_sess_epoch_{user_id}").limit(1).execute())
        return float(json.loads(res.data[0]['valore'])) if res.data else 0.0
    except Exception as e:
        print(f"  [!] get_session_epoch: {type(e).__name__}")
        return 0.0


def reset_email_consentito(email: str, cooldown_s: int = 60) -> bool:
    """Rate limit server-side (per email) sulle richieste di reset: 1 ogni `cooldown_s` secondi."""
    import hashlib
    key = "_reset_req_" + hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]
    st_ = _read_fail_state(key, "reset_email_consentito")
    if time.time() < st_.get("locked_until", 0.0):
        return False
    _write_fail_state(key, 1, time.time() + cooldown_s, "reset_email_consentito")
    return True


def get_utente_by_email(email: str) -> Optional[dict]:
    """Restituisce il record utente per email, o None se non trovato."""
    try:
        client = get_client()
        res = (client.table('app_users')
               .select('id')
               .eq('email', email.strip().lower())
               .eq('is_active', True)
               .limit(1)
               .execute())
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"  [!] get_utente_by_email: {type(e).__name__}")
        return None


def imposta_reset_token(user_id: int, token_hash: str, expiry_iso: str) -> bool:
    """Salva il reset token (già hashato SHA-256) e la sua scadenza."""
    try:
        client = get_client()
        client.table('app_users').update({
            'reset_token_hash': token_hash,
            'reset_token_expiry': expiry_iso,
        }).eq('id', user_id).execute()
        return True
    except Exception as e:
        print(f"  [!] imposta_reset_token: {type(e).__name__}")
        return False


_ALLOWED_TOKEN_FIELDS = frozenset({
    'reset_token_hash', 'reset_token_expiry',
    'invite_token_hash', 'invite_token_expiry',
})


def verifica_token(token_hash: str, campo_hash: str, campo_expiry: str) -> Optional[dict]:
    """
    Verifica un token (reset o invite) per hash e scadenza.
    campo_hash / campo_expiry: nomi colonna in app_users.
    Restituisce il record utente o None.
    """
    # S-NEW-08: allowlist per evitare column injection via f-string
    if campo_hash not in _ALLOWED_TOKEN_FIELDS or campo_expiry not in _ALLOWED_TOKEN_FIELDS:
        print(f"  [!] verifica_token: campo non consentito: {campo_hash!r}, {campo_expiry!r}")
        return None
    try:
        client = get_client()
        res = (client.table('app_users')
               .select(f'id, username, email, {campo_expiry}')
               .eq(campo_hash, token_hash)
               .limit(1)
               .execute())
        if not res.data:
            return None
        user = res.data[0]
        expiry_str = user.get(campo_expiry)
        if not expiry_str:
            return None
        expiry = datetime.fromisoformat(expiry_str.replace('Z', '+00:00'))
        if datetime.now(expiry.tzinfo) > expiry:
            return None
        return user
    except Exception as e:
        print(f"  [!] verifica_token: {type(e).__name__}")
        return None


def get_password_hash_by_id(user_id: int) -> Optional[str]:
    """Restituisce il password_hash corrente dell'utente dato il suo id, o None in caso di errore."""
    try:
        client = get_client()
        res = (client.table('app_users')
               .select('password_hash')
               .eq('id', user_id)
               .limit(1)
               .execute())
        if res.data:
            return res.data[0].get('password_hash')
        return None
    except Exception as e:
        print(f"  [!] get_password_hash_by_id: {type(e).__name__}")
        return None


def aggiorna_password_utente(user_id: int, new_hash: str) -> bool:
    """Aggiorna la password e cancella il reset token."""
    try:
        client = get_client()
        client.table('app_users').update({
            'password_hash': new_hash,
            'reset_token_hash': None,
            'reset_token_expiry': None,
        }).eq('id', user_id).execute()
        return True
    except Exception as e:
        print(f"  [!] aggiorna_password_utente: {type(e).__name__}")
        return False


def aggiorna_ultimo_login(user_id: int) -> bool:
    """Aggiorna last_login per l'utente."""
    try:
        client = get_client()
        client.table('app_users').update({
            'last_login': datetime.now().isoformat(),
        }).eq('id', user_id).execute()
        return True
    except Exception as e:
        print(f"  [!] aggiorna_ultimo_login: {type(e).__name__}")
        return False


def crea_utente_invito(username: str, email: str, token_hash: str,
                       expiry_iso: str, is_admin: bool = False) -> Optional[dict]:
    """
    Crea un utente inattivo con invite token.
    L'utente diventa attivo dopo aver impostato la password via link email.
    """
    try:
        client = get_client()
        record = {
            'username': username.strip().lower(),
            'email': email.strip().lower(),
            'password_hash': '',
            'is_admin': is_admin,
            'is_active': False,
            'invite_token_hash': token_hash,
            'invite_token_expiry': expiry_iso,
        }
        res = client.table('app_users').insert(record).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"  [!] crea_utente_invito: {type(e).__name__}")
        return None


def attiva_utente_invito(user_id: int, password_hash: str) -> bool:
    """Attiva l'account: imposta password e is_active=True, cancella invite token."""
    try:
        client = get_client()
        client.table('app_users').update({
            'password_hash': password_hash,
            'is_active': True,
            'invite_token_hash': None,
            'invite_token_expiry': None,
        }).eq('id', user_id).execute()
        return True
    except Exception as e:
        print(f"  [!] attiva_utente_invito: {type(e).__name__}")
        return False


def get_tutti_utenti() -> pd.DataFrame:
    """Restituisce lista utenti per il pannello admin."""
    try:
        client = get_client()
        res = (client.table('app_users')
               .select('id, username, email, is_admin, is_active, totp_enabled, created_at, last_login')
               .order('created_at')
               .limit(200)
               .execute())
        return pd.DataFrame(res.data) if res.data else pd.DataFrame()
    except Exception as e:
        print(f"  [!] get_tutti_utenti: {type(e).__name__}")
        return pd.DataFrame()


def is_utente_attivo(user_id: int) -> bool:
    """S-NEW-07: verifica se un utente è ancora attivo. Fail-open: True se il DB non risponde."""
    try:
        client = get_client()
        res = (client.table('app_users')
               .select('is_active')
               .eq('id', user_id)
               .eq('is_active', True)
               .limit(1)
               .execute())
        return bool(res.data)
    except Exception as e:
        print(f"  [!] is_utente_attivo: {type(e).__name__}")
        return True  # fail-open: non troncare la sessione se il DB è irraggiungibile


def set_utente_attivo(user_id: int, attivo: bool) -> bool:
    """Attiva o disattiva un utente (non cancella il record)."""
    try:
        client = get_client()
        client.table('app_users').update({'is_active': attivo}).eq('id', user_id).execute()
        return True
    except Exception as e:
        print(f"  [!] set_utente_attivo: {type(e).__name__}")
        return False


def elimina_utente(user_id: int) -> bool:
    """Elimina definitivamente un utente dal DB."""
    try:
        client = get_client()
        client.table('app_users').delete().eq('id', user_id).execute()
        return True
    except Exception as e:
        print(f"  [!] elimina_utente: {type(e).__name__}")
        return False


def salva_totp_secret(user_id: int, secret: str) -> bool:
    """Salva il secret TOTP e abilita 2FA per l'utente."""
    try:
        client = get_client()
        client.table('app_users').update({
            'totp_secret': secret,
            'totp_enabled': True,
        }).eq('id', user_id).execute()
        return True
    except Exception as e:
        print(f"  [!] salva_totp_secret: {type(e).__name__}")
        return False


def get_totp_info(user_id: int) -> dict:
    """Restituisce totp_enabled e totp_secret per un utente."""
    try:
        client = get_client()
        res = (client.table('app_users')
               .select('totp_enabled, totp_secret')
               .eq('id', user_id)
               .limit(1)
               .execute())
        if res.data:
            return res.data[0]
        return {'totp_enabled': False, 'totp_secret': None}
    except Exception as e:
        print(f"  [!] get_totp_info: {type(e).__name__}")
        return {'totp_enabled': False, 'totp_secret': None}


def disabilita_totp(user_id: int) -> bool:
    """Disabilita il 2FA e cancella il secret TOTP."""
    try:
        client = get_client()
        client.table('app_users').update({
            'totp_secret': None,
            'totp_enabled': False,
        }).eq('id', user_id).execute()
        return True
    except Exception as e:
        print(f"  [!] disabilita_totp: {type(e).__name__}")
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
