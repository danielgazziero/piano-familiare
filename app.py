"""
Piano Finanziario Familiare — App Streamlit
Avviare con: streamlit run app.py
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import plotly.io as pio
from pathlib import Path
from datetime import date, datetime, timedelta
import time
import sys
import html as _html

sys.path.insert(0, str(Path(__file__).parent / 'src'))

from parser import load_config, parse_all_inputs
from portfolio import (get_portfolio_performance, get_etf_data,
                        get_fondi_snapshot, simula_uscita_fondo_data_x, piano_uscita_ottimale,
                        get_azioni_snapshot, patrimonio_snapshot)
from simulator import (simula_pac, simula_pac_variabile, simula_pac_scenari,
                        simula_fondo_scenari, simula_portafoglio_fondi_scenari,
                        simula_portafoglio_etf_scenari, simula_migrazione_fondi,
                        simula_costi_figlio, simula_scenario_completo, confronta_scenari,
                        calcola_fire_metrics)
from app_state import (init_db_connection, carica_params_persistenti,
                        salva_params_persistenti, carica_quote_fondi_persistenti,
                        auto_save_snapshot, importa_transazioni_xls,
                        carica_transazioni_db, get_storico_patrimonio,
                        get_storico_fondo, aggiorna_quote_fondi,
                        esegui_backfill_avvio, get_storico_portafoglio,
                        get_storico_asset, aggiorna_quantita_asset,
                        get_eventi_portafoglio, get_asset_catalog,
                        salva_asset, rimuovi_asset)

st.set_page_config(page_title="Piano Finanziario Familiare",
                   page_icon="📊", layout="wide",
                   initial_sidebar_state="expanded")

_BG      = '#0E1117'
_BG2     = '#262730'
_BG3     = '#1e1f28'
_TEXT    = '#FAFAFA'
_BORDER  = 'rgba(255,255,255,0.12)'
_GRID    = 'rgba(255,255,255,0.07)'

def _plotly_chart(fig, _title=None, **kwargs):
    """Wrapper st.plotly_chart: bgcolor + titolo come elemento Streamlit separato.
    Passa _title esplicitamente per garantire che il titolo sia sempre quello corrente;
    se non passato, lo legge dal figure via to_dict() come fallback.
    """
    dark = st.session_state.get('_dark_mode_toggle', True)
    # Template per-figura: pio.templates.default è globale di processo (condiviso tra sessioni)
    fig.update_layout(template='_app_dark' if dark else 'plotly_white')
    if dark:
        fig.update_layout(paper_bgcolor=_BG3, plot_bgcolor=_BG2, font_color=_TEXT)
    else:
        fig.update_layout(paper_bgcolor='white', plot_bgcolor='white', font_color='#31333F')

    if _title is None:
        _title = fig.layout.title.text or None

    if _title:
        fig.update_layout(title_text='', margin=dict(t=20))
        st.markdown(
            f"<div style='text-align:center;font-weight:600;font-size:1em;"
            f"padding-bottom:2px'>{_html.escape(_title)}</div>",
            unsafe_allow_html=True,
        )

    kwargs['theme'] = None
    st.plotly_chart(fig, **kwargs)


def _has(v) -> bool:
    """True se v è un numero valido (né None né NaN; NaN è truthy in Python)."""
    return v is not None and not pd.isna(v)


def _nn(v):
    """NaN/NA -> None (NaN non è JSON valido per Supabase); numpy -> tipi Python."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        return v
    return v.item() if hasattr(v, 'item') else v


_ASSET_COLS = ('isin', 'nome', 'tipo', 'ticker_yf', 'ticker_bi', 'ter', 'proprietario',
               'stato', 'valore_quota_ref', 'data_ref', 'valore_iniziale', 'note')


def _asset_record(row: dict, **override) -> dict:
    """Record asset_catalog pulito da una riga del catalogo (solo colonne note, no NaN)."""
    rec = {k: _nn(row.get(k)) for k in _ASSET_COLS}
    fb = row.get('fallback_tickers')
    rec['fallback_tickers'] = fb if isinstance(fb, list) else []
    rec.update(override)
    return rec


def _nomi_effettivi(nomi) -> list:
    """Nomi da oscurare: quelli configurati in Privacy + i nomi di famiglia dai parametri."""
    _p = st.session_state.get('params', {})
    _gen = {'persona 1', 'persona 2', 'figlio', 'figlio/a', ''}
    return list(dict.fromkeys(
        list(nomi) + [str(_p.get(k) or '').strip()
                      for k in ('nome_persona1', 'nome_persona2', 'nome_figlio')
                      if str(_p.get(k) or '').strip().lower() not in _gen]))


def _init_plotly_template():
    """Imposta il template Plotly globale in base al tema corrente. Skip se tema invariato."""
    dark = st.session_state.get('_dark_mode_toggle', True)
    if st.session_state.get('_plotly_tpl_dark') == dark:
        return
    pio.templates['_app_dark'] = go.layout.Template(layout=go.Layout(
        paper_bgcolor=_BG3, plot_bgcolor=_BG2,
        font=dict(color=_TEXT, size=12),
        xaxis=dict(gridcolor=_GRID, linecolor=_BORDER, zerolinecolor=_BORDER,
                   tickfont=dict(color=_TEXT)),
        yaxis=dict(gridcolor=_GRID, linecolor=_BORDER, zerolinecolor=_BORDER,
                   tickfont=dict(color=_TEXT)),
        legend=dict(bgcolor='rgba(30,31,40,0.85)', bordercolor=_BORDER,
                    font=dict(color=_TEXT)),
        hoverlabel=dict(bgcolor=_BG3, bordercolor=_BORDER, font=dict(color=_TEXT)),
        colorway=['#1F5C8B','#1baf7a','#E9C46A','#E63946','#A8DADC','#7B2D8B','#2EC4B6','#FF9F1C'],
    ))
    st.session_state['_plotly_tpl_dark'] = dark


def _inject_css():
    """
    Inietta CSS di base sempre (scrollbar stabile, transizioni smooth),
    più overrides light mode quando il toggle è disattivato.
    """
    dark = st.session_state.get('_dark_mode_toggle', True)

    # Sempre: scrollbar-gutter:stable evita lo spostamento del contenuto quando
    # compare/scompare la scrollbar al cambio di tema o al caricamento di nuovi elementi.
    # Le transizioni rendono il cambio colore meno brusco.
    st.markdown("""<style>
html { scrollbar-gutter: stable; overflow-y: scroll; }
[data-testid="stApp"],
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
[data-testid="stMainBlockContainer"],
[data-testid="stSidebar"],
[data-testid="stHeader"] {
    transition: background-color 0.15s ease, color 0.15s ease;
}
</style>""", unsafe_allow_html=True)

    if not dark:
        st.markdown("""<style>
body {background-color:#FFFFFF!important;color:#31333F!important}
[data-testid="stApp"] {background:#FFFFFF!important;color:#31333F!important}
[data-testid="stAppViewContainer"] {background-color:#FFFFFF!important}
[data-testid="stHeader"] {background-color:#FFFFFF!important}
[data-testid="stMain"] {background-color:#FFFFFF!important}
[data-testid="stMainBlockContainer"] {background-color:#FFFFFF!important}
[data-testid="stSidebar"] {background-color:#F0F2F6!important;color:#31333F!important}
[data-testid="stSidebar"] * {color:#31333F!important}
[data-testid="stMetricValue"] {color:#31333F!important}
[data-testid="stMetricLabel"] {color:rgba(49,51,63,.65)!important}
[data-testid="stMetricDelta"] {color:#1F5C8B!important}
[data-testid="metric-container"] {background-color:#F0F2F6!important}
[data-testid="stExpander"] {background-color:#F0F2F6!important}
[data-testid="stAlert"] {background-color:#F0F2F6!important;color:#31333F!important}
[data-testid="stDataFrame"] {background-color:#FFFFFF!important}
input,textarea {background-color:#FFFFFF!important;color:#31333F!important}
[data-baseweb="select"]>div {background-color:#FFFFFF!important;color:#31333F!important}
[data-baseweb="popover"],[data-baseweb="menu"],[role="listbox"] {background-color:#FFFFFF!important;color:#31333F!important}
[data-baseweb="tab-list"] {background-color:#FFFFFF!important}
[data-baseweb="tab"] {color:rgba(49,51,63,.6)!important}
[aria-selected="true"][data-baseweb="tab"] {color:#31333F!important}
.stButton>button {background-color:#F0F2F6!important;color:#31333F!important}
small,[data-testid="stCaptionContainer"] {color:rgba(49,51,63,.5)!important}
hr {border-color:rgba(49,51,63,0.2)!important}
</style>""", unsafe_allow_html=True)

# ── DEMO MODE — controllato da DEMO_MODE in st.secrets (false in produzione)
_DEMO = bool(st.secrets.get("DEMO_MODE", False))

# ── AUTENTICAZIONE ────────────────────────────────────────────

def _auth_genera_token():
    """Genera token casuale (raw URL-safe) + hash SHA-256 per DB."""
    import secrets as _sec, hashlib as _hl
    raw = _sec.token_urlsafe(32)
    return raw, _hl.sha256(raw.encode()).hexdigest()


def _auth_handle_reset(token_raw: str):
    """Flow reset password — attivato da ?reset_token= in URL."""
    import hashlib as _hl
    from database import verifica_token as _vt, aggiorna_password_utente as _apw, hash_password as _hp
    tok_hash = _hl.sha256(token_raw.encode()).hexdigest()
    user = _vt(tok_hash, 'reset_token_hash', 'reset_token_expiry')
    if not user:
        st.error("Link non valido o scaduto. Richiedi un nuovo reset dalla schermata di login.")
        if st.button("← Vai al login"):
            st.query_params.clear()
            st.rerun()
        st.stop()
    st.title("🔑 Nuova password")
    st.caption(f"Account: **{user['username']}**")
    p1 = st.text_input("Nuova password", type="password", max_chars=1024)
    p2 = st.text_input("Conferma password", type="password", max_chars=1024)
    if st.button("Salva password", type="primary"):
        if not p1:
            st.error("Inserisci una password.")
        elif len(p1) < 8:
            st.error("Minimo 8 caratteri.")
        elif p1 != p2:
            st.error("Le password non corrispondono.")
        else:
            if _apw(user['id'], _hp(p1)):
                from database import (reset_server_login_state as _rsls,
                                      invalida_sessioni as _inv_s)
                _rsls(user['username'])
                _inv_s(user['id'])   # chiude le sessioni gia' aperte con la vecchia password
                st.success("Password aggiornata! Accedi con le tue nuove credenziali.")
                st.query_params.clear()
                if st.button("Vai al login"):
                    st.rerun()
            else:
                st.error("Errore durante l'aggiornamento. Riprova.")
    st.stop()


def _auth_handle_invite(token_raw: str):
    """Flow attivazione invito — attivato da ?invite_token= in URL."""
    import hashlib as _hl
    from database import verifica_token as _vt, attiva_utente_invito as _aui, hash_password as _hp
    tok_hash = _hl.sha256(token_raw.encode()).hexdigest()
    user = _vt(tok_hash, 'invite_token_hash', 'invite_token_expiry')
    if not user:
        st.error("Link non valido o scaduto. Chiedi all'amministratore un nuovo invito.")
        if st.button("← Vai al login"):
            st.query_params.clear()
            st.rerun()
        st.stop()
    st.title("👋 Attiva il tuo account")
    st.caption(f"Username: **{user['username']}** · Email: {user['email']}")
    p1 = st.text_input("Scegli una password", type="password", max_chars=1024)
    p2 = st.text_input("Conferma password", type="password", max_chars=1024)
    if st.button("Attiva account", type="primary"):
        if not p1:
            st.error("Inserisci una password.")
        elif len(p1) < 8:
            st.error("Minimo 8 caratteri.")
        elif p1 != p2:
            st.error("Le password non corrispondono.")
        else:
            if _aui(user['id'], _hp(p1)):
                st.success("Account attivato! Accedi con username e password.")
                st.query_params.clear()
                if st.button("Vai al login"):
                    st.rerun()
            else:
                st.error("Errore durante l'attivazione. Riprova.")
    st.stop()


def _auth_wizard():
    """Wizard primo avvio — crea l'account amministratore."""
    from database import crea_utente as _cu, hash_password as _hp, carica_param as _cp
    st.title("🏠 Benvenuto — Primo avvio")
    st.info("Nessun account trovato. Crea l'account amministratore per iniziare.")
    _legacy_hash = None
    try:
        _legacy_hash = _cp("app_password")
    except Exception:
        pass
    col1, col2 = st.columns(2)
    with col1:
        wiz_user  = st.text_input("Username (min. 3 caratteri)", max_chars=50)
        wiz_email = st.text_input("Email", max_chars=200)
    with col2:
        wiz_p1 = st.text_input("Password", type="password", max_chars=1024)
        wiz_p2 = st.text_input("Conferma password", type="password", max_chars=1024)
    _setup_code = str(st.secrets.get("SETUP_CODE", "") or "")
    if _setup_code:
        wiz_setup = st.text_input("Codice di setup (da secrets.toml → SETUP_CODE)",
                                  type="password", max_chars=200)
    else:
        wiz_setup = ""
        st.warning("⚠️ `SETUP_CODE` non configurato in secrets.toml: chiunque raggiunga questa "
                   "pagina può creare l'amministratore. Configuralo e riavvia.")
    usa_legacy = False
    if _legacy_hash:
        st.caption("💡 Rilevata una password esistente in config_params.")
        usa_legacy = st.checkbox("Importa la password attuale (raccomandato)", value=True)
    if st.button("✅ Crea account amministratore", type="primary"):
        errs = []
        if _setup_code:
            import hmac as _hmac_s
            from database import (get_server_login_state as _gsl_s,
                                  set_server_login_state as _ssl_s)
            _ss_state = _gsl_s("__setup__")
            if time.time() < _ss_state.get("locked_until", 0.0):
                errs.append("Troppi tentativi sul codice di setup. Riprova più tardi.")
            elif not _hmac_s.compare_digest(wiz_setup.encode(), _setup_code.encode()):
                _n_s = min(_ss_state.get("n", 0) + 1, _AUTH_MAX_FAILURES)
                _d_s = _AUTH_LOCKOUT_DELAYS[_n_s]
                _ssl_s("__setup__", _n_s, time.time() + _d_s if _d_s > 0 else 0.0)
                errs.append("Codice di setup errato.")
        if not wiz_user or len(wiz_user.strip()) < 3:
            errs.append("Username: almeno 3 caratteri.")
        if not wiz_email or '@' not in wiz_email:
            errs.append("Email non valida.")
        if not usa_legacy:
            if not wiz_p1:
                errs.append("Inserisci una password.")
            elif len(wiz_p1) < 8:
                errs.append("Password: almeno 8 caratteri.")
            elif wiz_p1 != wiz_p2:
                errs.append("Le password non corrispondono.")
        for _e in errs:
            st.error(_e)
        if not errs:
            try:
                if usa_legacy and _legacy_hash:
                    # Se già PBKDF2 lo usiamo direttamente; altrimenti lo hashiamo subito
                    # per non salvare plaintext in app_users.
                    pwd_hash = _legacy_hash if _legacy_hash.startswith('pbkdf2$') else _hp(_legacy_hash)
                else:
                    pwd_hash = _hp(wiz_p1)
                user = _cu(wiz_user.strip(), wiz_email.strip(), pwd_hash, is_admin=True)
                st.session_state.update({
                    "_auth_ok": True, "_auth_ts": time.time(),
                    "_auth_user_id": user['id'],
                    "_auth_username": user['username'],
                    "_auth_is_admin": True,
                    "_auth_active_ts": time.time(),
                })
                st.success(f"Account **{user['username']}** creato. Benvenuto!")
                st.rerun()
            except Exception as _ex:
                _ex_msg = (getattr(_ex, 'message', None) or str(_ex)).lower()
                if 'duplicate' in _ex_msg or 'unique' in _ex_msg:
                    st.error("Username o email già in uso.")
                else:
                    st.error(f"Errore nella creazione: {type(_ex).__name__}")
    st.stop()


def _auth_forgot_password():
    """Schermata 'Password dimenticata'."""
    import secrets as _sec
    from database import (get_utente_by_email as _gube, imposta_reset_token as _irt,
                          reset_email_consentito as _rate_ok)
    from auth_mail import invia_email_reset as _ier, smtp_configurato as _sc
    st.title("🔑 Reset password")
    if not _sc():
        st.warning(
            "SMTP non configurato — impossibile inviare email di reset. "
            "Configura SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD in secrets.toml "
            "oppure contatta l'amministratore."
        )
    else:
        email_input = st.text_input("La tua email", max_chars=200)
        if st.button("Invia link di reset", type="primary"):
            if not email_input or '@' not in email_input:
                st.error("Inserisci un'email valida.")
            elif time.time() < st.session_state.get("_reset_req_until", 0):
                st.warning("Attendi un minuto prima di richiedere un altro link.")
            else:
                st.session_state["_reset_req_until"] = time.time() + 60
                _t0 = time.time()
                user = _gube(email_input)
                if user and _rate_ok(email_input):
                    raw, tok_hash = _auth_genera_token()
                    expiry = (datetime.now() + timedelta(minutes=15)).isoformat()
                    if _irt(user['id'], tok_hash, expiry):
                        try:
                            base_url = st.secrets.get("APP_BASE_URL", "http://localhost:8501")
                            _ier(email_input, raw, base_url)
                        except Exception as _e:
                            st.error(f"Errore invio email: {type(_e).__name__}. Controlla la config SMTP.")
                            st.stop()
                # tempo di risposta costante: non rivela se l'email e' registrata
                time.sleep(max(0.0, 2.0 - (time.time() - _t0)))
                st.success("Se l'email è registrata riceverai un link entro pochi minuti.")
    if st.button("← Torna al login"):
        st.session_state.pop("_auth_view", None)
        st.rerun()
    st.stop()


_AUTH_LOCKOUT_DELAYS = [0, 0, 0, 5, 15, 30, 60, 120, 300]
_AUTH_MAX_FAILURES   = len(_AUTH_LOCKOUT_DELAYS) - 1


def _auth_login():
    """Schermata di login username + password con brute-force guard."""
    from database import verifica_credenziali as _vc
    from database import (get_server_login_state as _gsls,
                          set_server_login_state as _ssls,
                          reset_server_login_state as _rsls)
    st.title("🔒 Accesso protetto")
    _notice = st.session_state.pop("_auth_notice", None)
    if _notice:
        st.warning(_notice)
    _failures       = st.session_state.get("_auth_failures", 0)
    _locked_until   = st.session_state.get("_auth_locked_until", 0)
    if time.time() < _locked_until:
        _wait = int(_locked_until - time.time()) + 1
        st.error(f"Troppi tentativi falliti. Riprova tra {_wait} secondi.")
        st.stop()
    username = st.text_input("Username", max_chars=50)
    password = st.text_input("Password", type="password", max_chars=1024)
    _c1, _c2 = st.columns([1, 2])
    with _c1:
        login_btn = st.button("Accedi", type="primary")
    with _c2:
        if st.button("🔑 Password dimenticata?"):
            st.session_state["_auth_view"] = "forgot_password"
            st.rerun()
    if login_btn:
        if not username or not password:
            st.error("Inserisci username e password.")
        else:
            # Controlla lockout server-side (persiste tra sessioni/browser diversi)
            _srv = _gsls(username)
            if time.time() < _srv.get("locked_until", 0.0):
                _wait = int(_srv["locked_until"] - time.time()) + 1
                st.error(f"Account temporaneamente bloccato. Riprova tra {_wait} secondi.")
                st.stop()
            user = _vc(username, password)
            if user:
                _rsls(username)
                st.session_state["_auth_failures"] = 0
                st.session_state.pop("_auth_locked_until", None)
                if user.get('totp_enabled'):
                    st.session_state["_auth_pending_uid"]      = user['id']
                    st.session_state["_auth_pending_username"] = user['username']
                    st.session_state["_auth_pending_is_admin"] = user.get('is_admin', False)
                    st.session_state["_auth_view"] = "totp_verify"
                else:
                    st.session_state.update({
                        "_auth_ok": True, "_auth_ts": time.time(),
                        "_auth_user_id": user['id'],
                        "_auth_username": user['username'],
                        "_auth_is_admin": user.get('is_admin', False),
                        "_auth_active_ts": time.time(),
                    })
                    st.session_state.pop("_auth_view", None)
                st.rerun()
            else:
                _n_srv = min(_srv.get("n", 0) + 1, _AUTH_MAX_FAILURES)
                _delay_srv = _AUTH_LOCKOUT_DELAYS[_n_srv]
                _lu_srv = time.time() + _delay_srv if _delay_srv > 0 else 0.0
                _ssls(username, _n_srv, _lu_srv)
                _failures = min(_failures + 1, _AUTH_MAX_FAILURES)
                st.session_state["_auth_failures"] = _failures
                _delay = _AUTH_LOCKOUT_DELAYS[_failures]
                if _delay > 0:
                    st.session_state["_auth_locked_until"] = time.time() + _delay
                    st.error(f"Credenziali errate. Attendi {_delay} s prima del prossimo tentativo.")
                else:
                    st.error("Credenziali non valide.")
    st.stop()


