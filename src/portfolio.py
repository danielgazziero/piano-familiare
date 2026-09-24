"""
Portfolio: valorizza ETF (yfinance), fondi bancari (quote manuali),
azioni Accenture (yfinance), piano di uscita ottimale fondi.
"""

import pandas as pd
import numpy as np
from datetime import datetime, date
from typing import Dict, List, Optional
import yfinance as yf
from pathlib import Path
import json

from parser import load_config


def get_etf_data(ticker: str, period: str = "1y") -> pd.DataFrame:
    try:
        obj = yf.Ticker(ticker)
        hist = obj.history(period=period)
        if hist.empty:
            return pd.DataFrame()
        return hist[['Close']].rename(columns={'Close': 'price'})
    except Exception as e:
        print(f"  [!] Errore download {ticker}: {e}")
        return pd.DataFrame()


def get_etf_history_chart(ticker: str, period: str = "1y") -> pd.DataFrame:
    hist = get_etf_data(ticker, period)
    if hist.empty:
        return hist
    hist['indexed'] = hist['price'] / hist['price'].iloc[0] * 100
    return hist


def get_portfolio_performance(config: dict) -> pd.DataFrame:
    results = []
    for etf in config.get('etf', []):
        ticker = etf['ticker_yf']
        nome = etf['nome']
        valore_iniziale = etf.get('valore_iniziale', 0)
        proprietario = etf.get('proprietario', 'persona1')
        stato = etf.get('stato', 'candidato')

        hist_ytd = get_etf_data(ticker, period="ytd")
        hist_1y = get_etf_data(ticker, period="1y")

        perf_ytd = perf_1y = prezzo_attuale = None
        valore_attuale = valore_iniziale

        if not hist_ytd.empty:
            perf_ytd = round((hist_ytd['price'].iloc[-1] / hist_ytd['price'].iloc[0] - 1) * 100, 2)
            prezzo_attuale = round(float(hist_ytd['price'].iloc[-1]), 2)
            if valore_iniziale > 0:
                valore_attuale = round(valore_iniziale * (1 + perf_ytd / 100), 2)
        if not hist_1y.empty:
            perf_1y = round((hist_1y['price'].iloc[-1] / hist_1y['price'].iloc[0] - 1) * 100, 2)

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


