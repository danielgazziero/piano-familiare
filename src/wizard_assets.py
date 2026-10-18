"""
Componenti UI del wizard per inserire asset (ETF, fondi, azioni) e importare
estratti conto — manualmente o da file caricati dall'utente.

Tutto finisce su Supabase: asset_catalog, posizioni, quote_fondi, transazioni.
Nessun dato reale vive in config.yaml.
"""

import io
import re
import html
from datetime import date

import pandas as pd
import streamlit as st

ISIN_RE = re.compile(r'^[A-Z]{2}[A-Z0-9]{9}[0-9]$')
_PROPRIETARI = ['persona1', 'persona2', 'figlio']
_STATI_ETF = ['attivo', 'da_avviare', 'candidato']
_TIPI = {'etf': 'etf', 'fondo': 'fondo', 'azione': 'azione'}

MAX_UPLOAD_BYTES = 10 * 1024 * 1024   # 10 MB per file
MAX_CSV_ROWS = 300
CSV_TEMPLATE = (
    "isin,nome,tipo,ticker_yf,quantita,valore_quota,proprietario\n"
    "IE00B5BMR087,Esempio ETF,etf,CSPX.L,10,,persona1\n"
    "IT0000000000,Esempio fondo,fondo,,25.5,12.34,persona1\n"
)


def _txt(v, n=100) -> str:
    return str(v if v is not None and not (isinstance(v, float) and pd.isna(v)) else '').strip()[:n]


def _num(v, default=0.0) -> float:
    try:
        x = float(str(v).replace(',', '.'))
        return x if x == x and x >= 0 else default
    except (TypeError, ValueError):
        return default


def _salva_asset_completo(asset: dict, quantita: float, quota: float = 0.0) -> bool:
    """Scrive catalogo + posizione (+ quota fondo). Ritorna True se tutto ok."""
    from database import upsert_asset, salva_quote_fondi
    from positions import imposta_posizione
    if not upsert_asset(asset):
        return False
    ok = True
    if quantita > 0:
        ok = imposta_posizione(asset['isin'], asset['nome'], asset['tipo'], quantita)
    if ok and asset['tipo'] == 'fondo' and quota > 0:
        ok = salva_quote_fondi([{'isin': asset['isin'], 'nome': asset['nome'],
                                 'quota': quota, 'quantita': quantita,
                                 'valore': round(quantita * quota, 2)}])
    return bool(ok)


def _invalida_cache():
    for k in ('asset_catalog', 'pos_df_cache', 'fondi_df_cache', 'etf_perf_cache',
              'azioni_df_cache', 'quote_map', '_wiz_assets_cache'):
        st.session_state.pop(k, None)


def _elenco_asset(tipo: str):
    """Mostra gli asset del tipo gia' presenti nel catalogo con la quantita' attuale."""
    from database import carica_asset_catalog
    from positions import carica_posizioni
    cat = carica_asset_catalog()
    if cat.empty or 'tipo' not in cat.columns:
        st.info("Nessun asset inserito finora.")
        return
    cat = cat[cat['tipo'] == tipo]
    if cat.empty:
        st.info("Nessun asset di questo tipo inserito finora.")
        return
    pos = carica_posizioni()
    qmap = {}
    if not pos.empty and 'isin' in pos.columns:
        qmap = pos.set_index('isin')['quantita'].to_dict()
    righe = [{'ISIN': r['isin'], 'Nome': r.get('nome', ''),
              'Ticker': r.get('ticker_yf') or '', 'Quantità': qmap.get(r['isin'], 0)}
             for r in cat.to_dict('records')]
    st.dataframe(pd.DataFrame(righe), use_container_width=True, hide_index=True)