def _auth_totp_verify():
    """Secondo step login — verifica codice TOTP."""
    import pyotp as _pyotp
    from database import get_totp_info as _gti
    from database import (get_server_totp_state as _gsts,
                          set_server_totp_state as _ssts,
                          reset_server_totp_state as _rsts)
    _user_id  = st.session_state.get("_auth_pending_uid")
    _username = st.session_state.get("_auth_pending_username", "")
    _is_admin = st.session_state.get("_auth_pending_is_admin", False)
    if not _user_id:
        st.session_state.pop("_auth_view", None)
        st.rerun()

    # Contatori TOTP separati per user_id — persistono anche se l'utente
    # torna al login e ri-effettua il login con password corretta.
    _totp_fail_key  = f"_totp_failures_{_user_id}"
    _totp_lock_key  = f"_totp_locked_until_{_user_id}"
    _failures       = st.session_state.get(_totp_fail_key, 0)
    _locked_until   = st.session_state.get(_totp_lock_key, 0)
    _srv_totp       = _gsts(_user_id)
    _locked_until   = max(_locked_until, _srv_totp.get("locked_until", 0.0))
    if time.time() < _locked_until:
        _wait = int(_locked_until - time.time()) + 1
        st.error(f"Troppi tentativi falliti. Riprova tra {_wait} secondi.")
        st.stop()

    st.title("🔐 Verifica in due passaggi")
    st.caption(f"Account: **{_username}** — Inserisci il codice dall'app authenticator.")
    totp_code = st.text_input("Codice 6 cifre", max_chars=6, placeholder="000000")
    _c1, _c2 = st.columns([1, 2])
    with _c1:
        verify_btn = st.button("Verifica", type="primary")
    with _c2:
        if st.button("← Torna al login"):
            # Rimuove solo le chiavi del flusso pending — i contatori TOTP
            # per user_id rimangono, così il lockout non si azzera tornando indietro.
            for _k in ("_auth_pending_uid", "_auth_pending_username",
                       "_auth_pending_is_admin", "_auth_view",
                       "_auth_failures", "_auth_locked_until"):
                st.session_state.pop(_k, None)
            st.rerun()
    if verify_btn:
        if not totp_code:
            st.error("Inserisci il codice.")
        else:
            from database import totp_code_valido as _tcv
            _info = _gti(_user_id)
            _secret = _info.get('totp_secret')
            if _secret and _tcv(_user_id, _secret, totp_code):
                _rsts(_user_id)
                for _k in ("_auth_pending_uid", "_auth_pending_username",
                           "_auth_pending_is_admin", "_auth_failures",
                           "_auth_locked_until", "_auth_view",
                           _totp_fail_key, _totp_lock_key):
                    st.session_state.pop(_k, None)
                st.session_state.update({
                    "_auth_ok": True, "_auth_ts": time.time(),
                    "_auth_user_id": _user_id,
                    "_auth_username": _username,
                    "_auth_is_admin": _is_admin,
                    "_auth_active_ts": time.time(),
                })
                st.rerun()
            else:
                _n_srv_totp  = min(_srv_totp.get("n", 0) + 1, _AUTH_MAX_FAILURES)
                _delay_srv_t = _AUTH_LOCKOUT_DELAYS[_n_srv_totp]
                _lu_srv_t    = time.time() + _delay_srv_t if _delay_srv_t > 0 else 0.0
                _ssts(_user_id, _n_srv_totp, _lu_srv_t)
                _failures = min(_failures + 1, _AUTH_MAX_FAILURES)
                st.session_state[_totp_fail_key] = _failures
                _delay = _AUTH_LOCKOUT_DELAYS[_failures]
                if _delay > 0:
                    st.session_state[_totp_lock_key] = time.time() + _delay
                    st.error(f"Codice non valido. Attendi {_delay} s prima del prossimo tentativo.")
                else:
                    st.error("Codice non valido.")
    st.stop()


# ── Gate di autenticazione principale ────────────────────────

_reset_token  = st.query_params.get("reset_token")
_invite_token = st.query_params.get("invite_token")

if _reset_token:
    _auth_handle_reset(_reset_token)
elif _invite_token:
    _auth_handle_invite(_invite_token)
else:
    if st.session_state.get("_users_exist"):
        _n_users = 1   # evita una query Supabase ad ogni rerun
    else:
        try:
            from database import conta_utenti as _conta_utenti
            _n_users = _conta_utenti()
        except Exception:
            st.error("⚠️ DB non raggiungibile — impossibile verificare le credenziali. Riprova tra qualche istante.")
            st.stop()
        if _n_users > 0:
            st.session_state["_users_exist"] = True

    if _n_users == 0:
        _auth_wizard()
    elif not st.session_state.get("_auth_ok"):
        _view = st.session_state.get("_auth_view", "login")
        if _view == "forgot_password":
            _auth_forgot_password()
        elif _view == "totp_verify":
            _auth_totp_verify()
        else:
            _auth_login()
    elif time.time() - st.session_state.get("_auth_ts", 0) > 3600:
        for _k in ("_auth_ok", "_auth_ts", "_auth_user_id", "_auth_username", "_auth_is_admin",
                   "_auth_active_ts"):
            st.session_state.pop(_k, None)
        st.session_state["_auth_notice"] = "Sessione scaduta. Effettua nuovamente l'accesso."
        st.rerun()
    else:
        # S-NEW-07: rivalidazione is_active ogni 5 minuti — senza query ad ogni rerun
        if time.time() - st.session_state.get("_auth_active_ts", 0) > 300:
            from database import is_utente_attivo as _is_active
            from database import get_session_epoch as _get_epoch
            _uid_rv = st.session_state.get("_auth_user_id")
            _epoch_ko = _get_epoch(_uid_rv) > max(st.session_state.get("_auth_ts", 0),
                                                  st.session_state.get("_auth_epoch_seen", 0))
            if _epoch_ko or not _is_active(_uid_rv):
                for _k in ("_auth_ok", "_auth_ts", "_auth_user_id", "_auth_username", "_auth_is_admin",
                           "_auth_active_ts"):
                    st.session_state.pop(_k, None)
                st.session_state["_auth_notice"] = ("Sessione terminata: password modificata o "
                                                    "accesso revocato.")
                st.rerun()
            st.session_state["_auth_active_ts"] = time.time()

if _DEMO:
    from demo_data import (
        demo_init_db_connection             as init_db_connection,
        demo_carica_params_persistenti      as carica_params_persistenti,
        demo_salva_params_persistenti       as salva_params_persistenti,
        demo_carica_quote_fondi_persistenti as carica_quote_fondi_persistenti,
        demo_auto_save_snapshot             as auto_save_snapshot,
        demo_importa_transazioni_xls        as importa_transazioni_xls,
        demo_carica_transazioni_db          as carica_transazioni_db,
        demo_get_storico_patrimonio         as get_storico_patrimonio,
        demo_get_storico_fondo              as get_storico_fondo,
        demo_aggiorna_quote_fondi           as aggiorna_quote_fondi,
        demo_esegui_backfill_avvio          as esegui_backfill_avvio,
        demo_get_storico_portafoglio        as get_storico_portafoglio,
        demo_get_storico_asset              as get_storico_asset,
        demo_aggiorna_quantita_asset        as aggiorna_quantita_asset,
        demo_get_eventi_portafoglio         as get_eventi_portafoglio,
        demo_get_asset_catalog              as get_asset_catalog,
        demo_salva_asset                    as salva_asset,
        demo_rimuovi_asset                  as rimuovi_asset,
        demo_get_etf_data                   as get_etf_data,
    )

_init_plotly_template()
_inject_css()

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

config = get_config()

# Connessione DB (una volta per sessione)
db_ok = init_db_connection()

# ── ONBOARDING WIZARD ────────────────────────────────────────
# Mostrato solo in modalità non-DEMO, finché wizard_completed non è 'true' in config_params.
# Per rieseguire: admin preme il pulsante in sidebar → _wizard_restart = True.
if not _DEMO:
    from wizard import run_wizard as _run_wizard
    if not _run_wizard(config, db_ok):
        st.stop()

# Carica parametri persistenti da Supabase (ultimi valori salvati)
if 'params' not in st.session_state:
    st.session_state['params'] = carica_params_persistenti(config)

_N1 = st.session_state['params'].get('nome_persona1', 'Persona 1')
_N2 = st.session_state['params'].get('nome_persona2', 'Persona 2')
_NF = st.session_state['params'].get('nome_figlio',   'Figlio/a')

# Carica catalogo asset dal DB (fonte di verità per fondi/ETF/azioni)
if 'asset_catalog' not in st.session_state:
    st.session_state['asset_catalog'] = get_asset_catalog() if db_ok else pd.DataFrame()

_catalog = st.session_state.get('asset_catalog', pd.DataFrame())

# Carica quote fondi persistenti
if 'quote_map' not in st.session_state:
    st.session_state['quote_map'] = carica_quote_fondi_persistenti(config)

def _get_posizioni():
    """Carica posizioni dal DB con caching in session_state (evita 2× chiamate per rerun)."""
    if 'pos_df_cache' not in st.session_state:
        if _DEMO:
            from demo_data import demo_carica_posizioni
            st.session_state['pos_df_cache'] = demo_carica_posizioni()
        else:
            from positions import carica_posizioni as _cp
            st.session_state['pos_df_cache'] = _cp()
    return st.session_state['pos_df_cache']


def get_fondi():
    if 'fondi_df_cache' not in st.session_state:
        if _DEMO:
            from demo_data import demo_get_fondi_snapshot
            st.session_state['fondi_df_cache'] = demo_get_fondi_snapshot()
        else:
            cat = st.session_state.get('asset_catalog', pd.DataFrame())
            fondi_data = None
            if not cat.empty and 'tipo' in cat.columns:
                fondi_cat = cat[cat['tipo'] == 'fondo'].copy()
                try:
                    pos_df = _get_posizioni()
                    if not pos_df.empty and 'isin' in pos_df.columns:
                        pos_fondi = pos_df[pos_df['isin'].isin(fondi_cat['isin'])][['isin', 'quantita']]
                        fondi_cat = fondi_cat.merge(pos_fondi, on='isin', how='left')
                        fondi_cat['quantita'] = fondi_cat['quantita'].fillna(0)
                    else:
                        fondi_cat['quantita'] = fondi_cat.get('quantita', pd.Series(0, index=fondi_cat.index))
                except Exception:
                    if 'quantita' not in fondi_cat.columns:
                        fondi_cat['quantita'] = 0
                fondi_list = fondi_cat.to_dict('records')
                qm = st.session_state.get('quote_map', {})
                for f in fondi_list:
                    if f['isin'] in qm:
                        f['quota_aggiornata'] = qm[f['isin']]
                if fondi_list:
                    fondi_data = fondi_list
            st.session_state['fondi_df_cache'] = get_fondi_snapshot(
                get_config(), st.session_state.get('quote_map', {}), fondi_data=fondi_data)
    return st.session_state['fondi_df_cache']


def get_etf_perf():
    if 'etf_perf_cache' not in st.session_state:
        if _DEMO:
            from demo_data import demo_get_etf_perf
            st.session_state['etf_perf_cache'] = demo_get_etf_perf()
        else:
            cat = st.session_state.get('asset_catalog', pd.DataFrame())
            st.session_state['etf_perf_cache'] = get_portfolio_performance(
                get_config(), catalog_df=cat if not cat.empty else None)
    return st.session_state['etf_perf_cache']


def get_azioni():
    if 'azioni_df_cache' not in st.session_state:
        if _DEMO:
            from demo_data import demo_get_azioni
            st.session_state['azioni_df_cache'] = demo_get_azioni()
        else:
            cat = st.session_state.get('asset_catalog', pd.DataFrame())
            if cat.empty or 'tipo' not in cat.columns:
                result = get_azioni_snapshot(get_config())
            else:
                az_cat = cat[cat['tipo'] == 'azione'].copy()
                if az_cat.empty:
                    result = get_azioni_snapshot(get_config())
                else:
                    if 'quantita' not in az_cat.columns:
                        try:
                            pos_df = _get_posizioni()
                            if not pos_df.empty and 'isin' in pos_df.columns:
                                pos_az = pos_df[pos_df['isin'].isin(az_cat['isin'])][['isin', 'quantita']]
                                az_cat = az_cat.merge(pos_az, on='isin', how='left')
                                az_cat['quantita'] = az_cat['quantita'].fillna(0)
                            else:
                                az_cat['quantita'] = 0
                        except Exception:
                            az_cat['quantita'] = 0
                    result = get_azioni_snapshot(get_config(), catalog_df=az_cat)
            st.session_state['azioni_df_cache'] = result
    return st.session_state['azioni_df_cache']

if 'xls_importati' not in st.session_state:
    st.session_state['xls_importati'] = False

def _run_xls_import() -> None:
    """Import XLS lazy — chiamata solo nelle sezioni che ne hanno bisogno."""
    if st.session_state['xls_importati'] or not db_ok:
        return
    try:
        df_tx_xls = parse_all_inputs(INPUT_DIR, config)
        if not df_tx_xls.empty:
            n_nuove = importa_transazioni_xls(df_tx_xls)
            if n_nuove > 0:
                st.session_state['nuove_tx'] = n_nuove
        st.session_state['xls_importati'] = True
    except Exception as e:
        st.warning(f"⚠️ Errore import XLS: {type(e).__name__}")

# Lazy loading: snapshot, auto-save, backfill e XLS import avvengono dentro le sezioni
# (prima sezione caricata dall'utente che richiede dati ETF/azioni live).
# Sezioni come "Fine mese" o "Gestione Asset" non fanno chiamate yfinance.

# ── FRAGMENT: grafico storico ETF ─────────────────────────────
@st.fragment
def _frag_storico_etf():
    """Reruns solo quando periodo o selezione ticker cambia — non tutta la pagina."""
    _etf = get_etf_perf()
    _tutti_t = {r['ticker']: r['ticker_yf'] for r in _etf.to_dict('records') if r.get('ticker_yf')}
    _tutti_t['ACN'] = 'ACN'
    periodo = st.select_slider("Periodo", ["1mo","3mo","6mo","ytd","1y","2y"], value="1y")
    sel = st.multiselect("Titoli da confrontare", list(_tutti_t.keys()),
                          default=list(_tutti_t.keys())[:3])
    if not sel:
        return
    fig = go.Figure()
    pal = list(COLORS.values())
    if _DEMO:
        for i, nome in enumerate(sel):
            _tk = _tutti_t.get(nome, nome)
            _h = get_etf_data(_tk, periodo)
            if _h.empty:
                continue
            first = _h['price'].iloc[0]
            _indexed = (_h['price'] / first * 100).round(2) if first and not pd.isna(first) else _h['price']
            fig.add_trace(go.Scatter(x=_h.index, y=_indexed,
                                      name=nome, line=dict(color=pal[i%len(pal)], width=2)))
    else:
        from portfolio import _batch_download, _extract_series
        _sel_tickers = tuple(_tutti_t[n] for n in sel if n in _tutti_t)
        _raw_batch   = _batch_download(_sel_tickers, periodo)
        for i, nome in enumerate(sel):
            _tk = _tutti_t.get(nome, nome)
            _series = _extract_series(_raw_batch, _tk, len(_sel_tickers))
            if _series.empty:
                continue
            first = _series.iloc[0]
            _indexed = (_series / first * 100).round(2) if first and not pd.isna(first) else _series
            fig.add_trace(go.Scatter(x=_series.index, y=_indexed,
                                      name=nome, line=dict(color=pal[i%len(pal)], width=2)))
    fig.add_hline(y=100, line_dash="dash", line_color="gray", opacity=0.4)
    fig.update_layout(height=400, legend=dict(orientation="h",y=1.08))
    _plotly_chart(fig, _title=f"Performance relativa (base 100) · {periodo}",
                  use_container_width=True, theme=None)


# ── FRAGMENT: confronto ETF candidati ─────────────────────────
@st.fragment
def _frag_confronto_candidati():
    """Reruns solo quando il periodo del confronto cambia."""
    _etf = get_etf_perf()
    _candidati = _etf[_etf['stato'] == 'candidato']
    if _candidati.empty:
        return
    st.subheader("Confronto ETF candidati")
    fig_c = go.Figure()
    per_c = st.select_slider("Periodo confronto", ["6mo","ytd","1y","2y"], value="1y", key="pc")
    _cat_confronto = st.session_state.get('asset_catalog', pd.DataFrame())
    if not _cat_confronto.empty and 'tipo' in _cat_confronto.columns:
        confronto = {str(r.get('ticker_bi', r['isin'])): str(r['ticker_yf'])
                     for r in _cat_confronto[_cat_confronto['tipo'] == 'etf'].to_dict('records')
                     if r.get('ticker_yf')}
    else:
        confronto = {e['ticker_bi']: e['ticker_yf']
                     for e in config.get('etf', []) if e.get('ticker_yf')}
    pal_c = [COLORS['rosso'],COLORS['verde'],COLORS['blu'],COLORS['arancio']]
    if confronto:
        from portfolio import _batch_download, _extract_series
        _tickers_c = tuple(confronto.values())
        _raw_c = _batch_download(_tickers_c, per_c)
        for i, (nome, tick) in enumerate(confronto.items()):
            _ser = _extract_series(_raw_c, tick, len(_tickers_c))
            if not _ser.empty:
                _first = _ser.iloc[0]
                _idx = (_ser / _first * 100).round(2) if _has(_first) and _first else _ser
                fig_c.add_trace(go.Scatter(x=_ser.index, y=_idx,
                                           name=nome, line=dict(color=pal_c[i % len(pal_c)], width=2)))
    fig_c.add_hline(y=100, line_dash="dash", line_color="gray", opacity=0.4)
    fig_c.update_layout(title="Confronto (base 100)", height=320,
                         legend=dict(orientation="h",y=1.08))
    _plotly_chart(fig_c, use_container_width=True, theme=None)
    st.info("💡 MWRD = alternativa Amundi a IWDA (stesso indice, TER 0.12%). "
            "EMAE = satellite emergenti asiatici — interessante al 10-15% del PAC "
            "se vuoi esposizione separata dall'ACWI del figlio/a.")


