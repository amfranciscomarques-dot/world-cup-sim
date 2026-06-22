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
    return _DOC.replace("{{TITLE}}", title).replace("{{BODY}}", body).replace("{{EXTRA}}", "")


# --- Tracker page (user bets: model vs market, projected ROI) ------------


def _tracker_nav() -> str:
    """Top nav for the tracker page — links to the in-page anchors it renders."""
    items = [("hero", "Overview"), ("live", "Live status"),
             ("legs", "Per-leg model vs market"),
             ("bets", "Bet history"), ("perf", "P&L distribution")]
    links = "".join(f'<a href="#{i}">{_e(t)}</a>' for i, t in items)
    return (
        f'<nav class="nav reveal"><span class="brand">🎯 WC26 Tracker</span>'
        f'<a href="/" class="back">← Web UI</a>{links}</nav>'
    )


def _tracker_hero(rep: dict) -> str:
    perf = rep["performance"]
    meta = rep["meta"]
    pos = perf["mean_pnl"] >= 0
    edge_bets = sum(
        1 for s in rep["selections"]
        if "error" not in s and abs(s["edge"]) >= 0.02
    )
    value_bets = sum(
        1 for s in rep["selections"]
        if "error" not in s and s["edge"] >= 0.02
    )
    # Mean of per-bet win rates — the honest "what % of your slips win on
    # average" number. The whole-portfolio `win_rate` always reads 100% on
    # any positive-edge portfolio because the model's mean P&L across 500
    # runs is positive even with many individual losing slips; that's an
    # artefact of the simulation, not a settled result.
    per_bet_pnl = perf.get("per_bet_pnl", {}) or {}
    per_bet_wr = []
    for series in per_bet_pnl.values():
        if not series:
            continue
        per_bet_wr.append(sum(1 for x in series if x > 0) / len(series))
    mean_slip_wr = sum(per_bet_wr) / len(per_bet_wr) if per_bet_wr else 0.0
    stat = lambda l, v, cls="": f'<div><small>{_e(l)}</small><div class="num {cls}">{v}</div></div>'
    pnl_sign = "+" if pos else ""
    return f"""
  <section id="hero" class="reveal">
    <div class="hero">
      <div class="hero-meta">
        <div class="subtle">{_e(meta['tournament'])} · Tracker · generated {_e(meta['generated'])}</div>
        <h1>Your bets, priced against the model</h1>
        <p class="hero-sub">
          {meta['n_bets']} slips · {meta['n_selections']} selections · {perf['iterations']} simulated tournaments.
          Bets settle on the same model that prices them — this is edge variance, not an independent backtest.
        </p>
      </div>
      <div class="hero-stats">
        {stat('Total staked', f"€{perf['total_staked']:.2f}")}
        {stat('Mean P&L', f"{pnl_sign}€{perf['mean_pnl']:.2f}", 'pos' if pos else 'neg')}
        {stat('ROI', f"{pnl_sign}{perf['roi'] * 100:.1f}%", 'pos' if pos else 'neg')}
        {stat('Mean slip win%', f"{mean_slip_wr * 100:.1f}%")}
        {stat('P5 / P95', f"€{perf['p5']:.2f} / €{perf['p95']:.2f}")}
        {stat('Value legs (edge ≥ 2%)', f"{value_bets} / {edge_bets}")}
      </div>
    </div>
  </section>
"""


