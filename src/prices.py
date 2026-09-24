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
# MAPPA ASSET → TICKER YAHOO FINANCE  (derivata da config.yaml)
# ─────────────────────────────────────────────────────────────
# Fondi italiani con ticker_yf: null → ticker costruito come {isin}.MI
# ETF e azioni → usano ticker_yf dal config

def _build_tickers_from_config():
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        from parser import load_config
        cfg = load_config()
    except Exception:
        return {}, {}

    tickers: dict = {}
    fallbacks: dict = {}

    for f in cfg.get('fondi_bancari', {}).get('titoli', []):
        isin = f.get('isin')
        if not isin:
            continue
        tickers[isin] = f.get('ticker_yf') or f'{isin}.MI'
        if f.get('fallback_tickers'):
            fallbacks[isin] = f['fallback_tickers']

    for etf in cfg.get('etf', []):
        isin = etf.get('isin')
        ticker = etf.get('ticker_yf')
        if isin and ticker:
            tickers[isin] = ticker

    for az in cfg.get('azioni', []):
        isin = az.get('isin')
        ticker = az.get('ticker_yf')
        if isin and ticker:
            tickers[isin] = ticker

    return tickers, fallbacks


ASSET_TICKERS, FALLBACK_TICKERS = _build_tickers_from_config()


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
    Scarica storici per una lista di ISIN con una singola chiamata batch yfinance.
    Per ISIN senza dati nel batch, ritenta individualmente con i ticker di fallback.
    Restituisce DataFrame unificato con tutti i prezzi.
    """
    if data_fine is None:
        data_fine = date.today()

    isin_to_ticker = {isin: ASSET_TICKERS[isin] for isin in isins if isin in ASSET_TICKERS}
    if not isin_to_ticker:
        return pd.DataFrame()

    start_str = data_inizio.strftime('%Y-%m-%d')
    end_str = (data_fine + timedelta(days=1)).strftime('%Y-%m-%d')
    tickers_list = list(isin_to_ticker.values())

    # Batch download — una sola chiamata HTTP per tutti gli ISIN
    try:
        raw = yf.download(tickers_list, start=start_str, end=end_str,
                          auto_adjust=True, progress=False)
    except Exception as e:
        print(f"  [!] Errore batch yfinance: {e} — fallback a download singoli")
        raw = pd.DataFrame()

    frames = []
    missing_isins = []

    for isin, ticker in isin_to_ticker.items():
        try:
            if raw.empty:
                series = pd.Series(dtype=float)
            elif len(tickers_list) == 1:
                # Singolo ticker: colonne flat
                series = raw.get('Close', pd.Series(dtype=float)).dropna()
            else:
                # Multi-ticker: MultiIndex (field, ticker)
                close = raw.get('Close', pd.DataFrame())
                series = (close.get(ticker, pd.Series(dtype=float)).dropna()
                          if not isinstance(close, pd.Series) and not close.empty
                          else pd.Series(dtype=float))

            if series.empty:
                missing_isins.append(isin)
                continue

            df_t = series.reset_index()
            df_t.columns = ['data', 'prezzo']
            df_t['data'] = pd.to_datetime(df_t['data']).dt.date
            df_t['isin'] = isin
            df_t['ticker'] = ticker
            df_t['fonte'] = 'yahoo'
            frames.append(df_t[['data', 'isin', 'ticker', 'prezzo', 'fonte']])
            print(f"  [+] {isin}: {len(df_t)} giorni scaricati")
        except Exception as e:
            missing_isins.append(isin)
            print(f"  [!] {isin}: errore in batch — {e}")

    # Fallback individuale per ISIN con dati mancanti (usa fallback ticker se disponibili)
    for isin in missing_isins:
        df = scarica_storico(isin, data_inizio, data_fine)
        if not df.empty:
            frames.append(df)
            print(f"  [+] {isin}: {len(df)} giorni scaricati (fallback)")
        else:
            print(f"  [!] {isin}: nessun dato disponibile")

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
