"""
Modulo posizioni: gestisce le quantità degli asset nel tempo.
Ogni asset ha una storia di quantità — quando acquisti o vendi, aggiungi una riga.
Il valore giornaliero = quantità attiva quel giorno × prezzo di quel giorno.
"""

import pandas as pd
import numpy as np
from datetime import date, datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dateutil.relativedelta import relativedelta

from database import (get_client, salva_param, carica_param,
                       carica_asset_catalog, carica_asset_tickers)
from prices import scarica_tutti_storici, ASSET_TICKERS


def _build_quantita_arr(posizioni_parsed: list, dates_arr) -> np.ndarray:
    """Vettorizzato: assegna la quantità attiva per ogni data senza loop Python per giorno."""
    q = np.zeros(len(dates_arr), dtype=float)
    for d_inizio, d_fine, quantita in posizioni_parsed:
        mask = (dates_arr >= d_inizio) & (dates_arr <= d_fine)
        q[mask] = quantita
    return q


# Schema SQL per le tabelle posizioni e prezzi_storici
POSITIONS_SCHEMA_SQL = """
-- Storico quantità per ogni asset
CREATE TABLE IF NOT EXISTS posizioni (
    id              BIGSERIAL PRIMARY KEY,
    isin            TEXT NOT NULL,
    nome            TEXT,
    tipo            TEXT,           -- 'fondo', 'etf', 'azione'
    quantita        NUMERIC NOT NULL,
    data_inizio     DATE NOT NULL,  -- da quando vale questa quantità
    data_fine       DATE,           -- NULL = ancora attiva
    note            TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Prezzi storici giornalieri per ogni asset
CREATE TABLE IF NOT EXISTS prezzi_storici (
    id          BIGSERIAL PRIMARY KEY,
    isin        TEXT NOT NULL,
    data        DATE NOT NULL,
    prezzo      NUMERIC NOT NULL,
    fonte       TEXT DEFAULT 'yahoo',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(isin, data)
);

-- Ultima data scaricata per ogni asset (per il backfill)
CREATE TABLE IF NOT EXISTS backfill_stato (
    isin            TEXT PRIMARY KEY,
    ultima_data     DATE NOT NULL,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- RLS
ALTER TABLE posizioni         ENABLE ROW LEVEL SECURITY;
ALTER TABLE prezzi_storici    ENABLE ROW LEVEL SECURITY;
ALTER TABLE backfill_stato    ENABLE ROW LEVEL SECURITY;

CREATE POLICY IF NOT EXISTS "service_only" ON posizioni
    FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
CREATE POLICY IF NOT EXISTS "service_only" ON prezzi_storici
    FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
CREATE POLICY IF NOT EXISTS "service_only" ON backfill_stato
    FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
"""


# ─────────────────────────────────────────────────────────────
# GESTIONE POSIZIONI SU SUPABASE
# ─────────────────────────────────────────────────────────────

def carica_posizioni() -> pd.DataFrame:
    """Carica tutte le posizioni attive dal DB."""
    try:
        client = get_client()
        res = (client.table('posizioni')
               .select('*')
               .is_('data_fine', 'null')
               .order('isin')
               .execute())
        if not res.data:
            return pd.DataFrame()
        return pd.DataFrame(res.data)
    except Exception as e:
        print(f"  [!] Errore caricamento posizioni: {type(e).__name__}")
        return pd.DataFrame()


