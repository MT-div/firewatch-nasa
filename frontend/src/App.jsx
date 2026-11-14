import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Flame, PanelLeftOpen, PanelRightOpen, X, Download } from 'lucide-react';
import Navbar from './components/Navbar';
import CommandSearch from './components/CommandSearch';
import SearchAndFilterBar from './components/SearchAndFilterBar';
import FieldAssistant from './components/FieldAssistant';
const FireMap = lazy(() => import('./components/FireMap'));
import BurningCalendar from './components/BurningCalendar';
import AnomalyCard from './components/AnomalyCard';
import DatePicker from './components/DatePicker';
import { get, exportCsv } from './api';

const today = new Date();
const dateString = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
const boxParams = b => b ? ({ min_lat:b[0], max_lat:b[1], min_lon:b[2], max_lon:b[3] }) : {};

export default function App() {
  const [filters,setFilters]=useState({start_date:'2005-01-01',end_date:dateString(today),confidence:''}),[draftFilters,setDraftFilters]=useState({start_date:'2005-01-01',end_date:dateString(today),confidence:''});
  const [points,setPoints]=useState([]),[calendar,setCalendar]=useState(null),[calendarReady,setCalendarReady]=useState(false),[anomaly,setAnomaly]=useState(null);
  const [bounds,setBounds]=useState(null),[viewBounds,setViewBounds]=useState(null),[region,setRegion]=useState('Global');
  const [metric,setMetric]=useState('count'),[period,setPeriod]=useState([today.getFullYear(),today.getMonth()+1]);
  const [error,setError]=useState(''),[loading,setLoading]=useState(false),[toast,setToast]=useState('');
  const [expandedPanel,setExpandedPanel]=useState(null),[showLeft,setShowLeft]=useState(true),[showRight,setShowRight]=useState(true);
  const [filtersOpen,setFiltersOpen]=useState(false),[country,setCountry]=useState({name:'Locating country…',bounds:null,center:null});
  const [analysisRunning,setAnalysisRunning]=useState(false),[calendarRunning,setCalendarRunning]=useState(false),[exportOpen,setExportOpen]=useState(false),[exportCountry,setExportCountry]=useState(''),[exportStart,setExportStart]=useState(''),[exportEnd,setExportEnd]=useState('');
  const lookupTimer=useRef(),lookupId=useRef(0),retryTimer=useRef(null),retryCount=useRef(0),requestId=useRef(0);
  const refresh=useCallback(async()=>{const id=++requestId.current;clearTimeout(retryTimer.current);setLoading(true);setError('');try{const spatial=boxParams(bounds);const hot=await get('/api/hotspots',{...filters,...spatial,limit:1500,include_summary:false});if(id!==requestId.current)return;retryCount.current=0;setPoints(hot.points||[])}catch(e){if(id!==requestId.current)return;setError(`${e.message}. Waiting for the API at http://localhost:8000 and retrying…`);retryCount.current+=1;const delay=Math.min(10000,1000*2**Math.min(retryCount.current-1,3));retryTimer.current=setTimeout(()=>refresh(),delay)}finally{if(id===requestId.current)setLoading(false)}},[filters,bounds]);
  useEffect(()=>{refresh();return()=>{clearTimeout(retryTimer.current);requestId.current+=1}},[refresh]);
  function chooseRegion(b,name='Global'){const next=b||[-78,82,-180,180];setRegion(name);setBounds(next);setViewBounds(next)}
  function applyFilters(next,coords){setFilters(next);if(coords?.lat!==''&&coords?.lon!==''){const lat=Number(coords.lat),lon=Number(coords.lon),r=Math.max(1,Number(coords.radius)||100)/111;if(Number.isFinite(lat)&&Number.isFinite(lon)&&lat>=-90&&lat<=90&&lon>=-180&&lon<=180){const dx=r/Math.max(.2,Math.cos(lat*Math.PI/180));chooseRegion([lat-r,lat+r,lon-dx,lon+dx],'Coordinates')}}setFiltersOpen(false)}
  function currentMap(){if(viewBounds){setBounds(viewBounds);setRegion('Map selection')}}
  function togglePanel(name){setExpandedPanel(v=>v===name?null:name)}
  function selectCell(y,m){setPeriod([y,m]);setAnomaly(null);const next={...draftFilters,start_date:`${y}-${String(m).padStart(2,'0')}-01`,end_date:dateString(new Date(y,m,0))};setDraftFilters(next);setFilters(next)}
  async function trackCenter(center){if(!center||!Number.isFinite(center.lat)||!Number.isFinite(center.lon))return;clearTimeout(lookupTimer.current);lookupTimer.current=setTimeout(async()=>{const id=++lookupId.current;try{const url=`https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=3&lat=${center.lat}&lon=${center.lon}`;const r=await fetch(url,{headers:{'Accept-Language':'en'}});const data=await r.json();const name=data.address?.country||'International waters';let countryBounds=null;if(data.address?.country){const s=await fetch(`https://nominatim.openstreetmap.org/search?format=jsonv2&featureType=country&limit=1&q=${encodeURIComponent(name)}`,{headers:{'Accept-Language':'en'}});const rows=await s.json();if(rows[0]?.boundingbox){const [south,north,west,east]=rows[0].boundingbox.map(Number);countryBounds=[south,north,west,east]}}if(id===lookupId.current){if(country.name!==name)setAnomaly(null);setCountry({name,bounds:countryBounds,center});setExportCountry(name)}}catch{if(id===lookupId.current)setCountry(c=>({...c,center}))}},1000)}
  async function runAnalysis(nextPeriod=period,nextCountry=country){if(!nextCountry?.bounds){setToast('Country boundary is still being located. Try RUN again in a moment.');setTimeout(()=>setToast(''),3500);return}setAnalysisRunning(true);try{const [y,m]=nextPeriod;setAnomaly(await get('/api/anomalies',{...boxParams(nextCountry.bounds),target_year:y,target_month:m}))}catch(e){setToast(`Analysis failed: ${e.message}`);setTimeout(()=>setToast(''),4000)}finally{setAnalysisRunning(false)}}
  async function runCalendar(nextCountry=country){if(!nextCountry?.bounds){setToast('Country boundary is still being located.');setTimeout(()=>setToast(''),3500);return}setCalendarRunning(true);try{setCalendar(await get('/api/calendar',boxParams(nextCountry.bounds)));setCalendarReady(true)}catch(e){setToast(`Calendar failed: ${e.message}`);setTimeout(()=>setToast(''),4000)}finally{setCalendarRunning(false)}}
  function openExport(){setExportCountry(country.name==='Locating country…'?'':country.name);setExportStart(filters.start_date||'2005-01-01');setExportEnd(filters.end_date||dateString(today));setExportOpen(true)}
  async function doExport(){try{let spatial=country.bounds;if(exportCountry.trim()&&exportCountry.trim()!==country.name){const r=await fetch(`https://nominatim.openstreetmap.org/search?format=jsonv2&featureType=country&limit=1&q=${encodeURIComponent(exportCountry.trim())}`);const d=await r.json();if(d[0]?.boundingbox){const [s,n,w,e]=d[0].boundingbox.map(Number);spatial=[s,n,w,e]}}await exportCsv({...filters,start_date:exportStart,end_date:exportEnd,...boxParams(spatial||bounds)});setExportOpen(false)}catch(e){setToast(e.message);setTimeout(()=>setToast(''),3500)}}
  return <div className="app-shell">
    <Navbar onExport={openExport}/>
    <main className="dashboard-main">
      <div className="command-header"><div className="command-kicker"><span className="live-dot"/> GLOBAL THERMAL INTELLIGENCE <span>/</span> FIRE ACTIVITY</div><CommandSearch onRegion={chooseRegion} filtersOpen={filtersOpen} onToggleFilters={()=>setFiltersOpen(v=>!v)}/><div className="search-reserved-space"/>{filtersOpen&&<SearchAndFilterBar filters={draftFilters} setFilters={setDraftFilters} onApply={applyFilters} onRegion={chooseRegion} onMapView={currentMap} showLeft={showLeft} showRight={showRight} onToggleLeft={()=>setShowLeft(v=>!v)} onToggleRight={()=>setShowRight(v=>!v)}/>}</div>
      {error&&<div className="error-banner"><AlertTriangle size={17}/>{error}</div>}
      <div className={`workspace ${showLeft?'show-left':'hide-left'} ${showRight?'show-right':'hide-right'} ${expandedPanel?`expand-${expandedPanel}`:''}`}>
        {showLeft&&<aside className="left-rail"><div className="rail-heading"><div><span className="eyebrow">EARLY WARNING</span><b>ANALYSIS</b></div><button title="Hide left sidebar" onClick={()=>setShowLeft(false)}>×</button></div><AnomalyCard data={anomaly} year={period[0]} month={period[1]} countryName={country.name} running={analysisRunning} onPeriodChange={p=>{setPeriod(p);setAnomaly(null)}} onRun={()=>runAnalysis()} expanded={expandedPanel==='anomaly'} onToggle={()=>togglePanel('anomaly')}/><BurningCalendar data={calendarReady?calendar:null} ready={calendarReady} metric={metric} setMetric={setMetric} countryName={country.name} running={calendarRunning} onRun={()=>runCalendar()} onCell={selectCell} expanded={expandedPanel==='calendar'} onToggle={()=>togglePanel('calendar')}/></aside>}
        <section className="map-stage"><div className="section-heading map-heading"><div><b>ACTIVE FIRE MONITOR</b><small>{region.toUpperCase()}</small></div><span className="section-line"/></div><div className="map-panel globe-panel"><Suspense fallback={<div className="loading globe-loading">Initializing interactive globe…</div>}><FireMap points={points} bounds={bounds} onBounds={setViewBounds} onCenter={trackCenter} onRegion={chooseRegion} filters={draftFilters} onFilterChange={setDraftFilters} onApplyFilters={()=>applyFilters(draftFilters)}/></Suspense></div></section>
        {showRight&&<aside className="right-rail"><div className="rail-heading"><div><span className="eyebrow">REGIONAL CONTEXT</span><b>FIELD SUPPORT</b></div><button title="Hide right sidebar" onClick={()=>setShowRight(false)}>×</button></div><FieldAssistant expanded={expandedPanel==='assistant'} onToggle={()=>togglePanel('assistant')}/></aside>}
      </div>
    </main>
    {!showLeft&&<button className="sidebar-reopen reopen-left" onClick={()=>setShowLeft(true)} title="Show analysis sidebar"><PanelLeftOpen size={16}/></button>}{!showRight&&<button className="sidebar-reopen reopen-right" onClick={()=>setShowRight(true)} title="Show field assistant sidebar"><PanelRightOpen size={16}/></button>}
    {exportOpen&&<div className="modal-backdrop" onMouseDown={e=>e.target===e.currentTarget&&setExportOpen(false)}><section className="export-modal"><button className="modal-close" onClick={()=>setExportOpen(false)} aria-label="Close"><X size={17}/></button><span className="eyebrow">DATA DELIVERY</span><h2>Export fire observations</h2><p>Choose a country and acquisition window for the downloadable data.</p><label>COUNTRY<input value={exportCountry} onChange={e=>setExportCountry(e.target.value)} placeholder="Country name"/></label><div className="export-dates"><label>FROM<DatePicker value={exportStart} onChange={setExportStart}/></label><label>TO<DatePicker value={exportEnd} onChange={setExportEnd}/></label></div><button className="export-confirm" onClick={doExport}><Download size={15}/> DOWNLOAD CSV</button></section></div>}
    {toast&&<div className="toast">{toast}</div>}
  </div>
}







