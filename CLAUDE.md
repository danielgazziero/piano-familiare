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
- **Grafici:** Plotly (go.Figure / px)
- **Database:** Supabase (Postgres hosted) — credenziali in `.streamlit/secrets.toml` (gitignored) via `st.secrets`
- **Prezzi ETF/azioni:** yfinance — ticker in `config.yaml` e `src/prices.py`
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

1. `load_config()` — carica `config.yaml` (@st.cache_data ttl=3600)
2. `init_db_connection()` — verifica Supabase; l'app funziona anche offline
3. Carica `params` da `config_params` (DB) o fallback da config.yaml
4. Carica `quote_map` `{isin: quota}` da `quote_fondi` (DB) o fallback da config.yaml
5. Auto-import XLS da `data/input/` → `salva_transazioni()` (deduplicato via MD5)
6. Auto-save snapshot patrimonio odierno (upsert su `patrimonio_log`)
7. Backfill silenzioso: scarica prezzi mancanti per tutti gli ISIN noti

### Valorizzazione asset

- **ETF / azioni ACN:** prezzi in tempo reale via yfinance — `src/prices.py` + `src/portfolio.py`
- **Fondi bancari italiani:** `ticker_yf: null` in config — prezzi non disponibili su Yahoo. Il valore viene da `quote_map` (`quote_fondi` su Supabase, aggiornato manualmente via XLS o UI). Il fallback è `valore_quota_ref` in config.yaml.
- **Portafoglio storico:** prezzi giornalieri in `prezzi_storici` (Supabase), quantità in `posizioni` con date di inizio/fine — `positions.py` ricostruisce `valore = quantità(giorno) × prezzo(giorno)` con forward-fill per weekend/festivi.

### Parsing XLS bancari

`src/parser.py` legge `config.yaml → banche[].formato` e delega a `src/adapters.py`:
- `bper_xls` → xlrd 1.2.0 BIFF8 (estratti vecchio formato, conto Persona 1)
- `bper_xls_new` → openpyxl (estratti nuovo formato, conto Persona 2 e recenti Persona 1)

File XLS da mettere in `data/input/` — vengono importati automaticamente all'avvio.

### Autenticazione

La password di accesso viene **esclusivamente** da Supabase `config_params` → chiave `app_password`.

- **dev:** `_DEMO = True` hardcoded — mostra dati fittizi, ma auth Supabase sempre richiesta
- **main:** `_DEMO = bool(st.secrets.get("DEMO_MODE", False))` — con `DEMO_MODE = false` usa dati reali

Comportamento fail-secure: se Supabase non raggiungibile o `app_password` non presente → `st.stop()`. Mai accesso libero. `APP_PASSWORD` non esiste e non va aggiunto ai secrets Streamlit Cloud.

`carica_param()` in `src/database.py` prova `json.loads()` sul valore, poi fallback a raw string — consente inserimento manuale in Supabase senza encoding JSON.

## Schema Supabase

Tabelle principali (SQL completo in `src/database.py` e `src/positions.py`):

| Tabella | Contenuto |
|---|---|
| `patrimonio_log` | Snapshot giornaliero patrimonio totale per componente (upsert per `data`) |
| `quote_fondi` | Quote fondi bancari nel tempo (upsert per `data, isin`) |
| `transazioni` | Movimenti bancari da XLS (deduplicati via `hash_tx` MD5) |
| `config_params` | Key-value store per valori manuali (liquidità, conto comune, ecc.) |
| `posizioni` | Quantità asset per intervallo date (`data_inizio`, `data_fine` NULL=attiva) |
| `prezzi_storici` | Prezzi giornalieri ETF/azioni (upsert per `isin, data`) |
| `backfill_stato` | Ultima data scaricata per ISIN (checkpoint backfill) |

Schema da creare su Supabase SQL Editor: vedere `docs/setup_supabase_schema.sql`. Le tabelle `posizioni`, `prezzi_storici`, `backfill_stato` sono in `src/positions.py → POSITIONS_SCHEMA_SQL`.

