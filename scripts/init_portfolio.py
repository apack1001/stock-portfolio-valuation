#!/usr/bin/env python3
"""
Initialize local files for the stock-portfolio-valuation skill.

Usage:
  python3 scripts/init_portfolio.py
  python3 scripts/init_portfolio.py --with-optional-files
"""
import argparse
import csv
from pathlib import Path

BASE_DIR = Path.home() / "Desktop/持仓"
DETAIL_PATH = BASE_DIR / "明细.csv"
TOTAL_PATH = BASE_DIR / "总额.csv"
REALIZED_PATH = BASE_DIR / "已实现盈亏.csv"
CASHFLOW_PATH = BASE_DIR / "未来现金流.csv"
PROFILE_PATH = BASE_DIR / "profile.json"


def ensure_csv(path: Path, header: list[str]) -> bool:
    if path.exists():
        return False
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
    return True


def ensure_profile(path: Path) -> bool:
    if path.exists():
        return False
    path.write_text(
        """{
  "birth_year_month": null,
  "years_worked": null,
  "annual_spending_cny": null,
  "annual_savings_cny": null,
  "retirement_age": 63,
  "lifespan_age": 79,
  "current_social_security_personal_account_cny": null,
  "historical_contribution_multiple": null,
  "future_contribution_multiple": null,
  "beijing_average_monthly_salary_cny_today": 12000
}
""",
        encoding="utf-8",
    )
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--with-optional-files",
        action="store_true",
        help="also create 总额.csv / 已实现盈亏.csv / 未来现金流.csv",
    )
    args = parser.parse_args()

    BASE_DIR.mkdir(parents=True, exist_ok=True)

    created = []
    if ensure_csv(
        DETAIL_PATH,
        [
            "account",
            "category",
            "name",
            "code",
            "market",
            "currency",
            "shares",
            "cost_price",
            "cost_total",
            "last_market_value",
            "last_pnl",
            "last_updated",
            "note",
        ],
    ):
        created.append(str(DETAIL_PATH))

    if args.with_optional_files:
        optional_specs = [
            (
                TOTAL_PATH,
                [
                    "date",
                    "total_usd",
                    "total_cny",
                    "usd_mv",
                    "hkd_mv",
                    "cny_mv",
                    "usd_cny_rate",
                    "hkd_cny_rate",
                    "pnl_usd_excl_lti",
                    "pnl_cny_excl_lti",
                ],
            ),
            (
                REALIZED_PATH,
                ["date", "name", "code", "market", "currency", "realized_pnl", "note"],
            ),
            (
                CASHFLOW_PATH,
                ["date", "type", "name", "currency", "amount", "note"],
            ),
        ]
        for path, header in optional_specs:
            if ensure_csv(path, header):
                created.append(str(path))
        if ensure_profile(PROFILE_PATH):
            created.append(str(PROFILE_PATH))

    print(f"initialized_dir={BASE_DIR}")
    if created:
        for path in created:
            print(f"created={path}")
    else:
        print("created=none")
    print("next_step=you can now paste screenshots or describe holdings in natural language to fill 明细.csv")
    print("next_step_2=if you want retirement planning, fill profile.json or run scripts/profile_manager.py")


if __name__ == "__main__":
    main()
