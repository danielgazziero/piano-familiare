"""
Categorizzazione AI delle transazioni bancarie via Claude API (Haiku).
GUARDRAIL: le descrizioni passate qui devono essere già anonimizzate da scrubbing.scrub_batch().
"""

import json
import os

_MODEL      = 'claude-haiku-5-5'
_BATCH_SIZE = 50


def _get_api_key() -> str:
    try:
        import streamlit as st
        return st.secrets.get('ANTHROPIC_API_KEY', '') or ''
    except Exception:
        return os.environ.get('ANTHROPIC_API_KEY', '') or ''


def categorizza_batch(tx_scrubbed: list, taxonomy: dict) -> list:
    """
    Chiama Claude API per suggerire categoria e sottocategoria per ogni transazione.

    Args:
        tx_scrubbed: lista di dict {hash_tx, descrizione_anonimizzata, importo}
        taxonomy: dict {categorie: [...], tag: [...]} da carica_taxonomy()

    Returns:
        lista di dict {hash_tx, categoria, sottocategoria}
        (solo transazioni per cui il modello ha dato una risposta valida)
    """
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY non trovata. "
            "Aggiungila in .streamlit/secrets.toml: ANTHROPIC_API_KEY = \"sk-ant-...\""
        )

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    cat_attive = {
        c['id']: {
            'nome': c['nome'],
            'subs': [s['id'] for s in c.get('sottocategorie', [])
                     if s.get('attiva', True)],
        }
        for c in taxonomy.get('categorie', [])
        if c.get('attiva', True)
    }

    risultati = []
    for i in range(0, len(tx_scrubbed), _BATCH_SIZE):
        batch = tx_scrubbed[i:i + _BATCH_SIZE]
        risultati.extend(_chiama_api(client, batch, cat_attive))
    return risultati


def _chiama_api(client, batch: list, cat_attive: dict) -> list:
    cat_json = json.dumps(cat_attive, ensure_ascii=False)
    tx_json  = json.dumps(
        [{'hash_tx': t['hash_tx'],
          'desc': t['descrizione_anonimizzata'],
          'eur': float(t['importo'])}
         for t in batch],
        ensure_ascii=False,
    )

    prompt = (
        "Sei un assistente che categorizza transazioni bancarie italiane.\n"
        "Usa gli id esatti dalla TASSONOMIA.\n"
        "sottocategoria: id valido dalla lista subs della categoria scelta, oppure null.\n"
        "Se non riesci a categorizzare usa {\"categoria\":\"altro\",\"sottocategoria\":null}.\n\n"
        f"TASSONOMIA (id->nome,subs):\n{cat_json}\n\n"
        f"TRANSAZIONI (desc già anonimizzate):\n{tx_json}\n\n"
        "Rispondi SOLO con un array JSON, nessun testo aggiuntivo.\n"
        "Formato: [{\"hash_tx\":\"...\",\"categoria\":\"...\",\"sottocategoria\":\"...\"}]"
    )

    try:
        msg = client.messages.create(
            model=_MODEL,
            max_tokens=4096,
            messages=[{'role': 'user', 'content': prompt}],
        )
        testo = next((b.text for b in msg.content if hasattr(b, 'text')), '').strip()
    except Exception as e:
        raise RuntimeError(f"Errore chiamata Claude API: {type(e).__name__}: {e}") from e

    inizio = testo.find('[')
    fine   = testo.rfind(']') + 1
    if inizio == -1 or fine == 0:
        return []

    try:
        parsed = json.loads(testo[inizio:fine])
    except json.JSONDecodeError:
        return []

    return [
        r for r in parsed
        if isinstance(r, dict) and 'hash_tx' in r and 'categoria' in r
    ]


def analizza_spese_ai(semafori: list, kpi: dict, nomi: list) -> str:
    """
    Genera un'analisi testuale (markdown) delle spese familiari via Claude.

    I dati sono già aggregati per categoria — nessun dato individuale né nome
    personale viene inviato all'API. I nomi nella lista `nomi` vengono scrubbed
    dai testi prima dell'invio (misura difensiva aggiuntiva).

    Args:
        semafori: lista dict da calcola_analisi_spese()
        kpi:      dict {totale_mese, totale_media_12m, top_categoria, delta_pct}
        nomi:     lista nomi da anonimizzare (guardrail)

    Returns:
        Testo markdown con executive summary e suggerimenti
    """
    from scrubbing import scrub

    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY non trovata. "
            "Aggiungila in .streamlit/secrets.toml."
        )

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    # Separa categorie critiche (🔴/🟡) dalle virtuose (🟢)
    critiche = [r for r in semafori if r.get('semaforo') in ('🔴', '🟡')]
    virtuose  = [r for r in semafori if r.get('semaforo') == '🟢']

    # Scrub difensivo + limite alle top 4 critiche per importo
    def _pulisci(r: dict) -> dict:
        return {
            'categoria':     scrub(str(r.get('categoria', '')), nomi),
            'mese_eur':      r.get('mese_eur'),
            'media_12m_eur': r.get('media_12m_eur'),
            'benchmark_eur': r.get('benchmark_eur'),
            'trend':         r.get('trend'),
            'semaforo':      r.get('semaforo'),
        }

    critiche_top = sorted(critiche, key=lambda r: r.get('mese_eur', 0), reverse=True)[:4]
    migliore     = sorted(virtuose,  key=lambda r: r.get('mese_eur', 0), reverse=True)[:1]

    dati_json = json.dumps({
        'kpi':     kpi,
        'critiche': [_pulisci(r) for r in critiche_top],
        'virtuose': [_pulisci(r) for r in migliore],
    }, ensure_ascii=False)

    prompt = (
        "Sei un consulente finanziario personale che aiuta famiglie italiane a ottimizzare "
        "le spese. I dati sono aggregati per categoria (nessun dato personale). Importi in €/mese.\n\n"
        f"Dati famiglia:\n{dati_json}\n\n"
        "REGOLE (rispettale alla lettera):\n"
        "1. NON parafrasare i numeri — l'utente li vede già in tabella\n"
        "2. Per ogni categoria critica: ipotizza cosa genera la spesa, proponi "
        "   UN'azione concreta e specifica (non generica), stima il risparmio in €/mese\n"
        "3. Ogni bullet: max 2 righe. Sii diretto e pratico.\n"
        "4. Proponi alternative reali: servizi specifici, abitudini, offerte tariffarie italiane\n\n"
        "Rispondi SOLO con questo markdown (niente altro testo prima o dopo).\n"
        "USA SOLO grassetto (**testo**) per i titoli — NO heading (#, ##, ###).\n\n"
        "**Situazione in breve**\n"
        "_(1 frase con il dato più critico e il risparmio potenziale totale)_\n\n"
        "**Dove agire subito**\n"
        "_(bullet per ogni categoria critica: **Nome (€mese → obiettivo €)**: azione concreta)_\n\n"
        "**Un'azione strutturale**\n"
        "_(1 sola cosa da fare nei prossimi 30 giorni per ridurre le spese in modo duraturo)_\n\n"
        "**Punto di forza**\n"
        "_(1 frase sulla categoria virtuosa: perché è un buon risultato)_"
    )

    try:
        msg = client.messages.create(
            model=_MODEL,
            max_tokens=4096,
            messages=[{'role': 'user', 'content': prompt}],
        )
        testo = next((b.text for b in msg.content if hasattr(b, 'text')), '').strip()
    except Exception as e:
        raise RuntimeError(f"Errore chiamata Claude API: {type(e).__name__}: {e}") from e

    return testo
