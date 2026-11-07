"""Build a static portfolio dashboard (site/index.html).

For each ticker in tickers.txt it fetches 6 months of prices from Yahoo
Finance and the latest headlines from Google News. Meant to run once a day
from GitHub Actions; one failing ticker or feed never stops the build.
"""
import datetime
import html
import os
import sys
import time
import urllib.parse
import urllib.request

import feedparser
import plotly.graph_objects as go
import yfinance as yf

TICKERS_FILE = "tickers.txt"
OUT_DIR = "site"
NEWS_PER_STOCK = 6


def load_tickers():
    out = []
    with open(TICKERS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            t, _, name = line.partition(" ")
            out.append((t, name.strip() or t))
    return out


def retry(fn, tries=3, wait=5):
    """Call fn(), retrying on errors (Yahoo sometimes rate-limits CI servers)."""
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if i == tries - 1:
                raise
            print(f"  retry after error: {e}", file=sys.stderr)
            time.sleep(wait * (i + 1))


def get_prices(ticker):
    """Return (dataframe, currency). The dataframe may be empty."""
    tk = yf.Ticker(ticker)

    def fetch():
        df = tk.history(period="6mo")
        if df.empty:
            raise ValueError("no price data returned")
        return df

    df = retry(fetch)
    cur = (tk.history_metadata or {}).get("currency", "")
    return df, cur


def get_news(name, n=NEWS_PER_STOCK):
    q = urllib.parse.quote(f'"{name}"')
    url = f"https://news.google.com/rss/search?q={q}&hl=en-GB&gl=GB&ceid=GB:en"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (portfolio-dashboard)"})

    def fetch():
        with urllib.request.urlopen(req, timeout=20) as r:
            return feedparser.parse(r.read()).entries[:n]

    return retry(fetch)


def price_block(ticker, include_js):
    """Return (html, ok)."""
    try:
        df, cur = get_prices(ticker)
    except Exception as e:
        print(f"  {ticker}: price fetch failed: {e}", file=sys.stderr)
        return '<p class="muted">No price data available today.</p>', False

    closes = df["Close"].dropna()
    last = closes.iloc[-1]
    if cur == "GBp":
        cur = "GBp (pence)"
    if len(closes) >= 2:
        pct = (last / closes.iloc[-2] - 1) * 100
        cls = "up" if pct >= 0 else "down"
        change = f'<span class="{cls}">{pct:+.2f}%</span> on last close'
    else:
        change = ""
    as_of = closes.index[-1].strftime("%d %b %Y")
    head = (f'<p class="price"><b>{last:,.2f}</b> {html.escape(cur)} {change}'
            f' <span class="muted">· {as_of}</span></p>')

    fig = go.Figure(go.Scatter(x=closes.index, y=closes, mode="lines",
                               line=dict(width=2, color="#2a6fdb")))
    fig.update_layout(height=260, margin=dict(l=40, r=10, t=10, b=30),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      xaxis=dict(gridcolor="rgba(128,128,128,.2)"),
                      yaxis=dict(gridcolor="rgba(128,128,128,.2)"))
    chart = fig.to_html(full_html=False, include_plotlyjs="cdn" if include_js else False,
                        config={"displayModeBar": False, "responsive": True})
    return head + chart, True


def news_block(name):
    try:
        entries = get_news(name)
    except Exception as e:
        print(f"  {name}: news fetch failed: {e}", file=sys.stderr)
        entries = []
    if not entries:
        return '<p class="muted">No recent news found.</p>'
    items = "".join(
        f'<li><a href="{html.escape(e.get("link", "#"))}" target="_blank" rel="noopener">'
        f'{html.escape(e.get("title", ""))}</a></li>'
        for e in entries
    )
    return f"<ul>{items}</ul>"


def main():
    tickers = load_tickers()
    cards, js_done, ok_count = [], False, 0
    for t, name in tickers:
        print(f"{t} ({name})")
        prices, ok = price_block(t, include_js=not js_done)
        if ok:
            js_done = True
            ok_count += 1
        cards.append(f'<section class="card"><h2>{html.escape(name)} '
                     f'<small>{html.escape(t)}</small></h2>{prices}'
                     f'<h3>Latest news</h3>{news_block(name)}</section>')

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%d %b %Y %H:%M UTC")
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Portfolio Dashboard</title>
<style>
  :root {{ --bg:#f5f6f8; --card:#fff; --text:#1d1f23; --muted:#6b7280; --link:#1a5fb4;
          --up:#15803d; --down:#c62828; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#111317; --card:#1b1e24; --text:#e8eaed; --muted:#9aa0a6; --link:#8ab4f8;
            --up:#4ade80; --down:#f87171; }}
  }}
  body {{ font-family: system-ui, -apple-system, sans-serif; margin: 0 auto; max-width: 900px;
         padding: 16px; background: var(--bg); color: var(--text); }}
  .card {{ background: var(--card); border-radius: 12px; padding: 16px; margin-bottom: 16px;
          box-shadow: 0 1px 3px rgba(0,0,0,.12); }}
  h2 {{ margin: 0 0 4px; }} h2 small {{ color: var(--muted); font-weight: normal; font-size: .6em; }}
  h3 {{ font-size: 1em; margin: 12px 0 4px; }}
  .price {{ margin: 4px 0; }} .up {{ color: var(--up); }} .down {{ color: var(--down); }}
  .muted {{ color: var(--muted); }}
  ul {{ padding-left: 20px; margin: 0; }} li {{ margin: 6px 0; }}
  a {{ color: var(--link); text-decoration: none; }} a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<h1>Portfolio Dashboard</h1>
<p class="muted">Updated {stamp}</p>
{"".join(cards)}
</body>
</html>"""

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Wrote {OUT_DIR}/index.html ({ok_count}/{len(tickers)} with prices)")

    # Fail the run only if every price lookup failed, so a broken day is visible.
    if tickers and ok_count == 0:
        sys.exit("No price data for any ticker")


if __name__ == "__main__":
    main()
