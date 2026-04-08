/* ─────────────────────────────────────────────────────────────
   AFTERGLOW — Dashboard JS
   Wires the API to the UI: forecast fetch, score dials,
   7-day Chart.js chart, week cards, and live atmospheric tuner.
───────────────────────────────────────────────────────────── */

'use strict';

// ── Grade palette (mirrors scorer.py) ──────────────────────
const GRADES = [
  { min: 81, label: 'Epic',  color: '#e85d24' },
  { min: 61, label: 'Vivid', color: '#f0a84a' },
  { min: 41, label: 'Good',  color: '#8ec46a' },
  { min: 21, label: 'Fair',  color: '#6aaccf' },
  { min:  0, label: 'Poor',  color: '#6b6560' },
];

function gradeFor(score) {
  return GRADES.find(g => score >= g.min) || GRADES[GRADES.length - 1];
}

// ── Sky horizon gradient per grade ─────────────────────────
const SKY_PALETTES = {
  Epic:  { horizon: 'radial-gradient(ellipse 100% 55% at 50% 100%, #5c1a08 0%, #2a0d05 50%, transparent 100%)',
           glow:    'radial-gradient(ellipse 80% 30% at 50% 100%, rgba(232,93,36,0.25) 0%, transparent 70%)' },
  Vivid: { horizon: 'radial-gradient(ellipse 100% 50% at 50% 100%, #4a2808 0%, #221206 50%, transparent 100%)',
           glow:    'radial-gradient(ellipse 80% 25% at 50% 100%, rgba(240,168,74,0.18) 0%, transparent 70%)' },
  Good:  { horizon: 'radial-gradient(ellipse 100% 45% at 50% 100%, #1c2a10 0%, #0e1509 50%, transparent 100%)',
           glow:    'radial-gradient(ellipse 80% 20% at 50% 100%, rgba(142,196,106,0.10) 0%, transparent 70%)' },
  Fair:  { horizon: 'radial-gradient(ellipse 100% 40% at 50% 100%, #0d1e2a 0%, #070f15 50%, transparent 100%)',
           glow:    'radial-gradient(ellipse 80% 18% at 50% 100%, rgba(106,172,207,0.10) 0%, transparent 70%)' },
  Poor:  { horizon: 'radial-gradient(ellipse 100% 35% at 50% 100%, #111110 0%, #090908 50%, transparent 100%)',
           glow:    'none' },
};

function applySkyGrade(grade) {
  const pal = SKY_PALETTES[grade] || SKY_PALETTES.Poor;
  const horizon = document.getElementById('skyHorizon');
  const glow    = document.getElementById('skyGlow');
  horizon.style.background = pal.horizon;
  horizon.style.opacity    = '1';
  glow.style.background    = pal.glow;
  glow.style.opacity       = '1';
}

// ── Score dial (Canvas arc) ─────────────────────────────────
function drawDial(canvasId, score, color, animated = true) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx   = canvas.getContext('2d');
  const W     = canvas.width;
  const H     = canvas.height;
  const cx    = W / 2;
  const cy    = H / 2;
  const R     = Math.min(W, H) / 2 - 14;
  const start = Math.PI * 0.75;          // 7 o'clock
  const sweep = Math.PI * 1.5;           // 270° arc

  const draw = (frac) => {
    ctx.clearRect(0, 0, W, H);

    // Track
    ctx.beginPath();
    ctx.arc(cx, cy, R, start, start + sweep);
    ctx.strokeStyle = 'rgba(255,255,255,0.07)';
    ctx.lineWidth   = 4;
    ctx.lineCap     = 'round';
    ctx.stroke();

    // Tick marks
    for (let i = 0; i <= 10; i++) {
      const angle  = start + (sweep * i / 10);
      const inner  = R - 8;
      const outer  = R - 2;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(angle) * inner, cy + Math.sin(angle) * inner);
      ctx.lineTo(cx + Math.cos(angle) * outer, cy + Math.sin(angle) * outer);
      ctx.strokeStyle = i % 5 === 0
        ? 'rgba(255,255,255,0.20)'
        : 'rgba(255,255,255,0.08)';
      ctx.lineWidth = i % 5 === 0 ? 1.5 : 0.8;
      ctx.stroke();
    }

    if (frac <= 0) return;

    // Glow shadow
    ctx.save();
    ctx.shadowColor  = color;
    ctx.shadowBlur   = 18;
    ctx.beginPath();
    ctx.arc(cx, cy, R, start, start + sweep * frac);
    ctx.strokeStyle = color;
    ctx.lineWidth   = 4;
    ctx.lineCap     = 'round';
    ctx.stroke();
    ctx.restore();

    // Needle dot at tip
    const tipAngle = start + sweep * frac;
    ctx.beginPath();
    ctx.arc(
      cx + Math.cos(tipAngle) * R,
      cy + Math.sin(tipAngle) * R,
      4, 0, Math.PI * 2
    );
    ctx.fillStyle = color;
    ctx.shadowColor = color;
    ctx.shadowBlur  = 12;
    ctx.fill();
    ctx.shadowBlur  = 0;
  };

  if (!animated) { draw(score / 100); return; }

  let start_ = 0;
  const target = score / 100;
  const step   = () => {
    start_ += (target - start_) * 0.1;
    draw(start_);
    if (Math.abs(target - start_) > 0.002) requestAnimationFrame(step);
    else draw(target);
  };
  requestAnimationFrame(step);
}

