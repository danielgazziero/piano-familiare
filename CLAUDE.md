# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Piano Finanziario Familiare

App Streamlit per il monitoraggio del patrimonio familiare.

## Comandi principali

```bash
# Avvia app
python -m streamlit run app.py

# Testa connessione Supabase
python src/database.py

# Testa parser XLS bancari
python src/parser.py

# Controllo ambiente (dipendenze, config)
python check_and_setup.py
```

App disponibile su http://localhost:8501 — si ricarica automaticamente ad ogni modifica.

## Stack tecnico

- **Frontend:** Streamlit — navigazione via `st.sidebar.radio`, un unico `app.py`
- **Grafici:** Plotly (go.Figure / px) — wrapper `_plotly_chart()` forza bgcolor e titoli come elementi Streamlit separati (uniforme dark/light)
- **Database:** Supabase (Postgres hosted) — credenziali in `.streamlit/secrets.toml` (gitignored) via `st.secrets`
- **Prezzi ETF/azioni:** yfinance — ticker in `config.yaml` e `src/prices.py`; batch download via `_batch_download(tickers_tuple, period)` con `@st.cache_data(ttl=1800)`
- **Prezzi fondi bancari:** manuali — nessun ticker Yahoo, valorizzati da `quote_fondi` su Supabase
- **Parsing XLS banca:** xlrd 1.2.0 (BIFF8, formato vecchio conto Persona 1) + openpyxl (formato nuovo)

## Architettura e flusso dati

### Layer

```
app.py
  └─ src/app_state.py   # bridge Streamlit session_state ↔ Supabase
       └─ src/database.py    # CRUD Supabase (tutte le tabelle)
       └─ src/positions.py   # quantità asset nel tempo + backfill prezzi
  └─ src/portfolio.py   # snapshot valori correnti (ETF via yfinance, fondi via quote_map)
  └─ src/simulator.py   # simulazioni finanziarie pure (no DB, no yfinance)
  └─ src/parser.py      # orchestratore parsing XLS bancari
       └─ src/adapters.py    # parser specifici per banca
  └─ src/prices.py      # download storico prezzi via yfinance
  └─ config.yaml        # dati statici di riferimento e valori di fallback
```

### Sequenza di avvio (ogni sessione Streamlit)

1. `load_config()` — carica `config.yaml` (`@st.cache_data ttl=3600`)
2. `init_db_connection()` — verifica Supabase; l'app funziona anche offline
3. Auth — password PBKDF2 da `config_params.app_password`; cacheata in `session_state['_app_pwd_cache']`
4. Carica `params` da `config_params` → `session_state['params']`
5. Carica `asset_catalog` da DB → `session_state['asset_catalog']`
6. Carica `quote_map` da `quote_fondi` → `session_state['quote_map']`
7. Auto-import XLS da `data/input/` → `salva_transazioni()` (deduplicato via SHA-256)
8. Auto-save snapshot patrimonio odierno (upsert su `patrimonio_log`)
9. Backfill silenzioso: scarica prezzi mancanti per tutti gli ISIN noti

### Caching session_state

Tutte le chiamate Supabase/yfinance costose sono messe in cache la prima volta e riusate per ogni rerun della sessione. La chiave di invalidazione è il pulsante "🔄 Aggiorna tutto" che svuota:

```python
['params', 'quote_map', 'xls_importati', 'saved_today', 'backfill_done',
 'backfill_nuovi', 'nuove_tx', 'asset_catalog', 'pos_df_cache',
 'fondi_df_cache', '_app_pwd_cache', 'etf_perf_cache', 'azioni_df_cache',
 'tx_db_cache', 'patrimonio_log_cache', 'port_storico_cache',
 'eventi_storico_cache', '_plotly_tpl_dark']
```

Per `port_storico_cache` e `eventi_storico_cache` la cache è un dict keyed per `data_da`.

### Valorizzazione asset

- **ETF / azioni ACN:** batch download `_batch_download(tickers, period)` con cache `@st.cache_data(ttl=1800)`. (`get_etf_history_chart()` è stata rimossa come codice morto.)
- **Fondi bancari italiani:** prezzi da `quote_map` (Supabase `quote_fondi`). Fallback a `valore_quota_ref` in `asset_catalog` o `config.yaml`.
- **Portafoglio storico:** prezzi giornalieri in `prezzi_storici`, quantità in `posizioni` — `positions.py` ricostruisce `valore = quantità(giorno) × prezzo(giorno)` con forward-fill per weekend/festivi. Gli ISIN sono passati da `session_state['asset_catalog']` per evitare query ridondante.

### Parsing XLS bancari

`src/parser.py` legge `config.yaml → banche[].formato` e delega a `src/adapters.py`:
- `bper_xls` → xlrd 1.2.0 BIFF8 (estratti vecchio formato, conto Persona 1)
- `bper_xls_new` → openpyxl (estratti nuovo formato, conto Persona 2 e recenti Persona 1)

Le keyword per escludere i bonifici interni (`transfer_keywords` da Supabase) vengono passate come parametro `keywords=` a `adapter.parse()` e poi a `is_internal_transfer()` — **non** più come mutazione globale di `TRANSFER_KEYWORDS`.

File XLS da mettere in `data/input/` — vengono importati automaticamente all'avvio.

### Autenticazione

Auth basata su tabella `app_users` in Supabase (username + password PBKDF2-SHA256, ruolo admin/utente, TOTP 2FA opzionale).

- **dev:** `_DEMO = True` hardcoded — mostra dati fittizi, ma auth Supabase sempre richiesta
- **main:** `_DEMO = bool(st.secrets.get("DEMO_MODE", False))` — con `DEMO_MODE = false` usa dati reali

**Flussi:**
- **Primo avvio** (app_users vuota): wizard crea account admin; offre di importare l'hash legacy da `config_params.app_password`
- **Login**: username + password → se TOTP attivo, secondo step codice 6 cifre; brute-force guard con backoff esponenziale, timeout sessione 3600s
- **2FA TOTP**: ogni utente attiva/disattiva da sidebar "🔐 Sicurezza (2FA)"; usa `pyotp` (Google Authenticator / Authy); disabilitazione richiede codice corrente
- **Reset password**: link via email con token SHA-256 (15 min), SMTP provider-agnostico
- **Invito utenti**: admin crea account inattivi + link attivazione 48 ore (`?invite_token=`)
- **Admin panel** "👥 Utenti": visibile solo agli admin; lista con badge 2FA, disattiva/riattiva, reset password

