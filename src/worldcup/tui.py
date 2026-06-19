"""Interactive terminal UI for the World Cup simulator.

A pure-standard-library, arrow-key driven front end over the same engine the CLI
uses. No third-party dependencies: keys are read with ``msvcrt`` on Windows
(``termios`` elsewhere) and the screen is painted with ANSI escape codes.

Run it with ``worldcup tui`` (or ``python -m worldcup.tui``).
"""

from __future__ import annotations

import os
import random
import sys
from typing import Callable, Optional

from .data_loader import load_results, load_world_cup
from .engine import KNOCKOUT_STAGES, MatchSimulator
from .factors.builtin import ChemistryFactor, CoachFactor, club_clusters, team_intangibles
from .fm_rating import MENTAL, PHYSICAL, group_means, technical_attrs
from .lineup_store import lineup_for, lineup_map, load_saved, save_saved
from .models import POSITIONS, Team
from .tournament import TournamentSimulator

# --- ANSI styling ----------------------------------------------------------

RESET = "\033[0m"
TITLE = "\033[30;46m"     # black on cyan
HEADER = "\033[33m"       # yellow
SELECTED = "\033[30;42m"  # black on green
DIM = "\033[90m"
GRAY = "\033[37m"
HINT = "\033[36m"
STATUS = "\033[35m"
GREEN = "\033[32m"
BOLD = "\033[1m"

# Special key sentinels returned by _read_key().
UP, DOWN, LEFT, RIGHT = "UP", "DOWN", "LEFT", "RIGHT"
ENTER, ESC, BACKSPACE = "ENTER", "ESC", "BACKSPACE"


# --- Terminal plumbing -----------------------------------------------------

def _enable_ansi() -> None:
    """Turn on virtual-terminal processing so ANSI codes work on Windows 10+."""
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            # ENABLE_PROCESSED_OUTPUT | ENABLE_WRAP_AT_EOL_OUTPUT | VT_PROCESSING
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass


def _term_size() -> tuple[int, int]:
    try:
        size = os.get_terminal_size()
        return max(40, size.columns), max(12, size.lines)
    except OSError:
        return 80, 25


if os.name == "nt":
    import msvcrt

    def _read_key() -> str:
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):        # arrow / function key prefix
            code = msvcrt.getwch()
            return {"H": UP, "P": DOWN, "K": LEFT, "M": RIGHT}.get(code, "")
        if ch in ("\r", "\n"):
            return ENTER
        if ch == "\x1b":
            return ESC
        if ch in ("\x08", "\x7f"):
            return BACKSPACE
        return ch
else:  # pragma: no cover - POSIX fallback
    import termios
    import tty

    def _read_key() -> str:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                seq = sys.stdin.read(2)
                return {"[A": UP, "[B": DOWN, "[D": LEFT, "[C": RIGHT}.get(seq, ESC)
            if ch in ("\r", "\n"):
                return ENTER
            if ch in ("\x08", "\x7f"):
                return BACKSPACE
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _hide_cursor() -> None:
    sys.stdout.write("\033[?25l")


def _show_cursor() -> None:
    sys.stdout.write("\033[?25h")


def _paint(lines: list[str]) -> None:
    """Redraw from the top-left, clearing each line and below — no full clear,
    so there is no flicker."""
    buf = "\033[H" + "".join(line + "\033[K\n" for line in lines) + "\033[J"
    sys.stdout.write(buf)
    sys.stdout.flush()


def _clear() -> None:
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def _style(text: str, code: str, width: int) -> str:
    return f"{code}{text.ljust(width)}{RESET}"


def _frame(title: str, subtitle: str, body: list[str], hint: str, status: str) -> list[str]:
    width, height = _term_size()
    w = width - 1
    lines = [
        _style(f" {title}", TITLE, w),
        _style(f"  {subtitle}", DIM, w),
        "",
    ]
    lines.extend(body)
    # Pad the body so the footer sits at the bottom of the window.
    while len(lines) < height - 2:
        lines.append("")
    lines.append(_style(f" {hint}", HINT, w))
    lines.append(_style(f" {status}", STATUS, w))
    return lines


# --- Reusable widgets ------------------------------------------------------

def _menu(
    title: str,
    subtitle: str,
    options: list[str],
    *,
    hint: str = "[Up/Down] move   [Enter] select   [Esc] back",
    status: str = "",
    start: int = 0,
) -> Optional[int]:
    """Show a scrollable single-select menu. Returns the chosen index, or None
    if the user backs out with Esc/Left."""
    sel = max(0, min(start, len(options) - 1))
    while True:
        width, height = _term_size()
        w = width - 1
        capacity = max(1, height - 7)
        max_offset = max(0, len(options) - capacity)
        offset = sel - capacity // 2
        offset = max(0, min(offset, max_offset))

        body: list[str] = []
        for k in range(capacity):
            r = offset + k
            if r >= len(options):
                body.append("")
                continue
            if r == sel:
                body.append(_style(f"  > {options[r]} ", SELECTED, w))
            else:
                body.append(_style(f"    {options[r]}", GRAY, w))
        more = f"  ({sel + 1}/{len(options)})" if len(options) > capacity else ""
        _paint(_frame(title + more, subtitle, body, hint, status))

        key = _read_key()
        if key == UP:
            sel = (sel - 1) % len(options)
        elif key == DOWN:
            sel = (sel + 1) % len(options)
        elif key in (ENTER, RIGHT):
            return sel
        elif key in (ESC, LEFT, BACKSPACE):
            return None
        elif key in ("q", "Q"):
            return None


