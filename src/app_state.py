"""
Gestione stato app — ponte tra Supabase e Streamlit.
Carica dati all'avvio, salva automaticamente, gestisce fallback locale.
"""

import streamlit as st
import pandas as pd
from datetime import date, datetime
from pathlib import Path
from typing import Optional
import sys

sys.path.insert(0, str(Path(__file__).parent))

from database import (
    salva_snapshot_patrimonio, carica_patrimonio_log,
    salva_quote_fondi, carica_ultime_quote_fondi, storico_quote_fondo,
    salva_transazioni, carica_transazioni,
    salva_param, carica_param, carica_tutti_params,
    salva_params_batch, test_connessione,
    carica_asset_catalog, upsert_asset, elimina_asset,
    inizializza_asset_catalog_da_config,
)


def init_db_connection() -> bool:
    """
    Verifica connessione DB all'avvio dell'app.
    Mostra warning se offline ma non blocca l'app.
    """
    if 'db_ok' not in st.session_state:
        st.session_state['db_ok'] = test_connessione()
    return st.session_state['db_ok']


def carica_params_persistenti(config: dict) -> dict:
    """
    Carica i valori manuali dall'ultima sessione salvata su Supabase.
    Se non disponibili, usa i valori di default dal config.yaml.
    """
    params_db = carica_tutti_params()
    p = config['patrimonio']

    return {
        'liquidita_persona1':          params_db.get('liquidita_persona1',
                                                       p['liquidita_persona1']),
        'liquidita_persona2':          params_db.get('liquidita_persona2',
                                                       p['liquidita_persona2']),
        'conto_comune':               params_db.get('conto_comune',
                                                       p['conto_comune']),
        'affitto_accantonato':        params_db.get('affitto_accantonato',
                                                       p['affitto_accantonato']),
        'fondi_bancari':              params_db.get('fondi_bancari',
                                                       p['fondi_bancari']),
        'gestione_separata_generali': params_db.get('gestione_separata_generali',
                                                       p['gestione_separata_generali']),
        'nome_persona1':              params_db.get('nome_persona1', 'Persona 1'),
        'nome_persona2':              params_db.get('nome_persona2', 'Persona 2'),
        'nome_figlio':                params_db.get('nome_figlio',   'Figlio/a'),
    }


def salva_params_persistenti(params: dict) -> bool:
    """Salva i valori manuali aggiornati su Supabase — batch upsert unico."""
    return salva_params_batch(params)


def carica_quote_fondi_persistenti(config: dict) -> dict:
    """
    Carica le ultime quote fondi dal DB.
    Restituisce dict {isin: quota} da usare come override.
    Fallback su asset_catalog (o config.yaml) se quote_fondi è vuota.
    """
    df = carica_ultime_quote_fondi()
    if not df.empty:
        return {row['isin']: float(row['quota']) for _, row in df.iterrows()}

    # Fallback: usa le quote ref dall'asset_catalog
    catalog = carica_asset_catalog()
    if not catalog.empty and 'tipo' in catalog.columns:
        fondi = catalog[catalog['tipo'] == 'fondo']
        if not fondi.empty:
            return {str(r['isin']): float(r.get('valore_quota_ref') or 0)
                    for _, r in fondi.iterrows() if r.get('valore_quota_ref')}

    # Ultimo fallback: config.yaml
    return {f['isin']: f['valore_quota_ref']
            for f in config.get('fondi_bancari', {}).get('titoli', [])
            if f.get('valore_quota_ref')}


def auto_save_snapshot(snapshot: dict, params: dict) -> bool:
    """
    Salva automaticamente lo snapshot del giorno corrente.
    Chiamato silenziosamente all'avvio dell'app.
    Salva solo una volta al giorno (upsert per data).
    """
    ok1 = salva_snapshot_patrimonio(snapshot)
    ok2 = salva_params_persistenti(params)
    return ok1 and ok2


def importa_transazioni_xls(df_tx: pd.DataFrame) -> int:
    """
    Importa transazioni da XLS nel DB.
    Restituisce numero di nuove transazioni inserite.
    """
    return salva_transazioni(df_tx)


def carica_transazioni_db(mesi: int = 12) -> pd.DataFrame:
    """Carica transazioni dal DB (più veloci e complete degli XLS)."""
    return carica_transazioni(mesi)


