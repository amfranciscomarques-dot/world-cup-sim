import json
from datetime import datetime

def build_fixtures():
    with open("data/results_2026.json", "r", encoding='utf-8') as f:
        results = json.load(f)["results"]
    
    with open("data/tournament_2026.json", "r", encoding='utf-8') as f:
        tournament = json.load(f)
        groups = tournament["groups"]
    
    fixtures = []
    # Played fixtures
    for i, r in enumerate(results):
        fixtures.append({
            "match_id": f"played_{i}",
            "stage": r["stage"],
            "date": r["date"],
            "venue": "TBD",
            "home": r["home"],
            "away": r["away"],
            "kickoff_local": "18:00"
        })
    
    # We could populate the rest of the schedule here if we had it.
    # For now, this is enough to track fatigue for played games.
    
    with open("data/fixtures_2026.json", "w", encoding='utf-8') as f:
        json.dump(fixtures, f, indent=2)

if __name__ == "__main__":
    build_fixtures()