def _pager(title: str, subtitle: str, text_lines: list[str]) -> None:
    """Scrollable read-only view of pre-rendered lines."""
    top = 0
    while True:
        width, height = _term_size()
        w = width - 1
        capacity = max(1, height - 7)
        max_top = max(0, len(text_lines) - capacity)
        top = max(0, min(top, max_top))
        body = [text_lines[top + k].ljust(w) if top + k < len(text_lines) else ""
                for k in range(capacity)]
        pos = f"  (lines {top + 1}-{min(top + capacity, len(text_lines))}/{len(text_lines)})"
        _paint(_frame(title + pos, subtitle,
                      body, "[Up/Down] scroll   [Esc] back", ""))
        key = _read_key()
        if key == UP:
            top -= 1
        elif key == DOWN:
            top += 1
        elif key in ("PGUP",):
            top -= capacity
        elif key in (ESC, LEFT, BACKSPACE, "q", "Q"):
            return


def _notice(title: str, subtitle: str, body_lines: list[str], status: str = "") -> str:
    """Render a static panel and wait for one keypress; return the key."""
    width, _ = _term_size()
    w = width - 1
    body = [GREEN + line + RESET if line.startswith("  ") else line.ljust(w)
            for line in body_lines]
    _paint(_frame(title, subtitle, body, "[R] re-run   [Esc] back", status))
    return _read_key()


# --- Data helpers ----------------------------------------------------------

def _sorted_teams(teams: dict[str, Team]) -> list[Team]:
    return sorted(teams.values(), key=lambda t: (t.group or "Z", -t.rating, t.name))


def _pick_team(teams: dict[str, Team], title: str, subtitle: str) -> Optional[Team]:
    ordered = _sorted_teams(teams)
    labels = [f"[{t.group}] {t.name:<24} {t.rating:>5.1f}" for t in ordered]
    idx = _menu(title, subtitle, labels)
    return None if idx is None else ordered[idx]


# --- Screens ---------------------------------------------------------------

def _screen_teams(teams, tournament) -> None:
    lines: list[str] = []
    for letter, names in tournament.groups.items():
        lines.append(f"{HEADER}Group {letter}{RESET}")
        for n in names:
            t = teams[n]
            lines.append(f"   {t.name:<26} {t.rating:>5.1f}")
        lines.append("")
    _pager("TEAMS BY GROUP", f"{len(teams)} teams · {tournament.name}", lines)


def _attr_bar(v: float) -> str:
    """A 1-20 attribute as a 20-cell colour bar."""
    filled = max(0, min(20, int(round(v))))
    color = GREEN if v >= 16 else (HINT if v >= 12 else DIM)
    return f"{color}{'#' * filled}{RESET}{DIM}{'.' * (20 - filled)}{RESET}"


def _player_card(team, player) -> None:
    g = group_means(player.pos, player.attributes)
    club = player.club or "—"
    age = f"{player.age}y" if player.age else "—"
    mean, sigma = team_intangibles([player])
    if mean or sigma:
        xline = (f"   intangible: mean {mean:+.2f}  ·  swing ±{sigma:.2f}  "
                 f"(experience / legs / morale — cuts both ways)")
    else:
        xline = "   intangible: none (prime age)"
    lines = [
        f"{HEADER}{player.name}{RESET}   ({player.pos})   ·   {team.name}   ·   {club}   ·   {age}",
        f"   rating {GREEN}{player.rating:.1f}{RESET}   "
        f"Mental {g['mental']:.1f}   Physical {g['physical']:.1f}   Technical {g['technical']:.1f}",
        xline,
        "",
    ]
    if not player.attributes:
        lines.append("   (no FM attributes — using curated rating)")
    else:
        for label, names in (("MENTAL", MENTAL), ("PHYSICAL", PHYSICAL),
                             ("TECHNICAL", technical_attrs(player.pos))):
            lines.append(f"{HEADER}{label}{RESET}")
            for n in names:
                v = player.attributes.get(n)
                if v is None:
                    continue
                lines.append(f"   {n:<16}{v:>3}  {_attr_bar(v)}")
            lines.append("")
    _pager(f"PLAYER · {player.name}", "FM attributes (1-20) · Esc to go back", lines)


def _player_label(p, clusters: dict) -> str:
    """One squad-row label: position, name, rating, age, M/F/T means, club.

    A leading ``*`` flags a player whose club forms a chemistry cluster in the
    current XI.
    """
    g = group_means(p.pos, p.attributes)
    tag = "*" if p.club and p.club in clusters else " "
    age = f"{p.age:>2}y" if p.age else "  -"
    return (
        f"{tag}[{p.pos}] {p.name:<22}{p.rating:>5.1f} {age}  "
        f"M{g['mental']:>4.1f} F{g['physical']:>4.1f} T{g['technical']:>4.1f}   {p.club}"
    )


