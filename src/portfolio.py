"""
Portfolio: valorizza ETF (yfinance), fondi bancari (quote manuali),
azioni Accenture (yfinance), piano di uscita ottimale fondi.
"""

import pandas as pd
import numpy as np
from datetime import date
from typing import Dict, List, Optional
import yfinance as yf

from parser import load_config

try:
    import streamlit as _st
    _cache_data = _st.cache_data
except Exception:
    def _cache_data(**_kw):
        return lambda f: f


@_cache_data(ttl=1800)
def _batch_download(tickers: tuple, period: str) -> pd.DataFrame:
    """Scarica tutti i ticker in una singola chiamata yfinance."""
    try:
        return yf.download(list(tickers), period=period, auto_adjust=True, progress=False)
    except Exception as e:
        print(f"  [!] Errore batch download ETF: {type(e).__name__}")
        return pd.DataFrame()


def _extract_series(raw: pd.DataFrame, ticker: str, n_tickers: int) -> pd.Series:
    """Estrae la serie Close per un ticker dal risultato di yf.download."""
    if raw.empty:
        return pd.Series(dtype=float)
    if n_tickers == 1:
        return raw.get('Close', pd.Series(dtype=float)).dropna()
    close = raw.get('Close', pd.DataFrame())
    if isinstance(close, pd.Series) or close.empty:
        return pd.Series(dtype=float)
    return close.get(ticker, pd.Series(dtype=float)).dropna()


@_cache_data(ttl=1800)
def get_etf_data(ticker: str, period: str = "1y") -> pd.DataFrame:
    try:
        obj = yf.Ticker(ticker)
        hist = obj.history(period=period)
        if hist.empty:
            return pd.DataFrame()
        return hist[['Close']].rename(columns={'Close': 'price'})
    except Exception as e:
        print(f"  [!] Errore download {ticker}: {type(e).__name__}")
        return pd.DataFrame()


def get_etf_history_chart(ticker: str, period: str = "1y") -> pd.DataFrame:
    raw = _batch_download((ticker,), period)
    series = _extract_series(raw, ticker, 1)
    if series.empty:
        return pd.DataFrame()
    hist = series.to_frame(name='price')
    first_price = hist['price'].iloc[0]
    if not first_price or first_price == 0:
        return hist
    hist['indexed'] = hist['price'] / first_price * 100
    return hist


def get_portfolio_performance(config: dict,
                               catalog_df: 'pd.DataFrame | None' = None) -> pd.DataFrame:
    results = []
    if catalog_df is not None and not catalog_df.empty and 'tipo' in catalog_df.columns:
        etf_list = catalog_df[catalog_df['tipo'] == 'etf'].to_dict('records')
    else:
        etf_list = config.get('etf', [])
    tickers = tuple(etf['ticker_yf'] for etf in etf_list if etf.get('ticker_yf'))
    if not tickers:
        return pd.DataFrame()

    # Una sola chiamata batch per tutti i ticker (reduce 2×N → 1 HTTP call)
    raw = _batch_download(tickers, '1y')
    year_start = pd.Timestamp(date.today().year, 1, 1)

    for etf in etf_list:
        ticker = etf['ticker_yf']
        if not ticker:
            continue
        nome = etf['nome']
        valore_iniziale = etf.get('valore_iniziale', 0)
        proprietario = etf.get('proprietario', 'persona1')
        stato = etf.get('stato', 'candidato')

        series_1y = _extract_series(raw, ticker, len(tickers))

        # Ricava subset YTD dalla serie 1y (evita una seconda chiamata HTTP)
        if not series_1y.empty:
            idx = series_1y.index
            ys = year_start.tz_localize(idx.tz) if idx.tz is not None else year_start
            series_ytd = series_1y[idx >= ys]
        else:
            series_ytd = pd.Series(dtype=float)

        perf_ytd = perf_1y = prezzo_attuale = None
        valore_attuale = valore_iniziale

        if not series_ytd.empty:
            p0_ytd = float(series_ytd.iloc[0])
            p_curr = float(series_ytd.iloc[-1])
            prezzo_attuale = round(p_curr, 2)
            perf_ytd = round((p_curr / p0_ytd - 1) * 100, 2) if p0_ytd else None

        if not series_1y.empty:
            p0_1y = float(series_1y.iloc[0])
            p_curr = float(series_1y.iloc[-1])
            prezzo_attuale = round(p_curr, 2)
            perf_1y = round((p_curr / p0_1y - 1) * 100, 2) if p0_1y else None
            if valore_iniziale > 0 and p0_1y:
                # Calcola quantità implicita dal prezzo di 1 anno fa (più preciso di YTD% su costo storico)
                q_impl = valore_iniziale / p0_1y
                valore_attuale = round(q_impl * p_curr, 2)

        results.append({
            'ticker': etf['ticker_bi'], 'ticker_yf': ticker,
            'nome': nome, 'isin': etf.get('isin', ''),
            'proprietario': proprietario, 'stato': stato,
            'ter': etf.get('ter'), 'valore_iniziale': valore_iniziale,
            'valore_attuale': valore_attuale,
            'rendimento_eur': round(valore_attuale - valore_iniziale, 2),
            'rendimento_pct': perf_ytd, 'perf_1y': perf_1y,
            'prezzo_attuale': prezzo_attuale, 'note': etf.get('note', '')
        })
    return pd.DataFrame(results)


