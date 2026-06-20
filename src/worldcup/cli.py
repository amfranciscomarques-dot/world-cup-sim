"""Command-line interface for the World Cup simulator.

    worldcup teams                      list the 48 teams by group
    worldcup squad "Brazil"             show a squad and the auto-picked best XI
    worldcup match "Brazil" "Spain"     simulate one match (optionally with XIs)
    worldcup match "Brazil" "Spain" -n 5000   Monte Carlo win/draw/loss odds for one fixture
    worldcup tournament                 simulate one full tournament
    worldcup odds -n 2000               Monte Carlo title / stage probabilities
    worldcup bets --event world-cup-winner    compare our odds to Polymarket + simulate value bets
    worldcup games                       compare our match odds to Polymarket per-game markets
    worldcup dashboard                   model-vs-market scoreboard over all finished games
    worldcup html                        write a self-contained HTML dashboard and open it
    worldcup tui                         interactive, arrow-key terminal UI
    worldcup web                         interactive browser UI (same features as the TUI)

Everything the CLI does is also available as a Python API; lineup and per-team
"extras" overrides are richer there (see the README).
"""

from __future__ import annotations

import argparse
import random
import sys
from typing import Optional

from .data_loader import load_results, load_world_cup
from .engine import KNOCKOUT_STAGES, MatchSimulator
from .models import Lineup, Team
from .tournament import TournamentSimulator


