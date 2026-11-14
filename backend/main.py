"""FastAPI service for harmonized MODIS/VIIRS hotspot exploration."""
from pathlib import Path
from io import BytesIO, StringIO
import os
import threading
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
DATA_FILE = Path(os.getenv("DATA_FILE_PATH", "./data/mock_harmonized_hotspots.csv"))
if not DATA_FILE.is_absolute(): DATA_FILE = (BASE / DATA_FILE).resolve()
REQUIRED = ["latitude","longitude","bright_ti4","scan","track","acq_date","acq_time","satellite","instrument","confidence","version","bright_ti5","frp","daynight","type"]
LOCK = threading.RLock()
DATA = pd.DataFrame()
app = FastAPI(title="Harmonized Hotspot Observatory", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def normalize(frame: pd.DataFrame) -> pd.DataFrame:
    missing = set(REQUIRED) - set(frame.columns)
    if missing: raise ValueError("Missing required columns: " + ", ".join(sorted(missing)))
    df = frame[REQUIRED].copy()
    for col in ["latitude","longitude","bright_ti4","scan","track","bright_ti5","frp"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float32")
    df["date"] = pd.to_datetime(df.acq_date, errors="coerce")
    df = df.dropna(subset=["latitude","longitude","date","frp"])
    df["year"] = df.date.dt.year.astype("int16"); df["month"] = df.date.dt.month.astype("int8")
    c = df.confidence.astype(str).str.lower()
    df["confidence_label"] = c.map({"l":"Low","n":"Nominal","h":"High"}).fillna("Numeric")
    df["confidence_score"] = pd.to_numeric(c, errors="coerce").fillna(c.map({"l":25,"n":65,"h":90})).astype("int8")
    df["acq_time"] = df.acq_time.astype(str).str.zfill(4)
    return df.reset_index(drop=True)

def load_data(path=None):
    global DATA, DATA_FILE
    p = Path(path) if path else DATA_FILE
    if not p.is_absolute(): p = (BASE / p).resolve()
    try: frame = normalize(pd.read_csv(p, usecols=lambda c: c in REQUIRED, low_memory=True))
    except Exception as e: raise RuntimeError(f"Could not load dataset {p}: {e}") from e
    with LOCK: DATA, DATA_FILE = frame, p
    return len(frame)

@app.on_event("startup")
def startup():
    try: load_data()
    except RuntimeError:
        fallback = BASE / "data" / "mock_harmonized_hotspots.csv"
        if fallback.exists(): load_data(fallback)

def filtered(start_date=None, end_date=None, min_lat=None, max_lat=None, min_lon=None, max_lon=None, min_frp=None, confidence=None, instrument=None):
    with LOCK: df = DATA
    if not len(df): raise HTTPException(503, "No dataset loaded")
    mask = np.ones(len(df), dtype=bool)
    for col, value, op in [("date",start_date,"ge"),("date",end_date,"le"),("latitude",min_lat,"ge"),("latitude",max_lat,"le"),("longitude",min_lon,"ge"),("longitude",max_lon,"le"),("frp",min_frp,"ge")]:
        if value is not None: mask &= getattr(df[col], op)(pd.to_datetime(value) if col=="date" else value).to_numpy()
    if confidence: mask &= df.confidence_label.str.lower().eq(confidence.lower()).to_numpy() | df.confidence.astype(str).str.lower().eq(confidence.lower()).to_numpy()
    if instrument: mask &= df.instrument.str.contains(instrument, case=False, regex=False).to_numpy()
    return df.loc[mask]

def serialize(df):
    result = df.copy(); result["acq_date"] = result.date.dt.strftime("%Y-%m-%d")
    return result[REQUIRED + ["confidence_label","confidence_score"]].replace({np.nan: None}).to_dict("records")

@app.get("/api/health")
def health(): return {"status":"ok","rows":len(DATA),"data_file":str(DATA_FILE)}

@app.get("/api/hotspots")
def hotspots(start_date: str|None=None,end_date: str|None=None,min_lat: float|None=None,max_lat: float|None=None,min_lon: float|None=None,max_lon: float|None=None,min_frp: float|None=None,confidence: str|None=None,instrument: str|None=None,limit: int=Query(3000,ge=1,le=20000),include_summary: bool=True):
    df = filtered(start_date,end_date,min_lat,max_lat,min_lon,max_lon,min_frp,confidence,instrument)
    rows = df.sample(n=limit, random_state=2026).sort_values("date") if len(df)>limit else df
    summary={"total":len(df),"returned":len(rows),"mean_frp":round(float(df.frp.mean()),2) if len(df) else 0,"total_frp":round(float(df.frp.sum()),2) if len(df) else 0,"high_confidence":int(df.confidence_label.eq("High").sum())} if include_summary else None
    return {"points":serialize(rows),"summary":summary}

@app.get("/api/calendar")
def calendar(min_lat:float|None=None,max_lat:float|None=None,min_lon:float|None=None,max_lon:float|None=None,metric:str="count"):
    if metric not in {"count","mean_frp","total_frp"}: raise HTTPException(400,"metric must be count, mean_frp, or total_frp")
    df=filtered(min_lat=min_lat,max_lat=max_lat,min_lon=min_lon,max_lon=max_lon)
    agg={"count":"size","mean_frp":"mean","total_frp":"sum"}[metric]
    grouped=df.groupby(["year","month"]).frp.agg(agg).rename("value").reset_index()
    matrix=[]
    for year in range(2005,2027):
        matrix.append({"year":year,"values":[float(v) if pd.notna(v) else 0 for v in [grouped.loc[(grouped.year==year)&(grouped.month==m),"value"].iloc[0] if ((grouped.year==year)&(grouped.month==m)).any() else 0 for m in range(1,13)]]})
    monthly_values = df.groupby(["year","month"]).frp.agg(agg).rename("value").reset_index()
    stats=monthly_values.groupby("month").value.agg(["median","mean"]).to_dict("index")
    monthly={str(m):{"median":float(stats[m]["median"]) if m in stats else 0,"mean":float(stats[m]["mean"]) if m in stats else 0} for m in range(1,13)}
    return {"metric":metric,"matrix":matrix,"monthly_historical":monthly}

@app.get("/api/anomalies")
def anomalies(min_lat:float|None=None,max_lat:float|None=None,min_lon:float|None=None,max_lon:float|None=None,target_year:int=2026,target_month:int=7):
    if target_month<1 or target_month>12: raise HTTPException(400,"target_month must be 1-12")
    df=filtered(min_lat=min_lat,max_lat=max_lat,min_lon=min_lon,max_lon=max_lon)
    monthly=df[df.month.eq(target_month)]; hist=monthly[monthly.year.ne(target_year)]
    current=monthly[monthly.year.eq(target_year)]; x=float(len(current)); vals=hist.groupby("year").size().astype(float)
    mean=float(vals.mean()) if len(vals) else 0.; median=float(vals.median()) if len(vals) else 0.; sigma=float(vals.std(ddof=0)) if len(vals) else 0.
    z=(x-mean)/sigma if sigma else (0. if x==mean else float("inf") if x>mean else float("-inf"))
    severity="CRITICAL_ANOMALY" if x>mean+2*sigma and sigma else "ELEVATED" if x>mean+1.5*sigma and sigma else "NORMAL"
    pct=((x-median)/median*100) if median else (100. if x else 0.)
    message={"CRITICAL_ANOMALY":f"Critical hotspot surge: {int(x)} detections in month {target_month}, {pct:+.0f}% vs historical median. Prioritize responder review.","ELEVATED":f"Elevated activity: {int(x)} detections in month {target_month}, {pct:+.0f}% vs historical median. Monitor conditions.","NORMAL":f"Activity is within the expected range for month {target_month}."}[severity]
    curve=[{"year":int(y),"count":int(v)} for y,v in vals.items()]
    return {"target_year":target_year,"target_month":target_month,"current_value":int(x),"historical_average":round(mean,2),"historical_median":round(median,2),"standard_deviation":round(sigma,2),"percentage_change":round(pct,2),"z_score":round(z,2) if np.isfinite(z) else (99. if z>0 else -99.),"severity":severity,"message":message,"historical_curve":curve}

@app.get("/api/export-csv")
def export_csv(start_date:str|None=None,end_date:str|None=None,min_lat:float|None=None,max_lat:float|None=None,min_lon:float|None=None,max_lon:float|None=None,min_frp:float|None=None,confidence:str|None=None,instrument:str|None=None):
    df=filtered(start_date,end_date,min_lat,max_lat,min_lon,max_lon,min_frp,confidence,instrument)
    stream=StringIO(); df[REQUIRED].to_csv(stream,index=False)
    stream.seek(0)
    return StreamingResponse(iter([stream.getvalue()]),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=harmonized_hotspots.csv"})

class RAGRequest(BaseModel): query:str; region_stats:dict={}
@app.post("/api/rag-placeholder")
def rag_placeholder(body:RAGRequest):
    return {"summary":f"Analytical context for ‘{body.query}’: {body.region_stats}","method":"deterministic-placeholder","ready_for_llm":True}

@app.post("/api/upload-dataset")
async def upload_dataset(file:UploadFile=File(...)):
    if not file.filename.lower().endswith(".csv"): raise HTTPException(400,"Upload a CSV file")
    content=await file.read()
    try: candidate=normalize(pd.read_csv(BytesIO(content),usecols=lambda c:c in REQUIRED,low_memory=True))
    except Exception as e: raise HTTPException(400,str(e)) from e
    target=BASE/"data"/"uploaded_harmonized_hotspots.csv"; target.parent.mkdir(exist_ok=True)
    target.write_bytes(content)
    with LOCK: global DATA, DATA_FILE; DATA,DATA_FILE=candidate,target
    return {"status":"loaded","rows":len(candidate),"data_file":str(target)}

@app.post("/api/reload-data")
def reload_data():
    try: count=load_data()
    except RuntimeError as e: raise HTTPException(400,str(e)) from e
    return {"status":"reloaded","rows":count,"data_file":str(DATA_FILE)}