def inizializza_posizioni():
    """
    Inserisce le posizioni iniziali se la tabella è vuota.
    Legge da asset_catalog (DB) — non da config.yaml né da valori hardcoded.
    """
    try:
        client = get_client()
        existing = client.table('posizioni').select('id').limit(1).execute()
        if existing.data:
            print("  [i] Posizioni già inizializzate")
            return

        catalog = carica_asset_catalog()
        if catalog.empty:
            print("  [!] asset_catalog vuoto — impossibile inizializzare posizioni")
            return

        # Legge le quantità di avvio da config.yaml (solo per la prima inizializzazione)
        quantita_cfg: dict = {}
        data_acquisto_cfg: dict = {}
        try:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).parent))
            from parser import load_config
            cfg = load_config()
            for f in cfg.get('fondi_bancari', {}).get('titoli', []):
                if f.get('isin'):
                    quantita_cfg[f['isin']] = f.get('quantita', 0)
                    data_acquisto_cfg[f['isin']] = f.get('data_acquisto', '2020-01-01')
            for etf in cfg.get('etf', []):
                if etf.get('isin') and etf.get('in_portafoglio'):
                    quantita_cfg[etf['isin']] = etf.get('quantita', 0)
                    data_acquisto_cfg[etf['isin']] = etf.get('data_acquisto', '2024-01-01')
            for az in cfg.get('azioni', []):
                if az.get('isin'):
                    quantita_cfg[az['isin']] = az.get('quantita', 0)
                    data_acquisto_cfg[az['isin']] = az.get('data_acquisto', '2022-01-01')
        except Exception:
            pass

        records = []
        for _, row in catalog.iterrows():
            isin = row['isin']
            q = quantita_cfg.get(isin, 0)
            d = data_acquisto_cfg.get(isin, '2020-01-01')
            records.append({
                'isin': isin, 'nome': row['nome'], 'tipo': row['tipo'],
                'quantita': q, 'data_inizio': d, 'data_fine': None,
                'note': 'Posizione iniziale',
            })

        client.table('posizioni').insert(records).execute()
        print(f"  [+] {len(records)} posizioni inizializzate da asset_catalog")
    except Exception as e:
        print(f"  [!] Errore inizializzazione posizioni: {type(e).__name__}")


def aggiorna_quantita(isin: str, nuova_quantita: float,
                       data_modifica: date, note: str = None) -> bool:
    """
    Registra una modifica di quantità (acquisto parziale o dismissione).
    Chiude la posizione corrente e ne apre una nuova dalla data indicata.
    """
    try:
        client = get_client()

        # Recupera nome/tipo dalla posizione attiva nel DB
        nome = ''
        tipo = 'fondo'
        res_meta = (client.table('posizioni')
                    .select('nome, tipo')
                    .eq('isin', isin)
                    .is_('data_fine', 'null')
                    .limit(1)
                    .execute())
        if res_meta.data:
            nome = res_meta.data[0]['nome']
            tipo = res_meta.data[0]['tipo']

        data_fine_chiusura = (data_modifica - timedelta(days=1)).isoformat()

        # Chiudi posizione attiva
        client.table('posizioni').update({
            'data_fine': data_fine_chiusura
        }).eq('isin', isin).is_('data_fine', 'null').execute()

        try:
            client.table('posizioni').insert({
                'isin': isin,
                'nome': nome,
                'tipo': tipo,
                'quantita': nuova_quantita,
                'data_inizio': data_modifica.isoformat(),
                'data_fine': None,
                'note': note or f'Modifica quantità a {nuova_quantita}'
            }).execute()
        except Exception as ins_err:
            # Rollback compensativo: riapre la posizione chiusa
            try:
                client.table('posizioni').update({
                    'data_fine': None
                }).eq('isin', isin).eq('data_fine', data_fine_chiusura).execute()
            except Exception:
                pass
            raise ins_err

        print(f"  [+] Quantità {isin} aggiornata a {nuova_quantita} dal {data_modifica}")
        return True
    except Exception as e:
        print(f"  [!] Errore aggiornamento quantità: {type(e).__name__}")
        return False


def storico_posizioni(isin: str) -> pd.DataFrame:
    """Storico completo delle variazioni di quantità per un asset."""
    try:
        client = get_client()
        res = (client.table('posizioni')
               .select('*')
               .eq('isin', isin)
               .order('data_inizio')
               .execute())
        if not res.data:
            return pd.DataFrame()
        return pd.DataFrame(res.data)
    except Exception as e:
        print(f"  [!] Errore storico posizioni: {type(e).__name__}")
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────
# GESTIONE PREZZI E BACKFILL
# ─────────────────────────────────────────────────────────────

def get_ultima_data_scaricata(isin: str) -> Optional[date]:
    """Restituisce l'ultima data per cui abbiamo prezzi nel DB."""
    try:
        client = get_client()
        res = (client.table('backfill_stato')
               .select('ultima_data')
               .eq('isin', isin)
               .execute())
        if res.data:
            return date.fromisoformat(res.data[0]['ultima_data'])
        return None
    except Exception:
        return None


