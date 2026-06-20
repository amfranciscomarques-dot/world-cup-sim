"""Render the dashboard payload (see :mod:`worldcup.report`) as a single, fully
self-contained HTML page — an at-a-glance alternative to the terminal TUI.

No build step, no dependencies: one ``render_dashboard(report) -> str`` call
returns a complete document (embedded CSS + a sprinkle of vanilla JS, fonts from
Google Fonts) you can write to disk and open in any browser.

Aesthetic: a floodlit-stadium scoreboard. Deep green-black, electric lime for
*our model*, sky-blue for *the market*, gold for the champion. Archivo for the
broadcast-style headlines, Spline Sans for body, mono for the numbers.
"""

from __future__ import annotations

import html as _html
from typing import Optional

# --- small formatting helpers ----------------------------------------------

def _e(s) -> str:
    return _html.escape(str(s))


def _p1(x: Optional[float]) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _p0(x: Optional[float]) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def _sign(x: Optional[float]) -> str:
    return "—" if x is None else f"{x * 100:+.1f}%"


def _bar(frac: float, cls: str, scale: float = 1.0) -> str:
    w = max(0.0, min(100.0, (frac / scale) * 100.0)) if scale else 0.0
    return f'<span class="bar {cls}"><i style="--w:{w:.2f}%"></i></span>'


_HDA_CLASS = ("h", "d", "a")


def _segbar(triple) -> str:
    """A three-segment Home/Draw/Away probability bar."""
    if not triple:
        return '<div class="seg empty"></div>'
    segs = "".join(
        f'<i class="seg-{_HDA_CLASS[i]}" style="--w:{triple[i] * 100:.2f}%"></i>'
        for i in range(3)
    )
    return f'<div class="seg">{segs}</div>'


def _zone_dot(zone: str) -> str:
    return f'<span class="dot dot-{zone}"></span>'


# --- sections --------------------------------------------------------------

def _hero(rep: dict) -> str:
    m = rep["meta"]
    pick = rep["champion_pick"]
    perf = rep["performance"]
    favourite = ""
    if pick:
        mk = _p1(pick["market"]) if pick.get("market") is not None else "—"
        edge = pick.get("edge")
        edge_chip = ""
        if edge is not None:
            cls = "pos" if edge >= 0 else "neg"
            edge_chip = f'<span class="chip {cls}">{_sign(edge)} vs market</span>'
        favourite = f"""
      <div class="favourite">
        <div class="fav-label">Model title favourite</div>
        <div class="fav-team">{_e(pick['team'])}</div>
        <div class="fav-odds"><b>{_p1(pick['champion'])}</b><span>model</span>
          <em>{mk}</em><span>market</span></div>
        {edge_chip}
      </div>"""

    sharper = "model" if perf["model_sharper"] else "market"
    acc = f"{perf['model_correct']}/{perf['n']}" if perf["n"] else "—"
    stats = [
        ("Games played", f"{m['games_played']}<small>/{m['games_total']}</small>"),
        ("Model calls right", acc),
        ("Sharper so far", sharper.upper()),
        ("Title sims", f"{m['title_iters']:,}"),
    ]
    stat_html = "".join(
        f'<div class="stat"><span class="k">{_e(k)}</span>'
        f'<span class="v">{v}</span></div>'
        for k, v in stats
    )
    src = (f"market snapshot · {m['odds_fetched']}" if m["odds_fetched"]
           else "model only — no market data")
    return f"""
  <header class="hero reveal">
    <div class="hero-grid">
      <div class="hero-lead">
        <div class="kicker">Monte Carlo × Prediction Market</div>
        <h1>{_e(m['tournament'])}</h1>
        <p class="sub">Live model-vs-market dashboard · generated {_e(m['generated'])}
           · <span class="src">{_e(src)}</span></p>
        <div class="stats">{stat_html}</div>
      </div>
      {favourite}
    </div>
  </header>"""


def _nav() -> str:
    items = [("title", "Title race"), ("groups", "Groups"),
             ("fixtures", "Fixtures"), ("perf", "Model vs market"),
             ("value", "Value")]
    links = "".join(f'<a href="#{i}">{_e(t)}</a>' for i, t in items)
    return f'<nav class="nav reveal"><span class="brand">⚽ WC26</span>{links}</nav>'


