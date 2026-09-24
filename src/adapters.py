"""
Adapter layer: ogni banca ha il suo adapter.
Tutti producono lo stesso formato interno (lista di Transaction).
Per aggiungere una nuova banca: creare una nuova classe che estende BankAdapter.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
import pandas as pd
from pathlib import Path





@dataclass
class Transaction:
    date: datetime
    amount: float          # negativo = uscita, positivo = entrata
    description: str
    category: str
    account: str           # "daniel" | "alessandra" | "comune"
    raw_category: str = "" # categoria originale della banca


MESI_IT = {
    'gennaio': '01', 'febbraio': '02', 'marzo': '03', 'aprile': '04',
    'maggio': '05', 'giugno': '06', 'luglio': '07', 'agosto': '08',
    'settembre': '09', 'ottobre': '10', 'novembre': '11', 'dicembre': '12'
}

TRANSFER_KEYWORDS = [
    'GAZZIERO DANIEL', 'CRIPPA ALESSANDRA', 'ALESSANDRA CRIPPA',
    'BONIFICO o/c: GAZZIERO DANIEL', 'BONIFICO o/c: CRIPPA'
]


def parse_date_it(s: str) -> Optional[datetime]:
    try:
        s = str(s).strip().lower()
        for m, n in MESI_IT.items():
            s = s.replace(m, n)
        return datetime.strptime(s, '%d %m %Y')
    except Exception:
        return None


def is_internal_transfer(description: str) -> bool:
    desc_upper = str(description).upper()
    return any(kw.upper() in desc_upper for kw in TRANSFER_KEYWORDS)


class BankAdapter:
    """Classe base — ogni banca eredita da questa."""
    bank_id = "base"

    def parse(self, filepath: Path, account: str) -> List[Transaction]:
        raise NotImplementedError

    def _categorize(self, raw_cat: str, description: str) -> str:
        """Mappa categorie banca → categorie interne standard."""
        desc = description.upper()
        raw = raw_cat.upper()

        if any(k in desc for k in ['AMAZON', 'AMZN', 'EBAY', 'ZALANDO', 'SHEIN', 'VINTED']):
            return 'Shopping online'
        if any(k in desc for k in ['CONAD', 'ESSELUNGA', 'IPER', 'CARREFOUR', 'COOP',
                                    'EUROSPIN', 'LIDL', 'PENNY', 'MAURY', 'RIALTO']):
            return 'Spesa alimentare'
        if any(k in desc for k in ['FARMACIA', 'COLOMBO', 'MANDIA', 'GRANDVISION',
                                    'DOTTORE', 'MEDIC', 'CLINICA', 'OSPEDALE']):
            return 'Spese mediche'
        if any(k in desc for k in ['TELECOMITALIA', 'TIM', 'ILIAD', 'SKY', 'WINDTRE']):
            return 'Abbonamenti'
        if any(k in desc for k in ['UNIPOLMOVE', 'AUTOSTRAD', 'PEDAGGIO']):
            return 'Auto/Trasporti'
        if any(k in desc for k in ['CARBURANTE', 'ENI', 'IP ', 'AGIP', 'TOTAL']):
            return 'Carburante'
        if any(k in desc for k in ['RISTORANTE', 'PIZZERIA', 'BAR ', 'BIRRA', 'TRATTORIA']):
            return 'Ristorazione'
        if any(k in desc for k in ['BOOKING', 'AIRBNB', 'HOTEL', 'TRENITALIA', 'ITALO',
                                    'RYANAIR', 'EASYJET', 'PAYPAL *VOL']):
            return 'Viaggi/Vacanze'
        if 'F24' in raw or 'DELEGA UNIFICATA' in desc:
            return 'Tasse/F24'
        if 'PRELIEVO' in raw or 'ATM' in raw:
            return 'Prelievo contante'
        if 'GENERALI' in desc or 'ALLIANZ' in desc:
            return 'Assicurazioni'
        if 'STIPEND' in desc or 'CEDOLINO' in desc:
            return 'Stipendio'
        if 'GSE' in desc or 'FOTOVOLTAICO' in desc:
            return 'Rimborso GSE'
        if 'BLUE ASSISTANCE' in desc:
            return 'Rimborso assicurazione'
        if 'CANONE' in raw or 'SPESE' in raw:
            return 'Spese bancarie'
        if 'SDD' in raw or 'RID' in raw:
            return 'Addebiti fissi'
        return 'Altro'


class BperDanielAdapter(BankAdapter):
    """
    Adapter per estratto BPER di Daniel.
    Formato: BIFF8 (Excel 97-2003 .xls) — letto con xlrd 1.2.0.
    Non richiede LibreOffice né Office installato.
    Header alla riga 17 (indice 0-based), 8 colonne.
    """
    bank_id = "bper_xls"

    def parse(self, filepath: Path, account: str = "daniel") -> List[Transaction]:
        import xlrd

        wb = xlrd.open_workbook(str(filepath))
        ws = wb.sheet_by_index(0)

        # Leggi tutte le righe dal foglio
        rows = []
        for i in range(ws.nrows):
            rows.append([ws.cell_value(i, j) for j in range(ws.ncols)])

        # Trova la riga header (contiene "Data operazione")
        header_idx = None
        for i, row in enumerate(rows):
            vals = [str(v).strip().lower() for v in row if v]
            if any('data operazione' in v or 'data op' in v for v in vals):
                header_idx = i
                break

        if header_idx is None:
            # Fallback: usa riga 17 come da storico
            header_idx = 17

        # Costruisci DataFrame dalla riga header in poi
        data_rows = rows[header_idx + 1:]
        # Prendi le colonne utili (scarta prima colonna vuota se presente)
        records = []
        for row in data_rows:
            # Filtra righe vuote
            vals = [v for v in row if str(v).strip()]
            if len(vals) < 4:
                continue
            # Colonne attese: [drop?], data_op, data_val, descrizione, entrate, uscite, categoria, stato
            if len(row) >= 8:
                record = {
                    'data_op':    str(row[1]).strip() if len(row) > 1 else '',
                    'descrizione':str(row[3]).strip() if len(row) > 3 else '',
                    'entrate':    row[4] if len(row) > 4 else 0,
                    'uscite':     row[5] if len(row) > 5 else 0,
                    'categoria':  str(row[6]).strip() if len(row) > 6 else '',
                }
                records.append(record)

        if not records:
            print(f"  [!] Nessuna transazione trovata in {filepath.name}")
            return []

        df = pd.DataFrame(records)

        # Gestisci date Excel numeriche (xlrd le restituisce come float)
        def parse_xls_date(val):
            if isinstance(val, float) and val > 0:
                try:
                    return xlrd.xldate_as_datetime(val, wb.datemode).date()
                except Exception:
                    pass
            return parse_date_it(str(val))

        df['data'] = df['data_op'].apply(
            lambda v: parse_xls_date(v) if isinstance(v, float) else parse_date_it(str(v))
        )
        df['entrate'] = pd.to_numeric(df['entrate'], errors='coerce').fillna(0)
        df['uscite']  = pd.to_numeric(df['uscite'],  errors='coerce').fillna(0)
        df = df[df['data'].notna()].copy()

        transactions = []
        for _, row in df.iterrows():
            desc = str(row['descrizione'])
            if is_internal_transfer(desc):
                continue
            amount  = row['entrate'] + row['uscite']
            raw_cat = str(row['categoria']) if row['categoria'] else ''
            transactions.append(Transaction(
                date=row['data'],
                amount=amount,
                description=desc,
                category=self._categorize(raw_cat, desc),
                account=account,
                raw_category=raw_cat
            ))
        return transactions


class BperAlessandraAdapter(BankAdapter):
    """
    Adapter per estratto BPER di Alessandra.
    Formato: XLSX mascherato da .xls, header a riga 20,
    9 colonne con colonna vuota iniziale.
    """
    bank_id = "bper_xls_new"

    def parse(self, filepath: Path, account: str = "alessandra") -> List[Transaction]:
        df = pd.read_excel(filepath, engine='openpyxl',
                           header=None, skiprows=21)
        df = df.iloc[:, :9]
        df.columns = ['drop', 'data_op', 'data_val', 'descrizione',
                      'entrate', 'uscite', 'note', 'categoria', 'stato']
        df = df.drop(columns=['drop']).dropna(subset=['data_op'])
        df['data'] = df['data_op'].apply(parse_date_it)
        df['entrate'] = pd.to_numeric(df['entrate'], errors='coerce').fillna(0)
        df['uscite'] = pd.to_numeric(df['uscite'], errors='coerce').fillna(0)
        df = df[df['data'].notna()].copy()

        transactions = []
        for _, row in df.iterrows():
            desc = str(row['descrizione'])
            if is_internal_transfer(desc):
                continue
            amount = row['entrate'] + row['uscite']
            raw_cat = str(row['categoria']) if pd.notna(row['categoria']) else ''
            transactions.append(Transaction(
                date=row['data'],
                amount=amount,
                description=desc,
                category=self._categorize(raw_cat, desc),
                account=account,
                raw_category=raw_cat
            ))
        return transactions


# ----------------------------------------------------------------
# TEMPLATE PER NUOVA BANCA
# Copia questo blocco e adattalo quando cambi banca
# ----------------------------------------------------------------
class NuovaBancaAdapter(BankAdapter):
    """
    Template per aggiungere una nuova banca.
    1. Copia questa classe
    2. Cambia bank_id con un identificatore unico (es. "fineco_xls")
    3. Adatta il metodo parse() al formato del file della nuova banca
    4. Aggiungi la nuova banca in config.yaml sotto 'banche'
    """
    bank_id = "nuova_banca"  # CAMBIARE

    def parse(self, filepath: Path, account: str) -> List[Transaction]:
        # TODO: implementare lettura file nuova banca
        # Esempio generico CSV:
        # df = pd.read_csv(filepath, ...)
        # ...
        # return [Transaction(...) for ...]
        raise NotImplementedError("Implementare il parser per questa banca")


# ----------------------------------------------------------------
# REGISTRY: mappa bank_id → classe adapter
# ----------------------------------------------------------------
ADAPTER_REGISTRY = {
    adapter.bank_id: adapter
    for adapter in [BperDanielAdapter, BperAlessandraAdapter]
}


def get_adapter(bank_id: str) -> BankAdapter:
    """Restituisce l'adapter corretto dato il bank_id dal config."""
    cls = ADAPTER_REGISTRY.get(bank_id)
    if cls is None:
        available = list(ADAPTER_REGISTRY.keys())
        raise ValueError(
            f"Adapter '{bank_id}' non trovato. "
            f"Disponibili: {available}"
        )
    return cls()
