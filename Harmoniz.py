# ==========================================================
# FIRMS MODIS + VIIRS harmonization — batched, parquet output
# Reads zips from nasa_data/, extracts per-year to temp/,
# processes in file-batches, saves one parquet per year,
# cleans up temp/. Includes progress bars (tqdm optional).
# ==========================================================
from pathlib import Path
import sys
import time
import zipfile
import shutil
import numpy as np
import pandas as pd

# ---------- OPTIONAL PROGRESS (tqdm if available) ----------
try:
    from tqdm.auto import tqdm as _tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    _tqdm = None


def progress(iterable, total=None, desc=""):
    """tqdm bar if installed; else numbered prints."""
    if HAS_TQDM:
        return _tqdm(iterable, total=total, desc=desc, unit="it")
    if total is None:
        try:
            total = len(iterable)
        except TypeError:
            total = None
    def gen():
        for i, x in enumerate(iterable, 1):
            tag = f"[{i}/{total}]" if total else f"[{i}]"
            pct = f" ({i / total * 100:5.1f}%)" if total else ""
            print(f"{desc} {tag}{pct}")
            yield x
    return gen()


def _log(msg):
    """Print without breaking an active tqdm bar."""
    if HAS_TQDM:
        _tqdm.write(msg)
    else:
        print(msg)


# ---------- CONFIG ----------
NASA_DATA_DIR = Path("nasa_data")
TEMP_DIR      = Path("temp")
OUTPUT_DIR    = Path("Harmomized")

YEARS         = [2023, 2024, 2025]
GRID_DEG      = 0.01
CHUNK_SIZE    = 5     # CSVs per batch (lower if memory is tight)

# ---------- CONSTANTS ----------
CONF_ORDER      = {"l": 1, "n": 2, "h": 3}
CONF_INV        = {1: "l", 2: "n", 3: "h"}
CONF_TO_NUMERIC = {"l": 30, "n": 50, "h": 80}

VIIRS_SAT_K = 367
MODIS_SAT_K = 495


def _t(label, t0):
    _log(f"  [{label:<35}] {time.time() - t0:6.1f}s")
    return time.time()


