"""
Piano Finanziario Familiare — App Streamlit
Avviare con: streamlit run app.py
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from pathlib import Path
from datetime import date, datetime, timedelta
import sys

sys.path.insert(0, str(Path(__file__).parent / 'src'))

from parser import load_config, parse_all_inputs
from portfolio import (get_portfolio_performance, get_etf_history_chart, get_etf_data,
                        get_fondi_snapshot, simula_uscita_fondo_data_x, piano_uscita_ottimale,
                        get_azioni_snapshot, patrimonio_snapshot)
from simulator import (simula_pac, simula_pac_variabile, simula_pac_scenari,
                        simula_fondo_scenari, simula_portafoglio_fondi_scenari,
                        simula_portafoglio_etf_scenari, simula_migrazione_fondi,
                        simula_costi_flor, simula_scenario_completo, confronta_scenari)
from app_state import (init_db_connection, carica_params_persistenti,
                        salva_params_persistenti, carica_quote_fondi_persistenti,
                        auto_save_snapshot, importa_transazioni_xls,
                        carica_transazioni_db, get_storico_patrimonio,
                        get_storico_fondo, aggiorna_quote_fondi,
                        esegui_backfill_avvio, get_storico_portafoglio,
                        get_storico_asset, aggiorna_quantita_asset,
                        get_eventi_portafoglio)

st.set_page_config(page_title="Piano Finanziario Familiare",
                   page_icon="📊", layout="wide",
                   initial_sidebar_state="expanded")

# ── DEMO MODE — sempre attivo su branch dev
_DEMO = True

# ── AUTENTICAZIONE — richiesta sempre, anche in DEMO mode
_APP_PASSWORD = None
_AUTH_ERR = None
try:
    from database import carica_param as _carica_param_raw
    _APP_PASSWORD = _carica_param_raw("app_password")
except Exception:
    _AUTH_ERR = "db_error"

if _AUTH_ERR == "db_error":
    st.error("⚠️ DB non raggiungibile — impossibile verificare le credenziali. Riprova tra qualche istante.")
    st.stop()
elif not _APP_PASSWORD:
    st.error("⚠️ Password non configurata. Inserisci il valore `app_password` nella tabella `config_params` su Supabase.")
    st.stop()
elif not st.session_state.get("_auth_ok"):
    st.title("🔒 Accesso protetto")
    pwd = st.text_input("Password", type="password")
    if st.button("Accedi"):
        if pwd == _APP_PASSWORD:
            st.session_state["_auth_ok"] = True
            st.rerun()
        else:
            st.error("Password errata.")
    st.stop()

if _DEMO:
    from demo_data import (
        demo_init_db_connection            as init_db_connection,
        demo_carica_params_persistenti     as carica_params_persistenti,
        demo_salva_params_persistenti      as salva_params_persistenti,
        demo_carica_quote_fondi_persistenti as carica_quote_fondi_persistenti,
        demo_auto_save_snapshot            as auto_save_snapshot,
        demo_importa_transazioni_xls       as importa_transazioni_xls,
        demo_carica_transazioni_db         as carica_transazioni_db,
        demo_get_storico_patrimonio        as get_storico_patrimonio,
        demo_get_storico_fondo             as get_storico_fondo,
        demo_aggiorna_quote_fondi          as aggiorna_quote_fondi,
        demo_esegui_backfill_avvio         as esegui_backfill_avvio,
        demo_get_storico_portafoglio       as get_storico_portafoglio,
        demo_get_storico_asset             as get_storico_asset,
        demo_aggiorna_quantita_asset       as aggiorna_quantita_asset,
        demo_get_eventi_portafoglio        as get_eventi_portafoglio,
    )

BASE_DIR  = Path(__file__).parent
INPUT_DIR = BASE_DIR / 'data' / 'input'

COLORS    = {'blu':'#1F5C8B','verde':'#1baf7a','arancio':'#E9C46A',
             'rosso':'#E63946','grigio':'#888888','azzurro':'#A8DADC',
             'viola':'#7B2D8B','teal':'#2EC4B6'}
SC_COLORS = {'base':'#1F5C8B','worst':'#E63946','best':'#1baf7a'}
SC_DASH   = {'base':'solid','worst':'dash','best':'dot'}

# ── INIT — una volta per sessione ────────────────────────────
@st.cache_data(ttl=3600)
def get_config():
    return load_config(BASE_DIR / 'config.yaml')

@st.cache_data(ttl=1800)
def get_etf_perf():
    return get_portfolio_performance(get_config())

@st.cache_data(ttl=1800)
def get_azioni():
    return get_azioni_snapshot(get_config())

config = get_config()

# Connessione DB (una volta per sessione)
db_ok = init_db_connection()

# Carica parametri persistenti da Supabase (ultimi valori salvati)
if 'params' not in st.session_state:
    st.session_state['params'] = carica_params_persistenti(config)

_N1 = st.session_state['params'].get('nome_persona1', 'Persona 1')
_N2 = st.session_state['params'].get('nome_persona2', 'Persona 2')
_NF = st.session_state['params'].get('nome_figlio',   'Figlio/a')

# Carica quote fondi persistenti
if 'quote_map' not in st.session_state:
    st.session_state['quote_map'] = carica_quote_fondi_persistenti(config)

# Carica fondi con quote aggiornate dal DB — non cacheata (dipende da quote_map in session_state)
def get_fondi():
    if _DEMO:
        from demo_data import demo_get_fondi_snapshot
        return demo_get_fondi_snapshot()
    return get_fondi_snapshot(get_config(), st.session_state.get('quote_map', {}))

# Importa automaticamente eventuali nuovi XLS in data/input/
if 'xls_importati' not in st.session_state:
    st.session_state['xls_importati'] = False
if not st.session_state['xls_importati'] and db_ok:
    try:
        df_tx_xls = parse_all_inputs(INPUT_DIR, config)
        if not df_tx_xls.empty:
            n_nuove = importa_transazioni_xls(df_tx_xls)
            if n_nuove > 0:
                st.session_state['nuove_tx'] = n_nuove
        st.session_state['xls_importati'] = True
    except Exception:
        st.session_state['xls_importati'] = True

# Carica snapshot patrimonio corrente per auto-save
etf_df_init   = get_etf_perf()
fondi_df_init = get_fondi()
azioni_df_init= get_azioni()
snap_init = patrimonio_snapshot(
    config, etf_df_init, fondi_df_init, azioni_df_init,
    st.session_state['params']
)

# Auto-save silenzioso (upsert — sovrascrive se già salvato oggi)
if db_ok and 'saved_today' not in st.session_state:
    auto_save_snapshot(snap_init, st.session_state['params'])
    st.session_state['saved_today'] = True

# Backfill prezzi silenzioso — scarica retroattivamente i giorni mancanti
# Non blocca l'UI, gira in background al primo caricamento della sessione
if db_ok and 'backfill_done' not in st.session_state:
    try:
        res = esegui_backfill_avvio(config, verbose=False)
        st.session_state['backfill_done'] = True
        if res['n_prezzi_scaricati'] > 0:
            st.session_state['backfill_nuovi'] = res['n_prezzi_scaricati']
    except Exception:
        st.session_state['backfill_done'] = True

# ── SIDEBAR ──────────────────────────────────────────────────
with st.sidebar:
    st.title("📊 Piano Familiare")
    if _DEMO:
        st.warning("⚠️ DEMO MODE — dati fittizi")
        st.caption("Nessun dato reale viene letto o scritto.")
    else:
        st.caption(f"{_N1} & {_N2}")
    if db_ok:
        st.success("☁️ Supabase connesso", icon="✅")
    else:
        st.warning("⚠️ DB offline — dati non salvati")
    st.divider()
    _SEZIONE_FIGLIO = f"👶 {_NF} timeline"
    sezione = st.radio("Sezione", [
        "🏠 Stato di famiglia",
        "📉 Portafoglio storico",
        "📈 ETF & mercato",
        "🏦 Fondi bancari",
        "📊 Azioni Accenture",
        "🎯 Simulatore strategie",
        _SEZIONE_FIGLIO,
    ])
    with st.expander("✏️ Nomi"):
        n1_inp = st.text_input("Persona 1", value=_N1, key="edit_n1")
        n2_inp = st.text_input("Persona 2", value=_N2, key="edit_n2")
        nf_inp = st.text_input("Figlio/a",  value=_NF, key="edit_nf")
        if st.button("💾 Salva nomi"):
            st.session_state['params']['nome_persona1'] = n1_inp
            st.session_state['params']['nome_persona2'] = n2_inp
            st.session_state['params']['nome_figlio']   = nf_inp
            salva_params_persistenti({'nome_persona1': n1_inp,
                                      'nome_persona2': n2_inp,
                                      'nome_figlio':   nf_inp})
            st.rerun()
    if _APP_PASSWORD:
        with st.expander("🔑 Password"):
            new_pwd1 = st.text_input("Nuova password", type="password", key="pwd1")
            new_pwd2 = st.text_input("Conferma",       type="password", key="pwd2")
            if st.button("💾 Cambia password"):
                if not new_pwd1:
                    st.error("Inserisci una password.")
                elif new_pwd1 != new_pwd2:
                    st.error("Le password non corrispondono.")
                elif _DEMO:
                    st.warning("In DEMO mode il cambio password è disabilitato.")
                else:
                    from database import salva_param as _salva_param_raw
                    _salva_param_raw("app_password", new_pwd1)
                    st.success("Password aggiornata! Al prossimo login usa la nuova.")
    st.divider()
    st.caption(f"Config: {config['famiglia']['aggiornato']}")
    st.caption(f"Oggi: {date.today().strftime('%d/%m/%Y')}")
    if st.button("🔄 Aggiorna tutto"):
        st.cache_data.clear()
        for k in ['params','quote_map','xls_importati','saved_today',
                  'backfill_done','backfill_nuovi','nuove_tx']:
            st.session_state.pop(k, None)
        st.rerun()
    if st.session_state.get('nuove_tx'):
        st.info(f"📥 {st.session_state['nuove_tx']} nuove transazioni")
    if st.session_state.get('backfill_nuovi'):
        st.info(f"📡 {st.session_state['backfill_nuovi']} nuovi prezzi scaricati")


# ─────────────────────────────────────────────────────────────
# SEZIONE 1: STATO DI FAMIGLIA
# ─────────────────────────────────────────────────────────────
if sezione == "🏠 Stato di famiglia":
    st.title("Stato di famiglia")

    # Valori persistenti — pre-compilati dall'ultima sessione
    p_saved = st.session_state['params']
    with st.expander("🔧 Aggiorna valori manuali", expanded=False):
        st.caption("I valori vengono salvati automaticamente su Supabase e ripristinati alla prossima apertura.")
        c1, c2, c3 = st.columns(3)
        with c1:
            fondi_man    = st.number_input("Fondi bancari (€)", value=int(p_saved.get('fondi_bancari', config['patrimonio']['fondi_bancari'])), step=500)
            generali_man = st.number_input("Generali (€)", value=int(p_saved.get('gestione_separata_generali', config['patrimonio']['gestione_separata_generali'])), step=500)
        with c2:
            liq_dan = st.number_input(f"Liquidità {_N1} (€)", value=int(p_saved.get('liquidita_persona1', config['patrimonio']['liquidita_persona1'])), step=100)
            liq_ale = st.number_input(f"Liquidità {_N2} (€)", value=int(p_saved.get('liquidita_persona2', config['patrimonio']['liquidita_persona2'])), step=100)
        with c3:
            conto_com = st.number_input("Conto comune (€)", value=int(p_saved.get('conto_comune', config['patrimonio']['conto_comune'])), step=100)

        if st.button("💾 Salva valori"):
            nuovi_params = {
                'fondi_bancari': fondi_man, 'gestione_separata_generali': generali_man,
                'liquidita_persona1': liq_dan, 'liquidita_persona2': liq_ale,
                'conto_comune': conto_com,
                'affitto_accantonato': p_saved.get('affitto_accantonato',
                                                    config['patrimonio']['affitto_accantonato'])
            }
            if salva_params_persistenti(nuovi_params):
                st.session_state['params'] = nuovi_params
                st.session_state.pop('saved_today', None)  # forza re-save snapshot
                st.success("Salvato su Supabase ✓")
                st.rerun()

    params_correnti = {
        'fondi_bancari': fondi_man, 'gestione_separata_generali': generali_man,
        'liquidita_persona1': liq_dan, 'liquidita_persona2': liq_ale,
        'conto_comune': conto_com,
        'affitto_accantonato': p_saved.get('affitto_accantonato',
                                            config['patrimonio']['affitto_accantonato'])
    }

    etf_df   = get_etf_perf()
    fondi_df = get_fondi()
    azioni_df= get_azioni()
    snap = patrimonio_snapshot(config, etf_df, fondi_df, azioni_df, params_correnti)

    c1,c2,c3,c4,c5 = st.columns(5)
    c1.metric("Patrimonio totale", f"€ {snap['totale_eur']:,.0f}")
    c2.metric("Netto fiscale",     f"€ {snap['totale_netto_fiscale']:,.0f}", delta=f"-€ {snap['tassa_latente_fondi']:,.0f} latenti")
    c3.metric("Fondi bancari",     f"€ {snap['fondi_bancari']:,.0f}")
    c4.metric("ETF totale",   f"€ {snap['etf_persona1']+snap['etf_flor']:,.0f}")
    c5.metric("Azioni ACN (USD)",  f"$ {snap['azioni_acn_usd']:,.0f}")

    fig_pat = go.Figure(go.Pie(
        labels=['Fondi bancari','Generali',f'ETF {_N1}',f'ETF {_NF}','Azioni ACN','Liquidità'],
        values=[snap['fondi_bancari'],snap['generali'],snap['etf_persona1'],
                snap['etf_flor'],snap['azioni_acn_usd'],snap['liquidita']],
        hole=0.45, marker_colors=list(COLORS.values())[:6]
    ))
    fig_pat.update_layout(title="Composizione patrimonio", height=340, margin=dict(t=40,b=0,l=0,r=0))
    st.plotly_chart(fig_pat, use_container_width=True)

    # Storico patrimonio da Supabase
    log_df = get_storico_patrimonio(giorni=730)
    if not log_df.empty and 'totale_eur' in log_df.columns:
        st.subheader("Andamento patrimonio nel tempo")
        periodo_log = st.select_slider("Periodo storico",
                                        ["1 mese","3 mesi","6 mesi","1 anno","Tutto"],
                                        value="6 mesi", key="periodo_log")
        giorni_map = {"1 mese":30,"3 mesi":90,"6 mesi":180,"1 anno":365,"Tutto":9999}
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=giorni_map[periodo_log])
        log_filt = log_df[log_df.index >= cutoff]

        fig_log = go.Figure()
        fig_log.add_trace(go.Scatter(
            x=log_filt.index, y=log_filt['totale_eur'].round(0),
            name='Patrimonio totale', fill='tozeroy',
            line=dict(color=COLORS['blu'], width=2)
        ))
        if 'totale_netto_fiscale' in log_filt.columns:
            fig_log.add_trace(go.Scatter(
                x=log_filt.index, y=log_filt['totale_netto_fiscale'].round(0),
                name='Netto (al netto tasse)', line=dict(color=COLORS['verde'], width=1, dash='dash')
            ))
        fig_log.update_layout(height=250, xaxis_title="", yaxis_title="€",
                               margin=dict(t=20,b=0), legend=dict(orientation="h",y=1.08))
        st.plotly_chart(fig_log, use_container_width=True)

        # Delta rispetto a inizio periodo
        if len(log_filt) >= 2:
            delta = log_filt['totale_eur'].iloc[-1] - log_filt['totale_eur'].iloc[0]
            st.caption(f"Variazione nel periodo: **€ {delta:+,.0f}**")
    else:
        st.info("Lo storico si costruirà automaticamente giorno per giorno aprendo l'app.")

    st.subheader("Entrate & uscite bancarie")
    # Usa transazioni dal DB (più complete) o fallback su XLS locali
    df = carica_transazioni_db(mesi=12)
    if df.empty:
        st.info("Nessuna transazione nel DB. Copia gli XLS in data/input/ e premi 🔄 Aggiorna tutto.")
    else:
        mesi = sorted(df['month'].unique(), reverse=True)
        mese_sel = st.selectbox("Mese", [str(m) for m in mesi])
        mese_df  = df[df['month'] == mese_sel]
        e_tot = mese_df['income'].sum(); u_tot = mese_df['expense'].sum()
        budget= config['allocazione']['spese_correnti_ora']
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Entrate", f"€ {e_tot:,.0f}")
        c2.metric("Uscite",  f"€ {u_tot:,.0f}")
        c3.metric("Saldo",   f"€ {e_tot-u_tot:,.0f}")
        c4.metric("Δ budget",f"€ {u_tot-budget:+,.0f}", delta_color="inverse")

        cat_df = mese_df[mese_df['amount']<0].groupby('category')['expense'].sum().sort_values()
        fig_cat = go.Figure(go.Bar(x=cat_df.values, y=cat_df.index,
                                    orientation='h', marker_color=COLORS['blu']))
        fig_cat.update_layout(title=f"Uscite per categoria — {mese_sel}",
                               height=max(280,len(cat_df)*28), margin=dict(t=40,b=0,l=0,r=0))
        st.plotly_chart(fig_cat, use_container_width=True)
        with st.expander("Dettaglio transazioni"):
            show = mese_df[['date','account','description','amount','category']].copy()
            show['date']   = show['date'].dt.strftime('%d/%m/%Y')
            show['amount'] = show['amount'].map(lambda x: f"€ {x:,.2f}")
            st.dataframe(show, use_container_width=True, hide_index=True)


# ─────────────────────────────────────────────────────────────
# SEZIONE 2: PORTAFOGLIO STORICO
# ─────────────────────────────────────────────────────────────
elif sezione == "📉 Portafoglio storico":
    st.title("Portafoglio storico")
    st.caption("Valori giornalieri reali: quantità × prezzo di ogni giorno, "
               "scaricati retroattivamente all'apertura dell'app.")

    from positions import POSIZIONI_DEFAULT, ASSET_TICKERS, storico_posizioni

    # Periodo
    c1,c2 = st.columns(2)
    with c1:
        periodo_st = st.select_slider("Periodo",
            ["1 mese","3 mesi","6 mesi","1 anno","2 anni","Tutto"],
            value="1 anno", key="periodo_storico")
    with c2:
        vista = st.radio("Vista", ["Aggregato","Per asset"], horizontal=True)

    giorni_map = {"1 mese":30,"3 mesi":90,"6 mesi":180,
                  "1 anno":365,"2 anni":730,"Tutto":1825}
    data_da = date.today() - timedelta(days=giorni_map[periodo_st])

    # ── Vista aggregata ───────────────────────────────────────
    if vista == "Aggregato":
        with st.spinner("Caricamento storico portafoglio..."):
            df_port = get_storico_portafoglio(data_da)

        if df_port.empty:
            st.info("Nessun dato storico disponibile. "
                    "Premi 🔄 Aggiorna tutto per scaricare i prezzi.")
        else:
            # KPI
            valore_oggi = df_port['totale'].iloc[-1]
            valore_inizio = df_port['totale'].iloc[0]
            delta_eur = valore_oggi - valore_inizio
            delta_pct = (valore_oggi / valore_inizio - 1) * 100 if valore_inizio > 0 else 0

            c1,c2,c3,c4 = st.columns(4)
            c1.metric("Valore attuale", f"€ {valore_oggi:,.0f}")
            c2.metric("Inizio periodo", f"€ {valore_inizio:,.0f}")
            c3.metric("Variazione €", f"€ {delta_eur:+,.0f}")
            c4.metric("Variazione %", f"{delta_pct:+.1f}%")

            # Grafico valore totale
            fig_tot = go.Figure()
            fig_tot.add_trace(go.Scatter(
                x=df_port['data'], y=df_port['totale'].round(0),
                name='Valore totale', fill='tozeroy',
                line=dict(color=COLORS['blu'], width=2)
            ))

            # Evidenzia eventi (acquisti/vendite)
            eventi = get_eventi_portafoglio(data_da)
            if not eventi.empty:
                for _, ev in eventi.iterrows():
                    fig_tot.add_vline(
                        x=str(ev['data_inizio']),
                        line_dash="dot", line_color=COLORS['arancio'],
                        opacity=0.7,
                        annotation_text=f"↕ {ev['nome'][:15]}",
                        annotation_font_size=9
                    )

            fig_tot.update_layout(
                title="Valore totale portafoglio fondi",
                height=380, xaxis_title="", yaxis_title="€",
                margin=dict(t=40,b=0)
            )
            st.plotly_chart(fig_tot, use_container_width=True)

            # Grafico composizione (area stacked)
            st.subheader("Composizione nel tempo")
            isins_disponibili = [c for c in df_port.columns
                                  if c not in ['data','totale']
                                  and df_port[c].sum() > 0]

            nomi_map = {p['isin']: p['nome'] for p in POSIZIONI_DEFAULT}
            pal_area = [COLORS['blu'], COLORS['verde'], COLORS['arancio'],
                        COLORS['rosso'], COLORS['azzurro'], COLORS['viola'],
                        COLORS['teal'], COLORS['grigio']]

            fig_comp = go.Figure()
            for i, isin in enumerate(isins_disponibili):
                nome = nomi_map.get(isin, isin)
                fig_comp.add_trace(go.Scatter(
                    x=df_port['data'], y=df_port[isin].round(0),
                    name=nome, stackgroup='one',
                    line=dict(color=pal_area[i % len(pal_area)], width=0.5)
                ))
            fig_comp.update_layout(
                title="Composizione portafoglio (valore €)",
                height=350, xaxis_title="", yaxis_title="€",
                legend=dict(orientation="h", y=1.08),
                margin=dict(t=60,b=0)
            )
            st.plotly_chart(fig_comp, use_container_width=True)

    # ── Vista per asset ───────────────────────────────────────
    else:
        # Selezione asset
        tutti_asset = [p for p in POSIZIONI_DEFAULT if p['quantita'] > 0]
        nomi_asset = [p['nome'] for p in tutti_asset]
        asset_sel = st.selectbox("Seleziona asset", nomi_asset)
        isin_sel = next((p['isin'] for p in tutti_asset if p['nome'] == asset_sel), None)

        if isin_sel:
            with st.spinner(f"Caricamento storico {asset_sel}..."):
                df_asset = get_storico_asset(isin_sel, data_da)

            if df_asset.empty:
                st.info(f"Nessun dato storico per {asset_sel}. "
                        "Premi 🔄 Aggiorna tutto.")
            else:
                # KPI
                v_oggi = df_asset['valore'].iloc[-1]
                v_inizio = df_asset['valore'].iloc[0]
                p_oggi = df_asset['prezzo'].iloc[-1]
                q_attuale = df_asset['quantita'].iloc[-1]
                delta_v = v_oggi - v_inizio
                delta_p = (p_oggi / df_asset['prezzo'].iloc[0] - 1)*100 \
                          if df_asset['prezzo'].iloc[0] > 0 else 0

                c1,c2,c3,c4 = st.columns(4)
                c1.metric("Quote possedute", f"{q_attuale:,.3f}")
                c2.metric("Prezzo attuale", f"€ {p_oggi:.4f}")
                c3.metric("Valore attuale", f"€ {v_oggi:,.0f}")
                c4.metric("Var. periodo", f"{delta_p:+.1f}%",
                          delta=f"€ {delta_v:+,.0f}")

                # Grafico doppio: prezzo + valore
                fig_asset = go.Figure()
                fig_asset.add_trace(go.Scatter(
                    x=df_asset['data'], y=df_asset['prezzo'],
                    name='Prezzo quota (€)',
                    line=dict(color=COLORS['blu'], width=2),
                    yaxis='y1'
                ))
                fig_asset.add_trace(go.Scatter(
                    x=df_asset['data'], y=df_asset['valore'].round(0),
                    name='Valore totale (€)',
                    line=dict(color=COLORS['verde'], width=2, dash='dot'),
                    yaxis='y2'
                ))
                fig_asset.update_layout(
                    title=f"{asset_sel} — prezzo quota e valore posizione",
                    height=380,
                    yaxis=dict(title="Prezzo quota €", side='left'),
                    yaxis2=dict(title="Valore € posizione",
                                side='right', overlaying='y'),
                    legend=dict(orientation="h", y=1.08),
                    margin=dict(t=60,b=0)
                )
                st.plotly_chart(fig_asset, use_container_width=True)

                # Storico quantità (variazioni)
                st.subheader("Storico variazioni quantità")
                df_pos = storico_posizioni(isin_sel)
                if not df_pos.empty:
                    show_pos = df_pos[['data_inizio','data_fine','quantita','note']].copy()
                    show_pos.columns = ['Da','A','Quote','Note']
                    show_pos['A'] = show_pos['A'].fillna('oggi')
                    st.dataframe(show_pos, use_container_width=True, hide_index=True)

                # Form aggiornamento quantità
                st.subheader("Registra acquisto / vendita parziale")
                with st.form("modifica_quantita"):
                    c1,c2 = st.columns(2)
                    with c1:
                        nuova_q = st.number_input(
                            "Nuova quantità totale dopo l'operazione",
                            value=float(q_attuale), step=0.001, format="%.3f")
                    with c2:
                        data_op = st.date_input("Data operazione", value=date.today())
                    note_op = st.text_input("Note (es. 'Rimborso parziale 20%')", "")
                    submitted = st.form_submit_button("💾 Salva operazione")
                    if submitted:
                        if aggiorna_quantita_asset(isin_sel, nuova_q, data_op, note_op):
                            st.success(f"✓ Quantità aggiornata a {nuova_q:.3f} "
                                       f"dal {data_op.strftime('%d/%m/%Y')}")
                            # Forza ricalcolo
                            for k in ['backfill_done']:
                                st.session_state.pop(k, None)
                            st.rerun()
                        else:
                            st.error("Errore nel salvataggio. Riprova.")

# ─────────────────────────────────────────────────────────────
# SEZIONE 3: ETF & MERCATO
# ─────────────────────────────────────────────────────────────
elif sezione == "📈 ETF & mercato":
    st.title("ETF & mercato")
    etf_df = get_etf_perf()

    attivi     = etf_df[etf_df['stato']=='attivo']
    da_avviare = etf_df[etf_df['stato']=='da_avviare']
    candidati  = etf_df[etf_df['stato']=='candidato']

    if not attivi.empty:
        st.subheader("ETF attivi")
        for _,r in attivi.iterrows():
            c1,c2,c3,c4 = st.columns(4)
            c1.metric(r['nome'], f"€ {r['valore_attuale']:,.0f}")
            c2.metric("Prezzo", f"$ {r['prezzo_attuale']:.2f}" if r['prezzo_attuale'] else "N/D")
            c3.metric("YTD", f"{r['rendimento_pct']:+.1f}%" if r['rendimento_pct'] else "N/D")
            c4.metric("1 anno", f"{r['perf_1y']:+.1f}%" if r['perf_1y'] else "N/D")

    # Grafico storico
    st.subheader("Performance storica")
    periodo = st.select_slider("Periodo", ["1mo","3mo","6mo","ytd","1y","2y"], value="1y")
    tutti_ticker = {r['ticker']: r['ticker_yf'] for _,r in etf_df.iterrows() if r['ticker_yf']}
    tutti_ticker['ACN'] = 'ACN'
    sel = st.multiselect("Titoli da confrontare", list(tutti_ticker.keys()),
                          default=list(tutti_ticker.keys())[:3])
    if sel:
        fig = go.Figure()
        pal = list(COLORS.values())
        for i, nome in enumerate(sel):
            h = get_etf_history_chart(tutti_ticker.get(nome, nome), periodo)
            if not h.empty:
                fig.add_trace(go.Scatter(x=h.index, y=h['indexed'].round(2),
                                          name=nome, line=dict(color=pal[i%len(pal)],width=2)))
        fig.add_hline(y=100, line_dash="dash", line_color="gray", opacity=0.4)
        fig.update_layout(title=f"Performance relativa (base 100) · {periodo}",
                           height=400, legend=dict(orientation="h",y=1.08))
        st.plotly_chart(fig, use_container_width=True)

    # ── SIMULATORE PAC ETF — tabella editabile ────────────────
    st.subheader("Simulatore PAC — portafoglio ETF personalizzabile")
    st.caption("Aggiungi, rimuovi o modifica gli ETF e l'importo mensile per ciascuno. "
               "Il grafico mostra il portafoglio aggregato con scenari worst/base/best.")

    # Tabella editabile ETF PAC
    etf_default = [
        {'ETF': 'IWDA', 'Descrizione': 'iShares Core MSCI World', 'Importo €/mese': 800,
         'Valore iniziale €': 10500, 'Includi': True},
        {'ETF': 'ACWE (Figlio/a)', 'Descrizione': 'SPDR MSCI ACWI', 'Importo €/mese': 200,
         'Valore iniziale €': 0, 'Includi': True},
        {'ETF': 'EMAE', 'Descrizione': 'SPDR MSCI EM Asia', 'Importo €/mese': 0,
         'Valore iniziale €': 0, 'Includi': False},
        {'ETF': 'MWRD', 'Descrizione': 'Amundi Core MSCI World', 'Importo €/mese': 0,
         'Valore iniziale €': 0, 'Includi': False},
    ]
    df_etf_edit = st.data_editor(
        pd.DataFrame(etf_default),
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            'ETF': st.column_config.TextColumn('Ticker / Nome', width='small'),
            'Descrizione': st.column_config.TextColumn('Descrizione', width='medium'),
            'Importo €/mese': st.column_config.NumberColumn('€/mese PAC', min_value=0, step=50),
            'Valore iniziale €': st.column_config.NumberColumn('Valore attuale €', min_value=0, step=100),
            'Includi': st.column_config.CheckboxColumn('Includi'),
        },
        key="etf_editor"
    )

    c1,c2,c3 = st.columns(3)
    with c1: anni_pac = st.slider("Orizzonte PAC (anni)", 5, 30, 20, key="anni_pac_etf")
    with c2: rend_base_etf = st.slider("Rendimento base (%)", 3.0, 10.0, 7.0, step=0.5, key="rb_etf")
    with c3: mostra_singoli = st.checkbox("Mostra anche ETF singoli", value=True)

    df_attivi = df_etf_edit[df_etf_edit['Includi'] == True]

    if not df_attivi.empty:
        etf_rows_sim = df_attivi.rename(columns={
            'ETF': 'nome', 'Importo €/mese': 'importo_mensile',
            'Valore iniziale €': 'valore_iniziale'
        }).to_dict('records')

        sc_etf = simula_portafoglio_etf_scenari(
            etf_rows_sim, anni=anni_pac,
            rend_base=rend_base_etf/100,
            rend_worst=max(rend_base_etf/100 - 0.04, 0.01),
            rend_best=rend_base_etf/100 + 0.03
        )

        fig_etf_sc = go.Figure()
        # Banda worst-best
        anni_arr = sc_etf['base']['anno'].tolist()
        fig_etf_sc.add_trace(go.Scatter(
            x=anni_arr + anni_arr[::-1],
            y=sc_etf['best']['valore'].tolist() + sc_etf['worst']['valore'].tolist()[::-1],
            fill='toself', fillcolor='rgba(30,92,139,0.08)',
            line=dict(color='rgba(255,255,255,0)'), name='Intervallo worst-best', showlegend=True
        ))
        for sc, label in [('base','Base'), ('worst','Worst'), ('best','Best')]:
            fig_etf_sc.add_trace(go.Scatter(
                x=sc_etf[sc]['anno'], y=sc_etf[sc]['valore'],
                name=f"{label}", line=dict(color=SC_COLORS[sc], width=2, dash=SC_DASH[sc])
            ))

        # Singoli ETF (scenario base)
        if mostra_singoli:
            pal2 = [COLORS['arancio'], COLORS['teal'], COLORS['viola'], COLORS['azzurro']]
            for i, row in enumerate(etf_rows_sim):
                df_sing = simula_pac(row.get('importo_mensile',0), anni_pac,
                                      rend_base_etf/100)
                df_sing['valore'] += row.get('valore_iniziale', 0)
                fig_etf_sc.add_trace(go.Scatter(
                    x=df_sing['anno'], y=df_sing['valore'],
                    name=f"{row['nome']} (base)",
                    line=dict(color=pal2[i%len(pal2)], width=1, dash='dot'),
                    opacity=0.7
                ))

        tot_pac = df_attivi['Importo €/mese'].sum()
        tot_ini = df_attivi['Valore iniziale €'].sum()
        vf_base = sc_etf['base']['valore'].iloc[-1]
        vf_worst= sc_etf['worst']['valore'].iloc[-1]
        vf_best = sc_etf['best']['valore'].iloc[-1]

        fig_etf_sc.update_layout(
            title=f"Portafoglio ETF — €{tot_pac:,.0f}/mese · partenza €{tot_ini:,.0f} · {anni_pac} anni",
            height=420, xaxis_title="Anni", yaxis_title="€",
            legend=dict(orientation="h", y=1.08)
        )
        st.plotly_chart(fig_etf_sc, use_container_width=True)

        c1,c2,c3,c4 = st.columns(4)
        c1.metric("PAC totale/mese", f"€ {tot_pac:,.0f}")
        c2.metric(f"Valore base ({anni_pac}a)", f"€ {vf_base:,.0f}")
        c3.metric(f"Scenario worst", f"€ {vf_worst:,.0f}", delta=f"€ {vf_worst-vf_base:+,.0f}")
        c4.metric(f"Scenario best",  f"€ {vf_best:,.0f}",  delta=f"€ {vf_best-vf_base:+,.0f}")

    if not candidati.empty:
        st.subheader("Confronto ETF candidati")
        fig_c = go.Figure()
        per_c = st.select_slider("Periodo confronto", ["6mo","ytd","1y","2y"], value="1y", key="pc")
        confronto = {'EMAE':'EMAE.MI','MWRD':'MWRD.MI','IWDA':'IWDA.AS','CSPX':'CSPX.L'}
        pal_c = [COLORS['rosso'],COLORS['verde'],COLORS['blu'],COLORS['arancio']]
        for i,(nome,tick) in enumerate(confronto.items()):
            h = get_etf_history_chart(tick, per_c)
            if not h.empty:
                fig_c.add_trace(go.Scatter(x=h.index, y=h['indexed'].round(2),
                                            name=nome, line=dict(color=pal_c[i],width=2)))
        fig_c.add_hline(y=100, line_dash="dash", line_color="gray", opacity=0.4)
        fig_c.update_layout(title="Confronto (base 100)", height=320,
                             legend=dict(orientation="h",y=1.08))
        st.plotly_chart(fig_c, use_container_width=True)
        st.info("💡 MWRD = alternativa Amundi a IWDA (stesso indice, TER 0.12%). "
                "EMAE = satellite emergenti asiatici — interessante al 10-15% del PAC "
                "se vuoi esposizione separata dall'ACWI del figlio/a.")


# ─────────────────────────────────────────────────────────────
# SEZIONE 3: FONDI BANCARI
# ─────────────────────────────────────────────────────────────
elif sezione == "🏦 Fondi bancari":
    st.title("Fondi bancari — analisi e scenari")

    # ── Tabella editabile fondi ───────────────────────────────
    st.subheader("Il tuo portafoglio fondi — modifica liberamente")
    st.caption("Aggiorna le quote, cambia la % da mantenere, aggiungi o rimuovi righe. "
               "Il grafico e il piano di uscita si aggiornano in tempo reale.")

    fondi_df = get_fondi()
    costi_map = {
        'ARCA AZ EUROPA CLIMA': 2.0, 'ARCA AZ AMERICA CLIMA P': 2.0,
        'EURIZON AZ EMERG P': 2.5, 'JPMF GLO SUST EQ ACC': 2.2,
        'EURIZON AZ AMER P': 2.0, 'EURIZ AZ AREA EURO P': 1.9, 'EURIZON AZ INT P': 1.8,
    }
    df_edit_default = pd.DataFrame([{
        'Fondo': r['nome'],
        'ISIN': r['isin'],
        'Quote': r['quantita'],
        'Quota €': r['quota_attuale'],
        'Valore €': r['valore_attuale'],
        'Costo fisc. €': r['costo_fiscale_stimato'],
        'TER stimato %': costi_map.get(r['nome'], 2.0),
        '% mantenere': 100,
        'Includi': True,
    } for _, r in fondi_df.iterrows()])

    df_fondi_edit = st.data_editor(
        df_edit_default,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            'Fondo':          st.column_config.TextColumn('Fondo', width='medium'),
            'ISIN':           st.column_config.TextColumn('ISIN', width='medium'),
            'Quote':          st.column_config.NumberColumn('Quote', format="%.3f"),
            'Quota €':        st.column_config.NumberColumn('Quota €', format="%.4f"),
            'Valore €':       st.column_config.NumberColumn('Valore €', format="%.2f"),
            'Costo fisc. €':  st.column_config.NumberColumn('Costo fiscale €', format="%.2f"),
            'TER stimato %':  st.column_config.NumberColumn('TER %', min_value=0.0, max_value=5.0, step=0.1, format="%.2f"),
            '% mantenere':    st.column_config.NumberColumn('% da mantenere', min_value=0, max_value=100, step=10),
            'Includi':        st.column_config.CheckboxColumn('Includi'),
        },
        key="fondi_editor"
    )

    df_attivi_f = df_fondi_edit[df_fondi_edit['Includi'] == True].copy()

    # Totali snapshot
    tot_val  = (df_attivi_f['Valore €'] * df_attivi_f['% mantenere'] / 100).sum()
    tot_cf   = (df_attivi_f['Costo fisc. €'] * df_attivi_f['% mantenere'] / 100).sum()
    tot_pv   = max(tot_val - tot_cf, 0)
    tot_tassa= tot_pv * 0.26
    tot_netto= tot_val - tot_tassa
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Valore selezionato", f"€ {tot_val:,.0f}")
    c2.metric("Plusvalenza stimata", f"€ {tot_pv:,.0f}")
    c3.metric("Tassa latente 26%",  f"€ {tot_tassa:,.0f}")
    c4.metric("Netto se esci oggi",  f"€ {tot_netto:,.0f}")

    # ── Grafico singolo fondo + scenari ──────────────────────
    st.subheader("Scenari per singolo fondo")
    nomi_inclusi = df_attivi_f['Fondo'].tolist()
    fondo_sel = st.selectbox("Seleziona fondo", nomi_inclusi) if nomi_inclusi else None

    c1,c2,c3 = st.columns(3)
    with c1: anni_f = st.slider("Orizzonte (anni)", 1, 15, 5, key="anni_fondo")
    with c2: rend_b_f = st.slider("Rendimento base %", 0.0, 8.0, 4.0, step=0.5, key="rb_fondo")
    with c3: rend_w_f = st.slider("Rendimento worst %", -5.0, 4.0, 0.0, step=0.5, key="rw_fondo")
    rend_best_f = st.slider("Rendimento best %", 4.0, 12.0, 8.0, step=0.5, key="rbest_fondo")

    # Storico quote fondo selezionato da Supabase
    if fondo_sel:
        isin_sel = df_attivi_f[df_attivi_f['Fondo'] == fondo_sel]['ISIN'].iloc[0]
        storico_f = get_storico_fondo(isin_sel, giorni=365)
        if not storico_f.empty and len(storico_f) > 1:
            st.subheader(f"Storico quote — {fondo_sel}")
            fig_st = go.Figure()
            fig_st.add_trace(go.Scatter(x=storico_f['data'], y=storico_f['quota'],
                                         name='Quota €', line=dict(color=COLORS['blu'],width=2)))
            fig_st.update_layout(height=200, xaxis_title="", yaxis_title="Quota €",
                                  margin=dict(t=20,b=0))
            st.plotly_chart(fig_st, use_container_width=True)

    if fondo_sel:
        row_f = df_attivi_f[df_attivi_f['Fondo'] == fondo_sel].iloc[0]
        pct   = row_f['% mantenere'] / 100
        ter   = row_f['TER stimato %'] / 100
        val_f = row_f['Valore €'] * pct
        cf_f  = row_f['Costo fisc. €'] * pct

        df_sc_f = simula_fondo_scenari(
            val_f, cf_f, quantita_pct=1.0, anni=anni_f,
            rend_base=rend_b_f/100, rend_worst=rend_w_f/100, rend_best=rend_best_f/100,
            costo_annuo=ter, aliquota=0.26
        )

        fig_f = go.Figure()
        # Banda
        base_s = df_sc_f[df_sc_f['scenario']=='base']
        worst_s= df_sc_f[df_sc_f['scenario']=='worst']
        best_s = df_sc_f[df_sc_f['scenario']=='best']
        fig_f.add_trace(go.Scatter(
            x=best_s['anno'].tolist() + worst_s['anno'].tolist()[::-1],
            y=best_s['netto_uscita'].tolist() + worst_s['netto_uscita'].tolist()[::-1],
            fill='toself', fillcolor='rgba(30,139,92,0.08)',
            line=dict(color='rgba(0,0,0,0)'), name='Range worst-best'
        ))
        for sc_df, label in [(base_s,'Base'),(worst_s,'Worst'),(best_s,'Best')]:
            fig_f.add_trace(go.Scatter(
                x=sc_df['anno'], y=sc_df['netto_uscita'],
                name=f"Netto {label}", line=dict(color=SC_COLORS[sc_df['scenario'].iloc[0]], width=2, dash=SC_DASH[sc_df['scenario'].iloc[0]])
            ))
            fig_f.add_trace(go.Scatter(
                x=sc_df['anno'], y=sc_df['valore_lordo'],
                name=f"Lordo {label}", line=dict(color=SC_COLORS[sc_df['scenario'].iloc[0]], width=1, dash='dot'),
                opacity=0.5
            ))

        fig_f.update_layout(
            title=f"{fondo_sel} — {int(pct*100)}% · scenari worst/base/best",
            height=400, xaxis_title="Anni", yaxis_title="€",
            legend=dict(orientation="h", y=1.08)
        )
        st.plotly_chart(fig_f, use_container_width=True)

        # KPI anno scelto
        anno_kpi = st.slider("Mostra valori all'anno", 0, anni_f, min(3, anni_f), key="kpi_anno")
        for sc_df, label in [(base_s,'Base'),(worst_s,'Worst'),(best_s,'Best')]:
            row_k = sc_df[sc_df['anno'] == anno_kpi]
            if not row_k.empty:
                st.write(f"**{label}** — Anno {anno_kpi}: "
                          f"Lordo €{row_k['valore_lordo'].iloc[0]:,.0f} | "
                          f"Tassa €{row_k['tassa_latente'].iloc[0]:,.0f} | "
                          f"Netto **€{row_k['netto_uscita'].iloc[0]:,.0f}**")

    # ── Grafico aggregato tutti i fondi ──────────────────────
    st.subheader("Portafoglio fondi aggregato — worst / base / best")

    if not df_attivi_f.empty:
        fondi_rows_sim = [{
            'nome': r['Fondo'],
            'valore_attuale': r['Valore €'],
            'costo_fiscale_stimato': r['Costo fisc. €'],
            'costo_annuo': r['TER stimato %'] / 100,
            'pct_da_mantenere': r['% mantenere']
        } for _, r in df_attivi_f.iterrows()]

        sc_agg = simula_portafoglio_fondi_scenari(
            fondi_rows_sim, anni=anni_f,
            rend_base=rend_b_f/100, rend_worst=rend_w_f/100,
            rend_best=rend_best_f/100, aliquota=0.26
        )

        fig_agg = go.Figure()
        best_agg  = sc_agg['best']
        worst_agg = sc_agg['worst']
        fig_agg.add_trace(go.Scatter(
            x=best_agg['anno'].tolist() + worst_agg['anno'].tolist()[::-1],
            y=best_agg['netto_uscita'].tolist() + worst_agg['netto_uscita'].tolist()[::-1],
            fill='toself', fillcolor='rgba(30,92,139,0.08)',
            line=dict(color='rgba(0,0,0,0)'), name='Range worst-best'
        ))
        for sc_key, label in [('base','Base'),('worst','Worst'),('best','Best')]:
            df_sc = sc_agg[sc_key]
            fig_agg.add_trace(go.Scatter(
                x=df_sc['anno'], y=df_sc['netto_uscita'],
                name=f"Netto {label}",
                line=dict(color=SC_COLORS[sc_key], width=2, dash=SC_DASH[sc_key])
            ))
            fig_agg.add_trace(go.Scatter(
                x=df_sc['anno'], y=df_sc['valore_lordo'],
                name=f"Lordo {label}",
                line=dict(color=SC_COLORS[sc_key], width=1, dash='dot'), opacity=0.4
            ))

        fig_agg.update_layout(
            title="Portafoglio fondi aggregato — evoluzione netta e lorda",
            height=420, xaxis_title="Anni", yaxis_title="€",
            legend=dict(orientation="h", y=1.08)
        )
        st.plotly_chart(fig_agg, use_container_width=True)

    # ── Simulatore uscita data X ──────────────────────────────
    st.subheader("Simulatore: se esco il giorno X")
    c1,c2 = st.columns(2)
    with c1: data_uscita = st.date_input("Data di uscita", value=date(2027,6,1), min_value=date.today())
    with c2: rend_uscita = st.slider("Rendimento fondi (%)", 1.0, 8.0, 4.0, step=0.5, key="ru")

    quote_map = {r['ISIN']: r['Quota €'] for _,r in df_fondi_edit.iterrows() if r['Includi']}
    df_uscita = simula_uscita_fondo_data_x(config, data_uscita, rend_uscita/100, quote_map)
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Attesa", f"{df_uscita['mesi_attesa'].iloc[0]:.0f} mesi")
    c2.metric("Valore proiettato", f"€ {df_uscita['valore_proiettato'].sum():,.0f}")
    c3.metric("Tassa stimata",     f"€ {df_uscita['tassa_proiettata'].sum():,.0f}")
    c4.metric("Netto proiettato",  f"€ {df_uscita['netto_proiettato'].sum():,.0f}")

    show_u = df_uscita[['nome','valore_attuale','netto_uscita','valore_proiettato','tassa_proiettata','netto_proiettato']].copy()
    for c in show_u.columns[1:]:
        show_u[c] = show_u[c].map(lambda x: f"€ {x:,.2f}")
    show_u.columns = ['Fondo','Val. attuale','Netto oggi','Val. proiettato','Tassa','Netto proiettato']
    st.dataframe(show_u, use_container_width=True, hide_index=True)

    # ── Piano uscita ottimale ─────────────────────────────────
    st.subheader("Piano di uscita ottimale")
    piano_df = piano_uscita_ottimale(config, quote_map)
    if not piano_df.empty:
        fig_piano = px.bar(piano_df, x='anno', y='netto_in_etf', color='fondo',
                            title="Netto reinvestito in ETF per anno e per fondo",
                            labels={'anno':'Anno','netto_in_etf':'€ netto'})
        fig_piano.update_layout(height=300, legend=dict(orientation="h",y=1.08))
        st.plotly_chart(fig_piano, use_container_width=True)
        c1,c2,c3 = st.columns(3)
        c1.metric("Tot. rimborsato", f"€ {piano_df['rimborso_lordo'].sum():,.0f}")
        c2.metric("Tot. tasse",      f"€ {piano_df['tassa_26pct'].sum():,.0f}")
        c3.metric("Tot. netto ETF",  f"€ {piano_df['netto_in_etf'].sum():,.0f}")


# ─────────────────────────────────────────────────────────────
# SEZIONE 4: AZIONI ACCENTURE
# ─────────────────────────────────────────────────────────────
elif sezione == "📊 Azioni Accenture":
    st.title("Azioni Accenture")
    azioni_df = get_azioni()
    if not azioni_df.empty:
        r = azioni_df.iloc[0]
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Quantità",         f"{int(r['quantita'])} azioni")
        c2.metric("Prezzo attuale",    f"$ {r['prezzo_attuale_usd']:,.2f}" if r['prezzo_attuale_usd'] else "N/D")
        c3.metric("Valore totale USD", f"$ {r['valore_attuale_usd']:,.0f}" if r['valore_attuale_usd'] else "N/D")
        c4.metric("YTD",               f"{r['perf_ytd']:+.1f}%" if r['perf_ytd'] else "N/D")
        st.caption(f"ℹ️ {r['note']}")

    # Grafico storico ACN con scenari
    st.subheader("Andamento storico Accenture")
    periodo_acn = st.select_slider("Periodo", ["3mo","6mo","ytd","1y","2y","5y"], value="2y", key="pacn")
    hist_acn = get_etf_data("ACN", periodo_acn)
    if not hist_acn.empty:
        fig_acn = go.Figure()
        fig_acn.add_trace(go.Scatter(x=hist_acn.index, y=hist_acn['price'].round(2),
                                      name='ACN', fill='tozeroy',
                                      line=dict(color=COLORS['blu'],width=2)))
        fig_acn.update_layout(title=f"Accenture (ACN) — {periodo_acn}",
                               yaxis_title="USD", height=360)
        st.plotly_chart(fig_acn, use_container_width=True)

    st.subheader("Simulatore vendita — scenari worst/base/best")
    if not azioni_df.empty and azioni_df.iloc[0]['prezzo_attuale_usd']:
        prezzo_ora = azioni_df.iloc[0]['prezzo_attuale_usd']
        quantita_tot = int(azioni_df.iloc[0]['quantita'])
        c1,c2 = st.columns(2)
        with c1: n_vend  = st.slider("Azioni da vendere", 1, quantita_tot, 20, key="nacn")
        with c2: mesi_acn= st.slider("Tra quanti mesi?", 1, 48, 12, key="macn")
        c1,c2,c3 = st.columns(3)
        with c1: rw_acn = st.slider("Worst %/anno", -30, 0, -10, key="rw_acn")
        with c2: rb_acn = st.slider("Base %/anno",  -10, 20, 10,  key="rb_acn")
        with c3: rb2_acn= st.slider("Best %/anno",   0, 40, 25,   key="rbest_acn")

        fig_vend = go.Figure()
        anni_arr = [i/12 for i in range(mesi_acn+1)]
        for rend, label, col in [(rw_acn,'Worst',SC_COLORS['worst']),
                                   (rb_acn,'Base',SC_COLORS['base']),
                                   (rb2_acn,'Best',SC_COLORS['best'])]:
            prezzi = [prezzo_ora * (1 + rend/100) ** (m/12) for m in range(mesi_acn+1)]
            valori = [p * n_vend for p in prezzi]
            fig_vend.add_trace(go.Scatter(x=anni_arr, y=[round(v,0) for v in valori],
                                           name=label, line=dict(color=col,width=2)))

        fig_vend.update_layout(title=f"Valore {n_vend} azioni ACN — scenari",
                                height=320, xaxis_title="Anni", yaxis_title="USD")
        st.plotly_chart(fig_vend, use_container_width=True)

        anni_fraz = mesi_acn / 12
        for rend, label in [(rw_acn,'Worst'),(rb_acn,'Base'),(rb2_acn,'Best')]:
            p_proj = prezzo_ora * (1 + rend/100) ** anni_fraz
            st.write(f"**{label}:** {n_vend} azioni → $ {p_proj*n_vend:,.0f} "
                      f"(prezzo $ {p_proj:.2f} | residue {quantita_tot-n_vend} azioni = $ {p_proj*(quantita_tot-n_vend):,.0f})")

        st.warning("⚠️ RSU Accenture: tassazione separata — verificare con CAF/commercialista.")


# ─────────────────────────────────────────────────────────────
# SEZIONE 5: SIMULATORE STRATEGIE
# ─────────────────────────────────────────────────────────────
elif sezione == "🎯 Simulatore strategie":
    st.title("Simulatore strategie")
    tab1,tab2,tab3 = st.tabs(["PAC semplice","Migrazione fondi","Scenario completo"])

    with tab1:
        st.subheader("Simulatore PAC con scenari")
        c1,c2,c3 = st.columns(3)
        with c1: imp   = st.slider("Importo mensile (€)", 100, 3000, config['allocazione']['pac_persona1_ora'], step=50)
        with c2: anni  = st.slider("Anni", 5, 30, 20)
        with c3: rend  = st.slider("Rendimento base (%)", 3.0, 12.0, 7.0, step=0.5)

        variabile = st.checkbox("Simula riduzione PAC con asilo nido (mag 2027)")
        sc_pac = simula_pac_scenari(imp, anni, rend_base=rend/100,
                                     rend_worst=max(rend/100-0.04,0.01), rend_best=rend/100+0.03)
        fig_pac = go.Figure()
        anni_arr = sc_pac['base']['anno'].tolist()
        fig_pac.add_trace(go.Scatter(
            x=anni_arr+anni_arr[::-1],
            y=sc_pac['best']['valore'].tolist()+sc_pac['worst']['valore'].tolist()[::-1],
            fill='toself', fillcolor='rgba(30,92,139,0.08)',
            line=dict(color='rgba(0,0,0,0)'), name='Range'
        ))
        for sc,label in [('base','Base'),('worst','Worst'),('best','Best')]:
            fig_pac.add_trace(go.Scatter(
                x=sc_pac[sc]['anno'], y=sc_pac[sc]['valore'],
                name=label, line=dict(color=SC_COLORS[sc],width=2,dash=SC_DASH[sc])
            ))
        fig_pac.add_trace(go.Scatter(x=sc_pac['base']['anno'], y=sc_pac['base']['versato'],
                                      name='Versato', line=dict(color=COLORS['grigio'],width=1,dash='longdash')))
        if variabile:
            fasi = [{'importo': imp,'mesi':7},
                    {'importo': config['allocazione']['pac_persona1_con_nido'],'mesi':9999}]
            df_v = simula_pac_variabile(fasi, anni, rend/100)
            fig_pac.add_trace(go.Scatter(x=df_v['anno'], y=df_v['valore'],
                                          name='Con riduzione nido',
                                          line=dict(color=COLORS['arancio'],width=2,dash='dot')))
        fig_pac.update_layout(height=400, xaxis_title="Anni", yaxis_title="€",
                               legend=dict(orientation="h",y=1.08))
        st.plotly_chart(fig_pac, use_container_width=True)

        c1,c2,c3 = st.columns(3)
        c1.metric("Base",  f"€ {sc_pac['base']['valore'].iloc[-1]:,.0f}")
        c2.metric("Worst", f"€ {sc_pac['worst']['valore'].iloc[-1]:,.0f}")
        c3.metric("Best",  f"€ {sc_pac['best']['valore'].iloc[-1]:,.0f}")

    with tab2:
        st.subheader("Migrazione fondi → ETF")
        c1,c2 = st.columns(2)
        with c1:
            rimb = st.slider("Rimborso annuale (€)",10000,50000,22000,step=1000)
            re   = st.slider("Rendimento ETF (%)",4.0,12.0,7.0,step=0.5)
        with c2:
            rf   = st.slider("Rendimento fondi lordo (%)",2.0,8.0,4.0,step=0.5)
            cf   = st.slider("Costo annuo fondi (%)",0.5,3.0,2.0,step=0.1)
        df_mig = simula_migrazione_fondi(config,rimb,re/100,rf/100,cf/100)
        fig_mig = go.Figure()
        fig_mig.add_trace(go.Scatter(x=df_mig['anno'],y=df_mig['scenario_etf'],
                                      name='Migrazione ETF',line=dict(color=COLORS['verde'],width=2)))
        fig_mig.add_trace(go.Scatter(x=df_mig['anno'],y=df_mig['scenario_fondi'],
                                      name='Resto nei fondi',line=dict(color=COLORS['grigio'],width=2,dash='dash')))
        fig_mig.update_layout(height=340,xaxis_title="Anni",yaxis_title="€ netto")
        st.plotly_chart(fig_mig, use_container_width=True)

    with tab3:
        st.subheader("Scenario patrimoniale completo")
        c1,c2,c3 = st.columns(3)
        with c1: pd_s = st.slider(f"PAC {_N1} (€)",500,2000,config['allocazione']['pac_persona1_ora'],step=100)
        with c2: pf_s = st.slider(f"PAC {_NF} (€)",100,500,config['allocazione']['pac_flor'],step=50)
        with c3: rs   = st.slider("Rendimento (%)",4.0,10.0,7.0,step=0.5)
        anni_s  = st.slider("Orizzonte (anni)",5,25,18)
        tutti_sc= st.checkbox("Mostra 3 scenari (3%/7%/10%)")

        if tutti_sc:
            sc = confronta_scenari(config)
            fig_sc = go.Figure()
            pal_sc = {'Conservativo (3%)': COLORS['arancio'],
                       'Base (7%)': COLORS['blu'], 'Ottimista (10%)': COLORS['verde']}
            for nome, df_sc_s in sc.items():
                df_sc_s = df_sc_s[df_sc_s['anno']<=anni_s]
                fig_sc.add_trace(go.Scatter(x=df_sc_s['anno'],y=df_sc_s['totale'],
                                             name=nome,line=dict(color=pal_sc[nome],width=2)))
        else:
            df_sc = simula_scenario_completo(config,pd_s,pf_s,rs/100,anni_s)
            fig_sc = go.Figure()
            for col,nome,col_c in [('etf_persona1',f'ETF {_N1}',COLORS['blu']),
                                     ('etf_flor',f'ETF {_NF}',COLORS['azzurro']),
                                     ('fondi','Fondi bancari',COLORS['arancio']),
                                     ('generali','Generali',COLORS['rosso'])]:
                fig_sc.add_trace(go.Scatter(x=df_sc['anno'],y=df_sc[col],
                                             name=nome,stackgroup='one',line=dict(color=col_c)))
        fig_sc.update_layout(height=400,xaxis_title="Anni",yaxis_title="€",
                              legend=dict(orientation="h",y=1.08))
        st.plotly_chart(fig_sc, use_container_width=True)


# ─────────────────────────────────────────────────────────────
# SEZIONE 6: FIGLIO/A TIMELINE
# ─────────────────────────────────────────────────────────────
elif sezione == _SEZIONE_FIGLIO:
    st.title(f"{_NF} timeline")
    c1,c2 = st.columns(2)
    with c1: pac_f = st.slider(f"PAC mensile {_NF} (€)",100,500,config['allocazione']['pac_flor'],step=50)
    with c2: rf    = st.slider("Rendimento base (%)",4.0,10.0,7.0,step=0.5)

    sc_flor = simula_pac_scenari(pac_f,18,rend_base=rf/100,
                                  rend_worst=max(rf/100-0.04,0.01),rend_best=rf/100+0.03)
    df_costi = simula_costi_flor(config, eta_max=22)

    fig_f = go.Figure()
    anni_arr = sc_flor['base']['anno'].tolist()
    fig_f.add_trace(go.Scatter(
        x=anni_arr+anni_arr[::-1],
        y=sc_flor['best']['valore'].tolist()+sc_flor['worst']['valore'].tolist()[::-1],
        fill='toself', fillcolor='rgba(27,175,122,0.08)',
        line=dict(color='rgba(0,0,0,0)'), name='Range worst-best'
    ))
    for sc_key,label in [('base','Base'),('worst','Worst'),('best','Best')]:
        fig_f.add_trace(go.Scatter(
            x=sc_flor[sc_key]['anno'], y=sc_flor[sc_key]['valore'],
            name=label, line=dict(color=SC_COLORS[sc_key],width=2,dash=SC_DASH[sc_key])
        ))
    fig_f.add_trace(go.Scatter(x=sc_flor['base']['anno'], y=sc_flor['base']['versato'],
                                name='Versato', line=dict(color=COLORS['grigio'],width=1,dash='longdash')))
    for eta, lbl in {3:"Nido",6:"Elementari",11:"Medie",14:"Liceo",18:"18 anni"}.items():
        fig_f.add_vline(x=eta,line_dash="dot",line_color="#ccc",opacity=0.6)
        fig_f.add_annotation(x=eta,y=sc_flor['best']['valore'].iloc[-1]*0.85,
                               text=lbl,showarrow=False,font=dict(size=10,color="#888"))
    fig_f.update_layout(title=f"Fondo {_NF} — €{pac_f}/mese · 18 anni",
                         xaxis_title=f"Età {_NF}",yaxis_title="€",height=420,
                         legend=dict(orientation="h",y=1.08))
    st.plotly_chart(fig_f, use_container_width=True)

    c1,c2,c3 = st.columns(3)
    c1.metric("Worst (18 anni)", f"€ {sc_flor['worst']['valore'].iloc[-1]:,.0f}")
    c2.metric("Base (18 anni)",  f"€ {sc_flor['base']['valore'].iloc[-1]:,.0f}")
    c3.metric("Best (18 anni)",  f"€ {sc_flor['best']['valore'].iloc[-1]:,.0f}")

    st.subheader("Costi per fascia d'età")
    fig_c = px.bar(df_costi,x='eta',y='costo_annuale',color='voce',
                    labels={'eta':'Età','costo_annuale':'€/anno','voce':'Fase'},height=300)
    st.plotly_chart(fig_c, use_container_width=True)

    df_tab = sc_flor['base'][sc_flor['base']['anno'].apply(lambda x: x==int(x))].copy()
    df_tab['eta'] = df_tab['anno'].astype(int)
    df_tab = df_tab.merge(df_costi[['eta','voce','costo_annuale']],on='eta',how='left')
    show = df_tab[['eta','valore','versato','voce','costo_annuale']].copy()
    show.columns = ['Età','Fondo base (€)','Versato (€)','Fase','Costo annuale (€)']
    for c in ['Fondo base (€)','Versato (€)','Costo annuale (€)']:
        show[c] = show[c].map(lambda x: f"€ {x:,.0f}")
    st.dataframe(show,use_container_width=True,hide_index=True)
