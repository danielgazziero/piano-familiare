"""
Invio email per autenticazione — reset password e inviti utente.
Provider-agnostico: configurare SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
in .streamlit/secrets.toml o come variabili d'ambiente.
"""
import smtplib
import ssl
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from html import escape as _he


def _get_smtp_config() -> dict:
    try:
        import streamlit as st
        return {
            'host':     str(st.secrets.get("SMTP_HOST", "smtp.gmail.com")),
            'port':     int(st.secrets.get("SMTP_PORT", 587)),
            'user':     str(st.secrets.get("SMTP_USER", "")),
            'password': str(st.secrets.get("SMTP_PASSWORD", "")),
        }
    except Exception:
        return {
            'host':     os.environ.get("SMTP_HOST", "smtp.gmail.com"),
            'port':     int(os.environ.get("SMTP_PORT", 587)),
            'user':     os.environ.get("SMTP_USER", ""),
            'password': os.environ.get("SMTP_PASSWORD", ""),
        }


def smtp_configurato() -> bool:
    """Verifica che SMTP sia configurato (user e password presenti)."""
    cfg = _get_smtp_config()
    return bool(cfg['user'] and cfg['password'])


def _invia_email(to_email: str, subject: str, body_html: str) -> None:
    """
    Invia email via SMTP con STARTTLS.
    Solleva eccezione in caso di errore di connessione o autenticazione.
    """
    cfg = _get_smtp_config()
    if not cfg['user'] or not cfg['password']:
        raise RuntimeError(
            "SMTP non configurato. Aggiungi SMTP_HOST, SMTP_PORT, SMTP_USER, "
            "SMTP_PASSWORD in .streamlit/secrets.toml."
        )
    to_email = to_email.replace('\r', '').replace('\n', '')
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = cfg['user']
    msg['To']      = to_email
    msg.attach(MIMEText(body_html, 'html', 'utf-8'))
    context = ssl.create_default_context()
    with smtplib.SMTP(cfg['host'], cfg['port']) as server:
        server.starttls(context=context)
        server.login(cfg['user'], cfg['password'])
        server.sendmail(cfg['user'], to_email, msg.as_string())


def invia_email_reset(email: str, token: str, base_url: str) -> None:
    """Invia email con link per il reset della password (token valido 15 min)."""
    link = f"{base_url.rstrip('/')}/?reset_token={token}"
    body = f"""
<p>Hai richiesto il reset della password per <strong>Piano Finanziario Familiare</strong>.</p>
<p><a href="{link}" style="font-size:1.1em;font-weight:bold">
Clicca qui per impostare una nuova password
</a></p>
<p style="color:#888">Il link scade tra <strong>15 minuti</strong>.
Se non hai richiesto il reset, ignora questa email.</p>
"""
    _invia_email(email, "Reset password — Piano Finanziario Familiare", body)


def invia_email_invito(email: str, username: str, token: str, base_url: str) -> None:
    """Invia email di invito con link per attivare l'account (token valido 48 ore)."""
    link = f"{base_url.rstrip('/')}/?invite_token={token}"
    body = f"""
<p>Sei stato invitato ad accedere a <strong>Piano Finanziario Familiare</strong>.</p>
<p>Il tuo username è: <strong>{_he(username)}</strong></p>
<p><a href="{link}" style="font-size:1.1em;font-weight:bold">
Clicca qui per impostare la tua password e attivare l'account
</a></p>
<p style="color:#888">Il link scade tra <strong>48 ore</strong>.</p>
"""
    _invia_email(email, "Invito — Piano Finanziario Familiare", body)