Session_state auth keys: `_auth_ok`, `_auth_ts`, `_auth_user_id`, `_auth_username`, `_auth_is_admin`.
Session_state TOTP pending (step intermedio): `_auth_pending_uid`, `_auth_pending_username`, `_auth_pending_is_admin`.

Comportamento fail-secure: se Supabase non raggiungibile → `st.stop()`. Mai accesso libero.

**SQL da eseguire una volta su Supabase SQL Editor** per creare la tabella (vedi `APP_USERS_SCHEMA_SQL` in `src/database.py`).

**SQL aggiuntivo per 2FA** (se la tabella `app_users` esiste già):
```sql
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS totp_secret TEXT;
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN DEFAULT FALSE;
```

**SMTP** (per email reset/inviti): aggiungere in `.streamlit/secrets.toml`:
```toml
SMTP_HOST     = "smtp.gmail.com"   # o smtp.office365.com, smtp.sendgrid.net...
SMTP_PORT     = 587
SMTP_USER     = "..."
SMTP_PASSWORD = "..."              # App Password, non la password account
APP_BASE_URL  = "https://tua-app.streamlit.app"
```

`carica_param()` in `src/database.py` prova `json.loads()` sul valore, poi fallback a raw string.

### Dark / Light mode

- `config.toml` → `base = "dark"` (tema di default)
- Toggle "🌙 Dark mode" in sidebar → `session_state['_dark_mode_toggle']`
- `_inject_css()` — inietta sempre `scrollbar-gutter: stable; overflow-y: scroll` + transizioni; aggiunge overrides light mode se tema chiaro
- `_plotly_chart(fig, _title=None)` — wrapper che: (1) imposta bgcolor in base al tema, (2) estrae il titolo e lo renderizza come `st.markdown` centered (`html.escape` obbligatorio), (3) forza `theme=None` per bypassare l'override Streamlit
- `_init_plotly_template()` — skip se tema non è cambiato (`session_state['_plotly_tpl_dark']`)

## Schema Supabase

| Tabella | Contenuto |
|---|---|
| `patrimonio_log` | Snapshot giornaliero patrimonio totale per componente (upsert per `data`) |
| `quote_fondi` | Quote fondi bancari nel tempo (upsert per `data, isin`) |
| `transazioni` | Movimenti bancari da XLS (deduplicati via `hash_tx` SHA-256) |
| `config_params` | Key-value store per valori manuali (liquidità, conto comune, ecc.) |
| `posizioni` | Quantità asset per intervallo date (`data_inizio`, `data_fine` NULL=attiva) |
| `prezzi_storici` | Prezzi giornalieri ETF/azioni (upsert per `isin, data`) |
| `backfill_stato` | Ultima data scaricata per ISIN (checkpoint backfill) |
| `asset_catalog` | Metadati asset (ISIN, nome, tipo, ticker, TER, proprietario, stato) |

Schema SQL in `docs/setup_supabase_schema.sql`. Le tabelle `posizioni`, `prezzi_storici`, `backfill_stato` sono anche in `src/positions.py → POSITIONS_SCHEMA_SQL`.

> **Nota E7**: `carica_ultime_quote_fondi()` prova prima la RPC `get_latest_quotes()` (DISTINCT ON lato DB), poi fallback query standard. SQL da eseguire una volta nel SQL Editor (documentato inline in `src/database.py:~260`).

## Convenzioni di codice

### Nomi generici (privacy — repo pubblico)

Non usare mai nomi reali delle persone nel codice, config o DB. Usa sempre:
- `persona1` / `persona2` per le persone
- `figlio` per il figlio/a

I valori reali (keyword bonifici, pattern file XLS, nascita_figlio) vengono **esclusivamente da Supabase** `config_params`.

### Riconciliazione dati personali

| Chiave Supabase (`config_params`) | Usato in |
|---|---|
| `nome_persona1` | `src/parser.py` → pattern file XLS |
| `nome_persona2` | `src/parser.py` → pattern file XLS |
| `transfer_keywords` | `src/parser.py` → `src/adapters.py` (come parametro, non globale) |
| `nascita_figlio` | `app.py` sezione figlio — letto da `session_state['params']` |

### Altre convenzioni

- Colori come costanti dict in cima ad `app.py`: `COLORS`, `SC_COLORS`, `SC_DASH`
- `@st.cache_data(ttl=1800)` per chiamate yfinance; `ttl=3600` per config
- `iterrows()` è bandito — usare sempre `to_dict('records')` o operazioni vettoriali
- Non mutare oggetti restituiti da `@st.cache_data` (es. `config`) — costruire copie locali
- Le funzioni in `src/simulator.py` sono pure (no side-effect, no DB, no yfinance)
- ISIN validati con regex `^[A-Z]{2}[A-Z0-9]{9}[0-9]$` prima di scrivere su Supabase
- Valori monetari in EUR (eccetto ACN in USD — `valore_attuale_usd`)
- Date: formato italiano `DD/MM/YYYY` nell'UI, ISO `YYYY-MM-DD` internamente e in DB

## Asset in portafoglio

Metadati pubblici (ISIN, ticker) in `config.yaml`. Quantità, valori e quote reali esclusivamente in Supabase.

### Fondi bancari BPER (prezzi manuali)

| Fondo | ISIN |
|---|---|
| ARCA AZ EUROPA CLIMA | IT0001033486 |
| ARCA AZ AMERICA CLIMA P | IT0001033502 |
| EURIZON AZ EMERG P | IT0001031928 |
| JPMF GLO SUST EQ ACC | LU2293888439 |
| EURIZON AZ AMER P | IT0001050126 |
| EURIZ AZ AREA EURO P | IT0001050225 |
| EURIZON AZ INT P | IT0001080446 |

### ETF (Directa SIM)

| ETF | ISIN | Stato |
|---|---|---|
| CSPX | IE00B5BMR087 | Attivo |
| ACWE | IE00B44Z5B48 | Da avviare |
| IWDA | IE00B4L5Y983 | Da avviare |

