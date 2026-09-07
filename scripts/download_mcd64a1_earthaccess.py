#!/usr/bin/env python3
"""Search and optionally download MCD64A1.061 with NASA earthaccess."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SHORT_NAME = "MCD64A1"
VERSION = "061"
DEFAULT_BBOX = (-82.0, -21.0, -49.0, 6.0) # exact boundary of the LDAS runs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search or download MCD64A1.061 granules by year.")
    parser.add_argument("--bbox", nargs=4, type=float, default=4,
                        metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    parser.add_argument("r", type=int, default=2001)
    parser.add_argument("--end-year", type=int, default=2022)
    parser.add_argument("--output-dir", type=Path, default=Path("mcd64a1_raw"))
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--login-strategy",
                        choices=("environment", "netrc", "interactive"), default="netrc")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    west, south, east, north = args.bbox
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("invalid longitude/latitude bounding box")
    if args.start_year > args.end_year:
        raise ValueError("start-year must not exceed end-year")


def granule_record(granule: Any) -> dict[str, Any]:
    meta = granule.get("meta", {})
    umm = granule.get("umm", {})
    temporal = umm.get("TemporalExtent", {}).get("RangeDateTime", {})
    return {
        "concept_id": meta.get("concept-id"),
        "granule_ur": umm.get("GranuleUR"),
        "beginning_datetime": temporal.get("BeginningDateTime"),
        "ending_datetime": temporal.get("EndingDateTime"),
        "data_links": granule.data_links(access="external"),
    }


def main() -> None:
    args = parse_args()
    validate_args(args)
    if args.dry_run:
        print(f"Dataset: {SHORT_NAME}.{VERSION}")
        print(f"Bounding box: {tuple(args.bbox)}")
        for year in range(args.start_year, args.end_year + 1):
            print(f"{year}: search {year}-01-01 through {year}-12-31")
        return

    import earthaccess
    if args.download:
        earthaccess.login(strategy=args.login_strategy, persist=True)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for year in range(args.start_year, args.end_year + 1):
        granules = earthaccess.search_data(
            short_name=SHORT_NAME,
            version=VERSION,
            temporal=(f"{year}-01-01", f"{year}-12-31T23:59:59"),
            bounding_box=tuple(args.bbox),
        )
        records = [granule_record(granule) for granule in granules]
        manifest = {"dataset": f"{SHORT_NAME}.{VERSION}", "year": year,
                    "bbox": list(args.bbox), "granule_count": len(records),
                    "granules": records}
        manifest_path = args.output_dir / f"manifest_{year}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        total += len(granules)
        print(f"{year}: {len(granules)} granules; manifest={manifest_path}")
        if args.download and granules:
            year_dir = args.output_dir / str(year)
            year_dir.mkdir(exist_ok=True)
            earthaccess.download(granules, year_dir)
    print(f"Total matched granules across all years: {total}")


if __name__ == "__main__":
    main()