def _tracker_live_section() -> str:
    """Live bet status section: api-football.com live scores + Polymarket
    outright sentiment. Both panels are client-side JS — no extra data is
    needed at build time. The default API key is shipped inline so the
    section works out of the box; the user can override it in the UI."""
    return """
  <section id="live" class="reveal">
    <div class="sec-head">
      <h2>Live bet status</h2>
      <p>Today's <b>World Cup</b> matches from <span class="src">api-football.com</span> and
        real-time <span class="src">polymarket.com</span> outright sentiment.
        Each leg row in the table below gets a <b class="lime-key">winning</b> /
        <b style="color:var(--neg)">losing</b> / <b style="color:var(--gold)">live</b> chip.
        Click <b>Refresh</b> to fetch live data on demand.</p>
    </div>
    <div class="live-card">
      <div class="live-head">
        <h2>⚽ Live &amp; today</h2>
        <div class="live-key">
          <input id="apikey" type="password" placeholder="api-football.com key…" autocomplete="off" spellcheck="false">
          <button id="apikey-save">Save</button>
        </div>
        <div class="live-status">
          <span class="live-dot" id="live-dot"></span>
          <span id="live-status-text">Loading…</span>
        </div>
        <button id="live-refresh" class="refresh-btn">↻ Refresh</button>
      </div>
      <div id="live-list">
        <div class="live-empty">Press Refresh to load today's fixtures.</div>
      </div>
      <div class="live-foot">
        <span>Provider: <a href="https://www.api-football.com/" target="_blank" rel="noopener" style="color:var(--market)">api-football.com</a> · free tier: 100 req/day</span>
        <span id="live-meta"></span>
      </div>
    </div>
    <div class="poly-card" style="margin-top:18px">
      <div class="poly-head">
        <h2>📊 Polymarket sentiment · World Cup winner</h2>
        <div class="poly-status">
          <span class="live-dot" id="poly-dot"></span>
          <span id="poly-status-text">Loading…</span>
          <button id="poly-refresh" class="refresh-btn">↻ Refresh</button>
        </div>
      </div>
      <p class="poly-note">
        Real-time <span class="src">polymarket.com</span> outright winner prices for the 12 teams in your bets.
        Shows the market's implied chance each team lifts the trophy, plus 24h delta.
        <b>Not per-match</b> — outright ticks on tournament-wide events, not individual goals.
      </p>
      <div id="poly-list">
        <div class="live-empty">Fetching outright markets…</div>
      </div>
      <div class="live-foot">
        <span>Provider: <a href="https://polymarket.com/sports" target="_blank" rel="noopener" style="color:var(--market)">polymarket.com</a> · no key needed</span>
        <span id="poly-meta"></span>
      </div>
    </div>
  </section>
"""


def _tracker_legs_section(rep: dict) -> str:
    sels = rep["selections"]
    if not sels:
        return f"""
  <section id="legs" class="reveal">
    <div class="sec-head"><h2>Per-leg model vs market</h2></div>
    <p class="empty">No saved bets in <code>data/user_bets_2026.json</code>.</p>
  </section>
"""
    rows = []
    for s in sels:
        if "error" in s:
            rows.append(f"""
        <tr>
          <td class="team"><b>{_e(s['label'])}</b><br><span class="subtle mono">{_e(s['type'])}</span></td>
          <td class="num">{s['odds']:.2f}</td>
          <td colspan="4" class="neg">unpriced — {_e(s['error'])}</td>
        </tr>""")
            continue
        edge = s["edge"]
        cls = "edge pos" if edge >= 0.02 else ("edge neg" if edge <= -0.02 else "edge zero")
        sign = "+" if edge >= 0 else ""
        sub_bits = [_e(s["type"])]
        if s.get("team"):
            sub_bits.append(_e(s["team"]))
        if s.get("player"):
            sub_bits.append(_e(s["player"]))
        sub = " · ".join(sub_bits)
        rows.append(f"""
        <tr>
          <td class="team"><b>{_e(s['label'])}</b><br><span class="subtle">{sub}</span></td>
          <td class="num">{s['odds']:.2f}</td>
          <td class="num market">{_p1(s['implied_prob'])}</td>
          <td class="num model">{_p1(s['model_prob'])}</td>
          <td class="num {cls}">{sign}{edge * 100:.1f}%</td>
          <td class="num">{s['fair_odds']:.2f}</td>
        </tr>""")
    return f"""
  <section id="legs" class="reveal">
    <div class="sec-head">
      <h2>Per-leg model vs market</h2>
      <p>Model probability is the engine's read (Poisson + factors); implied is 1 / decimal odds (vig-included).
        <b>Edge</b> is model − implied; positive = the leg has value against the bookmaker.</p>
    </div>
    <table class="legs-table">
      <thead><tr>
        <th>Selection</th><th class="num">Book</th>
        <th class="num">Implied</th><th class="num">Model</th>
        <th class="num">Edge</th><th class="num">Fair</th>
      </tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </section>
"""


