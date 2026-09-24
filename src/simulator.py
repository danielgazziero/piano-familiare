"""
Simulatore: PAC, migrazione fondi, scenari famiglia, Flor, best/worst case.
"""

import numpy as np
import pandas as pd
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from typing import List, Dict, Tuple

from parser import load_config


def simula_pac(importo_mensile, anni, rendimento_annuo=0.07, data_inizio=None):
    if data_inizio is None:
        data_inizio = date.today().replace(day=1)
    tasso = rendimento_annuo / 12
    rows = []
    versato = valore = 0
    for i in range(anni * 12 + 1):
        d = data_inizio + relativedelta(months=i)
        if i > 0:
            valore = (valore + importo_mensile) * (1 + tasso)
            versato += importo_mensile
        rows.append({'data': d, 'anno': round(i/12, 4),
                     'versato': round(versato, 2), 'valore': round(valore, 2),
                     'rendimento': round(max(valore - versato, 0), 2)})
    return pd.DataFrame(rows)


def simula_pac_variabile(fasi, anni_totali, rendimento_annuo=0.07, data_inizio=None):
    if data_inizio is None:
        data_inizio = date.today().replace(day=1)
    tasso = rendimento_annuo / 12
    mesi_tot = anni_totali * 12
    importi = []
    for fase in fasi:
        importi.extend([fase['importo']] * fase['mesi'])
    while len(importi) < mesi_tot:
        importi.append(fasi[-1]['importo'])
    importi = importi[:mesi_tot]
    rows = []
    versato = valore = 0
    for i in range(mesi_tot + 1):
        d = data_inizio + relativedelta(months=i)
        imp = importi[i-1] if i > 0 else 0
        if i > 0:
            valore = (valore + imp) * (1 + tasso)
            versato += imp
        rows.append({'data': d, 'anno': round(i/12, 4), 'versato': round(versato, 2),
                     'valore': round(valore, 2), 'importo_mese': imp})
    return pd.DataFrame(rows)


def simula_pac_scenari(importo_mensile, anni,
                        rend_base=0.07, rend_worst=0.03, rend_best=0.10,
                        data_inizio=None):
    """Restituisce tre curve: base, worst, best per un PAC fisso."""
    return {
        'base': simula_pac(importo_mensile, anni, rend_base, data_inizio),
        'worst': simula_pac(importo_mensile, anni, rend_worst, data_inizio),
        'best': simula_pac(importo_mensile, anni, rend_best, data_inizio),
    }


def simula_fondo_scenari(valore_attuale, costo_fiscale, quantita_pct=1.0,
                          anni=10, rend_base=0.04, rend_worst=0.00, rend_best=0.08,
                          costo_annuo=0.02, aliquota=0.26):
    """
    Simula l'evoluzione di un fondo (o quota di esso) in 3 scenari.
    Restituisce valore lordo, tassa latente e netto per ogni anno.
    """
    capitale = valore_attuale * quantita_pct
    costo_fisc = costo_fiscale * quantita_pct

    rows = []
    for rend_label, rend in [('base', rend_base), ('worst', rend_worst), ('best', rend_best)]:
        val = capitale
        for anno in range(anni + 1):
            pv = max(val - costo_fisc, 0)
            tassa = pv * aliquota
            netto = val - tassa
            rows.append({
                'scenario': rend_label, 'anno': anno,
                'valore_lordo': round(val, 2),
                'plusvalenza': round(pv, 2),
                'tassa_latente': round(tassa, 2),
                'netto_uscita': round(netto, 2)
            })
            val = val * (1 + rend - costo_annuo)
    return pd.DataFrame(rows)


