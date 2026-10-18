"""
Onboarding wizard — configurazione guidata al primo avvio.

Per aggiungere un nuovo step quando si sviluppa una nuova funzionalità:
  1. Definire _step_<id>(config, params_db) -> dict | None
       • Ritorna dict  → i parametri vengono salvati in config_params via salva_params_batch
       • Ritorna {}    → step informativo/custom (salvataggio già gestito internamente)
       • Ritorna None  → step saltato, nessun salvataggio
  2. Appendere un entry a WIZARD_STEPS (fondo lista, prima di 'riepilogo'):
       {"id": "<id>", "titolo": "...", "gruppo": "...", "skippable": True/False}
  3. Se i dati vanno su tabelle diverse da config_params, gestire la persistenza
     dentro la funzione _step e tornare {}.
"""

import streamlit as st
from datetime import date, datetime
import json
from typing import Any


# ─── STEP CATALOGUE ──────────────────────────────────────────
# Ordine visibile nel wizard. 'render' viene collegato in _attach_renders().
# Per aggiungere: appendere PRIMA di 'riepilogo', poi definire _step_<id>.

WIZARD_STEPS = [
    {"id": "benvenuto",   "titolo": "Benvenuto",              "gruppo": "",                   "skippable": False},
    {"id": "nomi",        "titolo": "Nomi",                   "gruppo": "👨‍👩‍👧 Famiglia",        "skippable": False},
    {"id": "date",        "titolo": "Date chiave",            "gruppo": "👨‍👩‍👧 Famiglia",        "skippable": True},
    {"id": "entrate",     "titolo": "Entrate mensili",        "gruppo": "💰 Flussi di cassa", "skippable": True},
    {"id": "spese",       "titolo": "Spese mensili",          "gruppo": "💰 Flussi di cassa", "skippable": True},
    {"id": "liquidita",   "titolo": "Liquidità & conti",      "gruppo": "🏦 Patrimonio",      "skippable": True},
    {"id": "debiti",      "titolo": "Debiti",                 "gruppo": "🏦 Patrimonio",      "skippable": True},
    {"id": "generali",    "titolo": "Gestione separata",      "gruppo": "🏦 Patrimonio",      "skippable": True},
    {"id": "etf",         "titolo": "ETF & PAC",              "gruppo": "📈 Investimenti",    "skippable": True},
    {"id": "fondi",       "titolo": "Fondi bancari",          "gruppo": "📈 Investimenti",    "skippable": True},
    {"id": "azioni",      "titolo": "Azioni ACN",             "gruppo": "📈 Investimenti",    "skippable": True},
    {"id": "parser",      "titolo": "Parser banca",           "gruppo": "⚙️ Config tecnica",  "skippable": True},
    {"id": "smtp",        "titolo": "Email & notifiche",      "gruppo": "⚙️ Config tecnica",  "skippable": True},
    {"id": "fire",        "titolo": "Obiettivi FIRE",         "gruppo": "🎯 Obiettivi",       "skippable": True},
    {"id": "rebalancing", "titolo": "Target rebalancing ETF", "gruppo": "🎯 Obiettivi",       "skippable": True},
    {"id": "riepilogo",   "titolo": "Riepilogo",              "gruppo": "",                   "skippable": False},
]


# ─── HELPER ──────────────────────────────────────────────────

def _p(params_db: dict, key: str, default: Any = 0) -> Any:
    """Legge param dal DB (stringa) e lo converte nel tipo del default."""
    v = params_db.get(key)
    if v is None:
        return default
    if isinstance(default, float):
        try:
            return float(v)
        except (ValueError, TypeError):
            return default
    if isinstance(default, int):
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return default
    if isinstance(default, date):
        try:
            return date.fromisoformat(str(v))
        except (ValueError, TypeError):
            return default
    return str(v) if v else default


def _parse_date_or_none(s: str) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


# ─── STEP RENDERERS ──────────────────────────────────────────

def _step_benvenuto(config: dict, params_db: dict) -> dict:
    st.markdown("""
## Benvenuto in Piano Finanziario Familiare 👋

Questa procedura guidata ti aiuta a configurare l'app in pochi minuti.

**Cosa faremo:**
- Inserire i dati di famiglia (nomi, date importanti)
- Impostare entrate, spese e liquidità attuali
- Configurare gli investimenti (ETF, fondi, azioni)
- Definire gli obiettivi FIRE e i target di ribilanciamento

**Come funziona:**
- Puoi **saltare** qualsiasi step e tornare a compilarlo in seguito
- I dati vengono **salvati automaticamente su Supabase** dopo ogni step
- Puoi **rieseguire il wizard** in qualsiasi momento dalla sidebar (solo admin)
- I valori già salvati vengono pre-compilati automaticamente

---
> **Nota:** Tutti i valori numerici reali vivono su Supabase e non entrano mai nel codice o nel repo.
""")
    return {}


