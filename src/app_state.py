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
    test_connessione
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
        'liquidita_daniel':           params_db.get('liquidita_daniel',
                                                       p['liquidita_daniel']),
        'liquidita_alessandra':       params_db.get('liquidita_alessandra',
                                                       p['liquidita_alessandra']),
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
    """Salva i valori manuali aggiornati su Supabase."""
    ok = True
    for k, v in params.items():
        if not salva_param(k, v):
            ok = False
    return ok


def carica_quote_fondi_persistenti(config: dict) -> dict:
    """
    Carica le ultime quote fondi dal DB.
    Restituisce dict {isin: quota} da usare come override.
    """
    df = carica_ultime_quote_fondi()
    if df.empty:
        # Fallback: usa le quote dal config.yaml
        return {f['isin']: f['valore_quota_ref']
                for f in config['fondi_bancari']['titoli']}

    result = {}
    for _, row in df.iterrows():
        result[row['isin']] = float(row['quota'])
    return result


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


def aggiorna_quote_fondi(quote_map: dict, config: dict) -> bool:
    """
    Salva le quote aggiornate manualmente nell'app.
    quote_map: {isin: quota_nuova}
    """
    fondi_cfg = {f['isin']: f for f in config['fondi_bancari']['titoli']}
    righe = []
    for isin, quota in quote_map.items():
        fondo = fondi_cfg.get(isin, {})
        quantita = fondo.get('quantita', 0)
        righe.append({
            'isin': isin,
            'nome': fondo.get('nome', isin),
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
    from positions import backfill_prezzi, inizializza_posizioni, ASSET_TICKERS

    # Inizializza posizioni se è la prima volta
    inizializza_posizioni()

    # Lista ISIN da aggiornare: fondi + ETF attivi + azioni
    isins_da_aggiornare = list(ASSET_TICKERS.keys())

    # Esegui backfill
    risultati = backfill_prezzi(isins_da_aggiornare, verbose=verbose)
    n_totale = sum(v for v in risultati.values() if v)
    return {'n_prezzi_scaricati': n_totale, 'dettaglio': risultati}


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
