"""Generate a reproducible 8,000-row FIRMS-shaped sample CSV (stdlib only)."""
from pathlib import Path
import csv
import random
from datetime import date

SEED, N_ROWS = 2026, 8_000
OUT = Path(__file__).parent / "data" / "mock_harmonized_hotspots.csv"
REGIONS = {
    "Australia": (-35.0, -15.0, 115.0, 150.0),
    "South America": (-32.0, -3.0, -65.0, -45.0),
    "Eastern Mediterranean": (34.5, 37.5, 35.5, 38.5),
    "North America": (34.0, 58.0, -124.0, -110.0),
    "Antarctica": (-77.55, -77.50, 167.14, 167.20),
}
FIELDS = ["latitude","longitude","bright_ti4","scan","track","acq_date","acq_time","satellite","instrument","confidence","version","bright_ti5","frp","daynight","type"]

def generate():
    rng = random.Random(SEED)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    names = list(REGIONS)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS); writer.writeheader()
        for _ in range(N_ROWS):
            region = rng.choices(names, weights=[25,27,16,25,7])[0]
            y0,y1,x0,x1 = REGIONS[region]
            year = rng.randint(2005,2026)
            peak = [12,1,2] if region in ("Australia","South America") else [6,7,8] if region in ("Eastern Mediterranean","North America") else list(range(1,13))
            month = rng.choices(list(range(1,13)), weights=[2.5 if m in peak else 0.75 for m in range(1,13)])[0]
            day = rng.randint(1,28)
            summer = month in peak
            spike = ((region=="Australia" and year==2023 and month in (11,12)) or
                     (region=="South America" and year==2024 and month in (8,9,10)) or
                     (region=="Eastern Mediterranean" and year in (2025,2026) and month in (6,7,8)))
            frp = min(450,max(.2,rng.lognormvariate(2.25,.9)*(1.7 if summer else .65)*(4.3 if spike else 1)))
            instrument = rng.choices(["MODIS","VIIRS","MODIS_VIIRS_HARM"],[34,36,30])[0]
            satellite = rng.choice(["Terra","Aqua"]) if instrument=="MODIS" else rng.choice(["N20","SNPP"]) if instrument=="VIIRS" else "HARMONIZED"
            writer.writerow({"latitude":f"{rng.uniform(y0,y1):.5f}","longitude":f"{rng.uniform(x0,x1):.5f}","bright_ti4":f"{rng.gauss(327,16):.2f}","scan":f"{rng.uniform(.35,1.2):.2f}","track":f"{rng.uniform(.35,1.2):.2f}","acq_date":date(year,month,day).isoformat(),"acq_time":f"{rng.randrange(2400):04d}","satellite":satellite,"instrument":instrument,"confidence":rng.choices(["l","n","h"],[12,46,42])[0],"version":"2.0","bright_ti5":f"{rng.gauss(245,12):.2f}","frp":f"{frp:.2f}","daynight":rng.choices(["D","N"],[57,43])[0],"type":rng.choices([0,1,2],[72,23,5])[0]})
    print(f"Wrote {N_ROWS:,} records to {OUT}")

if __name__ == "__main__": generate()
