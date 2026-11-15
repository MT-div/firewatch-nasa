"""Memory-bounded query access for annual harmonized Parquet files."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import re
import threading

import duckdb
import numpy as np
import pyarrow.parquet as pq


AGGREGATED_COLUMNS = {
    "acq_date", "year_month", "lat_grid", "lon_grid", "latitude", "longitude",
    "modis_frp_sum", "viirs_frp_sum", "harmonized_detections", "harmonized_frp",
}


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _as_date(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    return str(value)[:10]


class AggregateParquetStore:
    """Query one or more aggregate-grid Parquet files without materializing them."""

    def __init__(self, files: list[Path], memory_limit: str, threads: int, temp_directory: Path):
        if not files:
            raise ValueError("No Parquet files were supplied")

        self.files = sorted(path.resolve() for path in files)
        schemas = [pq.read_schema(path) for path in self.files]
        for path, schema in zip(self.files, schemas):
            missing = AGGREGATED_COLUMNS - set(schema.names)
            if missing:
                raise ValueError(f"{path.name} is missing harmonized aggregate columns: " + ", ".join(sorted(missing)))
        self.columns = list(dict.fromkeys(name for schema in schemas for name in schema.names))

        self.rows = 0
        self.bytes = 0
        self.years: set[int] = set()
        date_min, date_max = [], []
        for path in self.files:
            parquet = pq.ParquetFile(path)
            self.rows += parquet.metadata.num_rows
            self.bytes += path.stat().st_size
            match = re.search(r"(?<!\d)(20\d{2})(?!\d)", path.stem)
            file_years: set[int] = set()
            if match:
                file_years.add(int(match.group(1)))

            schema_names = parquet.schema_arrow.names
            date_index = schema_names.index("acq_date")
            year_index = schema_names.index("year") if "year" in schema_names else None
            for row_group_index in range(parquet.metadata.num_row_groups):
                group = parquet.metadata.row_group(row_group_index)
                date_stats = group.column(date_index).statistics
                if date_stats and date_stats.has_min_max:
                    date_min.append(date_stats.min)
                    date_max.append(date_stats.max)
                if year_index is not None:
                    year_stats = group.column(year_index).statistics
                    if year_stats and year_stats.has_min_max:
                        low, high = int(year_stats.min), int(year_stats.max)
                        if high - low <= 100:
                            file_years.update(range(low, high + 1))
            self.years.update(file_years)

        if not self.years and date_min and date_max:
            first_year = int(min(_as_date(value) for value in date_min)[:4])
            last_year = int(max(_as_date(value) for value in date_max)[:4])
            self.years.update(range(first_year, last_year + 1))

        self.min_date = min((_as_date(value) for value in date_min), default=None)
        self.max_date = max((_as_date(value) for value in date_max), default=None)
        self.lock = threading.RLock()
        temp_directory.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(database=":memory:")
        self.connection.execute(f"SET memory_limit = {_sql_string(memory_limit)}")
        self.connection.execute(f"SET threads = {max(1, int(threads))}")
        self.connection.execute("SET enable_progress_bar = false")
        self.connection.execute("SET enable_progress_bar_print = false")
        self.connection.execute(f"SET temp_directory = {_sql_string(str(temp_directory.resolve()).replace(chr(92), '/'))}")

        file_list = ", ".join(_sql_string(str(path).replace("\\", "/")) for path in self.files)
        self.connection.execute(
            "CREATE VIEW harmonized AS "
            "SELECT *, CAST(acq_date AS DATE) AS _date, "
            "CAST(harmonized_frp AS DOUBLE) AS _frp, "
            "CAST(harmonized_detections AS DOUBLE) AS _detections "
            f"FROM read_parquet([{file_list}], union_by_name = true)"
        )

    @property
    def metadata(self) -> dict:
        return {
            "dataset_kind": "aggregated_grid",
            "rows": self.rows,
            "file_count": len(self.files),
            "file_names": [path.name for path in self.files],
            "file_bytes": self.bytes,
            "available_years": sorted(self.years),
            "min_date": self.min_date,
            "max_date": self.max_date,
            "columns": self.columns,
        }

    def _where(self, *, start_date=None, end_date=None, min_lat=None, max_lat=None,
               min_lon=None, max_lon=None, min_frp=None, confidence=None, instrument=None,
               target_month=None):
        clauses, params = [], []
        for expression, value, operator in (
            ("_date", start_date, ">="), ("_date", end_date, "<="),
            ("latitude", min_lat, ">="), ("latitude", max_lat, "<="),
            ("longitude", min_lon, ">="), ("longitude", max_lon, "<="),
            ("_frp", min_frp, ">="),
        ):
            if value is not None and value != "":
                clauses.append(f"{expression} {operator} CAST(? AS DATE)" if expression == "_date" else f"{expression} {operator} ?")
                params.append(value)
        if target_month is not None:
            clauses.append("month(_date) = ?")
            params.append(int(target_month))
        # This aggregate product has one harmonized confidence summary per cell.
        # Preserve the previous API behavior: confidence filters are unavailable
        # on this product rather than silently treating a cell as a raw detection.
        if confidence and str(confidence).lower() not in {"all", "all confidence"}:
            clauses.append("FALSE")
        if instrument and "harm" not in str(instrument).lower():
            clauses.append("FALSE")
        return (" AND ".join(clauses) if clauses else "TRUE"), params

    def _query_df(self, sql: str, params: list):
        with self.lock:
            return self.connection.execute(sql, params).fetchdf()

    def hotspots(self, *, limit: int, include_summary: bool, **filters) -> dict:
        where, params = self._where(**filters)
        sql = f"""
            SELECT CAST(_date AS VARCHAR) AS acq_date,
                   '' AS acq_time,
                   CAST(latitude AS DOUBLE) AS latitude,
                   CAST(longitude AS DOUBLE) AS longitude,
                   CAST(NULL AS DOUBLE) AS bright_ti4,
                   CAST(NULL AS DOUBLE) AS scan,
                   CAST(NULL AS DOUBLE) AS track,
                   'HARMONIZED' AS satellite,
                   'HARMONIZED' AS instrument,
                   'all' AS confidence,
                   '1' AS version,
                   CAST(NULL AS DOUBLE) AS bright_ti5,
                   _frp AS frp,
                   '' AS daynight,
                   0 AS type,
                   _detections AS hotspot_count,
                   'All confidence' AS confidence_label,
                   0 AS confidence_score
            FROM harmonized
            WHERE {where}
        """
        sampled = self._query_df(
            f"SELECT * FROM ({sql}) AS matching USING SAMPLE reservoir({int(limit)} ROWS) REPEATABLE (2026)",
            params,
        )
        points = sampled.astype(object).where(sampled.notna(), None).to_dict("records")
        summary = None
        if include_summary:
            summary_sql = f"""
                SELECT COALESCE(SUM(_detections), 0) AS total,
                       COUNT(*) AS cells,
                       COALESCE(SUM(_frp * _detections) / NULLIF(SUM(_detections), 0), 0) AS mean_frp,
                       COALESCE(SUM(_frp), 0) AS total_frp
                FROM harmonized WHERE {where}
            """
            row = self._query_df(summary_sql, params).iloc[0]
            summary = {
                "total": round(float(row.total), 2),
                "returned": len(points),
                "cells": int(row.cells),
                "mean_frp": round(float(row.mean_frp), 2),
                "total_frp": round(float(row.total_frp), 2),
                "high_confidence": 0,
            }
        return {"points": points, "summary": summary, "sampled": True}

    def calendar(self, *, metric: str, **filters) -> dict:
        where, params = self._where(**filters)
        grouped = self._query_df(
            f"""
            SELECT year(_date)::INTEGER AS year, month(_date)::INTEGER AS month,
                   SUM(_detections) AS detections,
                   SUM(_frp * _detections) / NULLIF(SUM(_detections), 0) AS mean_frp,
                   SUM(_frp) AS total_frp
            FROM harmonized WHERE {where}
            GROUP BY year(_date), month(_date)
            """, params,
        )
        metric_column = {"count": "detections", "mean_frp": "mean_frp", "total_frp": "total_frp"}[metric]
        year_values = {year: [0.0] * 12 for year in sorted(self.years)}
        monthly_by_year = {month: [] for month in range(1, 13)}
        for row in grouped.itertuples(index=False):
            value = float(getattr(row, metric_column) or 0)
            if row.year not in year_values:
                year_values[row.year] = [0.0] * 12
            year_values[row.year][row.month - 1] = value
        years = sorted(year_values)
        for month in range(1, 13):
            monthly_by_year[month] = [year_values[year][month - 1] for year in years]
        matrix = [{"year": year, "values": values} for year, values in sorted(year_values.items())]
        historical = {}
        for month, values in monthly_by_year.items():
            if values:
                historical[str(month)] = {
                    "median": float(np.median(values)),
                    "mean": float(np.mean(values)),
                }
            else:
                historical[str(month)] = {"median": 0.0, "mean": 0.0}
        return {
            "metric": metric,
            "matrix": matrix,
            "years": years,
            "monthly_historical": historical,
            "coverage": {"min_date": self.min_date, "max_date": self.max_date, "years": years},
        }

    def anomalies(self, *, target_year: int, target_month: int, **filters) -> dict:
        where, params = self._where(target_month=target_month, **filters)
        grouped = self._query_df(
            f"""
            SELECT year(_date)::INTEGER AS year, SUM(_detections) AS count
            FROM harmonized WHERE {where}
            GROUP BY year(_date) ORDER BY year(_date)
            """, params,
        )
        counts = {int(row.year): float(row.count) for row in grouped.itertuples(index=False)}
        current = counts.get(int(target_year), 0.0)
        history = {year: counts.get(year, 0.0) for year in sorted(self.years) if year != int(target_year)}
        values = list(history.values())
        mean = float(np.mean(values)) if values else 0.0
        median = float(np.median(values)) if values else 0.0
        sigma = float(np.std(values, ddof=0)) if values else 0.0
        z = (current - mean) / sigma if sigma else (0.0 if current == mean else 99.0 if current > mean else -99.0)
        pct = (current - median) / median * 100 if median else (100.0 if current else 0.0)
        covered = int(target_year) in self.years
        if not covered:
            severity = "NO_DATA"
            message = f"No data is available for {target_year}. Available years: {', '.join(map(str, sorted(self.years)))}."
        elif len(values) < 2:
            severity = "INSUFFICIENT_HISTORY"
            message = f"A historical baseline needs at least two comparison years; only {len(values)} are available for this month."
        elif sigma and current > mean + 2 * sigma:
            severity = "CRITICAL_ANOMALY"
            message = f"Critical hotspot surge: {int(current)} detections in month {target_month}, {pct:+.0f}% vs historical median. Prioritize responder review."
        elif sigma and current > mean + 1.5 * sigma:
            severity = "ELEVATED"
            message = f"Elevated activity: {int(current)} detections in month {target_month}, {pct:+.0f}% vs historical median. Monitor conditions."
        else:
            severity = "NORMAL"
            message = f"Activity is within the expected range for month {target_month}."
        return {
            "target_year": int(target_year), "target_month": int(target_month),
            "current_value": int(round(current)), "historical_average": round(mean, 2),
            "historical_median": round(median, 2), "standard_deviation": round(sigma, 2),
            "percentage_change": round(pct, 2), "z_score": round(float(z), 2),
            "severity": severity, "message": message,
            "historical_curve": [
                {"year": year, "count": int(round(counts.get(year, 0.0)))}
                for year in sorted(self.years)
            ],
            "baseline_years": sorted(history), "baseline_year_count": len(values),
            "available_years": sorted(self.years), "has_target_year_data": covered,
        }

    def export_chunks(self, *, filters: dict, chunk_size: int = 25_000):
        where, params = self._where(**filters)
        columns = ", ".join('"' + name.replace('"', '""') + '"' for name in self.columns)
        sql = f"SELECT {columns} FROM harmonized WHERE {where}"

        def generate():
            with self.lock:
                cursor = self.connection.execute(sql, params)
                header = True
                while True:
                    chunk = cursor.fetch_df_chunk(max(1, chunk_size // 2048))
                    if chunk is None or chunk.empty:
                        break
                    yield chunk.to_csv(index=False, header=header).encode("utf-8")
                    header = False
        return generate()

    def close(self):
        with self.lock:
            self.connection.close()
