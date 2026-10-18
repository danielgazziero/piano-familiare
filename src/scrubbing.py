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
_NUM_RE   = re.compile(r'(?<!\w)\d{6,}(?!\w)')

# Frasi che introducono una persona (beneficiario/ordinante): le parole che le seguono
# vengono oscurate anche se il nome non è tra quelli configurati.
_TRIGGER_RE = re.compile(
    r'\b(A FAVORE DI|A FAVORE|FAVORE DI|BENEFICIARIO|BENEF\.?|ORDINANTE|ORD\.?|'
    r'DISPOSTO DA|INTESTATO A|DA PARTE DI|MITTENTE|DESTINATARIO|ORDINATO DA|'
    r'A CARICO DI|COGNOME E NOME|NOMINATIVO)\b[:.\- ]*'
    r'((?:[^\W\d_]{2,}(?:[ ]+|$)){1,4})',
    re.IGNORECASE)
# parole che chiudono il nome (non fanno parte della persona)
_STOP = {'CAUSALE', 'IBAN', 'RIF', 'CRO', 'TRN', 'PER', 'ADDEBITO', 'ACCREDITO',
         'IMPORTO', 'DATA', 'CARTA', 'CONTO', 'NUM', 'ESEGUITO', 'SEPA'}
# operazioni tra privati: in modalità rigida si invia solo il tipo di operazione
_TRASFERIMENTO_RE = re.compile(
    r'^\s*(BONIFICO|BON\.|GIROCONTO|GIRO |SCT|SEPA CREDIT|ACCREDITO BONIFICO|'
    r'DISPOSIZIONE DI BONIFICO|BONIF)', re.IGNORECASE)
_PROPRIO_RE = re.compile(r'(?<!^)(?<![.!?] )\b[A-Z][a-zàèéìòù]{2,}\b')


def _nome_pattern(nome: str):
    """Match case-insensitive del nome come parola intera (non dentro altre parole)."""
    return re.compile(r'(?<!\w)' + re.escape(nome) + r'(?!\w)', re.IGNORECASE)


def _oscura_trigger(testo: str) -> str:
    def _sost(m):
        parole = re.findall(r'[^\W\d_]{2,}', m.group(2))
        tenute = []
        for w in parole:
            if w.upper() in _STOP:
                break
            tenute.append(w)
        if not tenute:
            return m.group(0)
        resto = ' '.join(parole[len(tenute):])
        return f"{m.group(1)} [NOME] {resto} "
    return _TRIGGER_RE.sub(_sost, testo)


def _espandi_nomi(nomi: list) -> list:
    """Nome completo + ogni singola parola (>=3 lettere): 'Mario Rossi' → anche 'Mario', 'Rossi'."""
    out = set()
    for n in nomi or []:
        n = (n or '').strip()
        if not n:
            continue
        out.add(n)
        out.update(t for t in re.split(r"[\s\-']+", n) if len(t) >= 3)
    return sorted(out, key=len, reverse=True)  # più lunghi per primi


def scrub(testo: str, nomi: list, rigido: bool = True) -> str:
    """
    Anonimizza una stringa. Livelli di difesa (anche se il nome non è configurato):
    1. nomi configurati: nome completo e ogni sua parola, come parola intera
    2. persone introdotte da 'A FAVORE DI', 'ORDINANTE', 'BENEFICIARIO'...
    3. email, IBAN (anche con spazi), codice fiscale, carta, telefono
    4. numeri lunghi (>=6 cifre: riferimenti, conti, pratiche)
    5. modalità rigida: per bonifici/giroconti il testo libero è omesso
       (restano solo tipo operazione e le parole dopo CAUSALE, già ripulite)
    """
    if not testo:
        return testo
    risultato = str(testo)
    for nome in _espandi_nomi(nomi):
        risultato = _nome_pattern(nome).sub('[NOME]', risultato)
    risultato = _EMAIL_RE.sub('[EMAIL]', risultato)
    risultato = _IBAN_RE.sub('[IBAN]', risultato)
    risultato = _CF_RE.sub('[CF]', risultato)
    risultato = _CARD_RE.sub('[CARTA]', risultato)
    risultato = _PHONE_RE.sub('[TEL]', risultato)
    risultato = _oscura_trigger(risultato)
    risultato = _NUM_RE.sub('[NUM]', risultato)
    m = _TRASFERIMENTO_RE.match(risultato) if rigido else None
    if m:
        causale = re.search(r'CAUSALE[:\s]+(.{0,40})', risultato, re.IGNORECASE)
        risultato = f"{m.group(1).strip().upper()} [dettagli omessi]"
        if causale:
            risultato += f" CAUSALE {causale.group(1).strip()}"
    return re.sub(r'\s+', ' ', risultato).strip()


def scrub_batch(descrizioni: list, nomi: list, rigido: bool = True) -> list:
    """Applica scrub() a una lista di descrizioni."""
    return [scrub(d, nomi, rigido) for d in descrizioni]


def sospetta_pii(testo_inviato: str) -> bool:
    """
    Euristica per l'anteprima: True se nel testo già ripulito restano parole che sembrano
    nomi propri (Maiuscola + minuscole). Segnala solo righe da controllare a mano.
    """
    t = re.sub(r'\[[A-Za-z ]+\]', '', testo_inviato or '')
    return bool(_PROPRIO_RE.search(t))


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