def _edit_lineup(team, saved: dict[str, list[str]]) -> None:
    """Interactive starting-XI editor for one team.

    Shows the starting XI and the bench as two sections, lets the user move
    players between them, inspect FM attributes, or reset to the best XI. The
    chosen XI is persisted to ``saved`` (and disk) on exit, so every simulation
    screen uses it — the whole point being to match official lineups before
    simulating.
    """
    squad = sorted(team.squad, key=lambda p: (POSITIONS.index(p.pos), -p.rating))
    names = saved.get(team.name)
    if names:
        starting = {n for n in names if any(p.name == n for p in squad)}
    else:
        starting = {p.name for p in team.best_lineup().players}

    sel = 0
    dirty = False
    while True:
        xi = [p for p in squad if p.name in starting]
        bench = [p for p in squad if p.name not in starting]
        flat = xi + bench  # selectable rows, in display order
        sel = max(0, min(sel, len(flat) - 1))

        n_gk = sum(1 for p in xi if p.pos == "GK")
        clusters = club_clusters(xi)
        chem = ", ".join(f"{c} x{len(n)}" for c, n in clusters.items()) or "none"
        subtitle = f"base {team.rating:.0f} · group {team.group} · chemistry: {chem}"

        # Rows: (flat index or None for non-selectable headers, text, kind).
        gk_note = "" if n_gk else "  — need a GK!"
        rows: list[tuple] = [(None, f"{HEADER}STARTING XI ({len(xi)}/11){gk_note}{RESET}", "head")]
        for i, p in enumerate(xi):
            rows.append((i, _player_label(p, clusters), "player"))
        rows.append((None, f"{HEADER}BENCH ({len(bench)}){RESET}", "head"))
        for j, p in enumerate(bench):
            rows.append((len(xi) + j, _player_label(p, {}), "player"))

        width, height = _term_size()
        w = width - 1
        capacity = max(4, height - 7)
        cur_disp = next(d for d, (fi, _t, _k) in enumerate(rows) if fi == sel)
        offset = cur_disp - capacity // 2
        offset = max(0, min(offset, max(0, len(rows) - capacity)))

        body: list[str] = []
        for d in range(offset, min(offset + capacity, len(rows))):
            fi, text, kind = rows[d]
            if fi is not None and fi == sel:
                body.append(_style(f"  > {text} ", SELECTED, w))
            elif kind == "head":
                body.append("  " + text)
            else:
                body.append(_style(f"    {text}", GRAY, w))

        warn = "" if (len(xi) == 11 and n_gk) else "  (lineup factor scales any size; 11 + a GK is normal)"
        status = f"Starting XI {len(xi)}/11{warn}" + ("   · unsaved changes" if dirty else "")
        _paint(_frame(
            f"LINEUP · {team.name}", subtitle, body,
            "[Up/Down] move   [Enter] toggle XI/bench   [I] inspect   [R] best XI   [Esc] save & back",
            status,
        ))

        key = _read_key()
        if key == UP:
            sel = (sel - 1) % len(flat)
        elif key == DOWN:
            sel = (sel + 1) % len(flat)
        elif key in (ENTER, " "):
            p = flat[sel]
            if p.name in starting:
                starting.discard(p.name)
            else:
                starting.add(p.name)
            dirty = True
            # Keep the cursor on the same player as it moves between sections.
            new_xi = [q for q in squad if q.name in starting]
            new_flat = new_xi + [q for q in squad if q.name not in starting]
            sel = next(i for i, q in enumerate(new_flat) if q.name == p.name)
        elif key in ("i", "I", RIGHT):
            _player_card(team, flat[sel])
        elif key in ("r", "R"):
            starting = {p.name for p in team.best_lineup().players}
            dirty = True
        elif key in (ESC, LEFT, BACKSPACE, "q", "Q"):
            if dirty:
                saved[team.name] = [p.name for p in xi]
                save_saved(saved)
            return


def _screen_squad(teams, tournament) -> None:
    saved = load_saved()
    while True:
        team = _pick_team(teams, "SQUADS & STARTING XI",
                          "Pick a team to set its starting XI (Enter toggles, I inspects)")
        if team is None:
            return
        _edit_lineup(team, saved)


def _pick_fixture(teams, tournament) -> Optional[tuple[Team, Team, str, bool]]:
    """Shared home/away/stage picker. Returns (home, away, stage, host) or None."""
    home = _pick_team(teams, "MATCH · HOME", "Select the home / first team")
    if home is None:
        return None
    away = _pick_team(teams, "MATCH · AWAY", f"{home.name} vs ... select the opponent")
    if away is None or away.name == home.name:
        return None

    stages = ["group (draws allowed)", "R32", "R16", "QF", "SF", "F (final)"]
    stage_codes = ["group", "R32", "R16", "QF", "SF", "F"]
    sidx = _menu("MATCH · STAGE", f"{home.name} vs {away.name}", stages)
    if sidx is None:
        return None
    stage = stage_codes[sidx]
    host = home.name in set(tournament.host_team_names)
    return home, away, stage, host


