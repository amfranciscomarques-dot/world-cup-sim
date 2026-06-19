"""Attach an ``age`` to every player and a ``coach`` profile to every team in
data/teams_2026.json, so the condition / coaching / intangibles factors have
real inputs (ages and managers as of the 2025/26 season, the 2026 World Cup).

Ages are curated estimates in the same spirit as the player ratings: the
veterans (33+) and wonderkids (<=21) — the players the intangibles factor
actually swings on — are placed carefully; the rest get a plausible prime age
that produces a near-zero effect. Players/teams not in the maps fall back to a
prime age (27) / no coach. Re-running is safe and idempotent.

    python scripts/add_intangibles.py
"""

from __future__ import annotations

import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "teams_2026.json"
DEFAULT_AGE = 27  # prime: mean 0, tiny sigma -> negligible intangible

# Age at the 2026 World Cup. Curated; veterans and youngsters are the ones that
# matter to the intangibles factor and are placed with care.
AGES: dict[str, int] = {
    # --- Veterans (33+): negative mean, large swing -------------------------
    "Cristiano Ronaldo": 41, "Lionel Messi": 39, "Luka Modric": 40,
    "Nicolas Otamendi": 38, "Edin Dzeko": 40, "Rais MBolhi": 40,
    "Yann Sommer": 37, "Robin Olsen": 36, "Marko Arnautovic": 37,
    "Miralem Pjanic": 36, "Kyle Walker": 36, "Antoine Griezmann": 35,
    "Hassan Al-Haydos": 35, "Salman Al-Faraj": 36, "Cuco Martina": 36,
    "Youssef Msakni": 35, "Enner Valencia": 36, "Pepe": 43,
    "James Rodriguez": 34, "Mohamed Salah": 34, "Casemiro": 34,
    "David Alaba": 34, "Danilo": 34, "Aissa Mandi": 34, "Cedric Bakambu": 34,
    "Jordan Ayew": 34, "Chris Wood": 34, "Memphis Depay": 32, "Sead Kolasinac": 32,
    "Virgil van Dijk": 34, "Thibaut Courtois": 33, "Edouard Mendy": 34,
    "Hakim Ziyech": 33, "Granit Xhaka": 33, "Marcelo Brozovic": 33,
    "Roberto Lopes": 33, "Mateo Kovacic": 31, "Kalidou Koulibaly": 34,
    "Sergio Rochet": 33, "Mohamed Elneny": 33, "Trezeguet": 31,
    "Alireza Beiranvand": 33, "Shojae Khalilzadeh": 36, "Ryan Mendes": 35,
    "Garry Rodrigues": 35, "Eloy Room": 37, "Johny Placide": 38, "Duckens Nazon": 31,
    "Almoez Ali": 29, "Bryan Ruiz": 40, "Vozinha": 39, "Mathew Ryan": 34,
    "Wataru Endo": 33, "Kim Young-gwon": 36, "Aymen Hussein": 29,
    # --- Wonderkids (<=21): raw mean, volatile ------------------------------
    "Lamine Yamal": 18, "Kendry Paez": 19, "Endrick": 19, "Arda Guler": 21,
    "Kenan Yildiz": 21, "Julio Enciso": 22, "Jhon Duran": 22,
    "Abdukodir Khusanov": 22, "Abbosbek Fayzullaev": 22, "Yazan Al-Naimat": 24,
    "Marko Stamenic": 24, "Kendry": 19, "Ben Waine": 25,
    # --- Prime-ish notables (kept for realism; near-zero effect) ------------
    "Kylian Mbappe": 27, "Erling Haaland": 25, "Jude Bellingham": 22,
    "Vinicius Junior": 25, "Pedri": 23, "Gavi": 21, "Florian Wirtz": 23,
    "Jamal Musiala": 23, "Cole Palmer": 24, "Phil Foden": 26, "Bukayo Saka": 24,
    "Rodrygo": 25, "Raphinha": 29, "Federico Valverde": 27, "Vitinha": 26,
    "Joao Neves": 21, "Nuno Mendes": 23, "Goncalo Ramos": 24, "Rafael Leao": 26,
    "Bruno Fernandes": 31, "Ruben Dias": 28, "Bernardo Silva": 31, "Joao Cancelo": 31,
    "Diogo Costa": 26, "Lautaro Martinez": 28, "Julian Alvarez": 26,
    "Enzo Fernandez": 25, "Alexis Mac Allister": 27, "Nico Williams": 23,
    "Pau Cubarsi": 19, "Dean Huijsen": 21, "Warren Zaire-Emery": 20,
    "Kobbie Mainoo": 20, "Xavi Simons": 22, "Estevao": 18, "Savinho": 21,
}

