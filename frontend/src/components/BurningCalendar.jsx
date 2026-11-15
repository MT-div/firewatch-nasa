import { Maximize2, Minimize2 } from 'lucide-react';

const months = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
const inferno = ['#20104a', '#57106e', '#a3235d', '#e34a33', '#fca50a', '#fcffa4'];

export default function BurningCalendar({ data, ready, availableYears = [], metric, setMetric, countryName, running, onRun, onCell, expanded, onToggle }) {
  const values = data?.matrix || [];
  const max = Math.max(1, ...values.flatMap(row => row.values));
  const color = value => {
    const index = Math.min(inferno.length - 1, Math.floor(value / max * (inferno.length - 1)));
    return value ? inferno[index] : '#171d24';
  };

  return (
    <section className={`panel calendar-panel side-panel ${expanded ? 'side-panel-expanded' : ''}`}>
      <div className="panel-head">
        <div><span className="eyebrow">LONGITUDINAL PATTERN · INFERNO</span><h2>Burning activity calendar</h2></div>
        <div className="panel-head-actions">
          <div className="calendar-controls">
            <select value={metric} onChange={event => setMetric(event.target.value)}>
              <option value="count">Hotspot count</option>
              <option value="mean_frp">Mean FRP</option>
            </select>
          </div>
          <button className="panel-expand" title={expanded ? 'Restore panels' : 'Expand calendar panel'} onClick={onToggle}>
            {expanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
        </div>
      </div>
      <div className="calendar-run-row">
        <span>{countryName || 'Locating country…'}</span>
        <button onClick={onRun} disabled={running || !countryName || countryName === 'Locating country…'}>{running ? 'LOADING' : 'RUN'}</button>
      </div>
      {!ready ? (
        <div className="calendar-empty">
          <span className="calendar-empty-mark">12 × {availableYears.length || '—'}</span>
          <b>Calendar is ready to run</b>
          <span>Choose the map focus country, then run its historical activity calendar.</span>
        </div>
      ) : (
        <>
          <div className="heatmap">
            <div className="month-labels"><span />{months.map(month => <span key={month}>{month}</span>)}</div>
            {values.map(row => (
              <div className="heat-row" key={row.year}>
                <span className="year-label">{row.year}</span>
                {row.values.map((value, index) => {
                  const median = data.monthly_historical?.[String(index + 1)]?.median || 0;
                  const deviation = median ? ((value - median) / median * 100) : 0;
                  return (
                    <button
                      key={index}
                      title={`${row.year} ${months[index]} · ${value.toFixed(1)} ${metric === 'count' ? 'hotspots' : 'MW mean FRP'} · ${deviation >= 0 ? '+' : ''}${deviation.toFixed(0)}% vs median`}
                      style={{ background: color(value) }}
                      onClick={() => onCell(row.year, index + 1)}
                    />
                  );
                })}
              </div>
            ))}
          </div>
          <div className="legend">
            <span>LOW ACTIVITY</span>
            <div>{inferno.map(shade => <i key={shade} style={{ background: shade }} />)}</div>
            <span>HIGH ACTIVITY</span>
            <small>Click a cell to inspect month</small>
          </div>
        </>
      )}
    </section>
  );
}