# ── FRAGMENT: simulatori scenari fondi ────────────────────────
@st.fragment
def _frag_fondi_scenari():
    """Reruns solo quando i slider cambiano — non tutta la pagina."""
    df_attivi_f   = st.session_state.get('_fondi_df_edit_attivi', pd.DataFrame())
    df_fondi_edit = st.session_state.get('_fondi_df_edit_full', pd.DataFrame())
    if df_attivi_f.empty:
        st.info("Nessun fondo selezionato nella tabella.")
        return

    _cfg      = get_config()
    _aliquota = _cfg.get('parametri', {}).get('aliquota_capital_gain',
                _cfg.get('migrazione_fondi', {}).get('aliquota_capital_gain', 0.26))

    st.subheader("Scenari per singolo fondo")
    nomi_inclusi = df_attivi_f['Fondo'].tolist()
    fondo_sel = st.selectbox("Seleziona fondo", nomi_inclusi) if nomi_inclusi else None

    c1,c2,c3 = st.columns(3)
    with c1: anni_f    = st.slider("Orizzonte (anni)", 1, 15, 5, key="anni_fondo")
    with c2: rend_b_f  = st.slider("Rendimento base %", 0.0, 8.0, 4.0, step=0.5, key="rb_fondo")
    with c3: rend_w_f  = st.slider("Rendimento worst %", -5.0, 4.0, 0.0, step=0.5, key="rw_fondo")
    rend_best_f = st.slider("Rendimento best %", 4.0, 12.0, 8.0, step=0.5, key="rbest_fondo")

    if fondo_sel:
        isin_sel  = df_attivi_f[df_attivi_f['Fondo'] == fondo_sel]['ISIN'].iloc[0]
        storico_f = get_storico_fondo(isin_sel, giorni=365)
        if not storico_f.empty and len(storico_f) > 1:
            st.subheader(f"Storico quote — {fondo_sel}")
            fig_st = go.Figure()
            fig_st.add_trace(go.Scatter(x=storico_f['data'], y=storico_f['quota'],
                                         name='Quota €', line=dict(color=COLORS['blu'],width=2)))
            fig_st.update_layout(height=200, xaxis_title="", yaxis_title="Quota €",
                                  margin=dict(t=20,b=0))
            _plotly_chart(fig_st, use_container_width=True, theme=None)

        row_f = df_attivi_f[df_attivi_f['Fondo'] == fondo_sel].iloc[0]
        pct   = row_f['% mantenere'] / 100
        ter   = row_f['TER stimato %'] / 100
        val_f = row_f['Valore €'] * pct
        cf_f  = row_f['Costo fisc. €'] * pct

        df_sc_f = simula_fondo_scenari(
            val_f, cf_f, quantita_pct=1.0, anni=anni_f,
            rend_base=rend_b_f/100, rend_worst=rend_w_f/100, rend_best=rend_best_f/100,
            costo_annuo=ter, aliquota=_aliquota
        )
        fig_f   = go.Figure()
        base_s  = df_sc_f[df_sc_f['scenario']=='base']
        worst_s = df_sc_f[df_sc_f['scenario']=='worst']
        best_s  = df_sc_f[df_sc_f['scenario']=='best']
        fig_f.add_trace(go.Scatter(
            x=best_s['anno'].tolist() + worst_s['anno'].tolist()[::-1],
            y=best_s['netto_uscita'].tolist() + worst_s['netto_uscita'].tolist()[::-1],
            fill='toself', fillcolor='rgba(30,139,92,0.08)',
            line=dict(color='rgba(0,0,0,0)'), name='Range worst-best'
        ))
        for sc_df, label in [(base_s,'Base'),(worst_s,'Worst'),(best_s,'Best')]:
            _sc_key = sc_df['scenario'].iloc[0]
            fig_f.add_trace(go.Scatter(
                x=sc_df['anno'], y=sc_df['netto_uscita'], name=f"Netto {label}",
                line=dict(color=SC_COLORS[_sc_key], width=2, dash=SC_DASH[_sc_key])
            ))
            fig_f.add_trace(go.Scatter(
                x=sc_df['anno'], y=sc_df['valore_lordo'], name=f"Lordo {label}",
                line=dict(color=SC_COLORS[_sc_key], width=1, dash='dot'), opacity=0.5
            ))
        fig_f.update_layout(height=400, xaxis_title="Anni", yaxis_title="€",
                             legend=dict(orientation="h", y=1.08))
        _plotly_chart(fig_f, _title=f"{fondo_sel} — {int(pct*100)}% · scenari worst/base/best",
                      use_container_width=True, theme=None)

        anno_kpi = st.slider("Mostra valori all'anno", 0, anni_f, min(3, anni_f), key="kpi_anno")
        for sc_df, label in [(base_s,'Base'),(worst_s,'Worst'),(best_s,'Best')]:
            row_k = sc_df[sc_df['anno'] == anno_kpi]
            if not row_k.empty:
                st.write(f"**{label}** — Anno {anno_kpi}: "
                          f"Lordo €{row_k['valore_lordo'].iloc[0]:,.0f} | "
                          f"Tassa €{row_k['tassa_latente'].iloc[0]:,.0f} | "
                          f"Netto **€{row_k['netto_uscita'].iloc[0]:,.0f}**")

    st.subheader("Portafoglio fondi aggregato — worst / base / best")
    fondi_rows_sim = [{
        'nome': r['Fondo'], 'valore_attuale': r['Valore €'],
        'costo_fiscale_stimato': r['Costo fisc. €'],
        'costo_annuo': r['TER stimato %'] / 100,
        'pct_da_mantenere': r['% mantenere']
    } for r in df_attivi_f.to_dict('records')]
    sc_agg = simula_portafoglio_fondi_scenari(
        fondi_rows_sim, anni=anni_f,
        rend_base=rend_b_f/100, rend_worst=rend_w_f/100,
        rend_best=rend_best_f/100, aliquota=_aliquota
    )
    fig_agg   = go.Figure()
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
            x=df_sc['anno'], y=df_sc['netto_uscita'], name=f"Netto {label}",
            line=dict(color=SC_COLORS[sc_key], width=2, dash=SC_DASH[sc_key])
        ))
        fig_agg.add_trace(go.Scatter(
            x=df_sc['anno'], y=df_sc['valore_lordo'], name=f"Lordo {label}",
            line=dict(color=SC_COLORS[sc_key], width=1, dash='dot'), opacity=0.4
        ))
    fig_agg.update_layout(title="Portafoglio fondi aggregato — evoluzione netta e lorda",
                           height=420, xaxis_title="Anni", yaxis_title="€",
                           legend=dict(orientation="h", y=1.08))
    _plotly_chart(fig_agg, use_container_width=True, theme=None)

    st.subheader("Simulatore: se esco il giorno X")
    c1,c2 = st.columns(2)
    with c1: data_uscita = st.date_input("Data di uscita", value=max(date(2027,6,1), date.today()), min_value=date.today())
    with c2: rend_uscita = st.slider("Rendimento fondi (%)", 1.0, 8.0, 4.0, step=0.5, key="ru")
    _quote_map_f = {r['ISIN']: r['Quota €']
                    for r in df_fondi_edit.to_dict('records') if r.get('Includi', False)}
    df_uscita = simula_uscita_fondo_data_x(_cfg, data_uscita, rend_uscita/100, _quote_map_f,
                                            fondi_df=get_fondi())
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Attesa",             f"{df_uscita['mesi_attesa'].iloc[0]:.0f} mesi")
    c2.metric("Valore proiettato",  f"€ {df_uscita['valore_proiettato'].sum():,.0f}")
    c3.metric("Tassa stimata",      f"€ {df_uscita['tassa_proiettata'].sum():,.0f}")
    c4.metric("Netto proiettato",   f"€ {df_uscita['netto_proiettato'].sum():,.0f}")
    show_u = df_uscita[['nome','valore_attuale','netto_uscita',
                          'valore_proiettato','tassa_proiettata','netto_proiettato']].copy()
    for _col in show_u.columns[1:]:
        show_u[_col] = show_u[_col].map(lambda x: f"€ {x:,.2f}")
    show_u.columns = ['Fondo','Val. attuale','Netto oggi','Val. proiettato','Tassa','Netto proiettato']
    st.dataframe(show_u, use_container_width=True, hide_index=True)

    st.subheader("Piano di uscita ottimale")
    piano_df = piano_uscita_ottimale(_cfg, _quote_map_f, fondi_df=get_fondi())
    if not piano_df.empty:
        fig_piano = px.bar(piano_df, x='anno', y='netto_in_etf', color='fondo',
                            title="Netto reinvestito in ETF per anno e per fondo",
                            labels={'anno':'Anno','netto_in_etf':'€ netto'})
        fig_piano.update_layout(height=300, legend=dict(orientation="h",y=1.08))
        _plotly_chart(fig_piano, use_container_width=True, theme=None)
        c1,c2,c3 = st.columns(3)
        c1.metric("Tot. rimborsato", f"€ {piano_df['rimborso_lordo'].sum():,.0f}")
        c2.metric("Tot. tasse",      f"€ {piano_df['tassa_26pct'].sum():,.0f}")
        c3.metric("Tot. netto ETF",  f"€ {piano_df['netto_in_etf'].sum():,.0f}")


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
    _auth_uname = st.session_state.get("_auth_username", "")
    if _auth_uname:
        _cu, _cl = st.columns([3, 1])
        _cu.caption(f"👤 {_auth_uname}")
        if _cl.button("Esci", use_container_width=True):
            for _k in ("_auth_ok", "_auth_ts", "_auth_user_id", "_auth_username",
                       "_auth_is_admin", "_auth_failures", "_auth_locked_until", "_auth_view",
                       "_auth_pending_uid", "_auth_pending_username", "_auth_pending_is_admin",
                       "_totp_setup_secret", "_auth_active_ts", "_auth_epoch_seen"):
                st.session_state.pop(_k, None)
            st.rerun()
    st.divider()
    _SEZIONE_FIGLIO = f"👶 {_NF} timeline"
    _sezioni_nav = [
        "🏠 Stato di famiglia",
        "📉 Portafoglio storico",
        "📈 ETF & mercato",
        "🏦 Fondi bancari",
        "📊 Azioni Accenture",
        "🎯 Simulatore strategie",
        _SEZIONE_FIGLIO,
        "📋 Fine mese",
        "💳 Analisi spese",
        "⚙️ Gestione Asset",
    ]
    if st.session_state.get("_auth_is_admin"):
        _sezioni_nav.append("👥 Utenti")
    sezione = st.radio("Sezione", _sezioni_nav)
    with st.expander("✏️ Nomi"):
        n1_inp = st.text_input("Persona 1", value=_N1, key="edit_n1", max_chars=100)
        n2_inp = st.text_input("Persona 2", value=_N2, key="edit_n2", max_chars=100)
        nf_inp = st.text_input("Figlio/a",  value=_NF, key="edit_nf", max_chars=100)
        if st.button("💾 Salva nomi"):
            st.session_state['params']['nome_persona1'] = n1_inp
            st.session_state['params']['nome_persona2'] = n2_inp
            st.session_state['params']['nome_figlio']   = nf_inp
            salva_params_persistenti({'nome_persona1': n1_inp,
                                      'nome_persona2': n2_inp,
                                      'nome_figlio':   nf_inp})
            st.rerun()
    with st.expander("🔑 Cambia password"):
        cur_pwd  = st.text_input("Password attuale", type="password", key="cur_pwd", max_chars=1024)
        new_pwd1 = st.text_input("Nuova password",   type="password", key="pwd1",    max_chars=1024)
        new_pwd2 = st.text_input("Conferma",         type="password", key="pwd2",    max_chars=1024)
        if st.button("💾 Cambia password"):
            if not cur_pwd:
                st.error("Inserisci la password attuale.")
            elif not new_pwd1:
                st.error("Inserisci una nuova password.")
            elif len(new_pwd1) < 8:
                st.error("Almeno 8 caratteri.")
            elif new_pwd1 != new_pwd2:
                st.error("Le password non corrispondono.")
            elif _DEMO:
                st.warning("In DEMO mode il cambio password è disabilitato.")
            else:
                from database import (aggiorna_password_utente as _apwu,
                                      hash_password as _hpc,
                                      verify_password as _vpwd,
                                      get_password_hash_by_id as _gphbi)
                from database import (get_server_pwdchg_state as _gpcs,
                                      set_server_pwdchg_state as _spcs)
                _uid = st.session_state.get("_auth_user_id")
                _pc = _gpcs(_uid) if _uid else {}
                _cur_hash = _gphbi(_uid) if _uid else None
                if time.time() < _pc.get("locked_until", 0.0):
                    st.error(f"Troppi tentativi. Riprova tra {int(_pc['locked_until'] - time.time()) + 1} secondi.")
                elif _cur_hash is None:
                    st.error("Errore durante la verifica.")
                elif not _vpwd(cur_pwd, _cur_hash):
                    _n_pc = min(_pc.get("n", 0) + 1, _AUTH_MAX_FAILURES)
                    _d_pc = _AUTH_LOCKOUT_DELAYS[_n_pc]
                    _spcs(_uid, _n_pc, time.time() + _d_pc if _d_pc > 0 else 0.0)
                    st.error("Password attuale non corretta.")
                elif _uid and _apwu(_uid, _hpc(new_pwd1)):
                    _spcs(_uid, 0, 0.0)
                    from database import invalida_sessioni as _inv_s2
                    _inv_s2(_uid)   # le altre sessioni vengono chiuse; questa resta valida
                    st.session_state["_auth_epoch_seen"] = time.time() + 1
                    st.success("Password aggiornata!")
                else:
                    st.error("Errore durante l'aggiornamento.")
    with st.expander("🔐 Sicurezza (2FA)"):
        from database import get_totp_info as _gti_sb, salva_totp_secret as _sts_sb, disabilita_totp as _dt_sb
        _uid_2fa = st.session_state.get("_auth_user_id")
        if '_totp_info_cache' not in st.session_state:
            st.session_state['_totp_info_cache'] = (
                _gti_sb(_uid_2fa) if _uid_2fa else {'totp_enabled': False, 'totp_secret': None}
            )
        _t_info  = st.session_state['_totp_info_cache']
        _t_on    = _t_info.get('totp_enabled', False)
        if _DEMO:
            st.warning("Disabilitato in DEMO mode.")
        elif st.session_state.get("_totp_setup_secret"):
            import pyotp as _pyotp_sb, qrcode as _qr_sb
            from io import BytesIO as _bio_sb
            _sec_sb  = st.session_state["_totp_setup_secret"]
            _uname_sb = st.session_state.get("_auth_username", "utente")
            _uri_sb  = _pyotp_sb.TOTP(_sec_sb).provisioning_uri(
                name=_uname_sb, issuer_name="Piano Finanziario Familiare"
            )
            _buf_sb = _bio_sb()
            _qr_sb.make(_uri_sb).save(_buf_sb, format="PNG")
            _buf_sb.seek(0)
            st.caption("Scansiona con Google Authenticator o Authy:")
            st.image(_buf_sb, width=180)
            st.caption(f"Oppure inserisci manualmente: `{_sec_sb}`")
            _code_sb = st.text_input("Codice di verifica (6 cifre)", max_chars=6, key="totp_setup_code")
            _sc1, _sc2 = st.columns(2)
            with _sc1:
                if st.button("✅ Conferma", key="btn_confirm_2fa"):
                    if not _code_sb:
                        st.error("Inserisci il codice.")
                    elif _pyotp_sb.TOTP(_sec_sb).verify(_code_sb.strip()):
                        if _sts_sb(_uid_2fa, _sec_sb):
                            st.session_state.pop("_totp_setup_secret", None)
                            st.session_state.pop("_totp_info_cache", None)
                            st.success("2FA attivato!")
                            st.rerun()
                        else:
                            st.error("Errore salvataggio. Riprova.")
                    else:
                        st.error("Codice non valido. Riprova.")
            with _sc2:
                if st.button("✖ Annulla", key="btn_cancel_2fa"):
                    st.session_state.pop("_totp_setup_secret", None)
                    st.rerun()
        elif not _t_on:
            st.caption("🔓 2FA non attivo")
            if st.button("Abilita 2FA", key="btn_enable_2fa"):
                import pyotp as _pyotp_en
                st.session_state["_totp_setup_secret"] = _pyotp_en.random_base32()
                st.rerun()
        else:
            st.caption("🔒 2FA attivo")
            _dis_code = st.text_input("Codice 2FA per disabilitare", max_chars=6, key="totp_dis_code")
            if st.button("Disabilita 2FA", key="btn_disable_2fa"):
                if not _dis_code:
                    st.error("Inserisci il codice 2FA.")
                else:
                    from database import totp_code_valido as _tcv_d
                    from database import (get_server_totp_state as _gsts_d,
                                          set_server_totp_state as _ssts_d,
                                          reset_server_totp_state as _rsts_d)
                    _td = _gsts_d(_uid_2fa)
                    _t_sec = _t_info.get('totp_secret')
                    if time.time() < _td.get("locked_until", 0.0):
                        st.error(f"Troppi tentativi. Riprova tra {int(_td['locked_until'] - time.time()) + 1} secondi.")
                    elif _t_sec and _tcv_d(_uid_2fa, _t_sec, _dis_code):
                        _rsts_d(_uid_2fa)
                        if _dt_sb(_uid_2fa):
                            st.session_state.pop("_totp_info_cache", None)
                            st.success("2FA disabilitato.")
                            st.rerun()
                        else:
                            st.error("Errore disabilitazione.")
                    else:
                        _n_td = min(_td.get("n", 0) + 1, _AUTH_MAX_FAILURES)
                        _d_td = _AUTH_LOCKOUT_DELAYS[_n_td]
                        _ssts_d(_uid_2fa, _n_td, time.time() + _d_td if _d_td > 0 else 0.0)
                        st.error("Codice non valido.")
    if st.session_state.get("_auth_is_admin") and not _DEMO:
        with st.expander("🧭 Wizard configurazione"):
            st.caption("Riesegui la procedura guidata di configurazione iniziale.")
            if st.button("Avvia wizard", key="btn_restart_wiz", use_container_width=True):
                st.session_state['_wizard_restart'] = True
                st.session_state.pop('_wizard_step', None)
                st.rerun()
    st.divider()
    st.caption(f"Config: {config.get('famiglia', {}).get('aggiornato', 'n/d')}")
    st.caption(f"Oggi: {date.today().strftime('%d/%m/%Y')}")
    if st.button("🔄 Aggiorna tutto"):
        if st.session_state.get("_auth_is_admin"):
            st.cache_data.clear()   # cache globale condivisa: la svuota solo l'admin
        for k in ['params','quote_map','xls_importati','saved_today',
                  'backfill_done','backfill_nuovi','nuove_tx','asset_catalog',
                  'pos_df_cache','fondi_df_cache',
                  'etf_perf_cache','azioni_df_cache','tx_db_cache',
                  'patrimonio_log_cache','port_storico_cache','eventi_storico_cache',
                  '_plotly_tpl_dark','_totp_info_cache','blocked_assets_cache','blocked_log_cache',
                  'taxonomy_cache','profilo_famiglia_cache',
                  'nomi_anonimiz_cache','tx_senza_cat_cache',
                  'spese_analisi_cache','spese_report_cache',
                  'batch_retro_riepilogo_cache','batch_retro_tx_cache',
                  'batch_retro_ai_cache','_retro_last_sel','ai_suggest_cache']:
            st.session_state.pop(k, None)
        st.rerun()
    st.toggle("🌙 Dark mode", key="_dark_mode_toggle", value=True)
    if st.session_state.get('nuove_tx'):
        st.info(f"📥 {st.session_state['nuove_tx']} nuove transazioni")
    if st.session_state.get('backfill_nuovi'):
        st.info(f"📡 {st.session_state['backfill_nuovi']} nuovi prezzi scaricati")
    with st.expander("📡 Allineamento dati"):
        _ss = st.session_state
        _snap_ok  = bool(_ss.get('saved_today'))
        _bf_ok    = bool(_ss.get('backfill_done'))
        _qm       = _ss.get('quote_map', {})
        _tx_cache = _ss.get('tx_db_cache')
        _last_tx  = "N/D"
        if isinstance(_tx_cache, pd.DataFrame) and not _tx_cache.empty and 'data' in _tx_cache.columns:
            try:
                _last_tx = pd.to_datetime(_tx_cache['data']).max().strftime('%d/%m/%Y')
            except Exception:
                pass
        st.markdown(f"{'✅' if _snap_ok else '⚠️'} Snapshot oggi: {'sì' if _snap_ok else 'no'}")
        st.markdown(f"{'✅' if _bf_ok else '⚠️'} Prezzi ETF: {'ok' if _bf_ok else 'in attesa'}")
        st.markdown(f"{'✅' if _qm else '⚠️'} Quote fondi: {str(len(_qm)) + ' caricate' if _qm else 'non caricate'}")
        st.caption(f"Ultima tx registrata: {_last_tx}")


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
            fondi_man    = st.number_input("Fondi bancari (€)", value=int(p_saved.get('fondi_bancari', config.get('patrimonio', {}).get('fondi_bancari', 0))), step=500)
            generali_man = st.number_input("Generali (€)", value=int(p_saved.get('gestione_separata_generali', config.get('patrimonio', {}).get('gestione_separata_generali', 0))), step=500)
        with c2:
            liq_dan = st.number_input(f"Liquidità {_N1} (€)", value=int(p_saved.get('liquidita_persona1', config.get('patrimonio', {}).get('liquidita_persona1', 0))), step=100)
            liq_ale = st.number_input(f"Liquidità {_N2} (€)", value=int(p_saved.get('liquidita_persona2', config.get('patrimonio', {}).get('liquidita_persona2', 0))), step=100)
        with c3:
            conto_com  = st.number_input("Conto comune (€)", value=int(p_saved.get('conto_comune', config.get('patrimonio', {}).get('conto_comune', 0))), step=100)
            debiti_man = st.number_input("Debiti totali (€)", value=int(p_saved.get('debiti_totale', 0)), step=100, help="Somma di tutti i debiti attivi (mutuo, prestiti, ecc.)")

        if st.button("💾 Salva valori"):
            nuovi_params = {
                'fondi_bancari': fondi_man, 'gestione_separata_generali': generali_man,
                'liquidita_persona1': liq_dan, 'liquidita_persona2': liq_ale,
                'conto_comune': conto_com, 'debiti_totale': debiti_man,
                'affitto_accantonato': p_saved.get('affitto_accantonato',
                                                    config.get('patrimonio', {}).get('affitto_accantonato', 0))
            }
            if salva_params_persistenti(nuovi_params):
                st.session_state['params'].update(nuovi_params)
                st.session_state.pop('saved_today', None)  # forza re-save snapshot
                st.success("Salvato su Supabase ✓")
                st.rerun()

    params_correnti = {
        'fondi_bancari': fondi_man, 'gestione_separata_generali': generali_man,
        'liquidita_persona1': liq_dan, 'liquidita_persona2': liq_ale,
        'conto_comune': conto_com, 'debiti_totale': debiti_man,
        'affitto_accantonato': p_saved.get('affitto_accantonato',
                                            config.get('patrimonio', {}).get('affitto_accantonato', 0))
    }

    # ── Asset illiquidi: carica cache prima della UI ──────────
    if 'blocked_assets_cache' not in st.session_state:
        from database import carica_blocked_assets_correnti as _cabc_init
        st.session_state['blocked_assets_cache'] = _cabc_init() if (db_ok and not _DEMO) else []

    # ── Asset illiquidi UI ────────────────────────────────────
    # In DEMO la sezione leggerebbe/scriverebbe dati reali (usa database.py direttamente)
    if db_ok and not _DEMO:
        from database import (salva_blocked_asset as _sba,
                               carica_blocked_assets_log as _cabl,
                               elimina_blocked_asset as _eba)
        _TIPI_ILLIQUIDI = ['immobile', 'tfr', 'previdenza', 'altro']
        with st.expander("🏦 Asset illiquidi (immobili, TFR, previdenza...)", expanded=False):
            st.caption("Valorizzazioni periodiche di asset non quotati. Ogni aggiornamento viene salvato con la data, costruendo uno storico nel tempo.")
            # Refresh blocklist dalla cache di sessione
            _bl = st.session_state.get('blocked_assets_cache', [])
            if _bl:
                st.markdown(f"**Totale asset illiquidi: € {sum(float(a.get('valore',0) or 0) for a in _bl):,.0f}**")
                _bl_df = pd.DataFrame(_bl)[['asset_nome','asset_tipo','valore','data']]
                _bl_df['data'] = pd.to_datetime(_bl_df['data']).dt.strftime('%d/%m/%Y')
                _bl_df['valore'] = _bl_df['valore'].apply(lambda v: f"€ {float(v):,.0f}")
                _bl_df.columns = ['Asset', 'Tipo', 'Valore', 'Ultima valoriz.']
                st.dataframe(_bl_df, use_container_width=True, hide_index=True)
            else:
                st.info("Nessun asset illiquido ancora registrato.")

            st.divider()
            st.markdown("##### Registra valorizzazione")
            _nomi_esistenti = [a['asset_nome'] for a in _bl] if _bl else []
            _modo = st.radio("Modalità", ["Aggiorna asset esistente", "Aggiungi nuovo asset"],
                             horizontal=True, key="ba_modo", label_visibility="collapsed",
                             disabled=not _bl)
            if not _bl or _modo == "Aggiungi nuovo asset":
                with st.form("ba_nuovo"):
                    _bn_nome = st.text_input("Nome asset", max_chars=80,
                                              placeholder="es. Casa Via Roma, TFR Azienda X")
                    _bn_tipo = st.selectbox("Tipo", _TIPI_ILLIQUIDI)
                    _bn_val  = st.number_input("Valore stimato (€)", min_value=0, step=1000)
                    _bn_data = st.date_input("Data valorizzazione", value=date.today())
                    _bn_nota = st.text_input("Nota (opzionale)", max_chars=200)
                    if st.form_submit_button("💾 Salva"):
                        _bn_nome = _bn_nome.strip()
                        if not _bn_nome:
                            st.error("Il nome asset è obbligatorio.")
                        else:
                            _aid = _bn_nome.lower().replace(' ', '_')[:40]
                            if any(a.get('asset_id') == _aid for a in _bl):
                                st.error("Esiste già un asset con questo nome (o troppo simile): "
                                         "usa 'Aggiorna asset esistente' o cambia nome.")
                            elif _sba(_aid, _bn_nome, _bn_tipo, _bn_val,
                                    _bn_data.isoformat(), _bn_nota or None):
                                st.session_state.pop('blocked_assets_cache', None)
                                st.session_state.pop('blocked_log_cache', None)
                                st.session_state.pop('saved_today', None)
                                st.success(f"✓ {_bn_nome} salvato")
                                st.rerun()
                            else:
                                st.error("Errore durante il salvataggio.")
            else:
                _sel_nome = st.selectbox("Asset da aggiornare", _nomi_esistenti, key="ba_sel")
                _sel_asset = next((a for a in _bl if a['asset_nome'] == _sel_nome), {})
                with st.form("ba_update"):
                    _bu_val  = st.number_input("Nuovo valore (€)", min_value=0, step=1000,
                                                value=int(_sel_asset.get('valore', 0) or 0))
                    _bu_data = st.date_input("Data valorizzazione", value=date.today())
                    _bu_nota = st.text_input("Nota (opzionale)", max_chars=200)
                    _col_s, _col_d = st.columns([3, 1])
                    with _col_s:
                        _do_save = st.form_submit_button("💾 Aggiorna valore")
                    with _col_d:
                        _do_del  = st.form_submit_button("🗑️ Elimina asset", type="secondary")
                    if _do_save:
                        _aid = _sel_asset.get('asset_id',
                               _sel_nome.lower().replace(' ', '_')[:40])
                        if _sba(_aid, _sel_nome, _sel_asset.get('asset_tipo', 'altro'),
                                _bu_val, _bu_data.isoformat(), _bu_nota or None):
                            st.session_state.pop('blocked_assets_cache', None)
                            st.session_state.pop('blocked_log_cache', None)
                            st.session_state.pop('saved_today', None)
                            st.success(f"✓ {_sel_nome} aggiornato")
                            st.rerun()
                        else:
                            st.error("Errore durante il salvataggio.")
                    elif _do_del:
                        _aid = _sel_asset.get('asset_id',
                               _sel_nome.lower().replace(' ', '_')[:40])
                        if _eba(_aid):
                            st.session_state.pop('blocked_assets_cache', None)
                            st.session_state.pop('blocked_log_cache', None)
                            st.session_state.pop('saved_today', None)
                            st.success(f"✓ {_sel_nome} eliminato")
                            st.rerun()
                        else:
                            st.error("Errore durante l'eliminazione.")

            # Grafico storico valorizzazioni
            if _bl:
                if 'blocked_log_cache' not in st.session_state:
                    st.session_state['blocked_log_cache'] = _cabl(giorni=730)
                _log_ba = st.session_state['blocked_log_cache']
                if not _log_ba.empty:
                    st.markdown("##### Storico valorizzazioni")
                    _asset_nomi = sorted(_log_ba['asset_nome'].unique())
                    _sel_chart = st.multiselect("Mostra", _asset_nomi,
                                                 default=_asset_nomi[:3], key="ba_chart_sel")
                    if _sel_chart:
                        fig_ba = go.Figure()
                        for _an in _sel_chart:
                            _s = _log_ba[_log_ba['asset_nome'] == _an].sort_values('data')
                            fig_ba.add_trace(go.Scatter(
                                x=_s['data'], y=_s['valore'].round(0),
                                name=_an, mode='lines+markers',
                                line=dict(width=2)
                            ))
                        fig_ba.update_layout(height=220, xaxis_title="",
                                              yaxis_title="€", margin=dict(t=10, b=0),
                                              legend=dict(orientation="h", y=1.08))
                        _plotly_chart(fig_ba, use_container_width=True, theme=None)

    _blocked_list = st.session_state.get('blocked_assets_cache', [])
    _blocked_totale = sum(float(a.get('valore', 0) or 0) for a in _blocked_list)

    etf_df   = get_etf_perf()
    fondi_df = get_fondi()
    azioni_df= get_azioni()
    snap = patrimonio_snapshot(config, etf_df, fondi_df, azioni_df, params_correnti,
                               blocked_assets_totale=_blocked_totale)

    # Auto-save silenzioso (lazy: gira quando i dati ETF/azioni sono già caricati qui sopra)
    if db_ok and 'saved_today' not in st.session_state:
        auto_save_snapshot(snap, st.session_state['params'])
        st.session_state['saved_today'] = True

    # Backfill prezzi storici (once per session, lazy)
    if db_ok and 'backfill_done' not in st.session_state:
        try:
            _bf_res = esegui_backfill_avvio(config, verbose=False)
            st.session_state['backfill_done'] = True
            if _bf_res.get('n_prezzi_scaricati', 0) > 0:
                st.session_state['backfill_nuovi'] = _bf_res['n_prezzi_scaricati']
        except Exception:
            st.session_state['backfill_done'] = True

    # Import XLS lazy — raggruppato con auto-save e backfill, prima dei KPI
    _run_xls_import()

    c1,c2,c3,c4,c5 = st.columns(5)
    c1.metric("Patrimonio totale", f"€ {snap['totale_eur']:,.0f}")
    c2.metric("Netto fiscale",     f"€ {snap['totale_netto_fiscale']:,.0f}", delta=f"-€ {snap['tassa_latente_fondi']:,.0f} latenti")
    c3.metric("Fondi bancari",     f"€ {snap['fondi_bancari']:,.0f}")
    c4.metric("ETF totale",        f"€ {snap['etf_persona1']+snap['etf_figlio']:,.0f}")
    c5.metric("Azioni ACN (USD)",  f"$ {snap['azioni_acn_usd']:,.0f}")

    if snap['debiti_totale'] > 0:
        _nw1, _nw2, _nw3 = st.columns(3)
        _nw1.metric("Net Worth reale", f"€ {snap['net_worth']:,.0f}",
                    delta=f"-€ {snap['debiti_totale']:,.0f} debiti", delta_color="inverse")
        _nw2.metric("Debiti totali", f"€ {snap['debiti_totale']:,.0f}")
        _nw3.metric("Patrimonio lordo", f"€ {snap['totale_eur']:,.0f}")

    _cfg_p = config.get('parametri', {})

    # ── Bollo threshold alert ──────────────────────────────────
    # Soglia da Supabase config_params (fallback config.yaml); editabile senza redeploy
    _bs_raw = p_saved.get('bollo_soglia_liquidita')
    try:
        _bollo_soglia = float(_bs_raw)
    except (TypeError, ValueError):
        _bollo_soglia = float(_cfg_p.get('bollo_soglia_liquidita', 5000))
    # snap['liquidita'] include affitto_accantonato — escludiamo: il bollo riguarda solo i conti correnti
    _liq_conti = float(snap['liquidita']) - float(params_correnti.get('affitto_accantonato') or 0)
    if _liq_conti > _bollo_soglia:
        st.warning(
            f"💰 **Liquidità sui conti correnti: € {_liq_conti:,.0f}** — supera la soglia di € {_bollo_soglia:,.0f}. "
            f"Considera di investire o spostare l'eccedenza (€ {_liq_conti - _bollo_soglia:,.0f}) "
            f"per evitare il bollo e ridurre il cash drag.",
            icon="⚠️"
        )

    # ── FIRE Progress tracker ─────────────────────────────────
    # Carica tx in anticipo per stimare le spese annue dall'ingestion
    if 'tx_db_cache' not in st.session_state:
        st.session_state['tx_db_cache'] = carica_transazioni_db(mesi=12)
    _tx_fire = st.session_state['tx_db_cache']
    _spese_stimate_fire = 0.0
    if not _tx_fire.empty and 'expense' in _tx_fire.columns:
        _pm_fire = _tx_fire.groupby('month')['expense'].sum()
        if not _pm_fire.empty:
            _spese_stimate_fire = round(float(_pm_fire.tail(12).mean()) * 12, 0)

    _fire_spese = float(p_saved.get('fire_spese_annue', 0) or 0)
    _fire_r  = float(_cfg_p.get('fire_rendimento', 0.07))
    _fire_tp = float(_cfg_p.get('fire_tasso_prelievo', 0.04))
    _fire_eta = int(p_saved.get('fire_eta_attuale', 35) or 35)
    _fire_pen = int(p_saved.get('fire_eta_pensione', 60) or 60)

    st.subheader("🔥 FIRE Progress")
    if _fire_spese > 0:
        _fire_m  = calcola_fire_metrics(
            patrimonio_attuale=float(snap['totale_eur']),
            spese_annue=_fire_spese,
            eta_attuale=_fire_eta, eta_pensione=_fire_pen,
            rendimento=_fire_r, tasso_prelievo=_fire_tp,
        )
        if _fire_m:
            _fc1, _fc2 = st.columns(2)
            with _fc1:
                st.markdown("**Regular FIRE**")
                st.caption(
                    f"Target: **€ {_fire_m['fire_target']:,.0f}** "
                    f"(€ {_fire_spese:,.0f}/anno ÷ {_fire_tp*100:.0f}%)"
                )
                st.progress(min(_fire_m['fire_pct'] / 100, 1.0))
                _anni_lab = (f" — ∼ {_fire_m['anni_a_fire']:.0f} anni a ritmo attuale"
                             if _fire_m['anni_a_fire'] else "")
                st.caption(f"**{_fire_m['fire_pct']:.1f}%** raggiunto{_anni_lab}")
            with _fc2:
                st.markdown("**Coast FIRE**")
                st.caption(
                    f"Target: **€ {_fire_m['coast_target']:,.0f}** "
                    f"(pensione a {_fire_pen} anni, r={_fire_r*100:.0f}%)"
                )
                st.progress(min(_fire_m['coast_pct'] / 100, 1.0))
                if _fire_m['coast_raggiunto']:
                    st.success("✅ Coast FIRE raggiunto — il composto fa il resto")
                else:
                    _manca = _fire_m['coast_target'] - snap['totale_eur']
                    st.caption(f"**{_fire_m['coast_pct']:.1f}%** — mancano € {_manca:,.0f}")
        with st.expander("⚙️ Modifica configurazione FIRE", expanded=False):
            _hint_fire = (f"Stima automatica dalle ultime transazioni: € {_spese_stimate_fire:,.0f}/anno"
                          if _spese_stimate_fire else None)
            _fce1, _fce2, _fce3 = st.columns(3)
            with _fce1:
                _e_spese = st.number_input("Spese annue (€)", value=int(_fire_spese),
                                           step=1000, help=_hint_fire, key="fire_edit_spese")
            with _fce2:
                _e_eta = st.number_input("Età attuale", value=_fire_eta,
                                         min_value=18, max_value=80, step=1, key="fire_edit_eta")
            with _fce3:
                _e_pen = st.number_input("Età target FIRE", value=_fire_pen,
                                         min_value=30, max_value=80, step=1, key="fire_edit_pen")
            if st.button("💾 Aggiorna configurazione FIRE", key="fire_edit_save"):
                _fp = {'fire_spese_annue': _e_spese, 'fire_eta_attuale': _e_eta, 'fire_eta_pensione': _e_pen}
                if salva_params_persistenti(_fp):
                    st.session_state['params'].update(_fp)
                    st.success("Salvato ✓")
                    st.rerun()
    else:
        _hint_fire = (f"Stima automatica dalle ultime transazioni: € {_spese_stimate_fire:,.0f}/anno"
                      if _spese_stimate_fire else None)
        _fn1, _fn2, _fn3 = st.columns(3)
        with _fn1:
            _val_spese_default = int(_spese_stimate_fire) if _spese_stimate_fire else 30000
            _new_spese = st.number_input("Spese annue target (€)", value=_val_spese_default,
                                         step=1000, help=_hint_fire, key="fire_new_spese")
        with _fn2:
            _new_eta = st.number_input("Età attuale", value=_fire_eta,
                                       min_value=18, max_value=80, step=1, key="fire_new_eta")
        with _fn3:
            _new_pen = st.number_input("Età target FIRE", value=_fire_pen,
                                       min_value=30, max_value=80, step=1, key="fire_new_pen")
        if st.button("💾 Salva e attiva FIRE tracker", key="fire_new_save"):
            _fp = {'fire_spese_annue': _new_spese, 'fire_eta_attuale': _new_eta, 'fire_eta_pensione': _new_pen}
            if salva_params_persistenti(_fp):
                st.session_state['params'].update(_fp)
                st.success("FIRE tracker attivato ✓")
                st.rerun()

    fig_pat = go.Figure(go.Pie(
        labels=['Fondi bancari','Generali',f'ETF {_N1}',f'ETF {_NF}','Azioni ACN','Liquidità'],
        values=[snap['fondi_bancari'],snap['generali'],snap['etf_persona1'],
                snap['etf_figlio'],snap['azioni_acn_usd'],snap['liquidita']],
        hole=0.45, marker_colors=list(COLORS.values())[:6]
    ))
    fig_pat.update_layout(title="Composizione patrimonio", height=340, margin=dict(t=40,b=0,l=0,r=0))
    _plotly_chart(fig_pat, use_container_width=True, theme=None)

    # Storico patrimonio da Supabase
    if 'patrimonio_log_cache' not in st.session_state:
        st.session_state['patrimonio_log_cache'] = get_storico_patrimonio(giorni=730)
    log_df = st.session_state['patrimonio_log_cache']
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
        _plotly_chart(fig_log, use_container_width=True, theme=None)

        # Delta rispetto a inizio periodo
        if len(log_filt) >= 2:
            delta = log_filt['totale_eur'].iloc[-1] - log_filt['totale_eur'].iloc[0]
            st.caption(f"Variazione nel periodo: **€ {delta:+,.0f}**")
    else:
        st.info("Lo storico si costruirà automaticamente giorno per giorno aprendo l'app.")

    st.subheader("Entrate & uscite bancarie")
    if 'tx_db_cache' not in st.session_state:
        st.session_state['tx_db_cache'] = carica_transazioni_db(mesi=12)
    df = st.session_state['tx_db_cache']
    if df.empty:
        st.info("Nessuna transazione nel DB. Copia gli XLS in data/input/ e premi 🔄 Aggiorna tutto.")
    else:
        mesi = sorted(df['month'].unique(), reverse=True)
        mese_sel = st.selectbox("Mese", [str(m) for m in mesi])
        mese_df  = df[df['month'] == mese_sel]
        e_tot = mese_df['income'].sum(); u_tot = mese_df['expense'].sum()
        budget= config.get('allocazione', {}).get('spese_correnti_ora', 0)
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Entrate", f"€ {e_tot:,.0f}")
        c2.metric("Uscite",  f"€ {u_tot:,.0f}")
        c3.metric("Saldo",   f"€ {e_tot-u_tot:,.0f}")
        c4.metric("Δ budget",f"€ {u_tot-budget:+,.0f}", delta_color="inverse")

        # Savings Rate
        _per_mese = df.groupby('month')[['income','expense']].sum()
        # Mesi senza entrate (import parziale): savings rate non definito → NaN, escluso dalle medie
        _per_mese['sr'] = (
            (_per_mese['income'] - _per_mese['expense'])
            .div(_per_mese['income'].where(_per_mese['income'] > 0)) * 100
        )
        _curr_year = str(date.today().year)
        _ytd_idx   = [m for m in _per_mese.index if str(m).startswith(_curr_year)]
        _sr_ytd    = _per_mese.loc[_ytd_idx, 'sr'].mean() if _ytd_idx else float('nan')
        _sr_12m    = _per_mese.tail(12)['sr'].mean() if not _per_mese.empty else float('nan')
        _sr_mese   = float(_per_mese.loc[mese_sel, 'sr']) if mese_sel in _per_mese.index else float('nan')
        _fmt_sr    = lambda v: f"{v:.1f}%" if _has(v) else "N/D"
        _sc1,_sc2,_sc3 = st.columns(3)
        _sc1.metric("Savings rate (mese)", _fmt_sr(_sr_mese))
        _sc2.metric("Savings rate YTD",    _fmt_sr(_sr_ytd))
        _sc3.metric("Media 12 mesi",       _fmt_sr(_sr_12m))

        cat_df = mese_df[mese_df['amount']<0].groupby('category')['expense'].sum().sort_values()
        fig_cat = go.Figure(go.Bar(x=cat_df.values, y=cat_df.index,
                                    orientation='h', marker_color=COLORS['blu']))
        fig_cat.update_layout(height=max(280,len(cat_df)*28), margin=dict(t=40,b=0,l=0,r=0))
        _plotly_chart(fig_cat, _title=f"Uscite per categoria — {mese_sel}", use_container_width=True, theme=None)
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

    from positions import ASSET_TICKERS, storico_posizioni
    _cat_pos = st.session_state.get('asset_catalog', pd.DataFrame())
    if not _cat_pos.empty:
        _cat_pos_rows = _cat_pos.to_dict('records')
        _nomi_map_pos    = {r['isin']: r['nome'] for r in _cat_pos_rows}
        _tutti_asset_pos = [{'isin': r['isin'], 'nome': r['nome']}
                            for r in _cat_pos_rows if r.get('stato', 'attivo') == 'attivo']
    else:
        _nomi_map_pos    = {}
        _tutti_asset_pos = []

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
        if 'port_storico_cache' not in st.session_state:
            st.session_state['port_storico_cache'] = {}
        with st.spinner("Caricamento storico portafoglio..."):
            if data_da not in st.session_state['port_storico_cache']:
                _cat_isins = (st.session_state['asset_catalog']['isin'].tolist()
                              if not st.session_state.get('asset_catalog', pd.DataFrame()).empty else None)
                st.session_state['port_storico_cache'][data_da] = get_storico_portafoglio(
                    data_da, isins=_cat_isins)
            df_port = st.session_state['port_storico_cache'][data_da]

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
            if 'eventi_storico_cache' not in st.session_state:
                st.session_state['eventi_storico_cache'] = {}
            if data_da not in st.session_state['eventi_storico_cache']:
                st.session_state['eventi_storico_cache'][data_da] = get_eventi_portafoglio(data_da)
            eventi = st.session_state['eventi_storico_cache'][data_da]
            if not eventi.empty:
                for ev in eventi.to_dict('records'):
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
            _plotly_chart(fig_tot, use_container_width=True, theme=None)

            # Grafico composizione (area stacked)
            st.subheader("Composizione nel tempo")
            isins_disponibili = [c for c in df_port.columns
                                  if c not in ['data','totale']
                                  and df_port[c].sum() > 0]

            nomi_map = _nomi_map_pos
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
            _plotly_chart(fig_comp, use_container_width=True, theme=None)

    # ── Vista per asset ───────────────────────────────────────
    else:
        # Selezione asset
        tutti_asset = _tutti_asset_pos
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
                    height=380,
                    yaxis=dict(title="Prezzo quota €", side='left'),
                    yaxis2=dict(title="Valore € posizione",
                                side='right', overlaying='y'),
                    legend=dict(orientation="h", y=1.08),
                    margin=dict(t=60,b=0)
                )
                _plotly_chart(fig_asset, _title=f"{asset_sel} — prezzo quota e valore posizione", use_container_width=True, theme=None)

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
        for r in attivi.to_dict('records'):
            c1,c2,c3,c4 = st.columns(4)
            c1.metric(r['nome'], f"€ {r['valore_attuale']:,.0f}")
            c2.metric("Prezzo", f"$ {r['prezzo_attuale']:.2f}" if _has(r['prezzo_attuale']) else "N/D")
            c3.metric("YTD", f"{r['rendimento_pct']:+.1f}%" if _has(r['rendimento_pct']) else "N/D")
            c4.metric("1 anno", f"{r['perf_1y']:+.1f}%" if _has(r['perf_1y']) else "N/D")

        # ── Rebalancing alert ─────────────────────────────────
        _tot_etf_val = attivi['valore_attuale'].sum()
        if _tot_etf_val > 0:
            _soglia_reb = float(config.get('parametri', {}).get('rebalancing_soglia_drift', 5.0))
            _cfg_target = {e['ticker_bi']: float(e.get('target_pct', 0))
                           for e in config.get('etf', []) if e.get('ticker_bi')}
            _cat_reb = st.session_state.get('asset_catalog', pd.DataFrame())
            if not _cat_reb.empty and 'target_pct' in _cat_reb.columns:
                _cfg_target.update({
                    str(r.get('ticker_bi', '')): float(r.get('target_pct', 0))
                    for r in _cat_reb[_cat_reb['tipo'] == 'etf'].to_dict('records')
                    if r.get('ticker_bi')
                })
            _reb_rows = []
            for r in attivi.to_dict('records'):
                _tk = str(r.get('ticker', r.get('ticker_bi', '')))
                _tgt = _cfg_target.get(_tk, 0.0)
                _att = r['valore_attuale'] / _tot_etf_val * 100
                _drift = _att - _tgt
                _da_acq = (_tgt - _att) / 100 * _tot_etf_val
                _reb_rows.append({
                    'ETF': r['nome'], 'Target %': _tgt,
                    'Attuale %': round(_att, 1), 'Drift %': round(_drift, 1),
                    'Ribilanciare €': round(_da_acq, 0),
                    '': '🔴' if abs(_drift) > _soglia_reb else '✅',
                })
            _reb_df = pd.DataFrame(_reb_rows)
            _alert  = _reb_df['Drift %'].abs().gt(_soglia_reb).any()
            with st.expander(
                f"⚖️ Rebalancing ETF {'— ⚠️ intervento consigliato' if _alert else '— in linea'}",
                expanded=_alert
            ):
                st.caption(f"Soglia drift: ±{_soglia_reb:.0f}%. Totale ETF attivi: € {_tot_etf_val:,.0f}")
                st.dataframe(_reb_df, use_container_width=True, hide_index=True)
                st.divider()
                st.caption("**⚙️ Modifica target allocation**")
                _reb_tk_list = [str(r.get('ticker', r.get('ticker_bi', ''))) for r in attivi.to_dict('records')]
                _reb_tgt_new = {}
                _reb_edit_cols = st.columns(max(len(_reb_tk_list), 1))
                for _ri, _rtkr in enumerate(_reb_tk_list):
                    with _reb_edit_cols[_ri]:
                        _reb_tgt_new[_rtkr] = st.number_input(
                            f"Target % {_rtkr}", value=float(_cfg_target.get(_rtkr, 0.0)),
                            min_value=0.0, max_value=100.0, step=5.0, key=f"reb_tgt_{_rtkr}")
                _reb_tot = sum(_reb_tgt_new.values())
                if _reb_tot > 0 and abs(_reb_tot - 100.0) > 0.1:
                    st.warning(f"I target sommano a {_reb_tot:.0f}% (diverso da 100%). Verifica prima di salvare.")
                if st.button("💾 Salva target allocation", key="reb_save_tgt"):
                    _cat_reb_upd = st.session_state.get('asset_catalog', pd.DataFrame())
                    _reb_ok = True
                    for _rtkr2, _rtgt_val in _reb_tgt_new.items():
                        if not _cat_reb_upd.empty and 'ticker_bi' in _cat_reb_upd.columns:
                            _arows = _cat_reb_upd[_cat_reb_upd['ticker_bi'] == _rtkr2]
                            if not _arows.empty:
                                _aupd = _asset_record(_arows.iloc[0].to_dict(),
                                                      target_pct=float(_rtgt_val))
                                if not salva_asset(_aupd):
                                    _reb_ok = False
                    if _reb_ok:
                        st.session_state.pop('asset_catalog', None)
                        st.session_state.pop('etf_perf_cache', None)
                        st.success("Target allocation salvati ✓")
                        st.rerun()
                    else:
                        st.error("Errore nel salvataggio. Verifica la connessione a Supabase.")

    # Grafico storico (@st.fragment: solo questo blocco reruns quando periodo/selezione cambia)
    st.subheader("Performance storica")
    _frag_storico_etf()

    # ── SIMULATORE PAC ETF — tabella editabile ────────────────
    st.subheader("Simulatore PAC — portafoglio ETF personalizzabile")
    st.caption("Aggiungi, rimuovi o modifica gli ETF e l'importo mensile per ciascuno. "
               "Il grafico mostra il portafoglio aggregato con scenari worst/base/best.")

    # Tabella editabile ETF PAC — seeded dal catalog DB (fonte di verità)
    _alloc = config.get('allocazione', {})
    _pat = config.get('patrimonio', {})
    _cat_now = st.session_state.get('asset_catalog', pd.DataFrame())
    if not _cat_now.empty and 'tipo' in _cat_now.columns:
        _etf_cat = _cat_now[_cat_now['tipo'] == 'etf']
        _etf_cat_rows = _etf_cat.to_dict('records')
        _etf_map = {str(r.get('ticker_bi', '')): r for r in _etf_cat_rows if r.get('ticker_bi')}
        _etf_attivi    = [r for r in _etf_cat_rows if str(r.get('stato','')) in ('attivo','da_avviare')]
        _etf_candidati = [r for r in _etf_cat_rows if str(r.get('stato','')) == 'candidato']
    else:
        _etf_map = {e['ticker_bi']: e for e in config.get('etf', []) if e.get('ticker_bi')}
        _etf_attivi = [e for e in config.get('etf', [])
                       if e.get('stato') in ('attivo','da_avviare')]
        _etf_candidati = [e for e in config.get('etf', []) if e.get('stato') == 'candidato']

    _iwda = _etf_map.get('IWDA', {})
    _acwe = _etf_map.get('ACWE', {})
    etf_default = [
        {'ETF': _iwda.get('ticker_bi', 'IWDA'),
         'Descrizione': _iwda.get('nome', 'iShares Core MSCI World'),
         'Importo €/mese': _alloc.get('pac_persona1_con_nido', 800),
         'Valore iniziale €': float(_iwda.get('valore_iniziale') or _pat.get('etf_cspx_directa', 0)),
         'Includi': True},
        {'ETF': f"ACWE ({st.session_state.get('params', {}).get('nome_figlio', 'Figlio/a')})",
         'Descrizione': _acwe.get('nome', 'SPDR MSCI ACWI'),
         'Importo €/mese': _alloc.get('pac_figlio', 0),
         'Valore iniziale €': 0,
         'Includi': True},
    ] + [
        {'ETF': str(e.get('ticker_bi', e.get('isin',''))),
         'Descrizione': str(e.get('nome','')),
         'Importo €/mese': 0, 'Valore iniziale €': 0, 'Includi': False}
        for e in _etf_candidati
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

        _p_cfg = config.get('parametri', {})
        _sc_worst = _p_cfg.get('scenario_spread_worst', 0.04)
        _sc_best = _p_cfg.get('scenario_spread_best', 0.03)
        sc_etf = simula_portafoglio_etf_scenari(
            etf_rows_sim, anni=anni_pac,
            rend_base=rend_base_etf/100,
            rend_worst=max(rend_base_etf/100 - _sc_worst, 0.01),
            rend_best=rend_base_etf/100 + _sc_best
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
            height=420, xaxis_title="Anni", yaxis_title="€",
            legend=dict(orientation="h", y=1.08)
        )
        _plotly_chart(fig_etf_sc, _title=f"Portafoglio ETF — €{tot_pac:,.0f}/mese · partenza €{tot_ini:,.0f} · {anni_pac} anni", use_container_width=True, theme=None)

        c1,c2,c3,c4 = st.columns(4)
        c1.metric("PAC totale/mese", f"€ {tot_pac:,.0f}")
        c2.metric(f"Valore base ({anni_pac}a)", f"€ {vf_base:,.0f}")
        c3.metric(f"Scenario worst", f"€ {vf_worst:,.0f}", delta=f"€ {vf_worst-vf_base:+,.0f}")
        c4.metric(f"Scenario best",  f"€ {vf_best:,.0f}",  delta=f"€ {vf_best-vf_base:+,.0f}")

    # Confronto candidati (@st.fragment: solo questo blocco reruns quando periodo cambia)
    _frag_confronto_candidati()

    # ── COMPOSIZIONE GEO/SETTORIALE ───────────────────────────
    _etf_bkdn = config.get('etf_breakdown', {})
    if _etf_bkdn:
        with st.expander("📊 Composizione geografica & settoriale", expanded=False):
            st.caption("Dati da factsheet ufficiali — aggiornare manualmente in `config.yaml → etf_breakdown`.")
            _bk_sel = st.selectbox("ETF", list(_etf_bkdn.keys()), key="sel_etf_breakdown")
            _bk = _etf_bkdn.get(_bk_sel, {})
            if _bk:
                st.caption(f"{_bk.get('nome','')} · aggiornato: {_bk.get('aggiornato','N/D')}")
                _geo  = _bk.get('geo', {})
                _sett = _bk.get('settori', {})
                c1_bk, c2_bk = st.columns(2)
                if _geo:
                    fig_geo = go.Figure(go.Pie(
                        labels=list(_geo.keys()), values=list(_geo.values()),
                        hole=0.4, textinfo='label+percent', textposition='outside',
                    ))
                    fig_geo.update_layout(height=320, showlegend=False, margin=dict(t=0,b=0,l=0,r=0))
                    with c1_bk:
                        _plotly_chart(fig_geo, _title="Distribuzione geografica",
                                      use_container_width=True, theme=None)
                if _sett:
                    fig_sett = go.Figure(go.Pie(
                        labels=list(_sett.keys()), values=list(_sett.values()),
                        hole=0.4, textinfo='label+percent', textposition='outside',
                    ))
                    fig_sett.update_layout(height=320, showlegend=False, margin=dict(t=0,b=0,l=0,r=0))
                    with c2_bk:
                        _plotly_chart(fig_sett, _title="Distribuzione settoriale",
                                      use_container_width=True, theme=None)


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
    _cat_fondi = st.session_state.get('asset_catalog', pd.DataFrame())
    if not _cat_fondi.empty and 'tipo' in _cat_fondi.columns:
        costi_map = {str(r['nome']): float(r.get('ter') or 0.02) * 100
                     for r in _cat_fondi[_cat_fondi['tipo'] == 'fondo'].to_dict('records')}
    else:
        costi_map = {f['nome']: f.get('ter', 0.02) * 100
                     for f in config.get('fondi_bancari', {}).get('titoli', [])}
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
    } for r in fondi_df.to_dict('records')],
        columns=['Fondo', 'ISIN', 'Quote', 'Quota €', 'Valore €', 'Costo fisc. €',
                 'TER stimato %', '% mantenere', 'Includi'])

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
    # Salvo per il fragment (si aggiorna ad ogni full-page rerun, non ai slider)
    st.session_state['_fondi_df_edit_attivi'] = df_attivi_f
    st.session_state['_fondi_df_edit_full']   = df_fondi_edit

    # Totali snapshot
    _aliquota = config.get('parametri', {}).get('aliquota_capital_gain',
                config.get('migrazione_fondi', {}).get('aliquota_capital_gain', 0.26))
    tot_val  = (df_attivi_f['Valore €'] * df_attivi_f['% mantenere'] / 100).sum()
    tot_cf   = (df_attivi_f['Costo fisc. €'] * df_attivi_f['% mantenere'] / 100).sum()
    tot_pv   = max(tot_val - tot_cf, 0)
    tot_tassa= tot_pv * _aliquota
    tot_netto= tot_val - tot_tassa
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Valore selezionato", f"€ {tot_val:,.0f}")
    c2.metric("Plusvalenza stimata", f"€ {tot_pv:,.0f}")
    c3.metric("Tassa latente 26%",  f"€ {tot_tassa:,.0f}")
    c4.metric("Netto se esci oggi",  f"€ {tot_netto:,.0f}")

    # Simulatori scenari (@st.fragment: solo questi blocchi rerunano sui slider)
    _frag_fondi_scenari()