### Azioni
- **Accenture ACN** (IE00B4BNMY34) — RSU stock plan, valorizzate in USD

## Contesto finanziario

I valori numerici reali (patrimonio, entrate, debiti) vivono in Supabase `config_params` e non sono mai nel codice o nel repo.

---

## Stato tecnico al 18/10/2026

### Branch corrente: `dev` — allineato a `main`

| Branch | Ultimo commit | Contenuto |
|---|---|---|
| `main` | `67da0ef` + commit doc successivo | merge: aggiorna CLAUDE.md stato 18/10/2026 (ultimo commit di codice: `3dc6d9d`) |
| `dev` | come `main` | allineato con fast-forward — nessun commit di divergenza |

### Onboarding wizard (`src/wizard.py`)

**Trigger:** Gate in `app.py` dopo auth — se `wizard_completed != 'true'` in config_params (solo non-DEMO).
**Riavvio:** Admin → sidebar → expander "🧭 Wizard configurazione" → Avvia wizard.
**Estendibilità:** Per aggiungere step a una nuova feature:
1. Definire `_step_<id>(config, params_db) -> dict | None` in `wizard.py`
2. Appendere entry a `WIZARD_STEPS` (prima di `riepilogo`)
3. Aggiungere al `_RENDER_MAP`
**Asset e upload:** gli step ETF/Fondi/Azioni usano `src/wizard_assets.py` (form manuale + import CSV/XLSX → `asset_catalog`, `posizioni`, `quote_fondi`); lo step Parser banca carica estratti conto XLS/XLSX (`parser.parse_uploaded_file` → `transazioni`). `config.yaml` non contiene più asset né patrimonio reali (`etf`, `fondi_bancari.titoli`, `azioni` vuoti).
**Persistenza:** `config_params.wizard_step_last` (ripresa), `_wiz_done_<id>` per step, `wizard_completed = 'true'` a fine.

### FIRE tracker — configurazione

Configurabile direttamente dall'UI in "Stato di famiglia" → sezione "🔥 FIRE Progress":
- Se non ancora attivato: form espanso con 3 campi + stima automatica da transazioni
- Se già attivato: expander collassato "⚙️ Modifica configurazione FIRE"

I valori vengono salvati su Supabase `config_params` via `salva_params_persistenti()`.

| Chiave `config_params` | Tipo | Default | Descrizione |
|---|---|---|---|
| `fire_spese_annue` | float | — (form mostrato se assente) | Spese annue target per FIRE |
| `fire_eta_attuale` | int | `35` | Età attuale persona1 |
| `fire_eta_pensione` | int | `60` | Età target pensione/FIRE |
| `bollo_soglia_liquidita` | float | `5000` (fallback `config.yaml`) | Soglia liquidità conti correnti per bollo alert |

Il tasso di prelievo (4%) e il rendimento atteso (7%) rimangono in `config.yaml → parametri.fire_tasso_prelievo` / `fire_rendimento` (non modificabili da UI — valori standard FIRE).

La stima automatica delle spese è calcolata da `tx_db_cache → expense` media ultimi 12 mesi × 12.

### Rebalancing ETF — configurazione

`target_pct` per ogni ETF configurabile dall'UI in "ETF & mercato" → expander "⚖️ Rebalancing ETF" → sezione "⚙️ Modifica target allocation". Salva su Supabase `asset_catalog.target_pct` via `salva_asset()`.

**Priorità lettura:** `asset_catalog.target_pct` (Supabase) sovrascrive `config.yaml → etf[].target_pct`.

**Prerequisito schema:** `ALTER TABLE asset_catalog ADD COLUMN IF NOT EXISTS target_pct NUMERIC DEFAULT 0;` (già in `docs/setup_supabase_schema.sql`).

La soglia drift è in `config.yaml → parametri.rebalancing_soglia_drift` (default 5%).

**Commit principali della sessione del 18/10/2026 (code review settimanale + onboarding senza dati di esempio):**

| Commit | Contenuto |
|---|---|
| `2389378` | fix: soglia bollo robusta, `quantita or 0`, timing guard legacy, workflow keep-alive con `env` |
| `c7cec33` | fix: RLS `service_only` nello schema SQL, lockout fail-closed + pulizia contatori, `verify_password` legacy, requirements con limiti superiori, workflow `pip-audit` |
| `9fb0d3f` | fix: yfinance MultiIndex su singolo ticker (azioni, prezzi storici); guard Simulatore con dati incompleti |
| `8fcae18` | fix: palette confronto ETF ciclica (IndexError con >4 ETF) |
| `4b80995` | feat: `config.yaml` senza asset; wizard con inserimento manuale e upload (CSV/XLSX asset, estratti conto XLS) |
| `3dc6d9d` | merge su main — main e dev allineati |
| `3ea41d2` / `67da0ef` | chore: aggiorna CLAUDE.md stato 18/10/2026 + merge su main |

**Commit principali della sessione del 14/10/2026 (code review settimanale automatico):**

| Commit | Data | Contenuto |
|---|---|---|
| `863b3c3` | 14/10 | fix: 8 fix da code review settimanale (auth, robustezza, efficienza) |

**Commit principali della sessione del 12-13/10/2026 (code review settimanale automatico):**

| Commit | Data | Contenuto |
|---|---|---|
| `e804192` | 12/10 | fix: 6 fix da code review automatico (sicurezza, efficienza, refactoring) |
| `021e41e` | 13/10 | merge: 4 fix da code review automatico su main |
| `ec4dd17` | 13/10 | fix: 4 fix da code review automatico (sicurezza, robustezza) |
| `7524872` | 13/10 | fix: 4 fix da code review settimanale (sicurezza, robustezza, refactoring) |
| `2a1b6cc` | 13/10 | merge: 4 fix da code review settimanale su main |
| `25fc4f4` | 13/10 | chore: aggiorna CLAUDE.md — stato 13/10/2026, fix WK-S1..WK-RF1 |
| `043b58a` | 13/10 | merge: aggiorna CLAUDE.md stato 13/10/2026 |

**Commit principali della sessione del 09/10/2026 (code review fix):**

| Commit | Contenuto |
|---|---|
| `351f99e` | fix: 5 fix da code review — auth logout, NaN guard, bollo alert |
| `47b158f` | merge: 5 fix da code review su main |

