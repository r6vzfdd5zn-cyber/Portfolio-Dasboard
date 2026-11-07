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
    return tk, df, cur


def fmt_date(d):
    return d.strftime("%-d %b %Y")


def get_dividends(tk, last_price):
    """Work out dividend facts from the stock's payment history.

    Returns None if the stock has never paid a dividend. Dates in the
    history are ex-dividend dates (you must own the stock before this day
    to receive the payment).
    """
    divs = retry(lambda: tk.dividends)
    if divs is None or divs.empty:
        return None
    divs = divs[divs > 0]
    if divs.empty:
        return None
    dates = [ts.date() for ts in divs.index]
    amounts = list(divs.values)
    today = datetime.date.today()
    year_ago = today - datetime.timedelta(days=365)

    recent = [a for d, a in zip(dates, amounts) if d > year_ago]
    per_year = sum(recent)
    count = len(recent)
    freq = {1: "once a year", 2: "twice a year", 4: "quarterly", 12: "monthly"}.get(
        count, f"{count}× a year" if count else "")

    info = {
        "per_year": per_year,
        "yield": per_year / last_price * 100 if last_price and per_year else 0,
        "freq": freq,
        "last_date": dates[-1],
        "last_amt": amounts[-1],
        "next_ex": None, "next_ex_announced": False, "next_pay": None,
    }

    # Dates the company has already announced (Yahoo's calendar).
    try:
        cal = tk.calendar or {}
    except Exception:
        cal = {}
    ex = cal.get("Ex-Dividend Date")
    pay = cal.get("Dividend Date")
    if isinstance(ex, datetime.date) and ex >= today:
        info["next_ex"], info["next_ex_announced"] = ex, True
    if isinstance(pay, datetime.date) and pay >= today and (
            not isinstance(ex, datetime.date) or pay >= ex):
        info["next_pay"] = pay

    # Otherwise estimate the next ex-date from the usual gap between payments.
    if info["next_ex"] is None and len(dates) >= 3 and count:
        gaps = sorted((b - a).days for a, b in zip(dates[-5:], dates[-4:]))
        gap = gaps[len(gaps) // 2]
        guess = dates[-1] + datetime.timedelta(days=gap)
        if guess >= today:
            info["next_ex"] = guess
    return info


def get_news(name, n=NEWS_PER_STOCK):
    q = urllib.parse.quote(f'"{name}"')
    url = f"https://news.google.com/rss/search?q={q}&hl=en-GB&gl=GB&ceid=GB:en"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (portfolio-dashboard)"})

    def fetch():
        with urllib.request.urlopen(req, timeout=20) as r:
            return feedparser.parse(r.read()).entries[:n]

    return retry(fetch)


def price_block(ticker, include_js):
    """Return (html, summary). summary is None when prices failed."""
    try:
        tk, df, cur = get_prices(ticker)
    except Exception as e:
        print(f"  {ticker}: price fetch failed: {e}", file=sys.stderr)
        return '<p class="muted">No price data available today.</p>', None

    closes = df["Close"].dropna()
    last = closes.iloc[-1]
    if cur == "GBp":
        cur = "GBp (pence)"
    pct = (last / closes.iloc[-2] - 1) * 100 if len(closes) >= 2 else None
    if pct is not None:
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
    return head + chart, {"price": last, "cur": cur, "pct": pct, "tk": tk}


def dividend_block(div, cur):
    if div is None:
        return '<p class="muted">Does not pay a dividend.</p>'
    cur = html.escape(cur.replace(" (pence)", ""))
    lines = []
    if div["per_year"]:
        lines.append(f'<b>{div["per_year"]:,.2f} {cur}</b> per share over the last 12 months'
                     f' · <b>{div["yield"]:.2f}%</b> yield · paid {div["freq"]}')
    else:
        lines.append('<span class="muted">No dividend paid in the last 12 months.</span>')
    lines.append(f'Last: {div["last_amt"]:,.2f} {cur} (ex-date {fmt_date(div["last_date"])})')
    if div["next_ex"]:
        if div["next_ex_announced"]:
            lines.append(f'Next ex-date: <b>{fmt_date(div["next_ex"])}</b>')
        else:
            lines.append(f'Next ex-date: around <b>{div["next_ex"].strftime("%b %Y")}</b>'
                         ' <span class="muted">(estimate)</span>')
    if div["next_pay"]:
        lines.append(f'Next payment: <b>{fmt_date(div["next_pay"])}</b>')
    return '<ul class="div">' + "".join(f"<li>{l}</li>" for l in lines) + "</ul>"


def calendar_block(events):
    if not events:
        return '<p class="muted">No dividend dates in the next 4 months.</p>'
    rows = []
    for d, name, anchor, kind, est in sorted(events, key=lambda e: e[0]):
        when = d.strftime("%b %Y") if est else d.strftime("%-d %b")
        if est:
            when = f'~{when}'
        rows.append(f'<tr><td class="nowrap">{when}</td>'
                    f'<td><a href="#{anchor}">{html.escape(name)}</a></td>'
                    f'<td class="num">{kind}</td></tr>')
    return ('<table><thead><tr><th>Date</th><th>Name</th><th class="num">Event</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>'
            '<p class="note muted">Ex-date: you must own the share <i>before</i> this day to get '
            'the next dividend. Payment: the day the money arrives. '
            '~ means estimated from past payments.</p>')


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
    cards, rows, events, js_done, ok_count = [], [], [], False, 0
    horizon = datetime.date.today() + datetime.timedelta(days=120)
    for t, name in tickers:
        print(f"{t} ({name})")
        prices, info = price_block(t, include_js=not js_done)
        anchor = "s-" + "".join(c if c.isalnum() else "-" for c in t.lower())
        if info:
            js_done = True
            ok_count += 1
            cur = info["cur"].replace(" (pence)", "")
            price = f'{info["price"]:,.2f} <span class="muted">{html.escape(cur)}</span>'
            if info["pct"] is None:
                pct = '<span class="muted">–</span>'
            else:
                cls = "up" if info["pct"] >= 0 else "down"
                pct = f'<span class="{cls}">{info["pct"]:+.2f}%</span>'
        else:
            price, pct = '<span class="muted">no data</span>', ""

        divs_html = ""
        if info:
            try:
                div = get_dividends(info["tk"], info["price"])
            except Exception as e:
                print(f"  {t}: dividend fetch failed: {e}", file=sys.stderr)
                div, divs_html = None, '<p class="muted">Dividend data unavailable today.</p>'
            if not divs_html:
                divs_html = dividend_block(div, info["cur"])
            if div:
                if div["next_ex"] and div["next_ex"] <= horizon:
                    events.append((div["next_ex"], name, anchor, "Ex-date",
                                   not div["next_ex_announced"]))
                if div["next_pay"] and div["next_pay"] <= horizon:
                    events.append((div["next_pay"], name, anchor, "Payment", False))
        rows.append(f'<tr><td><a href="#{anchor}">{html.escape(name)}</a>'
                    f'<br><small class="muted">{html.escape(t)}</small></td>'
                    f'<td class="num">{price}</td><td class="num">{pct}</td></tr>')
        cards.append(f'<section class="card" id="{anchor}"><h2>{html.escape(name)} '
                     f'<small>{html.escape(t)}</small></h2>{prices}'
                     f'<h3>Dividends</h3>{divs_html}'
                     f'<h3>Latest news</h3>{news_block(name)}'
                     f'<p class="top"><a href="#">↑ Back to top</a></p></section>')

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
  .summary {{ padding: 8px 12px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .95em; }}
  th {{ text-align: left; color: var(--muted); font-weight: 600; font-size: .85em; padding: 6px 4px; }}
  td {{ padding: 8px 4px; border-top: 1px solid rgba(128,128,128,.2); vertical-align: middle; }}
  .num {{ text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }}
  th.num {{ text-align: right; }}
  html {{ scroll-behavior: smooth; }} section.card {{ scroll-margin-top: 12px; }}
  .top {{ text-align: right; margin: 10px 0 0; font-size: .9em; }}
  .nowrap {{ white-space: nowrap; }}
  .note {{ font-size: .8em; margin: 8px 4px 2px; }}
  h2.section {{ font-size: 1.1em; margin: 0 4px 6px; }}
  ul.div {{ list-style: none; padding: 0; }}
  ul {{ padding-left: 20px; margin: 0; }} li {{ margin: 6px 0; }}
  a {{ color: var(--link); text-decoration: none; }} a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<h1>Portfolio Dashboard</h1>
<p class="muted">Updated {stamp}</p>
<section class="card summary">
<table>
<thead><tr><th>Name</th><th class="num">Price</th><th class="num">Change</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>
</section>
<section class="card summary">
<h2 class="section">Dividend calendar</h2>
{calendar_block(events)}
</section>
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
