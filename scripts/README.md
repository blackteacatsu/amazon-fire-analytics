# MCD64A1 extraction for the wildfire capstone

<!-- This directory contains the first reproducible data step for evaluating Amazon
LDAS wildfire-prediction skill. `export_mcd64a1.py` uses Google Earth Engine to
create QA-filtered, area-weighted monthly burned-area fractions from the NASA
MODIS MCD64A1 Collection 6.1 product.

## Authentication

Earth Engine is not limited to Colab. Register a noncommercial Earth Engine
project, install the Python client on the machine that will submit the exports,
and authenticate once:

```bash
python -m pip install -r wildfire_capstone/requirements.txt
earthengine authenticate
```

The export process runs in Earth Engine. The script can therefore be launched
locally even when the LDAS hindcasts remain on a separate remote server.

## Study boundary

Use the exact LDAS domain as a GeoJSON polygon whenever possible:

```bash
python wildfire_capstone/export_mcd64a1.py \
  --project YOUR_GOOGLE_CLOUD_PROJECT \
  --region-geojson /path/to/ldas_domain.geojson \
  --drive-folder amazon_mcd64a1 \
  --authenticate
```

A longitude/latitude bounding box is also supported:

```bash
python wildfire_capstone/export_mcd64a1.py \
  --project YOUR_GOOGLE_CLOUD_PROJECT \
  --bbox WEST SOUTH EAST NORTH
```

By default, the script creates 66 export tasks: one 24-band GeoTIFF for each
year from 2001 through 2022 at 5, 25, and 50 km. The default output projection
is the equal-area EPSG:6933 CRS. If the LDAS grid uses another projected CRS,
pass it with `--crs`.

Before authenticating or starting tasks, verify the intended job list:

````bash
python wildfire_capstone/export_mcd64a1.py \
  --project placeholder \
  --bbox -82 -20 -44 10 \
  --dry-run
```

## Output definition

Each month contributes two bands. `burn_fraction` is the fraction of valid land
area classified as burned, and `valid_coverage` is valid observed land area
divided by total identified land area. MCD64A1 pixels are retained only when the
QA band identifies land and sufficient observations. Both metrics are calculated
from pixel area rather than unweighted pixel counts. The modeling pipeline should
exclude low-coverage grid-cell months; use 80% as the initial threshold and test
70% and 90% in sensitivity analysis.

The exported GeoTIFFs must subsequently be transferred to the LDAS server and
aligned by `valid_timestamp`, `initialization_timestamp`, and `lead_time`. Do
not interpret the 5-km export as 5-km forecast skill until the native LDAS grid
and geolocation metadata have been confirmed. -->

## NASA Earthdata MODIS - MCD64A1

Use `download_mcd64a1_earthaccess.py` on
a machine with `earthaccess` installed. First search the catalog without downloading:

```bash
python wildfire_capstone/download_mcd64a1_earthaccess.py \
  --bbox WEST SOUTH EAST NORTH
```

This writes one JSON granule manifest per year. After checking the domain and
storage requirement, authenticate with NASA Earthdata Login and download:

```bash
python wildfire_capstone/download_mcd64a1_earthaccess.py \
  --bbox WEST SOUTH EAST NORTH \
  --output-dir /path/to/mcd64a1_raw \
  --download \
  --login-strategy netrc
```

Native MCD64A1 HDF tiles still require mosaicking, QA decoding, reprojection,
clipping, and area-weighted aggregation. The Earth Engine script performs those
transformations server-side; the `earthaccess` route does not.

## Process downloaded HDF tiles

`process_mcd64a1.py` decodes the MCD64A1 land and valid-data QA bits, creates
monthly mosaics, and writes burned fraction and valid coverage at 5, 25, and
50 km in the equal-area EPSG:6933 CRS. It works one month at a time and removes
all intermediate files after that month, limiting temporary disk use.

> **Note** :
> run `df -h .` to check disk usage before proceeding...

Check the archive without producing rasters:

```bash
python process_mcd64a1.py \
  --input-dir ./mcd64a1_raw \
  --start-year 2001 \
  --end-year 2022 \
  --dry-run
```

Process January 2001 as a one-month test first:

```bash
python process_mcd64a1.py \
  --input-dir ./mcd64a1_raw \
  --output-dir ./mcd64a1_monthly \
  --bbox -82 -21 -49 6 \
  --start-year 2001 \
  --end-year 2001 \
  --months 1
