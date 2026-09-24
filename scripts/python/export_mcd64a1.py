#!/usr/bin/env python3
"""Export QA-filtered monthly MCD64A1 burned-area fractions from Earth Engine.

The script creates one 24-band GeoTIFF export per year and spatial scale.  It
is intended to run on a workstation or remote server after Earth Engine
authentication.  Processing occurs in Earth Engine; the resulting files are
written to Google Drive.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable


DATASET = "MODIS/061/MCD64A1"
START_YEAR = 2001
END_YEAR = 2022
DEFAULT_SCALES = (5_000, 25_000, 50_000)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export annual 24-band GeoTIFFs of monthly MCD64A1 burned-area "
            "fraction and valid coverage at one or more target resolutions."
        )
    )
    region = parser.add_mutually_exclusive_group(required=True)
    region.add_argument(
        "--region-geojson",
        type=Path,
        help="GeoJSON Polygon, MultiPolygon, Feature, or FeatureCollection.",
    )
    region.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        help="Study boundary in longitude/latitude degrees.",
    )
    parser.add_argument("--project", required=True, help="Google Cloud project ID.")
    parser.add_argument(
        "--drive-folder",
        default="amazon_mcd64a1",
        help="Destination Google Drive folder (default: amazon_mcd64a1).",
    )
    parser.add_argument(
        "--scales",
        nargs="+",
        type=int,
        default=list(DEFAULT_SCALES),
        help="Output pixel sizes in metres (default: 5000 25000 50000).",
    )
    parser.add_argument("--start-year", type=int, default=START_YEAR)
    parser.add_argument("--end-year", type=int, default=END_YEAR)
    parser.add_argument(
        "--crs",
        default="EPSG:6933",
        help=(
            "Equal-area output CRS (default: EPSG:6933). Use the LDAS CRS if "
            "exact grid alignment is required."
        ),
    )
    parser.add_argument(
        "--authenticate",
        action="store_true",
        help="Run the interactive Earth Engine authentication flow first.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate arguments and list planned exports without Earth Engine.",
    )
    return parser.parse_args()


def planned_exports(
    start_year: int, end_year: int, scales: Iterable[int]
) -> list[str]:
    if start_year > end_year:
        raise ValueError("start-year must not exceed end-year")
    clean_scales = sorted(set(scales))
    if not clean_scales or any(scale <= 0 for scale in clean_scales):
        raise ValueError("all scales must be positive integers")
    return [
        f"mcd64a1_burn_fraction_{year}_{scale // 1000}km"
        for year in range(start_year, end_year + 1)
        for scale in clean_scales
    ]


def load_region(ee, geojson_path: Path | None, bbox: list[float] | None):
    if bbox is not None:
        return ee.Geometry.Rectangle(bbox, proj="EPSG:4326", geodesic=False)

    with geojson_path.open(encoding="utf-8") as src:
        obj = json.load(src)
    obj_type = obj.get("type")
    if obj_type == "FeatureCollection":
        return ee.FeatureCollection(obj).geometry()
    if obj_type == "Feature":
        return ee.Feature(obj).geometry()
    if obj_type in {"Polygon", "MultiPolygon"}:
        return ee.Geometry(obj)
    raise ValueError(f"unsupported GeoJSON type: {obj_type!r}")


def monthly_burn_metrics(ee, image, scale: int, crs: str):
    """Return area-weighted burned fraction and valid coverage for one month."""
    qa = image.select("QA")
    land = qa.bitwiseAnd(1).eq(1)
    valid = qa.rightShift(1).bitwiseAnd(1).eq(1)
    usable = land.And(valid)
    burned = image.select("BurnDate").gt(0)

    pixel_area = ee.Image.pixelArea()
    burned_area = pixel_area.updateMask(usable.And(burned)).rename("burned_area")
    valid_area = pixel_area.updateMask(usable).rename("valid_area")
    land_area = pixel_area.updateMask(land).rename("land_area")
    areas = burned_area.addBands(valid_area).addBands(land_area).unmask(0)
    totals = areas.reduceResolution(
        reducer=ee.Reducer.sum(), maxPixels=65_536, bestEffort=False
    ).reproject(crs=crs, scale=scale)

    burned_fraction = totals.select("burned_area").divide(
        totals.select("valid_area")
    )
    valid_coverage = totals.select("valid_area").divide(totals.select("land_area"))
    metrics = burned_fraction.rename("burn_fraction").addBands(
        valid_coverage.rename("valid_coverage")
    )
    metrics = metrics.updateMask(
        totals.select("valid_area").gt(0).And(totals.select("land_area").gt(0))
    )
    return metrics.copyProperties(
        image, ["system:time_start"]
    )


def annual_stack(ee, collection, year: int, scale: int, crs: str):
    bands = []
    names = []
    for month in range(1, 13):
        start = ee.Date.fromYMD(year, month, 1)
        end = start.advance(1, "month")
        source = ee.Image(collection.filterDate(start, end).first())
        bands.append(monthly_burn_metrics(ee, source, scale, crs))
        names.extend(
            [
                f"burn_fraction_{year}_{month:02d}",
                f"valid_coverage_{year}_{month:02d}",
            ]
        )
    return ee.Image.cat(bands).rename(names).toFloat()


def main() -> None:
    args = parse_args()
    names = planned_exports(args.start_year, args.end_year, args.scales)
    if args.dry_run:
        print(f"Planned exports: {len(names)}")
        print("\n".join(names))
        return

    import ee

    if args.authenticate:
        ee.Authenticate()
    ee.Initialize(project=args.project)

    boundary = load_region(ee, args.region_geojson, args.bbox)
    collection = ee.ImageCollection(DATASET)
    tasks = []
    for year in range(args.start_year, args.end_year + 1):
        for scale in sorted(set(args.scales)):
            description = f"mcd64a1_burn_fraction_{year}_{scale // 1000}km"
            image = annual_stack(ee, collection, year, scale, args.crs)
            task = ee.batch.Export.image.toDrive(
                image=image,
                description=description,
                folder=args.drive_folder,
                fileNamePrefix=description,
                region=boundary,
                crs=args.crs,
                scale=scale,
                maxPixels=1_000_000_000_000,
                fileFormat="GeoTIFF",
                formatOptions={"cloudOptimized": True, "noData": -9999},
            )
            task.start()
            tasks.append((description, task.id))

    print(f"Started {len(tasks)} Earth Engine export tasks:")
    for description, task_id in tasks:
        print(f"{task_id}\t{description}")


if __name__ == "__main__":
    main()
