"""
Modulo prezzi: scarica storico da Yahoo Finance per ETF, azioni e fondi italiani.
Gestisce il backfill automatico dei giorni mancanti dall'ultima apertura.
"""

import pandas as pd
import numpy as np
from datetime import date, datetime, timedelta
from typing import Optional, Dict, List, Tuple
import yfinance as yf


# ─────────────────────────────────────────────────────────────
# MAPPA ASSET → TICKER YAHOO FINANCE
# ─────────────────────────────────────────────────────────────
# Fondi italiani: suffisso .MI (Borsa Italiana / Euronext Milan)
# Fondi lussemburghesi: suffisso .MI o verifica alternativa
# ETF: tickers standard già noti

ASSET_TICKERS = {
    # Fondi bancari
    'IT0001033486': 'IT0001033486.MI',   # ARCA AZ EUROPA CLIMA
    'IT0001033502': 'IT0001033502.MI',   # ARCA AZ AMERICA CLIMA P
    'IT0001031928': 'IT0001031928.MI',   # EURIZON AZ EMERG P
    'LU2293888439': 'LU2293888439.MI',   # JPMF GLO SUST EQ ACC (probare anche .PA)
    'IT0001050126': 'IT0001050126.MI',   # EURIZON AZ AMER P
    'IT0001050225': 'IT0001050225.MI',   # EURIZ AZ AREA EURO P
    'IT0001080446': 'IT0001080446.MI',   # EURIZON AZ INT P

    # ETF su Directa
    'IE00B5BMR087': 'CSPX.L',           # iShares Core S&P 500
    'IE00B44Z5B48': 'ACWE.MI',          # SPDR MSCI ACWI
    'IE00B4L5Y983': 'IWDA.AS',          # iShares Core MSCI World
    'IE000BI8OT95': 'MWRD.MI',          # Amundi Core MSCI World
    'IE00B466KX20': 'EMAE.MI',          # SPDR MSCI EM Asia

    # Azioni
    'IE00B4BNMY34': 'ACN',              # Accenture
}

# Ticker di fallback se il primario non funziona
FALLBACK_TICKERS = {
    'LU2293888439': ['LU2293888439.PA', '0P00000XXX'],  # JPM Global Sust EQ
}


def get_ticker(isin: str) -> Optional[str]:
    """Restituisce il ticker Yahoo per un ISIN dato."""
    return ASSET_TICKERS.get(isin)


def scarica_storico(isin: str, data_inizio: date, data_fine: date = None) -> pd.DataFrame:
    """
    Scarica prezzi storici giornalieri per un ISIN.
    Restituisce DataFrame con colonne: data, prezzo_chiusura, fonte.
    """
    if data_fine is None:
        data_fine = date.today()

    ticker_str = ASSET_TICKERS.get(isin)
    if not ticker_str:
        return pd.DataFrame()

    try:
        ticker = yf.Ticker(ticker_str)
        hist = ticker.history(
            start=data_inizio.strftime('%Y-%m-%d'),
            end=(data_fine + timedelta(days=1)).strftime('%Y-%m-%d')
        )
        if hist.empty:
            # Prova fallback
            for fb in FALLBACK_TICKERS.get(isin, []):
                hist = yf.Ticker(fb).history(
                    start=data_inizio.strftime('%Y-%m-%d'),
                    end=(data_fine + timedelta(days=1)).strftime('%Y-%m-%d')
                )
                if not hist.empty:
                    ticker_str = fb
                    break

        if hist.empty:
            return pd.DataFrame()

        df = hist[['Close']].copy()
        df.index = df.index.date
        df.columns = ['prezzo']
        df.index.name = 'data'
        df = df.reset_index()
        df['isin'] = isin
        df['ticker'] = ticker_str
        df['fonte'] = 'yahoo'
        return df[['data', 'isin', 'ticker', 'prezzo', 'fonte']]

    except Exception as e:
        print(f"  [!] Errore scaricamento {isin} ({ticker_str}): {e}")
        return pd.DataFrame()


def prezzo_corrente(isin: str) -> Optional[float]:
    """Prezzo di chiusura più recente disponibile."""
    ticker_str = ASSET_TICKERS.get(isin)
    if not ticker_str:
        return None
    try:
        hist = yf.Ticker(ticker_str).history(period='5d')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
        return None
    except Exception:
        return None


def scarica_tutti_storici(isins: List[str], data_inizio: date,
                           data_fine: date = None) -> pd.DataFrame:
    """
    Scarica storici per una lista di ISIN.
    Restituisce DataFrame unificato con tutti i prezzi.
    """
    frames = []
    for isin in isins:
        df = scarica_storico(isin, data_inizio, data_fine)
        if not df.empty:
            frames.append(df)
            print(f"  [+] {isin}: {len(df)} giorni scaricati")
        else:
            print(f"  [!] {isin}: nessun dato disponibile")

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
