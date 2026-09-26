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

- **ETF / azioni ACN:** batch download `_batch_download(tickers, period)` con cache `@st.cache_data(ttl=1800)`. `get_etf_history_chart()` usa lo stesso `_batch_download` (non più `yf.Ticker` separato).
- **Fondi bancari italiani:** prezzi da `quote_map` (Supabase `quote_fondi`). Fallback a `valore_quota_ref` in `asset_catalog` o `config.yaml`.
- **Portafoglio storico:** prezzi giornalieri in `prezzi_storici`, quantità in `posizioni` — `positions.py` ricostruisce `valore = quantità(giorno) × prezzo(giorno)` con forward-fill per weekend/festivi. Gli ISIN sono passati da `session_state['asset_catalog']` per evitare query ridondante.

### Parsing XLS bancari

`src/parser.py` legge `config.yaml → banche[].formato` e delega a `src/adapters.py`:
- `bper_xls` → xlrd 1.2.0 BIFF8 (estratti vecchio formato, conto Persona 1)
- `bper_xls_new` → openpyxl (estratti nuovo formato, conto Persona 2 e recenti Persona 1)

Le keyword per escludere i bonifici interni (`transfer_keywords` da Supabase) vengono passate come parametro `keywords=` a `adapter.parse()` e poi a `is_internal_transfer()` — **non** più come mutazione globale di `TRANSFER_KEYWORDS`.

File XLS da mettere in `data/input/` — vengono importati automaticamente all'avvio.

### Autenticazione

La password di accesso viene **esclusivamente** da Supabase `config_params` → chiave `app_password`.

- **dev:** `_DEMO = True` hardcoded — mostra dati fittizi, ma auth Supabase sempre richiesta
- **main:** `_DEMO = bool(st.secrets.get("DEMO_MODE", False))` — con `DEMO_MODE = false` usa dati reali

Comportamento fail-secure: se Supabase non raggiungibile o `app_password` non presente → `st.stop()`. Mai accesso libero. `APP_PASSWORD` non esiste e non va aggiunto ai secrets Streamlit Cloud.

Password hashed PBKDF2-HMAC-SHA256. `verify_password()` tronca l'input a 1024 char prima di hashare (guard DoS).

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

## Stato tecnico al 26/09/2026

### Ultimo commit su main: `ac70efc` — Sicurezza e performance completati

Tutti i fix tecnici identificati da due cicli completi di scansione sicurezza + performance sono stati implementati. Il codebase è attualmente in uno stato pulito su entrambi i branch (`dev` = `main`).

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
| P12 | `get_etf_history_chart()` unificata su `_batch_download` | `src/portfolio.py` |
| P13-P15 | `iterrows()` → vettoriale in `carica_quote_fondi_persistenti`, `aggiorna_quote_fondi`, `carica_asset_tickers` | `src/app_state.py`, `src/database.py` |
| P-NEW-01 | Grafico confronto ETF: N download separati → 1 batch + `_extract_series` per ticker | `app.py` |
| P-NEW-02 | `simula_uscita_fondo_data_x()` e `piano_uscita_ottimale()`: parametro `fondi_df=` da cache | `src/portfolio.py`, `app.py` |
| P-NEW-03 | `iterrows()` → `to_dict('records')` in entrambi gli adapter bancari | `src/adapters.py` |
| P-NEW-04 | Due `iterrows()` separati su `asset_catalog` in Portafoglio storico → 1 `to_dict('records')` | `app.py` |
| P-NEW-05 | `get_storico_portafoglio()` riceve `isins=` da `session_state['asset_catalog']` | `src/app_state.py`, `app.py` |

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
| 1 | **FIRE Progress tracker** — Regular FIRE (target configurabile) + Coast FIRE, barra avanzamento, anni mancanti | `app.py`, `simulator.py`, `config.yaml` | Il Coast FIRE è già vicino al target stimato. |
| 2 | **Savings Rate** — KPI mensile, YTD, media 12 mesi in "Stato di famiglia" | `app.py`, `app_state.py` | Derivabile da `carica_transazioni_db()` già disponibile. |
| 3 | **Rebalancing alert ETF** — tabella target/attuale/drift e importo € da ribilanciare | `app.py`, `portfolio.py`, `config.yaml` | Target allocation da aggiungere in `config.yaml`. |
| 4 | **Liabilities nel net worth** — debiti dedotti dal patrimonio totale e inclusi in `patrimonio_log` | `app.py`, `database.py`, `config.yaml` | I debiti sono già in `config.yaml → debiti[]` ma non appaiono nei KPI. |
| 5 | **Onboarding wizard** — procedura guidata al primo avvio per compilare i parametri fondamentali | `app.py`, `app_state.py`, nuovo `src/cloud_storage.py` | Opzione: salvare `config.yaml` su Supabase Storage (zero nuove dipendenze). |
| 6 ✅ | Aggiornamento automatico docs al deploy via GitHub Actions | `docs/build_html_docs.py`, `.github/workflows/build-docs.yml` | Completato. |
| 7 | **Demo data** — set dati fittizi per screenshot, onboarding e condivisione | `app.py`, `src/demo_data.py`, `docs/demo_seed.sql` | `src/demo_data.py` esiste ma le funzioni demo non coprono tutti i tab. |

