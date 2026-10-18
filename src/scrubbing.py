"""
Layer di scrubbing anonimizzazione — obbligatorio prima di ogni chiamata AI.
Nessun dato identificativo esce in chiaro verso API esterne.
"""

import re
from datetime import date as _date, timedelta

_CF_RE    = re.compile(r'\b[A-Z]{6}[0-9]{2}[A-Z][0-9]{2}[A-Z][0-9]{3}[A-Z]\b', re.IGNORECASE)
# IBAN con eventuali spazi/trattini tra i gruppi (es. "IT60 X054 2811 ...")
_IBAN_RE  = re.compile(r'\b[A-Z]{2}\d{2}(?:[ \-]?[A-Z0-9]){11,30}\b', re.IGNORECASE)
_EMAIL_RE = re.compile(r'[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}', re.IGNORECASE)
# Numeri carta: 13-19 cifre, anche separate da spazi o trattini
_CARD_RE  = re.compile(r'\b(?:\d[ \-]?){12,18}\d\b')
# Telefoni IT (fissi/mobili), con prefisso +39/0039 opzionale
_PHONE_RE = re.compile(r'(?<!\d)(?:(?:\+|00)39[ .\-]?)?(?:3\d{2}|0\d{1,3})[ .\-]?\d{6,7}(?!\d)')


def _nome_pattern(nome: str):
    """Match case-insensitive del nome come parola intera (non dentro altre parole)."""
    return re.compile(r'(?<!\w)' + re.escape(nome) + r'(?!\w)', re.IGNORECASE)


def scrub(testo: str, nomi: list) -> str:
    """
    Anonimizza una stringa:
    1. Sostituisce ogni nome in `nomi` (parola intera, case-insensitive) con [NOME]
    2. Sostituisce email, IBAN (anche con spazi), codici fiscali, numeri carta
       e telefoni con segnaposto
    Il testo originale non viene alterato oltre alle sostituzioni.
    """
    if not testo:
        return testo
    risultato = str(testo)
    # nomi più lunghi per primi, così "Mario Rossi" prevale su "Mario"
    for nome in sorted({(n or '').strip() for n in nomi}, key=len, reverse=True):
        if nome:
            risultato = _nome_pattern(nome).sub('[NOME]', risultato)
    risultato = _EMAIL_RE.sub('[EMAIL]', risultato)
    risultato = _IBAN_RE.sub('[IBAN]', risultato)
    risultato = _CF_RE.sub('[CF]', risultato)
    risultato = _CARD_RE.sub('[CARTA]', risultato)
    risultato = _PHONE_RE.sub('[TEL]', risultato)
    return risultato


def scrub_batch(descrizioni: list, nomi: list) -> list:
    """Applica scrub() a una lista di descrizioni."""
    return [scrub(d, nomi) for d in descrizioni]


def find_giroconti(transazioni: list, finestra_giorni: int = 3) -> list:
    """
    Amount-based matching per identificare potenziali giroconti interni.

    Per ogni addebito (importo < 0) senza categoria su conto A, cerca un accredito
    (importo > 0) senza categoria su conto B diverso, con stesso importo assoluto
    entro `finestra_giorni` giorni.

    Restituisce lista di candidati:
    [{hash_addebito, hash_accredito, importo, data_addebito, data_accredito,
      conto_da, conto_a, desc_addebito, desc_accredito, delta_giorni}]
    """
    addebiti  = [t for t in transazioni
                 if float(t.get('importo', 0)) < 0 and not t.get('categoria')]
    accrediti = [t for t in transazioni
                 if float(t.get('importo', 0)) > 0 and not t.get('categoria')]

    matches = []
    used = set()

    for ad in addebiti:
        importo_abs = round(abs(float(ad['importo'])), 2)
        try:
            data_ad = _date.fromisoformat(str(ad['data']))
        except (ValueError, TypeError):
            continue
        conto_ad = ad.get('conto') or ''

        for ac in accrediti:
            if ac['hash_tx'] in used:
                continue
            if round(abs(float(ac.get('importo', 0))), 2) != importo_abs:
                continue
            conto_ac = ac.get('conto') or ''
            if conto_ad and conto_ac and conto_ad == conto_ac:
                continue
            try:
                data_ac = _date.fromisoformat(str(ac['data']))
            except (ValueError, TypeError):
                continue
            delta = abs((data_ac - data_ad).days)
            if delta > finestra_giorni:
                continue

            used.add(ac['hash_tx'])
            matches.append({
                'hash_addebito':  ad['hash_tx'],
                'hash_accredito': ac['hash_tx'],
                'importo':        importo_abs,
                'data_addebito':  str(ad['data']),
                'data_accredito': str(ac['data']),
                'conto_da':       conto_ad,
                'conto_a':        conto_ac,
                'desc_addebito':  ad.get('descrizione') or '',
                'desc_accredito': ac.get('descrizione') or '',
                'delta_giorni':   delta,
            })
            break  # primo match per ciascun addebito

    return matches