def _title_section(rep: dict) -> str:
    rows = rep["title"]
    if not rows:
        return ""
    top_champ = max((r["champion"] for r in rows), default=0.0) or 1.0
    has_mkt = rep["meta"]["has_market"]
    body = []
    for i, r in enumerate(rows):
        lead = "lead" if i == 0 else ""
        edge = r.get("edge")
        if edge is None:
            edge_cell = '<td class="num faint">—</td>'
        else:
            cls = "pos" if edge >= 0 else "neg"
            edge_cell = f'<td class="num edge {cls}">{_sign(edge)}</td>'
        mkt_cell = (f'<td class="num market">{_p1(r["market"])}</td>'
                    if has_mkt else "")
        body.append(f"""
        <tr class="{lead}">
          <td class="rank">{i + 1}</td>
          <td class="team"><b>{_e(r['team'])}</b><span class="grp">{_e(r['group'])}</span></td>
          <td class="num small">{_p0(r['r16'])}</td>
          <td class="num small">{_p0(r['qf'])}</td>
          <td class="num small">{_p0(r['sf'])}</td>
          <td class="num small">{_p0(r['final'])}</td>
          <td class="champ">
            <div class="champ-wrap">{_bar(r['champion'], 'lime', top_champ)}
              <span class="champ-v">{_p1(r['champion'])}</span></div>
          </td>
          {mkt_cell}
          {edge_cell}
        </tr>""")
    mkt_head = '<th class="num">Market</th>' if has_mkt else ""
    edge_head = '<th class="num">Edge</th>' if has_mkt else ""
    return f"""
  <section id="title" class="reveal">
    <div class="sec-head">
      <h2>Title race</h2>
      <p>Chance of reaching each stage — our Monte Carlo vs the Polymarket winner market.
         <span class="lime-key">lime = model</span> · <span class="blue-key">blue = market</span></p>
    </div>
    <div class="card table-wrap">
      <table class="title-table">
        <thead><tr>
          <th>#</th><th>Team</th>
          <th class="num">R16</th><th class="num">QF</th>
          <th class="num">SF</th><th class="num">Final</th>
          <th class="num champ-h">Champion</th>{mkt_head}{edge_head}
        </tr></thead>
        <tbody>{''.join(body)}</tbody>
      </table>
    </div>
  </section>"""


def _groups_section(rep: dict) -> str:
    cards = []
    for g in rep["groups"]:
        rows = []
        for i, r in enumerate(g["rows"]):
            rows.append(f"""
          <tr class="z-{r['zone']}">
            <td class="pos">{_zone_dot(r['zone'])}{i + 1}</td>
            <td class="gteam">{_e(r['team'])}</td>
            <td class="num">{r['pld']}</td>
            <td class="num gd">{r['gd']:+d}</td>
            <td class="num pts">{r['pts']}</td>
          </tr>""")
        cards.append(f"""
      <div class="group-card">
        <div class="group-top"><span class="gletter">{_e(g['letter'])}</span>Group {_e(g['letter'])}</div>
        <table class="group-table">
          <thead><tr><th></th><th></th><th class="num">P</th>
            <th class="num">GD</th><th class="num">Pts</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </div>""")
    return f"""
  <section id="groups" class="reveal">
    <div class="sec-head">
      <h2>Group standings</h2>
      <p>Real tables from played results (provisional).
         <span class="legend"><span class="dot dot-auto"></span>top two advance</span>
         <span class="legend"><span class="dot dot-third"></span>best-third race</span>
         <span class="legend"><span class="dot dot-out"></span>trailing</span></p>
    </div>
    <div class="group-grid">{''.join(cards)}</div>
  </section>"""


def _result_card(r: dict) -> str:
    res = r.get("result")
    cls = {"H": "win-h", "D": "draw", "A": "win-a"}.get(res, "")
    chips = ""
    if r.get("market") is not None and res:
        def chip(ok, label):
            return f'<span class="vchip {"ok" if ok else "no"}">{label} {"✓" if ok else "✗"}</span>'
        chips = (f'<div class="vchips">{chip(r.get("model_right"), "model")}'
                 f'{chip(r.get("market_right"), "market")}</div>')
    model = r["model"]
    market = r.get("market")
    nums = (f'<div class="mm"><span class="lime-key">M</span> {_p0(model[0])}·{_p0(model[1])}·{_p0(model[2])}</div>')
    if market:
        nums += (f'<div class="mm"><span class="blue-key">K</span> '
                 f'{_p0(market[0])}·{_p0(market[1])}·{_p0(market[2])}</div>')
    return f"""
      <div class="match played {cls}">
        <div class="match-row">
          <span class="ht">{_e(r['home'])}</span>
          <span class="score">{_e(r['score'] or '')}</span>
          <span class="at">{_e(r['away'])}</span>
        </div>
        {_segbar(model)}
        <div class="match-foot">{nums}{chips}</div>
      </div>"""