### 🟡 Media priorità

| # | Feature | Moduli coinvolti | Note |
|---|---|---|---|
| 8 | **XIRR stimato** — rendimento annualizzato via net cash flow | `simulator.py`, `app.py` | Richiede `scipy.optimize` o XIRR manuale. |
| 9 | **Bollo threshold alert** — avviso se cash > €5.000 | `app.py` | Soglia configurabile in `config.yaml`. |
| 10 | **YoY cash flow** — delta vs anno precedente per inflows/expenses/net CF | `app.py`, `app_state.py` | Estensione sezione transazioni. |
| 11 | **Blocked Assets storico** — valorizzazioni periodiche asset illiquidi (immobili, previdenza) | `database.py`, `app_state.py`, `app.py` | Nuova tabella Supabase `blocked_assets_log`. |

### 🟢 Bassa priorità

| # | Feature | Moduli coinvolti | Note |
|---|---|---|---|
| 12 | **Geo/sector ETF** — breakdown geografico aggiornabile dai factsheet | `app.py`, `config.yaml` | Dati statici, nessuna automazione. |
| 13 | **Data freshness indicator** — riepilogo allineamento dati nella sidebar | `app.py`, `app_state.py` | UX improvement. |
| 14 | **Month-end checklist** — pannello guidato 4 passi per validare il mese | `app.py` | UX improvement. |
| 15 | **Gestione Asset — elimina per nome** — selectbox mostra `nome (ISIN)` invece del solo ISIN | `app.py` | UX improvement segnalato dall'utente. |

---

## Note critiche

- **xlrd==1.2.0** — fissato a questa versione, NON aggiornare (rompe il parsing BIFF8)
- **config.yaml** — non sovrascrivere quando si aggiorna il codice
- **data/** — non sovrascrivere quando si aggiorna il codice
- Le credenziali Supabase vanno in `.streamlit/secrets.toml` (gitignored) o variabili d'ambiente — mai hardcodate
- `inizializza_posizioni()` va chiamata solo una volta
- **Non usare nomi personali** nel codice, config.yaml o DB — usare `persona1`/`persona2`
- **Non aggiungere `APP_PASSWORD`** a Streamlit Cloud secrets — la password viene esclusivamente da Supabase `config_params.app_password`
- **Non bypassare auth in DEMO mode** — `_DEMO` controlla i dati mostrati, non l'autenticazione
- **Non mutare `config`** (oggetto `@st.cache_data`) — costruire copie locali con `dict(config)` o `.copy()`
- **Supabase Security Advisor — `rls_auto_enable()`**: se segnala SECURITY DEFINER, eseguire: `ALTER FUNCTION public.rls_auto_enable() SECURITY INVOKER;`