def get_fondi_snapshot(config: dict, quote_aggiornate: Dict[str, float] = None) -> pd.DataFrame:
    fondi = config.get('fondi_bancari', {}).get('titoli', [])
    aliquota = config['fondi_bancari']['aliquota_capital_gain']
    tot_valore_ref = sum(f['quantita'] * f['valore_quota_ref'] for f in fondi)
    costo_fiscale_tot = config['patrimonio']['fondi_costo_fiscale']
    rows = []

    for fondo in fondi:
        quota = fondo['valore_quota_ref']
        if quote_aggiornate and fondo['isin'] in quote_aggiornate:
            quota = quote_aggiornate[fondo['isin']]

        valore_attuale = round(fondo['quantita'] * quota, 2)
        valore_ref = round(fondo['quantita'] * fondo['valore_quota_ref'], 2)
        peso = valore_ref / tot_valore_ref if tot_valore_ref > 0 else 0
        costo_fiscale_stimato = round(costo_fiscale_tot * peso, 2)
        plusvalenza_stimata = max(valore_attuale - costo_fiscale_stimato, 0)
        tassa_latente = round(plusvalenza_stimata * aliquota, 2)
        netto_uscita = round(valore_attuale - tassa_latente, 2)

        rows.append({
            'nome': fondo['nome'], 'isin': fondo['isin'],
            'quantita': fondo['quantita'],
            'quota_ref': fondo['valore_quota_ref'],
            'quota_attuale': quota, 'data_ref': fondo['data_ref'],
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
    df_proj['tassa_proiettata'] = (df_proj['plusvalenza_proiettata'] * 0.26).round(2)
    df_proj['netto_proiettato'] = (df_proj['valore_proiettato'] - df_proj['tassa_proiettata']).round(2)
    df_proj['data_uscita'] = data_uscita
    df_proj['mesi_attesa'] = round(giorni / 30.44, 1)
    return df_proj[['nome', 'isin', 'quantita', 'valore_attuale', 'netto_uscita',
                    'valore_proiettato', 'tassa_proiettata', 'netto_proiettato',
                    'mesi_attesa', 'data_uscita']]


def piano_uscita_ottimale(config: dict, quote_aggiornate: Dict[str, float] = None) -> pd.DataFrame:
    df = get_fondi_snapshot(config, quote_aggiornate)
    piano_cfg = config['migrazione_fondi']['piano_annuale']

    costi_stimati = {
        'ARCA AZ EUROPA CLIMA': 0.020, 'ARCA AZ AMERICA CLIMA P': 0.020,
        'EURIZON AZ EMERG P': 0.025, 'JPMF GLO SUST EQ ACC': 0.022,
        'EURIZON AZ AMER P': 0.020, 'EURIZ AZ AREA EURO P': 0.019,
        'EURIZON AZ INT P': 0.018,
    }
    df['costo_annuo_stimato'] = df['nome'].map(costi_stimati).fillna(0.02)
    df['priorita'] = df['costo_annuo_stimato'] * df['valore_attuale']
    df = df.sort_values('priorita', ascending=False).reset_index(drop=True)

    piano_rows = []
    fondi_residui = df.copy()

    for anno_cfg in piano_cfg:
        budget = anno_cfg['rimborso_lordo']
        anno_num = anno_cfg['anno']
        budget_residuo = budget

        for idx, row in fondi_residui.iterrows():
            if budget_residuo <= 0 or row['valore_attuale'] <= 0:
                continue
            importo = min(row['valore_attuale'], budget_residuo)
            quota_pv = (importo / row['valore_attuale']) * row['plusvalenza_stimata'] if row['valore_attuale'] > 0 else 0
            tassa = round(quota_pv * 0.26, 2)
            netto = round(importo - tassa, 2)
            budget_residuo -= importo
            fondi_residui.at[idx, 'valore_attuale'] = max(row['valore_attuale'] - importo, 0)
            fondi_residui.at[idx, 'plusvalenza_stimata'] = max(row['plusvalenza_stimata'] - quota_pv, 0)

            piano_rows.append({
                'anno': anno_num, 'fondo': row['nome'], 'isin': row['isin'],
                'rimborso_lordo': round(importo, 0),
                'tassa_26pct': round(tassa, 0),
                'netto_in_etf': round(netto, 0),
                'etf_destinazione': 'IWDA / VWCE',
                'motivo': f"TER ~{row['costo_annuo_stimato']*100:.1f}%"
            })

    return pd.DataFrame(piano_rows)


def get_azioni_snapshot(config: dict) -> pd.DataFrame:
    azioni = config.get('azioni', [])
    rows = []
    for az in azioni:
        ticker = az['ticker_yf']
        quantita = az['quantita']
        hist_ytd = get_etf_data(ticker, period="ytd")
        hist_1y = get_etf_data(ticker, period="1y")

        prezzo_attuale = valore_attuale = perf_ytd = perf_1y = None
        if not hist_ytd.empty:
            prezzo_attuale = round(float(hist_ytd['price'].iloc[-1]), 2)
            valore_attuale = round(prezzo_attuale * quantita, 2)
            perf_ytd = round((hist_ytd['price'].iloc[-1] / hist_ytd['price'].iloc[0] - 1) * 100, 2)
        if not hist_1y.empty:
            perf_1y = round((hist_1y['price'].iloc[-1] / hist_1y['price'].iloc[0] - 1) * 100, 2)

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
    etf_flor = 0
    if etf_df is not None and not etf_df.empty:
        etf_persona1 = etf_df[(etf_df['proprietario'] == 'persona1') &
                               (etf_df['stato'] == 'attivo')]['valore_attuale'].sum()
        etf_flor = etf_df[etf_df['proprietario'] == 'flor']['valore_attuale'].sum()

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
        'etf_flor': round(etf_flor, 0),
        'azioni_acn_usd': round(azioni_usd, 0),
        'liquidita': (p.get('liquidita_persona1', 0) + p.get('liquidita_persona2', 0) +
                      p.get('conto_comune', 0) + p.get('affitto_accantonato', 0)),
    }
    snap['totale_eur'] = (snap['fondi_bancari'] + snap['generali'] +
                          snap['etf_persona1'] + snap['etf_flor'] + snap['liquidita'])
    tassa_latente = p.get('fondi_plusvalenze', 0) * 0.26
    snap['tassa_latente_fondi'] = round(tassa_latente, 0)
    snap['totale_netto_fiscale'] = round(snap['totale_eur'] - tassa_latente, 0)
    return snap


def load_patrimonio_log(log_path: Path = None) -> pd.DataFrame:
    if log_path is None:
        log_path = Path(__file__).parent.parent / 'data' / 'reference' / 'patrimonio_log.json'
    try:
        with open(log_path) as f:
            log = json.load(f)
        df = pd.DataFrame(log).T
        df.index = pd.to_datetime(df.index + '-01')
        return df.apply(pd.to_numeric, errors='coerce')
    except (FileNotFoundError, json.JSONDecodeError):
        return pd.DataFrame()


def log_patrimonio(snapshot: dict, log_path: Path = None):
    if log_path is None:
        log_path = Path(__file__).parent.parent / 'data' / 'reference' / 'patrimonio_log.json'
    mese = datetime.now().strftime('%Y-%m')
    try:
        with open(log_path) as f:
            log = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        log = {}
    log[mese] = {k: round(v, 2) if isinstance(v, float) else v for k, v in snapshot.items()}
    with open(log_path, 'w') as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
