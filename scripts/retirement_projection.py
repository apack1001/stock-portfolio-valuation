#!/usr/bin/env python3
"""
Estimate earliest no-work age from profile.json, 总额.csv and 未来现金流.csv.
"""
import argparse
import csv
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

BASE_DIR = Path.home() / "Desktop/持仓"
PROFILE_PATH = BASE_DIR / "profile.json"
TOTAL_PATH = BASE_DIR / "总额.csv"
CASHFLOW_PATH = BASE_DIR / "未来现金流.csv"

PERSONAL_ACCOUNT_FACTORS = {
    60: 139,
    61: 132,
    62: 125,
    63: 117,
    64: 109,
    65: 101,
}

REQUIRED_PROFILE_FIELDS = (
    "birth_year_month",
    "years_worked",
    "annual_spending_cny",
    "annual_savings_cny",
)


@dataclass
class Profile:
    birth_year_month: str
    years_worked: float
    annual_spending_cny: float
    annual_savings_cny: float
    retirement_age: int = 63
    lifespan_age: int = 79
    current_social_security_personal_account_cny: float = 0.0
    historical_contribution_multiple: float | None = None
    future_contribution_multiple: float | None = None
    beijing_average_monthly_salary_cny_today: float = 12000.0


def load_json(path: Path):
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_profile() -> Profile:
    data = load_json(PROFILE_PATH)
    if data is None:
        raise FileNotFoundError(f"profile.json not found: {PROFILE_PATH}")
    missing = [field for field in REQUIRED_PROFILE_FIELDS if data.get(field) in (None, "")]
    if missing:
        raise ValueError(f"profile.json 缺少字段: {', '.join(missing)}")
    return Profile(
        birth_year_month=data["birth_year_month"],
        years_worked=float(data["years_worked"]),
        annual_spending_cny=float(data["annual_spending_cny"]),
        annual_savings_cny=float(data["annual_savings_cny"]),
        retirement_age=int(data.get("retirement_age") or 63),
        lifespan_age=int(data.get("lifespan_age") or 79),
        current_social_security_personal_account_cny=float(data.get("current_social_security_personal_account_cny") or 0),
        historical_contribution_multiple=(
            float(data["historical_contribution_multiple"])
            if data.get("historical_contribution_multiple") not in (None, "")
            else None
        ),
        future_contribution_multiple=(
            float(data["future_contribution_multiple"])
            if data.get("future_contribution_multiple") not in (None, "")
            else None
        ),
        beijing_average_monthly_salary_cny_today=float(
            data.get("beijing_average_monthly_salary_cny_today") or 12000
        ),
    )


