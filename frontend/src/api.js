const API=import.meta.env.VITE_API_URL||'http://localhost:8000';
export function queryString(filters={}){const p=new URLSearchParams();Object.entries(filters).forEach(([k,v])=>{if(v!==''&&v!==null&&v!==undefined)p.set(k,v)});return p.toString()}
export async function get(path,params={}){const r=await fetch(`${API}${path}?${queryString(params)}`);if(!r.ok)throw new Error((await r.json()).detail||r.statusText);return r.json()}
export async function upload(file){const f=new FormData();f.append('file',file);const r=await fetch(`${API}/api/upload-dataset`,{method:'POST',body:f});if(!r.ok)throw new Error((await r.json()).detail||r.statusText);return r.json()}
export async function exportCsv(params){const r=await fetch(`${API}/api/export-csv?${queryString(params)}`);if(!r.ok)throw new Error('Export failed');const blob=await r.blob();const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='fire_hotspots.csv';a.click();URL.revokeObjectURL(a.href)}