## Convenzioni di codice

### Nomi generici (privacy — repo pubblico)

Non usare mai nomi reali delle persone nel codice, config o DB. Usa sempre:
- `persona1` al posto del nome reale della Persona 1
- `persona2` al posto del nome reale della Persona 2

**Esempi corretti:** `liquidita_persona1`, `etf_persona1`, `pac_persona1_ora`, `intestatario: "persona1"`

I valori reali legati alle persone (keyword bonifici interni, pattern nomi file XLS) vengono **esclusivamente da Supabase** `config_params` — vedi sezione "Riconciliazione dati personali" qui sotto.

### Riconciliazione dati personali (pattern file e keyword bonifici)

Il codice è completamente agnostico rispetto alle persone. I valori specifici vengono da Supabase:

| Chiave Supabase (`config_params`) | Contenuto | Usato in |
|---|---|---|
| `nome_persona1` | Nome dell'intestatario conto Persona 1 | `src/parser.py` → pattern file XLS |
| `nome_persona2` | Nome dell'intestatario conto Persona 2 | `src/parser.py` → pattern file XLS |
| `transfer_keywords` | JSON array di stringhe per escludere bonifici interni tra conti di famiglia | `src/parser.py` → `src/adapters.py` |

**Come funziona la riconciliazione file XLS:**
`config.yaml` contiene `pattern_template: "Lista_Movimenti_{nome}*"` per ogni banca. A runtime, `parser.py` legge `nome_<account>` da Supabase e costruisce il pattern reale (`_risolvi_pattern()`). Il codice non conosce mai i nomi delle persone.

`TRANSFER_KEYWORDS` in `adapters.py` è `[]` di default e viene popolata a runtime da `transfer_keywords` in Supabase.

### Altre convenzioni

- Colori come costanti dict in cima ad `app.py`: `COLORS`, `SC_COLORS`, `SC_DASH`
- Cache con `@st.cache_data(ttl=1800)` per chiamate yfinance; `ttl=3600` per config
- `st.cache_data.clear()` + pulizia `st.session_state` nel pulsante "🔄 Aggiorna tutto"
- Valori monetari in EUR (eccetto ACN in USD — `valore_attuale_usd`)
- Date: formato italiano `DD/MM/YYYY` nell'UI, ISO `YYYY-MM-DD` internamente e in DB
- Le funzioni in `src/simulator.py` sono pure (no side-effect, no DB, no yfinance)

## Asset in portafoglio

Metadati pubblici (ISIN, ticker) in `config.yaml`. Quantità, valori e quote reali esclusivamente in Supabase (`asset_catalog`, `posizioni`, `quote_fondi`).

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

## Roadmap — Feature da implementare

Priorità derivata dall'analisi comparata con il Net Worth Tracker Excel (set 2026).

### ✅ Fix tecnici completati (24/09/2026)

| # | Fix | File |
|---|---|---|
| T1 | Credenziali Supabase → `st.secrets` + `.streamlit/secrets.toml` (non committato) | `src/database.py`, `.gitignore` |
| T2 | N+1 INSERT → batch `insert(records, ignore_duplicates=True)` | `src/database.py:salva_transazioni()` |
| T3 | `get_client()` → singleton `@st.cache_resource` | `src/database.py` |
| T4 | Full table scan `quote_fondi` → query con `.limit(500)` | `src/database.py:carica_ultime_quote_fondi()` |
| T5 | Date re-parsate per ogni giorno → pre-parse una sola volta in `_posizioni_parsed` | `src/positions.py:calcola_valore_giornaliero()` |
| T6 | `NuovaBancaAdapter` stub rimosso da `ADAPTER_REGISTRY` | `src/adapters.py` |
| — | `timedelta` aggiunto agli import mancanti | `app.py:11` |
| T7 | Auth fail-secure — password solo da `config_params.app_password` su Supabase, `APP_PASSWORD` rimosso da Streamlit Cloud secrets; dev protetto anche in DEMO mode | `app.py` |
| T8 | `carica_param` fallback a raw string se valore non è JSON valido (consente inserimento manuale senza encoding) | `src/database.py:carica_param()` |
| T9 | Nomi generici `persona1`/`persona2` in tutto il codice, `config.yaml` e schema DB; migrazione colonne Supabase | tutti i file `src/`, `config.yaml`, `setup_supabase.py`, `docs/setup_supabase_schema.sql` |
| T10 | RLS policy tutte le tabelle → `USING (auth.role() = 'service_role')` (anon/authenticated bloccati) | `setup_supabase.py`, `src/positions.py → POSITIONS_SCHEMA_SQL` |
| T11 | `public.rls_auto_enable()` SECURITY DEFINER → SECURITY INVOKER (Supabase Security Advisor) | SQL Supabase: `ALTER FUNCTION public.rls_auto_enable() SECURITY INVOKER;` |