def _step_nomi(config: dict, params_db: dict) -> dict | None:
    st.markdown("Come vuoi chiamare le persone nell'app? Usa nomi di fantasia o abbreviazioni — non vengono mai mostrati in log pubblici.")
    n1 = st.text_input("Persona 1 (es. Dan, Mamma, Luca…)",
                        value=_p(params_db, 'nome_persona1', 'Persona 1'),
                        max_chars=100, key="wiz_n1")
    n2 = st.text_input("Persona 2 (es. Ale, Papà, Sara…)",
                        value=_p(params_db, 'nome_persona2', 'Persona 2'),
                        max_chars=100, key="wiz_n2")
    nf = st.text_input("Figlio/a (es. Leo, Bimbo/a, Sofia…)",
                        value=_p(params_db, 'nome_figlio', 'Figlio/a'),
                        max_chars=100, key="wiz_nf")
    st.caption("Questi nomi appaiono nell'interfaccia e nei pattern di riconoscimento dei file XLS bancari.")
    if not n1.strip() or not n2.strip():
        st.warning("Inserisci almeno i nomi di Persona 1 e Persona 2.")
        return None
    return {'nome_persona1': n1.strip(), 'nome_persona2': n2.strip(), 'nome_figlio': nf.strip()}


def _step_date(config: dict, params_db: dict) -> dict | None:
    st.markdown("Date importanti per i calcoli di maternità, asilo e scadenza polizza Generali.")
    c1, c2 = st.columns(2)

    def _di(label, key, default_str, col):
        d = _parse_date_or_none(_p(params_db, key, default_str or ''))
        return col.date_input(label, value=d, key=f"wiz_{key}")

    nascita   = _di("Nascita figlio/a",     "nascita_figlio",    config.get('date',{}).get('nascita_figlio',''),     c1)
    ini_mat   = _di("Inizio maternità",      "inizio_maternita",  config.get('date',{}).get('inizio_maternita',''),   c1)
    fine_mat  = _di("Fine maternità",        "fine_maternita",    config.get('date',{}).get('fine_maternita',''),     c2)
    ini_nido  = _di("Inizio asilo nido",     "inizio_asilo_nido", config.get('date',{}).get('inizio_asilo_nido',''), c2)
    scad_gen  = _di("Scadenza polizza Generali", "scadenza_generali", config.get('date',{}).get('scadenza_generali',''), c1)

    res = {}
    for key, val in [
        ('nascita_figlio', nascita), ('inizio_maternita', ini_mat),
        ('fine_maternita', fine_mat), ('inizio_asilo_nido', ini_nido),
        ('scadenza_generali', scad_gen),
    ]:
        if val:
            res[key] = val.isoformat()
    return res


def _step_entrate(config: dict, params_db: dict) -> dict | None:
    st.markdown("Entrate mensili nette (dopo tasse). Usare 0 per voci non applicabili.")
    c_cfg = config.get('entrate', {})
    c1, c2 = st.columns(2)
    with c1:
        st1 = st.number_input("Stipendio Persona 1 (€/mese netti)",
                               value=_p(params_db, 'stipendio_persona1', c_cfg.get('stipendio_persona1', 0)),
                               step=100, min_value=0, key="wiz_st1")
        st2o = st.number_input("Stipendio Persona 2 ordinario (€/mese netti)",
                                value=_p(params_db, 'stipendio_persona2_ordinario', c_cfg.get('stipendio_persona2_ordinario', 0)),
                                step=100, min_value=0, key="wiz_st2o")
        st2m = st.number_input("Stipendio Persona 2 in maternità (€/mese netti)",
                                value=_p(params_db, 'stipendio_persona2_maternita', c_cfg.get('stipendio_persona2_maternita', 0)),
                                step=100, min_value=0, key="wiz_st2m")
    with c2:
        aff = st.number_input("Affitto esterno ricevuto (€/mese)",
                               value=_p(params_db, 'affitto_esterno', c_cfg.get('affitto_esterno', 0)),
                               step=100, min_value=0, key="wiz_aff")
        mut = st.number_input("Rata mutuo mensile (€ — uscita)",
                               value=_p(params_db, 'mutuo_mensile', c_cfg.get('mutuo_mensile', 0)),
                               step=50, min_value=0, key="wiz_mut")
    st.caption("Lo stipendio in maternità viene usato dal simulatore per il periodo di astensione.")
    return {
        'stipendio_persona1':           int(st1),
        'stipendio_persona2_ordinario': int(st2o),
        'stipendio_persona2_maternita': int(st2m),
        'affitto_esterno':              int(aff),
        'mutuo_mensile':                int(mut),
    }