def _tracker_bets_section(rep: dict) -> str:
    bets = rep["bets"]
    perf = rep["performance"]
    per_bet = perf.get("per_bet_pnl", {})
    if not bets:
        return ""
    rows = []
    for b in bets:
        sels = b["selections"]
        pnl_series = per_bet.get(b["id"], [])
        mean = sum(pnl_series) / len(pnl_series) if pnl_series else 0.0
        wr = sum(1 for x in pnl_series if x > 0) / len(pnl_series) if pnl_series else 0.0
        cls = "pos" if mean >= 0 else "neg"
        sign = "+" if mean >= 0 else ""
        sel_html = " + ".join(_e(s["label"]) for s in sels)
        rows.append(f"""
        <tr>
          <td class="team"><b>{sel_html}</b><br><span class="subtle mono">{_e(b['id'])} · {_e(b['date_placed'])}</span></td>
          <td class="num">€{b['stake']:.2f}</td>
          <td class="num">{b['total_odds']:.2f}</td>
          <td class="num {cls}">{sign}€{mean:.2f}</td>
          <td class="num">{wr * 100:.0f}%</td>
        </tr>""")
    return f"""
  <section id="bets" class="reveal">
    <div class="sec-head">
      <h2>Bet history</h2>
      <p>Per-bet mean P&L and win rate over {perf['iterations']} simulated tournaments.</p>
    </div>
    <table class="legs-table">
      <thead><tr>
        <th>Slip</th><th class="num">Stake</th>
        <th class="num">Odds</th><th class="num">Mean P&L</th><th class="num">Win%</th>
      </tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </section>
"""


def _tracker_perf_section(rep: dict) -> str:
    perf = rep["performance"]
    pnl = perf.get("per_bet_pnl", {})
    if not pnl:
        return ""
    # Histogram of total P&L across the n runs (sum across bets per run).
    n = perf["iterations"]
    totals = [0.0] * n
    for series in pnl.values():
        for i, v in enumerate(series):
            totals[i] += v
    if not totals:
        return ""
    lo, hi = min(totals), max(totals)
    if hi == lo:
        hi = lo + 1.0
    # Bucket into ~24 bars across [lo, hi].
    n_bars = 24
    width = (hi - lo) / n_bars
    counts = [0] * n_bars
    for v in totals:
        idx = int((v - lo) / width)
        if idx == n_bars:
            idx -= 1
        counts[idx] += 1
    peak = max(counts) or 1
    bars = []
    for i, c in enumerate(counts):
        h = (c / peak) * 100.0
        bar_lo = lo + i * width
        bar_hi = bar_lo + width
        cls = "bar-pos" if bar_lo >= 0 else "bar-neg"
        bars.append(
            f'<div class="bar-cell {cls}" style="--h:{h:.1f}%" '
            f'title="{c} runs · €{bar_lo:.1f}..€{bar_hi:.1f}"></div>'
        )
    p5, p50, p95 = perf["p5"], perf["p50"], perf["p95"]
    pnl_sign = lambda v: (f"+€{v:.2f}" if v >= 0 else f"−€{abs(v):.2f}")
    return f"""
  <section id="perf" class="reveal">
    <div class="sec-head">
      <h2>P&L distribution</h2>
      <p>Total profit across all {len(pnl)} slips over {n} simulated tournaments.
        Median <b>{pnl_sign(p50)}</b> · P5 <b>{pnl_sign(p5)}</b> · P95 <b>{pnl_sign(p95)}</b>.</p>
    </div>
    <div class="histogram">{''.join(bars)}</div>
    <div class="hist-axis">
      <span>{pnl_sign(lo)}</span><span>0</span><span>{pnl_sign(hi)}</span>
    </div>
  </section>
"""