def simula_portafoglio_fondi_scenari(fondi_rows, anni=10,
                                      rend_base=0.04, rend_worst=0.00, rend_best=0.08,
                                      costo_annuo_default=0.02, aliquota=0.26):
    """
    fondi_rows: lista di dict con nome, valore_attuale, costo_fiscale_stimato,
                pct_da_mantenere (0-100), costo_annuo (opzionale).
    Simula l'aggregato in 3 scenari.
    """
    costi = {
        'ARCA AZ EUROPA CLIMA': 0.020, 'ARCA AZ AMERICA CLIMA P': 0.020,
        'EURIZON AZ EMERG P': 0.025, 'JPMF GLO SUST EQ ACC': 0.022,
        'EURIZON AZ AMER P': 0.020, 'EURIZ AZ AREA EURO P': 0.019,
        'EURIZON AZ INT P': 0.018,
    }

    risultati = {}
    for scenario, rend in [('base', rend_base), ('worst', rend_worst), ('best', rend_best)]:
        anni_data = []
        for anno in range(anni + 1):
            tot_lordo = tot_netto = 0
            for row in fondi_rows:
                pct = row.get('pct_da_mantenere', 100) / 100
                if pct <= 0:
                    continue
                val_init = row['valore_attuale'] * pct
                cf_init = row['costo_fiscale_stimato'] * pct
                ter = row.get('costo_annuo', costi.get(row['nome'], costo_annuo_default))
                val = val_init * ((1 + rend - ter) ** anno)
                pv = max(val - cf_init, 0)
                tassa = pv * aliquota
                tot_lordo += val
                tot_netto += val - tassa
            anni_data.append({
                'scenario': scenario, 'anno': anno,
                'valore_lordo': round(tot_lordo, 0),
                'netto_uscita': round(tot_netto, 0)
            })
        risultati[scenario] = pd.DataFrame(anni_data)

    return risultati


def simula_portafoglio_etf_scenari(etf_rows, anni=20,
                                    rend_base=0.07, rend_worst=0.03, rend_best=0.10):
    """
    etf_rows: lista di dict con nome, importo_mensile, valore_iniziale.
    Simula il portafoglio ETF aggregato in 3 scenari con PAC.
    """
    risultati = {}
    for scenario, rend in [('base', rend_base), ('worst', rend_worst), ('best', rend_best)]:
        tasso = rend / 12
        rows_s = []
        # Accumula ogni ETF separatamente poi somma
        valori = {r['nome']: r.get('valore_iniziale', 0) for r in etf_rows}
        for mese in range(anni * 12 + 1):
            tot = 0
            for r in etf_rows:
                if mese > 0:
                    valori[r['nome']] = (valori[r['nome']] + r.get('importo_mensile', 0)) * (1 + tasso)
                tot += valori[r['nome']]
            if mese % 12 == 0:
                rows_s.append({'scenario': scenario, 'anno': mese // 12,
                                'valore': round(tot, 0)})
        risultati[scenario] = pd.DataFrame(rows_s)
    return risultati


def simula_migrazione_fondi(config, rimborso_annuale=22000, rendimento_etf=0.07,
                              rendimento_fondi=0.04, costo_fondi_annuo=0.02):
    p = config['patrimonio']
    capitale_fondi = p['fondi_bancari']
    costo_fiscale = p['fondi_costo_fiscale']
    aliquota = config['migrazione_fondi']['aliquota_capital_gain']
    anni = int(np.ceil(capitale_fondi / rimborso_annuale))
    rows = []
    valore_fondi = capitale_fondi
    valore_etf = 0
    fondi_residui = capitale_fondi
    costo_fiscale_residuo = costo_fiscale

    for anno in range(anni + 3):
        if fondi_residui > 0 and anno < anni + 1:
            rimborso = min(rimborso_annuale, fondi_residui)
            quota_pv = (rimborso / fondi_residui) * max(fondi_residui - costo_fiscale_residuo, 0)
            tassa = quota_pv * aliquota
            netto = rimborso - tassa
            valore_etf += netto
            fondi_residui -= rimborso
            costo_fiscale_residuo = max(costo_fiscale_residuo - (rimborso - quota_pv), 0)
        valore_etf *= (1 + rendimento_etf)
        valore_fondi *= (1 + rendimento_fondi - costo_fondi_annuo)
        pv_fondi = max(valore_fondi - costo_fiscale, 0)
        rows.append({
            'anno': anno,
            'scenario_fondi': round(valore_fondi * (1 - aliquota * pv_fondi / valore_fondi
                                                      if valore_fondi > 0 else 0), 0),
            'scenario_etf': round(valore_etf + fondi_residui * (1 - aliquota), 0),
            'fondi_residui': round(fondi_residui, 0),
            'etf_accumulato': round(valore_etf, 0)
        })
    df = pd.DataFrame(rows)
    df['vantaggio_etf'] = df['scenario_etf'] - df['scenario_fondi']
    return df


