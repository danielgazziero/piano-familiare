#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║     Piano Finanziario Familiare — Check & Setup              ║
║     Funziona su Windows e Mac                                ║
╚══════════════════════════════════════════════════════════════╝

Eseguire con:
  Windows:  python check_and_setup.py
  Mac:      python3 check_and_setup.py
"""

import sys
import os
import platform
import subprocess
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).parent

# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def titolo(t):
    print(f"\n{'═'*60}")
    print(f"  {t}")
    print(f"{'═'*60}")

def sezione(t):
    print(f"\n── {t} {'─'*(55-len(t))}")

def ok(t):    print(f"  ✓  {t}")
def warn(t):  print(f"  ⚠  {t}")
def errore(t):print(f"  ✗  {t}")
def info(t):  print(f"     {t}")

SISTEMA = platform.system()   # 'Windows' | 'Darwin' | 'Linux'
IS_WIN  = SISTEMA == 'Windows'
IS_MAC  = SISTEMA == 'Darwin'
errori_critici = []
avvisi = []

# ─────────────────────────────────────────────────────────────
# 1. PYTHON VERSION
# ─────────────────────────────────────────────────────────────

def check_python():
    sezione("Python")
    v = sys.version_info
    versione = f"{v.major}.{v.minor}.{v.micro}"
    if v.major < 3 or (v.major == 3 and v.minor < 9):
        errore(f"Python {versione} — richiesto Python 3.9 o superiore")
        errori_critici.append("Python versione insufficiente")
        if IS_WIN:
            info("→ Scarica da: https://www.python.org/downloads/")
            info("  Durante l'installazione spunta 'Add Python to PATH'")
        else:
            info("→ Installa con: brew install python3")
    else:
        ok(f"Python {versione} ({SISTEMA})")

# ─────────────────────────────────────────────────────────────
# 2. PIP
# ─────────────────────────────────────────────────────────────

def check_pip():
    sezione("pip")
    try:
        r = subprocess.run([sys.executable, '-m', 'pip', '--version'],
                           capture_output=True, text=True)
        if r.returncode == 0:
            ok(r.stdout.strip().split('\n')[0])
        else:
            raise Exception()
    except Exception:
        errore("pip non trovato")
        errori_critici.append("pip mancante")
        info("→ pip è incluso con Python 3.4+ — reinstalla Python")

# ─────────────────────────────────────────────────────────────
# 3. LIBRERIE PYTHON
# ─────────────────────────────────────────────────────────────

LIBRERIE = [
    ('streamlit',     '1.35.0',  True,  'Interfaccia web dell\'app'),
    ('pandas',        '2.0.0',   True,  'Analisi dati'),
    ('plotly',        '5.18.0',  True,  'Grafici interattivi'),
    ('yfinance',      '0.2.40',  True,  'Prezzi ETF e azioni in tempo reale'),
    ('openpyxl',      '3.1.0',   True,  'Lettura file XLS banca'),
    ('yaml',          '6.0.0',   True,  'Lettura config.yaml (PyYAML)'),
    ('supabase',      '2.4.0',   True,  'Database cloud persistente'),
    ('dateutil',      '2.8.0',   True,  'Calcoli date (python-dateutil)'),
    ('numpy',         '1.26.0',  True,  'Calcoli numerici'),
]

# Nome pacchetto pip (può differire dal nome import)
INSTALL_NAME = {
    'yaml':    'PyYAML',
    'dateutil':'python-dateutil',
}

def check_librerie():
    sezione("Librerie Python")
    da_installare = []

    for nome_import, versione_min, critica, descrizione in LIBRERIE:
        try:
            mod = __import__(nome_import)
            ver = getattr(mod, '__version__', '?')
            ok(f"{nome_import:<15} {ver:<12} {descrizione}")
        except ImportError:
            if critica:
                errore(f"{nome_import:<15} NON INSTALLATO  — {descrizione}")
                da_installare.append(INSTALL_NAME.get(nome_import, nome_import))
            else:
                warn(f"{nome_import:<15} non trovato (opzionale)")

    return da_installare

# ─────────────────────────────────────────────────────────────
# 4. LIBREOFFICE (necessario per leggere XLS BPER Daniel)
# ─────────────────────────────────────────────────────────────

def check_libreoffice():
    # LibreOffice non più necessario — usiamo xlrd 1.2.0 per leggere BIFF8
    sezione("LibreOffice")
    ok("Non richiesto — gli XLS BPER vengono letti con xlrd (già incluso nelle librerie Python)")
    return True

# ─────────────────────────────────────────────────────────────
# 5. STRUTTURA CARTELLE
# ─────────────────────────────────────────────────────────────

def check_struttura():
    sezione("Struttura cartelle")
    cartelle = [
        BASE_DIR / 'data' / 'input',
        BASE_DIR / 'data' / 'reference',
        BASE_DIR / 'data' / 'processed',
        BASE_DIR / 'src',
        BASE_DIR / 'docs',
    ]
    for c in cartelle:
        c.mkdir(parents=True, exist_ok=True)
        ok(f"✓ {c.relative_to(BASE_DIR)}")

    # Verifica file sorgente
    file_richiesti = [
        'app.py', 'config.yaml', 'requirements.txt',
        'src/adapters.py', 'src/parser.py', 'src/portfolio.py',
        'src/simulator.py', 'src/positions.py', 'src/prices.py',
        'src/database.py', 'src/app_state.py',
    ]
    tutti_ok = True
    for f in file_richiesti:
        p = BASE_DIR / f
        if p.exists():
            ok(f"{f}")
        else:
            errore(f"{f} — FILE MANCANTE")
            errori_critici.append(f"File mancante: {f}")
            tutti_ok = False
    return tutti_ok

# ─────────────────────────────────────────────────────────────
# 6. CONNESSIONE SUPABASE
# ─────────────────────────────────────────────────────────────

def check_supabase():
    sezione("Connessione Supabase")
    try:
        sys.path.insert(0, str(BASE_DIR / 'src'))
        from database import test_connessione
        if test_connessione():
            ok("Supabase raggiungibile — credenziali valide")
            return True
        else:
            warn("Supabase non raggiungibile")
            avvisi.append("Supabase non connesso")
            info("→ Verifica connessione internet")
            info("→ Esegui 'python setup_supabase.py' se è il primo avvio")
            return False
    except ImportError:
        warn("Impossibile testare Supabase (librerie mancanti)")
        return False
    except Exception as e:
        warn(f"Errore connessione Supabase: {e}")
        return False

# ─────────────────────────────────────────────────────────────
# 7. INSTALLA LIBRERIE MANCANTI
# ─────────────────────────────────────────────────────────────

def installa_librerie(da_installare):
    if not da_installare:
        return True

    sezione("Installazione librerie mancanti")
    print(f"\n  Da installare: {', '.join(da_installare)}")
    risposta = input("\n  Vuoi installarle adesso? [s/N] ").strip().lower()
    if risposta != 's':
        info("Installazione saltata. Esegui manualmente:")
        info(f"  pip install {' '.join(da_installare)}")
        return False

    print()
    cmd = [sys.executable, '-m', 'pip', 'install'] + da_installare
    r = subprocess.run(cmd)
    if r.returncode == 0:
        ok("Installazione completata")
        return True
    else:
        errore("Errore durante l'installazione")
        info("Prova manualmente:")
        info(f"  pip install {' '.join(da_installare)}")
        return False

# ─────────────────────────────────────────────────────────────
# 8. SETUP SUPABASE (primo avvio)
# ─────────────────────────────────────────────────────────────

def proponi_setup_supabase():
    sezione("Setup database (primo avvio)")
    try:
        sys.path.insert(0, str(BASE_DIR / 'src'))
        from database import get_client
        client = get_client()
        res = client.table('posizioni').select('id').limit(1).execute()
        ok("Tabelle Supabase già inizializzate")
        return True
    except Exception:
        warn("Tabelle Supabase non trovate — primo avvio")
        info("È necessario creare le tabelle su Supabase.")
        risposta = input("\n  Vuoi procedere con il setup adesso? [s/N] ").strip().lower()
        if risposta == 's':
            os.system(f"{sys.executable} {BASE_DIR / 'setup_supabase.py'}")
        else:
            info("→ Esegui manualmente: python setup_supabase.py")
        return False

# ─────────────────────────────────────────────────────────────
# 9. AVVIO APP
# ─────────────────────────────────────────────────────────────

def avvia_app():
    sezione("Avvio app")
    risposta = input("\n  Vuoi avviare l'app adesso? [s/N] ").strip().lower()
    if risposta == 's':
        print("\n  Avvio in corso... (apri il browser su http://localhost:8501)")
        print("  Per fermare l'app: premi Ctrl+C\n")
        os.system(f"{sys.executable} -m streamlit run {BASE_DIR / 'app.py'}")

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    titolo("Piano Finanziario Familiare — Check & Setup")
    print(f"\n  Sistema operativo: {SISTEMA} {platform.release()}")
    print(f"  Python:            {sys.version.split()[0]}")
    print(f"  Cartella progetto: {BASE_DIR}")

    # Step 1: Python
    check_python()
    if errori_critici:
        print("\n\n  ✗ Errori critici trovati — risolvi prima di continuare.")
        return

    # Step 2: pip
    check_pip()

    # Step 3: librerie
    da_installare = check_librerie()

    # Step 4: LibreOffice
    check_libreoffice()

    # Step 5: struttura
    check_struttura()

    # Step 6 & 7: installa se necessario
    if da_installare:
        librerie_ok = installa_librerie(da_installare)
        if not librerie_ok:
            errori_critici.append("Librerie Python mancanti")

    # Step 8: Supabase
    if not errori_critici:
        supabase_ok = check_supabase()
        if supabase_ok:
            proponi_setup_supabase()

    # Riepilogo finale
    titolo("Riepilogo")
    if not errori_critici and not avvisi:
        ok("Tutto in ordine — ambiente pronto!")
    else:
        if errori_critici:
            print("\n  Errori critici (da risolvere):")
            for e in errori_critici:
                errore(e)
        if avvisi:
            print("\n  Avvisi (opzionali):")
            for a in avvisi:
                warn(a)

    # Avvio
    if not errori_critici:
        avvia_app()
    else:
        print("\n  Risolvi gli errori sopra, poi riesegui questo script.")

    print()

if __name__ == '__main__':
    main()
