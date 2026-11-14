import {useRef} from 'react';
import {CalendarDays} from 'lucide-react';

export default function DatePicker({value,onChange,ariaLabel}){
  const input=useRef(null);
  function open(){const el=input.current;if(!el)return;if(typeof el.showPicker==='function')el.showPicker();else el.focus()}
  return <span className="date-picker"><input ref={input} type="date" value={value||''} aria-label={ariaLabel||'Select date'} onChange={e=>onChange(e.target.value)}/><button type="button" onClick={open} aria-label="Open calendar"><CalendarDays size={13}/></button></span>
}
