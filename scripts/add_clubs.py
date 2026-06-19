"""Attach a current ``club`` to each player in data/teams_2026.json and add the
PSG midfielder Joao Neves to Portugal, so the chemistry factor has shared-club
cores to reward (clubs as of the 2025/26 season).

Players not in the map keep an empty club (no chemistry contribution). Re-running
is safe and idempotent.

    python scripts/add_clubs.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from worldcup.fm_rating import generate_attributes, rating_from_attributes  # noqa: E402

DATA = ROOT / "data" / "teams_2026.json"

# name -> club. Curated to reflect 2025/26 squads, with an eye on the same-club
# cores that create chemistry (PSG/Portugal, Bayern/Germany, Real Madrid, ...).
CLUBS: dict[str, str] = {
    # Argentina
    "Emiliano Martinez": "Aston Villa", "Cristian Romero": "Tottenham",
    "Nicolas Otamendi": "Benfica", "Nahuel Molina": "Atletico Madrid",
    "Rodrigo De Paul": "Inter Miami", "Enzo Fernandez": "Chelsea",
    "Alexis Mac Allister": "Liverpool", "Lionel Messi": "Inter Miami",
    "Julian Alvarez": "Atletico Madrid", "Lautaro Martinez": "Inter Milan",
    # Spain
    "Unai Simon": "Athletic Bilbao", "Robin Le Normand": "Atletico Madrid",
    "Dani Carvajal": "Real Madrid", "Marc Cucurella": "Chelsea",
    "Rodri": "Manchester City", "Pedri": "Barcelona", "Fabian Ruiz": "PSG",
    "Lamine Yamal": "Barcelona", "Nico Williams": "Athletic Bilbao",
    "Alvaro Morata": "Galatasaray",
    # France
    "Mike Maignan": "AC Milan", "William Saliba": "Arsenal",
    "Dayot Upamecano": "Bayern Munich", "Theo Hernandez": "Al-Hilal",
    "Jules Kounde": "Barcelona", "Aurelien Tchouameni": "Real Madrid",
    "Eduardo Camavinga": "Real Madrid", "Antoine Griezmann": "Atletico Madrid",
    "Kylian Mbappe": "Real Madrid", "Ousmane Dembele": "PSG",
    "Marcus Thuram": "Inter Milan",
    # England
    "Jordan Pickford": "Everton", "John Stones": "Manchester City",
    "Marc Guehi": "Crystal Palace", "Kyle Walker": "Burnley",
    "Declan Rice": "Arsenal", "Jude Bellingham": "Real Madrid",
    "Cole Palmer": "Chelsea", "Phil Foden": "Manchester City",
    "Bukayo Saka": "Arsenal", "Harry Kane": "Bayern Munich",
    # Portugal
    "Diogo Costa": "Porto", "Ruben Dias": "Manchester City",
    "Joao Cancelo": "Al-Hilal", "Nuno Mendes": "PSG",
    "Joao Neves": "PSG", "Bruno Fernandes": "Manchester United",
    "Vitinha": "PSG", "Bernardo Silva": "Manchester City",
    "Cristiano Ronaldo": "Al-Nassr", "Rafael Leao": "AC Milan",
    "Goncalo Ramos": "PSG",
    # Brazil
    "Alisson": "Liverpool", "Marquinhos": "PSG", "Gabriel Magalhaes": "Arsenal",
    "Danilo": "Flamengo", "Casemiro": "Manchester United",
    "Bruno Guimaraes": "Newcastle", "Lucas Paqueta": "West Ham",
    "Vinicius Junior": "Real Madrid", "Rodrygo": "Real Madrid",
    "Raphinha": "Barcelona", "Endrick": "Real Madrid",
    # Netherlands
    "Bart Verbruggen": "Brighton", "Virgil van Dijk": "Liverpool",
    "Nathan Ake": "Manchester City", "Denzel Dumfries": "Inter Milan",
    "Frenkie de Jong": "Barcelona", "Tijjani Reijnders": "Manchester City",
    "Xavi Simons": "Tottenham", "Cody Gakpo": "Liverpool",
    "Memphis Depay": "Corinthians",
    # Germany
    "Marc-Andre ter Stegen": "Barcelona", "Antonio Rudiger": "Real Madrid",
    "Jonathan Tah": "Bayern Munich", "Joshua Kimmich": "Bayern Munich",
    "Aleksandar Pavlovic": "Bayern Munich", "Florian Wirtz": "Liverpool",
    "Jamal Musiala": "Bayern Munich", "Kai Havertz": "Arsenal",
    "Leroy Sane": "Galatasaray",
    # Morocco
    "Yassine Bounou": "Al-Hilal", "Achraf Hakimi": "PSG",
    "Nayef Aguerd": "Real Sociedad", "Noussair Mazraoui": "Manchester United",
    "Sofyan Amrabat": "Fiorentina", "Azzedine Ounahi": "Panathinaikos",
    "Brahim Diaz": "Real Madrid", "Hakim Ziyech": "Al-Duhail",
    "Youssef En-Nesyri": "Fenerbahce",
    # Belgium
    "Thibaut Courtois": "Real Madrid", "Wout Faes": "Leicester",
    "Timothy Castagne": "Fulham", "Zeno Debast": "Sporting CP",
    "Kevin De Bruyne": "Napoli", "Youri Tielemans": "Aston Villa",
    "Amadou Onana": "Aston Villa", "Jeremy Doku": "Manchester City",
    "Romelu Lukaku": "Napoli", "Leandro Trossard": "Arsenal",
    # Croatia
    "Dominik Livakovic": "Fenerbahce", "Josko Gvardiol": "Manchester City",
    "Josip Stanisic": "Bayer Leverkusen", "Luka Modric": "AC Milan",
    "Mateo Kovacic": "Manchester City", "Marcelo Brozovic": "Al-Nassr",
    "Andrej Kramaric": "Hoffenheim", "Ante Budimir": "Osasuna",
    # Colombia
    "Camilo Vargas": "Atlas", "Daniel Munoz": "Crystal Palace",
    "Davinson Sanchez": "Galatasaray", "James Rodriguez": "Rayo Vallecano",
    "Richard Rios": "Benfica", "Luis Diaz": "Bayern Munich",
    "Jhon Duran": "Fenerbahce", "Jhon Cordoba": "Krasnodar",
    # Uruguay
    "Sergio Rochet": "Internacional", "Ronald Araujo": "Barcelona",
    "Jose Maria Gimenez": "Atletico Madrid", "Federico Valverde": "Real Madrid",
    "Manuel Ugarte": "Manchester United", "Nicolas De La Cruz": "Flamengo",
    "Darwin Nunez": "Al-Hilal", "Facundo Pellistri": "Panathinaikos",
    # Mexico
    "Luis Malagon": "Club America", "Cesar Montes": "Almeria",
    "Jorge Sanchez": "Cruz Azul", "Edson Alvarez": "Fenerbahce",
    "Luis Chavez": "Dynamo Moscow", "Hirving Lozano": "San Diego FC",
    "Santiago Gimenez": "AC Milan", "Raul Jimenez": "Fulham",
    # Senegal
    "Edouard Mendy": "Al-Ahli", "Kalidou Koulibaly": "Al-Hilal",
    "Abdou Diallo": "Al-Arabi", "Idrissa Gana Gueye": "Everton",
    "Pape Matar Sarr": "Tottenham", "Sadio Mane": "Al-Nassr",
    "Ismaila Sarr": "Crystal Palace", "Nicolas Jackson": "Bayern Munich",
    # USA
    "Matt Turner": "Lyon", "Antonee Robinson": "Fulham",
    "Chris Richards": "Crystal Palace", "Tyler Adams": "Bournemouth",
    "Weston McKennie": "Juventus", "Yunus Musah": "AC Milan",
    "Gio Reyna": "Borussia Monchengladbach", "Christian Pulisic": "AC Milan",
    "Folarin Balogun": "Monaco",
    # Japan
    "Zion Suzuki": "Parma", "Ko Itakura": "Ajax",
    "Takehiro Tomiyasu": "Arsenal", "Wataru Endo": "Liverpool",
    "Hidemasa Morita": "Sporting CP", "Takefusa Kubo": "Real Sociedad",
    "Kaoru Mitoma": "Brighton", "Ritsu Doan": "Eintracht Frankfurt",
    # Switzerland
    "Yann Sommer": "Inter Milan", "Manuel Akanji": "Manchester City",
    "Nico Elvedi": "Borussia Monchengladbach", "Granit Xhaka": "Sunderland",
    "Remo Freuler": "Bologna", "Dan Ndoye": "Nottingham Forest",
    "Breel Embolo": "Rennes",
    # Norway
    "Orjan Nyland": "Sevilla", "Kristoffer Ajer": "Brentford",
    "Leo Ostigard": "Rennes", "Martin Odegaard": "Arsenal",
    "Sander Berge": "Fulham", "Erling Haaland": "Manchester City",
    "Alexander Sorloth": "Atletico Madrid",
    # Turkiye
    "Ugurcan Cakir": "Galatasaray", "Merih Demiral": "Al-Ahli",
    "Kaan Ayhan": "Galatasaray", "Hakan Calhanoglu": "Inter Milan",
    "Orkun Kokcu": "Benfica", "Arda Guler": "Real Madrid",
    "Kenan Yildiz": "Juventus",
    # Ecuador
    "Hernan Galindez": "Huracan", "Piero Hincapie": "Arsenal",
    "William Pacho": "PSG", "Moises Caicedo": "Chelsea",
    "Kendry Paez": "Chelsea", "Enner Valencia": "Internacional",
    # Austria
    "Patrick Pentz": "Brondby", "David Alaba": "Real Madrid",
    "Kevin Danso": "Tottenham", "Konrad Laimer": "Bayern Munich",
    "Nicolas Seiwald": "RB Leipzig", "Christoph Baumgartner": "RB Leipzig",
    "Marcel Sabitzer": "Borussia Dortmund", "Marko Arnautovic": "Red Star Belgrade",
    # South Korea
    "Kim Seung-gyu": "Al-Shabab", "Kim Min-jae": "Bayern Munich",
    "Kim Young-gwon": "Ulsan HD", "Hwang In-beom": "Feyenoord",
    "Lee Kang-in": "PSG", "Hwang Hee-chan": "Wolves",
    "Son Heung-min": "LAFC",
    # Australia
    "Mathew Ryan": "Lens", "Harry Souttar": "Sheffield United",
    "Jackson Irvine": "St. Pauli", "Aiden ONeill": "Standard Liege",
    "Martin Boyle": "Hibernian", "Mitchell Duke": "Machida Zelvia",
    # Algeria
    "Rais MBolhi": "CR Belouizdad", "Aissa Mandi": "Lille",
    "Ramy Bensebaini": "Borussia Dortmund", "Ismael Bennacer": "AC Milan",
    "Riyad Mahrez": "Al-Ahli", "Said Benrahma": "Lyon",
    # Egypt
    "Mohamed El Shenawy": "Al-Ahly", "Mohamed Abdelmonem": "Al-Ahly",
    "Mohamed Elneny": "Al-Jazira", "Mohamed Salah": "Liverpool",
    "Omar Marmoush": "Manchester City", "Trezeguet": "Trabzonspor",
    # Iran
    "Alireza Beiranvand": "Tractor", "Shojae Khalilzadeh": "Tractor",
    "Saeid Ezatolahi": "Shabab Al-Ahli", "Alireza Jahanbakhsh": "Heerenveen",
    "Saman Ghoddos": "Brentford", "Mehdi Taremi": "Inter Milan",
    "Sardar Azmoun": "Shabab Al-Ahli",
    # Canada
    "Maxime Crepeau": "Portland Timbers", "Alphonso Davies": "Bayern Munich",
    "Moise Bombito": "Nice", "Stephen Eustaquio": "Porto",
    "Jonathan David": "Juventus", "Cyle Larin": "Mallorca",
    "Tajon Buchanan": "Villarreal",
    # Ivory Coast
    "Yahia Fofana": "Angers", "Odilon Kossounou": "Atalanta",
    "Evan Ndicka": "Roma", "Franck Kessie": "Al-Ahli",
    "Seko Fofana": "Al-Nassr", "Simon Adingra": "Sunderland",
    "Sebastien Haller": "Utrecht",
    # Sweden
    "Robin Olsen": "Malmo", "Victor Lindelof": "Aston Villa",
    "Emil Krafth": "Newcastle", "Dejan Kulusevski": "Tottenham",
    "Anthony Elanga": "Newcastle", "Alexander Isak": "Liverpool",
    "Viktor Gyokeres": "Arsenal",
    # Czechia
    "Jindrich Stanek": "Slavia Prague", "Ladislav Krejci": "Girona",
    "David Doudera": "Slavia Prague", "Tomas Soucek": "West Ham",
    "Antonin Barak": "Fiorentina", "Patrik Schick": "Bayer Leverkusen",
    "Adam Hlozek": "Hoffenheim",
    # Ghana
    "Lawrence Ati-Zigi": "St. Gallen", "Alexander Djiku": "Fenerbahce",
    "Mohammed Kudus": "Tottenham", "Thomas Partey": "Villarreal",
    "Jordan Ayew": "Leicester", "Inaki Williams": "Athletic Bilbao",
    "Antoine Semenyo": "Bournemouth",
    # Bosnia-Herzegovina
    "Ibrahim Sehic": "Konyaspor", "Sead Kolasinac": "Atalanta",
    "Miralem Pjanic": "Al-Wahda", "Edin Dzeko": "Fiorentina",
    "Ermedin Demirovic": "Stuttgart",
    # Paraguay
    "Roberto Fernandez": "Braga", "Gustavo Gomez": "Palmeiras",
    "Omar Alderete": "Sunderland", "Miguel Almiron": "Atlanta United",
    "Julio Enciso": "Brighton", "Antonio Sanabria": "Torino",
    # Scotland
    "Angus Gunn": "Norwich", "Andy Robertson": "Liverpool",
    "Kieran Tierney": "Celtic", "Scott McTominay": "Napoli",
    "Billy Gilmour": "Napoli", "John McGinn": "Aston Villa",
    "Che Adams": "Torino",
    # Tunisia
    "Aymen Dahmen": "CS Sfaxien", "Montassar Talbi": "Al-Khaleej",
    "Ali Abdi": "Caen", "Aissa Laidouni": "Al-Wakrah",
    "Hannibal Mejbri": "Burnley", "Elias Achouri": "Copenhagen",
    "Youssef Msakni": "Al-Arabi",
    # DR Congo
    "Lionel Mpasi": "Rodez", "Chancel Mbemba": "Lille",
    "Arthur Masuaku": "Sunderland", "Charles Pickel": "Cremonese",
    "Yoane Wissa": "Newcastle", "Silas Katompa": "VfB Stuttgart",
    "Cedric Bakambu": "Real Betis",
    # Qatar
    "Meshaal Barsham": "Al-Sadd", "Boualem Khoukhi": "Al-Sadd",
    "Hassan Al-Haydos": "Al-Sadd", "Akram Afif": "Al-Sadd",
    "Almoez Ali": "Al-Duhail",
    # Saudi Arabia
    "Mohammed Al-Owais": "Al-Hilal", "Ali Al-Bulaihi": "Al-Hilal",
    "Salman Al-Faraj": "Al-Hilal", "Salem Al-Dawsari": "Al-Hilal",
    "Firas Al-Buraikan": "Al-Ahli",
    # South Africa
    "Ronwen Williams": "Mamelodi Sundowns", "Siyanda Xulu": "Sekhukhune United",
    "Teboho Mokoena": "Mamelodi Sundowns", "Percy Tau": "Qatar SC",
    "Lyle Foster": "Burnley",
    # Iraq
    "Jalal Hassan": "Al-Quwa Al-Jawiya", "Rebin Sulaka": "Apollon",
    "Amir Al-Ammari": "Halmstad", "Aymen Hussein": "Al-Najma",
    "Ali Al-Hamadi": "Ipswich",
    # Uzbekistan
    "Utkir Yusupov": "Pakhtakor", "Abdukodir Khusanov": "Manchester City",
    "Jaloliddin Masharipov": "Pakhtakor", "Abbosbek Fayzullaev": "CSKA Moscow",
    "Eldor Shomurodov": "Roma",
    # Panama
    "Orlando Mosquera": "Antalyaspor", "Andres Andrade": "Atletico Nacional",
    "Adalberto Carrasquilla": "Houston Dynamo", "Ismael Diaz": "Club Leon",
    "Jose Fajardo": "Independiente",
    # Jordan
    "Yazeed Abulaila": "Al-Faisaly", "Yazan Al-Arab": "Al-Wehdat",
    "Noor Al-Rawabdeh": "Al-Faisaly", "Mousa Al-Tamari": "Montpellier",
    "Yazan Al-Naimat": "Al-Ahli (JOR)",
    # Cape Verde
    "Vozinha": "Casa Pia", "Roberto Lopes": "Shamrock Rovers",
    "Kenny Rocha": "Estoril", "Garry Rodrigues": "PAOK",
    "Ryan Mendes": "Al-Wakrah",
    # New Zealand
    "Max Crocombe": "Salford City", "Michael Boxall": "Minnesota United",
    "Marko Stamenic": "Olympiacos", "Chris Wood": "Nottingham Forest",
    "Ben Waine": "Plymouth Argyle",
    # Curacao
    "Eloy Room": "Columbus Crew", "Cuco Martina": "PEC Zwolle",
    "Leandro Bacuna": "Sivasspor", "Juninho Bacuna": "Hatayspor",
    "Tahith Chong": "Luton Town",
    # Haiti
    "Johny Placide": "Valenciennes", "Ricardo Ade": "Bari",
    "Danley Jean Jacques": "Metz", "Frantzdy Pierrot": "Gaziantep",
    "Duckens Nazon": "Sint-Truiden",
}

# A genuine PSG + Portugal starter the curated squad was missing; adding him
# completes the PSG chemistry core (Nuno Mendes, Vitinha, Goncalo Ramos).
NEW_PLAYERS = {
    "Portugal": [{"name": "Joao Neves", "pos": "MID", "rating": 85}],
}


def main() -> None:
    raw = json.loads(DATA.read_text(encoding="utf-8"))

    added = 0
    for entry in raw["teams"]:
        for extra in NEW_PLAYERS.get(entry["name"], []):
            if not any(p["name"] == extra["name"] for p in entry["players"]):
                p = dict(extra)
                p["attributes"] = generate_attributes(p["name"], p["pos"], float(p["rating"]))
                entry["players"].append(p)
                added += 1

    tagged = missing = 0
    unknown: list[str] = []
    for entry in raw["teams"]:
        for p in entry["players"]:
            club = CLUBS.get(p["name"])
            if club:
                p["club"] = club
                tagged += 1
            else:
                p.setdefault("club", "")
                missing += 1
                unknown.append(f"{entry['name']}: {p['name']}")

    DATA.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"added {added} new player(s); tagged {tagged} clubs, {missing} left blank")
    if unknown:
        print("no club for:", ", ".join(unknown))


if __name__ == "__main__":
    main()