def simula_costi_flor(config, eta_max=23):
    nascita = datetime.strptime(config['date']['nascita_flor'], '%Y-%m-%d').date()
    rows = []
    for eta in range(eta_max + 1):
        data = nascita + relativedelta(years=eta)
        voce, costo = 'Nessun costo specifico', 0
        for fascia in config.get('costi_flor', []):
            if fascia['eta_da'] <= eta < fascia['eta_a']:
                voce, costo = fascia['voce'], fascia['mensile']
                break
        rows.append({'eta': eta, 'anno': data.year, 'voce': voce,
                     'costo_mensile': costo, 'costo_annuale': costo * 12})
    return pd.DataFrame(rows)


def simula_scenario_completo(config, pac_persona1=None, pac_flor=None,
                               rendimento=0.07, anni=20):
    if pac_persona1 is None:
        pac_persona1 = config['allocazione']['pac_persona1_ora']
    if pac_flor is None:
        pac_flor = config['allocazione']['pac_flor']
    p = config['patrimonio']
    nascita_flor = datetime.strptime(config['date']['nascita_flor'], '%Y-%m-%d').date()
    inizio_nido = datetime.strptime(config['date']['inizio_asilo_nido'], '%Y-%m-%d').date()
    tasso = rendimento / 12
    data_inizio = date.today().replace(day=1)
    liquidita = (p['liquidita_persona1'] + p['liquidita_persona2'] +
                 p['conto_comune'] + p['affitto_accantonato'])
    etf_persona1, etf_flor = p['etf_cspx_directa'], 0
    fondi, generali = p['fondi_bancari'], p['gestione_separata_generali']
    generali_attivo = True
    rows = []
    for mese in range(anni * 12 + 1):
        dc = data_inizio + relativedelta(months=mese)
        pac_d = (config['allocazione']['pac_persona1_con_nido']
                 if dc >= inizio_nido else pac_persona1)
        eta_anni = (dc - nascita_flor).days / 365.25
        costo_flor = 0
        for f in config.get('costi_flor', []):
            if f['eta_da'] <= eta_anni < f['eta_a']:
                costo_flor = f['mensile']
                break
        if mese > 0:
            etf_persona1 = (etf_persona1 + pac_d) * (1 + tasso)
            etf_flor = (etf_flor + pac_flor) * (1 + tasso)
            fondi *= (1 + 0.04/12 - 0.02/12)
            if generali_attivo:
                generali = generali * (1 + 0.03/12) + p['generali_versamento_mensile']
            scad = datetime.strptime(config['date']['scadenza_generali'], '%Y-%m-%d').date()
            if dc >= scad and generali_attivo:
                etf_persona1 += generali * 0.74
                generali = 0
                generali_attivo = False
        totale = etf_persona1 + etf_flor + fondi + generali + liquidita
        rows.append({'data': dc, 'anno': round(mese/12, 2),
                     'etf_persona1': round(etf_persona1, 0), 'etf_flor': round(etf_flor, 0),
                     'fondi': round(fondi, 0), 'generali': round(generali, 0),
                     'liquidita': round(liquidita, 0), 'totale': round(totale, 0),
                     'costo_flor_mese': costo_flor, 'pac_persona1_mese': pac_d if mese > 0 else 0})
    return pd.DataFrame(rows)


def confronta_scenari(config):
    return {
        'Conservativo (3%)': simula_scenario_completo(config, rendimento=0.03),
        'Base (7%)': simula_scenario_completo(config, rendimento=0.07),
        'Ottimista (10%)': simula_scenario_completo(config, rendimento=0.10),
    }
