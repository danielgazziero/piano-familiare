#!/bin/bash
# Piano Finanziario Familiare — Avvio Mac

# Vai nella cartella dello script
cd "$(dirname "$0")"

echo ""
echo "  Piano Finanziario Familiare"
echo "  ============================"
echo ""

# Controlla Python 3
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    echo "  ERRORE: Python non trovato."
    echo ""
    echo "  Installa Python con:"
    echo "    brew install python3"
    echo ""
    read -p "  Premi INVIO per chiudere..."
    exit 1
fi

echo "  Avvio controllo ambiente..."
echo ""
$PYTHON check_and_setup.py
