@echo off
REM Installa il pre-push hook git che rigenera gli HTML docs prima di ogni push.
REM Eseguire una volta dopo git init o git clone.

if not exist ".git" (
    echo [ERRORE] Eseguire dalla radice del repository git.
    exit /b 1
)

echo python docs/build_html_docs.py > .git\hooks\pre-push
echo [OK] Hook pre-push installato.
echo      Ogni git push rigenera automaticamente MANUALE.html e DEPLOY.html.