# ─────────────────────────────────────────────────────────────
# SEZIONE 4: AZIONI ACCENTURE
# ─────────────────────────────────────────────────────────────
elif sezione == "📊 Azioni Accenture":
    st.title("Azioni Accenture")
    azioni_df = get_azioni()
    if not azioni_df.empty:
        r = azioni_df.iloc[0]
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Quantità",         f"{int(r['quantita']) if _has(r['quantita']) else 0} azioni")
        c2.metric("Prezzo attuale",    f"$ {r['prezzo_attuale_usd']:,.2f}" if _has(r['prezzo_attuale_usd']) else "N/D")
        c3.metric("Valore totale USD", f"$ {r['valore_attuale_usd']:,.0f}" if _has(r['valore_attuale_usd']) else "N/D")
        c4.metric("YTD",               f"{r['perf_ytd']:+.1f}%" if _has(r['perf_ytd']) else "N/D")
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
        fig_acn.update_layout(yaxis_title="USD", height=360)
        _plotly_chart(fig_acn, _title=f"Accenture (ACN) — {periodo_acn}", use_container_width=True, theme=None)

    st.subheader("Simulatore vendita — scenari worst/base/best")
    _q_acn = azioni_df.iloc[0]['quantita'] if not azioni_df.empty else None
    if (not azioni_df.empty and _has(azioni_df.iloc[0]['prezzo_attuale_usd'])
            and azioni_df.iloc[0]['prezzo_attuale_usd'] and _has(_q_acn) and int(_q_acn) >= 2):
        prezzo_ora = azioni_df.iloc[0]['prezzo_attuale_usd']
        quantita_tot = int(_q_acn)
        c1,c2 = st.columns(2)
        with c1: n_vend  = st.slider("Azioni da vendere", 1, quantita_tot, min(20, quantita_tot), key="nacn")
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

        fig_vend.update_layout(height=320, xaxis_title="Anni", yaxis_title="USD")
        _plotly_chart(fig_vend, _title=f"Valore {n_vend} azioni ACN — scenari", use_container_width=True, theme=None)

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
        with c1: imp   = st.slider("Importo mensile (€)", 100, 3000,
                                   min(max(int(config.get('allocazione', {}).get('pac_persona1_ora', 0)), 100), 3000), step=50)
        with c2: anni  = st.slider("Anni", 5, 30, 20)
        with c3: rend  = st.slider("Rendimento base (%)", 3.0, 12.0, 7.0, step=0.5)

        variabile = st.checkbox("Simula riduzione PAC con asilo nido (mag 2027)")
        _p5 = config.get('parametri', {})
        _sw5 = _p5.get('scenario_spread_worst', 0.04)
        _sb5 = _p5.get('scenario_spread_best', 0.03)
        sc_pac = simula_pac_scenari(imp, anni, rend_base=rend/100,
                                     rend_worst=max(rend/100 - _sw5, 0.01),
                                     rend_best=rend/100 + _sb5)
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
                    {'importo': config.get('allocazione', {}).get('pac_persona1_con_nido', 0),'mesi':9999}]
            df_v = simula_pac_variabile(fasi, anni, rend/100)
            fig_pac.add_trace(go.Scatter(x=df_v['anno'], y=df_v['valore'],
                                          name='Con riduzione nido',
                                          line=dict(color=COLORS['arancio'],width=2,dash='dot')))
        fig_pac.update_layout(height=400, xaxis_title="Anni", yaxis_title="€",
                               legend=dict(orientation="h",y=1.08))
        _plotly_chart(fig_pac, use_container_width=True, theme=None)

        c1,c2,c3 = st.columns(3)
        c1.metric("Base",  f"€ {sc_pac['base']['valore'].iloc[-1]:,.0f}")
        c2.metric("Worst", f"€ {sc_pac['worst']['valore'].iloc[-1]:,.0f}")
        c3.metric("Best",  f"€ {sc_pac['best']['valore'].iloc[-1]:,.0f}")

    with tab2:
        st.subheader("Migrazione fondi → ETF")
        c1,c2 = st.columns(2)
        _p_mig = config.get('parametri', {})
        _rimb_def = _p_mig.get('rimborso_annuale_default',
                    config['migrazione_fondi']['piano_annuale'][0]['rimborso_lordo'])
        with c1:
            rimb = st.slider("Rimborso annuale (€)", 10000, 50000, int(_rimb_def), step=1000)
            re   = st.slider("Rendimento ETF (%)", 4.0, 12.0,
                             _p_mig.get('rendimento_etf_base', 0.07) * 100, step=0.5)
        with c2:
            rf   = st.slider("Rendimento fondi lordo (%)", 2.0, 8.0,
                             _p_mig.get('rendimento_fondi_base', 0.04) * 100, step=0.5)
            cf   = st.slider("Costo annuo fondi (%)", 0.5, 3.0,
                             _p_mig.get('ter_default', 0.02) * 100, step=0.1)
        df_mig = simula_migrazione_fondi(config,rimb,re/100,rf/100,cf/100)
        fig_mig = go.Figure()
        fig_mig.add_trace(go.Scatter(x=df_mig['anno'],y=df_mig['scenario_etf'],
                                      name='Migrazione ETF',line=dict(color=COLORS['verde'],width=2)))
        fig_mig.add_trace(go.Scatter(x=df_mig['anno'],y=df_mig['scenario_fondi'],
                                      name='Resto nei fondi',line=dict(color=COLORS['grigio'],width=2,dash='dash')))
        fig_mig.update_layout(height=340,xaxis_title="Anni",yaxis_title="€ netto")
        _plotly_chart(fig_mig, use_container_width=True, theme=None)

    with tab3:
        st.subheader("Scenario patrimoniale completo")
        c1,c2,c3 = st.columns(3)
        with c1: pd_s = st.slider(f"PAC {_N1} (€)",500,2000,
                                  min(max(int(config.get('allocazione', {}).get('pac_persona1_ora', 0)), 500), 2000),step=100)
        with c2: pf_s = st.slider(f"PAC {_NF} (€)",100,500,
                                  min(max(int(config['allocazione'].get('pac_figlio',0) or 0), 100), 500),step=50)
        with c3: rs   = st.slider("Rendimento (%)",4.0,10.0,7.0,step=0.5)
        anni_s  = st.slider("Orizzonte (anni)",5,25,18)
        tutti_sc= st.checkbox("Mostra 3 scenari (3%/7%/10%)")

        fig_sc = None
        sc = confronta_scenari(config) if tutti_sc else None
        df_sc = None if tutti_sc else simula_scenario_completo(config,pd_s,pf_s,rs/100,anni_s)
        if (sc is not None and any(d.empty for d in sc.values())) or (df_sc is not None and df_sc.empty):
            st.info("Dati insufficienti per lo scenario: completa nascita figlio, scadenza Generali "
                    "e valori patrimoniali (wizard di configurazione).")
        elif tutti_sc:
            fig_sc = go.Figure()
            pal_sc = {'Conservativo (3%)': COLORS['arancio'],
                       'Base (7%)': COLORS['blu'], 'Ottimista (10%)': COLORS['verde']}
            for nome, df_sc_s in sc.items():
                df_sc_s = df_sc_s[df_sc_s['anno']<=anni_s]
                fig_sc.add_trace(go.Scatter(x=df_sc_s['anno'],y=df_sc_s['totale'],
                                             name=nome,line=dict(color=pal_sc[nome],width=2)))
        else:
            fig_sc = go.Figure()
            for col,nome,col_c in [('etf_persona1',f'ETF {_N1}',COLORS['blu']),
                                     ('etf_figlio',f'ETF {_NF}',COLORS['azzurro']),
                                     ('fondi','Fondi bancari',COLORS['arancio']),
                                     ('generali','Generali',COLORS['rosso'])]:
                fig_sc.add_trace(go.Scatter(x=df_sc['anno'],y=df_sc[col],
                                             name=nome,stackgroup='one',line=dict(color=col_c)))
        if fig_sc is not None:
            fig_sc.update_layout(height=400,xaxis_title="Anni",yaxis_title="€",
                                  legend=dict(orientation="h",y=1.08))
            _plotly_chart(fig_sc, use_container_width=True, theme=None)


