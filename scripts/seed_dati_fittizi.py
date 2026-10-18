"""
Dati fittizi per provare categorizzazione AI e batch retroattivo.

Uso (dalla radice del progetto):
    python scripts/seed_dati_fittizi.py             # anteprima, non scrive nulla
    python scripts/seed_dati_fittizi.py --carica    # inserisce in Supabase (categoria NULL)
    python scripts/seed_dati_fittizi.py --pulisci   # elimina SOLO le righe di test

Le righe di test hanno conto = 'TEST-FITTIZIO' (e 'TEST-FITTIZIO-2' per i giroconti),
quindi la pulizia non tocca i dati reali. Nomi e identificativi sono inventati.
Per provare l'anonimizzazione aggiungi in "Privacy" i nomi: Mario Rossi, Anna Bianchi.
"""

import hashlib
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

CONTO, CONTO2 = 'TEST-FITTIZIO', 'TEST-FITTIZIO-2'

# (giorni fa, importo, descrizione)
_MODELLO = [
    (2,   -54.30, "PAGAMENTO POS COOP LOMBARDIA SUPERMERCATO CARTA 4111 1111 1111 1111"),
    (3,   -12.50, "PAGAMENTO POS BAR CENTRALE CAFFE"),
    (4,   -38.00, "CARBURANTE ENI STATION AUTOSTRADA"),
    (5,   -89.90, "ADDEBITO SDD ENEL ENERGIA UTENZA LUCE"),
    (6,   -19.99, "NETFLIX.COM ABBONAMENTO"),
    (7,  -120.00, "BONIFICO A FAVORE DI Mario Rossi CAUSALE REGALO IBAN IT60 X054 2811 1010 0000 0123 456"),
    (9,   -45.00, "FARMACIA SAN MARCO ACQUISTO"),
    (10,  -62.00, "RISTORANTE DA LUIGI CENA mario.rossi@example.com"),
    (12,  -25.00, "RICARICA TELEFONICA 3331234567"),
    (15,  -310.00, "ASILO NIDO IL GIRASOLE RETTA MENSILE Anna Bianchi"),
    (18,  -75.40, "AMAZON EU SARL ORDINE ELETTRONICA"),
    (21,  -33.20, "TRENITALIA BIGLIETTO REGIONALE"),
    (24,  -58.10, "PAGAMENTO POS ESSELUNGA SPESA"),
    (30, -850.00, "RATA MUTUO CASA"),
    (33,  -41.00, "PALESTRA FITNESS CLUB ABBONAMENTO"),
    (38,  -66.60, "COOP LOMBARDIA SUPERMERCATO"),
    (45,  -29.90, "TELEPASS PEDAGGI"),
    (52, -140.00, "DENTISTA STUDIO ROSSI VISITA CF RSSMRA80A01H501U"),
    (60,  -22.00, "CINEMA MULTISALA BIGLIETTI"),
    (75,  -95.00, "ASSICURAZIONE AUTO UNIPOL RATA"),
    (80,  -48.30, "SUPERMERCATO CONAD SPESA"),
    (95,  -17.50, "PIZZERIA BELLA NAPOLI"),
]
# giroconto: addebito su conto 1 e accredito su conto 2 stesso importo, 1 giorno di scarto
_GIRO = [(14, -500.00, CONTO, "GIROCONTO A CONTO RISPARMIO"),
         (13, 500.00, CONTO2, "GIROCONTO DA CONTO CORRENTE")]


def _righe() -> list:
    oggi = date.today()
    out = []
    for giorni, importo, desc in _MODELLO:
        out.append((oggi - timedelta(days=giorni), importo, desc, CONTO))
    for giorni, importo, conto, desc in _GIRO:
        out.append((oggi - timedelta(days=giorni), importo, desc, conto))
    return out


def _record(d, importo, desc, conto) -> dict:
    h = hashlib.sha256(f"TEST_{d}_{importo}_{desc}_{conto}".encode()).hexdigest()
    return {'data': d.isoformat(), 'importo': importo, 'descrizione': desc,
            'categoria': None, 'conto': conto, 'raw_categoria': '', 'hash_tx': h}


def main() -> None:
    righe = [_record(*r) for r in _righe()]
    if '--pulisci' in sys.argv:
        from database import get_client
        c = get_client()
        for conto in (CONTO, CONTO2):
            c.table('transazioni').delete().eq('conto', conto).execute()
        print("Righe di test eliminate.")
    elif '--carica' in sys.argv:
        from database import get_client
        resp = get_client().table('transazioni').upsert(righe, on_conflict='hash_tx', ignore_duplicates=True).execute()
        print(f"Inserite {len(resp.data or [])} righe di test (categoria NULL).")
    else:
        for r in righe:
            print(f"{r['data']}  {r['importo']:>9.2f}  {r['conto']:<15} {r['descrizione']}")
        print(f"\n{len(righe)} righe — anteprima. Usa --carica per inserirle.")


if __name__ == '__main__':
    main()
