#!/usr/bin/env python3
"""
Create or update ~/Desktop/持仓/profile.json for retirement planning.

Usage:
  python3 scripts/profile_manager.py --show
  python3 scripts/profile_manager.py --birth-year-month 1989-07 --years-worked 14.5 \
    --annual-spending-cny 400000 --annual-savings-cny 500000
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path.home() / "Desktop/持仓"
PROFILE_PATH = BASE_DIR / "profile.json"

DEFAULT_PROFILE = {
    "birth_year_month": None,
    "years_worked": None,
    "annual_spending_cny": None,
    "annual_savings_cny": None,
    "retirement_age": 63,
    "lifespan_age": 79,
    "current_social_security_personal_account_cny": None,
    "historical_contribution_multiple": None,
    "future_contribution_multiple": None,
    "beijing_average_monthly_salary_cny_today": 12000,
}


def load_profile():
    if not PROFILE_PATH.exists():
        return DEFAULT_PROFILE.copy()
    with open(PROFILE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    merged = DEFAULT_PROFILE.copy()
    merged.update(data)
    return merged


def save_profile(profile):
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--show", action="store_true", help="print current profile.json")
    parser.add_argument("--birth-year-month")
    parser.add_argument("--years-worked", type=float)
    parser.add_argument("--annual-spending-cny", type=float)
    parser.add_argument("--annual-savings-cny", type=float)
    parser.add_argument("--retirement-age", type=int)
    parser.add_argument("--lifespan-age", type=int)
    parser.add_argument("--current-social-security-personal-account-cny", type=float)
    parser.add_argument("--historical-contribution-multiple", type=float)
    parser.add_argument("--future-contribution-multiple", type=float)
    parser.add_argument("--beijing-average-monthly-salary-cny-today", type=float)
    args = parser.parse_args()

    profile = load_profile()

    updates = {
        "birth_year_month": args.birth_year_month,
        "years_worked": args.years_worked,
        "annual_spending_cny": args.annual_spending_cny,
        "annual_savings_cny": args.annual_savings_cny,
        "retirement_age": args.retirement_age,
        "lifespan_age": args.lifespan_age,
        "current_social_security_personal_account_cny": args.current_social_security_personal_account_cny,
        "historical_contribution_multiple": args.historical_contribution_multiple,
        "future_contribution_multiple": args.future_contribution_multiple,
        "beijing_average_monthly_salary_cny_today": args.beijing_average_monthly_salary_cny_today,
    }
    changed = False
    for key, value in updates.items():
        if value is not None:
            profile[key] = value
            changed = True

    if changed or not PROFILE_PATH.exists():
        save_profile(profile)

    if args.show or changed or not PROFILE_PATH.exists():
        print(json.dumps(profile, ensure_ascii=False, indent=2))
    else:
        print(f"profile_path={PROFILE_PATH}")


if __name__ == "__main__":
    main()