def _step_spese(config: dict, params_db: dict) -> dict | None:
    st.markdown("Spese mensili correnti. Il budget viene confrontato con le transazioni bancarie in 'Stato di famiglia'.")
    a_cfg = config.get('allocazione', {})
    c1, c2 = st.columns(2)
    with c1:
        sc_ora  = st.number_input("Spese correnti ora (€/mese)",
                                   value=_p(params_db, 'spese_correnti_ora', a_cfg.get('spese_correnti_ora', 0)),
                                   step=100, min_value=0, key="wiz_sc_ora")
        sc_nido = st.number_input("Spese correnti con asilo nido (€/mese)",
                                   value=_p(params_db, 'spese_correnti_con_nido', a_cfg.get('spese_correnti_con_nido', 0)),
                                   step=100, min_value=0, key="wiz_sc_nido")
    with c2:
        vac = st.number_input("Buffer vacanze/imprevisti (€/anno)",
                               value=_p(params_db, 'vacanze_buffer', a_cfg.get('vacanze_buffer', 0)),
                               step=100, min_value=0, key="wiz_vac")
    st.caption("Le spese 'con asilo nido' vengono attivate automaticamente dal simulatore alla data di inizio nido.")
    return {
        'spese_correnti_ora':       int(sc_ora),
        'spese_correnti_con_nido':  int(sc_nido),
        'vacanze_buffer':           int(vac),
    }


def _step_liquidita(config: dict, params_db: dict) -> dict | None:
    st.markdown("Liquidità attuale nei conti correnti. Viene inclusa nel calcolo del patrimonio totale.")
    p_cfg = config.get('patrimonio', {})
    c1, c2, c3 = st.columns(3)
    with c1:
        l1 = st.number_input("Liquidità Persona 1 (€)",
                              value=_p(params_db, 'liquidita_persona1', p_cfg.get('liquidita_persona1', 0)),
                              step=100, min_value=0, key="wiz_l1")
        l2 = st.number_input("Liquidità Persona 2 (€)",
                              value=_p(params_db, 'liquidita_persona2', p_cfg.get('liquidita_persona2', 0)),
                              step=100, min_value=0, key="wiz_l2")
    with c2:
        cc = st.number_input("Conto comune (€)",
                              value=_p(params_db, 'conto_comune', p_cfg.get('conto_comune', 0)),
                              step=100, min_value=0, key="wiz_cc")
        aff_acc = st.number_input("Affitto accantonato (€)",
                                   value=_p(params_db, 'affitto_accantonato', p_cfg.get('affitto_accantonato', 0)),
                                   step=100, min_value=0, key="wiz_aff_acc")
    with c3:
        st.info("💡 Aggiorna questi valori ogni mese da **Stato di famiglia → Aggiorna valori manuali**.")
    return {
        'liquidita_persona1':  int(l1),
        'liquidita_persona2':  int(l2),
        'conto_comune':        int(cc),
        'affitto_accantonato': int(aff_acc),
    }


def _step_debiti(config: dict, params_db: dict) -> dict | None:
    st.markdown("Debiti in essere. Vengono sottratti dal patrimonio netto nel calcolo del net worth.")
    debito_raw = params_db.get('debito_json', '')
    try:
        debito = json.loads(debito_raw) if debito_raw else {}
    except (json.JSONDecodeError, TypeError):
        debito = {}

    imp = st.number_input("Importo totale debito (€)",
                           value=float(debito.get('importo_totale', 0)),
                           step=500.0, min_value=0.0, key="wiz_deb_imp")
    desc = st.text_input("Descrizione (es. Prestito familiare)",
                          value=debito.get('descrizione', ''),
                          max_chars=200, key="wiz_deb_desc")
    note = st.text_area("Note aggiuntive / piano rimborso",
                         value=debito.get('note', ''),
                         height=80, key="wiz_deb_note")
    st.caption("La struttura di rimborso dettagliata (tranche + date) si gestisce in `config.yaml → debiti[]`.")
    return {
        'debito_json': json.dumps({
            'importo_totale': float(imp),
            'descrizione':    desc.strip(),
            'note':           note.strip(),
        }),
    }