def _tracker_footer(rep: dict) -> str:
    return f"""
  <footer class="reveal">
    <p>Refreshed {_e(rep['meta']['generated'])} ·
       {_e(rep['meta']['tournament'])} ·
       {_e(rep['meta']['n_bets'])} slips ·
       {_e(rep['meta']['n_selections'])} selections.</p>
    <p class="subtle">Same numbers as the in-app <i>Bet Tracker</i> screen — built from
       <code>data/user_bets_2026.json</code>, priced by the engine in
       <code>src/worldcup/tracker.py</code>.</p>
  </footer>
"""


def render_tracker_html(rep: dict) -> str:
    """Return a complete, self-contained HTML document for the tracker page.

    Mirrors :func:`render_dashboard` — same ``_DOC`` template + floodlit-stadium
    styling — but the body sections are tracker-specific: a hero KPIs strip,
    a live status panel (api-football + Polymarket, rendered client-side),
    the per-leg model-vs-market table, the bet history, and a P&L histogram.
    """
    body = (
        _tracker_nav()
        + _tracker_hero(rep)
        + _tracker_live_section()
        + _tracker_legs_section(rep)
        + _tracker_bets_section(rep)
        + _tracker_perf_section(rep)
        + _tracker_footer(rep)
    )
    title = _e(rep["meta"]["tournament"]) + " · Bet Tracker"
    return _DOC.replace("{{TITLE}}", title).replace("{{BODY}}", body).replace("{{EXTRA}}", _TRACKER_EXTRA)