def set_ultima_data_scaricata(isin: str, ultima_data: date):
    """Aggiorna la data dell'ultimo prezzo scaricato."""
    try:
        client = get_client()
        client.table('backfill_stato').upsert({
            'isin': isin,
            'ultima_data': ultima_data.isoformat(),
            'updated_at': datetime.now().isoformat()
        }, on_conflict='isin').execute()
    except Exception as e:
        print(f"  [!] Errore aggiornamento backfill_stato {isin}: {type(e).__name__}")


def salva_prezzi(df: pd.DataFrame) -> int:
    """Salva prezzi storici nel DB, ignora duplicati. Restituisce n. inseriti."""
    if df.empty:
        return 0
    try:
        client = get_client()
        records = [{
            'isin': str(r['isin']),
            'data': str(r['data']),
            'prezzo': float(r['prezzo']),
            'fonte': str(r.get('fonte', 'yahoo'))
        } for _, r in df.iterrows()]

        inseriti = 0
        # Upsert a batch di 100
        batch_size = 100
        for i in range(0, len(records), batch_size):
            batch = records[i:i+batch_size]
            try:
                client.table('prezzi_storici').upsert(
                    batch, on_conflict='isin,data'
                ).execute()
                inseriti += len(batch)
            except Exception as e:
                print(f"  [!] Errore batch prezzi: {type(e).__name__}")
        return inseriti
    except Exception as e:
        print(f"  [!] Errore salvataggio prezzi: {type(e).__name__}")
        return 0


def carica_prezzi_db(isin: str, data_inizio: date,
                      data_fine: date = None) -> pd.DataFrame:
    """Carica prezzi storici dal DB per un ISIN."""
    if data_fine is None:
        data_fine = date.today()
    try:
        client = get_client()
        res = (client.table('prezzi_storici')
               .select('data, prezzo')
               .eq('isin', isin)
               .gte('data', data_inizio.isoformat())
               .lte('data', data_fine.isoformat())
               .order('data')
               .execute())
        if not res.data:
            return pd.DataFrame()
        df = pd.DataFrame(res.data)
        df['data'] = pd.to_datetime(df['data']).dt.date
        df['prezzo'] = pd.to_numeric(df['prezzo'])
        return df
    except Exception as e:
        print(f"  [!] Errore caricamento prezzi {isin}: {type(e).__name__}")
        return pd.DataFrame()


def backfill_prezzi(isins: List[str], verbose: bool = True) -> Dict[str, int]:
    """
    Scarica retroattivamente tutti i prezzi mancanti dall'ultima apertura ad oggi.
    Per ogni ISIN: controlla l'ultima data nel DB, scarica da lì a oggi.
    """
    risultati = {}
    oggi = date.today()
    try:
        from parser import load_config
        _cfg = load_config()
        data_default_inizio = date.fromisoformat(
            _cfg.get('parametri', {}).get('data_inizio_storico', '2024-01-01'))
    except Exception:
        data_default_inizio = date(2024, 1, 1)

    # Carica tutti i checkpoint in una sola query invece di N query separate
    try:
        client = get_client()
        res = client.table('backfill_stato').select('isin, ultima_data').execute()
        checkpoint_map = {
            r['isin']: date.fromisoformat(r['ultima_data']) for r in (res.data or [])
        }
    except Exception:
        checkpoint_map = {}

    for isin in isins:
        ultima = checkpoint_map.get(isin)

        if ultima is None:
            # Prima volta — scarica dall'inizio
            data_da = data_default_inizio
        elif ultima >= oggi:
            # Già aggiornato oggi
            if verbose:
                print(f"  [=] {isin}: già aggiornato a oggi")
            risultati[isin] = 0
            continue
        else:
            # Scarica dal giorno dopo l'ultima data
            data_da = ultima + timedelta(days=1)

        giorni_mancanti = (oggi - data_da).days
        if verbose:
            print(f"  [↓] {isin}: scarico {giorni_mancanti} giorni ({data_da} → {oggi})")

        from prices import scarica_storico
        df = scarica_storico(isin, data_da, oggi)

        if not df.empty:
            n = salva_prezzi(df)
            ultima_scaricata = df['data'].max()
            set_ultima_data_scaricata(isin, ultima_scaricata)
            risultati[isin] = n
            if verbose:
                print(f"  [+] {isin}: {n} prezzi salvati")
        else:
            risultati[isin] = 0
            if verbose:
                print(f"  [!] {isin}: nessun prezzo disponibile")

    return risultati