def _step_generali(config: dict, params_db: dict) -> dict | None:
    st.markdown("Gestione separata Generali — polizza vita/risparmio. I valori vengono inclusi nel patrimonio totale.")
    p_cfg = config.get('patrimonio', {})
    c1, c2 = st.columns(2)
    with c1:
        val_gen = st.number_input("Valore attuale Generali (€)",
                                   value=_p(params_db, 'gestione_separata_generali', p_cfg.get('gestione_separata_generali', 0)),
                                   step=500, min_value=0, key="wiz_gen_val")
        vers_men = st.number_input("Versamento mensile Generali (€)",
                                    value=_p(params_db, 'generali_versamento_mensile', p_cfg.get('generali_versamento_mensile', 0)),
                                    step=50, min_value=0, key="wiz_gen_mens")
    with c2:
        scad_raw = params_db.get('scadenza_generali', config.get('date', {}).get('scadenza_generali', ''))
        scad_d = _parse_date_or_none(scad_raw)
        scad = st.date_input("Data di scadenza polizza", value=scad_d, key="wiz_gen_scad")
    return {
        'gestione_separata_generali':  int(val_gen),
        'generali_versamento_mensile': int(vers_men),
        'scadenza_generali':           scad.isoformat() if scad else '',
    }


def _step_etf(config: dict, params_db: dict) -> dict | None:
    st.markdown("Configura gli importi PAC (Piano di Accumulo del Capitale) mensili per gli ETF attivi.")
    a_cfg = config.get('allocazione', {})

    st.subheader("Importi PAC mensili")
    c1, c2 = st.columns(2)
    with c1:
        pac1 = st.number_input("PAC Persona 1 — importo attuale (€/mese)",
                                value=_p(params_db, 'pac_persona1_ora', a_cfg.get('pac_persona1_ora', 0)),
                                step=50, min_value=0, key="wiz_pac1")
        pac1_nido = st.number_input("PAC Persona 1 — dopo asilo nido (€/mese)",
                                     value=_p(params_db, 'pac_persona1_con_nido', a_cfg.get('pac_persona1_con_nido', 0)),
                                     step=50, min_value=0, key="wiz_pac1n")
    with c2:
        pac_f = st.number_input("PAC Figlio/a (€/mese)",
                                 value=_p(params_db, 'pac_figlio', a_cfg.get('pac_figlio', 0)),
                                 step=50, min_value=0, key="wiz_pacf")

    st.divider()
    from wizard_assets import render_asset_step
    render_asset_step('etf')

    return {
        'pac_persona1_ora':       int(pac1),
        'pac_persona1_con_nido':  int(pac1_nido),
        'pac_figlio':             int(pac_f),
    }


def _step_fondi(config: dict, params_db: dict) -> dict:
    """Asset salvati direttamente su Supabase (asset_catalog, posizioni, quote_fondi)."""
    st.markdown("Aggiungi i fondi bancari che possiedi (a mano o da file). "
                "Le quote si aggiornano poi ogni mese da **🏦 Fondi bancari → Aggiorna quote**.")
    from wizard_assets import render_asset_step
    render_asset_step('fondo')
    return {}


def _step_azioni(config: dict, params_db: dict) -> dict | None:
    st.markdown("Azioni (es. RSU / stock plan) — valorizzate tramite yfinance, in valuta del titolo. "
                "Per acquisti/vendite future usa **📉 Portafoglio storico**.")
    from wizard_assets import render_asset_step
    render_asset_step('azione')
    return {}


