"""Phase 1: download CMS Star Ratings data tables and Technical Notes, then inventory them.

Run:
  python download.py              # download (skips files already present) + inventory
  python download.py --inventory  # inventory only
"""
import argparse
import io
import zipfile
from pathlib import Path

import requests

RAW = Path("data/raw")
SOURCES = {
    2026: {
        "tables": "https://www.cms.gov/files/zip/2026-star-ratings-data-tables.zip",
        "notes": "https://www.cms.gov/files/document/2026-star-ratings-technical-notes.pdf",
    },
    2025: {
        "tables": "https://www.cms.gov/files/zip/2025-star-ratings-data-tables.zip",
        "notes": "https://www.cms.gov/files/document/2025-star-ratings-technical-notes.pdf",
    },
    2024: {
        "tables": "https://www.cms.gov/files/zip/2024-star-ratings-data-tables-jul-2-2024.zip",
        "notes": "https://www.cms.gov/files/document/2024-star-ratings-technical-notes.pdf",
    },
}
HEADERS = {"User-Agent": "Mozilla/5.0 (StarsLens portfolio project; research use)"}


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=120)
    r.raise_for_status()
    return r.content


def extract_zip(blob, dest):
    """Extract a zip, recursing into any zips nested inside it."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            if name.endswith("/") or "__MACOSX" in name:
                continue
            data = z.read(name)
            if name.lower().endswith(".zip"):
                extract_zip(data, dest / Path(name).stem)
            else:
                out = dest / Path(name).name
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)


def download():
    for year, src in SOURCES.items():
        ydir = RAW / str(year)
        tables_dir = ydir / "tables"
        notes = ydir / f"{year}_technical_notes.pdf"
        ydir.mkdir(parents=True, exist_ok=True)
        if tables_dir.exists() and any(tables_dir.rglob("*")):
            print(f"{year}: data tables already present, skipping")
        else:
            print(f"{year}: downloading data tables ...", flush=True)
            extract_zip(fetch(src["tables"]), tables_dir)
        if notes.exists():
            print(f"{year}: technical notes already present, skipping")
        else:
            print(f"{year}: downloading technical notes ...", flush=True)
            notes.write_bytes(fetch(src["notes"]))


def inventory():
    print("\n=== INVENTORY ===")
    for year in sorted(SOURCES):
        ydir = RAW / str(year)
        print(f"\n##### {year} #####")
        for f in sorted(ydir.rglob("*")):
            if f.is_dir():
                continue
            rel = f.relative_to(ydir)
            print(f"\n- {rel}  ({f.stat().st_size / 1024:,.0f} KB)")
            if f.suffix.lower() in (".csv", ".txt"):
                text = f.read_bytes()[:4000].decode("latin-1", errors="replace")
                for line in text.splitlines()[:4]:
                    print("    | " + line[:180])
            elif f.suffix.lower() in (".xlsx", ".xls"):
                try:
                    import pandas as pd
                    xl = pd.ExcelFile(f)
                    print(f"    sheets: {xl.sheet_names}")
                except Exception as e:
                    print(f"    (could not open: {e})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", action="store_true", help="skip downloading")
    args = ap.parse_args()
    if not args.inventory:
        download()
    inventory()