// ── Clock ───────────────────────────────────────────────────
function tickClock() {
  const el = document.getElementById('metaTime');
  if (!el) return;
  const now = new Date();
  el.textContent = now.toLocaleTimeString('en-US', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
}
setInterval(tickClock, 1000);
tickClock();

// ── Time formatting ─────────────────────────────────────────
function fmtTime(isoStr) {
  if (!isoStr) return '——:——';
  try {
    const d = new Date(isoStr);
    return d.toLocaleTimeString('en-US', {
      hour: '2-digit', minute: '2-digit', hour12: false,
    });
  } catch { return '——:——'; }
}

function fmtDate(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr + 'T00:00:00');
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}

function dayOfMonth(isoStr) {
  return isoStr ? isoStr.split('-')[2].replace(/^0/, '') : '?';
}

function shortDay(isoStr) {
  const d = new Date(isoStr + 'T00:00:00');
  return d.toLocaleDateString('en-US', { weekday: 'short' }).toUpperCase();
}

// ── Chart.js instance ───────────────────────────────────────
let _chart     = null;
let _chartMode = 'sunset';
let _weekData  = [];

function buildChart(days, mode) {
  const labels  = days.map(d => shortDay(d.date) + '\n' + dayOfMonth(d.date));
  const scores  = days.map(d => mode === 'sunset'
    ? d.sunset_score.score : d.sunrise_score.score);
  const colors  = scores.map(s => gradeFor(s).color);
  const alphas  = colors.map(c => c + '33');

  const canvas = document.getElementById('forecastChart');
  if (!canvas) return;

  if (_chart) { _chart.destroy(); _chart = null; }

  _chart = new Chart(canvas.getContext('2d'), {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data: scores,
        backgroundColor: alphas,
        borderColor:     colors,
        borderWidth:     1.5,
        borderRadius:    4,
        hoverBackgroundColor: colors.map(c => c + '55'),
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 600, easing: 'easeOutQuart' },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#111009',
          borderColor:     'rgba(255,210,140,0.15)',
          borderWidth:     1,
          titleColor:      '#e8dcc8',
          bodyColor:       '#9e9082',
          titleFont:       { family: "'JetBrains Mono', monospace", size: 11 },
          bodyFont:        { family: "'JetBrains Mono', monospace", size: 11 },
          callbacks: {
            title: (items) => {
              const d = days[items[0].dataIndex];
              return fmtDate(d.date);
            },
            label: (item) => {
              const g = gradeFor(item.raw);
              return ` Score: ${item.raw}  (${g.label})`;
            },
          },
        },
      },
      scales: {
        x: {
          grid:  { color: 'rgba(255,255,255,0.04)', drawBorder: false },
          ticks: { color: '#5a5248', font: { family: "'JetBrains Mono', monospace", size: 10 } },
          border: { color: 'rgba(255,210,140,0.08)' },
        },
        y: {
          min: 0, max: 100,
          grid:  { color: 'rgba(255,255,255,0.04)', drawBorder: false },
          ticks: {
            color:    '#5a5248',
            stepSize: 20,
            font:     { family: "'JetBrains Mono', monospace", size: 10 },
          },
          border: { color: 'rgba(255,210,140,0.08)' },
        },
      },
      onClick: (_, elements) => {
        if (elements.length) selectDay(elements[0].index);
      },
    },
  });
}