def get_fondi_snapshot(config: dict, quote_aggiornate: Dict[str, float] = None,
                        fondi_data: list = None) -> pd.DataFrame:
    """
    fondi_data: lista opzionale di dict con isin, nome, quantita, valore_quota_ref, ter, data_ref.
                Se fornita, viene usata al posto di config (fonte DB).
    """
    if fondi_data is not None:
        fondi = fondi_data
        aliquota = config.get('parametri', {}).get('aliquota_capital_gain', 0.26)
    else:
        fondi = config.get('fondi_bancari', {}).get('titoli', [])
        aliquota = config.get('fondi_bancari', {}).get('aliquota_capital_gain', 0.26)
    tot_valore_ref = sum(f.get('quantita', 0) * f.get('valore_quota_ref', 0) for f in fondi)
    costo_fiscale_tot = config['patrimonio']['fondi_costo_fiscale']
    rows = []

    for fondo in fondi:
        quota = fondo.get('quota_aggiornata') or fondo.get('valore_quota_ref', 0)
        if quote_aggiornate and fondo['isin'] in quote_aggiornate:
            quota = quote_aggiornate[fondo['isin']]
        quantita = fondo.get('quantita', 0)

        valore_attuale = round(quantita * quota, 2)
        valore_ref = round(quantita * fondo.get('valore_quota_ref', 0), 2)
        peso = valore_ref / tot_valore_ref if tot_valore_ref > 0 else 0
        costo_fiscale_stimato = round(costo_fiscale_tot * peso, 2)
        plusvalenza_stimata = max(valore_attuale - costo_fiscale_stimato, 0)
        tassa_latente = round(plusvalenza_stimata * aliquota, 2)
        netto_uscita = round(valore_attuale - tassa_latente, 2)

        rows.append({
            'nome': fondo['nome'], 'isin': fondo['isin'],
            'quantita': quantita,
            'quota_ref': fondo.get('valore_quota_ref', 0),
            'quota_attuale': quota, 'data_ref': fondo.get('data_ref', ''),
            'valore_attuale': valore_attuale,
            'costo_fiscale_stimato': costo_fiscale_stimato,
            'plusvalenza_stimata': plusvalenza_stimata,
            'tassa_latente': tassa_latente,
            'netto_uscita': netto_uscita,
            'pct_plusvalenza': round(plusvalenza_stimata / costo_fiscale_stimato * 100
                                     if costo_fiscale_stimato > 0 else 0, 1)
        })
    return pd.DataFrame(rows)


