"""
Dati fittizi per DEMO_MODE=true — nessun dato reale esposto.
Attivato tramite DEMO_MODE = true in .streamlit/secrets.toml.
"""

import pandas as pd
import numpy as np
from datetime import date, timedelta

_RNG = np.random.default_rng(42)

# ─── ISINs fondi (stessi del config.yaml, valori demo) ───────
_FONDI_DEMO = [
    {"isin": "IT0001033486", "nome": "ARCA AZ EUROPA CLIMA",    "quantita": 30.0,    "quota": 30.00},
    {"isin": "IT0001033502", "nome": "ARCA AZ AMERICA CLIMA P", "quantita": 50.0,    "quota": 120.00},
    {"isin": "IT0001031928", "nome": "EURIZON AZ EMERG P",      "quantita": 800.0,   "quota": 18.00},
    {"isin": "LU2293888439", "nome": "JPMF GLO SUST EQ ACC",    "quantita": 100.0,   "quota": 160.00},
    {"isin": "IT0001050126", "nome": "EURIZON AZ AMER P",       "quantita": 500.0,   "quota": 70.00},
    {"isin": "IT0001050225", "nome": "EURIZ AZ AREA EURO P",    "quantita": 400.0,   "quota": 75.00},
    {"isin": "IT0001080446", "nome": "EURIZON AZ INT P",        "quantita": 1500.0,  "quota": 40.00},
]


def is_demo_mode() -> bool:
    try:
        import streamlit as st
        return bool(st.secrets.get("DEMO_MODE", False))
    except Exception:
        return False


# ─── Params patrimonio ────────────────────────────────────────

def demo_carica_params_persistenti(config: dict) -> dict:
    return {
        "fondi_bancari":              140000,
        "gestione_separata_generali": 30000,
        "liquidita_daniel":           8000,
        "liquidita_alessandra":       6000,
        "conto_comune":               12000,
        "affitto_accantonato":        2000,
    }


def demo_salva_params_persistenti(params: dict) -> bool:
    return True


# ─── Quote fondi ─────────────────────────────────────────────

def demo_carica_quote_fondi_persistenti(config: dict) -> dict:
    return {f["isin"]: f["quota"] for f in _FONDI_DEMO}


def demo_aggiorna_quote_fondi(quote_map: dict, config: dict) -> bool:
    return True


# ─── DB connection ────────────────────────────────────────────

def demo_init_db_connection() -> bool:
    return True


def demo_auto_save_snapshot(snapshot: dict, params: dict) -> bool:
    return True


def demo_importa_transazioni_xls(df_tx) -> int:
    return 0


def demo_esegui_backfill_avvio(config: dict, verbose: bool = False) -> dict:
    return {"n_prezzi_scaricati": 0, "dettaglio": {}}


def demo_aggiorna_quantita_asset(isin: str, nuova_quantita: float,
                                  data_modifica, note: str = None) -> bool:
    return True


# ─── Storico patrimonio ───────────────────────────────────────

def demo_get_storico_patrimonio(giorni: int = 365) -> pd.DataFrame:
    today = date.today()
    days = [today - timedelta(days=i) for i in range(giorni - 1, -1, -1)]
    n = len(days)

    totale = np.linspace(210_000, 260_000, n) + _RNG.normal(0, 600, n).cumsum() * 0.25

    fondi   = totale * 0.52
    generali= totale * 0.12
    etf_dan = totale * 0.10 + _RNG.normal(0, 200, n).cumsum() * 0.1
    etf_flo = np.zeros(n)
    acn_usd = totale * 0.14 + _RNG.normal(0, 300, n).cumsum() * 0.1
    liq     = totale * 0.10

    return pd.DataFrame({
        "data":                 days,
        "fondi_bancari":        fondi.round(0),
        "generali":             generali.round(0),
        "etf_daniel":           etf_dan.round(0),
        "etf_flor":             etf_flo,
        "azioni_acn_usd":       acn_usd.round(0),
        "liquidita":            liq.round(0),
        "totale_eur":           totale.round(0),
        "totale_netto_fiscale": (totale * 0.96).round(0),
        "tassa_latente_fondi":  (totale * 0.04).round(0),
    })


# ─── Storico fondo ────────────────────────────────────────────