# ─────────────────────────────────────────────────────────────
# SEZIONE 6: FIGLIO/A TIMELINE
# ─────────────────────────────────────────────────────────────
elif sezione == _SEZIONE_FIGLIO:
    st.title(f"{_NF} timeline")
    c1,c2 = st.columns(2)
    with c1: pac_f = st.slider(f"PAC mensile {_NF} (€)",0,500,
                               min(max(int(config['allocazione'].get('pac_figlio',0) or 0), 0), 500),step=50)
    with c2: rf    = st.slider("Rendimento base (%)",4.0,10.0,7.0,step=0.5)

    _p6 = config.get('parametri', {})
    sc_figlio = simula_pac_scenari(pac_f, 18, rend_base=rf/100,
                                    rend_worst=max(rf/100 - _p6.get('scenario_spread_worst', 0.04), 0.01),
                                    rend_best=rf/100 + _p6.get('scenario_spread_best', 0.03))
    # Legge nascita_figlio da session_state['params'] (già caricato), poi da config come fallback.
    # Non muta mai config (è oggetto @st.cache_data condiviso tra rerun).
    _nb = (st.session_state['params'].get('nascita_figlio')
           or config.get('date', {}).get('nascita_figlio'))
    _config_figlio = dict(config)
    if _nb:
        _config_figlio['date'] = dict(config.get('date', {}))
        _config_figlio['date']['nascita_figlio'] = str(_nb)

    df_costi = simula_costi_figlio(_config_figlio, eta_max=22)

    if df_costi.empty:
        with st.expander("📅 Imposta data di nascita", expanded=True):
            nascita_input = st.date_input("Data di nascita", value=None, min_value=date(2000,1,1), max_value=date.today())
            if st.button("💾 Salva") and nascita_input:
                from app_state import salva_param as _sp
                _nb_str = nascita_input.strftime('%Y-%m-%d')
                _sp('nascita_figlio', _nb_str)
                st.session_state['params']['nascita_figlio'] = _nb_str
                st.success("Data salvata.")
                st.rerun()

    fig_f = go.Figure()
    anni_arr = sc_figlio['base']['anno'].tolist()
    fig_f.add_trace(go.Scatter(
        x=anni_arr+anni_arr[::-1],
        y=sc_figlio['best']['valore'].tolist()+sc_figlio['worst']['valore'].tolist()[::-1],
        fill='toself', fillcolor='rgba(27,175,122,0.08)',
        line=dict(color='rgba(0,0,0,0)'), name='Range worst-best'
    ))
    for sc_key,label in [('base','Base'),('worst','Worst'),('best','Best')]:
        fig_f.add_trace(go.Scatter(
            x=sc_figlio[sc_key]['anno'], y=sc_figlio[sc_key]['valore'],
            name=label, line=dict(color=SC_COLORS[sc_key],width=2,dash=SC_DASH[sc_key])
        ))
    fig_f.add_trace(go.Scatter(x=sc_figlio['base']['anno'], y=sc_figlio['base']['versato'],
                                name='Versato', line=dict(color=COLORS['grigio'],width=1,dash='longdash')))
    for eta, lbl in {3:"Nido",6:"Elementari",11:"Medie",14:"Liceo",18:"18 anni"}.items():
        fig_f.add_vline(x=eta,line_dash="dot",line_color="#ccc",opacity=0.6)
        fig_f.add_annotation(x=eta,y=sc_figlio['best']['valore'].iloc[-1]*0.85,
                               text=lbl,showarrow=False,font=dict(size=10,color="#888"))
    _fig_f_title2 = f"Fondo {_NF} — €{pac_f}/mese · 18 anni"
    fig_f.update_layout(xaxis_title=f"Età {_NF}", yaxis_title="€", height=420,
                        legend=dict(orientation="h", y=1.08))
    _plotly_chart(fig_f, _title=_fig_f_title2, use_container_width=True, theme=None)

    c1,c2,c3 = st.columns(3)
    c1.metric("Worst (18 anni)", f"€ {sc_figlio['worst']['valore'].iloc[-1]:,.0f}")
    c2.metric("Base (18 anni)",  f"€ {sc_figlio['base']['valore'].iloc[-1]:,.0f}")
    c3.metric("Best (18 anni)",  f"€ {sc_figlio['best']['valore'].iloc[-1]:,.0f}")

    if not df_costi.empty:
        st.subheader("Costi per fascia d'età")
        fig_c = px.bar(df_costi,x='eta',y='costo_annuale',color='voce',
                        labels={'eta':'Età','costo_annuale':'€/anno','voce':'Fase'},height=300)
        _plotly_chart(fig_c, use_container_width=True, theme=None)

        df_tab = sc_figlio['base'][sc_figlio['base']['anno'].apply(lambda x: x==int(x))].copy()
        df_tab['eta'] = df_tab['anno'].astype(int)
        df_tab = df_tab.merge(df_costi[['eta','voce','costo_annuale']],on='eta',how='left')
        show = df_tab[['eta','valore','versato','voce','costo_annuale']].copy()
        show.columns = ['Età','Fondo base (€)','Versato (€)','Fase','Costo annuale (€)']
        for c in ['Fondo base (€)','Versato (€)','Costo annuale (€)']:
            show[c] = show[c].map(lambda x: f"€ {x:,.0f}")
        st.dataframe(show,use_container_width=True,hide_index=True)