def simula_uscita_fondo_data_x(config: dict, data_uscita: date,
                                rendimento_annuo: float = 0.04,
                                quote_aggiornate: Dict[str, float] = None) -> pd.DataFrame:
    aliquota = config.get('parametri', {}).get(
        'aliquota_capital_gain',
        config.get('migrazione_fondi', {}).get('aliquota_capital_gain', 0.26))
    df = get_fondi_snapshot(config, quote_aggiornate)
    oggi = date.today()
    giorni = max((data_uscita - oggi).days, 0)
    anni_fraz = giorni / 365.25
    fattore = (1 + rendimento_annuo) ** anni_fraz

    df_proj = df.copy()
    df_proj['quota_proiettata'] = (df_proj['quota_attuale'] * fattore).round(4)
    df_proj['valore_proiettato'] = (df_proj['quantita'] * df_proj['quota_proiettata']).round(2)
    df_proj['plusvalenza_proiettata'] = (df_proj['valore_proiettato'] -
                                         df_proj['costo_fiscale_stimato']).clip(lower=0).round(2)
    df_proj['tassa_proiettata'] = (df_proj['plusvalenza_proiettata'] * aliquota).round(2)
    df_proj['netto_proiettato'] = (df_proj['valore_proiettato'] - df_proj['tassa_proiettata']).round(2)
    df_proj['data_uscita'] = data_uscita
    df_proj['mesi_attesa'] = round(giorni / 30.44, 1)
    return df_proj[['nome', 'isin', 'quantita', 'valore_attuale', 'netto_uscita',
                    'valore_proiettato', 'tassa_proiettata', 'netto_proiettato',
                    'mesi_attesa', 'data_uscita']]


def piano_uscita_ottimale(config: dict, quote_aggiornate: Dict[str, float] = None) -> pd.DataFrame:
    df = get_fondi_snapshot(config, quote_aggiornate)
    piano_cfg = config['migrazione_fondi']['piano_annuale']

    ter_map = {f['nome']: f.get('ter', 0.02)
               for f in config.get('fondi_bancari', {}).get('titoli', [])}
    df['costo_annuo_stimato'] = df['nome'].map(ter_map).fillna(0.02)
    df['priorita'] = df['costo_annuo_stimato'] * df['valore_attuale']
    df = df.sort_values('priorita', ascending=False).reset_index(drop=True)

    aliquota = config.get('parametri', {}).get(
        'aliquota_capital_gain',
        config.get('migrazione_fondi', {}).get('aliquota_capital_gain', 0.26))
    etf_dest = config.get('parametri', {}).get('etf_destinazione_migrazione', 'IWDA / VWCE')

    piano_rows = []
    fondi_residui = df[['nome', 'isin', 'valore_attuale', 'plusvalenza_stimata',
                         'costo_annuo_stimato']].to_dict('records')

    for anno_cfg in piano_cfg:
        budget_residuo = anno_cfg['rimborso_lordo']
        anno_num = anno_cfg['anno']

        for r in fondi_residui:
            if budget_residuo <= 0 or r['valore_attuale'] <= 0:
                continue
            importo = min(r['valore_attuale'], budget_residuo)
            quota_pv = (importo / r['valore_attuale']) * r['plusvalenza_stimata'] if r['valore_attuale'] > 0 else 0
            tassa = round(quota_pv * aliquota, 2)
            netto = round(importo - tassa, 2)
            budget_residuo -= importo
            r['valore_attuale'] = max(r['valore_attuale'] - importo, 0)
            r['plusvalenza_stimata'] = max(r['plusvalenza_stimata'] - quota_pv, 0)

            piano_rows.append({
                'anno': anno_num, 'fondo': r['nome'], 'isin': r['isin'],
                'rimborso_lordo': round(importo, 0),
                'tassa_26pct': round(tassa, 0),
                'netto_in_etf': round(netto, 0),
                'etf_destinazione': etf_dest,
                'motivo': f"TER ~{r['costo_annuo_stimato']*100:.1f}%"
            })

    return pd.DataFrame(piano_rows)