def latest_total_assets_cny():
    if not TOTAL_PATH.exists():
        return None
    with open(TOTAL_PATH, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return None
    return {
        "date": rows[-1].get("date", ""),
        "total_cny": float(rows[-1]["total_cny"]),
    }


def load_cashflows():
    if not CASHFLOW_PATH.exists():
        return []
    with open(CASHFLOW_PATH, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def age_years(birth_ym: str, today: date) -> float:
    birth = datetime.strptime(birth_ym + "-01", "%Y-%m-%d").date()
    months = (today.year - birth.year) * 12 + (today.month - birth.month)
    if today.day < birth.day:
        months -= 1
    return months / 12


def annual_cashflow_for_year(rows, year: int) -> float:
    total = 0.0
    for row in rows:
        if (row.get("currency") or "CNY") != "CNY":
            continue
        start = row.get("start_date", "")
        end = row.get("end_date", "")
        freq = (row.get("frequency") or "").lower()
        amount = float(row.get("amount") or 0)
        if not start or not end or freq != "yearly":
            continue
        start_year = int(start[:4])
        end_year = int(end[:4])
        if start_year <= year <= end_year:
            total += amount
    return total


def estimate_annual_pension(profile: Profile, current_age: float, stop_age: int):
    future_work_years = max(0.0, stop_age - current_age)
    current_ss = profile.current_social_security_personal_account_cny

    annual_account_contrib = 0.0
    projected_account = current_ss
    if (
        current_ss > 0
        and profile.years_worked > 0
        and profile.historical_contribution_multiple
        and profile.future_contribution_multiple
    ):
        annual_account_contrib = (
            current_ss / profile.years_worked
        ) * (profile.future_contribution_multiple / profile.historical_contribution_multiple)
        projected_account = current_ss + annual_account_contrib * future_work_years

    total_years = profile.years_worked + future_work_years
    if total_years <= 0:
        return {
            "annual_pension_cny": 0.0,
            "base_monthly_pension_cny": 0.0,
            "personal_monthly_pension_cny": 0.0,
            "projected_social_security_account_cny": projected_account,
        }

    if profile.historical_contribution_multiple and profile.future_contribution_multiple:
        weighted_multiple = (
            profile.years_worked * profile.historical_contribution_multiple
            + future_work_years * profile.future_contribution_multiple
        ) / total_years
    else:
        weighted_multiple = 1.0

    base_monthly = (
        profile.beijing_average_monthly_salary_cny_today
        * (1 + weighted_multiple)
        / 2
        * total_years
        * 0.01
    )
    factor = PERSONAL_ACCOUNT_FACTORS.get(profile.retirement_age, 117)
    personal_monthly = projected_account / factor if factor > 0 else 0.0
    annual_pension = (base_monthly + personal_monthly) * 12
    return {
        "annual_pension_cny": annual_pension,
        "base_monthly_pension_cny": base_monthly,
        "personal_monthly_pension_cny": personal_monthly,
        "projected_social_security_account_cny": projected_account,
    }


def simulate_for_stop_age(profile: Profile, current_age: float, current_total_assets_cny: float, stop_age: int, cashflows):
    bridge_assets = current_total_assets_cny - profile.current_social_security_personal_account_cny
    future_work_years = max(0.0, stop_age - current_age)
    assets_at_stop = bridge_assets + future_work_years * profile.annual_savings_cny
    pension = estimate_annual_pension(profile, current_age, stop_age)

    birth_year = int(profile.birth_year_month.split("-")[0])
    stop_year = birth_year + stop_age
    retirement_year = birth_year + profile.retirement_age
    end_year = birth_year + profile.lifespan_age

    assets = assets_at_stop
    yearly_rows = []
    for year in range(stop_year, end_year):
        extra_cashflow = annual_cashflow_for_year(cashflows, year)
        annual_income = extra_cashflow
        if year >= retirement_year:
            annual_income += pension["annual_pension_cny"]
        annual_gap = profile.annual_spending_cny - annual_income
        assets -= annual_gap
        yearly_rows.append(
            {
                "year": year,
                "annual_gap_cny": round(annual_gap, 2),
                "ending_assets_cny": round(assets, 2),
            }
        )

    return {
        "stop_age": stop_age,
        "assets_at_stop_cny": round(assets_at_stop, 2),
        "ending_assets_cny": round(assets, 2),
        "annual_pension_cny": round(pension["annual_pension_cny"], 2),
        "base_monthly_pension_cny": round(pension["base_monthly_pension_cny"], 2),
        "personal_monthly_pension_cny": round(pension["personal_monthly_pension_cny"], 2),
        "projected_social_security_account_cny": round(
            pension["projected_social_security_account_cny"], 2
        ),
        "yearly_projection": yearly_rows,
    }


def earliest_no_work_age(profile: Profile, current_age: float, current_total_assets_cny: float, cashflows):
    start_age = max(int(current_age), int(current_age) + (0 if current_age.is_integer() else 1))
    result = None
    for stop_age in range(start_age, profile.lifespan_age + 1):
        projected = simulate_for_stop_age(
            profile, current_age, current_total_assets_cny, stop_age, cashflows
        )
        if projected["ending_assets_cny"] >= 0:
            result = projected
            break
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--current-total-assets-cny", type=float)
    parser.add_argument("--stop-age", type=int, help="simulate a specific stop-working age")
    args = parser.parse_args()

    profile = load_profile()
    today = date.today()
    total_snapshot = latest_total_assets_cny()
    if args.current_total_assets_cny is not None:
        current_total_assets_cny = args.current_total_assets_cny
        current_total_assets_source_date = today.isoformat()
    elif total_snapshot is not None:
        current_total_assets_cny = total_snapshot["total_cny"]
        current_total_assets_source_date = total_snapshot["date"]
    else:
        current_total_assets_cny = None
        current_total_assets_source_date = ""
    if current_total_assets_cny is None:
        raise FileNotFoundError("总额.csv 缺失，且未提供 --current-total-assets-cny")

    current_age = age_years(profile.birth_year_month, today)
    cashflows = load_cashflows()

    summary = {
        "current_date": today.isoformat(),
        "current_age_years": round(current_age, 2),
        "current_total_assets_cny": round(current_total_assets_cny, 2),
        "current_total_assets_source_date": current_total_assets_source_date,
        "bridge_assets_excluding_social_security_cny": round(
            current_total_assets_cny - profile.current_social_security_personal_account_cny,
            2,
        ),
        "profile": profile.__dict__,
    }

    if args.stop_age is not None:
        projection = simulate_for_stop_age(
            profile, current_age, current_total_assets_cny, args.stop_age, cashflows
        )
        summary["projection"] = projection
    else:
        earliest = earliest_no_work_age(profile, current_age, current_total_assets_cny, cashflows)
        summary["earliest_no_work_projection"] = earliest
        summary["scenario_table"] = [
            simulate_for_stop_age(profile, current_age, current_total_assets_cny, age, cashflows)
            for age in sorted({46, 48, 50, 55, 58, 60, profile.retirement_age})
            if age >= int(current_age)
        ]

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