function setChartMode(mode) {
  _chartMode = mode;
  document.getElementById('btnToggleSunset') .classList.toggle('active', mode === 'sunset');
  document.getElementById('btnToggleSunrise').classList.toggle('active', mode === 'sunrise');
  if (_weekData.length) buildChart(_weekData, mode);
}

// ── Week cards ──────────────────────────────────────────────
let _selectedDay = 0;

function renderWeekCards(days) {
  const strip = document.getElementById('weekStrip');
  strip.innerHTML = '';
  days.forEach((day, i) => {
    const g  = gradeFor(day.sunset_score.score);
    const card = document.createElement('div');
    card.className = 'day-card' + (i === 0 ? ' active' : '');
    card.dataset.idx = i;
    card.innerHTML = `
      <div class="day-name">${shortDay(day.date)}</div>
      <div class="day-date">${dayOfMonth(day.date)}</div>
      <div class="day-score" style="color:${g.color}">${day.sunset_score.score}</div>
      <div class="day-grade">${g.label}</div>
      <div class="day-sunset-time">${fmtTime(day.solar_events.sunset)}</div>
    `;
    card.addEventListener('click', () => selectDay(i));
    strip.appendChild(card);
  });
}

function selectDay(idx) {
  _selectedDay = idx;
  document.querySelectorAll('.day-card').forEach((c, i) => {
    c.classList.toggle('active', i === idx);
  });
  const day = _weekData[idx];
  if (!day) return;
  renderBreakdown(day.sunset_score, 'SUNSET');
}

// ── Breakdown strip ─────────────────────────────────────────
const FACTOR_LABELS = {
  low_cloud:  'LOW CLOUD',
  mid_cloud:  'MID CLOUD',
  aod:        'AEROSOL',
  high_cloud: 'HIGH CLOUD',
};

const PENALTY_LABELS = {
  humidity:         'HUMIDITY',
  visibility:       'VISIBILITY',
  precipitation:    'PRECIP',
  overcast_ceiling: 'OVERCAST',
};

function renderBreakdown(scoreData, label) {
  const strip = document.getElementById('breakdownStrip');
  strip.querySelector('.strip-label').textContent = `${label} — FACTOR BREAKDOWN`;

  const factors = document.getElementById('stripFactors');
  factors.innerHTML = '';

  const total = Object.values(scoreData.breakdown).reduce((a, b) => a + b, 0);

  // Factor cells
  Object.entries(scoreData.breakdown).forEach(([key, val]) => {
    const pct  = total > 0 ? (val / total) * 100 : 0;
    const g    = gradeFor(scoreData.score);
    const cell = document.createElement('div');
    cell.className = 'factor-cell';
    cell.innerHTML = `
      <div class="factor-name">${FACTOR_LABELS[key] || key.toUpperCase()}</div>
      <div class="factor-bar-track">
        <div class="factor-bar-fill" style="width:0%;background:${g.color}"></div>
      </div>
      <div class="factor-value">${val.toFixed(1)}</div>
    `;
    factors.appendChild(cell);
    // animate bar
    requestAnimationFrame(() => {
      cell.querySelector('.factor-bar-fill').style.width = pct + '%';
    });
  });

  // Penalty cells
  Object.entries(scoreData.penalties).forEach(([key, val]) => {
    const ok   = val >= 0.99;
    const cell = document.createElement('div');
    cell.className = 'factor-cell';
    cell.innerHTML = `
      <div class="factor-name">${PENALTY_LABELS[key] || key.toUpperCase()} ×</div>
      <div class="factor-bar-track">
        <div class="factor-bar-fill" style="width:0%;background:${ok ? '#8ec46a' : '#e8975a'}"></div>
      </div>
      <div class="factor-value" style="color:${ok ? '#8ec46a' : '#e8975a'}">${val.toFixed(3)}</div>
    `;
    factors.appendChild(cell);
    requestAnimationFrame(() => {
      cell.querySelector('.factor-bar-fill').style.width = (val * 100) + '%';
    });
  });

  // Flags
  const flagsEl = document.getElementById('stripFlags');
  flagsEl.innerHTML = scoreData.flags.length
    ? scoreData.flags.map(f => `<span class="flag-pill">${f.replace(/_/g,' ')}</span>`).join('')
    : '';
}