def get_azioni_snapshot(config: dict,
                         catalog_df: 'pd.DataFrame | None' = None) -> pd.DataFrame:
    if catalog_df is not None and not catalog_df.empty and 'tipo' in catalog_df.columns:
        azioni = catalog_df[catalog_df['tipo'] == 'azione'].to_dict('records')
    else:
        azioni = config.get('azioni', [])
    rows = []
    for az in azioni:
        ticker = az.get('ticker_yf') or az.get('ticker')
        quantita = az.get('quantita', 0)
        hist_1y = get_etf_data(ticker, period="1y")
        prezzo_attuale = valore_attuale = perf_ytd = perf_1y = None
        if not hist_1y.empty:
            prezzo_attuale = round(float(hist_1y['price'].iloc[-1]), 2)
            valore_attuale = round(prezzo_attuale * quantita, 2)
            perf_1y = round((hist_1y['price'].iloc[-1] / hist_1y['price'].iloc[0] - 1) * 100, 2)
            # YTD: subset da 1 gennaio — evita una seconda chiamata yfinance
            year_start = pd.Timestamp(date.today().year, 1, 1)
            idx = hist_1y.index
            ys = year_start.tz_localize(idx.tz) if idx.tz is not None else year_start
            hist_ytd = hist_1y[idx >= ys]
            if not hist_ytd.empty:
                perf_ytd = round((hist_ytd['price'].iloc[-1] / hist_ytd['price'].iloc[0] - 1) * 100, 2)

        rows.append({
            'nome': az['nome'], 'ticker': ticker, 'isin': az.get('isin', ''),
            'quantita': quantita, 'prezzo_attuale_usd': prezzo_attuale,
            'valore_attuale_usd': valore_attuale,
            'perf_ytd': perf_ytd, 'perf_1y': perf_1y,
            'tipo': az.get('tipo', ''), 'note': az.get('note', '')
        })
    return pd.DataFrame(rows)


def patrimonio_snapshot(config: dict, etf_df=None, fondi_df=None,
                         azioni_df=None, aggiornamenti_manuali=None) -> dict:
    p = config['patrimonio'].copy()
    if aggiornamenti_manuali:
        p.update(aggiornamenti_manuali)

    etf_persona1 = p.get('etf_cspx_directa', 0)
    etf_figlio = 0
    if etf_df is not None and not etf_df.empty:
        etf_persona1 = etf_df[(etf_df['proprietario'] == 'persona1') &
                               (etf_df['stato'] == 'attivo')]['valore_attuale'].sum()
        etf_figlio = etf_df[etf_df['proprietario'] == 'figlio']['valore_attuale'].sum()

    fondi_tot = p.get('fondi_bancari', 0)
    if fondi_df is not None and not fondi_df.empty:
        fondi_tot = fondi_df['valore_attuale'].sum()

    azioni_usd = 0
    if azioni_df is not None and not azioni_df.empty:
        azioni_usd = azioni_df['valore_attuale_usd'].fillna(0).sum()

    snap = {
        'fondi_bancari': round(fondi_tot, 0),
        'generali': p.get('gestione_separata_generali', 0),
        'etf_persona1': round(etf_persona1, 0),
        'etf_figlio': round(etf_figlio, 0),
        'azioni_acn_usd': round(azioni_usd, 0),
        'liquidita': (p.get('liquidita_persona1', 0) + p.get('liquidita_persona2', 0) +
                      p.get('conto_comune', 0) + p.get('affitto_accantonato', 0)),
    }
    snap['totale_eur'] = (snap['fondi_bancari'] + snap['generali'] +
                          snap['etf_persona1'] + snap['etf_figlio'] + snap['liquidita'])
    aliquota = config.get('parametri', {}).get(
        'aliquota_capital_gain',
        config.get('migrazione_fondi', {}).get('aliquota_capital_gain', 0.26))
    tassa_latente = p.get('fondi_plusvalenze', 0) * aliquota
    snap['tassa_latente_fondi'] = round(tassa_latente, 0)
    snap['totale_netto_fiscale'] = round(snap['totale_eur'] - tassa_latente, 0)
    return snap