def render_asset_step(tipo: str):
    """Elenco asset + form di inserimento + import CSV per un tipo (etf|fondo|azione)."""
    tipo = _TIPI[tipo]
    st.subheader("Asset inseriti")
    _elenco_asset(tipo)

    st.subheader("➕ Aggiungi manualmente")
    with st.form(f"wiz_form_asset_{tipo}", clear_on_submit=True):
        c1, c2 = st.columns(2)
        isin = c1.text_input("ISIN *", max_chars=12,
                             help="2 lettere + 9 alfanumerici + 1 cifra (es. IE00B5BMR087)")
        nome = c2.text_input("Nome *", max_chars=100)
        ticker = c1.text_input("Ticker Yahoo Finance", max_chars=20,
                               help="Es. CSPX.L, IWDA.AS, ACN. Vuoto per i fondi bancari.")
        prop = c2.selectbox("Proprietario", _PROPRIETARI)
        qta = c1.number_input("Quantità / quote possedute", min_value=0.0,
                              step=0.001, format="%.3f")
        ticker_bi, stato, quota, ter = '', 'attivo', 0.0, 0.0
        if tipo == 'etf':
            ticker_bi = c2.text_input("Ticker breve (es. CSPX)", max_chars=20)
            stato = c1.selectbox("Stato", _STATI_ETF)
            ter = c2.number_input("TER %", min_value=0.0, max_value=5.0,
                                  value=0.20, step=0.01, format="%.2f")
        elif tipo == 'fondo':
            quota = c2.number_input("Valore quota attuale (€)", min_value=0.0,
                                    step=0.0001, format="%.4f")
            ter = c1.number_input("TER %", min_value=0.0, max_value=5.0,
                                  value=2.0, step=0.1, format="%.2f")
        submit = st.form_submit_button("Aggiungi")

    if submit:
        isin_c = isin.strip().upper()
        if not nome.strip():
            st.error("Il nome è obbligatorio.")
        elif not ISIN_RE.match(isin_c):
            st.error("ISIN non valido (2 lettere + 9 alfanumerici + 1 cifra).")
        else:
            asset = {
                'isin': isin_c, 'nome': nome.strip(), 'tipo': tipo,
                'ticker_yf': ticker.strip() or None,
                'ticker_bi': ticker_bi.strip() or None,
                'ter': (ter / 100 if tipo == 'fondo' else ter) if tipo != 'azione' else None,
                'proprietario': prop, 'stato': stato,
                'valore_quota_ref': quota if quota > 0 else None,
                'fallback_tickers': [],
            }
            if _salva_asset_completo(asset, qta, quota):
                _invalida_cache()
                st.success(f"✓ {nome.strip()} aggiunto.")
                st.rerun()
            else:
                st.error("Errore durante il salvataggio su Supabase.")

    render_csv_import(tipo)