// ── Hero render ─────────────────────────────────────────────
function renderHero(today) {
  const ss = today.sunset_score;
  const sr = today.sunrise_score;
  const ev = today.solar_events;
  const gSS = gradeFor(ss.score);
  const gSR = gradeFor(sr.score);

  document.getElementById('heroSunsetTime').textContent  = fmtTime(ev.sunset);
  document.getElementById('heroSunriseTime').textContent = fmtTime(ev.sunrise);

  document.getElementById('dialSunsetScore').textContent  = ss.score;
  document.getElementById('dialSunsetGrade').textContent  = ss.grade;
  document.getElementById('dialSunriseScore').textContent = sr.score;
  document.getElementById('dialSunriseGrade').textContent = sr.grade;

  document.getElementById('dialSunsetScore').style.color  = gSS.color;
  document.getElementById('dialSunriseScore').style.color = gSR.color;

  document.getElementById('heroSunsetDesc').textContent  = ss.description;
  document.getElementById('heroSunriseDesc').textContent = sr.description;

  // Golden hour window
  if (ev.afterglow_window) {
    const [ws, we] = ev.afterglow_window;
    document.getElementById('heroSunsetWindow').textContent =
      `Golden hour: ${fmtTime(ws)} – ${fmtTime(we)}`;
  }
  if (ev.sunrise) {
    document.getElementById('heroSunriseWindow').textContent =
      `Golden hour: ${fmtTime(ev.golden_hour_start)}`;
  }

  drawDial('dialSunset',  ss.score, gSS.color);
  drawDial('dialSunrise', sr.score, gSR.color);
  applySkyGrade(ss.grade);
}

// ── Tuner setup ─────────────────────────────────────────────
const TUNER_DEFS = [
  { key: 'cloud_cover_low',       label: 'Low cloud',  min: 0,    max: 100,  step: 1,    dflt: 35,    fmt: v => v + '%' },
  { key: 'cloud_cover_mid',       label: 'Mid cloud',  min: 0,    max: 100,  step: 1,    dflt: 45,    fmt: v => v + '%' },
  { key: 'cloud_cover_high',      label: 'High cloud', min: 0,    max: 100,  step: 1,    dflt: 15,    fmt: v => v + '%' },
  { key: 'aerosol_optical_depth', label: 'AOD',        min: 0,    max: 1.2,  step: 0.01, dflt: 0.22,  fmt: v => parseFloat(v).toFixed(2) },
  { key: 'relative_humidity_2m',  label: 'Humidity',   min: 0,    max: 100,  step: 1,    dflt: 38,    fmt: v => v + '%' },
  { key: 'visibility',            label: 'Visibility', min: 500,  max: 60000,step: 500,  dflt: 40000, fmt: v => (v/1000).toFixed(0) + ' km' },
  { key: 'precipitation',         label: 'Precip',     min: 0,    max: 10,   step: 0.1,  dflt: 0,     fmt: v => parseFloat(v).toFixed(1) + ' mm' },
  { key: 'solar_elevation',       label: 'Solar elev', min: -12,  max: 3,    step: 0.1,  dflt: -4.2,  fmt: v => parseFloat(v).toFixed(1) + '°' },
];

let _tunerDebounce = null;

function buildTuner() {
  const container = document.getElementById('tunerSliders');
  TUNER_DEFS.forEach(def => {
    const row = document.createElement('div');
    row.className = 'tuner-row';
    row.innerHTML = `
      <div class="tuner-label">
        <span class="tuner-name">${def.label.toUpperCase()}</span>
        <span class="tuner-val" id="tv-${def.key}">${def.fmt(def.dflt)}</span>
      </div>
      <input class="tuner-slider" type="range"
        id="ts-${def.key}"
        min="${def.min}" max="${def.max}" step="${def.step}"
        value="${def.dflt}" />
    `;
    container.appendChild(row);
    row.querySelector('input').addEventListener('input', e => {
      document.getElementById('tv-' + def.key).textContent = def.fmt(e.target.value);
      clearTimeout(_tunerDebounce);
      _tunerDebounce = setTimeout(runTuner, 120);
    });
  });
  runTuner();
}