def _upcoming_card(r: dict) -> str:
    edge = r.get("edge")
    edge_chip = ""
    if edge:
        side = {"H": r["home"], "D": "Draw", "A": r["away"]}[edge["side"]]
        cls = "pos" if edge["value"] >= 0 else "neg"
        edge_chip = f'<span class="echip {cls}">{_e(side)} {_sign(edge["value"])}</span>'
    model = r["model"]
    market = r.get("market")
    nums = (f'<div class="mm"><span class="lime-key">M</span> {_p0(model[0])}·{_p0(model[1])}·{_p0(model[2])}</div>')
    if market:
        nums += (f'<div class="mm"><span class="blue-key">K</span> '
                 f'{_p0(market[0])}·{_p0(market[1])}·{_p0(market[2])}</div>')
    return f"""
      <div class="match up">
        <div class="match-row">
          <span class="ht">{_e(r['home'])}</span>
          <span class="vs">{_e(r['date'][5:])}</span>
          <span class="at">{_e(r['away'])}</span>
        </div>
        {_segbar(model)}
        <div class="match-foot">{nums}{edge_chip}</div>
      </div>"""


def _fixtures_section(rep: dict) -> str:
    results = rep["results"][:12]
    upcoming = rep["upcoming"]
    res_html = "".join(_result_card(r) for r in results) or '<p class="empty">No games played yet.</p>'
    up_html = "".join(_upcoming_card(r) for r in upcoming) or '<p class="empty">No upcoming fixtures.</p>'
    return f"""
  <section id="fixtures" class="reveal">
    <div class="sec-head">
      <h2>Fixtures</h2>
      <p>Three-way odds per game — <span class="lime-key">M = model</span>,
         <span class="blue-key">K = market</span> (home·draw·away). The bar is the model.</p>
    </div>
    <div class="fix-grid">
      <div class="fix-col">
        <h3>Upcoming</h3>
        <div class="match-list">{up_html}</div>
      </div>
      <div class="fix-col">
        <h3>Recent results</h3>
        <div class="match-list">{res_html}</div>
      </div>
    </div>
  </section>"""


def _perf_section(rep: dict) -> str:
    p = rep["performance"]
    if not p["n"]:
        return ""
    mix = p["mix"]
    sharper = "model" if p["model_sharper"] else "market"
    gap = abs(p["model_brier"] - p["market_brier"])
    max_brier = max(p["model_brier"], p["market_brier"], 0.001)

    def metric(label, mv, kv, fmt, lower_better=True):
        better_model = (mv < kv) if lower_better else (mv > kv)
        return f"""
        <div class="metric">
          <div class="m-label">{_e(label)}</div>
          <div class="m-vals">
            <span class="m-model {'best' if better_model else ''}">{fmt(mv)}</span>
            <span class="m-market {'best' if not better_model else ''}">{fmt(kv)}</span>
          </div>
        </div>"""

    def acc_pct(c):
        return c / p["n"]
    metrics = (
        metric("Favourite correct", acc_pct(p["model_correct"]), acc_pct(p["market_correct"]),
               _p0, lower_better=False)
        + metric("Brier score", p["model_brier"], p["market_brier"], lambda x: f"{x:.3f}")
        + metric("Log-loss", p["model_logloss"], p["market_logloss"], lambda x: f"{x:.3f}")
    )

    h2h = ""
    if p["disagreements"]:
        lead = ("model" if p["model_edge_wins"] > p["market_edge_wins"]
                else "market" if p["market_edge_wins"] > p["model_edge_wins"] else "neither")
        h2h = f"""
      <div class="h2h">
        <div class="h2h-title">When they backed different favourites
          <span class="muted">· {p['disagreements']} games</span></div>
        <div class="h2h-bars">
          <div class="h2h-side"><span class="lime-key">model's pick won</span>
            <b>{p['model_edge_wins']}</b></div>
          <div class="h2h-side"><span class="blue-key">market's pick won</span>
            <b>{p['market_edge_wins']}</b></div>
        </div>
        <div class="h2h-verdict">{_e(lead.upper())} has the edge so far</div>
      </div>"""

    return f"""
  <section id="perf" class="reveal">
    <div class="sec-head">
      <h2>Model vs market</h2>
      <p>A genuine backtest over {p['n']} finished games — real outcomes, pre-kickoff market odds.
         Lower Brier / log-loss is sharper.</p>
    </div>
    <div class="perf-grid">
      <div class="card perf-card">
        <div class="verdict">
          <span class="big">{sharper.upper()}</span> is sharper
          <span class="by">by {gap:.3f} Brier</span>
        </div>
        <div class="metrics">{metrics}</div>
        <div class="brierbars">
          <div class="bb"><span>model</span>{_bar(p['model_brier'], 'lime', max_brier)}
            <em>{p['model_brier']:.3f}</em></div>
          <div class="bb"><span>market</span>{_bar(p['market_brier'], 'blue', max_brier)}
            <em>{p['market_brier']:.3f}</em></div>
        </div>
        <div class="mixline">Results so far ·
          <b>{mix['H']}</b> home · <b>{mix['D']}</b> draw · <b>{mix['A']}</b> away</div>
      </div>
      {h2h}
    </div>
  </section>"""