_TRACKER_EXTRA = """
<style>
.live-card,.poly-card{background:var(--panel); border:1px solid var(--line); border-radius:18px; padding:18px 20px}
.live-head,.poly-head{display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:14px}
.live-head h2,.poly-head h2{font-family:"Archivo"; font-weight:800; font-size:18px; text-transform:uppercase; letter-spacing:.04em; margin:0}
.poly-head h2{font-size:16px}
.live-key{display:flex; gap:6px; align-items:center; font-size:12px; color:var(--muted)}
.live-key input{background:var(--bg2); border:1px solid var(--line); border-radius:8px; padding:6px 10px; color:var(--ink); font-family:"Spline Sans Mono"; font-size:12px; width:230px}
.live-key input:focus{outline:none; border-color:var(--accent)}
.live-key button{background:var(--accent); color:var(--bg); border:none; border-radius:8px; padding:6px 12px; font-weight:700; font-size:12px; cursor:pointer; font-family:"Archivo"}
.live-key button:hover{filter:brightness(1.1)}
.live-status,.poly-status{font-family:"Spline Sans Mono"; font-size:11.5px; color:var(--muted); margin-left:auto; display:flex; align-items:center; gap:8px}
.live-dot{width:8px; height:8px; border-radius:50%; background:var(--faint)}
.live-dot.ok{background:var(--pos); box-shadow:0 0 8px var(--pos)}
.live-dot.warn{background:var(--gold)}
.live-dot.err{background:var(--neg)}
.live-match{display:grid; grid-template-columns:1.4fr 1fr 1fr 1fr; gap:10px; align-items:center; padding:10px 12px; border-bottom:1px solid var(--line2); font-size:13px}
.live-match:last-child{border-bottom:none}
.live-match .team{font-weight:600}
.live-match .team.away{text-align:right; color:var(--ink)}
.live-match .team.home{text-align:left}
.live-match .score{font-family:"Archivo"; font-weight:800; font-size:18px; text-align:center; color:var(--ink)}
.live-match .score.live{color:var(--accent)}
.live-match .clock{font-family:"Spline Sans Mono"; font-size:11px; color:var(--muted); text-align:center}
.live-match .settle{text-align:center}
.live-match .badge{font-size:10.5px; font-weight:700; padding:3px 8px; border-radius:6px; font-family:"Archivo"; text-transform:uppercase; letter-spacing:.04em}
.live-match .badge.live,.live-match .badge['1h']{background:rgba(255,107,122,.18); color:var(--neg); animation:pulse 1.6s ease-in-out infinite}
.live-match .badge.ft{background:var(--line2); color:var(--muted)}
.live-match .badge.ns{background:var(--line2); color:var(--faint)}
.live-match .badge.ht{background:rgba(255,206,77,.18); color:var(--gold)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.55}}
.live-empty{color:var(--faint); font-size:13px; padding:18px 0; text-align:center; font-style:italic}
.live-foot{margin-top:10px; font-size:11px; color:var(--faint); font-family:"Spline Sans Mono"; display:flex; justify-content:space-between; flex-wrap:wrap; gap:8px}
.leg-live{display:inline-block; margin-left:8px; font-size:9.5px; font-weight:700; padding:2px 6px; border-radius:5px; vertical-align:middle; font-family:"Archivo"; text-transform:uppercase; letter-spacing:.04em}
.leg-live.win{background:var(--accent-dim); color:var(--accent)}
.leg-live.lose{background:rgba(255,107,122,.16); color:var(--neg)}
.leg-live.live{background:rgba(255,206,77,.18); color:var(--gold)}
.leg-live.pending{background:var(--line2); color:var(--faint)}
.refresh-btn{background:transparent; border:1px solid var(--line); border-radius:8px; padding:5px 11px; color:var(--muted); font-family:"Archivo"; font-size:12px; font-weight:700; cursor:pointer; transition:.15s; margin-left:6px}
.refresh-btn:hover{border-color:var(--accent); color:var(--accent)}
.poly-note{color:var(--muted); font-size:13px; margin:0 0 14px}
.poly-note b{color:var(--ink)}
.poly-row{display:grid; grid-template-columns:1.4fr 1fr 1fr 1.2fr; gap:10px; align-items:center; padding:10px 12px; border-bottom:1px solid var(--line2); font-size:13px}
.poly-row:last-child{border-bottom:none}
.poly-row .pteam{font-weight:600}
.poly-row .pprice{font-family:"Spline Sans Mono"; font-weight:600; font-size:14px; text-align:right}
.poly-row .pdelta{font-family:"Spline Sans Mono"; font-size:12px; text-align:right}
.poly-row .pdelta.up{color:var(--pos)}
.poly-row .pdelta.down{color:var(--neg)}
.poly-row .pdelta.flat{color:var(--faint)}
.poly-row .pvol{font-family:"Spline Sans Mono"; font-size:11px; color:var(--muted); text-align:right}
.poly-row.hot{background:linear-gradient(90deg, rgba(184,255,60,.06), transparent 70%)}
@media(max-width:880px){
  .live-match{grid-template-columns:1.5fr 1fr 1fr; gap:6px; font-size:12px}
  .live-match .clock{display:none}
  .live-key input{width:160px}
}
</style>
<script>
/* api-football.com live scores + Polymarket outright sentiment */
(function(){
  var KEY_LS='wc26_apifootball_key';
  var DEFAULT_KEY='4259258e2d4e737a554491478a88819b';
  var $key=document.getElementById('apikey');
  var $save=document.getElementById('apikey-save');
  var $list=document.getElementById('live-list');
  var $dot=document.getElementById('live-dot');
  var $status=document.getElementById('live-status-text');
  var $meta=document.getElementById('live-meta');
  var $plist=document.getElementById('poly-list');
  var $pdot=document.getElementById('poly-dot');
  var $pstatus=document.getElementById('poly-status-text');
  var $pmeta=document.getElementById('poly-meta');
  if(!$key) return;
  var key='';
  try{ key=localStorage.getItem(KEY_LS)||DEFAULT_KEY; }catch(e){ key=DEFAULT_KEY; }
  $key.value=key;
  $save.addEventListener('click',function(){
    var v=$key.value.trim();
    if(!v){ setLS('warn','Key empty'); return; }
    try{ localStorage.setItem(KEY_LS,v); }catch(e){}
    key=v; setLS('warn','Loading…'); refreshLive();
  });
  $key.addEventListener('keydown',function(e){ if(e.key==='Enter') $save.click(); });
  function setLS(kind,msg){ $dot.className='live-dot '+(kind==='ok'?'ok':kind==='err'?'err':'warn'); $status.textContent=msg; }
  function setPS(kind,msg){ $pdot.className='live-dot '+(kind==='ok'?'ok':kind==='err'?'err':'warn'); $pstatus.textContent=msg; }
  function todayUTC(){ var d=new Date(); return d.getUTCFullYear()+'-'+String(d.getUTCMonth()+1).padStart(2,'0')+'-'+String(d.getUTCDate()).padStart(2,'0'); }
  function fetchToday(){
    return fetch('https://v3.football.api-sports.io/fixtures?date='+todayUTC(),{headers:{'x-apisports-key':key}})
      .then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); })
      .then(function(j){
        if(j.errors && (j.errors.token||j.errors.key)) throw new Error('Bad key');
        var resp=j.response||[];
        return resp.filter(function(f){ return f.league.id===1 || /world cup/i.test(f.league.name); });
      });
  }
  function settleLeg(leg,fix){
    var st=fix.fixture.status.short, h=fix.goals.home, a=fix.goals.away;
    if(st==='NS') return {state:'pending'};
    if(st==='FT'||st==='AET'||st==='PEN'){
      if(leg.type==='match_result'){
        var won=(leg.pick==='home'&&h>a)||(leg.pick==='away'&&a>h)||(leg.pick==='draw'&&h===a);
        return {state:won?'win':'lose',score:h+'-'+a};
      }
      if(leg.type==='over25'){ var over=(h+a)>2; return {state:(leg.pick==='over'?over?'win':'lose':over?'lose':'win'),score:h+'-'+a}; }
    }
    return {state:'live',score:(h==null?'?':h)+'-'+(a==null?'?':a),elapsed:fix.fixture.status.elapsed};
  }
  function renderLive(fixes){
    if(!fixes.length){ $list.innerHTML='<div class="live-empty">No World Cup fixtures today (UTC).</div>'; $meta.textContent='0 fixtures'; updateLegBadges([]); return; }
    var seen={};
    $list.innerHTML = fixes.map(function(f){
      var k=(f.teams.home.name+'|'+f.teams.away.name).toLowerCase();
      if(seen[k]) return ''; seen[k]=true;
      var st=f.fixture.status.short;
      var badge='<span class="badge '+st.toLowerCase()+'">'+st+'</span>';
      var clock=f.fixture.status.elapsed?f.fixture.status.elapsed+"'":'';
      var score=(f.goals.home==null?'-':f.goals.home)+' : '+(f.goals.away==null?'-':f.goals.away);
      var isLive=['1H','2H','HT','ET','P','BT'].indexOf(st)>=0;
      return '<div class="live-match"><div><span class="team home">'+f.teams.home.name+'</span> <span class="team away">'+f.teams.away.name+'</span></div>'
        +'<div class="'+(isLive?'score live':'score')+'">'+score+'</div>'
        +'<div class="clock">'+clock+'</div>'
        +'<div class="settle">'+badge+'</div></div>';
    }).join('');
    $meta.textContent=fixes.length+' World Cup fixtures · '+todayUTC()+' UTC';
    updateLegBadges(fixes);
  }
  function updateLegBadges(fixes){
    document.querySelectorAll('#legs tbody tr').forEach(function(tr){
      var cell=tr.querySelector('td.team'); if(!cell) return;
      var sub=cell.querySelector('.subtle'); var subTxt=sub?sub.textContent:'';
      var old=cell.querySelector('.leg-live'); if(old) old.remove();
      var visible=cell.textContent, match=null;
      for(var i=0;i<fixes.length;i++){
        var f=fixes[i];
        if(visible.indexOf(f.teams.home.name)>=0 && visible.indexOf(f.teams.away.name)>=0){ match=f; break; }
        if(visible.indexOf(f.teams.home.name)>=0 || visible.indexOf(f.teams.away.name)>=0){ match=f; break; }
      }
      if(!match) return;
      var leg={type:subTxt.indexOf('over25')>=0?'over25':subTxt.indexOf('player_goal')>=0?'player_goal':'match_result',pick:subTxt.indexOf('over25')>=0?'over':'home'};
      var r=settleLeg(leg,match);
      var b=document.createElement('span'); b.className='leg-live '+r.state;
      b.textContent=r.state==='win'?'✓ won':r.state==='lose'?'✗ lost':r.state==='live'?'⏱ '+r.score+' '+(r.elapsed||'')+"'":'—';
      cell.appendChild(b);
    });
  }
  function refreshLive(){
    if(!key){ setLS('err','No key'); return; }
    fetchToday().then(function(f){ renderLive(f); setLS('ok','Live · '+f.length+' matches'); })
      .catch(function(e){ setLS('err',e.message); $list.innerHTML='<div class="live-empty">'+e.message+'<br><small>Check key at <a href="https://www.api-football.com/" target="_blank" style="color:var(--market)">api-football.com</a></small></div>'; });
  }
  /* Polymarket */
  var BET_TEAMS={Argentina:1,Austria:1,France:1,Iraq:1,Norway:1,Senegal:1,Mexico:1,Germany:1,Australia:1,Jordan:1,England:1,Spain:1};
  function fetchEvent(){
    return fetch('https://gamma-api.polymarket.com/events?slug=world-cup-winner&closed=false')
      .then(function(r){ return r.json(); })
      .then(function(j){ return Array.isArray(j)&&j.length?j[0]:null; });
  }
  function fetchHist(tok){
    return fetch('https://clob.polymarket.com/prices-history?market='+encodeURIComponent(tok)+'&interval=1d&fidelity=60')
      .then(function(r){ return r.ok?r.json():null; })
      .then(function(j){ return (j&&j.history)||[]; })
      .catch(function(){ return []; });
  }
  function pct(x){ return (x*100).toFixed(1)+'%'; }
  function fmtVol(v){ if(!v||v<1) return '—'; if(v>=1e6) return '$'+(v/1e6).toFixed(1)+'M'; if(v>=1e3) return '$'+(v/1e3).toFixed(0)+'K'; return '$'+Math.round(v); }
  function renderPoly(ev){
    if(!ev){ setPS('err','No event'); return; }
    var markets=(ev.markets||[]).filter(function(m){ return /win the 2026/i.test(m.question||''); });
    var byTeam={};
    markets.forEach(function(m){ for(var t in BET_TEAMS){ if((m.question||'').indexOf(t)>=0){ byTeam[t]=m; break; } } });
    var teams=Object.keys(byTeam).sort(function(a,b){ return (byTeam[b].bestAsk||0)-(byTeam[a].bestAsk||0); });
    if(!teams.length){ $plist.innerHTML='<div class="live-empty">No markets</div>'; return; }
    $plist.innerHTML = teams.map(function(t){
      var m=byTeam[t], p=m.bestAsk!=null?m.bestAsk:(m.lastTradePrice||0);
      return '<div class="poly-row" data-team="'+t+'"><div class="pteam">'+t+'</div><div class="pprice">'+(p?pct(p):'—')+'</div><div class="pdelta flat">…</div><div class="pvol">'+fmtVol(m.volumeNum||m.volume)+'</div></div>';
    }).join('');
    $pmeta.textContent=markets.length+' outright markets · ranked by YES price';
    setPS('ok','Live');
    teams.forEach(function(t){
      var m=byTeam[t], toks=m.clobTokenIds||[];
      if(!toks.length) return;
      fetchHist(toks[0]).then(function(h){
        if(!h||h.length<2) return;
        var cur=m.bestAsk!=null?m.bestAsk:(m.lastTradePrice||0);
        var d=cur-h[0].p, pp=(d*100).toFixed(1);
        var $row=$plist.querySelector('[data-team="'+t+'"]'); if(!$row) return;
        var $d=$row.querySelector('.pdelta');
        if(Math.abs(d)>=0.005){
          $d.textContent=(d>0?'▲ +':'▼ -')+pp+'pp';
          $d.className='pdelta '+(d>0?'up':'down');
          if(Math.abs(d)>=0.01) $row.classList.add('hot');
        } else { $d.textContent='±0.0pp'; $d.className='pdelta flat'; }
      });
    });
  }
  function refreshPoly(){
    setPS('warn','Loading…');
    fetchEvent().then(function(ev){ renderPoly(ev); })
      .catch(function(e){ setPS('err',e.message); $plist.innerHTML='<div class="live-empty">'+e.message+'</div>'; });
  }
  var $lbtn=document.getElementById('live-refresh');
  var $pbtn=document.getElementById('poly-refresh');
  if($lbtn) $lbtn.addEventListener('click',function(){ setLS('warn','Loading\u2026'); refreshLive(); });
  if($pbtn) $pbtn.addEventListener('click',function(){ setPS('warn','Loading\u2026'); refreshPoly(); });
  setLS('warn','Press Refresh to load');
  setPS('warn','Press Refresh to load');
})();
</script>
"""


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

