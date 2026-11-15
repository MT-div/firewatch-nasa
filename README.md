# FireWatch

FireWatch is an interactive fire activity observatory for exploring hotspot detections, historical burning patterns, regional anomalies, and exportable records. The repository contains a React map dashboard and a FastAPI analytics service.

## Repository layout

```text
firewatch-nasa/
├── backend/        # FastAPI API and sample dataset
├── frontend/       # React + Vite dashboard
├── .gitignore
└── README.md
```

## Run locally

### Backend (PowerShell)

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
uvicorn main:app --reload --port 8000
```

The API and interactive documentation are available at `http://localhost:8000` and `http://localhost:8000/docs`.

### Frontend (second terminal)

```powershell
cd frontend
npm ci
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
npm run dev
```

Open `http://localhost:5173`. The frontend reads `VITE_API_URL` from its environment; the local default points to `http://localhost:8000`.

## Deploy

Deploy the frontend and backend as separate services connected to this same repository. Configure each host to use `frontend/` or `backend/` as that service's root directory. Set `VITE_API_URL` on the frontend to the public API URL. Set `DATA_FILE_PATH` on the backend to the dataset path, such as `./data/mock_harmonized_hotspots.csv`.

Never commit `.env` files, credentials, or deployment tokens. Use the host's environment variable settings for production values. The `.env.example` files contain non-secret local defaults.

## Data

The API accepts either a canonical hotspot CSV/Parquet file, one aggregated Parquet file, or a directory/glob containing annual Parquet files with the same harmonized aggregate schema. For a local folder of annual files, set `DATA_FILE_PATH` to the folder path in `backend/.env` (for example, `C:/data/firewatch/harmonized`). It discovers all `.parquet` files and reads them with DuckDB on demand instead of loading the full dataset into Pandas. Configure `DUCKDB_MEMORY_LIMIT`, `DUCKDB_THREADS`, and `DUCKDB_TEMP_DIRECTORY` for available resources. `GET /api/metadata` reports row count, file list, and available years; the calendar and anomaly endpoints use the years found in the files. For aggregated data, raw brightness-temperature and acquisition-time fields are unavailable and are returned as null/empty rather than fabricated.

The current local trial uses three annual files (2023–2025) in the `Harmomized` folder. Together they contain approximately 66.7 million rows and 2.33 GB. The ignored local `backend/.env` points to that folder; preserve its `DATA_FILE_PATH` when starting the app. Keep the Parquet files outside Git; the API only needs their local folder path. The anomaly baseline is limited to the years actually present in the selected dataset.

For a local Syria data trial, point `DATA_FILE_PATH` at `Syria_FIRMS_1km_AllConfidence.parquet` (or its CSV counterpart). The provided Syria sample contains 18,383 aggregated records from 2012 through 2025; its detection values are fractional harmonized equivalents, so calendar and anomaly counts sum `harmonized_detections` rather than counting each row as one raw fire.

## API overview

- `GET /api/health`
- `GET /api/hotspots`
- `GET /api/calendar`
- `GET /api/anomalies`
- `GET /api/export-csv`
- `POST /api/upload-dataset`
- `POST /api/reload-data`
- `POST /api/rag-placeholder`

See the OpenAPI page at `/docs` while the API is running for query parameters and response schemas.
