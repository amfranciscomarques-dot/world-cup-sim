import json
from worldcup.calibrate import calculate_metrics
from worldcup.data_loader import load_world_cup
from worldcup.engine import poisson

def grid_search():
    with open("data/historical/curated_wc.json", "r", encoding='utf-8') as f:
        fixtures = json.load(f)
    teams, _ = load_world_cup()
    
    rating_coeffs = [0.025, 0.030, 0.035, 0.040, 0.045]
    base_goals_list = [1.20, 1.30, 1.35, 1.45, 1.55]
    
    best_brier = 999.0
    best_params = (0, 0)
    
    # Save original values
    orig_rc = poisson.RATING_COEFF
    orig_bg = poisson.BASE_GOALS
    
    print("| RATING_COEFF | BASE_GOALS | Brier (1X2) | Log-loss | Goal RMSE |")
    print("| :--- | :--- | :--- | :--- | :--- |")
    
    for rc in rating_coeffs:
        for bg in base_goals_list:
            poisson.RATING_COEFF = rc
            poisson.BASE_GOALS = bg
            
            res = calculate_metrics(fixtures, teams, n=1000, seed=42)
            print(f"| {rc:.3f} | {bg:.2f} | {res.brier_1x2:.5f} | {res.log_loss_1x2:.5f} | {res.rmse_goals:.5f} |")
            
            if res.brier_1x2 < best_brier:
                best_brier = res.brier_1x2
                best_params = (rc, bg)
                
    # Restore (though script ends)
    poisson.RATING_COEFF = orig_rc
    poisson.BASE_GOALS = orig_bg
    
    print(f"\nBest: RATING_COEFF={best_params[0]}, BASE_GOALS={best_params[1]} (Brier={best_brier:.5f})")

if __name__ == "__main__":
    grid_search()
