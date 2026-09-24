# Deploy — Guida ambienti Dev e Produzione

---

## 1. Creare gli ambienti

Il progetto usa **due ambienti separati**: uno locale per sviluppo e anteprima, uno remoto per la versione stabile accessibile da qualsiasi dispositivo.

| Ambiente | URL | Scopo |
|---|---|---|
| **Development (locale)** | `http://localhost:8501` | Anteprima modifiche prima di pubblicarle, test funzionalità, import XLS |
| **Production (Cloud)** | URL Streamlit Community Cloud | Versione stabile, accessibile da browser e smartphone |
| **Production (VPS)** | `http://tuo-server:8501` | Self-hosted su Linux, controllo totale (alternativa al Cloud) |

### Come creare l'ambiente di sviluppo (locale)

**Prerequisiti:** Python 3.10+, pip, accesso a internet per Supabase.

```bash
# 1. Clona o decomprime il progetto
cd C:\Progetti\piano-familiare

# 2. Installa le dipendenze
pip install -r requirements.txt

# 3. Verifica che tutto sia configurato correttamente
python check_and_setup.py
```

Se il check va a buon fine, l'ambiente di sviluppo è pronto.

### Come creare l'ambiente di produzione (Streamlit Cloud)

Vedi la sezione **Production — Streamlit Community Cloud** più avanti in questo documento. In sintesi:
1. Crea un repository GitHub con il codice
2. Collega il repo a [share.streamlit.io](https://share.streamlit.io)
3. Configura i secrets (credenziali Supabase) nell'interfaccia web di Streamlit Cloud

---

## 2. Usare il locale come anteprima prima del deploy

L'ambiente locale è la **staging area** prima di mandare in produzione qualsiasi modifica. Il flusso consigliato è:

```
modifica codice → testa in locale → verifica che tutto funzioni → push su main → produzione si aggiorna
```

### Avvia l'anteprima locale

```bash
cd C:\Progetti\piano-familiare
python -m streamlit run app.py
```

L'app si apre su **http://localhost:8501** e si ricarica automaticamente a ogni modifica ai file `.py` o `config.yaml` — non serve riavviare.

### Checklist prima di fare push in produzione

- [ ] L'app si avvia senza errori in locale
- [ ] La sezione modificata funziona con dati reali
- [ ] Il pulsante "🔄 Aggiorna tutto" non genera eccezioni
- [ ] I grafici si caricano (nessun `None` o traccia vuota)
- [ ] `python check_and_setup.py` non segnala errori
- [ ] I nuovi HTML dei docs sono stati rigenerati (`python docs/build_html_docs.py`)

### Opzioni utili in dev

```bash
# Porta alternativa (se 8501 è occupata)
python -m streamlit run app.py --server.port 8502

# Disabilita l'auto-reload (utile se i reload sono lenti)
python -m streamlit run app.py --server.runOnSave false

# Log dettagliati (per debug)
python -m streamlit run app.py --logger.level debug
```

### Verifica connessioni

```bash
# Controlla dipendenze e connessione Supabase
python check_and_setup.py

# Testa solo connessione DB
python src/database.py

# Testa solo parser XLS
python src/parser.py
```

### Reset completo (cache e stato)
Dalla sidebar dell'app: pulsante **🔄 Aggiorna tutto**.
Oppure da terminale: `Ctrl+C` per fermare, poi riavvia.

---

## 3. Production — Streamlit Community Cloud (consigliato)

La soluzione più semplice per un'app personale: gratuita, nessun server da gestire, accessibile da qualsiasi dispositivo.

### Prerequisiti
- Account GitHub (gratuito)
- Account Streamlit Community Cloud: **https://streamlit.io/cloud**
- Il progetto deve essere in un repository GitHub (pubblico o privato)

### Passo 1 — Crea il repository GitHub

```bash
# Nella cartella del progetto
git init
git add .
git commit -m "first commit"
git remote add origin https://github.com/tuo-username/piano-familiare.git
git push -u origin main
```

> ⚠️ Prima del push: aggiungi un `.gitignore` per escludere `data/input/` (contiene estratti bancari personali) e verifica che `src/database.py` non contenga credenziali che non vuoi rendere pubbliche se il repo è pubblico.

**`.gitignore` consigliato:**
```
data/input/*.xls
data/input/*.xlsx
data/processed/
__pycache__/
*.pyc
.streamlit/secrets.toml
```

### Passo 2 — Gestisci i secrets su Streamlit Cloud

Invece di avere le credenziali hardcoded, usa i **Secrets** di Streamlit Cloud.

**Modifica `src/database.py`** per leggere da `st.secrets`:

```python
import streamlit as st

# Legge da .streamlit/secrets.toml in dev, da Streamlit Cloud in prod
SUPABASE_URL    = st.secrets.get("SUPABASE_URL",    "https://tuo-progetto.supabase.co")
SUPABASE_KEY    = st.secrets.get("SUPABASE_KEY",    "sb_publishable_...")
SUPABASE_SECRET = st.secrets.get("SUPABASE_SECRET", "sb_secret_...")
```

**Crea `.streamlit/secrets.toml`** (solo in locale, NON committare):
```toml
SUPABASE_URL    = "https://tuo-progetto.supabase.co"
SUPABASE_KEY    = "sb_publishable_..."
SUPABASE_SECRET = "sb_secret_..."
```

**Su Streamlit Cloud** (dopo il deploy):
- App settings → **Secrets** → incolla le stesse variabili in formato TOML

### Passo 3 — Deploy su Streamlit Cloud

1. Vai su **https://share.streamlit.io**
2. Clicca **New app**
3. Seleziona il repository GitHub
4. **Main file path:** `app.py`
5. **Branch:** `main`
6. Clicca **Deploy**

L'app viene compilata automaticamente (installa `requirements.txt`) e diventa disponibile su un URL tipo `https://tuo-username-piano-familiare.streamlit.app`.

### Aggiornare la versione in produzione

```bash
# Modifica i file in locale, testa in dev, poi:
git add .
git commit -m "descrizione modifica"
git push origin main
```

Streamlit Cloud rileva il push e rideploya automaticamente in ~1 minuto.

---

## 4. Production — VPS Linux (auto-hosted)

Per chi vuole controllo totale, nessun dipendenza da servizi terzi, o ha un server già disponibile.

### Requisiti
- Server Linux (Ubuntu 22.04+ consigliato) con accesso SSH
- Python 3.10+
- Almeno 512MB RAM, 1GB disco

### Passo 1 — Configura il server

```bash
# Aggiorna i pacchetti
sudo apt update && sudo apt upgrade -y

# Installa Python e pip
sudo apt install python3 python3-pip python3-venv -y

# Crea utente dedicato (buona pratica)
sudo useradd -m -s /bin/bash appuser
sudo su - appuser
```

### Passo 2 — Carica il progetto

```bash
# Opzione A: clona da GitHub
git clone https://github.com/tuo-username/piano-familiare.git
cd piano-familiare

# Opzione B: copia i file via SCP dalla macchina locale
# (dalla tua macchina Windows/Mac)
scp -r C:\Progetti\piano-familiare user@tuo-server:/home/appuser/
```

### Passo 3 — Installa le dipendenze

```bash
cd piano-familiare
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Passo 4 — Configura i secrets

```bash
mkdir -p .streamlit
nano .streamlit/secrets.toml
```

Incolla:
```toml
SUPABASE_URL    = "https://tuo-progetto.supabase.co"
SUPABASE_KEY    = "sb_publishable_..."
SUPABASE_SECRET = "sb_secret_..."
```

### Passo 5 — Configura Streamlit per headless

Crea `.streamlit/config.toml`:
```toml
[server]
headless = true
port = 8501
enableCORS = false

[browser]
gatherUsageStats = false
```

### Passo 6 — Avvia come servizio systemd (avvio automatico)

Crea il file di servizio:
```bash
sudo nano /etc/systemd/system/piano-familiare.service
```

```ini
[Unit]
Description=Net Worth Tracker — Streamlit App
After=network.target

[Service]
Type=simple
User=appuser
WorkingDirectory=/home/appuser/piano-familiare
ExecStart=/home/appuser/piano-familiare/venv/bin/python -m streamlit run app.py
Restart=always
RestartSec=10
Environment="HOME=/home/appuser"

[Install]
WantedBy=multi-user.target
```

Attiva e avvia il servizio:
```bash
sudo systemctl daemon-reload
sudo systemctl enable piano-familiare
sudo systemctl start piano-familiare

# Verifica stato
sudo systemctl status piano-familiare

# Vedi i log
sudo journalctl -u piano-familiare -f
```

### Passo 7 — Esponi con nginx (accesso via browser, porta 80/443)

```bash
sudo apt install nginx -y
sudo nano /etc/nginx/sites-available/piano-familiare
```

```nginx
server {
    listen 80;
    server_name tuo-dominio.com;  # o indirizzo IP del server

    location / {
        proxy_pass http://localhost:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/piano-familiare /etc/nginx/sites-enabled/
sudo nginx -t        # verifica config
sudo systemctl reload nginx
```

### Aggiornare la versione su VPS

```bash
# Via GitHub
ssh user@tuo-server
cd /home/appuser/piano-familiare
git pull origin main
sudo systemctl restart piano-familiare

# Verifica dopo il restart
sudo systemctl status piano-familiare
```

---

## 6. Gestione dei dati in produzione

### File XLS bancari
Gli estratti bancari vanno copiati in `data/input/` **sul server** dove gira l'app — non si caricano dall'interfaccia web.

```bash
# Copia via SCP dalla macchina locale
scp "Lista_Movimenti_Dic2026.xls" user@tuo-server:/home/appuser/piano-familiare/data/input/
```

Poi riavvia l'app (o premi 🔄 Aggiorna tutto nel browser) — l'import è automatico.

### Backup Supabase
Il database Supabase è cloud — Supabase fa backup automatici (7 giorni su piano gratuito).
Per un backup manuale: **Supabase Dashboard → Database → Backups → Download**.

### Aggiornare `config.yaml` in produzione

```bash
ssh user@tuo-server
nano /home/appuser/piano-familiare/config.yaml
sudo systemctl restart piano-familiare
```

---

## 5. Aggiornamento automatico documentazione

Il file `docs/build_html_docs.py` rigenera `MANUALE.html` e `DEPLOY.html` dai Markdown sorgente. Va eseguito ogni volta che si modificano i `.md` prima del deploy.

### Dev locale — manuale

```bash
python docs/build_html_docs.py
```

### Streamlit Community Cloud — GitHub Actions (automatico)

Crea `.github/workflows/build-docs.yml`:

```yaml
name: Build HTML docs
on:
  push:
    branches: [main]
    paths:
      - 'docs/*.md'
      - 'docs/build_html_docs.py'

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install markdown
      - run: python docs/build_html_docs.py
      - uses: stefanzweifel/git-auto-commit-action@v5
        with:
          commit_message: "docs: rigenera HTML [skip ci]"
          file_pattern: "docs/*.html"
```

### Git hook locale (alternativa senza CI)

```bash
# Nella cartella del progetto
echo 'python docs/build_html_docs.py' >> .git/hooks/pre-push
# Su Windows (Git Bash):
chmod +x .git/hooks/pre-push
```

---

## 7. Confronto opzioni deploy

| Criterio | Dev locale | Streamlit Cloud | VPS Linux |
|---|---|---|---|
| Setup | Zero | 15 min | 1-2 ore |
| Costo | Gratuito | Gratuito | ~5€/mese |
| Accessibile da mobile | No | Sì | Sì |
| Auto-deploy da git | — | Sì | No (manuale) |
| Controllo totale | Sì | No | Sì |
| Uptime 24/7 | No | Sì | Sì |
| Import XLS automatico | Sì | Solo via git | Via SCP |

**Raccomandazione:** per uso personale, **Streamlit Community Cloud** è la scelta ottimale — gratuita, zero manutenzione, accessibile da smartphone.