def get_storico_patrimonio(giorni: int = 365) -> pd.DataFrame:
    """Carica storico patrimonio per grafici andamento."""
    return carica_patrimonio_log(giorni)


def get_storico_fondo(isin: str, giorni: int = 180) -> pd.DataFrame:
    """Carica storico quote di un singolo fondo."""
    return storico_quote_fondo(isin, giorni)


def aggiorna_quote_fondi(quote_map: dict, config: dict,
                          catalog_df: 'pd.DataFrame | None' = None) -> bool:
    """
    Salva le quote aggiornate manualmente nell'app.
    quote_map: {isin: quota_nuova}
    Usa catalog_df (DB) se disponibile, altrimenti fallback su config.
    """
    if catalog_df is not None and not catalog_df.empty:
        fondi_meta = {
            row['isin']: row
            for _, row in catalog_df[catalog_df['tipo'] == 'fondo'].iterrows()
        }
    else:
        fondi_meta = {f['isin']: f for f in config.get('fondi_bancari', {}).get('titoli', [])}

    righe = []
    for isin, quota in quote_map.items():
        meta = fondi_meta.get(isin, {})
        quantita = meta.get('quantita', 0)
        righe.append({
            'isin': isin,
            'nome': meta.get('nome', isin),
            'quota': quota,
            'quantita': quantita,
            'valore': round(quantita * quota, 2)
        })
    return salva_quote_fondi(righe, fonte='manuale_app')


# ─────────────────────────────────────────────────────────────
# BACKFILL AUTOMATICO ALL'AVVIO
# ─────────────────────────────────────────────────────────────

def esegui_backfill_avvio(config: dict, verbose: bool = False) -> dict:
    """
    Chiamato silenziosamente all'avvio dell'app.
    Scarica tutti i prezzi mancanti dall'ultima apertura ad oggi.
    """
    from positions import backfill_prezzi, inizializza_posizioni
    from prices import reload_asset_tickers, ASSET_TICKERS

    # 1. Semina asset_catalog da config.yaml se è la prima volta
    inizializza_asset_catalog_da_config()

    # 2. Ricarica la mappa ISIN→ticker dal DB (ora popolata)
    reload_asset_tickers()

    # 3. Inizializza le posizioni attive se la tabella è vuota
    inizializza_posizioni()

    # 4. Backfill prezzi per tutti gli ISIN noti
    isins_da_aggiornare = list(ASSET_TICKERS.keys())
    risultati = backfill_prezzi(isins_da_aggiornare, verbose=verbose)
    n_totale = sum(v for v in risultati.values() if v)
    return {'n_prezzi_scaricati': n_totale, 'dettaglio': risultati}


def get_asset_catalog() -> 'pd.DataFrame':
    """Carica il catalogo asset dal DB."""
    return carica_asset_catalog()


def salva_asset(asset: dict) -> bool:
    """Upsert di un asset nel catalogo DB."""
    return upsert_asset(asset)


def rimuovi_asset(isin: str) -> bool:
    """Rimuove un asset dal catalogo DB."""
    return elimina_asset(isin)


def get_storico_portafoglio(data_inizio=None, data_fine=None) -> "pd.DataFrame":
    """Carica il valore storico aggregato del portafoglio."""
    from positions import calcola_portafoglio_storico
    from datetime import date
    if data_inizio is None:
        from datetime import timedelta
        data_inizio = date.today() - timedelta(days=365)
    return calcola_portafoglio_storico(data_inizio=data_inizio, data_fine=data_fine)


def get_storico_asset(isin: str, data_inizio=None, data_fine=None) -> "pd.DataFrame":
    """Carica il valore storico di un singolo asset."""
    from positions import calcola_valore_giornaliero
    from datetime import date, timedelta
    if data_inizio is None:
        data_inizio = date.today() - timedelta(days=365)
    return calcola_valore_giornaliero(isin, data_inizio, data_fine)


def aggiorna_quantita_asset(isin: str, nuova_quantita: float,
                              data_modifica, note: str = None) -> bool:
    """Registra una modifica di quantità (acquisto/vendita parziale)."""
    from positions import aggiorna_quantita
    return aggiorna_quantita(isin, nuova_quantita, data_modifica, note)


def get_eventi_portafoglio(data_inizio=None, data_fine=None) -> "pd.DataFrame":
    """Restituisce tutti gli eventi di acquisto/vendita."""
    from positions import eventi_portafoglio
    return eventi_portafoglio(data_inizio, data_fine)