**Commit principali della sessione del 07/10/2026 (quality scan + feature #9):**

| Commit | Contenuto |
|---|---|
| `3de298a` | feat: bollo threshold alert + fix params_correnti |
| `4d906eb` | merge: bollo threshold alert (feature #9) su main |
| `c03ad6f` | fix: code quality scan — 7 fix qualità e sicurezza |
| `3c6f5d6` | merge: code quality scan su main |

**Commit principali della sessione del 28/09/2026 (config UI):**

| Commit | Contenuto |
|---|---|
| `142a211` | feat: FIRE config UI + ETF target allocation editabile da interfaccia |
| `18f8734` | merge: su main — main e dev allineati |

**Commit principali della sessione del 27/09/2026 (feature #1-4):**

| Commit | Contenuto |
|---|---|
| `160c22a` | feat: FIRE tracker, Savings Rate, Rebalancing ETF, Liabilities net worth |
| `3fe2541` | chore: aggiorna CLAUDE.md feature #1-4 completate |

**Commit principali della sessione del 27/09/2026 (performance):**

| Commit | Contenuto |
|---|---|
| `e9cb971` | merge: fix sicurezza S-NEW-06/07/08 + performance P-NEW-W1/A1-A4/I1/D1 |
| `19536c7` | perf: lazy loading yfinance — snapshot/backfill solo su "Stato di famiglia" |
| `296a678` | perf: lazy XLS import — parse_all_inputs solo su sezioni rilevanti |
| `73470e1` | perf: @st.fragment su grafici ETF e simulatori fondi |
| `2528970` | merge: tutte le ottimizzazioni su main |

**Commit principali della sessione del 27/09/2026 (wizard e auth):**

| Commit | Contenuto |
|---|---|
| `cd5a5cf` | feat: auth a username+password — wizard primo avvio, reset email, admin panel |
| `e85ce14` | feat: elimina utente inattivo nell'admin panel |
| `54ac76f` | feat: autenticazione a 2 fattori (TOTP) — pyotp + qrcode |
| `4bdb245` | merge: 2FA TOTP su main |

**Commit principali della sessione del 26/09/2026:**

| Commit | Contenuto |
|---|---|
| `df92d8f` | fix: scrollbar-gutter stable + transizioni CSS (layout shift dark/light) |
| `3d2aa9d` | fix: titolo grafico come elemento Streamlit separato |
| `504e249` | fix: sicurezza S-01 + performance HIGH P-01..P-06 |
| `b9d62bc` | perf: fix MEDIUM + LOW P-07..P-15 |
| `0ce7e46` | fix: sicurezza S-NEW-01..04 (2° scansione) |
| `0cc41f6` | perf: fix performance P-NEW-01..05 (2° scansione) |

---

## Roadmap — Fix tecnici completati

### ✅ Fix UX/UI (26/09/2026)

| # | Fix | File |
|---|---|---|
| U1 | Dark/light mode: `base="dark"` in config.toml, `scrollbar-gutter:stable`, transizioni CSS | `app.py`, `.streamlit/config.toml` |
| U2 | Titoli grafici Plotly come `st.markdown` centrato — uniformi in dark e light mode | `app.py:_plotly_chart()` |
| U3 | `theme=None` nel wrapper Plotly — bypassa override Streamlit su posizionamento assi | `app.py:_plotly_chart()` |
| U4 | Tutti i titoli dinamici passati esplicitamente via `_title=` (non estratti da fig post-render) | `app.py` (8 call site) |

### ✅ Fix sicurezza (24-26/09/2026)

| # | Fix | File |
|---|---|---|
| S1 | Password plaintext in Supabase: auto-hash PBKDF2 all'avvio | `app.py` |
| S2 | Glob metacharacter injection: `nome_<account>` sanitizzato prima del pattern | `src/parser.py` |
| S3 | MD5 → SHA-256 per `hash_tx` deduplicazione transazioni | `src/database.py` |
| S4 | `str(e)` → `type(e).__name__` in ~25 print eccezione | `src/database.py`, `src/positions.py`, `src/prices.py`, `src/portfolio.py`, `src/parser.py` |
| S5 | Timeout sessione auth 3600s con redirect automatico | `app.py` |
| S6 | `data_ref` validata con `date.fromisoformat()` prima dell'upsert | `app.py` |
| S7 | `st.warning(f"...{e}")` → `type(e).__name__` (UI visibile a utenti auth) | `app.py` |
| S-NEW-01 | DoS PBKDF2 pre-auth: `pwd = pwd[:1024]` in `verify_password` + `max_chars=1024` su tutti i campi password | `src/database.py`, `app.py` |
| S-NEW-02 | Race condition `TRANSFER_KEYWORDS` globale: eliminata mutazione modulo, keywords passate come parametro `keywords=` | `src/adapters.py`, `src/parser.py` |
| S-NEW-03 | ISIN non validato: `max_chars=12` + regex `^[A-Z]{2}[A-Z0-9]{9}[0-9]$` | `app.py` |
| S-NEW-04 | Input nomi persona: `max_chars=100` | `app.py` |
| S-NEW-05 | **2FA TOTP** — secondo step login con codice 6 cifre (pyotp); setup QR code in sidebar; disabilitazione richiede codice corrente | `app.py`, `src/database.py` |
| S-NEW-06 | Auto-rehash password legacy (plaintext → PBKDF2) al primo login riuscito | `src/database.py` |
| S-NEW-07 | Rivalidazione `is_active` ogni 5 min in sessione — revoca accesso utenti disattivati | `src/database.py`, `app.py` |
| S-NEW-08 | Allowlist `_ALLOWED_TOKEN_FIELDS` + `.limit(1)` in `verifica_token` — blocca column injection | `src/database.py` |

### ✅ Fix code review settimanale e onboarding (18/10/2026)

| # | Fix | File |
|---|---|---|
| WK3-S1 | `docs/setup_supabase_schema.sql`: policy `full_access USING (true)` sostituite da `service_only` (`auth.role() = 'service_role'`) + `DROP POLICY IF EXISTS full_access`. Il DB di produzione aveva già solo `service_only` (verificato) — era lo schema in `docs/` a essere disallineato | `docs/setup_supabase_schema.sql` |
| WK3-S2 | Lockout login/TOTP **fail-closed**: su errore DB blocco di 30 s (senza DB il login non riesce comunque). Helper unificati `_read_fail_state` / `_write_fail_state` | `src/database.py` |
| WK3-S3 | Contatori `_auth_fail_<username>` più vecchi di 24h eliminati a ogni scrittura (`_prune_login_fail_rows`) — un attaccante non può gonfiare `config_params` con username inventati | `src/database.py` |
| WK3-S4 | `verify_password`: confronto legacy in chiaro su bytes UTF-8 (`compare_digest` su str non-ASCII lanciava `TypeError`) | `src/database.py` |
| WK3-S5 | `requirements.txt` con limiti superiori (`<major+1`, compatibili con pandas 3 / plotly 7 installati); `.github/workflows/pip-audit.yml` (lunedì 07:00 UTC + su modifica requirements) | `requirements.txt`, `.github/workflows/` |
| WK3-R1 | `_extract_series` e download storico: yfinance recente restituisce colonne MultiIndex anche per 1 ticker → `float(Series)` `TypeError` in "Azioni Accenture". Ora `Close` DataFrame viene ridotto alla prima colonna | `src/portfolio.py`, `src/prices.py` |
| WK3-R2 | Simulatore strategie: se `simula_scenario_completo` ritorna DataFrame vuoto (nascita figlio / scadenza Generali mancanti) mostra un avviso invece di `KeyError` | `app.py` |
| WK3-R3 | Confronto ETF candidati: `pal_c[i % len(pal_c)]` (IndexError con più di 4 ETF) | `app.py` |
| WK3-F1 | `config.yaml`: `etf`, `fondi_bancari.titoli`, `azioni` vuoti; `patrimonio` a zero. Gli asset vivono solo in Supabase (`asset_catalog`, `posizioni`) | `config.yaml` |
| WK3-F2 | Nuovo `src/wizard_assets.py`: negli step ETF / Fondi / Azioni elenco asset, form di inserimento (ISIN validato, quantità → `posizioni`, quota fondo → `quote_fondi`) e import CSV/XLSX con modello scaricabile (max 10 MB, 300 righe, righe invalide scartate e riportate) | `src/wizard_assets.py`, `src/wizard.py` |
| WK3-F3 | Step "Parser banca": upload estratti conto XLS/XLSX con scelta banca/conto; `parse_uploaded_file()` usa file temporaneo sempre eliminato; dedup via `hash_tx` | `src/parser.py`, `src/wizard_assets.py` |
| WK3-F4 | Step "Rebalancing" legge gli ETF dal catalogo DB (non più da `config.yaml`); `positions.imposta_posizione()` crea o sostituisce la quantità corrente | `src/wizard.py`, `src/positions.py` |

**Note operative emerse:**
- L'app usa `SUPABASE_SECRET` (chiave `sb_secret_...`, ruolo `service_role`) — [src/database.py:23](src/database.py:23). `SUPABASE_KEY` (`sb_publishable_...`) serve solo al workflow keep-alive; con RLS `service_only` la anon key legge liste vuote ma risponde 200.
- Unità `ter`: fondi in frazione (0.02 = 2%), ETF in percentuale (0.20 = 0.20%). Il wizard converte l'input % dei fondi.
- Gli ETF in `config.yaml → etf_breakdown` (geo/settore) restano: sono dati pubblici da factsheet.

### ✅ Fix code review settimanale (14/10/2026)

| # | Fix | File |
|---|---|---|
| WK2-CR1 | `_auth_wizard()` setta `_auth_active_ts` al login — evita DB call su ogni rerun dopo il primo avvio | `app.py` |
| WK2-CR2 | Ripristinato `user_data.get('password_hash')` guard in `verifica_credenziali` prima di `.startswith('pbkdf2$')` — rimosso per errore nel refactor WK-S2 | `src/database.py` |
| WK2-CR3 | `patrimonio_snapshot`: `config['patrimonio']` → `config.get('patrimonio', {})` — KeyError su config parziale | `src/portfolio.py` |
| WK2-CR4 | `get_azioni_snapshot`: deduplicazione tickers con `dict.fromkeys()` prima di `_batch_download` — ticker doppio causava branch errato in `_extract_series` → dati azzerati | `src/portfolio.py` |
| WK2-CR5 | Guard `pd.isna(p0_ytd)` su calcolo `perf_ytd` — `bool(float('nan'))` è True in Python; allineato a `perf_1y` | `src/portfolio.py` |
| WK2-CR6 | Guard NaN su `first` nei grafici ETF confronto (DEMO e live): `if first and not pd.isna(first)` | `app.py` |
| WK2-CR7 | `str(_ex)` → `getattr(_ex, 'message', None) or str(_ex)` per detect duplicate-key in wizard e invite path | `app.py` |
| WK2-CR8 | `_FAKE_HASH` promossa a costante di modulo — rimossa allocazione locale a ogni call di `verifica_credenziali` | `src/database.py` |

### ✅ Fix sicurezza e robustezza (12-13/10/2026)

| # | Fix | File |
|---|---|---|
| WK-S1 | `totp_secret` rimosso dal SELECT di `verifica_credenziali` — non serve fuori dal flusso TOTP | `src/database.py` |
| WK-S2 | Timing guard per username enumeration: fake hash su utente inesistente (user-not-found path) | `src/database.py` |
| WK-S3 | Wizard: legacy password hashata con PBKDF2 subito se plaintext — non più salvata as-is in `app_users` | `app.py` |
| WK-S4 | Timing guard aggiunto anche nel blocco `except` di `verifica_credenziali` — copre errori DB | `src/database.py` |
| WK-R1 | Filtro asset senza ticker prima di `_batch_download` — evita `None` nel tuple passato a `yf.download` | `src/portfolio.py` |
| WK-R2 | Guard `zero/NaN` su `serie.iloc[0]` in `get_azioni_snapshot` — evita `inf` silenzioso su perf 1y | `src/portfolio.py` |
| WK-R3 | `carica_posizioni`: limite alzato a 1000 con log warning se troncato | `src/positions.py` |
| WK-E1 | `get_azioni_snapshot` usa `_batch_download` invece di N chiamate yfinance sequenziali | `src/portfolio.py` |
| WK-RF1 | `_AUTH_LOCKOUT_DELAYS`/`_AUTH_MAX_FAILURES` estratte come costanti modulo — rimossi alias locali duplicati nelle funzioni login e TOTP | `app.py` |

### ✅ Fix performance (24-26/09/2026)

| # | Fix | File |
|---|---|---|
| E1 | N+1 INSERT → batch `insert(records, ignore_duplicates=True)` | `src/database.py` |
| E2 | `get_client()` → singleton `@st.cache_resource` | `src/database.py` |
| E3 | `get_azioni_snapshot()`: 2× yfinance → 1 (YTD come subset di 1y) | `src/portfolio.py` |
| E4 | `carica_posizioni()` cacheata in `session_state['pos_df_cache']` | `app.py` |
| E5 | Backfill: ISIN raggruppati per `data_da` → `scarica_tutti_storici()` batch | `src/positions.py` |
| E6 | `iterrows()` → `to_dict('records')` in `salva_prezzi()` e `salva_transazioni()` | `src/positions.py`, `src/database.py` |
| E7 | `carica_ultime_quote_fondi()`: RPC `get_latest_quotes()` con fallback | `src/database.py` |
| E8 | `_build_client` a livello modulo con `@cache_resource` | `src/database.py` |
| E9 | `get_fondi()` cacheata in `session_state['fondi_df_cache']` | `app.py` |
| E10 | `_APP_PASSWORD` cacheato in `session_state['_app_pwd_cache']` | `app.py` |
| E11-E21 | `.limit()` su tutte le query Supabase (evita troncamento silenzioso a 1000 righe) | `src/positions.py`, `src/database.py` |
| P01-P06 | Cache session_state per ETF perf, azioni, transazioni, patrimonio log, portafoglio storico, eventi storico | `app.py` |
| P07 | `_init_plotly_template()` skip rebuild se tema invariato | `app.py` |
| P08 | `nascita_figlio` da `session_state['params']`; no mutazione di `config` cached | `app.py` |
| P09 | Gestione Asset usa `session_state['asset_catalog']` (no query fresca) | `app.py` |
| P10 | Rimosso reload ridondante `asset_catalog` dopo backfill | `app.py` |
| P11 | `piano_uscita_ottimale()`: `iterrows()+.at[]` → `to_dict('records')`; costanti fuori dai loop | `src/portfolio.py` |
| P12 | `get_etf_history_chart()` unificata su `_batch_download` (poi rimossa come dead code il 13/10) | `src/portfolio.py` |
| P13-P15 | `iterrows()` → vettoriale in `carica_quote_fondi_persistenti`, `aggiorna_quote_fondi`, `carica_asset_tickers` | `src/app_state.py`, `src/database.py` |
| P-NEW-01 | Grafico confronto ETF: N download separati → 1 batch + `_extract_series` per ticker | `app.py` |
| P-NEW-02 | `simula_uscita_fondo_data_x()` e `piano_uscita_ottimale()`: parametro `fondi_df=` da cache | `src/portfolio.py`, `app.py` |
| P-NEW-03 | `iterrows()` → `to_dict('records')` in entrambi gli adapter bancari | `src/adapters.py` |
| P-NEW-04 | Due `iterrows()` separati su `asset_catalog` in Portafoglio storico → 1 `to_dict('records')` | `app.py` |
| P-NEW-05 | `get_storico_portafoglio()` riceve `isins=` da `session_state['asset_catalog']` | `src/app_state.py`, `app.py` |
| P-NEW-W1 | `_wiz_params_cache` in session_state nel wizard — evita N query Supabase durante navigazione step | `src/wizard.py` |
| P-NEW-A1 | Batch download confronto ETF candidati → `_batch_download` + `_extract_series` | `app.py` |
| P-NEW-A2 | 3× iterrows su `_etf_cat` → singolo `to_dict('records')` | `app.py` |
| P-NEW-A3 | 4× iterrows sezione fondi → `to_dict('records')` | `app.py` |
| P-NEW-A4 | iterrows su ETF attivi metrics → `to_dict('records')` | `app.py` |
| P-NEW-I1 | `inizializza_posizioni()` → `to_dict('records')` su catalog | `src/positions.py` |
| P-NEW-D1 | `.limit(1)` in `verifica_token` — evita scan completo tabella | `src/database.py` |

### ✅ Ottimizzazioni caricamento (27/09/2026)

| # | Fix | File |
|---|---|---|
| OPT-1 | **`@st.fragment`** su 3 sezioni interattive: grafico storico ETF, confronto candidati, simulatori fondi — slider non triggerano rerun intera app | `app.py` |
| OPT-2 | **Lazy loading yfinance** — `get_etf_perf()`, `get_azioni()`, backfill solo dentro "Stato di famiglia"; altre sezioni zero yfinance | `app.py` |
| OPT-3 | **Lazy XLS import** — `parse_all_inputs()` solo su "Stato di famiglia" e "Fine mese"; Portafoglio, ETF, Simulatore non toccano il parser | `app.py` |

### ✅ Fix code review (09/10/2026)

| # | Fix | File |
|---|---|---|
| CR1 | `_auth_active_ts` aggiunta al cleanup del button "Esci" — mancava nel logout manuale (era già in timeout e revoca admin) | `app.py` |
| CR2 | `if not first_price or pd.isna(first_price):` — guard NaN esplicito in `get_etf_history_chart` (`bool(np.nan)` è True, bypassava la condizione) | `src/portfolio.py` |
| CR3 | `bollo_soglia_liquidita` letta da `p_saved` (Supabase) con fallback `config.yaml` — editabile senza redeploy come FIRE/rebalancing | `app.py` |
| CR4 | `_liq_conti` calcolata da `snap['liquidita'] - affitto_accantonato` invece di ri-sommare 3 chiavi — evita drift futuro se `patrimonio_snapshot` aggiunge nuove fonti | `app.py` |

### ✅ Fix qualità e sicurezza (07/10/2026)

| # | Fix | File |
|---|---|---|
| Q1 | `get_fondi_snapshot()`: `config['patrimonio']['fondi_costo_fiscale']` → `.get()` (prevenzione KeyError) | `src/portfolio.py` |
| Q2 | `_auth_active_ts` aggiunto alla lista di chiavi azzerata su timeout sessione e su revoca accesso admin | `app.py` |
| Q3 | `iterrows()` → `to_dict('records')` nel loop eventi portafoglio (unica violazione rimasta, CLAUDE.md) | `app.py` |
| Q4 | `pass` → `continue` in `calcola_portafoglio_storico` (intent = skip ISIN senza posizioni) | `src/positions.py` |
| Q5 | `{k: v for k, v in asset.items()}` → `dict(asset)` in `upsert_asset` | `src/database.py` |
| Q6 | Rimosso import ridondante `scarica_tutti_storici` nel body di `backfill_prezzi` (già a livello modulo) | `src/positions.py` |
| Q7 | `not first_price or first_price == 0` → `not first_price` (condizione ridondante, poi integrata con NaN guard in CR2) | `src/portfolio.py` |

### ✅ Fix infrastruttura (24-25/09/2026)

| # | Fix | File |
|---|---|---|
| T1-T11 | Credenziali Supabase, RLS, nomi generici persona1/persona2, auth fail-secure | vari |
| T12-T18 | KeyError quantita, privacy `figlio`, auto-seeding disabilitato, crash simulator | vari |

---

## Roadmap — Feature da implementare

### 🔴 Alta priorità

| # | Feature | Moduli coinvolti | Note |
|---|---|---|---|
| 1 ✅ | **FIRE Progress tracker** — Regular FIRE + Coast FIRE, barra avanzamento, anni mancanti | `app.py`, `simulator.py`, `config.yaml` | Completato 27/09/2026. `calcola_fire_metrics()` in `simulator.py`. Parametri (spese, età) configurabili da UI → `config_params`; stima spese automatica da transazioni. |
| 2 ✅ | **Savings Rate** — KPI mensile, YTD, media 12 mesi in "Stato di famiglia" | `app.py` | Completato 27/09/2026. Calcolato da `tx_db_cache` già in cache, nessuna query aggiuntiva. |
| 3 ✅ | **Rebalancing alert ETF** — tabella target/attuale/drift e importo € da ribilanciare | `app.py`, `asset_catalog` | Completato 27/09/2026. `target_pct` editabile da UI → `asset_catalog` Supabase (fallback `config.yaml`). Migration SQL in `docs/setup_supabase_schema.sql`. |
| 4 ✅ | **Liabilities nel net worth** — debiti dedotti dal patrimonio totale | `app.py`, `portfolio.py` | Completato 27/09/2026. Input `debiti_totale` in "Aggiorna valori manuali"; `patrimonio_snapshot()` restituisce `net_worth` e `debiti_totale`. |
| 5 ✅ | **Onboarding wizard** — procedura guidata al primo avvio (15 step) per compilare tutti i parametri fondamentali | `app.py`, `src/wizard.py` (nuovo) | Completato 27/09/2026. Gate in app.py dopo auth; admin può rieseguire dalla sidebar. **Per aggiungere step a nuove feature: v. istruzioni in `src/wizard.py` (docstring + `WIZARD_STEPS`).** |
| 6 ✅ | Aggiornamento automatico docs al deploy via GitHub Actions | `docs/build_html_docs.py`, `.github/workflows/build-docs.yml` | Completato. |
| 7 ✅ | **Demo data completa** — tutti i tab coperti: asset catalog, ETF perf, azioni, prezzi yfinance, posizioni, CRUD no-op | `app.py`, `src/demo_data.py` | Completato 27/09/2026. Gestione Asset disabilitata in DEMO con placeholder. |

### 🟡 Media priorità

| # | Feature | Moduli coinvolti | Note |
|---|---|---|---|
| 8 | **XIRR stimato** — rendimento annualizzato via net cash flow | `simulator.py`, `app.py` | Richiede `scipy.optimize` o XIRR manuale. |
| 9 ✅ | **Bollo threshold alert** — avviso se cash > soglia configurabile | `app.py`, `config.yaml` | Completato 07/10/2026. Calcola `snap['liquidita'] - affitto_accantonato`; soglia da `config_params.bollo_soglia_liquidita` (fallback `config.yaml`, default €5.000) — editabile da Supabase senza redeploy. |
| 10 | **YoY cash flow** — delta vs anno precedente per inflows/expenses/net CF | `app.py`, `app_state.py` | Estensione sezione transazioni. |
| 11 | **Blocked Assets storico** — valorizzazioni periodiche asset illiquidi (immobili, previdenza) | `database.py`, `app_state.py`, `app.py` | Nuova tabella Supabase `blocked_assets_log`. |
| 16 | **AI Spending Analysis** — categorizzazione transazioni + analisi AI con benchmark ISTAT e confronto storico | `app.py`, `simulator.py`, `database.py`, `config.yaml`, `config_params` | Design finalizzato 10/10/2026. Vedi memory `feature_ai_spending_analysis.md` per architettura completa. Richiede `ANTHROPIC_API_KEY` in secrets.toml. **Ordine implementazione:** (1) schema DB + taxonomy manager UI, (2) profilo famiglia editabile, (3) scrubbing + amount-based matching giroconti, (4) UI categorizzazione con AI suggest, (5) analisi AI con output strutturato, (6) batch retroattivo. |

### 🟢 Bassa priorità

| # | Feature | Moduli coinvolti | Note |
|---|---|---|---|
| 12 ✅ | **Geo/sector ETF** — breakdown geografico aggiornabile dai factsheet | `app.py`, `config.yaml` | Completato 27/09/2026. Dati statici in `config.yaml → etf_breakdown`; grafici a torta geo/settore nella sezione ETF. |
| 13 ✅ | **Data freshness indicator** — riepilogo allineamento dati nella sidebar | `app.py` | Completato 27/09/2026. Expander "📡 Allineamento dati" nella sidebar: snapshot, backfill ETF, quote fondi, ultima tx. |
| 14 ✅ | **Month-end checklist** — pannello guidato 4 passi per validare il mese | `app.py` | Completato 27/09/2026. Nuova sezione "📋 Fine mese" nel nav con progress bar e 4 passi guidati. |
| 15 ✅ | **Gestione Asset — elimina per nome** — selectbox mostra `nome (ISIN)` invece del solo ISIN | `app.py` | Completato 27/09/2026. `format_func` su 3 selectbox (fondi, ETF, azioni). |
| 17 | **Distribuzione desktop (Windows / macOS)** — eseguibile standalone installabile, DB locale, solo chiamate online per dati aggiornati | `database.py`, `config.yaml`, `app.py`, packaging scripts | Vedi dettaglio sotto. |

---

## Feature #17 — Distribuzione desktop (Windows / macOS)

Obiettivo: pacchettizzare l'app come eseguibile installabile che gira interamente in locale, mantenendo solo le chiamate di rete strettamente necessarie (prezzi ETF/azioni via yfinance, quote fondi se esposti via API, opzionalmente sync Supabase cloud).

### Architettura target

| Componente | Attuale (cloud) | Target (locale) |
|---|---|---|
| Database | Supabase Postgres hosted | SQLite locale (`~/.piano-familiare/piano.db`) |
| Auth | Supabase `app_users` | SQLite stessa tabella; stesso schema e logica PBKDF2 |
| Config | Supabase `config_params` | SQLite `config_params`; import/export JSON |
| File XLS | `data/input/` | Cartella locale scelta dall'utente |
| Prezzi ETF/azioni | yfinance (già online) | Invariato — yfinance resta online |
| Quote fondi | Supabase `quote_fondi` | Inserimento manuale locale; sync opzionale da cloud |
| Backup | — | Export JSON/SQLite con un click |

### Chiamate di rete mantenute (invariate)

- **yfinance** — prezzi storici e correnti ETF/azioni
- **Claude API** (se feature #16 attiva) — categorizzazione AI + analisi spese
- **Sync opzionale Supabase** — per chi vuole tenere allineati cloud e locale (non obbligatorio)

### Stack di packaging

- **Electron + Streamlit embedded** — Electron lancia Streamlit come processo figlio su porta locale (127.0.0.1:8501); webview punta a localhost. Un installer NSIS (Windows) / DMG (macOS) distribuisce tutto.
- Alternativa più semplice: **PyInstaller + launcher script** — crea un `.exe` / `.app` che avvia Streamlit; l'utente interagisce dal browser. Meno integrato ma molto più rapido da implementare.
- Python bundled con PyInstaller (no dipendenza Python installato sul PC dell'utente).

### Layer database da astrarre

`src/database.py` va separato in:
- `src/db_backend.py` — interfaccia astratta (Protocol/ABC): `get`, `upsert`, `delete`, query base
- `src/db_supabase.py` — implementazione attuale (invariata)
- `src/db_sqlite.py` — implementazione SQLite con stesso schema (migrate con `alembic` o script SQL)

`DATABASE_BACKEND` in `secrets.toml` / variabile d'ambiente: `supabase` (default) | `sqlite`.

### Ordine implementazione

1. Astrazione `db_backend.py` con Protocol — nessuna rottura per il cloud
2. `db_sqlite.py` — SQLite con schema identico a Supabase; migration script da `docs/setup_supabase_schema.sql`
3. Switch runtime via `secrets.toml → DATABASE_BACKEND`
4. Test parità funzionale cloud ↔ locale
5. PyInstaller build script (Windows first, poi macOS)
6. Electron wrapper opzionale per esperienza nativa (no browser esterno)
7. Installer NSIS / DMG

### Note critiche

- `config.yaml` e `data/` restano in cartella applicazione — stesso vincolo attuale (non sovrascrivere)
- Credenziali yfinance: nessuna (API pubblica) — invariato
- `ANTHROPIC_API_KEY` se feature #16 attiva: rimane in `secrets.toml` locale
- Auth TOTP (2FA): `pyotp` non richiede rete — funziona offline
- Backup SQLite: copia del file `.db` è sufficiente come backup completo

---

## Supabase keep-alive (✅ implementato 15/10/2026)

Il free tier di Supabase mette il progetto in **standby dopo 7 giorni senza attività**. Risolto con GitHub Actions cron — ping alla REST API ogni 4 giorni.

File: **`.github/workflows/keep-supabase-alive.yml`**

```yaml
name: Keep Supabase alive

on:
  schedule:
    - cron: '0 8 */4 * *'   # ogni 4 giorni alle 08:00 UTC

jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - name: Ping Supabase REST API
        run: |
          curl -s -o /dev/null -w "%{http_code}" \
            "${{ secrets.SUPABASE_URL }}/rest/v1/config_params?select=chiave&limit=1" \
            -H "apikey: ${{ secrets.SUPABASE_ANON_KEY }}" \
            -H "Authorization: Bearer ${{ secrets.SUPABASE_ANON_KEY }}"
```

**Secrets GitHub da aggiungere** (repo → Settings → Secrets and variables → Actions):
- `SUPABASE_URL` — es. `https://xxxx.supabase.co`
- `SUPABASE_ANON_KEY` — chiave **anon** (pubblica, non la service role key)

Alternativa senza codice: **cron-job.org** — job HTTP GET con header `apikey: <anon_key>` ogni 4 giorni.

---

## Note critiche

- **xlrd==1.2.0** — fissato a questa versione, NON aggiornare (rompe il parsing BIFF8)
- **config.yaml** — non sovrascrivere quando si aggiorna il codice; **non inserirvi asset o importi reali** (vanno in Supabase tramite wizard / Gestione Asset)
- **data/** — non sovrascrivere quando si aggiorna il codice
- Le credenziali Supabase vanno in `.streamlit/secrets.toml` (gitignored) o variabili d'ambiente — mai hardcodate
- `inizializza_posizioni()` va chiamata solo una volta
- **Non usare nomi personali** nel codice, config.yaml o DB — usare `persona1`/`persona2`
- **Non aggiungere `APP_PASSWORD`** a Streamlit Cloud secrets — la password viene esclusivamente da Supabase `config_params.app_password`
- **Non bypassare auth in DEMO mode** — `_DEMO` controlla i dati mostrati, non l'autenticazione
- **Non mutare `config`** (oggetto `@st.cache_data`) — costruire copie locali con `dict(config)` o `.copy()`
- **Supabase Security Advisor — `rls_auto_enable()`**: se segnala SECURITY DEFINER, eseguire: `ALTER FUNCTION public.rls_auto_enable() SECURITY INVOKER;`
