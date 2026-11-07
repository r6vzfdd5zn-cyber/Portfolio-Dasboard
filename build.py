import html, urllib.parse, datetime, os
import yfinance as yf, feedparser
import plotly.graph_objects as go

def load_tickers():
    out = []
    for line in open("tickers.txt", encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        t, _, name = line.partition(" ")
        out.append((t, name.strip() or t))
    return out

def get_news(name, n=6):
    q = urllib.parse.quote(f'"{name}"')
    url = f"https://news.google.com/rss/search?q={q}&hl=en-GB&gl=GB&ceid=GB:en"
    return feedparser.parse(url).entries[:n]

cards, first = [], True
for t, name in load_tickers():
    tk = yf.Ticker(t)
    df = tk.history(period="6mo")
    if df.empty:
        chart, change = "<p>No price data.</p>", ""
    else:
        try:
            cur = tk.fast_info["currency"]
        except Exception:
            cur = ""
        if cur == "GBp":
            cur = "GBp (pence)"
        last, prev = df["Close"].iloc[-1], df["Close"].iloc[-2]
        pct = (last / prev - 1) * 100
        colour = "green" if pct >= 0 else "red"
        change = f'<p style="color:{colour}">{last:,.2f} {cur} ({pct:+.2f}% today)</p>'
        fig = go.Figure(go.Scatter(x=df.index, y=df["Close"], mode="lines"))
        fig.update_layout(height=280, margin=dict(l=40, r=10, t=10, b=30))
        chart = fig.to_html(full_html=False, include_plotlyjs="cdn" if first else False)
        first = False
    news = "".join(
        f'<li><a href="{html.escape(e.link)}" target="_blank">{html.escape(e.title)}</a></li>'
        for e in get_news(name)
    )
    cards.append(f'<div class="card"><h2>{html.escape(name)} <small>{t}</small></h2>'
                 f'{change}{chart}<ul>{news}</ul></div>')

stamp = datetime.datetime.utcnow().strftime("%d %b %Y %H:%M UTC")
page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Portfolio Dashboard</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 0 auto; max-width: 900px; padding: 16px; background: #f6f7f9; color: #222; }}
  .card {{ background: #fff; border-radius: 10px; padding: 16px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }}
  h2 small {{ color: #888; font-weight: normal; }}
  ul {{ padding-left: 20px; }}
  li {{ margin: 4px 0; }}
  a {{ color: #0b5cad; text-decoration: none; }}
</style>
</head>
<body>
<h1>Portfolio Dashboard</h1>
<p>Updated {stamp}</p>
{"".join(cards)}
</body>
</html>"""

os.makedirs("site", exist_ok=True)
open("site/index.html", "w", encoding="utf-8").write(page)
