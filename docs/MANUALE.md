# Net Worth Tracker — Manuale utente
**Versione 2.1 · Settembre 2026**

---

## Indice

1. [Prima installazione — Windows](#1-prima-installazione--windows)
2. [Prima installazione — Mac](#2-prima-installazione--mac)
3. [Setup database Supabase](#3-setup-database-supabase)
4. [Avviare l'app ogni giorno](#4-avviare-lapp-ogni-giorno)
5. [Aggiornamento mensile](#5-aggiornamento-mensile)
6. [Come usare la dashboard](#6-come-usare-la-dashboard)
7. [Cosa fa l'app all'avvio](#7-cosa-fa-lapp-allavvio)
8. [Cambiare banca](#8-cambiare-banca)
9. [Registrare acquisti e vendite fondi](#9-registrare-acquisti-e-vendite-fondi)
10. [Modifica parametri](#10-modifica-parametri)
11. [Upgrade futuro database](#11-upgrade-futuro-database)
12. [Risoluzione problemi](#12-risoluzione-problemi)

---

## 1. Prima installazione — Windows

### Passo 1 — Installa Python
1. Vai su **https://www.python.org/downloads/**
2. Clicca il pulsante "Download Python 3.x.x"
3. Apri il file scaricato
4. ⚠️ **IMPORTANTE:** spunta **"Add Python to PATH"** prima di cliccare Install Now
5. Clicca **Install Now** e attendi

**Verifica:** apri il Prompt dei comandi (tasto Windows → `cmd` → Invio):
```
python --version
```
Deve apparire `Python 3.x.x`.

---

### Passo 2 — Esegui il check automatico
1. Decomprimi la cartella del progetto dove preferisci (es. `C:\Progetti\piano-familiare`)
2. Fai **doppio click su `AVVIA_WINDOWS.bat`**

Lo script controlla tutto, installa le librerie mancanti e ti guida.

Se il doppio click non funziona, apri il Prompt dei comandi:
```
cd C:\Progetti\piano-familiare
python check_and_setup.py
```

---

## 2. Prima installazione — Mac

### Passo 1 — Installa Homebrew (se non ce l'hai)
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### Passo 2 — Installa Python
```bash
brew install python3
```

### Passo 3 — Esegui il check automatico
```bash
cd /percorso/piano-familiare
bash avvia_mac.sh
```

---

## 3. Setup database Supabase

**Da fare una sola volta** dopo il primo avvio.

### Passo 1 — Crea le tabelle
1. Vai su **https://supabase.com** e accedi al tuo progetto
2. Menu a sinistra → **SQL Editor** → **New query**
3. Incolla tutto il contenuto di `docs/setup_supabase_schema.sql`
4. Clicca **Run** — deve apparire "Success"

### Passo 2 — Inizializza i dati
```bash
# Windows
python setup_supabase.py

# Mac
python3 setup_supabase.py
```

Questo carica le posizioni iniziali (fondi, ETF, azioni) nel database.

---

## 4. Avviare l'app ogni giorno

### Windows
Doppio click su **`AVVIA_WINDOWS.bat`**
oppure:
```
python -m streamlit run app.py
```

### Mac
```bash
python3 -m streamlit run app.py
```

Il browser si apre su **http://localhost:8501** — se non si apre automaticamente, digita quell'indirizzo.

Per fermare: premi `Ctrl+C` nel Terminale/Prompt.

---

## 5. Aggiornamento mensile

### Ogni mese (10 minuti in tutto)

**Passo 1 — Scarica gli estratti dalla banca**
1. Accedi all'home banking
2. Vai su Movimenti → Esporta → formato XLS
3. Copia i file in: `data/input/`

**Passo 2 — Avvia l'app**
L'app importa automaticamente i nuovi XLS. Nella sidebar apparirà:
- `📥 N nuove transazioni importate`
- `📡 N nuovi prezzi scaricati`

**Passo 3 — Aggiorna i valori manuali**
Sezione **Stato di famiglia** → pannello **🔧 Aggiorna valori manuali**:
- Liquidità conti
- Valore fondi bancari (dal rendiconto mensile banca)
- Valore Generali (dal rendiconto trimestrale)

Clicca **💾 Salva valori** — vengono salvati su Supabase.

**Passo 4 — Aggiorna le quote fondi** (quando hai il rendiconto banca)
Sezione **Fondi bancari** → tabella editabile → aggiorna la colonna **Quota €**.

---

## 6. Come usare la dashboard

L'app si naviga dalla **sidebar a sinistra**. Ogni sezione è indipendente.

---

### 🏠 Stato di famiglia

**Cosa mostra:** il patrimonio totale attuale, suddiviso per componente.

**Pannello "Aggiorna valori manuali"** (espandibile)
- Inserisci i saldi aggiornati di liquidità, fondi, conto comune
- Clicca **💾 Salva valori** per persistere i dati su Supabase
- I valori vengono ripristinati automaticamente alla prossima apertura

**KPI principali**

| KPI | Significato |
|---|---|
| Patrimonio totale | Somma di tutti gli asset (fondi + ETF + azioni + liquidità + Generali) |
| Netto fiscale | Patrimonio al netto della tassa latente sul capital gain dei fondi (26%) |
| Fondi bancari | Valore attuale dei fondi BPER (quote × prezzo) |
| ETF | Valore portafoglio ETF a prezzi live Yahoo Finance |
| Azioni ACN | Valore Accenture in USD (non convertito) |

**Grafico torta:** composizione del patrimonio per tipo di asset.

**Grafico andamento:** storico patrimonio giornaliero costruito automaticamente — si popola giorno dopo giorno aprendo l'app. Usa il selettore periodo per filtrare (1 mese → tutto).

**Sezione transazioni bancarie:** mostra entrate e uscite per mese, con grafico a barre per categoria e dettaglio transazioni espandibile.

> **Quando usarla:** all'inizio di ogni mese dopo aver aggiornato i valori manuali. È il check generale sulla situazione finanziaria.

---

### 📉 Portafoglio storico

**Cosa mostra:** il valore giornaliero reale del portafoglio (quantità × prezzo giornaliero) con backfill automatico.

**Vista Aggregato**
- KPI: valore attuale, valore a inizio periodo, variazione € e %
- Grafico valore totale con marcatori verticali per acquisti/vendite
- Grafico composizione a area stacked per ISIN

**Vista Per asset**
- Seleziona un singolo fondo/ETF/azione
- Vedi prezzo quota e valore posizione su doppio asse Y
- Tabella storico variazioni quantità
- Form per registrare un nuovo acquisto o vendita parziale

**Registrare un'operazione:**
1. Seleziona l'asset dalla lista
2. Scendi al form "Registra acquisto / vendita parziale"
3. Inserisci la **quantità totale** dopo l'operazione (non la variazione)
4. Inserisci la data reale dell'operazione
5. Clicca **💾 Salva operazione**

> **Quando usarla:** per verificare l'andamento storico reale e dopo ogni acquisto/rimborso fondi.

---

### 📈 ETF & mercato

**Cosa mostra:** performance degli ETF in portafoglio e simulatore PAC.

**ETF attivi:** KPI per ogni ETF (prezzo, YTD, 1 anno).

**Grafico performance storica:** confronto relativo (base 100) tra i titoli selezionati nel periodo scelto. Seleziona uno o più ticker dal multiselect.

**Simulatore PAC — portafoglio ETF personalizzabile**
- Tabella editabile: aggiungi/rimuovi ETF, imposta importo mensile e valore iniziale
- Seleziona orizzonte (anni) e rendimento base
- Il grafico mostra scenario worst/base/best e, se attivo, i singoli ETF
- I KPI sotto il grafico mostrano il valore finale nei tre scenari

**Confronto ETF candidati:** grafico performance relativa di ETF alternativi (EMAE, MWRD, IWDA, CSPX) utile per valutare nuovi acquisti.

> **Quando usarla:** per pianificare nuovi PAC, confrontare alternative, o verificare la performance degli ETF attivi.

---

### 🏦 Fondi bancari

**Cosa mostra:** analisi e scenari di evoluzione per i fondi bancari.

**Tabella editabile fondi**
- Modifica quote, percentuale da mantenere, TER stimato
- I KPI si aggiornano in tempo reale al variare della selezione

**KPI snapshot**

| KPI | Significato |
|---|---|
| Valore selezionato | Valore attuale dei fondi inclusi, pesato per % da mantenere |
| Plusvalenza stimata | Valore - costo fiscale di acquisto |
| Tassa latente 26% | Capital gain × 0,26 se uscissi oggi |
| Netto se esci oggi | Ciò che incassi dopo le tasse |

**Storico quote:** grafico della quota del fondo selezionato negli ultimi 12 mesi (da Supabase).

**Scenari per singolo fondo:** evoluzione worst/base/best del valore lordo e netto su orizzonte configurabile.

**Portafoglio fondi aggregato:** stesso grafico scenari ma per tutti i fondi insieme.

**Simulatore "se esco il giorno X":** proietta il valore e la tassa al rimborso in una data futura.

**Piano di uscita ottimale:** distribuzione annuale consigliata dei rimborsi per minimizzare l'impatto fiscale e reinvestire in ETF.

> **Quando usarla:** per decidere quando e quanto rimborsare dai fondi, in particolare nella pianificazione della migrazione verso ETF.

---

### 📊 Azioni Accenture

**Cosa mostra:** valore attuale delle azioni RSU e simulatore vendita.

- KPI: quantità, prezzo live Yahoo Finance, valore USD, YTD
- Grafico storico prezzi ACN nel periodo selezionato
- **Simulatore vendita:** scegli quante azioni vendere e tra quanti mesi, imposta rendimento worst/base/best annuale. Il grafico mostra il valore proiettato in USD.

> ⚠️ Le RSU Accenture hanno una tassazione separata — verificare con CAF/commercialista prima di vendere.

> **Quando usarla:** per pianificare eventuali vendite parziali e valutare scenari di prezzo.

---

### 🎯 Simulatore strategie

**Tre tab:**

**PAC semplice**
- Importo mensile, orizzonte in anni, rendimento base
- Opzione: simula riduzione PAC con asilo nido (variabile nel tempo)
- Grafico worst/base/best + linea "versato"
- KPI: valore finale nei tre scenari

**Migrazione fondi → ETF**
- Simula il risultato di rimborsare i fondi gradualmente (rimborso annuale configurabile) e reinvestire in ETF
- Confronto diretto: "resto nei fondi" vs "migro in ETF"
- Parametri: rendimento ETF, rendimento lordo fondi, costo annuo fondi

**Scenario patrimoniale completo**
- Simula l'evoluzione dell'intero patrimonio su un orizzonte pluriennale
- Imposta PAC, rendimento, orizzonte
- Opzione: mostra 3 scenari (3% / 7% / 10%) in un unico grafico
- Area chart stacked per componente (ETF, fondi, Generali)

> **Quando usarla:** per prendere decisioni strategiche — quanto versare, quando migrare, cosa aspettarsi nei prossimi 10-20 anni.

---

### 👶 Flor timeline

**Cosa mostra:** proiezione del fondo dedicato a una figlia/figlio dalla nascita all'università.

- Slider PAC mensile e rendimento base
- Grafico worst/base/best con linee verticali per le fasi di vita (nido, elementari, medie, liceo, 18 anni)
- Grafico a barre costi annuali per fascia d'età
- Tabella: valore del fondo, versato totale, fase e costo annuale per ogni anno di età

> **Quando usarla:** per dimensionare il PAC figlio/figlia in base ai costi attesi nelle diverse fasi scolastiche.

---

### ⚙️ Sidebar — Controlli globali

| Controllo | Funzione |
|---|---|
| 🔄 Aggiorna tutto | Svuota la cache, forza il re-download di prezzi e dati dal DB |
| `☁️ Supabase connesso` | Indica che il DB è raggiungibile — se offline l'app funziona ma i dati non vengono salvati |
| `Config: YYYY-MM` | Data dell'ultimo aggiornamento del file `config.yaml` |

---

## 7. Cosa fa l'app all'avvio

Ad ogni avvio, in automatico:

1. **Legge `config.yaml`** — parametri statici (ETF, fondi, allocazione)
2. **Verifica Supabase** — l'app funziona anche offline (dati non vengono salvati)
3. **Carica valori persistenti** — liquidità, quote fondi dall'ultima sessione
4. **Importa XLS** — i nuovi file in `data/input/` vengono letti e caricati nel DB
5. **Salva snapshot patrimonio** — un record al giorno su `patrimonio_log` (upsert)
6. **Backfill prezzi** — scarica tutti i prezzi mancanti dall'ultima apertura ad oggi per tutti gli ISIN

Non devi fare nulla — lo storico si costruisce da solo.

---

## 8. Cambiare banca

**Passo 1** — Scarica un estratto campione dalla nuova banca

**Passo 2** — Invialo con questa richiesta a Claude Code:
> "Ho cambiato banca, ecco un estratto campione. Aggiorna l'adapter."

**Passo 3** — Sostituisci `src/adapters.py` con la versione aggiornata

**Passo 4** — Aggiorna `config.yaml`:
```yaml
banche:
  - id: "nuova_banca"
    nome: "NomeBanca"
    intestatario: "intestatario"
    formato: "nuova_banca_id"
    pattern_file: "Estratto_*"
```

---

## 9. Registrare acquisti e vendite fondi

Sezione **Portafoglio storico** → Vista **Per asset** → seleziona l'asset.

Form in fondo alla pagina:
- **Nuova quantità totale** — inserisci il totale quote dopo l'operazione (non la variazione)
- **Data operazione** — il giorno reale
- **Note** — es. "Rimborso parziale primo anno"

Clicca **💾 Salva operazione**. Da quel giorno il calcolo usa la nuova quantità; i giorni precedenti mantengono quella originale.

---

## 10. Modifica parametri

Tutto in `config.yaml` — aprilo con qualsiasi editor di testo.

### Aggiungere un ETF
```yaml
etf:
  - ticker_yf: "IWDA.AS"
    ticker_bi: "IWDA"
    nome: "iShares Core MSCI World"
    isin: "IE00B4L5Y983"
    valore_iniziale: 19368
    proprietario: "intestatario"
    stato: "attivo"
```

### Aggiornare importo PAC
```yaml
allocazione:
  pac_persona1_ora: 1200
```

### Aggiungere un rimborso effettuato
```yaml
debiti:
  - piano_rimborso:
      - data: "2026-12-01"
        importo: 5000
        effettuato: true
```

---

## 11. Upgrade futuro database

**Migrazione a un altro cloud (Railway, Neon, Render):**
```bash
# Esporta da Supabase Dashboard → Database → Backups
psql postgres://nuovo_host/db < backup.sql
```
Poi aggiorna `SUPABASE_URL` e `SUPABASE_SECRET` in `src/database.py`.

---

## 12. Risoluzione problemi

### "python: comando non trovato" (Windows)
Reinstalla Python spuntando "Add Python to PATH". Alternativa: usa `py` invece di `python`.

### "streamlit: comando non trovato"
```bash
python -m streamlit run app.py
```

### "ModuleNotFoundError"
```bash
pip install -r requirements.txt
```

### L'app non si connette a Supabase
- Verifica connessione internet
- Controlla le credenziali in `src/database.py`
- Esegui `python setup_supabase.py` se è il primo avvio

### I prezzi non si aggiornano
Yahoo Finance ha interruzioni temporanee — attendi e premi 🔄. Se un fondo non carica mai, verifica il ticker in `src/prices.py`.

### "Il browser non si apre"
Vai manualmente su: **http://localhost:8501**