def _value_section(rep: dict) -> str:
    v = rep["value_bets"]
    if not v:
        return ""
    comps = [c for c in v["comparisons"] if c["edge"] > 0][:12]
    rows = []
    for c in comps:
        rows.append(f"""
        <tr>
          <td class="team"><b>{_e(c['team'])}</b></td>
          <td class="num market">{_p1(c['market'])}</td>
          <td class="num">{_p1(c['model'])}</td>
          <td class="num edge pos">{_sign(c['edge'])}</td>
          <td class="num">{c['ev'] * 100:+.0f}%</td>
        </tr>""")
    table = (f'<table class="value-table"><thead><tr><th>Team</th>'
             f'<th class="num">Market</th><th class="num">Model</th>'
             f'<th class="num">Edge</th><th class="num">EV/$1</th></tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table>') if rows else \
            '<p class="empty">No positive-edge teams in the winner market right now.</p>'

    bets = v["bets"]
    bet_html = ""
    if bets:
        items = "".join(
            f"""<div class="bet">
              <div class="bet-team">{_e(b['team'])}</div>
              <div class="bet-stake">${b['stake']:.2f}</div>
              <div class="bet-meta">@ {b['price']:.3f} · edge {b['edge'] * 100:+.0f}% · EV {b['ev'] * 100:+.0f}%</div>
            </div>"""
            for b in bets
        )
        bet_html = f"""
      <div class="bets-panel">
        <div class="bets-head">Half-Kelly slate
          <span class="muted">· ${v['bankroll']:.0f} bankroll · ${v['total_staked']:.2f} staked</span></div>
        <div class="bets">{items}</div>
      </div>"""

    return f"""
  <section id="value" class="reveal">
    <div class="sec-head">
      <h2>Value bets</h2>
      <p>Where our title odds sit above the winner market — positive expected value if the model is right.</p>
    </div>
    <div class="value-grid">
      <div class="card">{table}</div>
      {bet_html}
    </div>
  </section>"""


def _footer(rep: dict) -> str:
    m = rep["meta"]
    return f"""
  <footer class="foot reveal">
    <p>Model: results-aware Monte Carlo ({m['title_iters']:,} tournaments, {m['game_iters']:,} sims/fixture).
       Market: Polymarket{(' · snapshot ' + m['odds_fetched']) if m['odds_fetched'] else ''}.</p>
    <p class="fine">Curated ratings · simulated odds for entertainment and analysis, not betting advice.
       Value-bet P&amp;L is settled by the same model that prices it — edge variance, not an independent backtest.</p>
  </footer>"""


# --- assembly --------------------------------------------------------------