def _step_parser(config: dict, params_db: dict) -> dict | None:
    st.markdown("Il parser XLS bancario usa il nome delle persone per riconoscere i file e filtrare i bonifici interni.")

    n1 = _p(params_db, 'nome_persona1', 'Persona 1')
    n2 = _p(params_db, 'nome_persona2', 'Persona 2')

    banche = config.get('banche', [])
    if banche:
        st.subheader("Pattern file attesi")
        for b in banche:
            pattern = b.get('pattern_template', '').replace('{nome}', f'<nome_{b.get("intestatario","?")}>')
            st.caption(f"**{b['nome']}** — `{pattern}`")
        st.caption("Il nome viene sostituito automaticamente al posto di `{nome}` nel pattern.")

    from wizard_assets import render_transactions_upload
    render_transactions_upload(config)
    st.divider()

    st.subheader("Keyword bonifici interni")
    kw_raw = params_db.get('transfer_keywords', '')
    try:
        kw_list = json.loads(kw_raw) if kw_raw else []
    except (json.JSONDecodeError, TypeError):
        kw_list = []
    kw_text = st.text_area(
        "Una keyword per riga — transazioni che le contengono vengono escluse dalle statistiche",
        value='\n'.join(kw_list) if kw_list else f"{n1}\n{n2}",
        height=120,
        key="wiz_kw",
        help="Esempi: nomi delle persone, 'Giroconto', 'Ricarica conto'",
    )
    kw_parsed = [k.strip() for k in kw_text.splitlines() if k.strip()]

    return {'transfer_keywords': json.dumps(kw_parsed)}


def _step_smtp(config: dict, params_db: dict) -> dict:
    st.markdown("Configura l'invio email per reset password e inviti utente (opzionale).")
    st.info(
        "Le credenziali SMTP vanno in `.streamlit/secrets.toml` (gitignored) — "
        "**non** in Supabase, per evitare che finiscano nel DB."
    )
    st.code("""# .streamlit/secrets.toml
SMTP_HOST     = "smtp.gmail.com"   # oppure smtp.office365.com, smtp.sendgrid.net
SMTP_PORT     = 587
SMTP_USER     = "tua@email.com"
SMTP_PASSWORD = "..."              # Gmail: usa App Password, non la password account
APP_BASE_URL  = "https://tua-app.streamlit.app"
""", language="toml")

    st.subheader("Verifica configurazione")
    try:
        from auth_mail import smtp_configurato as _sc
        if _sc():
            st.success("✅ SMTP configurato correttamente.")
        else:
            st.warning("⚠️ SMTP non configurato — le email di reset/invito non verranno inviate.")
    except Exception:
        st.warning("Impossibile verificare la configurazione SMTP.")
    st.caption("Puoi saltare questo step e configurare SMTP in seguito senza ripetere il wizard.")
    return {}


def _step_fire(config: dict, params_db: dict) -> dict | None:
    st.markdown(
        "Definisci il tuo obiettivo FIRE (Financial Independence, Retire Early). "
        "Questi parametri alimenteranno la feature **FIRE Progress tracker** (in sviluppo)."
    )
    c1, c2 = st.columns(2)
    with c1:
        target = st.number_input(
            "Patrimonio target FIRE (€)",
            value=_p(params_db, 'fire_target_eur', 0),
            step=10_000, min_value=0, key="wiz_fire_target",
            help="Patrimonio investito necessario per coprire le spese con la regola del 4%.",
        )
        eta = st.number_input(
            "Età target FIRE (anni)",
            value=_p(params_db, 'fire_eta_target', 50),
            step=1, min_value=30, max_value=80, key="wiz_fire_eta",
        )
    with c2:
        coast_target = st.number_input(
            "Patrimonio target Coast FIRE (€)",
            value=_p(params_db, 'coast_fire_target_eur', 0),
            step=5_000, min_value=0, key="wiz_coast_target",
            help="Patrimonio a cui il compound interest da solo ti porta al FIRE target senza ulteriori apporti.",
        )
        st.caption("**Regola del 4%:** target ≈ spese annuali × 25")
        spese_ann = _p(params_db, 'spese_correnti_ora', config.get('allocazione', {}).get('spese_correnti_ora', 0))
        if spese_ann:
            st.caption(f"Con spese attuali di €{int(spese_ann):,}/mese → target stimato **€{int(spese_ann)*12*25:,}**")
    return {
        'fire_target_eur':       int(target),
        'fire_eta_target':       int(eta),
        'coast_fire_target_eur': int(coast_target),
    }