# ---------- PER-BATCH: load + standardize + aggregate ----------
def process_chunk(csv_files, current_year):
    """Load & standardize a batch of CSVs; aggregate per (acq_date, cell)."""
    frames = []
    for f in csv_files:
        name = f.stem.lower()
        if "modis" in name:
            product = "modis"
        elif "viirs" in name:
            product = "viirs-snpp"
        else:
            continue
        try:
            try:
                size = f.stat().st_size / 1e6
                _log(f"    · {f.name}  ({size:.1f} MB)")
            except Exception:
                _log(f"    · {f.name}")
            df = pd.read_csv(f, low_memory=False)
        except Exception as e:
            _log(f"    [skip {f.name}: {e}]")
            continue
        df.columns = df.columns.str.lower().str.strip()
        if "frp" not in df.columns and "frp_mw" in df.columns:
            df["frp"] = df["frp_mw"]
        df["product"] = product
        frames.append(df)

    if not frames:
        return None

    df = pd.concat(frames, ignore_index=True, sort=False)

    # ---- Standardize
    df["acq_date"] = pd.to_datetime(df["acq_date"], errors="coerce", format="%Y-%m-%d")
    df = df.dropna(subset=["acq_date"])
    df = df[df["acq_date"].dt.year == current_year].copy()

    df["latitude"]  = pd.to_numeric(df["latitude"],  errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df = df.dropna(subset=["latitude", "longitude"])

    if "frp" in df.columns:
        df["frp_mw"] = pd.to_numeric(df["frp"], errors="coerce")
    else:
        df["frp_mw"] = pd.to_numeric(df["frp_mw"], errors="coerce")
    df["frp_mw"] = df["frp_mw"].fillna(0).clip(lower=0)

    df["weight"] = 1.0
    # ---- Confidence harmonization (vectorized)
    modis_mask = df["product"] == "modis"
    viirs_mask = ~modis_mask

    if "confidence" not in df.columns:
        df["confidence"] = np.nan

    conf_num = pd.to_numeric(df["confidence"], errors="coerce")
    conf_str = df["confidence"].astype(str).str.strip().str.lower()

    cat_from_num = pd.Series(pd.NA, index=df.index, dtype="object")
    cat_from_num.loc[conf_num.notna()] = "h"
    cat_from_num.loc[conf_num.notna() & (conf_num < 80)] = "n"
    cat_from_num.loc[conf_num.notna() & (conf_num < 30)] = "l"

    cat_from_str = conf_str.map({
        "l": "l", "low": "l",
        "n": "n", "nominal": "n",
        "h": "h", "high": "h",
    })

    df["confidence_harmonized"] = cat_from_num.fillna(cat_from_str)
    df["confidence_numeric"]    = df["confidence_harmonized"].map(CONF_TO_NUMERIC)
    df["conf_code"]             = df["confidence_harmonized"].map(CONF_ORDER)

    df["modis_conf_num"] = df["confidence_numeric"].where(modis_mask)
    df["viirs_conf_num"] = df["confidence_numeric"].where(viirs_mask)

    df["is_low_conf"]  = (df["confidence_harmonized"] == "l").astype("int8")
    df["is_nom_conf"]  = (df["confidence_harmonized"] == "n").astype("int8")
    df["is_high_conf"] = (df["confidence_harmonized"] == "h").astype("int8")

    # ---- Brightness
    df["brightness_modis"] = np.nan
    df["brightness_viirs"] = np.nan
    if "brightness" in df.columns and modis_mask.any():
        df.loc[modis_mask, "brightness_modis"] = pd.to_numeric(
            df.loc[modis_mask, "brightness"], errors="coerce")
    if "bright_ti4" in df.columns and viirs_mask.any():
        df.loc[viirs_mask, "brightness_viirs"] = pd.to_numeric(
            df.loc[viirs_mask, "bright_ti4"], errors="coerce")

    df["viirs_sat_flag"] = (
        viirs_mask & df["brightness_viirs"].notna()
        & (df["brightness_viirs"] >= VIIRS_SAT_K)).astype("int8")
    df["modis_sat_flag"] = (
        modis_mask & df["brightness_modis"].notna()
        & (df["brightness_modis"] >= MODIS_SAT_K)).astype("int8")

    # ---- Grid
    df["lat_cell"] = (df["latitude"]  / GRID_DEG).round().astype("int32")
    df["lon_cell"] = (df["longitude"] / GRID_DEG).round().astype("int32")
    df["modis_frp"] = np.where(modis_mask, df["frp_mw"], 0.0)
    df["viirs_frp"] = np.where(viirs_mask, df["frp_mw"], 0.0)
    df["is_modis_detected"] = (df["modis_frp"] > 0).astype("int8")
    df["is_viirs_detected"] = (df["viirs_frp"] > 0).astype("int8")

    # Weighted sums for later weighted-mean lat/lon
    df["sum_lat_w"] = df["latitude"]  * df["weight"]
    df["sum_lon_w"] = df["longitude"] * df["weight"]

    # ---- Aggregate to cell-day (merge-correct primitives only)
    agg = (
        df.groupby(["acq_date", "lat_cell", "lon_cell"], sort=False)
        .agg(
            modis_frp_sum=("modis_frp", "sum"),
            viirs_frp_sum=("viirs_frp", "sum"),
            harmonized_detections=("weight", "sum"),
            sum_lat_w=("sum_lat_w", "sum"),
            sum_lon_w=("sum_lon_w", "sum"),
            n_modis=("is_modis_detected", "sum"),
            n_viirs=("is_viirs_detected", "sum"),
            n_low_conf=("is_low_conf", "sum"),
            n_nominal_conf=("is_nom_conf", "sum"),
            n_high_conf=("is_high_conf", "sum"),
            confidence_numeric_max=("confidence_numeric", "max"),
            confidence_modis_numeric_max=("modis_conf_num", "max"),
            confidence_viirs_numeric_max=("viirs_conf_num", "max"),
            _conf_code_max=("conf_code", "max"),
            brightness_modis_max=("brightness_modis", "max"),
            brightness_viirs_max=("brightness_viirs", "max"),
            n_viirs_saturated=("viirs_sat_flag", "sum"),
            n_modis_saturated=("modis_sat_flag", "sum"),
        )
        .reset_index()
    )
    return agg


# ---------- COMBINE partial aggregates (all primitives merge cleanly) ----------
def combine_aggregates(chunks):
    if not chunks:
        return None
    if len(chunks) == 1:
        return chunks[0]

    combined = pd.concat(chunks, ignore_index=True)
    return (
        combined.groupby(["acq_date", "lat_cell", "lon_cell"], sort=False)
        .agg(
            modis_frp_sum=("modis_frp_sum", "sum"),
            viirs_frp_sum=("viirs_frp_sum", "sum"),
            harmonized_detections=("harmonized_detections", "sum"),
            sum_lat_w=("sum_lat_w", "sum"),
            sum_lon_w=("sum_lon_w", "sum"),
            n_modis=("n_modis", "sum"),
            n_viirs=("n_viirs", "sum"),
            n_low_conf=("n_low_conf", "sum"),
            n_nominal_conf=("n_nominal_conf", "sum"),
            n_high_conf=("n_high_conf", "sum"),
            confidence_numeric_max=("confidence_numeric_max", "max"),
            confidence_modis_numeric_max=("confidence_modis_numeric_max", "max"),
            confidence_viirs_numeric_max=("confidence_viirs_numeric_max", "max"),
            _conf_code_max=("_conf_code_max", "max"),
            brightness_modis_max=("brightness_modis_max", "max"),
            brightness_viirs_max=("brightness_viirs_max", "max"),
            n_viirs_saturated=("n_viirs_saturated", "sum"),
            n_modis_saturated=("n_modis_saturated", "sum"),
        )
        .reset_index()
    )


# ---------- RECONSTRUCT confidence median from l/n/h counts ----------
def derive_numeric_median_vec(n_low, n_nom, n_high):
    """Median of values in {30, 50, 80} given counts per category."""
    N = n_low + n_nom + n_high
    result = np.full(N.shape, np.nan, dtype=float)

    def value_at(pos):
        return np.where(n_low > pos, 30.0,
               np.where(n_low + n_nom > pos, 50.0, 80.0))

    even_mask = (N % 2 == 0) & (N > 0)
    if even_mask.any():
        p1 = N // 2 - 1
        p2 = N // 2
        v1 = value_at(p1)
        v2 = value_at(p2)
        result[even_mask] = (v1[even_mask] + v2[even_mask]) / 2.0

    odd_mask = (N % 2 == 1) & (N > 0)
    if odd_mask.any():
        mid = (N - 1) // 2
        v = value_at(mid)
        result[odd_mask] = v[odd_mask]

    return result


# ---------- FINALIZE ----------
def finalize(df):
    df = df.copy()

    df["latitude"]  = df["sum_lat_w"] / df["harmonized_detections"]
    df["longitude"] = df["sum_lon_w"] / df["harmonized_detections"]
    df = df.drop(columns=["sum_lat_w", "sum_lon_w"])

    df["confidence_max_cat"] = df["_conf_code_max"].map(CONF_INV)
    df = df.drop(columns=["_conf_code_max"])

    df["lat_grid"] = df["lat_cell"] * GRID_DEG
    df["lon_grid"] = df["lon_cell"] * GRID_DEG

    both = (df["modis_frp_sum"] > 0) & (df["viirs_frp_sum"] > 0)
    df["harmonized_frp"] = np.where(
        both,
        np.sqrt(df["modis_frp_sum"] * df["viirs_frp_sum"]),
        df[["modis_frp_sum", "viirs_frp_sum"]].max(axis=1),
    )
    df["frp_lower"] = df[["modis_frp_sum", "viirs_frp_sum"]].min(axis=1)
    df["frp_upper"] = df[["modis_frp_sum", "viirs_frp_sum"]].max(axis=1)

    df["acq_date"]   = pd.to_datetime(df["acq_date"])
    df["year"]       = df["acq_date"].dt.year
    df["year_month"] = df["acq_date"].dt.to_period("M").astype(str)

    df["confidence_numeric_median"] = derive_numeric_median_vec(
        df["n_low_conf"].to_numpy(),
        df["n_nominal_conf"].to_numpy(),
        df["n_high_conf"].to_numpy(),
    )

    col_order = [
        "acq_date", "year", "year_month",
        "latitude", "longitude",
        "lat_grid", "lon_grid", "lat_cell", "lon_cell",
        "harmonized_frp", "frp_lower", "frp_upper",
        "modis_frp_sum", "viirs_frp_sum",
        "harmonized_detections", "n_modis", "n_viirs",
        "confidence_max_cat", "confidence_numeric_max",
        "confidence_numeric_median",
        "confidence_modis_numeric_max", "confidence_viirs_numeric_max",
        "n_low_conf", "n_nominal_conf", "n_high_conf",
        "brightness_modis_max", "brightness_viirs_max",
        "n_modis_saturated", "n_viirs_saturated",
    ]
    df = df[[c for c in col_order if c in df.columns]]
    return df


# ---------- MAIN ----------
def main():
    t_start = time.time()

    if not NASA_DATA_DIR.exists():
        raise SystemExit(f"NASA data folder not found: {NASA_DATA_DIR}")

    if TEMP_DIR.exists():
        _log(f"Removing stale {TEMP_DIR}/")
        shutil.rmtree(TEMP_DIR)
    TEMP_DIR.mkdir(parents=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_zips = sorted(NASA_DATA_DIR.rglob("*.zip"))
    _log(f"Found {len(all_zips)} zip files under {NASA_DATA_DIR}\n")
    if not all_zips:
        raise SystemExit("No zip files found.")

    for year in YEARS:
        _log(f"\n{'='*60}")
        _log(f"YEAR {year}")
        _log(f"{'='*60}")
        t = time.time()

        year_zips = [
            z for z in all_zips
            if str(year) in z.name or str(year) == z.parent.name
        ]
        if not year_zips:
            _log(f"  No zips found for {year} — skipping")
            continue
        _log(f"  {len(year_zips)} zip(s) for {year}")

        temp_dir = TEMP_DIR / str(year)
        temp_dir.mkdir(parents=True, exist_ok=True)

        # --- Unzip
        for zp in progress(year_zips, total=len(year_zips),
                           desc=f"  Unzip {year}"):
            _log(f"  Extracting: {zp.name}")
            try:
                with zipfile.ZipFile(zp) as zf:
                    zf.extractall(temp_dir)
            except Exception as e:
                _log(f"    [skip: {e}]")
        t = _t(f"unzip {len(year_zips)} file(s)", t)

        csv_files = sorted(temp_dir.rglob("*.csv"))
        _log(f"  CSVs found: {len(csv_files)}")
        if not csv_files:
            _log(f"  No CSVs for {year} — cleaning up")
            shutil.rmtree(temp_dir)
            continue

        # --- Process in batches with incremental combine
        accumulated = None
        n_batches = (len(csv_files) + CHUNK_SIZE - 1) // CHUNK_SIZE
        batch_starts = list(range(0, len(csv_files), CHUNK_SIZE))
        for i in progress(batch_starts, total=n_batches,
                          desc=f"  Batches {year}"):
            batch = csv_files[i:i + CHUNK_SIZE]
            chunk_agg = process_chunk(batch, year)
            if chunk_agg is None or len(chunk_agg) == 0:
                continue
            accumulated = chunk_agg if accumulated is None else \
                combine_aggregates([accumulated, chunk_agg])
        t = _t("process + combine", t)

        if accumulated is None or len(accumulated) == 0:
            _log(f"  No aggregated data for {year} — cleaning up")
            shutil.rmtree(temp_dir)
            continue

        # --- Finalize + save
        df_year = finalize(accumulated)
        t = _t("finalize", t)

        out_path = OUTPUT_DIR / f"{year}_harmonized.parquet"
        df_year.to_parquet(out_path, index=False)
        _log(f"  Saved: {out_path}  ({len(df_year):,} rows, {len(df_year.columns)} cols)")
        t = _t("save parquet", t)

        # --- Cleanup
        shutil.rmtree(temp_dir)
        _log(f"  Removed {temp_dir}")

    # remove temp/ if empty
    try:
        if TEMP_DIR.exists() and not any(TEMP_DIR.iterdir()):
            TEMP_DIR.rmdir()
    except Exception:
        pass

    _log(f"\n✅ Done. Total runtime: {time.time() - t_start:.1f}s")


if __name__ == "__main__":
    main()