def render_csv_import(tipo_default: str):
    """Import CSV di asset: colonne isin,nome,[tipo,ticker_yf,quantita,valore_quota,proprietario]."""
    with st.expander("📤 Importa da file CSV / Excel"):
        st.caption("Colonne: `isin`, `nome` (obbligatorie); `tipo`, `ticker_yf`, `quantita`, "
                   "`valore_quota`, `proprietario` (facoltative). Se manca `tipo` si usa quello "
                   f"di questo step (**{tipo_default}**).")
        st.download_button("⬇️ Scarica modello CSV", CSV_TEMPLATE,
                           file_name="modello_asset.csv", mime="text/csv",
                           key=f"wiz_tpl_{tipo_default}")
        up = st.file_uploader("Carica file", type=['csv', 'xlsx'], key=f"wiz_csv_{tipo_default}")
        if up is None:
            return
        if up.size > MAX_UPLOAD_BYTES:
            st.error("File troppo grande (max 10 MB).")
            return
        try:
            raw = up.getvalue()
            if up.name.lower().endswith('.xlsx'):
                df = pd.read_excel(io.BytesIO(raw), engine='openpyxl', dtype=str)
            else:
                df = pd.read_csv(io.BytesIO(raw), dtype=str, sep=None, engine='python')
        except Exception as ex:
            st.error(f"File non leggibile ({type(ex).__name__}).")
            return
        df.columns = [str(c).strip().lower() for c in df.columns]
        if not {'isin', 'nome'} <= set(df.columns):
            st.error("Servono almeno le colonne `isin` e `nome`.")
            return
        if len(df) > MAX_CSV_ROWS:
            st.error(f"Troppe righe (max {MAX_CSV_ROWS}).")
            return

        validi, errori = [], []
        for n, r in enumerate(df.to_dict('records'), start=2):
            isin = _txt(r.get('isin')).upper()
            tipo = _txt(r.get('tipo'), 10).lower() or tipo_default
            prop = _txt(r.get('proprietario'), 10).lower() or 'persona1'
            if not ISIN_RE.match(isin):
                errori.append(f"riga {n}: ISIN non valido")
            elif tipo not in _TIPI:
                errori.append(f"riga {n}: tipo '{html.escape(tipo)}' non valido")
            elif not _txt(r.get('nome')):
                errori.append(f"riga {n}: nome mancante")
            else:
                validi.append({
                    'isin': isin, 'nome': _txt(r.get('nome')), 'tipo': tipo,
                    'ticker_yf': _txt(r.get('ticker_yf'), 20) or None,
                    'proprietario': prop if prop in _PROPRIETARI else 'persona1',
                    'quantita': _num(r.get('quantita')),
                    'quota': _num(r.get('valore_quota')),
                })
        st.write(f"Righe valide: **{len(validi)}** · scartate: **{len(errori)}**")
        if errori:
            st.warning("; ".join(errori[:10]) + (" …" if len(errori) > 10 else ""))
        if validi:
            st.dataframe(pd.DataFrame(validi), use_container_width=True, hide_index=True)
            if st.button(f"✅ Importa {len(validi)} asset", key=f"wiz_csv_go_{tipo_default}"):
                ko = 0
                for v in validi:
                    asset = {
                        'isin': v['isin'], 'nome': v['nome'], 'tipo': v['tipo'],
                        'ticker_yf': v['ticker_yf'], 'ticker_bi': None,
                        'proprietario': v['proprietario'], 'stato': 'attivo',
                        'valore_quota_ref': v['quota'] if v['quota'] > 0 else None,
                        'fallback_tickers': [],
                    }
                    if not _salva_asset_completo(asset, v['quantita'], v['quota']):
                        ko += 1
                _invalida_cache()
                if ko:
                    st.error(f"{ko} asset non salvati.")
                else:
                    st.success(f"✓ Importati {len(validi)} asset.")
                    st.rerun()


def render_transactions_upload(config: dict):
    """Upload estratti conto XLS/XLSX → parsing con adapter bancario → tabella transazioni."""
    st.subheader("📥 Carica estratti conto")
    st.caption("Carica i file scaricati dalla banca. Le transazioni già presenti non vengono duplicate.")
    banche = config.get('banche', [])
    if not banche:
        st.info("Nessuna banca configurata in config.yaml.")
        return
    labels = {b['id']: b['nome'] for b in banche}
    bid = st.selectbox("Banca / conto", list(labels), format_func=lambda x: labels[x],
                       key="wiz_tx_bank")
    banca = next(b for b in banche if b['id'] == bid)
    files = st.file_uploader("File estratto conto (.xls / .xlsx)", type=['xls', 'xlsx'],
                             accept_multiple_files=True, key="wiz_tx_files")
    if files and st.button("📥 Importa transazioni", key="wiz_tx_go"):
        from parser import parse_uploaded_file
        from database import salva_transazioni, carica_param
        kw = carica_param('transfer_keywords')
        kw = kw if isinstance(kw, list) else []
        tot_nuove = 0
        for f in files:
            if f.size > MAX_UPLOAD_BYTES:
                st.error(f"{html.escape(f.name)}: file troppo grande (max 10 MB).")
                continue
            try:
                df = parse_uploaded_file(f.getvalue(), f.name, banca['formato'],
                                         banca['intestatario'], keywords=kw)
            except Exception as ex:
                st.error(f"{html.escape(f.name)}: formato non riconosciuto ({type(ex).__name__}). "
                         "Controlla di aver scelto la banca/conto giusti.")
                continue
            if df.empty:
                st.warning(f"{html.escape(f.name)}: nessuna transazione trovata.")
                continue
            n = salva_transazioni(df)
            tot_nuove += n
            st.success(f"{html.escape(f.name)}: {len(df)} righe lette, {n} nuove.")
        if tot_nuove:
            for k in ('tx_db_cache', 'xls_importati'):
                st.session_state.pop(k, None)