# ─────────────────────────────────────────────────────────────
# CALCOLO VALORE GIORNALIERO
# ─────────────────────────────────────────────────────────────

def calcola_valore_giornaliero(isin: str, data_inizio: date,
                                 data_fine: date = None) -> pd.DataFrame:
    """
    Calcola il valore giornaliero di un asset:
    valore = quantità_attiva(giorno) × prezzo(giorno)

    Gestisce automaticamente i cambi di quantità nel tempo.
    Interpola i prezzi nei giorni senza quotazione (weekend/festivi).
    """
    if data_fine is None:
        data_fine = date.today()

    # Carica storico posizioni (quantità nel tempo)
    try:
        client = get_client()
        res = (client.table('posizioni')
               .select('quantita, data_inizio, data_fine')
               .eq('isin', isin)
               .order('data_inizio')
               .execute())
        posizioni_storico = res.data if res.data else []
    except Exception:
        posizioni_storico = []

    if not posizioni_storico:
        # Fallback: cerca nell'asset_catalog l'eventuale quantità registrata
        try:
            catalog = carica_asset_catalog()
            row = catalog[catalog['isin'] == isin]
            if not row.empty:
                posizioni_storico = [{
                    'quantita': 0,
                    'data_inizio': '2020-01-01',
                    'data_fine': None
                }]
        except Exception:
            pass

    # Carica prezzi dal DB
    prezzi_df = carica_prezzi_db(isin, data_inizio, data_fine)
    if prezzi_df.empty:
        return pd.DataFrame()

    # Costruisci serie date complete (tutti i giorni, inclusi weekend)
    date_range = pd.date_range(data_inizio, data_fine, freq='D')
    df = pd.DataFrame({'data': date_range.date})

    # Merge prezzi (solo giorni con quotazione)
    df = df.merge(prezzi_df.rename(columns={'prezzo': 'prezzo_raw'}),
                  on='data', how='left')

    # Forward fill per weekend/festivi — nessun bfill: evita prezzi fittizi ante-prima quotazione
    df['prezzo'] = df['prezzo_raw'].ffill()

    _posizioni_parsed = [
        (
            date.fromisoformat(pos['data_inizio']),
            date.fromisoformat(pos['data_fine']) if pos['data_fine'] else date(2099, 12, 31),
            float(pos['quantita']),
        )
        for pos in posizioni_storico
    ]

    df['quantita'] = _build_quantita_arr(_posizioni_parsed, df['data'].values)
    df['valore'] = (df['quantita'] * df['prezzo']).round(2)
    df['isin'] = isin

    return df[['data', 'isin', 'quantita', 'prezzo', 'valore']].dropna(subset=['prezzo'])


def _computa_valore_isin(posizioni_storico: list, prezzi_df: pd.DataFrame,
                          df_dates: pd.DataFrame) -> Optional[pd.Series]:
    """Calcola serie valore giornaliero da dati già caricati (no DB calls)."""
    if not posizioni_storico or prezzi_df.empty:
        return None

    df = df_dates.merge(prezzi_df.rename(columns={'prezzo': 'prezzo_raw'}),
                        on='data', how='left')
    df['prezzo'] = df['prezzo_raw'].ffill()

    posizioni_parsed = [
        (
            date.fromisoformat(pos['data_inizio']),
            date.fromisoformat(pos['data_fine']) if pos['data_fine'] else date(2099, 12, 31),
            float(pos['quantita']),
        )
        for pos in posizioni_storico
    ]

    df['quantita'] = _build_quantita_arr(posizioni_parsed, df['data'].values)
    df['valore'] = (df['quantita'] * df['prezzo']).round(2)
    series = df.set_index('data')['valore'].dropna()
    return series if not series.empty else None


