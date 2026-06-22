"""Value-bet scanner for today's World Cup fixtures.

Runs the Poisson/Dixon-Coles model against Polymarket prices for every game on
today's date. From the model's full joint scoreline distribution it derives
hundreds of derivative markets (1X2, Over/Under multiple lines, BTTS, exact
score, Asian Handicap, Double Chance, Draw No Bet) and surfaces the
positive-edge bets ranked by edge size, with half-Kelly staking.

Polymarket only lists the 1X2 three-way market for each game (verified live),
so the 1X2 picks are the only ones priced vs. an actual exchange line. The
hundreds of derivative markets are computed from the model so the user can
price them against any sportsbook (Pinnacle, Bet365, etc.) themselves.

Usage:
    PYTHONPATH=src python scripts/value_bets_today.py
    PYTHONPATH=src python scripts/value_bets_today.py --date 2026-06-22 -n 5000
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# --- make the package importable whether run from project root or src ----
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from worldcup.data_loader import load_world_cup
from worldcup.engine.match import MatchSimulator
from worldcup.polymarket import fetch_games
from worldcup.sofascore_store import load_form_extras


# --- markets -------------------------------------------------------------


@dataclass
class MktRow:
    market: str        # human label
    selection: str     # bet selection
    prob: float        # model probability
    price: Optional[float] = None  # market decimal odds (None = no price)
    edge: Optional[float] = None    # model - market-implied (where priced)


def asian_handicap(home_goals: int, away_goals: int, line: float) -> str:
    """Return 'H' if home covers, 'A' if away covers, 'P' if push."""
    diff = home_goals - away_goals + line
    if diff > 0:
        return "H"
    if diff < 0:
        return "A"
    return "P"


def derive_markets(
    scores: Counter, total: int, home: str, away: str
) -> list[MktRow]:
    """Score a single scoreline distribution into every market we can derive."""
    rows: list[MktRow] = []

    # --- 1X2 -------------------------------------------------------------
    pH = sum(c for (h, a), c in scores.items() if h > a) / total
    pD = sum(c for (h, a), c in scores.items() if h == a) / total
    pA = sum(c for (h, a), c in scores.items() if h < a) / total
    rows += [
        MktRow("1X2", home, pH),
        MktRow("1X2", "Draw", pD),
        MktRow("1X2", away, pA),
    ]

    # --- Double Chance ---------------------------------------------------
    rows += [
        MktRow("Double Chance", f"{home} or Draw", pH + pD),
        MktRow("Double Chance", f"{away} or Draw", pA + pD),
        MktRow("Double Chance", f"{home} or {away}", pH + pA),
    ]

    # --- Draw No Bet -----------------------------------------------------
    rows += [
        MktRow("Draw No Bet", home, pH / (pH + pA)),
        MktRow("Draw No Bet", away, pA / (pH + pA)),
    ]

    # --- Over/Under (0.5, 1.5, 2.5, 3.5, 4.5) ----------------------------
    for line in (0.5, 1.5, 2.5, 3.5, 4.5):
        over = sum(c for (h, a), c in scores.items() if h + a > line) / total
        under = 1.0 - over
        rows += [
            MktRow(f"Total Goals O/U {line}", f"Over {line}", over),
            MktRow(f"Total Goals O/U {line}", f"Under {line}", under),
        ]

    # --- Team totals (home & away each over 0.5/1.5) ---------------------
    for side, name in (("H", home), ("A", away)):
        idx = 0 if side == "H" else 1
        for line in (0.5, 1.5, 2.5):
            over = sum(c for s, c in scores.items() if s[idx] > line) / total
            rows += [
                MktRow(f"{name} Goals O/U {line}", f"Over {line}", over),
                MktRow(f"{name} Goals O/U {line}", f"Under {line}", 1.0 - over),
            ]

    # --- BTTS ------------------------------------------------------------
    pBTTS_Y = sum(c for (h, a), c in scores.items() if h > 0 and a > 0) / total
    rows += [
        MktRow("Both Teams To Score", "Yes", pBTTS_Y),
        MktRow("Both Teams To Score", "No", 1.0 - pBTTS_Y),
    ]

    # --- BTTS + Over 2.5 combos ------------------------------------------
    pCombo = (
        sum(c for (h, a), c in scores.items() if h > 0 and a > 0 and h + a > 2)
        / total
    )
    rows += [
        MktRow("BTTS & Over 2.5", "Yes", pCombo),
        MktRow("BTTS & Over 2.5", "No", 1.0 - pCombo),
    ]

    # --- Asian Handicap (whole + half lines) -----------------------------
    for line in (-2.5, -1.5, -0.5, 0.5, 1.5, 2.5):
        pH = sum(
            c for (h, a), c in scores.items()
            if asian_handicap(h, a, line) == "H"
        ) / total
        pA = sum(
            c for (h, a), c in scores.items()
            if asian_handicap(h, a, line) == "A"
        ) / total
        rows += [
            MktRow(f"Asian Handicap {line:+g}", home, pH),
            MktRow(f"Asian Handicap {line:+g}", away, pA),
        ]

    # --- Exact score (top scorelines + any other) -----------------------
    # list every observed scoreline (usually 30-50 distinct ones)
    for (h, a), c in scores.most_common():
        rows.append(MktRow("Correct Score", f"{h}-{a}", c / total))

    return rows


# --- Kelly staking ------------------------------------------------------


def kelly(prob: float, decimal_odds: float) -> float:
    """Full-Kelly fraction (capped at 0 if no edge)."""
    if decimal_odds <= 1.0:
        return 0.0
    b = decimal_odds - 1.0
    q = 1.0 - prob
    f = (b * prob - q) / b
    return max(0.0, f)


def fair_odds(prob: float) -> float:
    return 1.0 / prob if prob > 0 else float("inf")


# --- main ---------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None,
                        help="ISO date YYYY-MM-DD (default: today UTC)")
    parser.add_argument("-n", "--iterations", type=int, default=3000,
                        help="Monte-Carlo iterations per match (default 3000)")
    parser.add_argument("--bankroll", type=float, default=100.0)
    parser.add_argument("--kelly", type=float, default=0.5,
                        help="Kelly multiplier (default 0.5 = half-Kelly)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-edge", type=float, default=0.02,
                        help="minimum edge (model - market) to surface")
    parser.add_argument("--out", type=Path, default=None,
                        help="write the ranked value bets JSON here")
    args = parser.parse_args()

    today = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"=== Value-bet scan for {today} ===")
    print(f"Iterations: {args.iterations}  Bankroll: ${args.bankroll:g}  "
          f"Kelly: {args.kelly:g}  Min edge: {args.min_edge:.0%}\n")

    teams, tournament = load_world_cup()
    sim = MatchSimulator(rng=__import__("random").Random(args.seed))
    extras = load_form_extras()

    games = fetch_games(teams, closed=False)
    today_games = [g for g in games if g.date == today and g.mapped]

    if not today_games:
        print(f"No upcoming mapped games found on {today}.")
        return

    print(f"Games found: {len(today_games)}\n")

    all_bets: list[dict] = []

    for g in today_games:
        home, away = teams[g.home], teams[g.away]
        host_names = set(tournament.host_team_names)
        home_host = g.home in host_names
        away_host = g.away in host_names

        # host advantage: only one of {home, away} can be a host
        if away_host and not home_host:
            mc = sim.monte_carlo(
                away, home, args.iterations, stage="group", neutral=False,
                home_extras=extras.get(away.name),
                away_extras=extras.get(home.name),
            )
            xg_h, xg_a = mc.avg_away_goals, mc.avg_home_goals
        else:
            mc = sim.monte_carlo(
                home, away, args.iterations, stage="group",
                neutral=not home_host,
                home_extras=extras.get(home.name),
                away_extras=extras.get(away.name),
            )
            xg_h, xg_a = mc.avg_home_goals, mc.avg_away_goals

        # rebuild a (h,a) -> count dict from the score grid
        # We don't have raw scores from mc, so re-simulate cheaply with a
        # parallel call that captures the full distribution.
        scores: Counter = Counter()
        for _ in range(args.iterations):
            r = sim.simulate(
                home, away, stage="group",
                neutral=not (home_host and not away_host),
                home_extras=extras.get(home.name),
                away_extras=extras.get(away.name),
            )
            scores[(r.home_goals, r.away_goals)] += 1

        # market prices from the GameMarket (de-vigged)
        market_raw = g.live_probs  # (H, D, A) Yes prices
        if market_raw is None or None in market_raw:
            print(f"  {g.title}: no live market prices — skipping")
            continue
        market_total = sum(market_raw)
        market_probs = tuple(p / market_total for p in market_raw)

        rows = derive_markets(scores, args.iterations, home.name, away.name)

        # attach market prices where applicable (1X2 only — that's all Poly offers)
        for row in rows:
            if row.market == "1X2":
                if row.selection == home.name:
                    row.price = fair_odds(market_probs[0])
                elif row.selection == "Draw":
                    row.price = fair_odds(market_probs[1])
                elif row.selection == away.name:
                    row.price = fair_odds(market_probs[2])
                if row.price is not None and row.price > 1.0:
                    mkt_implied = 1.0 / row.price
                    row.edge = row.prob - mkt_implied

        # print headline
        print(f"── {g.title} ({g.slug}) ──")
        print(f"   xG: {home.name} {xg_h:.2f}  {away.name} {xg_a:.2f}")
        print(f"   Model 1X2:  {home.name} {rows[0].prob:.1%}  "
              f"Draw {rows[1].prob:.1%}  {away.name} {rows[2].prob:.1%}")
        mH, mD, mA = market_probs
        print(f"   Mkt   1X2:  {home.name} {mH:.1%}  Draw {mD:.1%}  "
              f"{away.name} {mA:.1%}")
        edge_h = rows[0].prob - mH
        edge_d = rows[1].prob - mD
        edge_a = rows[2].prob - mA
        print(f"   Edge:       {home.name} {edge_h:+.1%}  Draw {edge_d:+.1%}  "
              f"{away.name} {edge_a:+.1%}\n")

        # --- top markets table ------------------------------------------
        # group by market, find top-2 model selections per market
        markets: dict[str, list[MktRow]] = {}
        for row in rows:
            markets.setdefault(row.market, []).append(row)
        print(f"   Top-2 per market ({len(markets)} markets):")
        # print compact table
        for mkt, mrows in markets.items():
            mrows.sort(key=lambda r: r.prob, reverse=True)
            for r in mrows[:2]:
                line = f"     {mkt:<28} {r.selection:<24} {r.prob:6.2%}"
                if r.price is not None and r.edge is not None:
                    line += f"   fair {r.price:5.2f}   edge {r.edge:+.1%}"
                print(line)
        print()

        # --- collect positive-edge value bets ---------------------------
        for row in rows:
            if row.edge is None or row.edge < args.min_edge:
                continue
            f_full = kelly(row.prob, row.price) if row.price else 0.0
            f = f_full * args.kelly
            stake = round(args.bankroll * f, 2)
            if stake <= 0:
                continue
            all_bets.append({
                "game": g.title,
                "slug": g.slug,
                "market": row.market,
                "selection": row.selection,
                "model_prob": round(row.prob, 4),
                "fair_odds": round(row.price, 3) if row.price else None,
                "edge": round(row.edge, 4),
                "kelly_full": round(f_full, 4),
                "kelly_stake": round(f * args.kelly / args.kelly, 4),
                "stake_$_per_100": round(stake, 2),
                "ev_per_$_stake": round(row.edge * row.price, 4)
                    if row.price else None,
            })

    # --- final ranked list ----------------------------------------------
    all_bets.sort(key=lambda b: b["edge"], reverse=True)

    # Summary stats
    priced_markets = [b for b in all_bets if b["edge"] is not None]
    if priced_markets:
        total = sum(b["stake_$_per_100"] for b in priced_markets)
        weighted_edge = (
            sum(b["edge"] * b["stake_$_per_100"] for b in priced_markets) / total
            if total > 0 else 0.0
        )

    print("=" * 72)
    print(f"VALUE BETS (min edge {args.min_edge:.1%}, {args.kelly:g}-Kelly "
          f"on ${args.bankroll:g})")
    print("=" * 72)
    if not all_bets:
        print(f"  No bets cleared the {args.min_edge:.1%} edge threshold.")
        print("  Polymarket only prices 1X2 per game; the derivative markets")
        print("  above are model-only — price them against your sportsbook.")
    else:
        for i, b in enumerate(all_bets, 1):
            ev = f"EV {b['ev_per_$_stake']:+.3f}" if b['ev_per_$_stake'] else ""
            print(f"  #{i:>2}  {b['market']:<28} {b['selection']:<24}  "
                  f"{b['game']}")
            print(f"        model {b['model_prob']:.1%}  fair {b['fair_odds']}  "
                  f"edge {b['edge']:+.1%}  stake ${b['stake_$_per_100']}  {ev}")
        if priced_markets:
            total = sum(b["stake_$_per_100"] for b in priced_markets)
            print(f"\n  Total staked: ${total:.2f} of ${args.bankroll:g} bankroll")
            print(f"  Weighted edge: {weighted_edge:+.1%}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({
                "date": today,
                "iterations": args.iterations,
                "bankroll": args.bankroll,
                "kelly": args.kelly,
                "min_edge": args.min_edge,
                "bets": all_bets,
            }, f, indent=2)
        print(f"\n  Wrote {len(all_bets)} value bets -> {args.out}")


if __name__ == "__main__":
    main()