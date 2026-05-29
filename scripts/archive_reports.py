#!/usr/bin/env python3
"""Archive portfolio calculation outputs into ~/Desktop/持仓/测算归档."""
import argparse
import shutil
from datetime import datetime
from pathlib import Path


ARCHIVE_ROOT = Path.home() / "Desktop/持仓/测算归档"


def archive_file(src: Path, category: str, prefix: str = ""):
    if not src.exists():
        raise FileNotFoundError(src)
    target_dir = ARCHIVE_ROOT / category
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{prefix}{src.name}" if prefix else src.name
    target = target_dir / name
    if target.exists():
        target = target_dir / f"{timestamp}_{name}"
    shutil.copy2(src, target)
    return target


def archive_tmp_defaults():
    patterns = [
        ("估值快照", "portfolio_refresh_*.json"),
        ("退休测算", "retirement*.json"),
        ("港股IPO", "hk_ipo_*.csv"),
        ("港股IPO", "aastocks_listedipo.html"),
    ]
    archived = []
    for category, pattern in patterns:
        for src in sorted(Path("/tmp").glob(pattern)):
            archived.append(archive_file(src, category))
    return archived


def main():
    parser = argparse.ArgumentParser(description="Archive calculation outputs to the local portfolio folder.")
    parser.add_argument("--from-tmp", action="store_true", help="Archive known portfolio files from /tmp.")
    parser.add_argument("--valuation-json", help="Path to a valuation JSON file.")
    parser.add_argument("--retirement-json", help="Path to a retirement projection JSON file.")
    parser.add_argument("--ipo-csv", help="Path to a Hong Kong IPO CSV file.")
    args = parser.parse_args()

    archived = []
    if args.from_tmp:
        archived.extend(archive_tmp_defaults())
    if args.valuation_json:
        archived.append(archive_file(Path(args.valuation_json), "估值快照"))
    if args.retirement_json:
        archived.append(archive_file(Path(args.retirement_json), "退休测算"))
    if args.ipo_csv:
        archived.append(archive_file(Path(args.ipo_csv), "港股IPO"))

    for path in archived:
        print(path)


if __name__ == "__main__":
    main()