### 🔴 Alta priorità

| # | Feature | Moduli coinvolti | Note |
|---|---|---|---|
| 1 | **FIRE Progress tracker** — Regular FIRE (target configurabile) + Coast FIRE con barra avanzamento, anni mancanti proiettati e rendimento assunto | `app.py`, `simulator.py`, `config.yaml` | Attualmente assente. Il Coast FIRE è già vicino al target stimato. |
| 2 | **Savings Rate** — KPI mensile, YTD, media 12 mesi nella sezione "Stato di famiglia" | `app.py`, `app_state.py` | Facile: derivabile da `carica_transazioni_db()` già disponibile. |
| 3 | **Rebalancing alert ETF** — tabella target/attuale/drift (pp) e importo € da comprare/vendere per rientrare in policy (es. 80/20 World/EM) | `app.py`, `portfolio.py`, `config.yaml` | Target allocation da aggiungere in `config.yaml`. |
| 4 | **Liabilities nel net worth** — dedurre debiti dal patrimonio totale e includerli nello storico `patrimonio_log` | `app.py`, `database.py`, `config.yaml` | I debiti sono già in `config.yaml → debiti[]` ma non appaiono nei KPI. |
| 5 | **Onboarding wizard + cloud storage** — procedura guidata al primo avvio (o da sidebar) per compilare tutti i parametri fondamentali (asset, PAC, redditi, debiti) tramite un template Excel multi-sheet o form in-app. I dati vengono salvati su storage cloud (Google Drive via API OAuth2 o Dropbox) anziché su file locale, così sono disponibili su qualsiasi dispositivo e la dashboard li carica automaticamente alle sessioni successive. | `app.py`, `app_state.py`, `config.yaml`, nuovo `src/cloud_storage.py` | Prerequisiti: credenziali OAuth Google Drive o Dropbox API token in `st.secrets`. Template Excel: un foglio per tab (Profilo, Asset, ETF, Fondi, Debiti, Spese). Opzione alternativa: salvare il `config.yaml` compilato direttamente su Supabase Storage (già nel progetto, zero nuove dipendenze). |
| 6 ✅ | **Aggiornamento automatico docs al deploy** — `.github/workflows/build-docs.yml` per GitHub Actions (si attiva su push a main se cambiano `.md` o `build_html_docs.py`). Hook git locale tramite `scripts/install_hooks.bat` (Windows) o `scripts/install_hooks.sh` (Mac/Linux) — eseguire una volta dopo `git init`. | `docs/build_html_docs.py`, `.github/workflows/build-docs.yml`, `scripts/install_hooks.*` | — |
| 7 | **Demo data / dati campione** — set di dati fittizi e realistici (config, posizioni, transazioni, prezzi storici) che popolano interamente l'app senza dati reali. Serve per condividere l'app con altri utenti, fare screenshot, o fare onboarding senza esporre informazioni personali. I dummy data devono coprire ogni tab e ogni KPI della dashboard. | `app.py`, nuovo `src/demo_data.py`, `config_demo.yaml`, `docs/demo_seed.sql` | Implementazione: `config_demo.yaml` con nomi generici (es. "Utente A", banca "Banca Esempio"), `demo_seed.sql` con INSERT su tutte le tabelle Supabase, flag `DEMO_MODE=true` in `st.secrets` che carica i demo data invece del DB reale. I valori devono avere senso finanziario (es. PAC mensile coerente con il patrimonio simulato) per fungere da guida alla compilazione. |