```

The full run omits `--months` and uses `--end-year 2022`. Completed months are
skipped on restart. Replace the bounding box with the exact LDAS domain before
the definitive processing run.

Use `--overwrite` only when regenerating outputs after changing processing
settings or code. The source projection is read from the GDAL-translated MODIS
tiles rather than manually overridden.

Processing persistently so an SSH disconnection does not stop it:

```bash
nohup python process_mcd64a1.py \
  --input-dir ./mcd64a1_raw \
  --output-dir ./mcd64a1_monthly \
  --bbox -82 -21 -49 6 \
  --start-year 2001 \
  --end-year 2022 \
  > ./logs/mcd64a1_processing.log 2>&1 &
```

Monitor progress use: `tail -f mcd64a1_processing.log`,
and check output count using `find ./mcd64a1_monthly -type f -name '*.tif' | wc -l`.
Expected 1584 files in under this directory, since 264 months (12*22 years) * 3 resolution \* 2 variables = 1584.

## Build analysis-ready cubes and extreme-fire labels

After all 1,584 monthly GeoTIFFs have been created,
`build_extreme_fire_cubes.py` produces one CF-style NetCDF cube at each
resolution. It applies the 80% valid-coverage filter, calculates the cellwise
90th percentile separately for each calendar month, requires at least 15 valid
years for a threshold, and creates binary extreme-fire labels. The conservative
`higher` empirical quantile method and a strict greater-than comparison produce
two extremes among 22 distinct valid values. Label value 255 means the
observation cannot be classified.

Check the raster inventory first:

```bash
python build_extreme_fire_cubes.py \
  --input-dir ./mcd64a1_monthly \
  --start-year 2001 \
  --end-year 2022 \
  --dry-run
```

Build and internally verify all three cubes:

```bash
python build_extreme_fire_cubes.py \
  --input-dir ./mcd64a1_monthly \
  --output-dir ./mcd64a1_cubes \
  --start-year 2001 \
  --end-year 2022 \
  --coverage-threshold 0.80 \
  --quantile 0.90 \
  --min-years 15
```

Outputs are `mcd64a1_5km_extreme_fire.nc`,
`mcd64a1_25km_extreme_fire.nc`, and `mcd64a1_50km_extreme_fire.nc`. Each file
contains filtered `burn_fraction`, `valid_coverage`, `extreme_threshold`,
`valid_year_count`, and `extreme_fire`. For strictly out-of-sample forecast
evaluation, calculate the threshold from training years inside each
cross-validation fold; this caveat is recorded in the cube metadata.

## Inspect LDAS hindcasts before temporal alignment

The LDAS forecast archive is remote and its initialization/lead conventions
must be established from metadata before joining it to monthly fire outcomes.
`inspect_ldas_archive.py` inventories NetCDF/HDF files and emits compact JSON
describing dimensions, coordinate samples, units, calendars, variables, groups,
and candidates for initialization time, valid time, lead, ensemble member, and
spatial coordinates.

```bash
python inspect_ldas_archive.py /path/to/ldas/archive \
  --sample-files 3 \
  --output ldas_inventory.json
```

Temporal alignment uses `initialization_timestamp`, ordinal `lead_time` 1–6,
and `valid_timestamp`. The verified archive convention sets lead time 1 in the
initialization month and lead time 6 five calendar months later.

The complete temporal, spatial, variable, ensemble, and leakage requirements
are recorded in `LDAS_ALIGNMENT.md`. Published HydroViewer examples suggest a
0.05-degree LIS grid and seven forecast members, but the remote inventory must
confirm those details before alignment begins.

After generating the inventory, build the exact initialization/lead-time
index and join availability against all three fire cubes:

```bash
python build_ldas_fire_temporal_index.py \
  --ldas-dir /path/to/forecast/monthly \
  --outcome-cubes ./mcd64a1_cubes/mcd64a1_5km_extreme_fire.nc \
                  ./mcd64a1_cubes/mcd64a1_25km_extreme_fire.nc \
                  ./mcd64a1_cubes/mcd64a1_50km_extreme_fire.nc \
  --output-csv ldas_fire_temporal_index.csv \
  --summary-json ldas_fire_temporal_summary.json
```

The command rejects missing predictor variables, malformed or duplicate
initializations, non-consecutive lead times, inconsistent outcome time
coordinates, and files whose timestamps contradict the verified lead-time
definition. A file with fewer than six otherwise valid consecutive lead times
is indexed only for its available leads and explicitly listed as
incomplete in the summary. Pass `--require-complete-files` when a hard failure
is preferable.

Each aligned timestamp pair includes `valid_year`, `valid_month`, and the zero-based
`outcome_time_index` shared by all three fire cubes. This makes the CSV the
authoritative temporal join table for downstream streaming or spatial
regridding.

<!-- ## Build a resolution-agnostic legacy aligned cube

`build_aligned_cube.py` uses the verified temporal index to extract lead
times 1, 3, and 6, calculate ensemble-mean LDAS predictors, construct
thickness-weighted 0–100 cm root-zone soil moisture, and area-average the 0.05°
LDAS grid onto the exact EPSG:6933 outcome grid at any resolution. It copies outcomes through the
verified zero-based `outcome_time_index` and checks every copied label against
the source cube. Before area averaging, it masks values outside broad physical
ranges; this is necessary because missing air temperature in the archive can
appear as the finite value −273.15 °C rather than as a NetCDF fill value.

```bash
python build_aligned_cube.py \
  --temporal-index ./ldas_fire_temporal_index.csv \
  --outcome-cube ./mcd64a1_cubes/mcd64a1_50km_extreme_fire.nc \
  --output ./aligned_ldas_fire_50km_smoke.nc \
  --lead-times 1 3 6 \
  --max-samples 3

