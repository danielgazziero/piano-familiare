@echo off
chcp 65001 >nul
echo.
echo  Piano Finanziario Familiare
echo  ============================
echo.

:: Vai nella cartella dello script (funziona da qualsiasi posizione)
cd /d "%~dp0"

:: Controlla se Python è installato
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERRORE: Python non trovato.
    echo.
    echo  Installa Python da: https://www.python.org/downloads/
    echo  Durante l'installazione spunta "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

:: Esegui il check e setup
echo  Avvio controllo ambiente...
echo.
python check_and_setup.py

pause