def calcola_portafoglio_storico(isins: List[str] = None,
                                  data_inizio: date = None,
                                  data_fine: date = None) -> pd.DataFrame:
    """
    Calcola il valore aggregato del portafoglio giorno per giorno.
    Usa 2 query batch (posizioni + prezzi) invece di 2×N query separate.
    Restituisce DataFrame con: data, valore_totale, e colonne per ogni asset.
    """
    if isins is None:
        # Usa gli ISIN dal catalog DB (fonte di verità)
        try:
            t, _ = carica_asset_tickers()
            isins = list(t.keys())
        except Exception:
            isins = list(ASSET_TICKERS.keys())
    if data_inizio is None:
        try:
            from parser import load_config
            _cfg = load_config()
            data_inizio = date.fromisoformat(
                _cfg.get('parametri', {}).get('data_inizio_storico', '2024-01-01'))
        except Exception:
            data_inizio = date(2024, 1, 1)
    if data_fine is None:
        data_fine = date.today()

    # Carica tutte le posizioni in una sola query
    try:
        client = get_client()
        res_pos = (client.table('posizioni')
                   .select('isin, quantita, data_inizio, data_fine')
                   .in_('isin', isins)
                   .order('data_inizio')
                   .execute())
        posizioni_raw = res_pos.data or []
    except Exception:
        posizioni_raw = []

    # Carica tutti i prezzi in una sola query
    try:
        client = get_client()
        res_prez = (client.table('prezzi_storici')
                    .select('isin, data, prezzo')
                    .in_('isin', isins)
                    .gte('data', data_inizio.isoformat())
                    .lte('data', data_fine.isoformat())
                    .order('data')
                    .execute())
        prezzi_raw = res_prez.data or []
    except Exception:
        prezzi_raw = []

    # Organizza posizioni per ISIN
    posizioni_per_isin: Dict[str, list] = {isin: [] for isin in isins}
    for p in posizioni_raw:
        posizioni_per_isin.setdefault(p['isin'], []).append(p)

    # Organizza prezzi per ISIN come DataFrame
    prezzi_per_isin: Dict[str, pd.DataFrame] = {}
    if prezzi_raw:
        pz = pd.DataFrame(prezzi_raw)
        pz['data'] = pd.to_datetime(pz['data']).dt.date
        pz['prezzo'] = pd.to_numeric(pz['prezzo'])
        for isin_val, grp in pz.groupby('isin'):
            prezzi_per_isin[str(isin_val)] = grp[['data', 'prezzo']].reset_index(drop=True)

    df_dates = pd.DataFrame({'data': pd.date_range(data_inizio, data_fine, freq='D').date})

    frames = {}
    for isin in isins:
        posizioni_storico = posizioni_per_isin.get(isin, [])
        if not posizioni_storico:
            pass  # Se il DB non ha posizioni, saltiamo questo ISIN

        prezzi_isin = prezzi_per_isin.get(isin, pd.DataFrame())
        series = _computa_valore_isin(posizioni_storico, prezzi_isin, df_dates)
        if series is not None:
            frames[isin] = series

    if not frames:
        return pd.DataFrame()

    combined = pd.DataFrame(frames)
    combined['totale'] = combined.sum(axis=1)
    combined = combined.reset_index()
    combined.columns.name = None
    return combined


def eventi_portafoglio(data_inizio: date = None, data_fine: date = None) -> pd.DataFrame:
    """
    Restituisce tutti gli eventi di modifica quantità nel periodo
    (acquisti, vendite parziali) con relativo impatto sul valore.
    """
    try:
        client = get_client()
        q = client.table('posizioni').select('*').order('data_inizio')
        if data_inizio:
            q = q.gte('data_inizio', data_inizio.isoformat())
        if data_fine:
            q = q.lte('data_inizio', data_fine.isoformat())
        res = q.execute()
        if not res.data:
            return pd.DataFrame()

        df = pd.DataFrame(res.data)
        df['data_inizio'] = pd.to_datetime(df['data_inizio']).dt.date
        return df
    except Exception:
        return pd.DataFrame()