def _screen_match(teams, tournament) -> None:
    fixture = _pick_fixture(teams, tournament)
    if fixture is None:
        return
    home, away, stage, host = fixture

    saved = load_saved()
    home_lineup = lineup_for(home, saved)
    away_lineup = lineup_for(away, saved)
    home_xi = home_lineup.players if home_lineup else home.best_lineup().players
    away_xi = away_lineup.players if away_lineup else away.best_lineup().players

    def xi_of(team) -> list:
        return home_xi if team is home else away_xi

    def chem_line(team) -> str:
        clusters = club_clusters(xi_of(team))
        bonus = ChemistryFactor().per_pair * sum(
            len(n) * (len(n) - 1) // 2 for n in clusters.values())
        bonus = min(ChemistryFactor().cap, bonus)
        detail = ", ".join(f"{c} x{len(n)}" for c, n in clusters.items()) or "none"
        return f"   {team.name}: +{bonus:.1f}  ({detail})"

    def coach_line(team) -> str:
        c = team.coach or {}
        if not c:
            return f"   {team.name}: no profile"
        tilt = CoachFactor().tilt
        return (f"   {team.name}: skill {c.get('skill', 0):+.1f}  "
                f"att {c.get('bias', 0) * tilt:+.1f} / def {-c.get('bias', 0) * tilt:+.1f}")

    def intan_line(team) -> str:
        mean, sigma = team_intangibles(xi_of(team))
        return f"   {team.name}: mean {mean:+.2f}  swing ±{sigma:.2f}"

    def cond_line(team) -> str:
        if team.condition >= 1.0:
            return f"   {team.name}: full fitness"
        hit = (team.condition - 1.0) * 12.0
        note = f"  ({team.injuries})" if team.injuries else ""
        return f"   {team.name}: {team.condition:.2f}  {hit:+.1f}{note}"

    def goal_log(result) -> list[str]:
        """Both teams' goals interleaved chronologically: minute, scorer, assist."""
        events = [(m, home.name, s, a) for m, s, a in result.home_scorers]
        events += [(m, away.name, s, a) for m, s, a in result.away_scorers]
        events.sort(key=lambda e: e[0])
        if not events:
            return ["   (no goals)"]
        return [
            f"   {m:>3}'  {s}{f'  (assist: {a})' if a else ''}   ({team})"
            for m, team, s, a in events
        ]

    def sub_log(team, subs) -> str:
        if not subs:
            return f"   {team.name}: none"
        return f"   {team.name}: " + ", ".join(f"{m}' {off} -> {on}" for m, off, on in subs)

    seed: Optional[int] = None
    while True:
        rng = random.Random(seed)
        sim = MatchSimulator(rng=rng)
        result = sim.simulate_live(home, away, stage=stage, neutral=not host,
                                   home_lineup=home_lineup, away_lineup=away_lineup)
        xi_note = (f"   lineups: {home.name} "
                   f"{'custom XI' if home_lineup else 'best XI'}  ·  "
                   f"{away.name} {'custom XI' if away_lineup else 'best XI'}")
        body = [
            "",
            f"  {result.score_str()}",
            "",
            f"   expected goals:  {home.name} {result.home_xg:.2f}  -  {result.away_xg:.2f} {away.name}",
            f"   stage: {stage}" + ("   (host plays at home)" if host else "   (neutral venue)"),
            xi_note,
            "",
            f"{HEADER}   goals (scorer · assist){RESET}",
            *goal_log(result),
            "",
            f"{HEADER}   substitutions{RESET}",
            sub_log(home, result.home_subs),
            sub_log(away, result.away_subs),
            "",
            f"{HEADER}   chemistry{RESET}",
            chem_line(home),
            chem_line(away),
            "",
            f"{HEADER}   coaching (skill + attack/defence tilt){RESET}",
            coach_line(home),
            coach_line(away),
            "",
            f"{HEADER}   intangibles (drawn this match; mean ± swing){RESET}",
            intan_line(home),
            intan_line(away),
            "",
            f"{HEADER}   condition (disclosed injuries){RESET}",
            cond_line(home),
            cond_line(away),
        ]
        if result.winner:
            body += ["", f"   winner: {result.winner}"]
        key = _notice(f"MATCH RESULT", f"{home.name} vs {away.name}", body)
        if key in ("r", "R"):
            seed = None
            continue
        return


def _screen_match_odds(teams, tournament) -> None:
    fixture = _pick_fixture(teams, tournament)
    if fixture is None:
        return
    home, away, stage, host = fixture

    presets = ["200 iterations (fast)", "500 iterations", "1000 iterations",
               "2000 iterations", "5000 iterations (slow)"]
    counts = [200, 500, 1000, 2000, 5000]
    cidx = _menu("MATCH ODDS", f"{home.name} vs {away.name} ({stage}) — Monte Carlo",
                 presets, start=2)
    if cidx is None:
        return
    n = counts[cidx]

    saved = load_saved()
    home_lineup = lineup_for(home, saved)
    away_lineup = lineup_for(away, saved)

    knockout = stage in KNOCKOUT_STAGES
    verb = "advances" if knockout else "win"
    while True:
        _paint(_frame("MATCH ODDS", f"Running {n} simulations...",
                      ["", f"  {GREEN}Simulating {n} matches — please wait...{RESET}"], "", ""))
        rng = random.Random()
        sim = MatchSimulator(rng=rng)
        odds = sim.monte_carlo(home, away, n, stage=stage, neutral=not host,
                               home_lineup=home_lineup, away_lineup=away_lineup,
                               track_scorers=True)

        rows = [(f"{home.name} {verb}", odds.home_win)]
        if not knockout:
            rows.append(("Draw", odds.draw))
        rows.append((f"{away.name} {verb}", odds.away_win))
        wlabel = max(len(label) for label, _ in rows)

        body = [
            "",
            f"   {home.name} vs {away.name}   ·   stage {stage}"
            + ("   (host plays at home)" if host else "   (neutral venue)"),
            f"   lineups: {home.name} {'custom XI' if home_lineup else 'best XI'}"
            f"  ·  {away.name} {'custom XI' if away_lineup else 'best XI'}",
            "",
            f"{HEADER}   outcome over {odds.iterations} simulations{RESET}",
        ]
        for label, prob in rows:
            body.append(f"   {label:<{wlabel}}  {prob:>6.1%}")
        body += [
            "",
            f"{HEADER}   average goals / xG{RESET}",
            f"   goals:  {home.name} {odds.avg_home_goals:.2f}  -  {odds.avg_away_goals:.2f} {away.name}",
            f"   xG:     {home.name} {odds.avg_home_xg:.2f}  -  {odds.avg_away_xg:.2f} {away.name}",
            "",
            f"{HEADER}   top scorers / assisters (per match){RESET}",
        ]

        def scorer_rows(team, ranked):
            rows = [(name, g, a) for name, g, a in ranked if g > 0][:5]
            if not rows:
                return [f"   {team.name}: no goals"]
            out = [f"   {team.name}"]
            wname = max(len(name) for name, _, _ in rows)
            for name, g, a in rows:
                out.append(f"     {name:<{wname}}  {g:>4.2f} G   {a:>4.2f} A")
            return out

        body += scorer_rows(home, odds.home_scorers)
        body += scorer_rows(away, odds.away_scorers)
        body += [
            "",
            f"{HEADER}   most common scorelines{RESET}",
        ]
        for (hg, ag), prob in odds.scorelines:
            body.append(f"   {hg}-{ag}   {prob:>6.1%}")

        bet_verb = "to advance" if knockout else "to win"
        cands = [
            (f"{home.name} {bet_verb}", odds.home_win, "match result"),
            (f"{away.name} {bet_verb}", odds.away_win, "match result"),
            ("Over 2.5 goals", odds.over25, "total goals"),
            ("Under 2.5 goals", 1.0 - odds.over25, "total goals"),
            ("Both teams to score: Yes", odds.btts, "BTTS"),
            ("Both teams to score: No", 1.0 - odds.btts, "BTTS"),
        ]
        if not knockout:
            cands.append(("Draw", odds.draw, "match result"))
        if odds.scorelines:
            (hg, ag), p = odds.scorelines[0]
            cands.append((f"{home.name} {hg}-{ag} {away.name}", p, "correct score"))

        def _fair(p):
            return 1.0 / p if p > 0 else float("inf")

        pick = max(cands, key=lambda c: c[1])
        wsel = max(len(sel) for sel, _, _ in cands)
        body += [
            "",
            f"{HEADER}   suggested bet{RESET}  (back it if the site's odds beat the fair price)",
            f"   {GREEN}>> {pick[0]}  -  model {pick[1]:.1%}, fair odds {_fair(pick[1]):.2f}{RESET}",
            "",
            f"{HEADER}   fair odds across markets{RESET}",
        ]
        for sel, p, market in cands:
            body.append(f"   {sel:<{wsel}}  {p:>6.1%}  fair {_fair(p):>5.2f}  ({market})")

        key = _notice("MATCH ODDS", f"{home.name} vs {away.name}", body)
        if key in ("r", "R"):
            continue
        return


def _screen_bets(teams, tournament) -> None:
    from . import polymarket
    from .betting import run_betting

    labels = [f"{label}" for _slug, label in polymarket.KNOWN_EVENTS]
    eidx = _menu("POLYMARKET BETS", "Pick a market to compare against our model",
                 labels)
    if eidx is None:
        return
    slug = polymarket.KNOWN_EVENTS[eidx][0]

    presets = ["500 iterations (fast)", "1000 iterations", "2000 iterations",
               "5000 iterations (slow)"]
    counts = [500, 1000, 2000, 5000]
    cidx = _menu("POLYMARKET BETS", f"{labels[eidx]} — Monte Carlo runs", presets, start=1)
    if cidx is None:
        return
    n = counts[cidx]

    bidx = _menu("POLYMARKET BETS", "Bankroll for staking (half-Kelly)",
                 ["$100", "$500", "$1000"], start=0)
    if bidx is None:
        return
    bankroll = [100.0, 500.0, 1000.0][bidx]

    _paint(_frame("POLYMARKET BETS", f"Fetching '{slug}' and simulating {n} runs...",
                  ["", f"  {GREEN}Contacting Polymarket and running {n} tournaments...{RESET}"],
                  "", ""))
    try:
        event = polymarket.fetch_event(slug, teams)
    except polymarket.PolymarketError as exc:
        _notice("POLYMARKET BETS", "Network error",
                ["", f"   Could not reach Polymarket:", f"   {exc}",
                 "", "   Check your connection and try again."])
        return
    if not event.matched:
        _notice("POLYMARKET BETS", event.title,
                ["", "   None of this market's teams map to our squads.",
                 f"   Unmatched: {', '.join(event.unmatched) or 'n/a'}"])
        return

    rng = random.Random()
    sim = TournamentSimulator(teams, tournament, rng=rng)
    report = run_betting(sim, event, n, bankroll=bankroll, kelly_mult=0.5, min_edge=0.02)

    lines: list[str] = [
        f"{HEADER}Model vs market — edge = our prob - market prob{RESET}",
        f"  {'Team':<20}{'Market':>9}{'Model':>9}{'Edge':>9}{'EV/$1':>9}",
        "",
    ]
    for c in report.comparisons:
        color = GREEN if c.edge > 0 else DIM
        lines.append(f"  {c.team:<20}{c.market_prob:>9.1%}{c.model_prob:>9.1%}"
                     f"{color}{c.edge:>+9.1%}{RESET}{c.ev:>+9.1%}")
    if report.unmatched:
        lines.append("")
        lines.append(f"{DIM}  skipped (no squad): {', '.join(report.unmatched)}{RESET}")

    lines += ["", f"{HEADER}Value bets — half-Kelly, ${bankroll:g} bankroll, min edge 2%{RESET}"]
    if not report.bets:
        lines.append("  (no positive-edge bets clear the threshold)")
    else:
        for b in report.bets:
            lines.append(f"  {GREEN}{b.team:<20}{RESET} ${b.stake:>7.2f} @ {b.price:.3f}  "
                         f"{b.shares:>6.1f} shares  edge {b.edge:+.1%}  EV {b.ev:+.0%}")
        lines += [
            "",
            f"  staked ${report.total_staked:.2f}   mean P&L {GREEN}${report.mean_pnl:+.2f}{RESET}"
            f"   ROI {report.roi:+.1%}   green {report.win_rate:.0%} of runs",
            f"  P&L range:  p5 ${report.percentile(0.05):+.2f}   "
            f"median ${report.percentile(0.50):+.2f}   p95 ${report.percentile(0.95):+.2f}",
            f"{DIM}  note: bets settle on the same model that prices them — this is edge"
            f" variance, not a backtest.{RESET}",
        ]
    _pager(f"BETS · {event.title}", f"{report.iterations} simulations · {slug}", lines)


def _game_detail(teams, game, hosts) -> None:
    from .games import compare_game

    n = 3000
    while True:
        extra = " and fetching pre-kickoff odds" if game.closed else ""
        _paint(_frame("MATCH vs MARKET", f"Simulating {game.home} vs {game.away}...",
                      ["", f"  {GREEN}Running {n} match simulations{extra}...{RESET}"], "", ""))
        sim = MatchSimulator(rng=random.Random())
        try:
            comp = compare_game(sim, teams, game, n, host_names=hosts)
        except Exception as exc:  # CLOB history can be missing for some games
            _notice("MATCH vs MARKET", game.title,
                    ["", "   Could not load market odds:", f"   {exc}"])
            return

        tag = "pre-kickoff market" if game.closed else "live market"
        rows = [(f"{game.home} win", 0), ("Draw", 1), (f"{game.away} win", 2)]
        wlabel = max(len(label) for label, _ in rows)
        body = [
            "",
            f"   {game.home} vs {game.away}   ·   {game.date}   ·   {tag}",
            "",
            f"{HEADER}   {'':<{wlabel}}  {'Market':>8}{'Model':>8}{'Edge':>9}{RESET}",
        ]
        for label, i in rows:
            mk = f"{comp.market[i]:>8.1%}" if comp.market else f"{'-':>8}"
            if comp.edges:
                col = GREEN if comp.edges[i] > 0 else DIM
                ed = f"{col}{comp.edges[i]:>+9.1%}{RESET}"
            else:
                ed = f"{'-':>9}"
            body.append(f"   {label:<{wlabel}}  {mk}{comp.model[i]:>8.1%}{ed}")
        if game.result:
            called = {"H": f"{game.home} win", "D": "draw", "A": f"{game.away} win"}[game.result]
            body += [
                "",
                f"{HEADER}   actual result{RESET}",
                f"   {GREEN}{game.home} {game.score} {game.away}{RESET}   ->   {called}",
                f"   model favourite right: {'yes' if comp.model_pick == game.result else 'no'}"
                f"    ·    market favourite right: {'yes' if comp.market_pick == game.result else 'no'}",
            ]
        key = _notice("MATCH vs MARKET", f"{game.home} vs {game.away}", body)
        if key in ("r", "R"):
            continue
        return


def _games_accuracy(teams, games, hosts) -> None:
    from .games import accuracy_summary, compare_games

    played = [g for g in games if g.closed]
    if not played:
        _notice("ACCURACY", "Model vs market", ["", "   No played games yet."])
        return
    n = 2000

    def progress(i, total):
        _paint(_frame("ACCURACY", "Scoring played games against actual results",
                      ["", f"  {GREEN}Game {i}/{total} — simulating + fetching pre-kickoff odds...{RESET}"],
                      "", ""))

    sim = MatchSimulator(rng=random.Random())
    try:
        comps = compare_games(sim, teams, played, n, host_names=hosts, on_progress=progress)
    except Exception as exc:
        _notice("ACCURACY", "Network error", ["", f"   {exc}"])
        return
    s = accuracy_summary(comps)

    lines = [
        f"{HEADER}Played games: who predicted better?{RESET}",
        f"  {'Fixture':<28}{'Market H/D/A':>20}{'Model H/D/A':>20}  Res",
        "",
    ]
    for c in comps:
        res = c.game.result or "?"
        mk = (f"{c.market[0]:>6.0%}{c.market[1]:>6.0%}{c.market[2]:>7.0%}" if c.market
              else f"{'-':>19}")
        md = f"{c.model[0]:>6.0%}{c.model[1]:>6.0%}{c.model[2]:>7.0%}"
        fixture = f"{c.game.home} {c.game.score} {c.game.away}"
        lines.append(f"  {fixture:<28}{mk:>20}{md:>20}   {res}")
    lines += [
        "",
        f"{HEADER}Scorecard over {s.n} games{RESET}",
        f"  favourite correct:  model {GREEN}{s.model_correct}/{s.n}{RESET}"
        f"   market {s.market_correct}/{s.n}",
        f"  Brier score (lower = better):  model {s.model_brier:.3f}   market {s.market_brier:.3f}",
        f"  -> {GREEN}{'model' if s.model_brier < s.market_brier else 'market'} was sharper{RESET}"
        " on these games",
        f"{DIM}  (real outcomes — unlike the futures bet sim, this is a genuine backtest){RESET}",
    ]
    _pager("ACCURACY · played games", f"{s.n} games · {n} sims each", lines)


def _dash_bar(value: float, scale: float, width: int = 20) -> str:
    filled = max(0, min(width, round(width * value / scale))) if scale > 0 else 0
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def _screen_dashboard(teams, tournament) -> None:
    from . import polymarket
    from .games import build_dashboard, compare_games

    _paint(_frame("PERFORMANCE DASHBOARD", "Model vs market over every finished game",
                  ["", f"  {GREEN}Fetching World Cup fixtures from Polymarket...{RESET}"], "", ""))
    try:
        games = polymarket.fetch_games(teams)
    except polymarket.PolymarketError as exc:
        _notice("DASHBOARD", "Network error",
                ["", "   Could not reach Polymarket:", f"   {exc}"])
        return
    played = [g for g in games if g.mapped and g.closed]
    if not played:
        _notice("DASHBOARD", "Nothing to score yet",
                ["", "   No finished World Cup fixtures yet — check back after kickoff."])
        return

    hosts = set(tournament.host_team_names)
    n = 2000

    def progress(i, total):
        _paint(_frame("PERFORMANCE DASHBOARD", "Scoring finished games against actual results",
                      ["", f"  {GREEN}Game {i}/{total} — simulating + fetching pre-kickoff odds...{RESET}"],
                      "", ""))

    sim = MatchSimulator(rng=random.Random())
    try:
        comps = compare_games(sim, teams, played, n, host_names=hosts, on_progress=progress)
    except Exception as exc:
        _notice("DASHBOARD", "Network error", ["", f"   {exc}"])
        return
    dash = build_dashboard(comps)
    s = dash.summary
    if not s.n:
        _notice("DASHBOARD", "Nothing to score yet",
                ["", "   No finished fixtures could be graded (missing market history)."])
        return

    mix = dash.result_mix
    winner = "model" if dash.model_sharper else "market"
    gap = abs(s.model_brier - s.market_brier)
    lines = [
        f"{HEADER}Finished games graded: {s.n}{RESET}"
        f"   ·   results so far: {mix['H']} home / {mix['D']} draw / {mix['A']} away",
        "",
        f"{HEADER}   {'':24}{'Model':>12}{'Market':>12}{RESET}",
        f"   {'favourite correct':24}"
        f"{f'{s.model_correct}/{s.n} ({s.model_correct/s.n:.0%})':>12}"
        f"{f'{s.market_correct}/{s.n} ({s.market_correct/s.n:.0%})':>12}",
        f"   {'Brier (lower=better)':24}{s.model_brier:>12.3f}{s.market_brier:>12.3f}",
        f"   {'log-loss (lower=better)':24}{dash.model_logloss:>12.3f}{dash.market_logloss:>12.3f}",
        "",
        f"   Brier   model  {_dash_bar(s.model_brier, 1.0)} {s.model_brier:.3f}",
        f"           market {_dash_bar(s.market_brier, 1.0)} {s.market_brier:.3f}",
        f"           -> {GREEN}{winner} sharper{RESET} by {gap:.3f}",
    ]
    if dash.disagreements:
        lead = ("model" if dash.model_edge_wins > dash.market_edge_wins
                else "market" if dash.market_edge_wins > dash.model_edge_wins else "neither")
        lines += [
            "",
            f"{HEADER}When they backed different favourites ({dash.disagreements} game(s)){RESET}",
            f"   model's pick won {GREEN}{dash.model_edge_wins}{RESET}"
            f"   ·   market's pick won {dash.market_edge_wins}"
            f"   ->   {GREEN}{lead}{RESET} has the edge so far",
        ]
    lines += [
        "",
        f"{HEADER}Per game{RESET}",
        f"  {'Fixture':<30}{'Res':>4}{'Model':>7}{'Market':>8}   Verdict",
    ]
    for gr in dash.games:
        c = gr.comparison
        fixture = f"{c.game.home} {c.game.score} {c.game.away}"
        if gr.model_right and gr.market_right:
            verdict = f"{GREEN}both right{RESET}"
        elif gr.model_right:
            verdict = f"{GREEN}model only{RESET}"
        elif gr.market_right:
            verdict = "market only"
        else:
            verdict = f"{DIM}both wrong{RESET}"
        star = " *" if gr.disagreed else ""
        lines.append(f"  {fixture:<30}{gr.result:>4}{c.model_pick:>7}{c.market_pick:>8}   {verdict}{star}")
    if any(gr.disagreed for gr in dash.games):
        lines.append(f"{DIM}  (* = model and market backed different favourites){RESET}")
    lines.append(f"{DIM}  real outcomes — a genuine backtest of model vs market.{RESET}")
    _pager("PERFORMANCE DASHBOARD", f"model vs market · {s.n} finished games · {n} sims each", lines)


def _screen_games(teams, tournament) -> None:
    from . import polymarket

    _paint(_frame("MATCH ODDS vs MARKET", "Fetching World Cup fixtures from Polymarket...",
                  ["", f"  {GREEN}Contacting Polymarket...{RESET}"], "", ""))
    try:
        games = polymarket.fetch_games(teams)
    except polymarket.PolymarketError as exc:
        _notice("MATCH ODDS vs MARKET", "Network error",
                ["", "   Could not reach Polymarket:", f"   {exc}",
                 "", "   Check your connection and try again."])
        return
    games = [g for g in games if g.mapped]
    if not games:
        _notice("MATCH ODDS vs MARKET", "No fixtures",
                ["", "   No World Cup fixtures could be matched to our squads."])
        return

    hosts = set(tournament.host_team_names)
    upcoming = [g for g in games if not g.closed]
    played = [g for g in games if g.closed]
    sel = 0
    while True:
        items: list[tuple[str, object]] = [("accuracy", None)]
        labels = [f"Accuracy summary: model vs market on {len(played)} played games"]
        for g in upcoming:
            labels.append(f"[{g.date[5:]}]  {g.home} vs {g.away}")
            items.append(("game", g))
        for g in played:
            labels.append(f"[done]   {g.home} {g.score} {g.away}")
            items.append(("game", g))
        idx = _menu("MATCH ODDS vs MARKET",
                    f"{len(upcoming)} upcoming · {len(played)} played · pick a fixture",
                    labels, start=sel)
        if idx is None:
            return
        sel = idx
        kind, payload = items[idx]
        if kind == "accuracy":
            _games_accuracy(teams, games, hosts)
        else:
            _game_detail(teams, payload, hosts)


def _screen_tournament(teams, tournament) -> None:
    seed_choice = _menu("FULL TOURNAMENT", "Run one complete tournament",
                        ["Random run", "Seeded run (seed = 7)", "Seeded run (seed = 42)"])
    if seed_choice is None:
        return
    seed = {0: None, 1: 7, 2: 42}[seed_choice]
    rng = random.Random(seed)
    results = load_results()
    sim = TournamentSimulator(teams, tournament, rng=rng, results=results)
    lineups = lineup_map(teams)
    outcome = sim.run_once(lineups=lineups)

    lines: list[str] = []
    if results:
        lines.append(f"{GREEN}seeded with {len(results)} games already played; "
                     f"only remaining fixtures simulated{RESET}")
        lines.append("")
    lines += [f"{HEADER}=== GROUP STAGE ==={RESET}", ""]
    for g in outcome.groups:
        lines.append(f"{HEADER}Group {g.letter}{RESET}")
        lines.append(f"  {'Team':<22}{'Pld':>4}{'W':>3}{'D':>3}{'L':>3}{'GF':>4}{'GA':>4}{'GD':>5}{'Pts':>5}")
        for s in g.standings:
            lines.append(f"  {s.team:<22}{s.played:>4}{s.won:>3}{s.drawn:>3}{s.lost:>3}"
                         f"{s.gf:>4}{s.ga:>4}{s.gd:>+5}{s.pts:>5}")
        lines.append("")
    lines.append(f"{HEADER}=== KNOCKOUT ==={RESET}")
    for rnd in outcome.knockout.rounds:
        lines.append("")
        lines.append(f"{HEADER}{rnd[0].stage}{RESET}")
        for m in rnd:
            lines.append(f"   {m.score_str()}")
    lines.append("")
    lines.append(f"{GREEN}{BOLD}>>> CHAMPION: {outcome.champion} <<<{RESET}")
    label = "random" if seed is None else f"seed {seed}"
    xi = f" · {len(lineups)} custom XI" if lineups else ""
    _pager("TOURNAMENT RESULT", f"One full run ({label}){xi}", lines)


def _screen_odds(teams, tournament) -> None:
    presets = ["200 iterations (fast)", "500 iterations", "1000 iterations",
               "2000 iterations", "5000 iterations (slow)"]
    counts = [200, 500, 1000, 2000, 5000]
    cidx = _menu("TITLE ODDS", "Monte Carlo — more iterations = steadier numbers", presets,
                 start=2)
    if cidx is None:
        return
    n = counts[cidx]
    top_idx = _menu("TITLE ODDS", "How many teams to show?", ["Top 8", "Top 16", "Top 24"], start=1)
    if top_idx is None:
        return
    top = [8, 16, 24][top_idx]

    width, _ = _term_size()
    _paint(_frame("TITLE ODDS", f"Running {n} simulations...",
                  ["", f"  {GREEN}Simulating {n} tournaments — please wait...{RESET}"],
                  "", ""))
    rng = random.Random()
    results = load_results()
    sim = TournamentSimulator(teams, tournament, rng=rng, results=results)
    lineups = lineup_map(teams)
    report = sim.monte_carlo(n, lineups=lineups)

    lines = [f"  {'Team':<22}{'R16':>8}{'QF':>8}{'SF':>8}{'Final':>8}{'Win':>8}", ""]
    for name, _p in report.title_odds()[:top]:
        r = report.reach[name]
        lines.append(f"  {name:<22}{r['R16']:>8.1%}{r['QF']:>8.1%}"
                     f"{r['SF']:>8.1%}{r['Final']:>8.1%}{GREEN}{r['Champion']:>8.1%}{RESET}")
    xi = f" · {len(lineups)} custom XI" if lineups else ""
    played = f" · {len(results)} games played" if results else ""
    _pager("TITLE ODDS", f"{report.iterations} simulations · top {top}{xi}{played}", lines)


# --- Main loop -------------------------------------------------------------

def run() -> None:
    _enable_ansi()
    teams, tournament = load_world_cup()

    screens: list[tuple[str, Callable]] = [
        ("Teams by group", _screen_teams),
        ("Squads & starting XI (edit lineups)", _screen_squad),
        ("Simulate a match", _screen_match),
        ("Match odds (Monte Carlo)", _screen_match_odds),
        ("Full tournament", _screen_tournament),
        ("Title odds (Monte Carlo)", _screen_odds),
        ("Match odds vs market (Polymarket)", _screen_games),
        ("Performance dashboard (model vs market)", _screen_dashboard),
        ("Polymarket: compare & bet", _screen_bets),
    ]
    labels = [name for name, _ in screens] + ["Quit"]

    _hide_cursor()
    _clear()
    try:
        sel = 0
        while True:
            idx = _menu(
                "WORLD CUP 2026 SIMULATOR",
                tournament.name,
                labels,
                hint="[Up/Down] move   [Enter] open   [Q] quit",
                status=f"{len(teams)} teams · {len(tournament.groups)} groups",
                start=sel,
            )
            if idx is None or idx == len(screens):  # Esc/Q or "Quit"
                return
            sel = idx
            _clear()
            screens[idx][1](teams, tournament)
            _clear()
    finally:
        _show_cursor()
        _clear()


if __name__ == "__main__":
    run()