def render_dashboard(rep: dict) -> str:
    """Return a complete, self-contained HTML document for ``rep``."""
    body = (
        _nav() + _hero(rep) + _title_section(rep) + _groups_section(rep)
        + _fixtures_section(rep) + _perf_section(rep) + _value_section(rep)
        + _footer(rep)
    )
    title = _e(rep["meta"]["tournament"]) + " · Model vs Market"
    return _DOC.replace("{{TITLE}}", title).replace("{{BODY}}", body)


_DOC = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;800;900&family=Spline+Sans:wght@400;500;600&family=Spline+Sans+Mono:wght@500;600&display=swap" rel="stylesheet">
<style>
""" + """
:root{
  --bg:#070b09; --bg2:#0a110d; --panel:#0e1714; --panel2:#13211a;
  --ink:#e9f2ec; --muted:#7e958a; --faint:#46564d;
  --line:rgba(180,255,210,.09); --line2:rgba(180,255,210,.05);
  --accent:#b8ff3c; --accent-dim:rgba(184,255,60,.14);
  --market:#57c7ff; --market-dim:rgba(87,199,255,.14);
  --gold:#ffce4d; --pos:#5be08c; --neg:#ff6b7a;
  --home:#3ddc97; --draw:#54655d; --away:#7aa2ff;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  margin:0; background:var(--bg); color:var(--ink);
  font-family:"Spline Sans",system-ui,sans-serif; font-size:15px; line-height:1.5;
  -webkit-font-smoothing:antialiased;
  background-image:
    radial-gradient(900px 520px at 8% -8%, rgba(184,255,60,.10), transparent 60%),
    radial-gradient(820px 600px at 105% 4%, rgba(87,199,255,.09), transparent 55%),
    repeating-linear-gradient(0deg, transparent, transparent 38px, rgba(255,255,255,.012) 38px, rgba(255,255,255,.012) 39px);
  background-attachment:fixed;
}
.wrap{max-width:1180px; margin:0 auto; padding:0 22px 80px}
h1,h2,h3{font-family:"Archivo",sans-serif; margin:0; letter-spacing:-.015em}
.num,.mono{font-family:"Spline Sans Mono",monospace; font-variant-numeric:tabular-nums}

/* nav */
.nav{position:sticky; top:0; z-index:30; display:flex; gap:6px; align-items:center;
  margin:0 -22px 0; padding:11px 22px; backdrop-filter:blur(14px);
  background:linear-gradient(180deg, rgba(7,11,9,.92), rgba(7,11,9,.62));
  border-bottom:1px solid var(--line)}
.nav .brand{font-family:"Archivo"; font-weight:900; letter-spacing:.02em; margin-right:auto; color:var(--accent)}
.nav a{color:var(--muted); text-decoration:none; font-size:13px; font-weight:600;
  padding:6px 11px; border-radius:999px; transition:.18s}
.nav a:hover{color:var(--ink); background:var(--line)}
.nav a.active{color:var(--bg); background:var(--accent)}

/* hero */
.hero{padding:54px 0 30px}
.hero-grid{display:grid; grid-template-columns:1.5fr .9fr; gap:34px; align-items:end}
.kicker{font-family:"Spline Sans Mono"; font-size:11.5px; letter-spacing:.32em;
  text-transform:uppercase; color:var(--accent); margin-bottom:14px}