### 🟡 Media priorità

| # | Feature | Moduli coinvolti | Note |
|---|---|---|---|
| 8 | **XIRR stimato** — rendimento annualizzato usando net cash flow come proxy dei flussi esterni al portafoglio | `simulator.py`, `app.py` | Richiede `scipy.optimize` o implementazione manuale XIRR. |
| 9 | **Bollo threshold alert** — avviso se cash reserve > €5.000 (soglia imposta di bollo €34,20/anno) con azione suggerita | `app.py` | Soglia configurabile in `config.yaml`. |
| 10 | **YoY cash flow** — colonna delta vs anno precedente per inflows, expenses, net CF | `app.py`, `app_state.py` | Estensione della sezione transazioni esistente. |
| 11 | **Blocked Assets storico** — tabella aggiornabile per valorizzazioni periodiche di asset illiquidi (immobili, previdenza), separata dal portafoglio liquido | `database.py`, `app_state.py`, `app.py` | Nuova tabella Supabase `blocked_assets_log`. |

### 🟢 Bassa priorità / nice to have

| # | Feature | Moduli coinvolti | Note |
|---|---|---|---|
| 12 | **Geo/sector ETF** — breakdown geografico aggiornabile manualmente ogni trimestre dai factsheet | `app.py`, `config.yaml` | Dati statici, nessuna automazione possibile. |
| 13 | **Data freshness indicator** — riepilogo allineamento dati (ultima data chiusa per ogni dataset) nella sidebar | `app.py`, `app_state.py` | UX improvement. |
| 14 | **Month-end checklist** — pannello guidato 4 passi con stato Aperto/Chiuso per validare il mese | `app.py` | Migliora consistenza dati nel tempo. |
| 15 | **Gestione Asset — elimina per nome** — il form di eliminazione asset usa attualmente l'ISIN come selettore; sostituire con selectbox che mostra `nome (ISIN)` per maggiore usabilità | `app.py` sezione "⚙️ Gestione Asset" | UX improvement segnalato dall'utente. |

---

## Note critiche

- **xlrd==1.2.0** — fissato a questa versione, NON aggiornare (rompe il parsing BIFF8)
- **config.yaml** — non sovrascrivere quando si aggiorna il codice
- **data/** — non sovrascrivere quando si aggiorna il codice
- Le credenziali Supabase vanno in `.streamlit/secrets.toml` (gitignored) o variabili d'ambiente `SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_SECRET` — mai hardcodate nel codice
- `inizializza_posizioni()` va chiamata solo una volta (inserisce le posizioni default nel DB se vuoto)
- **Non usare nomi personali** nel codice, config.yaml o DB — usare `persona1`/`persona2`. I valori sensibili (keyword bonifici, pattern file XLS) vanno in Supabase `config_params`
- **Non aggiungere `APP_PASSWORD`** a Streamlit Cloud secrets — la password app viene esclusivamente da Supabase `config_params.app_password`
- **Non bypassare auth in DEMO mode** — `_DEMO` controlla i dati mostrati, non l'autenticazione
- **Supabase Security Advisor — `rls_auto_enable()`**: funzione interna Supabase, non nel codice. Se il Security Advisor segnala "Public/Signed-In Users Can Execute SECURITY DEFINER Function", eseguire nel SQL Editor: `ALTER FUNCTION public.rls_auto_enable() SECURITY INVOKER;` — il semplice `REVOKE EXECUTE` non basta perché Supabase ha grant a livello di schema
