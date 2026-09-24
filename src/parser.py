"""
Parser principale: legge tutti i file nella cartella data/input/,
usa l'adapter corretto per ciascuno, restituisce DataFrame unificato.
"""

import pandas as pd
from pathlib import Path
from typing import List
import yaml

import adapters as _adapters_mod
from adapters import get_adapter, Transaction


def _carica_da_db(chiave: str):
    """Carica un valore da Supabase config_params. Ritorna None se non disponibile."""
    try:
        from database import carica_param
        return carica_param(chiave)
    except Exception:
        return None


def load_config(config_path: Path = None) -> dict:
    if config_path is None:
        config_path = Path(__file__).parent.parent / 'config.yaml'
    with open(config_path, encoding='utf-8') as f:
        return yaml.safe_load(f)


def parse_all_inputs(input_dir: Path = None, config: dict = None) -> pd.DataFrame:
    """
    Scansiona input_dir, identifica ogni file tramite config,
    lo parsa con l'adapter corretto, restituisce DataFrame unificato.
    """
    if input_dir is None:
        input_dir = Path(__file__).parent.parent / 'data' / 'input'
    if config is None:
        config = load_config()

    banche = config.get('banche', [])
    all_transactions: List[Transaction] = []

    # Inietta keyword trasferimenti interni da Supabase
    kw = _carica_da_db('transfer_keywords')
    if isinstance(kw, list) and kw:
        _adapters_mod.TRANSFER_KEYWORDS = kw

    for banca in banche:
        bank_id = banca['formato']
        account = banca['intestatario']

        # Pattern: prima Supabase (pattern_file_<banca_id>), poi config.yaml, poi skip
        pattern = _carica_da_db(f"pattern_file_{banca['id']}") or banca.get('pattern_file')
        if not pattern:
            print(f"  [!] Pattern non configurato per {banca['nome']}.")
            print(f"      → Aggiungi 'pattern_file_{banca['id']}' in Supabase config_params.")
            continue

        files = list(input_dir.glob(pattern))
        if not files:
            print(f"  [!] Nessun file trovato per pattern '{pattern}' ({banca['nome']})")
            continue

        adapter = get_adapter(bank_id)
        for filepath in files:
            print(f"  [+] Parsing {filepath.name} ({banca['nome']})...")
            try:
                txns = adapter.parse(filepath, account)
                all_transactions.extend(txns)
                print(f"      → {len(txns)} transazioni caricate")
            except Exception as e:
                print(f"  [!] Errore su {filepath.name}: {e}")

    if not all_transactions:
        print("  [!] Nessuna transazione trovata. Controlla i file in data/input/")
        return pd.DataFrame(columns=[
            'date', 'amount', 'description', 'category', 'account', 'raw_category'
        ])

    df = pd.DataFrame([{
        'date': t.date,
        'amount': t.amount,
        'description': t.description,
        'category': t.category,
        'account': t.account,
        'raw_category': t.raw_category
    } for t in all_transactions])

    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date', ascending=False).reset_index(drop=True)
    df['month'] = df['date'].dt.to_period('M')
    df['year'] = df['date'].dt.year
    df['income'] = df['amount'].clip(lower=0)
    df['expense'] = df['amount'].clip(upper=0).abs()

    return df


def monthly_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Riepilogo entrate/uscite per mese e conto."""
    if df.empty:
        return pd.DataFrame()

    summary = df.groupby(['month', 'account']).agg(
        entrate=('income', 'sum'),
        uscite=('expense', 'sum'),
        n_transazioni=('amount', 'count')
    ).reset_index()
    summary['netto'] = summary['entrate'] - summary['uscite']
    return summary


def category_summary(df: pd.DataFrame, months: int = 3) -> pd.DataFrame:
    """Uscite per categoria negli ultimi N mesi."""
    if df.empty:
        return pd.DataFrame()

    latest = df['month'].max()
    recent = df[df['month'] >= (latest - months + 1)]
    cat = recent[recent['amount'] < 0].groupby('category')['expense'].sum()
    return cat.sort_values(ascending=False).reset_index()


if __name__ == "__main__":
    print("=== Test parser ===")
    cfg = load_config()
    df = parse_all_inputs(config=cfg)
    if not df.empty:
        print(f"\nTotale transazioni: {len(df)}")
        print(f"Periodo: {df['date'].min().date()} → {df['date'].max().date()}")
        print(f"\nRiepilogo mensile:\n{monthly_summary(df)}")
        print(f"\nTop categorie uscite:\n{category_summary(df)}")