.hero h1{font-size:clamp(38px,6.4vw,76px); font-weight:900; line-height:.92;
  text-transform:uppercase; font-stretch:expanded;
  background:linear-gradient(94deg,#fff,#cfeede 55%,var(--accent)); -webkit-background-clip:text;
  background-clip:text; color:transparent}
.hero .sub{color:var(--muted); margin:16px 0 24px; font-size:14px}
.hero .src{color:var(--market)}
.stats{display:flex; flex-wrap:wrap; gap:10px}
.stat{background:var(--panel); border:1px solid var(--line); border-radius:14px;
  padding:12px 16px; min-width:118px}
.stat .k{display:block; font-size:11px; text-transform:uppercase; letter-spacing:.09em; color:var(--muted)}
.stat .v{display:block; font-family:"Archivo"; font-weight:800; font-size:25px; margin-top:3px}
.stat .v small{font-size:14px; color:var(--faint); font-weight:600}
.favourite{position:relative; background:
   linear-gradient(160deg, rgba(255,206,77,.14), rgba(14,23,20,.6) 46%);
   border:1px solid rgba(255,206,77,.32); border-radius:20px; padding:22px 24px;
   box-shadow:0 24px 60px -28px rgba(255,206,77,.5)}
.fav-label{font-size:11px; letter-spacing:.16em; text-transform:uppercase; color:var(--gold)}
.fav-team{font-family:"Archivo"; font-weight:900; font-size:34px; line-height:1.04; margin:6px 0 12px; text-transform:uppercase}
.fav-odds{display:flex; align-items:baseline; gap:7px; flex-wrap:wrap; font-size:13px; color:var(--muted)}
.fav-odds b{font-family:"Spline Sans Mono"; font-size:30px; color:var(--accent); font-weight:600}
.fav-odds em{font-family:"Spline Sans Mono"; font-size:19px; color:var(--market); font-style:normal; margin-left:6px}
.chip{display:inline-block; margin-top:13px; font-size:12px; font-weight:600; padding:4px 11px;
  border-radius:999px}
.chip.pos{background:var(--accent-dim); color:var(--accent)}
.chip.neg{background:rgba(255,107,122,.14); color:var(--neg)}

/* sections */
section{padding:40px 0 8px}
.sec-head{margin-bottom:20px}
.sec-head h2{font-size:clamp(24px,3vw,34px); font-weight:800; text-transform:uppercase; letter-spacing:-.01em}
.sec-head h2::before{content:""; display:inline-block; width:10px; height:10px; border-radius:3px;
  background:var(--accent); margin-right:12px; vertical-align:middle; transform:translateY(-3px)}
.sec-head p{color:var(--muted); margin:8px 0 0; font-size:13.5px}
.lime-key{color:var(--accent); font-weight:600}
.blue-key{color:var(--market); font-weight:600}
.card{background:var(--panel); border:1px solid var(--line); border-radius:18px}

/* bars */
.bar{display:inline-block; position:relative; height:8px; width:100%; border-radius:6px;
  background:var(--line); overflow:hidden; vertical-align:middle}
.bar i{position:absolute; left:0; top:0; bottom:0; width:0; border-radius:6px;
  animation:grow 1s cubic-bezier(.2,.7,.2,1) forwards}
.bar.lime i{background:linear-gradient(90deg,#7fd520,var(--accent))}
.bar.blue i{background:linear-gradient(90deg,#2f8fd0,var(--market))}
@keyframes grow{to{width:var(--w)}}

/* title table */
.table-wrap{overflow-x:auto}
table{width:100%; border-collapse:collapse}
.title-table th,.title-table td{padding:11px 12px; text-align:left; border-bottom:1px solid var(--line2)}
.title-table th{font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); font-weight:600}
.title-table th.num,.title-table td.num{text-align:right}
.title-table .rank{color:var(--faint); font-family:"Spline Sans Mono"; width:30px}
.title-table .team b{font-weight:600}
.title-table .grp{display:inline-block; margin-left:8px; font-size:10px; font-weight:700; color:var(--muted);
  border:1px solid var(--line); border-radius:5px; padding:1px 6px; vertical-align:middle}
.title-table td.small{color:var(--muted); font-size:13px}
.title-table .champ{width:160px}
.champ-wrap{display:flex; align-items:center; gap:10px}
.champ-v{font-family:"Spline Sans Mono"; color:var(--accent); font-weight:600; min-width:46px; text-align:right}
.title-table td.market{color:var(--market); font-weight:500}
.title-table td.edge.pos{color:var(--pos)} .title-table td.edge.neg{color:var(--neg)}
.title-table tr.lead td{background:linear-gradient(90deg, rgba(255,206,77,.08), transparent 70%)}
.title-table tr.lead .team b{color:var(--gold)}
.title-table tbody tr:hover td{background:rgba(184,255,60,.04)}
.faint{color:var(--faint)}

/* groups */
.group-grid{display:grid; grid-template-columns:repeat(auto-fill,minmax(266px,1fr)); gap:14px}
.group-card{background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:12px 14px 6px}
.group-top{display:flex; align-items:center; gap:10px; font-family:"Archivo"; font-weight:700;
  font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); margin-bottom:6px}
.gletter{display:grid; place-items:center; width:26px; height:26px; border-radius:8px;
  background:var(--accent-dim); color:var(--accent); font-weight:900; font-size:14px}
.group-table{width:100%; font-size:13px}
.group-table th{padding:2px 5px; font-size:10px; color:var(--faint); font-weight:600}
.group-table td{padding:5px 5px; border-bottom:1px solid var(--line2)}
.group-table tr:last-child td{border-bottom:none}
.group-table .pos{color:var(--faint); font-family:"Spline Sans Mono"; width:34px; white-space:nowrap}
.group-table .gteam{font-weight:500; width:100%}
.group-table .num{text-align:right; font-family:"Spline Sans Mono"}
.group-table .pts{color:var(--ink); font-weight:600}
.group-table .gd{color:var(--muted)}
.dot{display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:6px; vertical-align:middle}
.dot-auto{background:var(--accent)} .dot-third{background:var(--gold)} .dot-out{background:var(--faint)}
tr.z-auto .gteam{color:var(--ink)} tr.z-out .gteam{color:var(--muted)}
.legend{margin-left:14px; font-size:12px; color:var(--muted)}

/* fixtures */
.fix-grid{display:grid; grid-template-columns:1fr 1fr; gap:18px}
.fix-col h3{font-size:13px; text-transform:uppercase; letter-spacing:.1em; color:var(--muted); margin:0 0 12px; font-weight:600}
.match-list{display:flex; flex-direction:column; gap:9px}
.match{background:var(--panel); border:1px solid var(--line); border-radius:13px; padding:11px 14px; transition:.16s}
.match:hover{border-color:rgba(184,255,60,.28); transform:translateY(-1px)}
.match-row{display:grid; grid-template-columns:1fr auto 1fr; align-items:center; gap:10px; font-weight:500}
.match-row .ht{text-align:right} .match-row .at{text-align:left}
.match .score{font-family:"Archivo"; font-weight:800; font-size:18px; padding:0 10px}
.match .vs{font-family:"Spline Sans Mono"; font-size:11px; color:var(--faint); padding:0 8px}
.match.win-h .ht,.match.win-a .at{color:var(--accent)}
.seg{display:flex; height:6px; border-radius:5px; overflow:hidden; margin:10px 0 8px; background:var(--line)}
.seg i{height:100%; width:0; animation:grow 1s cubic-bezier(.2,.7,.2,1) forwards}
.seg .seg-h{background:var(--home)} .seg .seg-d{background:var(--draw)} .seg .seg-a{background:var(--away)}
.seg.empty{background:var(--line)}
.match-foot{display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap}
.mm{font-family:"Spline Sans Mono"; font-size:11.5px; color:var(--muted)}
.vchips,.echip{display:flex; gap:5px}
.vchip{font-size:10.5px; font-weight:600; padding:2px 7px; border-radius:6px}
.vchip.ok{background:var(--accent-dim); color:var(--accent)} .vchip.no{background:rgba(255,107,122,.12); color:var(--neg)}
.echip{font-size:11px; font-weight:600; padding:3px 9px; border-radius:7px}
.echip.pos{background:var(--accent-dim); color:var(--accent)} .echip.neg{background:rgba(255,107,122,.12); color:var(--neg)}
.empty{color:var(--faint); font-size:13px}

/* performance */
.perf-grid{display:grid; grid-template-columns:1.4fr 1fr; gap:18px}
.perf-card{padding:22px 24px}
.verdict{font-family:"Archivo"; font-size:19px; font-weight:600; color:var(--muted); margin-bottom:18px}
.verdict .big{font-size:30px; font-weight:900; color:var(--accent)}
.verdict .by{display:block; font-size:13px; color:var(--faint); margin-top:2px}
.metrics{display:flex; flex-direction:column; gap:2px; margin-bottom:18px}
.metric{display:grid; grid-template-columns:1fr auto; align-items:center; padding:9px 0; border-bottom:1px solid var(--line2)}
.m-label{color:var(--muted); font-size:13px}
.m-vals{display:flex; gap:18px; font-family:"Spline Sans Mono"; font-weight:600}
.m-model{color:var(--accent); opacity:.45} .m-market{color:var(--market); opacity:.45}
.m-model.best,.m-market.best{opacity:1}
.m-vals span{position:relative; min-width:54px; text-align:right}
.m-vals .best::after{content:"●"; font-size:8px; position:absolute; right:-12px; top:5px}
.brierbars{display:flex; flex-direction:column; gap:8px; margin-bottom:16px}
.bb{display:grid; grid-template-columns:54px 1fr 50px; align-items:center; gap:10px; font-size:12px; color:var(--muted)}
.bb em{font-family:"Spline Sans Mono"; font-style:normal; text-align:right; color:var(--ink)}
.mixline{font-size:12.5px; color:var(--muted)} .mixline b{color:var(--ink); font-family:"Spline Sans Mono"}
.h2h{background:var(--panel); border:1px solid var(--line); border-radius:18px; padding:22px 24px}
.h2h-title{font-family:"Archivo"; font-weight:700; font-size:15px; margin-bottom:18px}
.h2h-title .muted{color:var(--faint); font-weight:400}
.h2h-bars{display:flex; flex-direction:column; gap:12px; margin-bottom:16px}
.h2h-side{display:flex; justify-content:space-between; align-items:baseline; font-size:13px;
  border-bottom:1px solid var(--line2); padding-bottom:10px}
.h2h-side b{font-family:"Archivo"; font-size:26px; font-weight:900}
.h2h-verdict{font-family:"Spline Sans Mono"; font-size:12px; letter-spacing:.04em; color:var(--gold)}

/* value */
.value-grid{display:grid; grid-template-columns:1.5fr 1fr; gap:18px}
.value-table{font-size:14px}
.value-table th,.value-table td{padding:10px 12px; border-bottom:1px solid var(--line2); text-align:left}
.value-table th{font-size:11px; text-transform:uppercase; color:var(--muted); letter-spacing:.07em}
.value-table th.num,.value-table td.num{text-align:right; font-family:"Spline Sans Mono"}
.value-table td.market{color:var(--market)} .value-table td.edge.pos{color:var(--pos)}
.value-table .card{padding:0}
.bets-panel{background:linear-gradient(160deg, rgba(184,255,60,.08), var(--panel) 50%);
  border:1px solid rgba(184,255,60,.24); border-radius:18px; padding:18px 20px}
.bets-head{font-family:"Archivo"; font-weight:700; margin-bottom:14px}
.bets-head .muted{color:var(--faint); font-weight:400; font-size:12px}
.bets{display:flex; flex-direction:column; gap:10px}
.bet{display:grid; grid-template-columns:1fr auto; gap:2px 12px; align-items:baseline;
  border-bottom:1px solid var(--line2); padding-bottom:10px}
.bet-team{font-weight:600} .bet-stake{font-family:"Archivo"; font-weight:900; font-size:20px; color:var(--accent)}
.bet-meta{grid-column:1/-1; font-size:11.5px; color:var(--muted); font-family:"Spline Sans Mono"}

/* footer */
.foot{margin-top:48px; padding-top:24px; border-top:1px solid var(--line); color:var(--muted); font-size:12.5px}
.foot .fine{color:var(--faint); margin-top:6px}

/* reveal */
.reveal{opacity:0; transform:translateY(16px); transition:opacity .7s ease, transform .7s ease}
.reveal.in{opacity:1; transform:none}
.nav.reveal{opacity:1; transform:none}

@media(max-width:880px){
  .hero-grid,.perf-grid,.value-grid,.fix-grid{grid-template-columns:1fr}
  .hero{padding:34px 0 20px}
  .nav{gap:2px; overflow-x:auto} .nav a{padding:6px 9px}
}
</style>
</head>
<body>
<div class="wrap">
{{BODY}}
</div>
<script>
(function(){
  var obs=new IntersectionObserver(function(es){
    es.forEach(function(e){ if(e.isIntersecting){ e.target.classList.add('in'); obs.unobserve(e.target);} });
  },{threshold:.08});
  document.querySelectorAll('.reveal').forEach(function(el){obs.observe(el);});

  var secs=[].slice.call(document.querySelectorAll('section[id]'));
  var links=[].slice.call(document.querySelectorAll('.nav a'));
  function spy(){
    var y=window.scrollY+120, cur=secs[0];
    secs.forEach(function(s){ if(s.offsetTop<=y) cur=s; });
    links.forEach(function(a){ a.classList.toggle('active', a.getAttribute('href')==='#'+(cur&&cur.id)); });
  }
  window.addEventListener('scroll',spy,{passive:true}); spy();
})();
</script>
</body>
</html>"""