async function runTuner() {
  const params = {};
  TUNER_DEFS.forEach(def => {
    const el = document.getElementById('ts-' + def.key);
    if (el) params[def.key] = parseFloat(el.value);
  });

  try {
    const qs   = new URLSearchParams(params).toString();
    const resp = await fetch(`/api/score?${qs}`);
    const data = await resp.json();
    const g    = gradeFor(data.score);

    document.getElementById('tunerScore').textContent = data.score;
    document.getElementById('tunerScore').style.color = g.color;
    document.getElementById('tunerGrade').textContent = data.grade;
    drawDial('dialTuner', data.score, g.color, false);

    const bk = document.getElementById('tunerBreakdown');
    bk.innerHTML = Object.entries(data.breakdown).map(([k, v]) =>
      `<div class="tb-row">
        <span class="tb-key">${FACTOR_LABELS[k] || k}</span>
        <span class="tb-val">${v.toFixed(1)}</span>
       </div>`
    ).join('') +
    Object.entries(data.penalties).map(([k, v]) =>
      `<div class="tb-row">
        <span class="tb-key">${(PENALTY_LABELS[k] || k)} ×</span>
        <span class="tb-val" style="color:${v>=0.99?'#8ec46a':'#e8975a'}">${v.toFixed(3)}</span>
       </div>`
    ).join('');
  } catch (e) {
    console.warn('Tuner score failed:', e);
  }
}

// ── Main fetch ──────────────────────────────────────────────
function getParams() {
  return {
    lat:  parseFloat(document.getElementById('inputLat').value),
    lon:  parseFloat(document.getElementById('inputLon').value),
    tz:   document.getElementById('inputTz').value.trim(),
    elev: parseFloat(document.getElementById('inputElev').value) || 0,
  };
}

function showState(state) {
  document.getElementById('stateLoading').classList.toggle('hidden', state !== 'loading');
  document.getElementById('stateError')  .classList.toggle('hidden', state !== 'error');
  document.getElementById('forecastWrap').classList.toggle('hidden', state !== 'ready');
}

async function fetchForecast() {
  const { lat, lon, tz, elev } = getParams();
  if (isNaN(lat) || isNaN(lon)) return;

  document.getElementById('metaCoords').textContent =
    `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
  document.getElementById('btnForecast').disabled = true;
  showState('loading');

  try {
    const qs   = new URLSearchParams({ lat, lon, tz, elev }).toString();
    const resp = await fetch(`/api/forecast?${qs}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    _weekData     = data.days;
    _selectedDay  = 0;

    renderHero(data.days[0]);
    renderBreakdown(data.days[0].sunset_score, 'TONIGHT · SUNSET');
    buildChart(data.days, _chartMode);
    renderWeekCards(data.days);
    showState('ready');

  } catch (err) {
    document.getElementById('errorMsg').textContent =
      err.message || 'Could not reach the forecast service.';
    showState('error');
  } finally {
    document.getElementById('btnForecast').disabled = false;
  }
}

function retryFetch() { fetchForecast(); }

// ── GPS ─────────────────────────────────────────────────────
document.getElementById('btnGPS').addEventListener('click', () => {
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition(pos => {
    document.getElementById('inputLat').value = pos.coords.latitude.toFixed(4);
    document.getElementById('inputLon').value = pos.coords.longitude.toFixed(4);
    fetchForecast();
  });
});

// ── Wiring ──────────────────────────────────────────────────
document.getElementById('btnForecast').addEventListener('click', fetchForecast);

document.querySelectorAll('.loc-input').forEach(el => {
  el.addEventListener('keydown', e => { if (e.key === 'Enter') fetchForecast(); });
});

// ── Init ────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  buildTuner();
  fetchForecast();
});