# ─────────────────────────────────────────────────────────────
# SEZIONE: FINE MESE
# ─────────────────────────────────────────────────────────────
elif sezione == "📋 Fine mese":
    st.title("📋 Checklist fine mese")
    st.caption("Guida in 4 passi per validare i dati prima di chiudere il mese.")

    # Import XLS lazy — essenziale per la checklist (Passo 1)
    _run_xls_import()

    _ss_fm    = st.session_state
    _nuove_tx = _ss_fm.get('nuove_tx', 0)
    _xls_imp  = bool(_ss_fm.get('xls_importati'))
    _snap_ok  = bool(_ss_fm.get('saved_today'))
    _qm_fm    = _ss_fm.get('quote_map', {})
    _quote_ok = bool(_qm_fm)

    _completati = sum([_xls_imp, _quote_ok, _snap_ok])
    st.progress(_completati / 4, text=f"{_completati}/4 passi completati automaticamente")
    st.divider()

    # Passo 1 — Estratti XLS
    st.markdown(f"### {'✅' if _xls_imp else '⬜'} Passo 1 — Importa estratti bancari")
    if _xls_imp:
        _ntx_label = f" — {_nuove_tx} nuove transazioni" if _nuove_tx else ""
        st.success(f"Estratti importati questa sessione{_ntx_label}.")
    else:
        st.info("Deposita i file XLS in `data/input/` e clicca '🔄 Aggiorna tutto' nella sidebar.")
    st.divider()

    # Passo 2 — Quote fondi
    st.markdown(f"### {'✅' if _quote_ok else '⬜'} Passo 2 — Aggiorna quote fondi bancari")
    if _quote_ok:
        st.success(f"Quote fondi caricate ({len(_qm_fm)} fondi). "
                   "Vai in '🏦 Fondi bancari' per aggiornare il valore quota al prezzo corrente.")
    else:
        st.warning("Quote fondi non caricate — controlla la connessione al DB.")
    st.divider()

    # Passo 3 — Liquidità (sempre manuale)
    st.markdown("### ⬜ Passo 3 — Aggiorna liquidità e valori manuali")
    st.info("Vai in '🏠 Stato di famiglia' → '🔧 Aggiorna valori manuali' e inserisci i saldi bancari del mese.")
    st.divider()

    # Passo 4 — Snapshot
    st.markdown(f"### {'✅' if _snap_ok else '⬜'} Passo 4 — Snapshot patrimonio odierno")
    if _snap_ok:
        st.success("Snapshot salvato per oggi su Supabase.")
    else:
        st.warning("Snapshot non ancora salvato. Torna in '🏠 Stato di famiglia' oppure clicca '🔄 Aggiorna tutto'.")
    st.divider()

    if _completati == 4 or (_completati == 3 and _xls_imp and _quote_ok and _snap_ok):
        st.balloons()
        st.success("🎉 Tutti i passi completati — il mese è chiuso!")
    else:
        _mancanti = 4 - _completati
        st.caption(f"Il passo 3 (aggiorna liquidità) richiede sempre un'azione manuale. "
                   f"{'Manca ancora il passo 3 manuale.' if _completati >= 3 else ''}")