/* tracker page (per-leg table, hero stats, P&L histogram) */
.hero-stats{display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:14px; margin-top:18px}
.hero-stats > div{background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:12px 14px}
.hero-stats small{font-size:10.5px; text-transform:uppercase; color:var(--muted); letter-spacing:.07em}
.hero-stats .num{font-family:"Spline Sans Mono"; font-weight:600; font-size:18px; margin-top:4px; color:var(--ink)}
.legs-table{font-size:14px; width:100%}
.legs-table th,.legs-table td{padding:10px 12px; border-bottom:1px solid var(--line2); text-align:left}
.legs-table th{font-size:11px; text-transform:uppercase; color:var(--muted); letter-spacing:.07em}
.legs-table th.num,.legs-table td.num{text-align:right; font-family:"Spline Sans Mono"}
.legs-table td.team b{font-weight:600}
.legs-table td.model{color:var(--accent)} .legs-table td.market{color:var(--market)}
.legs-table td.edge.pos{color:var(--pos)} .legs-table td.edge.neg{color:var(--neg)}
.legs-table td.edge.zero{color:var(--muted)}
.histogram{display:flex; align-items:flex-end; gap:2px; height:140px; padding:14px;
  background:var(--panel); border:1px solid var(--line); border-radius:14px;
  margin-top:18px; overflow:hidden}
.bar-cell{flex:1; min-width:6px; background:var(--muted); border-radius:2px 2px 0 0;
  height:var(--h,0%); transition:background .3s}
.bar-cell.bar-pos{background:var(--accent)} .bar-cell.bar-neg{background:var(--neg); opacity:.55}
.hist-axis{display:flex; justify-content:space-between; margin-top:6px;
  font-family:"Spline Sans Mono"; font-size:11px; color:var(--muted)}
.hist-axis span:nth-child(2){color:var(--ink)}
.empty{color:var(--muted); padding:18px 0; font-style:italic}

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
  .hero-stats{grid-template-columns:repeat(2,minmax(0,1fr))}
  .legs-table{font-size:12.5px}
  .legs-table th,.legs-table td{padding:8px 8px}
  .histogram{height:100px}
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
{{EXTRA}}
</body>
</html>"""