def _step_rebalancing(config: dict, params_db: dict) -> dict | None:
    st.markdown(
        "Definisci i pesi target per il ribilanciamento del portafoglio ETF. "
        "La somma deve essere 100%. Alimenterà la feature **Rebalancing alert** (in sviluppo)."
    )
    from database import carica_asset_catalog
    _cat = carica_asset_catalog()
    etf_attivi = ([e for e in _cat[_cat['tipo'] == 'etf'].to_dict('records')
                   if e.get('stato') in ('attivo', 'da_avviare')]
                  if not _cat.empty and 'tipo' in _cat.columns else [])
    if not etf_attivi:
        st.info("Nessun ETF attivo nel catalogo: aggiungili nello step **ETF & PAC**.")

    rb_raw = params_db.get('rebalancing_targets', '')
    try:
        rb = json.loads(rb_raw) if rb_raw else {}
    except (json.JSONDecodeError, TypeError):
        rb = {}

    targets = {}
    totale  = 0
    for e in etf_attivi:
        ticker = e.get('ticker_bi') or e.get('isin', '?')
        nome   = e.get('nome', ticker)
        default_pct = rb.get(ticker, 0)
        val = st.number_input(
            f"{ticker} — {nome} (%)",
            value=int(default_pct),
            step=5, min_value=0, max_value=100,
            key=f"wiz_rb_{ticker}",
        )
        targets[ticker] = val
        totale += val

    if etf_attivi:
        color = "🟢" if totale == 100 else ("🟡" if totale <= 100 else "🔴")
        st.metric("Totale allocazione", f"{totale}%", delta=f"{totale-100:+d}%" if totale != 100 else None)
        if totale != 100:
            st.caption(f"{color} La somma deve essere 100%. Attuale: {totale}%.")
        else:
            st.caption("🟢 Allocazione bilanciata.")

    st.caption("Puoi modificare i target in qualsiasi momento senza ripetere il wizard (funzione in sviluppo).")
    return {'rebalancing_targets': json.dumps(targets)}


def _step_riepilogo(config: dict, params_db: dict) -> dict:
    st.markdown("### 🎉 Configurazione completata!")
    st.success(
        "Tutti i parametri fondamentali sono stati salvati su Supabase. "
        "L'app è pronta all'uso."
    )
    step_ids = [s["id"] for s in WIZARD_STEPS if s["id"] != "riepilogo"]
    salvati   = [sid for sid in step_ids if params_db.get(f'_wiz_done_{sid}')]
    saltati   = [sid for sid in step_ids if not params_db.get(f'_wiz_done_{sid}') and sid != 'benvenuto']

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Step completati:**")
        for sid in salvati:
            label = next((s['titolo'] for s in WIZARD_STEPS if s['id'] == sid), sid)
            st.markdown(f"✅ {label}")
    with col2:
        if saltati:
            st.markdown("**Step saltati (compilabili in seguito):**")
            for sid in saltati:
                label = next((s['titolo'] for s in WIZARD_STEPS if s['id'] == sid), sid)
                st.markdown(f"⬜ {label}")

    st.divider()
    st.markdown("**Prossimi passi consigliati:**")
    st.markdown("""
- 📥 Carica altri estratti conto dal passo **Parser banca** del wizard (o da `data/input/` in locale)
- ⚙️ Vai in **Gestione Asset** per aggiungere ETF, fondi e azioni al catalogo
- 📉 Vai in **Portafoglio storico** per registrare le quantità RSU Accenture
- 🏦 Vai in **Fondi bancari** per aggiornare le quote mensilmente
- 📋 Usa **Fine mese** per la checklist di validazione mensile
""")
    return {}


# ─── STEP RENDER MAP ─────────────────────────────────────────

_RENDER_MAP = {
    "benvenuto":   _step_benvenuto,
    "nomi":        _step_nomi,
    "date":        _step_date,
    "entrate":     _step_entrate,
    "spese":       _step_spese,
    "liquidita":   _step_liquidita,
    "debiti":      _step_debiti,
    "generali":    _step_generali,
    "etf":         _step_etf,
    "fondi":       _step_fondi,
    "azioni":      _step_azioni,
    "parser":      _step_parser,
    "smtp":        _step_smtp,
    "fire":        _step_fire,
    "rebalancing": _step_rebalancing,
    "riepilogo":   _step_riepilogo,
}


# ─── MAIN ENTRY POINT ────────────────────────────────────────

