"""
Rigenera MANUALE.html e DEPLOY.html dai file Markdown sorgente.
Eseguire ogni volta che si aggiorna MANUALE.md o DEPLOY.md:

    python docs/build_html_docs.py

In CI (GitHub Actions) aggiungere questo step dopo ogni push su main.
"""
import markdown
import re
from pathlib import Path

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  :root {{ --primary: #1F5C8B; --accent: #1baf7a; --bg: #f7f9fb; --text: #1a1a2e; --border: #d0d7de; --code-bg: #f0f3f5; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: "Segoe UI", Arial, sans-serif; font-size: 14px; line-height: 1.7; color: var(--text); background: var(--bg); display: flex; }}
  nav {{ width: 230px; min-width: 230px; background: #fff; border-right: 1px solid var(--border); padding: 28px 16px; position: sticky; top: 0; height: 100vh; overflow-y: auto; }}
  nav .logo {{ font-size: 13px; font-weight: 700; color: var(--primary); margin-bottom: 20px; display: block; }}
  nav ul {{ list-style: none; }}
  nav ul li a {{ display: block; color: var(--text); text-decoration: none; padding: 4px 8px; border-radius: 4px; font-size: 12.5px; }}
  nav ul li a:hover {{ background: var(--code-bg); color: var(--primary); }}
  main {{ flex: 1; max-width: 860px; padding: 48px 56px; }}
  h1 {{ font-size: 26px; color: var(--primary); border-bottom: 3px solid var(--accent); padding-bottom: 10px; margin-bottom: 8px; }}
  h2 {{ font-size: 20px; color: var(--primary); border-left: 4px solid var(--accent); padding-left: 12px; margin: 40px 0 14px; }}
  h3 {{ font-size: 16px; color: var(--primary); margin: 26px 0 10px; }}
  h4 {{ font-size: 14px; margin: 18px 0 8px; color: #444; }}
  p {{ margin-bottom: 12px; }}
  a {{ color: var(--primary); }}
  code {{ background: var(--code-bg); border: 1px solid var(--border); border-radius: 3px; padding: 1px 5px; font-family: "Consolas", "Courier New", monospace; font-size: 12.5px; }}
  pre {{ background: #1e1e2e; color: #cdd6f4; border-radius: 6px; padding: 16px 18px; overflow-x: auto; margin: 12px 0 18px; font-size: 12.5px; line-height: 1.5; }}
  pre code {{ background: none; border: none; padding: 0; color: inherit; font-size: inherit; }}
  table {{ border-collapse: collapse; width: 100%; margin: 14px 0 20px; font-size: 13px; }}
  th {{ background: var(--primary); color: #fff; padding: 8px 12px; text-align: left; font-weight: 600; }}
  td {{ padding: 7px 12px; border-bottom: 1px solid var(--border); }}
  tr:nth-child(even) td {{ background: #f2f5f8; }}
  blockquote {{ border-left: 4px solid var(--accent); background: #eef9f5; padding: 10px 16px; margin: 14px 0; border-radius: 0 6px 6px 0; font-size: 13px; }}
  blockquote p {{ margin: 0; }}
  ul, ol {{ padding-left: 24px; margin-bottom: 12px; }}
  li {{ margin-bottom: 4px; }}
  hr {{ border: none; border-top: 1px solid var(--border); margin: 32px 0; }}
  @media print {{ nav {{ display: none; }} body {{ display: block; }} main {{ max-width: 100%; padding: 20px; }} pre {{ background: #f5f5f5; color: #000; border: 1px solid #ccc; }} }}
  @media (max-width: 700px) {{ nav {{ display: none; }} main {{ padding: 24px 18px; }} }}
</style>
</head>
<body>
<nav>
  <span class="logo">Net Worth Tracker</span>
  {toc}
</nav>
<main>
{body}
</main>
</body>
</html>"""


def _make_slug(text: str) -> str:
    text = re.sub(r'[^\w\s-]', '', text.lower())
    return re.sub(r'\s+', '-', text.strip())


def _add_ids(html: str) -> str:
    def _repl(m):
        level, content = m.group(1), m.group(2)
        text = re.sub(r'<[^>]+>', '', content)
        return f'<h{level} id="{_make_slug(text)}">{content}</h{level}>'
    return re.sub(r'<h([23])>(.*?)</h\1>', _repl, html, flags=re.DOTALL)


def _build_toc(html: str) -> str:
    items = []
    for m in re.finditer(r'<h([23]) id="([^"]+)">(.*?)</h\1>', html, re.DOTALL):
        level, slug, content = m.group(1), m.group(2), m.group(3)
        text = re.sub(r'<[^>]+>', '', content)
        indent = '  ' if level == '3' else ''
        items.append(f'{indent}<li><a href="#{slug}">{text}</a></li>')
    return '<ul>' + '\n'.join(items) + '</ul>' if items else ''


def convert(md_path: Path, html_path: Path, title: str) -> None:
    md_text = md_path.read_text(encoding='utf-8')
    body = markdown.markdown(md_text, extensions=['fenced_code', 'tables', 'nl2br'])
    body = _add_ids(body)
    toc = _build_toc(body)
    html = HTML_TEMPLATE.format(title=title, toc=toc, body=body)
    html_path.write_text(html, encoding='utf-8')
    print(f"OK  {html_path.name}")


if __name__ == '__main__':
    base = Path(__file__).parent
    convert(base / 'MANUALE.md', base / 'MANUALE.html', 'Net Worth Tracker - Manuale utente')
    convert(base / 'DEPLOY.md',  base / 'DEPLOY.html',  'Net Worth Tracker - Guida al Deploy')
    print("Docs aggiornati.")