# ─────────────────────────────────────────────────────────────
# SEZIONE: ANALISI SPESE
# ─────────────────────────────────────────────────────────────
elif sezione == "💳 Analisi spese":
    st.title("💳 Analisi spese")
    st.caption("Categorizza le transazioni e analizza le spese con AI.")

    if _DEMO:
        st.warning("⚠️ In DEMO mode l'analisi spese è disabilitata — userebbe transazioni reali e l'API AI.")
        st.stop()

    if not db_ok:
        st.warning("Funzionalità disponibile solo con connessione al DB.")
        st.stop()

    from database import (carica_taxonomy as _carica_tax,
                          salva_taxonomy   as _salva_tax)

    # Carica taxonomy in cache di sessione (si invalida su Aggiorna tutto)
    if 'taxonomy_cache' not in st.session_state:
        st.session_state['taxonomy_cache'] = _carica_tax()

    _tax = st.session_state['taxonomy_cache']

    # ── Taxonomy manager ─────────────────────────────────────
    with st.expander("⚙️ Categorie & Tag", expanded=True):
        _tab_cat, _tab_tag = st.tabs(["🗂️ Categorie", "🏷️ Tag globali"])

        # ── Tab Categorie ─────────────────────────────────────
        with _tab_cat:
            _tax_cats = _tax.get('categorie', [])
            _n_attive = sum(bool(c.get('attiva', True)) for c in _tax_cats)
            st.caption(f"{_n_attive} categorie attive su {len(_tax_cats)} totali.")

            # Panoramica
            if _tax_cats:
                import pandas as _pd_tax
                _overview = _pd_tax.DataFrame([{
                    "": c.get('icona', '📌'),
                    "Categoria": c.get('nome', c.get('id', '')),
                    "Sottocategorie": len(c.get('sottocategorie', [])),
                    "Attiva": "✅" if c.get('attiva', True) else "⬜",
                } for c in _tax_cats])
                st.dataframe(_overview, use_container_width=True, hide_index=True)

            st.divider()

            # Seleziona categoria da modificare
            if _tax_cats:
                _cat_labels = [f"{c.get('icona','📌')} {c.get('nome', c.get('id',''))}"
                               for c in _tax_cats]
                _sel_ci = st.selectbox(
                    "Modifica categoria",
                    range(len(_tax_cats)),
                    format_func=lambda i: _cat_labels[i],
                    key="tax_sel_cat",
                )
                _sel_cat = _tax_cats[_sel_ci]

                _tcc1, _tcc2 = st.columns(2)
                with _tcc1:
                    _cur_att = _sel_cat.get('attiva', True)
                    if st.button("⬜ Disattiva" if _cur_att else "✅ Attiva",
                                 key="tax_toggle_att"):
                        _tax['categorie'][_sel_ci]['attiva'] = not _cur_att
                        st.rerun()
                with _tcc2:
                    if st.session_state.get('_tax_del_pending') == _sel_cat.get('id'):
                        st.warning("Le transazioni già categorizzate con questa categoria "
                                   "resteranno con un id non più in tassonomia. Confermi?")
                        _dc1, _dc2 = st.columns(2)
                        if _dc1.button("✅ Sì, elimina", key="tax_del_cat_ok"):
                            _tax['categorie'].pop(_sel_ci)
                            st.session_state.pop('tax_sel_cat', None)
                            st.session_state.pop('_tax_del_pending', None)
                            st.rerun()
                        if _dc2.button("Annulla", key="tax_del_cat_no"):
                            st.session_state.pop('_tax_del_pending', None)
                            st.rerun()
                    elif st.button("🗑️ Elimina categoria", key="tax_del_cat",
                                   help="Rimuove la categoria e tutte le sue sottocategorie"):
                        st.session_state['_tax_del_pending'] = _sel_cat.get('id')
                        st.rerun()

                # Sottocategorie della categoria selezionata
                st.markdown(f"**Sottocategorie di {_sel_cat.get('nome', '')}:**")
                _sel_subs = _sel_cat.get('sottocategorie', [])
                if _sel_subs:
                    for _si, _sub in enumerate(_sel_subs):
                        _sc1, _sc2 = st.columns([8, 1])
                        with _sc1:
                            st.write(f"• {_sub.get('nome', '')}")
                        with _sc2:
                            if st.button("🗑️", key=f"del_sub_{_sel_ci}_{_si}"):
                                _tax['categorie'][_sel_ci]['sottocategorie'].pop(_si)
                                st.rerun()
                else:
                    st.caption("Nessuna sottocategoria.")

                with st.form(key=f"add_sub_form_{_sel_ci}"):
                    _new_sub_nome = st.text_input("Nuova sottocategoria", max_chars=60)
                    if st.form_submit_button("+ Aggiungi"):
                        if _new_sub_nome.strip():
                            _new_sub_id = _new_sub_nome.strip().lower().replace(' ', '_')[:40]
                            if any(_s.get('id') == _new_sub_id for _s in _sel_subs):
                                st.warning("Sottocategoria già presente in questa categoria.")
                            else:
                                _tax['categorie'][_sel_ci]['sottocategorie'].append(
                                    {'id': _new_sub_id, 'nome': _new_sub_nome.strip(), 'attiva': True})
                                st.rerun()

            st.divider()
            with st.form("add_cat_form"):
                st.markdown("**+ Nuova categoria**")
                _fc1, _fc2 = st.columns([1, 4])
                with _fc1:
                    _new_ico = st.text_input("Icona", value="📌", max_chars=4,
                                             key="new_cat_ico")
                with _fc2:
                    _new_cat_nome = st.text_input("Nome categoria", max_chars=50,
                                                  key="new_cat_nome")
                if st.form_submit_button("Aggiungi categoria"):
                    if _new_cat_nome.strip():
                        _new_cid = _new_cat_nome.strip().lower().replace(' ', '_')[:40]
                        if any(_c.get('id') == _new_cid for _c in _tax_cats):
                            st.warning("Esiste già una categoria con questo nome.")
                        else:
                            _tax['categorie'].append({
                                'id': _new_cid,
                                'nome': _new_cat_nome.strip(),
                                'icona': _new_ico or '📌',
                                'attiva': True,
                                'sottocategorie': [],
                            })
                            st.rerun()

        # ── Tab Tag globali ──────────────────────────────────
        with _tab_tag:
            _tax_tags = _tax.get('tag', [])
            st.caption("Tag trasversali applicabili a qualunque categoria "
                       "(es. spese una-tantum, rimborsabili...).")
            if _tax_tags:
                for _ti, _tag_item in enumerate(_tax_tags):
                    _tg1, _tg2 = st.columns([8, 1])
                    with _tg1:
                        st.write(f"• `{_tag_item}`")
                    with _tg2:
                        if st.button("🗑️", key=f"del_tag_{_ti}"):
                            _tax['tag'].pop(_ti)
                            st.rerun()
            else:
                st.caption("Nessun tag definito.")

            with st.form("add_tag_form"):
                _new_tag_val = st.text_input("Nuovo tag", max_chars=40)
                if st.form_submit_button("+ Aggiungi tag"):
                    _new_tag_norm = _new_tag_val.strip().lower().replace(' ', '_')
                    if _new_tag_norm and _new_tag_norm not in _tax_tags:
                        _tax['tag'].append(_new_tag_norm)
                        st.rerun()

        st.divider()
        _ts1, _ts2 = st.columns(2)
        with _ts1:
            if st.button("💾 Salva categorie & tag", type="primary", key="tax_save_btn"):
                if _salva_tax(_tax):
                    st.session_state.pop('taxonomy_cache', None)
                    st.success("✅ Taxonomy salvata.")
                    st.rerun()
                else:
                    st.error("Errore durante il salvataggio.")
        with _ts2:
            if st.button("↩️ Ripristina default", key="tax_reset_btn"):
                from database import _DEFAULT_TAXONOMY as _deftax
                import copy as _copy_tax
                st.session_state['taxonomy_cache'] = _copy_tax.deepcopy(_deftax)
                st.rerun()

    # ── Profilo famiglia ──────────────────────────────────────
    from database import (carica_profilo_famiglia as _cpf,
                          salva_profilo_famiglia  as _spf,
                          get_profilo_attivo      as _gpa)

    if 'profilo_famiglia_cache' not in st.session_state:
        st.session_state['profilo_famiglia_cache'] = _cpf()

    _pf_storia = st.session_state['profilo_famiglia_cache']
    _pf_attivo = _gpa(_pf_storia)

    _TIPI_FAM = {
        "single":              "Single",
        "coppia":              "Coppia senza figli",
        "coppia_con_figlio":   "Coppia con figlio/i",
        "famiglia_numerosa":   "Famiglia numerosa (3+ figli)",
    }
    _AREE_GEO = {
        "nord":   "Nord Italia",
        "centro": "Centro Italia",
        "sud":    "Sud Italia",
        "estero": "Estero",
    }
    _FASCE_ETA = ["25-30", "30-35", "35-40", "40-45", "45-50", "50-55", "55-60", "60+"]

    with st.expander("👨‍👩‍👧 Profilo famiglia", expanded=not bool(_pf_attivo)):
        st.caption("Il profilo è usato per il confronto benchmark ISTAT nelle analisi AI.")

        # Profilo corrente
        if _pf_attivo:
            _pf_c1, _pf_c2, _pf_c3, _pf_c4 = st.columns(4)
            _pf_c1.metric("Tipo", _TIPI_FAM.get(_pf_attivo.get('tipo_famiglia',''), '—'))
            _pf_c2.metric("Area", _AREE_GEO.get(_pf_attivo.get('area_geografica',''), '—'))
            _pf_c3.metric("Adulti", _pf_attivo.get('num_adulti', '—'))
            _pf_c4.metric("Figli", _pf_attivo.get('num_figli', '—'))
            st.caption(f"Attivo dal: {_pf_attivo.get('dal','—')} | "
                       f"Fascia età: {_pf_attivo.get('fascia_eta','—')}")
        else:
            st.info("Nessun profilo configurato. Compila il form per iniziare.")

        st.divider()

        # Scelta modalità
        _pf_mode = st.radio(
            "Modalità",
            ["✏️ Modifica profilo corrente", "🆕 Nuova decorrenza da oggi"],
            horizontal=True,
            key="pf_mode_radio",
            help="'Modifica' aggiorna i dati in-place. "
                 "'Nuova decorrenza' chiude il periodo corrente e ne apre uno nuovo.",
        ) if _pf_attivo else "✏️ Modifica profilo corrente"

        with st.form("profilo_famiglia_form"):
            _pff1, _pff2 = st.columns(2)
            with _pff1:
                _pf_tipo = st.selectbox(
                    "Tipo famiglia",
                    list(_TIPI_FAM.keys()),
                    format_func=lambda k: _TIPI_FAM[k],
                    index=list(_TIPI_FAM.keys()).index(
                        _pf_attivo.get('tipo_famiglia', 'coppia')) if _pf_attivo else 0,
                    key="pf_tipo_sel",
                )
                _pf_adulti = st.number_input("N. adulti", min_value=1, max_value=6,
                                             value=int(_pf_attivo.get('num_adulti', 2)) if _pf_attivo else 2,
                                             key="pf_adulti_inp")
                _pf_fascia = st.selectbox(
                    "Fascia età (persona1)",
                    _FASCE_ETA,
                    index=_FASCE_ETA.index(_pf_attivo['fascia_eta'])
                          if _pf_attivo and _pf_attivo.get('fascia_eta') in _FASCE_ETA else 2,
                    key="pf_fascia_sel",
                )
            with _pff2:
                _pf_area = st.selectbox(
                    "Area geografica",
                    list(_AREE_GEO.keys()),
                    format_func=lambda k: _AREE_GEO[k],
                    index=list(_AREE_GEO.keys()).index(
                        _pf_attivo.get('area_geografica', 'nord')) if _pf_attivo else 0,
                    key="pf_area_sel",
                )
                _pf_figli = st.number_input("N. figli", min_value=0, max_value=8,
                                            value=int(_pf_attivo.get('num_figli', 0)) if _pf_attivo else 0,
                                            key="pf_figli_inp")

            if st.form_submit_button("💾 Salva profilo", type="primary"):
                _new_entry = {
                    'tipo_famiglia':   _pf_tipo,
                    'area_geografica': _pf_area,
                    'num_adulti':      int(_pf_adulti),
                    'num_figli':       int(_pf_figli),
                    'fascia_eta':      _pf_fascia,
                    'dal':             date.today().isoformat(),
                    'al':              None,
                }
                if _pf_attivo and _pf_mode == "✏️ Modifica profilo corrente":
                    # Aggiorna il periodo corrente senza chiuderlo
                    _new_entry['dal'] = _pf_attivo.get('dal', date.today().isoformat())
                    _pf_storia_new = [e for e in _pf_storia
                                      if e.get('dal') != _pf_attivo.get('dal')]
                    _pf_storia_new.append(_new_entry)
                else:
                    # Chiude il periodo attivo e apre uno nuovo
                    from datetime import timedelta as _td
                    _ieri = (date.today() - _td(days=1)).isoformat()
                    _pf_storia_new = []
                    for _e in _pf_storia:
                        _da_chiudere = bool(_pf_attivo) and _e.get('al') is None \
                                       and _e.get('dal') == _pf_attivo.get('dal')
                        if _da_chiudere and _e.get('dal', '') >= date.today().isoformat():
                            continue   # periodo iniziato oggi: sostituito, non chiuso (evita al < dal)
                        _pf_storia_new.append({**_e, 'al': _ieri} if _da_chiudere else _e)
                    _pf_storia_new.append(_new_entry)

                if _spf(_pf_storia_new):
                    st.session_state['profilo_famiglia_cache'] = sorted(
                        _pf_storia_new, key=lambda x: x.get('dal', ''), reverse=True)
                    st.success("✅ Profilo salvato.")
                    st.rerun()
                else:
                    st.error("Errore durante il salvataggio.")

        # Storico periodi
        if len(_pf_storia) > 1:
            with st.expander("📅 Storico periodi"):
                import pandas as _pd_pf
                _hist_rows = []
                for _e in sorted(_pf_storia, key=lambda x: x.get('dal',''), reverse=True):
                    _hist_rows.append({
                        "Dal": _e.get('dal',''),
                        "Al":  _e.get('al') or "corrente",
                        "Tipo": _TIPI_FAM.get(_e.get('tipo_famiglia',''), _e.get('tipo_famiglia','')),
                        "Area": _AREE_GEO.get(_e.get('area_geografica',''), _e.get('area_geografica','')),
                        "Adulti": _e.get('num_adulti',''),
                        "Figli": _e.get('num_figli',''),
                        "Fascia età": _e.get('fascia_eta',''),
                    })
                st.dataframe(_pd_pf.DataFrame(_hist_rows), use_container_width=True, hide_index=True)

    # ── Scrubbing anonimizzazione ─────────────────────────────
    from database import (carica_nomi_anonimizzare as _cna,
                          salva_nomi_anonimizzare  as _sna,
                          carica_tx_senza_categoria as _ctx_sc)

    if 'nomi_anonimiz_cache' not in st.session_state:
        st.session_state['nomi_anonimiz_cache'] = _cna()

    _nomi_anon = st.session_state['nomi_anonimiz_cache']

    with st.expander("🔒 Privacy & Anonimizzazione", expanded=not bool(_nomi_anon)):
        st.caption(
            "Questi nomi vengono sostituiti con `[NOME]` in ogni testo inviato all'AI. "
            "Aggiungi nomi propri, ragioni sociali o qualsiasi stringa da oscurare."
        )

        # Lista corrente
        if _nomi_anon:
            for _ni, _nn in enumerate(_nomi_anon):
                _nc1, _nc2 = st.columns([8, 1])
                with _nc1:
                    st.write(f"• `{_nn}`")
                with _nc2:
                    if st.button("🗑️", key=f"del_nome_{_ni}"):
                        _nuovi_n = [n for j, n in enumerate(_nomi_anon) if j != _ni]
                        if _sna(_nuovi_n):
                            st.session_state['nomi_anonimiz_cache'] = _nuovi_n
                            st.rerun()
                        else:
                            st.error("Errore salvataggio: nome non rimosso.")
        else:
            st.info("Nessun nome configurato. Aggiungi almeno i nomi dei membri della famiglia.")

        with st.form("add_nome_anon_form"):
            _new_nome = st.text_input("Nome / stringa da oscurare", max_chars=100,
                                      placeholder="es. Mario, Rossi, MARIO ROSSI")
            if st.form_submit_button("+ Aggiungi"):
                _new_nome = _new_nome.strip()
                if _new_nome and _new_nome not in _nomi_anon:
                    _nuovi_n = list(_nomi_anon) + [_new_nome]
                    if _sna(_nuovi_n):
                        st.session_state['nomi_anonimiz_cache'] = _nuovi_n
                        st.rerun()
                    else:
                        st.error("Errore salvataggio: nome non aggiunto.")
                elif _new_nome in _nomi_anon:
                    st.warning("Nome già presente.")

        # Preview scrubbing
        if _nomi_anon:
            st.divider()
            st.markdown("**Preview scrubbing**")
            _prev_input = st.text_area(
                "Testo di prova (es. una causale bancaria)",
                placeholder="PAGAMENTO MARIO ROSSI IT60X0542811101000000123456",
                key="scrub_preview_txt",
                height=80,
            )
            if _prev_input.strip():
                from scrubbing import scrub as _scrub_fn
                _prev_out = _scrub_fn(_prev_input, _nomi_anon)
                st.code(_prev_out, language=None)

    # ── Giroconti sospetti ────────────────────────────────────
    with st.expander("🔀 Giroconti sospetti (amount-based matching)"):
        st.caption(
            "Addebiti e accrediti di pari importo su conti diversi entro 3 giorni "
            "vengono proposti come giroconti interni. Confermali per escluderli "
            "dall'analisi spese."
        )

        if st.button("🔍 Cerca giroconti", key="cerca_giroconti_btn"):
            st.session_state.pop('tx_senza_cat_cache', None)

        if 'tx_senza_cat_cache' not in st.session_state:
            with st.spinner("Caricamento transazioni..."):
                st.session_state['tx_senza_cat_cache'] = _ctx_sc(limit=500)

        _tx_sc = st.session_state['tx_senza_cat_cache']

        if not _tx_sc:
            st.info("Nessuna transazione senza categoria nel DB. "
                    "Importa gli estratti conto dalla sezione 'Fine mese'.")
        else:
            from scrubbing import find_giroconti as _find_gc
            _gc_candidates = _find_gc(_tx_sc)

            if not _gc_candidates:
                st.success(f"Nessun giroconto rilevato su {len(_tx_sc)} transazioni non categorizzate.")
            else:
                st.info(f"Trovati **{len(_gc_candidates)}** candidati su {len(_tx_sc)} transazioni. "
                        "Conferma quelli corretti per categorizzarli come 'Trasferimento interno'.")

                from database import aggiorna_categoria_tx as _agg_cat
                _gc_confermati = 0
                for _gc in _gc_candidates:
                    with st.container(border=True):
                        _gca, _gcb, _gcc = st.columns([3, 3, 2])
                        with _gca:
                            st.markdown(f"**Addebito** — {_gc['data_addebito']}")
                            st.write(f"Conto: `{_gc['conto_da'] or '—'}`")
                            st.caption(_gc['desc_addebito'][:80] or '—')
                        with _gcb:
                            st.markdown(f"**Accredito** — {_gc['data_accredito']}")
                            st.write(f"Conto: `{_gc['conto_a'] or '—'}`")
                            st.caption(_gc['desc_accredito'][:80] or '—')
                        with _gcc:
                            st.metric("Importo", f"€ {_gc['importo']:,.2f}")
                            st.caption(f"Δ {_gc['delta_giorni']} gg")
                            if st.button("✅ Conferma giroconto",
                                         key=f"gc_ok_{_gc['hash_addebito']}"):
                                _ok1 = _agg_cat(_gc['hash_addebito'],
                                                'trasferimento_interno',
                                                sottocategoria=None, tag=[], confermata=True)
                                _ok2 = _agg_cat(_gc['hash_accredito'],
                                                'trasferimento_interno',
                                                sottocategoria=None, tag=[], confermata=True)
                                if _ok1 and _ok2:
                                    _gc_confermati += 1
                                    st.session_state.pop('tx_senza_cat_cache', None)
                                    st.success("Categorizzato come trasferimento interno.")
                                    st.rerun()

    # ── Categorizzazione AI ───────────────────────────────────
    def _ui_categorizzazione_ai(tx_list, *, ai_cache_key, key_prefix, editor_key,
                                clear_keys, con_tag, nomi, tax, titolo_review):
        """Flusso condiviso: scrub → Claude → tabella di revisione → salvataggio bulk."""
        from scrubbing import scrub_batch, sospetta_pii
        from ai_categorize import categorizza_batch, MODEL_NAME
        from database import aggiorna_categoria_tx_bulk
        import pandas as pd

        nomi_eff = _nomi_effettivi(nomi)

        desc_anon = scrub_batch([t.get('descrizione') or '' for t in tx_list], nomi_eff)
        sospette = [sospetta_pii(d) for d in desc_anon]
        n_sosp = sum(sospette)

        with st.expander(f"🔍 Anteprima: testo che verrà inviato a Claude ({len(tx_list)} tx)",
                         expanded=bool(n_sosp)):
            st.caption(
                "Questo è l'unico testo delle causali che lascia l'app (insieme a importo e "
                "hash). Oltre ai nomi configurati vengono oscurati: persone dopo «a favore di», "
                "«ordinante», «beneficiario», email, IBAN, carte, telefoni, numeri lunghi. "
                "Per bonifici e giroconti si invia solo il tipo di operazione e la causale.")
            st.dataframe(
                pd.DataFrame({
                    '⚠️': ['⚠️' if s else '' for s in sospette],
                    'Originale (solo qui)': [(t.get('descrizione') or '')[:120]
                                             for t in tx_list],
                    'Inviato a Claude': [d[:120] for d in desc_anon]}),
                hide_index=True, use_container_width=True,
                column_config={'⚠️': st.column_config.Column(width=50),
                               'Originale (solo qui)': st.column_config.Column(width=420),
                               'Inviato a Claude': st.column_config.Column(width=420)})
            invia_sosp = False
            if n_sosp:
                st.warning(
                    f"⚠️ {n_sosp} causali contengono parole che sembrano nomi propri e NON "
                    "vengono inviate. Aggiungi quei nomi in 🔒 Privacy per includerle, oppure "
                    "spunta qui sotto per inviarle comunque.")
                invia_sosp = st.checkbox("Invia comunque le righe segnalate",
                                         key=f"{key_prefix}_invia_sosp")

        idx_invio = [i for i, s in enumerate(sospette) if invia_sosp or not s] if n_sosp \
            else list(range(len(tx_list)))

        col_b, col_c = st.columns([2, 5])
        with col_b:
            run_ai = st.button("✨ Analizza con AI", type="primary",
                               key=f"{key_prefix}_ai_btn",
                               help="Invia le causali anonimizzate a Claude Haiku")
        with col_c:
            st.caption(f"{len(idx_invio)} tx da inviare su {len(tx_list)} · "
                       f"modello: {MODEL_NAME}")

        if run_ai:
            # blocco di sicurezza: nessun nome oscurabile deve comparire nel testo inviato
            _residui = [n for n in nomi_eff if n.strip() and any(
                n.strip().lower() in desc_anon[i].lower() for i in idx_invio)]
            if _residui:
                st.error("⛔ Invio bloccato: la causale contiene ancora un nome da anonimizzare.")
                return
            tx_input = [{'hash_tx': tx_list[i]['hash_tx'],
                         'descrizione_anonimizzata': desc_anon[i],
                         'importo': float(tx_list[i].get('importo', 0))}
                        for i in idx_invio]
            with st.spinner(f"Analisi AI in corso ({len(tx_input)} transazioni)..."):
                try:
                    ris = categorizza_batch(tx_input, tax, nomi_eff)
                    st.session_state[ai_cache_key] = {r['hash_tx']: r for r in ris}
                    st.success(f"✅ Ricevuti suggerimenti per {len(ris)} transazioni.")
                except RuntimeError as err:
                    st.error(str(err))

        ai_map = st.session_state.get(ai_cache_key, {})

        # Etichette leggibili: "🏠 Casa", "Casa › Utenze", "Una tantum"
        attive = [c for c in tax.get('categorie', []) if c.get('attiva', True)]
        cat_label = {c['id']: f"{c.get('icona', '')} {c['nome']}".strip() for c in attive}
        label2cat = {v: k for k, v in cat_label.items()}
        sub_label, label2sub = {}, {}
        cat2subs = {}
        for c in attive:
            cat2subs[c['id']] = []
            for s in c.get('sottocategorie', []):
                if s.get('attiva', True):
                    lab = f"{c['nome']} › {s['nome']}"
                    sub_label[(c['id'], s['id'])] = lab
                    label2sub[lab] = (c['id'], s['id'])
                    cat2subs[c['id']].append(s['id'])
        tag_label = {t: str(t).replace('_', ' ').capitalize() for t in tax.get('tag', [])}
        label2tag = {v: k for k, v in tag_label.items()}

        righe = []
        for t in tx_list:
            sug = ai_map.get(t['hash_tx'], {})
            cid = sug.get('categoria') or ''
            riga = {'hash_tx': t['hash_tx'],
                    'Data': str(t.get('data', '')),
                    'Descrizione': (t.get('descrizione') or '')[:120],
                    'Importo €': float(t.get('importo', 0)),
                    'Categoria': cat_label.get(cid, ''),
                    'Sottocategoria': sub_label.get((cid, sug.get('sottocategoria')), '')}
            if con_tag:
                riga['Tag'] = ''
            riga['Conferma'] = False
            righe.append(riga)
        df = pd.DataFrame(righe)
        if df.empty:
            return

        cfg = {
            'hash_tx': None,  # nascosta, serve solo per il salvataggio
            'Data': st.column_config.Column(disabled=True, width=95),
            'Descrizione': st.column_config.Column(disabled=True, width=380),
            'Importo €': st.column_config.NumberColumn(
                disabled=True, format="€ %.2f", width=95),
            'Categoria': st.column_config.SelectboxColumn(
                options=list(cat_label.values()), width=230),
            'Sottocategoria': st.column_config.SelectboxColumn(
                options=[''] + list(sub_label.values()), width=300),
            'Conferma': st.column_config.CheckboxColumn(width=85),
        }
        if con_tag:
            cfg['Tag'] = st.column_config.SelectboxColumn(
                options=[''] + list(tag_label.values()), width=160)
        st.markdown(titolo_review)
        st.caption("Suggerimento: passa il mouse sulla tabella e usa l'icona ⛶ in alto a destra "
                   "per vederla a schermo intero.")
        edited = st.data_editor(df, column_config=cfg, hide_index=True,
                                use_container_width=True, key=editor_key,
                                num_rows='fixed', row_height=38)
        n_sel = int(edited['Conferma'].sum())
        st.caption(f"{n_sel} righe selezionate per il salvataggio.")

        if st.button("💾 Salva categorizzazioni", type="primary",
                     key=f"{key_prefix}_salva_btn", disabled=n_sel == 0):
            da_salvare = []
            for row in edited[edited['Conferma']].to_dict('records'):
                cat_id = label2cat.get(row.get('Categoria') or '')
                sub = None
                coppia = label2sub.get(row.get('Sottocategoria') or '')
                if coppia:
                    if cat_id is None:
                        cat_id = coppia[0]  # categoria dedotta dalla sottocategoria
                    if coppia[0] == cat_id:
                        sub = coppia[1]     # altrimenti incoerente → scartata
                tag_val = label2tag.get(row.get('Tag') or '') if con_tag else None
                da_salvare.append({'hash_tx': row['hash_tx'], 'categoria': cat_id or 'altro',
                                   'sottocategoria': sub,
                                   'tag': [tag_val] if tag_val else []})
            salvate, errori = aggiorna_categoria_tx_bulk(da_salvare)
            for k in clear_keys:
                st.session_state.pop(k, None)
            if errori == 0:
                st.success(f"✅ {salvate} transazioni categorizzate e salvate.")
            else:
                st.warning(f"Salvate {salvate}, errori: {errori}.")
            st.rerun()

    with st.expander("✨ Categorizzazione AI transazioni"):
        st.caption(
            "Claude analizza le causali anonimizzate e suggerisce categoria e sottocategoria. "
            "Rivedi i suggerimenti nel tabellone, correggi dove necessario e salva."
        )

        # Verifica API key
        _ai_key_ok = bool(st.secrets.get('ANTHROPIC_API_KEY', ''))
        if not _ai_key_ok:
            st.warning(
                "🔑 `ANTHROPIC_API_KEY` non configurata. "
                "Aggiungila in `.streamlit/secrets.toml` per abilitare la categorizzazione AI."
            )

        # Verifica nomi anonimizzazione configurati
        if not _nomi_anon:
            st.warning(
                "⚠️ Nessun nome configurato nell'expander 'Privacy & Anonimizzazione'. "
                "Aggiungi i nomi dei membri della famiglia prima di procedere."
            )

        # Carica (o riusa) transazioni senza categoria
        if 'tx_senza_cat_cache' not in st.session_state:
            with st.spinner("Caricamento transazioni..."):
                st.session_state['tx_senza_cat_cache'] = _ctx_sc(limit=500)

        _tx_da_cat = [t for t in st.session_state.get('tx_senza_cat_cache', [])
                      if float(t.get('importo', 0)) < 0]

        st.metric("Transazioni da categorizzare (uscite)", len(_tx_da_cat))

        if not _tx_da_cat:
            st.info("Nessuna uscita senza categoria. "
                    "Importa estratti conto dalla sezione '📋 Fine mese'.")
        elif _ai_key_ok and _nomi_anon:
            _ui_categorizzazione_ai(
                _tx_da_cat, ai_cache_key='ai_suggest_cache', key_prefix='cat',
                editor_key='cat_editor',
                clear_keys=('tx_senza_cat_cache', 'ai_suggest_cache'),
                con_tag=True, nomi=_nomi_anon, tax=_tax,
                titolo_review="**Revisiona e correggi i suggerimenti AI:**")

    # ── Analisi AI spese ─────────────────────────────────────
    from database import carica_spese_categorizzate as _csc2
    from simulator import calcola_analisi_spese      as _calc_analisi
    from ai_categorize import analizza_spese_ai      as _analisi_ai

    with st.expander("📊 Analisi AI spese"):
        st.caption(
            "KPI mensili, semafori per categoria vs media 12 mesi e benchmark ISTAT, "
            "e suggerimenti AI concreti per ottimizzare le spese."
        )

        if not _nomi_anon:
            st.warning("⚠️ Prima configura almeno un nome nella sezione 🔒 Privacy.")
        else:
            if st.button("🔄 Ricarica dati spese", key="ricarica_spese_btn"):
                st.session_state.pop('spese_analisi_cache', None)
                st.session_state.pop('spese_report_cache', None)
                st.rerun()

            if 'spese_analisi_cache' not in st.session_state:
                with st.spinner("Caricamento transazioni categorizzate..."):
                    st.session_state['spese_analisi_cache'] = _csc2(limit=2000)

            _tx_cat = st.session_state.get('spese_analisi_cache', [])

            if not _tx_cat:
                st.info("Nessuna transazione categorizzata. Prima usa ✨ Categorizzazione AI.")
            else:
                _bench_cfg = config.get('benchmark_istat', {})
                _analisi   = _calc_analisi(_tx_cat, _bench_cfg)
                _kpi       = _analisi['kpi']
                _sem       = _analisi['semafori']
                _n_mesi    = _analisi['n_mesi_storico']

                # ── KPI cards ────────────────────────────────
                _col1, _col2, _col3, _col4 = st.columns(4)
                with _col1:
                    st.metric("💸 Spese mese", f"€{_kpi['totale_mese']:,.0f}")
                with _col2:
                    _dp = _kpi['delta_pct']
                    st.metric(
                        "📊 vs media 12m",
                        f"{_dp:+.1f}%",
                        delta=f"{_dp:+.1f}%",
                        delta_color="inverse",
                    )
                with _col3:
                    st.metric("🏆 Top categoria", _kpi['top_categoria'])
                with _col4:
                    st.metric("📅 Mesi storico", str(_n_mesi))

                st.divider()

                # ── Tabella semafori ──────────────────────────
                st.markdown("**🚦 Spese per categoria**")
                if _sem:
                    _df_sem = pd.DataFrame(_sem)
                    _df_sem['Mese (€)']     = _df_sem['mese_eur'].apply(lambda x: f"€{x:,.0f}")
                    _df_sem['Media 12m (€)'] = _df_sem['media_12m_eur'].apply(lambda x: f"€{x:,.0f}")
                    _df_sem['ISTAT (€)']    = _df_sem['benchmark_eur'].apply(
                        lambda x: f"€{x:,.0f}" if x is not None else 'N/D'
                    )
                    st.dataframe(
                        _df_sem[['semaforo', 'categoria', 'Mese (€)', 'trend',
                                 'Media 12m (€)', 'ISTAT (€)']].rename(columns={
                            'semaforo':  '',
                            'categoria': 'Categoria',
                            'trend':     'Trend',
                        }),
                        use_container_width=True,
                        hide_index=True,
                    )

                st.divider()

                # ── Analisi AI ───────────────────────────────
                st.markdown("**✨ Analisi AI**")
                _ai_key_ok2 = bool(st.secrets.get('ANTHROPIC_API_KEY', ''))
                if not _ai_key_ok2:
                    st.warning(
                        "⚠️ `ANTHROPIC_API_KEY` non configurata — "
                        "aggiungi la chiave in `.streamlit/secrets.toml` per attivare l'analisi AI."
                    )
                else:
                    if st.button("✨ Genera analisi AI", key="genera_analisi_ai_btn"):
                        st.session_state.pop('spese_report_cache', None)
                        with st.spinner("Claude sta analizzando le spese..."):
                            try:
                                _report = _analisi_ai(_sem, _kpi, _nomi_effettivi(_nomi_anon))
                                st.session_state['spese_report_cache'] = _report
                            except RuntimeError as _ae:
                                st.error(f"Errore analisi AI: {_ae}")
                        st.rerun()

                    _report = st.session_state.get('spese_report_cache')
                    if _report:
                        st.markdown(_report)

    # ── Batch retroattivo ─────────────────────────────────────
    from database import (carica_riepilogo_mensile_senza_categoria as _crm,
                          carica_tx_senza_categoria_per_mese       as _ctx_mese)

    with st.expander("📅 Batch retroattivo — mesi precedenti"):
        st.caption(
            "Categorizza le uscite non ancora elaborate per ogni mese. "
            "Seleziona un mese, analizza con AI, rivedi e salva."
        )

        if not _nomi_anon:
            st.warning("⚠️ Prima configura almeno un nome nella sezione 🔒 Privacy.")
        elif not bool(st.secrets.get('ANTHROPIC_API_KEY', '')):
            st.warning(
                "⚠️ `ANTHROPIC_API_KEY` non configurata — "
                "necessaria per la categorizzazione AI."
            )
        else:
            if st.button("🔄 Aggiorna riepilogo mesi", key="retro_ricarica_btn"):
                for _rk in ('batch_retro_riepilogo_cache', 'batch_retro_tx_cache',
                             'batch_retro_ai_cache', '_retro_last_sel'):
                    st.session_state.pop(_rk, None)
                st.rerun()

            if 'batch_retro_riepilogo_cache' not in st.session_state:
                with st.spinner("Caricamento riepilogo mesi..."):
                    st.session_state['batch_retro_riepilogo_cache'] = _crm()

            _riepilogo = st.session_state.get('batch_retro_riepilogo_cache', [])

            if not _riepilogo:
                st.success("✅ Nessuna uscita non categorizzata nei mesi precedenti.")
            else:
                import pandas as _pd_retro
                _df_riepilogo = _pd_retro.DataFrame(_riepilogo)[['label', 'conteggio']].rename(
                    columns={'label': 'Mese', 'conteggio': 'Uscite da categorizzare'}
                )
                st.dataframe(_df_riepilogo, use_container_width=True, hide_index=True)

                _labels = [r['label'] for r in _riepilogo]
                _sel_label = st.selectbox("Seleziona mese da elaborare", _labels,
                                          key="retro_mese_sel")
                _sel_mese_row = next((r for r in _riepilogo if r['label'] == _sel_label), None)

                if _sel_mese_row:
                    _r_anno = _sel_mese_row['anno']
                    _r_mese = _sel_mese_row['mese']
                    _r_key  = f"{_r_anno}_{_r_mese}"

                    # Invalida cache se mese cambiato
                    if st.session_state.get('_retro_last_sel') != _r_key:
                        st.session_state.pop('batch_retro_tx_cache', None)
                        st.session_state.pop('batch_retro_ai_cache', None)
                        st.session_state['_retro_last_sel'] = _r_key

                    if 'batch_retro_tx_cache' not in st.session_state:
                        with st.spinner(f"Caricamento transazioni {_sel_label}..."):
                            st.session_state['batch_retro_tx_cache'] = _ctx_mese(
                                _r_anno, _r_mese)

                    _tx_retro = st.session_state.get('batch_retro_tx_cache', [])
                    st.metric(f"Uscite — {_sel_label}", len(_tx_retro))

                    if not _tx_retro:
                        st.info("Nessuna uscita non categorizzata per questo mese.")
                    else:
                        _ui_categorizzazione_ai(
                            _tx_retro, ai_cache_key='batch_retro_ai_cache',
                            key_prefix='retro', editor_key=f'retro_editor_{_r_key}',
                            clear_keys=('batch_retro_riepilogo_cache', 'batch_retro_tx_cache',
                                        'batch_retro_ai_cache', 'tx_senza_cat_cache',
                                        'spese_analisi_cache'),
                            con_tag=False, nomi=_nomi_anon, tax=_tax,
                            titolo_review=f"**Revisiona i suggerimenti — {_sel_label}:**")