# National-team managers (2025/26). skill = rating points added to both ends;
# bias in [-1, 1] tilts attack (+) vs defence (-).
COACHES: dict[str, dict] = {
    "Argentina": {"skill": 2.5, "bias": 0.1},     # Scaloni
    "Brazil": {"skill": 2.5, "bias": 0.1},        # Ancelotti
    "France": {"skill": 2.0, "bias": -0.2},       # Deschamps, pragmatic
    "Spain": {"skill": 1.5, "bias": 0.3},         # De la Fuente
    "England": {"skill": 2.0, "bias": 0.1},       # Tuchel
    "Portugal": {"skill": 1.0, "bias": 0.4},      # Martinez, gung-ho
    "Germany": {"skill": 2.0, "bias": 0.3},       # Nagelsmann
    "Netherlands": {"skill": 1.5, "bias": 0.1},   # Koeman
    "Belgium": {"skill": 1.0, "bias": 0.2},       # Garcia
    "Croatia": {"skill": 1.5, "bias": 0.0},       # Dalic
    "Uruguay": {"skill": 1.5, "bias": 0.6},       # Bielsa, extreme attack
    "Morocco": {"skill": 1.5, "bias": -0.1},      # Regragui
    "USA": {"skill": 2.0, "bias": 0.2},           # Pochettino
    "Mexico": {"skill": 1.0, "bias": 0.0},        # Aguirre
    "Colombia": {"skill": 1.5, "bias": 0.2},      # Lorenzo
    "Austria": {"skill": 2.0, "bias": 0.4},       # Rangnick, gegenpress
    "Canada": {"skill": 1.5, "bias": 0.3},        # Marsch, high press
    "Norway": {"skill": 0.5, "bias": 0.2},        # Solbakken
    "Japan": {"skill": 1.0, "bias": 0.1},         # Moriyasu
    "South Korea": {"skill": 0.5, "bias": 0.0},   # Hong Myung-bo
    "Switzerland": {"skill": 0.5, "bias": -0.1},  # Yakin
    "Turkiye": {"skill": 1.0, "bias": 0.3},       # Montella
    "Ecuador": {"skill": 0.5, "bias": 0.0},       # Beccacece
    "Senegal": {"skill": 1.0, "bias": 0.0},
    "Egypt": {"skill": 0.5, "bias": 0.1},         # Hossam Hassan
    "Iran": {"skill": 0.5, "bias": -0.2},         # Ghalenoei
    "Australia": {"skill": 0.5, "bias": -0.1},    # Popovic
    "Saudi Arabia": {"skill": 1.0, "bias": 0.0},  # Renard
    "Scotland": {"skill": 1.0, "bias": 0.0},      # Clarke
    "Sweden": {"skill": 0.5, "bias": 0.1},
}


def main() -> None:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    tagged = 0
    defaulted = 0
    coached = 0
    for team in data["teams"]:
        if team["name"] in COACHES:
            team["coach"] = COACHES[team["name"]]
            coached += 1
        for p in team["players"]:
            if p["name"] in AGES:
                p["age"] = AGES[p["name"]]
                tagged += 1
            else:
                p["age"] = DEFAULT_AGE
                defaulted += 1
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"ages: {tagged} curated, {defaulted} defaulted to {DEFAULT_AGE}; "
          f"coaches: {coached} teams")


if __name__ == "__main__":
    main()