python build_aligned_cube.py \
  --temporal-index ./ldas_fire_temporal_index.csv \
  --outcome-cube ./mcd64a1_cubes/mcd64a1_50km_extreme_fire.nc \
  --output ./aligned_ldas_fire_50km.nc \
  --lead-times 1 3 6 \
  --overwrite
```

The embedded `extreme_fire_full_period` labels are descriptive only. The
baseline evaluation must recreate fire thresholds from training years inside
each cross-validation fold.

## Run the legacy ensemble-mean logistic baseline

`evaluate_baseline.py` evaluates lead times 1, 3, and 6 using five
contiguous valid-year folds. Within every fold it recalculates cell-and-calendar
month fire thresholds, predictor climatologies, standard deviations, and the
climatological event probability using training years only. A pooled L2
logistic regression is compared with cell/month climatology using ROC-AUC,
PR-AUC, Brier score, and Brier skill.

```bash
python evaluate_baseline.py \
  --input ./aligned_ldas_fire_50km.nc \
  --lead-times 1 3 6 \
  --folds 5 \
  --quantile 0.90 \
  --min-training-years 15 \
  --output-json baseline_50km_results.json \
  --output-csv baseline_50km_folds.csv
```

This analysis is retained only as `legacy_ensemble_mean_logistic`; it is not
the primary deck-defined probabilistic fire-risk method. -->

## Run the 5 km probabilistic fire-risk method

First preserve the individual LDAS members on their native grid. The output is
large, so build it on the remote analysis filesystem with compression enabled.

```bash
python ./scripts/build_ldas_ensemble_cube.py \
  --temporal-index ./outputs/ldas_fire_temporal_index.csv \
  --output ./outputs/ldas_ensemble_native_2001_2022.nc \
  --start-year 2001 \
  --end-year 2022 \
  --lead-times 1 3 6
```

Then calculate fold-local terciles and out-of-fold member
probabilities. The evaluator applies the deck's strict 60% soil-moisture and
temperature/precipitation rules, limits verification to the fixed
month-specific 2001–2022 fire-prone mask, and reports POD, FAR, and CSI.

```bash
python evaluate_probabilistic_fire_risk.py \
  --ensemble-cube ./ldas_ensemble_native_2001_2022.nc \
  --outcome-cube ./mcd64a1_cubes/mcd64a1_5km_extreme_fire.nc \
  --output-netcdf ./probabilistic_fire_risk_5km_oof.nc \
  --output-json ./probabilistic_fire_risk_5km_results.json \
  --output-csv ./probabilistic_fire_risk_5km_results.csv \
  --soil-profile-index 0 \
  --t-resolution 5
```

The outcome cube must contain every year from 2001 through 2022 to construct
the requested fixed mask. Results are separated into all, single-member, and
seven-member regimes. Single-member forecasts necessarily produce only 0 or 1
member-fraction probabilities.

The tercile calculation follows the HydroViewer backend implementation:
hindcast time and ensemble values are pooled, xarray-compatible linear 1/3 and
2/3 quantiles define `(lower, upper]` categories, zero forecast values are
masked, and category counts are divided by the complete ensemble size.
All four `SoilMoist_inst` profiles are preserved in the native ensemble cube;
`--soil-profile-index` selects the profile evaluated in a run and is recorded
in both NetCDF and JSON metadata.

If a fold/lead/month group has fewer than `--min-training-years` distinct
training years, that group is skipped rather than aborting the run. Its output
maps remain missing, `threshold_supported=0`, its observations are excluded
from verification metrics, and the reason and affected test years are written
to `unsupported_groups` in the JSON result.

## How to Interpret Metrics Measured

| Metrics                        | Definations                                             |
| ------------------------------ | ------------------------------------------------------- |
| Probability of detection [POD] | ratio between `hits` and `hits + misses`                |
| False alarm ratio [FAR]        | ratio between `false_alarms` and `hits + false_alarms`  |
| Critical success index [CSI]   | ratio between `hits` and `hits + misses + false_alarms` |
