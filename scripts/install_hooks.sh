#!/usr/bin/env bash
# Installa il pre-push hook git che rigenera gli HTML docs prima di ogni push.
# Eseguire una volta dopo git init o git clone:
#   bash scripts/install_hooks.sh

set -e

if [ ! -d ".git" ]; then
  echo "[ERRORE] Eseguire dalla radice del repository git."
  exit 1
fi

cat > .git/hooks/pre-push << 'EOF'
#!/usr/bin/env bash
python docs/build_html_docs.py
EOF

chmod +x .git/hooks/pre-push
echo "[OK] Hook pre-push installato."
echo "     Ogni git push rigenera automaticamente MANUALE.html e DEPLOY.html."