# ─────────────────────────────────────────────────────────────
# SEZIONE: GESTIONE ASSET
# ─────────────────────────────────────────────────────────────
elif sezione == "⚙️ Gestione Asset":
    st.title("Gestione Asset")
    st.caption("Aggiungi, modifica o rimuovi ETF, fondi e azioni. Le modifiche si riflettono subito in tutte le sezioni dell'app.")

    if _DEMO:
        st.warning("⚠️ In DEMO mode la gestione asset è disabilitata — nessun dato viene scritto.")
        st.info("Nel profilo reale questa sezione permette di aggiungere, modificare ed eliminare ETF, fondi e azioni dal catalogo.")
        st.stop()

    if not db_ok:
        st.error("DB non disponibile — impossibile gestire gli asset.")
        st.stop()

    # Usa il catalog già in cache (sessione); si ricarica solo dopo modifiche (pop + rerun)
    cat_df = st.session_state.get('asset_catalog', pd.DataFrame())
    if cat_df.empty:
        cat_df = get_asset_catalog()
        st.session_state['asset_catalog'] = cat_df

    if cat_df.empty:
        st.info("Nessun asset nel catalogo. Aggiungi il primo qui sotto.")
        cat_df = pd.DataFrame(columns=['isin','nome','tipo','ticker_yf','ticker_bi',
                                        'fallback_tickers','ter','proprietario',
                                        'stato','valore_quota_ref','data_ref',
                                        'valore_iniziale','note'])

    tab_fondi, tab_etf, tab_azioni, tab_aggiungi = st.tabs(
        ["🏦 Fondi", "📈 ETF", "📊 Azioni", "➕ Aggiungi"])

    # ── TAB FONDI ──────────────────────────────────────────────
    with tab_fondi:
        fondi_df = cat_df[cat_df['tipo'] == 'fondo'] if not cat_df.empty else pd.DataFrame()
        if fondi_df.empty:
            st.info("Nessun fondo nel catalogo.")
        else:
            disp_cols = ['isin','nome','ter','valore_quota_ref','data_ref','note']
            disp_cols = [c for c in disp_cols if c in fondi_df.columns]
            st.dataframe(fondi_df[disp_cols], use_container_width=True, hide_index=True)

        st.subheader("Modifica / elimina fondo")
        _f_name = fondi_df.set_index('isin')['nome'].to_dict() if not fondi_df.empty else {}
        isin_sel = st.selectbox("Seleziona fondo",
                                 options=fondi_df['isin'].tolist() if not fondi_df.empty else [],
                                 format_func=lambda x: f"{_f_name.get(x, x)} ({x})",
                                 key="sel_fondo")
        if isin_sel and not fondi_df.empty:
            row = fondi_df[fondi_df['isin'] == isin_sel].iloc[0]
            with st.form("edit_fondo"):
                c1, c2 = st.columns(2)
                nome_f  = c1.text_input("Nome", value=str(row.get('nome','')))
                ter_f   = c2.number_input("TER %", value=float(row.get('ter') or 0.02) * 100,
                                           min_value=0.0, max_value=5.0, step=0.1, format="%.2f")
                vqr     = c1.number_input("Quota ref (€)", value=float(row.get('valore_quota_ref') or 0),
                                           min_value=0.0, step=0.01)
                dr      = c2.text_input("Data ref (YYYY-MM-DD)", value=str(row.get('data_ref') or ''))
                note_f  = st.text_input("Note", value=str(row.get('note') or ''))
                col_s, col_d = st.columns(2)
                salva_btn = col_s.form_submit_button("💾 Salva modifiche")
                elim_btn  = col_d.form_submit_button("🗑️ Elimina asset", type="secondary")
            if salva_btn:
                if dr:
                    try:
                        date.fromisoformat(dr)
                    except ValueError:
                        st.error("Data ref non valida. Usa il formato YYYY-MM-DD (es. 2026-09-25).")
                        st.stop()
                ok = salva_asset({
                    'isin': isin_sel, 'nome': nome_f, 'tipo': 'fondo',
                    'ter': ter_f / 100, 'valore_quota_ref': vqr,  # fondi: frazione (0.02 = 2%)
                    'data_ref': dr or None,
                    'note': note_f or None,
                    'ticker_yf': _nn(row.get('ticker_yf')),
                    'fallback_tickers': row.get('fallback_tickers') if isinstance(row.get('fallback_tickers'), list) else [],
                    'proprietario': row.get('proprietario','persona1'),
                    'stato': row.get('stato','attivo'),
                })
                if ok:
                    st.success("Fondo aggiornato.")
                    st.session_state.pop('asset_catalog', None)
                    st.rerun()
                else:
                    st.error("Errore salvataggio.")
            if elim_btn:
                if rimuovi_asset(isin_sel):
                    st.success(f"{isin_sel} rimosso dal catalogo.")
                    st.session_state.pop('asset_catalog', None)
                    st.rerun()
                else:
                    st.error("Errore eliminazione.")

    # ── TAB ETF ────────────────────────────────────────────────
    with tab_etf:
        etf_df = cat_df[cat_df['tipo'] == 'etf'] if not cat_df.empty else pd.DataFrame()
        if etf_df.empty:
            st.info("Nessun ETF nel catalogo.")
        else:
            disp = ['isin','nome','ticker_yf','ticker_bi','ter','proprietario','stato','note']
            disp = [c for c in disp if c in etf_df.columns]
            st.dataframe(etf_df[disp], use_container_width=True, hide_index=True)

        st.subheader("Modifica / elimina ETF")
        _e_name = etf_df.set_index('isin')['nome'].to_dict() if not etf_df.empty else {}
        isin_etf = st.selectbox("Seleziona ETF",
                                  options=etf_df['isin'].tolist() if not etf_df.empty else [],
                                  format_func=lambda x: f"{_e_name.get(x, x)} ({x})",
                                  key="sel_etf")
        if isin_etf and not etf_df.empty:
            row = etf_df[etf_df['isin'] == isin_etf].iloc[0]
            with st.form("edit_etf"):
                c1, c2, c3 = st.columns(3)
                nome_e  = c1.text_input("Nome", value=str(row.get('nome','')))
                tyk_e   = c2.text_input("Ticker YF", value=str(row.get('ticker_yf') or ''))
                tbi_e   = c3.text_input("Ticker BI", value=str(row.get('ticker_bi') or ''))
                ter_e   = c1.number_input("TER %", value=float(row.get('ter') or 0.20),
                                           min_value=0.0, max_value=5.0, step=0.01, format="%.2f")
                prop_e  = c2.selectbox("Proprietario",['persona1','persona2','figlio'],
                                        index=['persona1','persona2','figlio'].index(
                                            str(row.get('proprietario','persona1'))) if str(row.get('proprietario','persona1')) in ['persona1','persona2','figlio'] else 0)
                stato_e = c3.selectbox("Stato",['attivo','da_avviare','candidato','dismisso'],
                                        index=['attivo','da_avviare','candidato','dismisso'].index(
                                            str(row.get('stato','candidato'))) if str(row.get('stato','candidato')) in ['attivo','da_avviare','candidato','dismisso'] else 0)
                vi_e    = c1.number_input("Valore iniziale (€)", value=float(row.get('valore_iniziale') or 0),
                                           min_value=0.0, step=100.0)
                note_e  = st.text_input("Note", value=str(row.get('note') or ''))
                col_s, col_d = st.columns(2)
                salva_e = col_s.form_submit_button("💾 Salva modifiche")
                elim_e  = col_d.form_submit_button("🗑️ Elimina asset", type="secondary")
            if salva_e:
                ok = salva_asset({
                    'isin': isin_etf, 'nome': nome_e, 'tipo': 'etf',
                    'ticker_yf': tyk_e or None, 'ticker_bi': tbi_e or None,
                    'ter': ter_e, 'proprietario': prop_e, 'stato': stato_e,
                    'valore_iniziale': vi_e, 'note': note_e or None,
                    'fallback_tickers': row.get('fallback_tickers', []),
                })
                if ok:
                    st.success("ETF aggiornato.")
                    st.session_state.pop('asset_catalog', None)
                    st.rerun()
                else:
                    st.error("Errore salvataggio.")
            if elim_e:
                if rimuovi_asset(isin_etf):
                    st.success(f"{isin_etf} rimosso dal catalogo.")
                    st.session_state.pop('asset_catalog', None)
                    st.rerun()

    # ── TAB AZIONI ─────────────────────────────────────────────
    with tab_azioni:
        az_df = cat_df[cat_df['tipo'] == 'azione'] if not cat_df.empty else pd.DataFrame()
        if az_df.empty:
            st.info("Nessuna azione nel catalogo.")
        else:
            disp = ['isin','nome','ticker_yf','proprietario','stato','note']
            disp = [c for c in disp if c in az_df.columns]
            st.dataframe(az_df[disp], use_container_width=True, hide_index=True)

        st.subheader("Modifica / elimina azione")
        _a_name = az_df.set_index('isin')['nome'].to_dict() if not az_df.empty else {}
        isin_az = st.selectbox("Seleziona azione",
                                 options=az_df['isin'].tolist() if not az_df.empty else [],
                                 format_func=lambda x: f"{_a_name.get(x, x)} ({x})",
                                 key="sel_azione")
        if isin_az and not az_df.empty:
            row = az_df[az_df['isin'] == isin_az].iloc[0]
            with st.form("edit_azione"):
                c1, c2 = st.columns(2)
                nome_a = c1.text_input("Nome", value=str(row.get('nome','')))
                tyk_a  = c2.text_input("Ticker YF", value=str(row.get('ticker_yf') or ''))
                prop_a = c1.selectbox("Proprietario",['persona1','persona2','figlio'],
                                       index=['persona1','persona2','figlio'].index(
                                           str(row.get('proprietario','persona1'))) if str(row.get('proprietario','persona1')) in ['persona1','persona2','figlio'] else 0)
                note_a = st.text_input("Note", value=str(row.get('note') or ''))
                col_s, col_d = st.columns(2)
                salva_a = col_s.form_submit_button("💾 Salva modifiche")
                elim_a  = col_d.form_submit_button("🗑️ Elimina asset", type="secondary")
            if salva_a:
                ok = salva_asset({
                    'isin': isin_az, 'nome': nome_a, 'tipo': 'azione',
                    'ticker_yf': tyk_a or None, 'proprietario': prop_a,
                    'stato': str(row.get('stato','attivo')),
                    'note': note_a or None, 'fallback_tickers': [],
                })
                if ok:
                    st.success("Azione aggiornata.")
                    st.session_state.pop('asset_catalog', None)
                    st.rerun()
            if elim_a:
                if rimuovi_asset(isin_az):
                    st.success(f"{isin_az} rimosso dal catalogo.")
                    st.session_state.pop('asset_catalog', None)
                    st.rerun()

    # ── TAB AGGIUNGI ───────────────────────────────────────────
    with tab_aggiungi:
        st.subheader("Aggiungi nuovo asset")
        with st.form("nuovo_asset"):
            c1, c2, c3 = st.columns(3)
            tipo_n   = c1.selectbox("Tipo", ['etf','fondo','azione'])
            isin_n   = c2.text_input("ISIN *", max_chars=12,
                                     help="Formato: 2 lettere + 9 alfanumerici + 1 cifra (es. IE00B5BMR087)")
            nome_n   = c3.text_input("Nome *")
            tyk_n    = c1.text_input("Ticker Yahoo Finance")
            tbi_n    = c2.text_input("Ticker BI (solo ETF)")
            ter_n    = c3.number_input("TER %", min_value=0.0, max_value=5.0,
                                        value=0.20, step=0.01, format="%.2f")
            prop_n   = c1.selectbox("Proprietario", ['persona1','persona2','figlio'])
            stato_n  = c2.selectbox("Stato", ['attivo','da_avviare','candidato'])
            vqr_n    = c3.number_input("Quota ref € (fondi)", min_value=0.0, step=0.01)
            note_n   = st.text_input("Note")
            submit_n = st.form_submit_button("➕ Aggiungi al catalogo")

        if submit_n:
            import re as _re
            _isin_clean = isin_n.strip().upper()
            if not isin_n or not nome_n:
                st.error("ISIN e Nome sono obbligatori.")
            elif not _re.match(r'^[A-Z]{2}[A-Z0-9]{9}[0-9]$', _isin_clean):
                st.error("ISIN non valido. Formato atteso: 2 lettere + 9 alfanumerici + 1 cifra (es. IE00B5BMR087).")
            else:
                asset_dict = {
                    'isin': _isin_clean, 'nome': nome_n.strip(),
                    'tipo': tipo_n, 'ticker_yf': tyk_n.strip() or None,
                    'ticker_bi': tbi_n.strip() or None,
                    # fondi: frazione (0.02 = 2%); ETF: percentuale; azioni: nessun TER
                    'ter': (ter_n / 100 if tipo_n == 'fondo' else ter_n) if tipo_n != 'azione' else None,
                    'proprietario': prop_n, 'stato': stato_n,
                    'valore_quota_ref': vqr_n if vqr_n > 0 else None,
                    'note': note_n.strip() or None, 'fallback_tickers': [],
                }
                if salva_asset(asset_dict):
                    st.success(f"Asset {isin_n} aggiunto al catalogo.")
                    st.session_state.pop('asset_catalog', None)
                    st.rerun()
                else:
                    st.error("Errore durante il salvataggio.")

        st.divider()
        st.caption("Dopo aver aggiunto un asset, vai su '🔄 Aggiorna tutto' per scaricare i prezzi storici.")
        st.info("Il catalogo asset è la fonte di verità dell'app. Aggiungi qui qualsiasi ETF, fondo o azione che vuoi tracciare.")

# ─────────────────────────────────────────────────────────────
# SEZIONE ADMIN: GESTIONE UTENTI
# ─────────────────────────────────────────────────────────────
elif sezione == "👥 Utenti":
    if not st.session_state.get("_auth_is_admin"):
        st.error("Accesso riservato agli amministratori.")
        st.stop()

    st.title("👥 Gestione Utenti")

    from database import (get_tutti_utenti as _gtu, crea_utente_invito as _cui,
                          set_utente_attivo as _sua, imposta_reset_token as _irt2,
                          get_utente_by_email as _gube2, elimina_utente as _eu)
    from auth_mail import (invia_email_invito as _iei, invia_email_reset as _ier2,
                           smtp_configurato as _sc2)

    tab_lista, tab_invita = st.tabs(["👥 Utenti registrati", "➕ Invita utente"])

    with tab_lista:
        _utenti_df = _gtu()
        if _utenti_df.empty:
            st.info("Nessun utente trovato.")
        else:
            for _u in _utenti_df.to_dict('records'):
                _col_info, _col_azioni = st.columns([3, 1])
                with _col_info:
                    _stato = "🟢 Attivo" if _u.get('is_active') else "🔴 Disattivo"
                    _admin = " · 👑 Admin" if _u.get('is_admin') else ""
                    _2fa   = " · 🔐 2FA" if _u.get('totp_enabled') else ""
                    _ll = _u.get('last_login', '')
                    if _ll:
                        try:
                            _ll = datetime.fromisoformat(
                                str(_ll).replace('Z', '+00:00')
                            ).strftime('%d/%m/%Y %H:%M')
                        except Exception:
                            pass
                    else:
                        _ll = "Mai"
                    st.markdown(f"**{_u['username']}** — {_u['email']}")
                    st.caption(f"{_stato}{_admin}{_2fa} · Ultimo accesso: {_ll}")
                with _col_azioni:
                    _uid2 = _u['id']
                    _me   = st.session_state.get("_auth_user_id")
                    if _uid2 != _me:
                        if _u.get('is_active'):
                            if st.button("Disattiva", key=f"dis_{_uid2}"):
                                _sua(_uid2, False)
                                st.rerun()
                        else:
                            if st.button("Riattiva", key=f"ria_{_uid2}"):
                                _sua(_uid2, True)
                                st.rerun()
                            if st.button("🗑 Elimina", key=f"del_{_uid2}"):
                                _eu(_uid2)
                                st.rerun()
                    if _sc2() and _u.get('is_active') and _uid2 != _me:
                        if st.button("Reset pwd", key=f"rst_{_uid2}"):
                            _raw2, _th2 = _auth_genera_token()
                            _exp2 = (datetime.now() + timedelta(minutes=15)).isoformat()
                            if _irt2(_uid2, _th2, _exp2):
                                try:
                                    _base = st.secrets.get("APP_BASE_URL", "http://localhost:8501")
                                    _ier2(_u['email'], _raw2, _base)
                                    st.success(f"Email di reset inviata a {_u['email']}")
                                except Exception as _ex2:
                                    st.error(f"Errore invio: {type(_ex2).__name__}")
                st.divider()

    with tab_invita:
        st.subheader("Invita un nuovo utente")
        if not _sc2():
            st.warning(
                "SMTP non configurato — puoi creare l'utente ma il link di attivazione "
                "dovrà essere condiviso manualmente. Configura SMTP_HOST, SMTP_PORT, "
                "SMTP_USER, SMTP_PASSWORD in secrets.toml per abilitare gli inviti via email."
            )
        with st.form("invita_utente"):
            _inv_user  = st.text_input("Username (min. 3 caratteri)", max_chars=50)
            _inv_email = st.text_input("Email", max_chars=200)
            _inv_admin = st.checkbox("Ruolo amministratore", value=False)
            _inv_submit = st.form_submit_button("📨 Invia invito")

        if _inv_submit:
            _inv_errs = []
            if not _inv_user or len(_inv_user.strip()) < 3:
                _inv_errs.append("Username: almeno 3 caratteri.")
            if not _inv_email or '@' not in _inv_email:
                _inv_errs.append("Email non valida.")
            for _e3 in _inv_errs:
                st.error(_e3)
            if not _inv_errs:
                try:
                    _raw3, _th3 = _auth_genera_token()
                    _exp3 = (datetime.now() + timedelta(hours=48)).isoformat()
                    _new_u = _cui(_inv_user.strip(), _inv_email.strip(), _th3, _exp3, _inv_admin)
                    if _new_u:
                        if _sc2():
                            try:
                                _base3 = st.secrets.get("APP_BASE_URL", "http://localhost:8501")
                                _iei(_inv_email.strip(), _inv_user.strip(), _raw3, _base3)
                                st.success(f"Invito inviato a {_inv_email}. Valido 48 ore.")
                            except Exception as _ex3:
                                st.warning(
                                    f"Utente creato ma email non inviata ({type(_ex3).__name__}). "
                                    f"Condividi manualmente: `?invite_token={_raw3}`"
                                )
                        else:
                            st.info(
                                f"Utente **{_inv_user.strip()}** creato. "
                                f"Condividi questo link:\n`?invite_token={_raw3}`"
                            )
                    else:
                        st.error("Errore nella creazione. Username o email già in uso?")
                except Exception as _ex4:
                    _ex4_msg = (getattr(_ex4, 'message', None) or str(_ex4)).lower()
                    if 'duplicate' in _ex4_msg or 'unique' in _ex4_msg:
                        st.error("Username o email già in uso.")
                    else:
                        st.error(f"Errore: {type(_ex4).__name__}")