def _resolve_team(teams: dict[str, Team], query: str) -> Team:
    if query in teams:
        return teams[query]
    matches = [t for n, t in teams.items() if n.lower() == query.lower()]
    if not matches:
        matches = [t for n, t in teams.items() if query.lower() in n.lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        sys.exit(f"error: no team matching {query!r}. Try `worldcup teams`.")
    sys.exit(f"error: {query!r} is ambiguous: {', '.join(t.name for t in matches)}")


def _build_lineup(team: Team, names_csv: Optional[str]) -> Optional[Lineup]:
    if not names_csv:
        return None
    players = []
    for raw in names_csv.split(","):
        name = raw.strip()
        if not name:
            continue
        try:
            players.append(team.player(name))
        except KeyError:
            sys.exit(f"error: {name!r} not in {team.name} squad. Try `worldcup squad \"{team.name}\"`.")
    return Lineup(team=team.name, players=players)


def cmd_teams(args: argparse.Namespace) -> None:
    teams, tournament = load_world_cup()
    for letter, names in tournament.groups.items():
        print(f"Group {letter}: " + ", ".join(f"{n} ({teams[n].rating:.0f})" for n in names))


def cmd_squad(args: argparse.Namespace) -> None:
    from .factors.builtin import club_clusters, team_intangibles
    from .fm_rating import group_means

    teams, _ = load_world_cup()
    team = _resolve_team(teams, args.team)
    print(f"{team.name}  (base rating {team.rating:.0f})")
    if team.coach:
        bias = team.coach.get("bias", 0.0)
        tilt = "attacking" if bias > 0.05 else ("defensive" if bias < -0.05 else "balanced")
        print(f"  Coach: skill {team.coach.get('skill', 0):+.1f}, {tilt} (bias {bias:+.2f})")
    if team.condition < 1.0:
        hit = (team.condition - 1.0) * 12.0   # ConditionFactor.scale
        note = f" — {team.injuries}" if team.injuries else ""
        print(f"  Condition: {team.condition:.2f} ({hit:+.1f} rating){note}")
    for pos in ("GK", "DEF", "MID", "FWD"):
        line = [p for p in team.squad if p.pos == pos]
        if line:
            print(f"  {pos}:")
            for p in sorted(line, key=lambda p: p.rating, reverse=True):
                club = f"  {p.club}" if p.club else ""
                age = f"  {p.age}y" if p.age else ""
                if p.attributes:
                    g = group_means(p.pos, p.attributes)
                    print(f"     {p.name:<26}{p.rating:>5.1f}{age:>5}   "
                          f"M{g['mental']:>4.1f} F{g['physical']:>4.1f} T{g['technical']:>4.1f}{club}")
                else:
                    print(f"     {p.name:<26}{p.rating:>5.1f}{age:>5}{club}")
    best = team.best_lineup(args.size)
    print(f"  Best XI ({len(best.players)}): " + ", ".join(p.name for p in best.players))
    clusters = club_clusters(best.players)
    if clusters:
        cores = "; ".join(f"{c}: {', '.join(n)}" for c, n in clusters.items())
        print(f"  Chemistry cores: {cores}")
    mean, sigma = team_intangibles(best.players)
    print(f"  Intangibles (best XI): mean {mean:+.2f}, swing ±{sigma:.2f} "
          f"(veterans/youngsters add expectation + uncertainty)")


def _print_suggested_bet(odds, home, away, knockout: bool) -> None:
    """Turn Monte Carlo probabilities into a bet you can place on a sportsbook.

    Each market's fair decimal odds are ``1 / probability``; a book price *above*
    that fair number is positive expected value by our model. We headline the
    selection the model is most confident in (the safest single back) and list
    fair prices across the common markets so you can shop for value.
    """
    verb = "to advance" if knockout else "to win"
    # (market label, selection, model probability)
    cands = [
        ("Match result", f"{home.name} {verb}", odds.home_win),
        ("Match result", f"{away.name} {verb}", odds.away_win),
        ("Total goals", "Over 2.5 goals", odds.over25),
        ("Total goals", "Under 2.5 goals", 1.0 - odds.over25),
        ("Both teams to score", "Yes", odds.btts),
        ("Both teams to score", "No", 1.0 - odds.btts),
    ]
    if not knockout:
        cands.append(("Match result", "Draw", odds.draw))
    if odds.scorelines:
        (hg, ag), p = odds.scorelines[0]
        cands.append(("Correct score", f"{home.name} {hg}-{ag} {away.name}", p))

    def fair(p: float) -> float:
        return 1.0 / p if p > 0 else float("inf")

    pick = max(cands, key=lambda c: c[2])
    print("\n  suggested bet (place if the site's odds beat the fair price):")
    label = f"{pick[1]} [{pick[0]}]"
    print(f"     >> {label}  -  model {pick[2]:.1%}, fair odds {fair(pick[2]):.2f}")
    print("\n  fair odds across markets:")
    width = max(len(sel) for _, sel, _ in cands)
    for market, sel, p in cands:
        print(f"     {sel:<{width}}  {p:>6.1%}  fair {fair(p):>5.2f}  [{market}]")


def cmd_match(args: argparse.Namespace) -> None:
    teams, _ = load_world_cup()
    home = _resolve_team(teams, args.home)
    away = _resolve_team(teams, args.away)
    rng = random.Random(args.seed)
    sim = MatchSimulator(rng=rng)
    # Explicit --home-xi/--away-xi wins; otherwise fall back to any saved XI
    # in data/lineups_2026.json (set in the TUI lineup editor), then best XI.
    from .lineup_store import lineup_for
    home_lineup = _build_lineup(home, args.home_xi) or lineup_for(home)
    away_lineup = _build_lineup(away, args.away_xi) or lineup_for(away)

    # In-tournament SofaScore form for each side (no-op without a snapshot or
    # with --no-sofa), applied to this hypothetical fixture like any future game.
    sofa = _sofa_extras(args)
    home_extras = sofa.get(home.name)
    away_extras = sofa.get(away.name)

    if args.iterations > 1:
        odds = sim.monte_carlo(
            home, away, args.iterations,
            stage=args.stage,
            neutral=not args.home_advantage,
            home_lineup=home_lineup,
            away_lineup=away_lineup,
            home_extras=home_extras,
            away_extras=away_extras,
        )
        knockout = args.stage in KNOCKOUT_STAGES
        verb = "advances" if knockout else "win"
        rows = [(f"{home.name} {verb}", odds.home_win)]
        if not knockout:
            rows.append(("Draw", odds.draw))
        rows.append((f"{away.name} {verb}", odds.away_win))
        width = max(len(label) for label, _ in rows)
        print(f"{home.name} vs {away.name} over {odds.iterations} simulations ({args.stage})\n")
        for label, prob in rows:
            print(f"  {label:<{width}}  {prob:>6.1%}")
        print(f"\n  avg goals: {home.name} {odds.avg_home_goals:.2f} - {odds.avg_away_goals:.2f} {away.name}")
        print(f"  avg xG:    {home.name} {odds.avg_home_xg:.2f} - {odds.avg_away_xg:.2f} {away.name}")
        print("\n  most common scorelines:")
        for (hg, ag), prob in odds.scorelines:
            print(f"     {hg}-{ag}  {prob:>6.1%}")
        _print_suggested_bet(odds, home, away, knockout)
        return

    meta = {"temperature": args.temp} if args.temp is not None else None
    if args.no_live:
        result = sim.simulate(
            home, away,
            stage=args.stage,
            neutral=not args.home_advantage,
            home_lineup=home_lineup,
            away_lineup=away_lineup,
            home_extras=home_extras,
            away_extras=away_extras,
            meta=meta,
        )
    else:
        result = sim.simulate_live(
            home, away,
            stage=args.stage,
            neutral=not args.home_advantage,
            home_lineup=home_lineup,
            away_lineup=away_lineup,
            home_extras=home_extras,
            away_extras=away_extras,
            meta=meta,
        )
    print(result.score_str())
    print(f"  expected goals: {home.name} {result.home_xg:.2f} - {result.away_xg:.2f} {away.name}")

    if not args.no_live:
        def show_subs(team_name: str, subs: list) -> None:
            if subs:
                print(f"  {team_name} subs:")
                for minute, off, on in subs:
                    print(f"     {minute}'  {off}  ->  {on}")
        show_subs(home.name, result.home_subs)
        show_subs(away.name, result.away_subs)
        if result.cooling_breaks:
            print(f"  cooling breaks: {result.cooling_breaks} (hot conditions, {args.temp:.0f}C)")


def _played_results(args: argparse.Namespace) -> list:
    """Load already-played fixtures unless ``--fresh`` was passed."""
    if getattr(args, "fresh", False):
        return []
    return load_results()


def _sofa_extras(args: argparse.Namespace) -> dict:
    """Per-team SofaScore form to feed future games.

    Suppressed by ``--no-sofa`` and by ``--fresh`` (simulating from scratch means
    ignoring the tournament so far, form included). ``{}`` when there is no
    snapshot, which makes the SofaScore factor a no-op."""
    from .sofascore_store import load_form_extras

    if getattr(args, "fresh", False) or getattr(args, "no_sofa", False):
        return {}
    return load_form_extras()


def cmd_tournament(args: argparse.Namespace) -> None:
    teams, tournament = load_world_cup()
    results = _played_results(args)
    rng = random.Random(args.seed)
    sim = TournamentSimulator(teams, tournament, rng=rng, results=results)
    sofa = _sofa_extras(args)
    outcome = sim.run_once(extras=sofa)

    if results:
        print(f"(seeded with {len(results)} games already played; "
              f"only remaining fixtures are simulated"
              + (f"; SofaScore form applied to {len(sofa)} teams" if sofa else "")
              + ")\n")

    print("=== GROUP STAGE ===")
    for g in outcome.groups:
        print(f"\nGroup {g.letter}")
        print(f"  {'Team':<22}{'Pld':>4}{'W':>3}{'D':>3}{'L':>3}{'GF':>4}{'GA':>4}{'GD':>4}{'Pts':>5}")
        for s in g.standings:
            print(f"  {s.team:<22}{s.played:>4}{s.won:>3}{s.drawn:>3}{s.lost:>3}{s.gf:>4}{s.ga:>4}{s.gd:>+4}{s.pts:>5}")

    print("\n=== KNOCKOUT ===")
    for rnd in outcome.knockout.rounds:
        stage = rnd[0].stage
        print(f"\n{stage}")
        for m in rnd:
            print(f"  {m.score_str()}")
    print(f"\nCHAMPION: {outcome.champion}")


def cmd_tui(args: argparse.Namespace) -> None:
    from .tui import run

    run()


def cmd_web(args: argparse.Namespace) -> None:
    from .webapp import serve

    serve(host=args.host, port=args.port, open_browser=not args.no_open)


def cmd_odds(args: argparse.Namespace) -> None:
    teams, tournament = load_world_cup()
    results = _played_results(args)
    rng = random.Random(args.seed)
    sim = TournamentSimulator(teams, tournament, rng=rng, results=results)
    report = sim.monte_carlo(args.iterations, extras=_sofa_extras(args))

    seeded = (f"  ({len(results)} games already played, only remaining fixtures simulated)"
              if results else "")
    print(f"Title odds over {report.iterations} simulations{seeded}\n")
    print(f"  {'Team':<22}{'R16':>7}{'QF':>7}{'SF':>7}{'Final':>7}{'Win':>7}")
    for name, _ in report.title_odds()[: args.top]:
        r = report.reach[name]
        print(f"  {name:<22}{r['R16']:>7.1%}{r['QF']:>7.1%}{r['SF']:>7.1%}{r['Final']:>7.1%}{r['Champion']:>7.1%}")


def cmd_bets(args: argparse.Namespace) -> None:
    from . import polymarket
    from .betting import run_betting

    if args.search:
        try:
            results = polymarket.search_events(args.search)
        except polymarket.PolymarketError as exc:
            sys.exit(f"error: {exc}")
        if not results:
            sys.exit(f"no Polymarket events matching {args.search!r}")
        print(f"Polymarket events matching {args.search!r}:\n")
        for slug, title in results:
            print(f"  {slug:<46} {title}")
        print("\nUse: worldcup bets --event <slug>")
        return

    teams, tournament = load_world_cup()
    try:
        event = polymarket.fetch_event(args.event, teams)
    except polymarket.PolymarketError as exc:
        sys.exit(f"error: {exc}\nTry `worldcup bets --search \"world cup\"` to find a valid slug.")

    if not event.matched:
        sys.exit(f"error: none of {event.title!r}'s teams map to our squads "
                 f"(unmatched: {', '.join(event.unmatched) or 'n/a'})")

    rng = random.Random(args.seed)
    sim = TournamentSimulator(teams, tournament, rng=rng, results=_played_results(args))
    report = run_betting(
        sim, event, args.iterations,
        bankroll=args.bankroll, kelly_mult=args.kelly, min_edge=args.min_edge,
    )

    print(f"{report.event_title}  —  model vs Polymarket over {report.iterations} simulations\n")
    print(f"  {'Team':<20}{'Market':>9}{'Model':>9}{'Edge':>9}{'EV/$1':>9}")
    for c in report.comparisons[: args.top]:
        print(f"  {c.team:<20}{c.market_prob:>9.1%}{c.model_prob:>9.1%}{c.edge:>+9.1%}{c.ev:>+9.1%}")
    if report.unmatched:
        print(f"\n  (skipped {len(report.unmatched)} unmapped market entries: "
              f"{', '.join(report.unmatched)})")

    print(f"\nValue bets — half-Kelly×{args.kelly:g}, ${args.bankroll:g} bankroll, "
          f"min edge {args.min_edge:.0%}:")
    if not report.bets:
        print("  (no positive-edge bets clear the threshold)")
    else:
        for b in report.bets:
            print(f"  {b.team:<20} stake ${b.stake:>7.2f} @ {b.price:.3f}  "
                  f"-> {b.shares:.1f} shares  edge {b.edge:+.1%}  EV {b.ev:+.0%}")
        print(f"\n  staked ${report.total_staked:.2f} of ${args.bankroll:g}  ·  "
              f"mean P&L ${report.mean_pnl:+.2f}  ·  ROI {report.roi:+.1%}  ·  "
              f"green {report.win_rate:.0%} of runs")
        print(f"  P&L range: p5 ${report.percentile(0.05):+.2f}  "
              f"median ${report.percentile(0.50):+.2f}  p95 ${report.percentile(0.95):+.2f}")
        print("  note: bets are settled by the same model that priced them — this shows "
              "edge variance,\n        not an independent backtest.")


def _fmt_triple(t) -> str:
    return f"{t[0]:>6.0%}{t[1]:>6.0%}{t[2]:>6.0%}" if t else f"{'-':>6}{'-':>6}{'-':>6}"


def cmd_games(args: argparse.Namespace) -> None:
    from . import polymarket
    from .games import accuracy_summary, compare_game, compare_games, find_game

    teams, tournament = load_world_cup()
    hosts = set(tournament.host_team_names)
    rng = random.Random(args.seed)
    sim = MatchSimulator(rng=rng)

    print("fetching World Cup fixtures from Polymarket...", file=sys.stderr)
    try:
        games = polymarket.fetch_games(teams)
    except polymarket.PolymarketError as exc:
        sys.exit(f"error: {exc}")
    mapped = [g for g in games if g.mapped]
    if not mapped:
        sys.exit("error: no Polymarket World Cup fixtures could be matched to our squads")

    # Single-fixture detail.
    if args.game:
        game = find_game(mapped, args.game)
        if game is None:
            sys.exit(f"error: no fixture matching {args.game!r}. Try `worldcup games`.")
        if game.closed:
            print("fetching pre-kickoff market odds...", file=sys.stderr)
        comp = compare_game(sim, teams, game, args.iterations, host_names=hosts)
        rows = [(f"{game.home} win", 0), ("Draw", 1), (f"{game.away} win", 2)]
        width = max(len(label) for label, _ in rows)
        tag = "pre-kickoff market" if game.closed else "live market"
        print(f"{game.home} vs {game.away}   ({game.date}, {tag})\n")
        print(f"  {'':<{width}}{'Market':>9}{'Model':>9}{'Edge':>9}")
        for label, i in rows:
            mk = f"{comp.market[i]:>9.1%}" if comp.market else f"{'-':>9}"
            ed = f"{comp.edges[i]:>+9.1%}" if comp.edges else f"{'-':>9}"
            print(f"  {label:<{width}}{mk}{comp.model[i]:>9.1%}{ed}")
        if game.result:
            called = {"H": f"{game.home} win", "D": "draw", "A": f"{game.away} win"}[game.result]
            print(f"\n  actual: {game.home} {game.score} {game.away}  ->  {called}")
            print(f"  model called it: {'yes' if comp.model_pick == game.result else 'no'}"
                  f"   |   market called it: {'yes' if comp.market_pick == game.result else 'no'}")
        return

    show_played = args.played or not args.upcoming
    show_upcoming = args.upcoming or not args.played

    header = (f"  {'Fixture':<30}{'Mkt H/D/A':>18}{'Model H/D/A':>18}"
              f"{'Edge':>9}  Result")

    if show_upcoming:
        upcoming = [g for g in mapped if not g.closed][: args.top]
        comps = compare_games(sim, teams, upcoming, args.iterations, host_names=hosts,
                              extras=_sofa_extras(args))
        print(f"\nUPCOMING -- live market vs model over {args.iterations} sims\n")
        print(header)
        for c in comps:
            be = c.best_edge
            edge = f"{be[1]:>+7.1%} {be[0]}" if be else f"{'-':>9}"
            fixture = f"{c.game.home} vs {c.game.away}"
            print(f"  {fixture:<30}{_fmt_triple(c.market):>18}{_fmt_triple(c.model):>18}{edge}")

    if show_played:
        played = [g for g in mapped if g.closed]
        if args.top:
            played = played[-args.top:]
        print("fetching pre-kickoff market odds for played games...", file=sys.stderr)
        comps = compare_games(sim, teams, played, args.iterations, host_names=hosts)
        print("\nPLAYED -- pre-kickoff market vs model vs actual result\n")
        print(header)
        for c in comps:
            res = c.game.result
            mark = ""
            if res:
                mc = "M" if c.model_pick == res else " "
                kc = "K" if c.market_pick == res else " "
                mark = f"  {res} [{kc}{mc}]"
            fixture = f"{c.game.home} {c.game.score} {c.game.away}"
            print(f"  {fixture:<30}{_fmt_triple(c.market):>18}{_fmt_triple(c.model):>18}{'':>9}{mark}")
        s = accuracy_summary(comps)
        if s.n:
            print(f"\n  over {s.n} played games  -  picks correct: "
                  f"model {s.model_correct}/{s.n}, market {s.market_correct}/{s.n}")
            print(f"  Brier (lower=better): model {s.model_brier:.3f}, market {s.market_brier:.3f}"
                  f"   [{'model' if s.model_brier < s.market_brier else 'market'} sharper]")
            print("  legend: K = market's favourite was right, M = model's favourite was right")


def _bar(value: float, scale: float, width: int = 18) -> str:
    """A `[####----]` gauge of ``value`` against ``scale`` (clamped)."""
    filled = max(0, min(width, round(width * value / scale))) if scale > 0 else 0
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def cmd_dashboard(args: argparse.Namespace) -> None:
    from . import polymarket
    from .games import build_dashboard, compare_games

    teams, tournament = load_world_cup()
    hosts = set(tournament.host_team_names)
    sim = MatchSimulator(rng=random.Random(args.seed))

    print("fetching World Cup fixtures from Polymarket...", file=sys.stderr)
    try:
        games = polymarket.fetch_games(teams)
    except polymarket.PolymarketError as exc:
        sys.exit(f"error: {exc}")
    played = [g for g in games if g.mapped and g.closed]
    if not played:
        sys.exit("no finished World Cup fixtures yet -- check back after kickoff.")

    print("fetching pre-kickoff market odds...", file=sys.stderr)
    comps = compare_games(sim, teams, played, args.iterations, host_names=hosts)
    dash = build_dashboard(comps)
    s = dash.summary
    if not s.n:
        sys.exit("no finished fixtures could be graded (missing market history).")

    mix = dash.result_mix
    print("\n" + "=" * 58)
    print("  MODEL vs MARKET -- PERFORMANCE DASHBOARD")
    print("=" * 58)
    print(f"  finished games graded: {s.n}"
          f"   (results: {mix['H']} home / {mix['D']} draw / {mix['A']} away)")
    print(f"  model odds: {args.iterations} sims/fixture   |   market: pre-kickoff Polymarket\n")

    print(f"  {'':22}{'Model':>12}{'Market':>12}")
    print(f"  {'favourite correct':22}"
          f"{f'{s.model_correct}/{s.n} ({s.model_correct/s.n:.0%})':>12}"
          f"{f'{s.market_correct}/{s.n} ({s.market_correct/s.n:.0%})':>12}")
    print(f"  {'Brier (lower=better)':22}{s.model_brier:>12.3f}{s.market_brier:>12.3f}")
    print(f"  {'log-loss (lower=better)':22}{dash.model_logloss:>12.3f}{dash.market_logloss:>12.3f}")

    sharper = "model" if dash.model_sharper else "market"
    gap = abs(s.model_brier - s.market_brier)
    print(f"\n  Brier   model  {_bar(s.model_brier, 1.0)} {s.model_brier:.3f}")
    print(f"          market {_bar(s.market_brier, 1.0)} {s.market_brier:.3f}")
    print(f"          -> {sharper} sharper by {gap:.3f}\n")

    if dash.disagreements:
        lead = ("model" if dash.model_edge_wins > dash.market_edge_wins
                else "market" if dash.market_edge_wins > dash.model_edge_wins else "neither")
        print(f"  head-to-head on {dash.disagreements} game(s) where they "
              f"picked different favourites:")
        print(f"    model's pick won {dash.model_edge_wins}   |   "
              f"market's pick won {dash.market_edge_wins}   ->  {lead} has the edge so far\n")

    print(f"  {'Fixture':<30}{'Res':>4}{'  Model':>8}{'Market':>8}   Verdict")
    for gr in dash.games:
        c = gr.comparison
        fixture = f"{c.game.home} {c.game.score} {c.game.away}"
        if gr.model_right and gr.market_right:
            verdict = "both right"
        elif gr.model_right:
            verdict = "model only"
        elif gr.market_right:
            verdict = "market only"
        else:
            verdict = "both wrong"
        if gr.disagreed:
            verdict += " *"
        print(f"  {fixture:<30}{gr.result:>4}{c.model_pick:>8}{c.market_pick:>8}   {verdict}")
    if any(gr.disagreed for gr in dash.games):
        print("  (* = model and market backed different favourites)")


def cmd_html(args: argparse.Namespace) -> None:
    import webbrowser
    from pathlib import Path

    from . import polymarket
    from .html import render_dashboard
    from .odds_store import load_odds
    from .report import build_report

    teams, tournament = load_world_cup()
    results = _played_results(args)

    # Market data: the offline snapshot by default; --live refetches Polymarket.
    odds = None
    if args.live:
        print("fetching live odds from Polymarket...", file=sys.stderr)
        try:
            odds = _live_snapshot(teams, tournament)
        except polymarket.PolymarketError as exc:
            print(f"warning: live fetch failed ({exc}); falling back to snapshot", file=sys.stderr)
    if odds is None:
        odds = load_odds()
    if odds is None and not args.live:
        print("note: no odds snapshot found — run `python scripts/update_odds.py` "
              "for market columns (rendering model-only).", file=sys.stderr)

    def progress(stage: str, done: int, total: int) -> None:
        pct = f"{done}/{total}" if total else ""
        print(f"\r  {stage:<16} {pct:>10}   ", end="", file=sys.stderr, flush=True)

    report = build_report(
        teams, tournament, results, odds,
        title_iters=args.iterations, game_iters=args.game_iterations,
        max_upcoming=args.upcoming, top_title=args.top, seed=args.seed,
        sofa_extras=_sofa_extras(args),
        progress=progress,
    )
    print("", file=sys.stderr)

    out = Path(args.output)
    out.write_text(render_dashboard(report), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())


def _live_snapshot(teams, tournament) -> "object":
    """Build an OddsSnapshot from live Polymarket data (no file write)."""
    from datetime import date

    from . import polymarket
    from .odds_store import GameOdds, OddsSnapshot

    def event_map(slug):
        try:
            return {ln.team: ln.yes_price for ln in polymarket.fetch_event(slug, teams).matched}
        except polymarket.PolymarketError:
            return {}

    games = polymarket.fetch_games(teams)
    rows = []
    for g in games:
        if not g.mapped:
            continue
        pre = None
        if g.closed:
            try:
                pre = polymarket.pregame_odds(g)
            except polymarket.PolymarketError:
                pre = None
        rows.append(GameOdds(date=g.date, home=g.home, away=g.away, closed=g.closed,
                             score=g.score, live=g.live_probs, pregame=pre))
    return OddsSnapshot(
        fetched=date.today().isoformat(),
        winner=event_map("world-cup-winner"),
        reach={"R16": event_map("world-cup-nation-to-reach-round-of-16"),
               "QF": event_map("world-cup-nation-to-reach-quarterfinals")},
        group_winners={letter: event_map(f"world-cup-group-{letter.lower()}-winner")
                       for letter in tournament.groups},
        games=rows,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="worldcup", description="2026 FIFA World Cup simulator")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("teams", help="list teams by group")
    p.set_defaults(func=cmd_teams)

    p = sub.add_parser("squad", help="show a team's squad and best XI")
    p.add_argument("team")
    p.add_argument("--size", type=int, default=11, help="XI size (default 11)")
    p.set_defaults(func=cmd_squad)

    p = sub.add_parser("match", help="simulate a single match")
    p.add_argument("home")
    p.add_argument("away")
    p.add_argument("-n", "--iterations", type=int, default=1,
                   help="replay the fixture N times for Monte Carlo win/draw/loss odds (default 1 = one game)")
    p.add_argument("--stage", default="group", help="group/R32/R16/QF/SF/F (knockout forces a winner)")
    p.add_argument("--home-advantage", action="store_true", help="treat home team as playing at home")
    p.add_argument("--home-xi", help="comma-separated player names to field for the home team")
    p.add_argument("--away-xi", help="comma-separated player names to field for the away team")
    p.add_argument("--temp", type=float, default=None,
                   help="kickoff air temperature in C; >=30 triggers hydration/cooling breaks")
    p.add_argument("--no-live", action="store_true",
                   help="use the fast aggregate model (no in-match fatigue/subs/hydration)")
    p.add_argument("--no-sofa", action="store_true",
                   help="ignore in-tournament SofaScore form when rating the teams")
    p.add_argument("--seed", type=int, default=None, help="RNG seed for reproducibility")
    p.set_defaults(func=cmd_match)

    p = sub.add_parser("tournament", help="simulate one full tournament")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--fresh", action="store_true",
                   help="ignore already-played results and simulate from scratch")
    p.add_argument("--no-sofa", action="store_true",
                   help="ignore in-tournament SofaScore form when rating the teams")
    p.set_defaults(func=cmd_tournament)

    p = sub.add_parser("tui", help="interactive terminal UI (arrow keys)")
    p.set_defaults(func=cmd_tui)

    p = sub.add_parser("web", help="interactive web UI in the browser (same features as the TUI)")
    p.add_argument("--port", type=int, default=8000, help="port to serve on (default 8000)")
    p.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    p.add_argument("--no-open", action="store_true", help="don't open a browser automatically")
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("odds", help="Monte Carlo stage/title probabilities")
    p.add_argument("-n", "--iterations", type=int, default=1000)
    p.add_argument("--top", type=int, default=16, help="how many teams to show")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--fresh", action="store_true",
                   help="ignore already-played results and simulate from scratch")
    p.add_argument("--no-sofa", action="store_true",
                   help="ignore in-tournament SofaScore form when rating the teams")
    p.set_defaults(func=cmd_odds)

    p = sub.add_parser("bets", help="compare our odds to Polymarket and simulate value bets")
    p.add_argument("--event", default="world-cup-winner",
                   help="Polymarket event slug (default world-cup-winner)")
    p.add_argument("--search", help="instead of betting, list event slugs matching this text")
    p.add_argument("-n", "--iterations", type=int, default=2000,
                   help="Monte Carlo runs for our model probabilities (default 2000)")
    p.add_argument("--bankroll", type=float, default=100.0, help="bankroll for staking (default 100)")
    p.add_argument("--kelly", type=float, default=0.5,
                   help="Kelly fraction multiplier; 0.5 = half Kelly (default)")
    p.add_argument("--min-edge", type=float, default=0.02,
                   help="minimum model-minus-market edge to bet (default 0.02)")
    p.add_argument("--top", type=int, default=20, help="how many teams to show in the table")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--fresh", action="store_true",
                   help="ignore already-played results and simulate from scratch")
    p.set_defaults(func=cmd_bets)

    p = sub.add_parser("games", help="compare our match odds to Polymarket's per-game markets")
    p.add_argument("--game", help="show one fixture in detail (slug or \"home vs away\")")
    p.add_argument("--played", action="store_true", help="only played games (vs pre-kickoff odds + result)")
    p.add_argument("--upcoming", action="store_true", help="only upcoming games (vs live odds)")
    p.add_argument("-n", "--iterations", type=int, default=2000,
                   help="Monte Carlo runs per fixture for our model odds (default 2000)")
    p.add_argument("--top", type=int, default=20, help="max fixtures per section (default 20)")
    p.add_argument("--no-sofa", action="store_true",
                   help="ignore in-tournament SofaScore form for upcoming-game model odds")
    p.add_argument("--seed", type=int, default=None)
    p.set_defaults(func=cmd_games)

    p = sub.add_parser("dashboard", help="model-vs-market scoreboard over all finished games")
    p.add_argument("-n", "--iterations", type=int, default=2000,
                   help="Monte Carlo runs per fixture for our model odds (default 2000)")
    p.add_argument("--seed", type=int, default=None)
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("html", help="write a self-contained HTML dashboard (alternative to the TUI)")
    p.add_argument("-o", "--output", default="dashboard.html", help="output file (default dashboard.html)")
    p.add_argument("-n", "--iterations", type=int, default=2000,
                   help="tournament Monte Carlo runs for title/stage odds (default 2000)")
    p.add_argument("--game-iterations", type=int, default=1500,
                   help="Monte Carlo runs per fixture for match odds (default 1500)")
    p.add_argument("--upcoming", type=int, default=12, help="how many upcoming fixtures to show")
    p.add_argument("--top", type=int, default=16, help="how many teams in the title race table")
    p.add_argument("--live", action="store_true",
                   help="fetch fresh odds from Polymarket instead of the offline snapshot")
    p.add_argument("--no-open", action="store_true", help="write the file but don't open a browser")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--fresh", action="store_true",
                   help="ignore already-played results and simulate from scratch")
    p.add_argument("--no-sofa", action="store_true",
                   help="ignore in-tournament SofaScore form when rating the teams")
    p.set_defaults(func=cmd_html)

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