def run_wizard(config: dict, db_ok: bool) -> bool:
    """
    Mostra il wizard di onboarding se non è ancora stato completato.
    Ritorna True  → wizard completato, l'app può procedere normalmente.
    Ritorna False → wizard attivo, l'app deve fermarsi (st.stop() nel chiamante).
    """
    if not db_ok:
        st.warning(
            "⚠️ Il wizard richiede una connessione Supabase attiva. "
            "Verifica le credenziali in `.streamlit/secrets.toml` e premi **🔄 Aggiorna tutto**."
        )
        st.stop()

    from database import carica_tutti_params, salva_params_batch

    # P-NEW-W1: cache params_db in session_state — 1 query per sessione, non 1 per ogni rerun
    if '_wiz_params_cache' not in st.session_state:
        st.session_state['_wiz_params_cache'] = carica_tutti_params()
    params_db = st.session_state['_wiz_params_cache']

    # Già completato (e non richiesto il re-run)
    if params_db.get('wizard_completed') == 'true' and not st.session_state.get('_wizard_restart'):
        return True

    _render_wizard(config, params_db, db_ok)
    return False


def _render_wizard(config: dict, params_db: dict, db_ok: bool):
    from database import salva_params_batch

    n_steps = len(WIZARD_STEPS)

    # Step corrente — da session_state (veloce) oppure da Supabase (ripresa)
    if '_wizard_step' not in st.session_state:
        try:
            last = int(params_db.get('wizard_step_last', 0))
        except (ValueError, TypeError):
            last = 0
        st.session_state['_wizard_step'] = last

    idx = st.session_state['_wizard_step']
    idx = max(0, min(idx, n_steps - 1))

    step = WIZARD_STEPS[idx]
    render_fn = _RENDER_MAP.get(step['id'])

    # ── Header ───────────────────────────────────────────────
    st.markdown(
        "<h2 style='text-align:center;margin-bottom:0'>🧭 Configurazione iniziale</h2>",
        unsafe_allow_html=True,
    )

    # Barra di progresso
    pct = idx / (n_steps - 1) if n_steps > 1 else 1.0
    st.progress(pct, text=f"Step {idx + 1} di {n_steps} — **{step['titolo']}**")

    # Gruppo (intestazione visiva)
    if step['gruppo']:
        st.caption(f"Sezione: {step['gruppo']}")

    st.divider()

    # ── Contenuto step ───────────────────────────────────────
    with st.container():
        result = render_fn(config, params_db) if render_fn else {}

    st.divider()

    # ── Navigazione ──────────────────────────────────────────
    is_last  = (idx == n_steps - 1)
    is_first = (idx == 0)

    nav_cols = st.columns([1, 2, 1])

    # Indietro
    with nav_cols[0]:
        if not is_first:
            if st.button("← Indietro", use_container_width=True):
                st.session_state['_wizard_step'] = idx - 1
                st.rerun()

    # Salta
    with nav_cols[1]:
        if step['skippable']:
            if st.button("Salta per ora", use_container_width=True, type="secondary"):
                st.session_state['_wizard_step'] = idx + 1
                salva_params_batch({'wizard_step_last': str(idx + 1)})
                st.rerun()

    # Avanti / Completa
    with nav_cols[2]:
        if is_last:
            if st.button("✅ Chiudi wizard", type="primary", use_container_width=True):
                salva_params_batch({
                    'wizard_completed':  'true',
                    'wizard_step_last':  str(n_steps - 1),
                })
                st.session_state.pop('_wizard_step', None)
                st.session_state.pop('_wizard_restart', None)
                st.session_state.pop('_wiz_params_cache', None)
                st.balloons()
                st.rerun()
        else:
            btn_label = "Avanti →" if idx > 0 else "Inizia →"
            if st.button(btn_label, type="primary", use_container_width=True):
                # Salva il risultato dello step corrente
                if result is not None and isinstance(result, dict) and result:
                    try:
                        batch = dict(result)
                        batch['wizard_step_last'] = str(idx + 1)
                        batch[f'_wiz_done_{step["id"]}'] = 'true'
                        salva_params_batch(batch)
                        params_db.update(batch)
                    except Exception as ex:
                        st.error(f"Errore salvataggio: {type(ex).__name__}")
                        st.stop()
                elif result == {}:
                    # Step custom (salvataggio già gestito internamente) o informativo
                    salva_params_batch({
                        'wizard_step_last':        str(idx + 1),
                        f'_wiz_done_{step["id"]}': 'true',
                    })
                st.session_state['_wizard_step'] = idx + 1
                st.rerun()

    st.caption(
        "Puoi chiudere questa finestra e riprendere in seguito — il progresso è salvato. "
        "L'admin può rieseguire il wizard dalla sidebar."
    )