def demo_get_storico_fondo(isin: str, giorni: int = 180) -> pd.DataFrame:
    fondo = next((f for f in _FONDI_DEMO if f["isin"] == isin), _FONDI_DEMO[0])
    today = date.today()
    days = [today - timedelta(days=i) for i in range(giorni - 1, -1, -1)]
    n = len(days)

    quota_start = fondo["quota"] * 0.92
    quota_vals  = np.linspace(quota_start, fondo["quota"], n) + _RNG.normal(0, fondo["quota"] * 0.005, n)
    valore_vals = quota_vals * fondo["quantita"]

    return pd.DataFrame({
        "data":     days,
        "isin":     isin,
        "quota":    quota_vals.round(4),
        "valore":   valore_vals.round(2),
        "quantita": fondo["quantita"],
        "fonte":    "demo",
    })


# ─── Transazioni ─────────────────────────────────────────────

def demo_carica_transazioni_db(mesi: int = 12) -> pd.DataFrame:
    today = date.today()
    rows = []

    for m in range(mesi):
        base = today.replace(day=1) - timedelta(days=30 * m)

        def d(offset=0):
            return base + timedelta(days=int(offset))

        rows += [
            (d(1),  2600,   "ACCREDITO STIPENDIO DANIEL",       "entrate",  "daniel"),
            (d(1),  1600,   "ACCREDITO STIPENDIO ALESSANDRA",   "entrate",  "alessandra"),
            (d(5),   300,   "AFFITTO CHIARA",                   "entrate",  "conto_comune"),
            (d(3), -1317,   "RATA MUTUO",                       "mutuo",    "conto_comune"),
            (d(6),  -280,   "ESSELUNGA SPESA",                  "spesa",    "daniel"),
            (d(13), -265,   "CONAD SUPERMERCATO",               "spesa",    "alessandra"),
            (d(20), -310,   "CARREFOUR SPESA",                  "spesa",    "daniel"),
            (d(8),  -120,   "ENI GAS E LUCE",                   "bollette", "conto_comune"),
            (d(10), -55,    "TIM FIBRA",                        "bollette", "conto_comune"),
            (d(15), -65,    "AMAZON PRIME / ORDINI",            "varie",    "daniel"),
            (d(18), -42 + _RNG.integers(-20, 20),
                            "BAR / RISTORANTE",                 "varie",    "daniel"),
            (d(22), -85,    "FARMACIA",                         "salute",   "alessandra"),
        ]

    df = pd.DataFrame(rows, columns=["data", "importo", "descrizione", "categoria", "conto"])
    df["hash_tx"] = [f"demo_{i:04d}" for i in range(len(df))]
    df["data"] = pd.to_datetime(df["data"])
    return df.sort_values("data", ascending=False).reset_index(drop=True)


# ─── Storico portafoglio ──────────────────────────────────────

def demo_get_storico_portafoglio(data_inizio=None, data_fine=None) -> pd.DataFrame:
    today = date.today()
    if data_inizio is None:
        data_inizio = today - timedelta(days=365)
    if data_fine is None:
        data_fine = today

    days = pd.date_range(data_inizio, data_fine, freq="D")
    n = len(days)

    # CSPX: cresce da ~8.500 a ~11.200
    cspx_vals = np.linspace(8_500, 11_200, n) + _RNG.normal(0, 80, n).cumsum() * 0.2

    return pd.DataFrame({
        "data":   days,
        "isin":   "IE00B5BMR087",
        "nome":   "iShares Core S&P 500",
        "valore": cspx_vals.round(2),
    })


def demo_get_storico_asset(isin: str, data_inizio=None, data_fine=None) -> pd.DataFrame:
    today = date.today()
    if data_inizio is None:
        data_inizio = today - timedelta(days=365)
    if data_fine is None:
        data_fine = today

    days = pd.date_range(data_inizio, data_fine, freq="D")
    n = len(days)
    prezzo = np.linspace(400, 520, n) + _RNG.normal(0, 5, n)
    quantita = 71
    valore = prezzo * quantita * 0.92  # USD→EUR approssimato

    return pd.DataFrame({
        "data":     days,
        "isin":     isin,
        "prezzo":   prezzo.round(2),
        "quantita": quantita,
        "valore":   valore.round(2),
    })


def demo_get_eventi_portafoglio(data_inizio=None, data_fine=None) -> pd.DataFrame:
    return pd.DataFrame(columns=["data", "isin", "tipo", "quantita", "prezzo", "note"])
