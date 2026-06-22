import json
import os

ALIAS_MAP = {
    "United States": "USA",
    "South Korea": "South Korea",
    "Korea Republic": "South Korea",
    "Türkiye": "Turkiye",
    "Turkey": "Turkiye",
    "Côte d'Ivoire": "Ivory Coast",
    "DR Congo": "DR Congo",
    "Congo DR": "DR Congo",
    "Bosnia and Herzegovina": "Bosnia-Herzegovina",
    "Czech Republic": "Czechia"
}

def normalize(name):
    return ALIAS_MAP.get(name, name)

def process_statsbomb(path):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    results = []
    for m in data:
        results.append({
            "home": normalize(m["home_team"]["home_team_name"]),
            "away": normalize(m["away_team"]["away_team_name"]),
            "home_goals": m["home_score"],
            "away_goals": m["away_score"],
            "date": m["match_date"]
        })
    return results

def main():
    all_results = []
    hist_dir = "data/historical"
    for f in os.listdir(hist_dir):
        if f.endswith(".json"):
            all_results.extend(process_statsbomb(os.path.join(hist_dir, f)))
    
    with open("data/historical/curated_wc.json", "w", encoding='utf-8') as f:
        json.dump(all_results, f, indent=2)

if __name__ == "__main__":
    main()
