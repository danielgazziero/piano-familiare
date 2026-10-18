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
        testo = msg.content[0].text.strip()
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
