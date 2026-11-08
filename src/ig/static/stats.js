// Settings → Your stats. Numbers come from /stats/data?days=7|30|0 (stats.py).
const boot = JSON.parse(document.getElementById('boot').textContent);
if (boot.android) document.body.classList.add('android-app');
const status = document.getElementById('settings-status');
autoHideStatus(status);
const tooltip = document.getElementById('tooltip');
const SVG = 'http://www.w3.org/2000/svg';
const SERIES = ['--series-1', '--series-2', '--series-3', '--series-4', '--series-5', '--series-6', '--series-7'];
let current = null, loadVersion = 0, showWeeksTable = false;

const number = n => (n ?? 0).toLocaleString();
const percent = n => `${Math.round((n || 0) * 100)}%`;
function duration(seconds) {
  seconds = Math.round(seconds || 0);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
const day = t => new Date(t * 1000).toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
function node(tag, attrs = {}, text) {
  const el = tag.startsWith('svg:') ? document.createElementNS(SVG, tag.slice(4)) : document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  if (text !== undefined) el.textContent = text;
  return el;
}

// ---- tooltip: hover on desktop, tap on touch ----
function showTip(event, lines) {
  tooltip.replaceChildren(...lines.map(line => {
    const row = node('div');
    if (line.color) row.append(node('span', {class: 'swatch', style: `background:${line.color}`}));
    if (line.bold) row.append(node('b', {}, line.bold), document.createTextNode(line.text ? ' ' + line.text : ''));
    else row.append(document.createTextNode(line.text));
    return row;
  }));
  tooltip.hidden = false;
  const x = Math.min(event.clientX + 12, innerWidth - tooltip.offsetWidth - 8);
  const y = Math.max(8, event.clientY - tooltip.offsetHeight - 12);
  tooltip.style.left = Math.max(8, x) + 'px'; tooltip.style.top = y + 'px';
}
const hideTip = () => { tooltip.hidden = true; };
function bindTip(target, lines) {
  target.addEventListener('pointermove', event => showTip(event, lines()));
  target.addEventListener('pointerdown', event => showTip(event, lines()));
  target.addEventListener('pointerleave', hideTip);
}
document.addEventListener('scroll', hideTip, {passive: true});
document.addEventListener('pointerdown', event => { if (!event.target.closest?.('.hit')) hideTip(); });

// ---- sections ----
function renderTiles(summary) {
  const tiles = [
    ['Reels watched', number(summary.reels), `${number(summary.creators)} creators`],
    ['Watch time', duration(summary.seconds), summary.reels ? `${duration(summary.seconds / summary.reels)} per reel` : ''],
    ['New creators', number(summary.new_creators), 'not in your feeds'],
    ['Likes', number(summary.likes), `${number(summary.skips)} quick skips`],
  ];
  const box = document.getElementById('tiles');
  box.replaceChildren(...tiles.map(([label, value, detail]) => {
    const tile = node('div', {class: 'tile'});
    tile.append(node('span', {}, label), node('b', {}, value));
    if (detail) tile.append(node('small', {class: 'muted'}, detail));
    return tile;
  }));
  box.setAttribute('aria-busy', 'false');
}

function listRow(name, value, share, details, href) {
  const row = node('div', {class: 'list-row'});
  const label = node('span', {class: 'name'});
  if (href) label.append(node('a', {href}, name)); else label.textContent = name;
  const track = node('div', {class: 'track', 'aria-hidden': 'true'});
  track.append(node('i', {style: `width:${Math.max(1, share * 100).toFixed(1)}%`}));
  const detail = node('div', {class: 'detail'});
  for (const item of details) detail.append(typeof item === 'string' ? node('span', {}, item) : item);
  row.append(label, node('span', {class: 'value'}, value), track, detail);
  return row;
}

function renderCreators(creators) {
  const box = document.getElementById('creators');
  if (!creators.length) { box.replaceChildren(node('p', {class: 'empty-note'}, 'Nothing watched in this period yet.')); return; }
  const top = creators[0].seconds || 1;
  box.replaceChildren(...creators.map(c => listRow('@' + c.owner, duration(c.seconds), c.seconds / top,
    [`${number(c.reels)} reel${c.reels === 1 ? '' : 's'}`, ...(c.likes ? [`${number(c.likes)} liked`] : []),
     c.source === 'feed' ? 'in your feeds' : 'new creator'], '/u/' + encodeURIComponent(c.owner))));
}

const FEW = 20;   // below this many reels, rates are too noisy to compare
function renderSources(sources) {
  const box = document.getElementById('sources');
  if (!sources.length) { box.replaceChildren(node('p', {class: 'empty-note'}, 'Nothing watched in this period yet.')); return; }
  const longest = Math.max(...sources.map(s => s.average_seconds), 1);
  box.replaceChildren(...sources.map(s => {
    const details = [`${number(s.reels)} reels`, `${percent(s.like_rate)} liked`, `${percent(s.skip_rate)} skipped`];
    if (s.reels < FEW) details.push(node('span', {class: 'few'}, 'too few to judge'));
    return listRow(s.label, `${duration(s.average_seconds)} avg`, s.average_seconds / longest, details);
  }));
}

function color(index, key) {
  if (key === '__other__') return 'var(--series-other)';
  return `var(${SERIES[index % SERIES.length]})`;
}
function resolved(cssColor) {
  const probe = node('span', {style: `color:${cssColor}`}); document.body.append(probe);
  const value = getComputedStyle(probe).color; probe.remove(); return value;
}

function renderWeeks(weeks) {
  const box = document.getElementById('weeks'), legend = document.getElementById('weeks-legend');
  const series = weeks.series;
  const colors = {};
  let slot = 0;
  for (const s of series) colors[s.key] = color(s.key === '__other__' ? -1 : slot++, s.key);
  const totals = weeks.weeks.map(w => Object.values(w.seconds).reduce((a, b) => a + b, 0));
  if (!series.length || !totals.some(Boolean)) {
    box.replaceChildren(node('p', {class: 'empty-note'}, 'Watch a few reels and this fills in week by week.'));
    legend.replaceChildren(); return;
  }
  if (showWeeksTable) {
    const table = node('table', {class: 'data-table'});
    const head = node('tr'); head.append(node('th', {}, 'Week'), ...series.map(s => node('th', {}, s.label)), node('th', {}, 'Total'));
    table.append(head);
    weeks.weeks.forEach((w, i) => {
      const tr = node('tr'); tr.append(node('td', {}, day(w.start)), ...series.map(s => node('td', {}, duration(w.seconds[s.key] || 0))),
        node('td', {}, duration(totals[i])));
      table.append(tr);
    });
    const wrap = node('div', {class: 'data-table-wrap'}); wrap.append(table);
    box.replaceChildren(wrap);
  } else {
    const W = 600, H = 210, left = 40, bottom = 22, top = 8;
    const max = Math.max(...totals) || 1;
    const svg = node('svg:svg', {class: 'chart', viewBox: `0 0 ${W} ${H}`, role: 'img',
      'aria-label': 'Watch time each week by feed'});
    for (let k = 0; k <= 3; k++) {
      const y = top + (H - top - bottom) * (1 - k / 3);
      svg.append(node('svg:line', {class: 'gridline', x1: left, x2: W, y1: y, y2: y}));
      svg.append(node('svg:text', {x: left - 6, y: y + 4, 'text-anchor': 'end'}, duration(max * k / 3)));
    }
    const band = (W - left) / weeks.weeks.length, barWidth = Math.min(26, band * 0.6);
    weeks.weeks.forEach((w, i) => {
      const x = left + band * i + (band - barWidth) / 2;
      let y = H - bottom;
      const parts = series.filter(s => w.seconds[s.key] > 0);
      parts.forEach((s, k) => {
        const height = (H - top - bottom) * w.seconds[s.key] / max;
        const gap = k === parts.length - 1 ? 0 : 2;   // surface gap between stacked parts
        const isTop = k === parts.length - 1;
        const segment = node('svg:rect', {x, y: y - height + gap, width: barWidth, height: Math.max(0.5, height - gap),
          rx: isTop ? 4 : 0, fill: colors[s.key], class: 'mark'});
        if (isTop && height > 4) {
          // Round only the top: square off the bottom with a second rect.
          svg.append(node('svg:rect', {x, y: y - Math.min(height, 6) + gap, width: barWidth,
            height: Math.max(0, Math.min(height, 6) - gap), fill: colors[s.key]}));
        }
        svg.append(segment);
        y -= height;
      });
      const hit = node('svg:rect', {class: 'hit', x: left + band * i, y: top, width: band, height: H - top - bottom});
      bindTip(hit, () => [{bold: 'Week of ' + day(w.start), text: '· ' + duration(totals[i])},
        ...series.filter(s => w.seconds[s.key] > 0).reverse().map(s => ({color: resolved(colors[s.key]), text: `${s.label}: ${duration(w.seconds[s.key])}`}))]);
      svg.append(hit);
      if (i % 2 === (weeks.weeks.length - 1) % 2)
        svg.append(node('svg:text', {x: left + band * i + band / 2, y: H - 6, 'text-anchor': 'middle'}, day(w.start)));
    });
    box.replaceChildren(svg);
  }
  legend.replaceChildren(...series.map(s => {
    const item = node('span'); item.append(node('i', {style: `background:${colors[s.key]}`}), document.createTextNode(s.label));
    return item;
  }));
}

function renderTags(tags) {
  const box = document.getElementById('tags');
  if (!tags.enough_history || (!tags.rising.length && !tags.falling.length)) {
    box.replaceChildren(node('p', {class: 'empty-note'},
      'Needs a few weeks of watching: this compares the last 2 weeks with the time before.'));
    return;
  }
  const column = (title, items, sign) => {
    const col = node('div'); col.append(node('h3', {}, title));
    const list = node('ol');
    if (!items.length) list.append(node('li', {class: 'muted'}, 'None'));
    for (const item of items) {
      const li = node('li'); li.append(node('span', {}, '#' + item.tag), node('span', {}, `${sign}${Math.abs(item.change)} pts`));
      list.append(li);
    }
    col.append(list); return col;
  };
  const grid = node('div', {class: 'tag-columns'});
  grid.append(column('Rising', tags.rising, '+'), column('Fading', tags.falling, '−'));
  box.replaceChildren(grid);
}

function renderHours(hours) {
  const box = document.getElementById('hours');
  if (!hours.some(Boolean)) { box.replaceChildren(node('p', {class: 'empty-note'}, 'Nothing watched in this period yet.')); return; }
  const W = 600, H = 150, left = 40, bottom = 22, top = 8, max = Math.max(...hours);
  const svg = node('svg:svg', {class: 'chart', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Watch time by hour of day'});
  for (let k = 0; k <= 2; k++) {
    const y = top + (H - top - bottom) * (1 - k / 2);
    svg.append(node('svg:line', {class: 'gridline', x1: left, x2: W, y1: y, y2: y}));
    svg.append(node('svg:text', {x: left - 6, y: y + 4, 'text-anchor': 'end'}, duration(max * k / 2)));
  }
  const band = (W - left) / 24, barWidth = Math.max(4, band - 4);
  hours.forEach((seconds, hour) => {
    const height = (H - top - bottom) * seconds / max;
    const x = left + band * hour + (band - barWidth) / 2;
    if (seconds) {
      svg.append(node('svg:rect', {x, y: H - bottom - height, width: barWidth, height: Math.max(1, height), rx: Math.min(4, barWidth / 2),
        fill: 'var(--bar)', class: 'mark'}));
      if (height > 6) svg.append(node('svg:rect', {x, y: H - bottom - Math.min(height, 6) / 2, width: barWidth,
        height: Math.min(height, 6) / 2, fill: 'var(--bar)'}));
    }
    const hit = node('svg:rect', {class: 'hit', x: left + band * hour, y: top, width: band, height: H - top - bottom});
    const label = `${String(hour).padStart(2, '0')}:00–${String((hour + 1) % 24).padStart(2, '0')}:00`;
    bindTip(hit, () => [{bold: label, text: '· ' + duration(seconds)}]);
    svg.append(hit);
    if (hour % 6 === 0) svg.append(node('svg:text', {x: left + band * hour + band / 2, y: H - 6, 'text-anchor': 'middle'},
      `${String(hour).padStart(2, '0')}:00`));
  });
  box.replaceChildren(svg);
}

function render(data) {
  current = data;
  document.getElementById('since').textContent = data.since
    ? `Recorded on this device since ${new Date(data.since * 1000).toLocaleDateString(undefined, {month: 'long', day: 'numeric', year: 'numeric'})}.`
    : 'Nothing recorded on this device yet.';
  renderTiles(data.summary);
  renderCreators(data.creators);
  renderSources(data.sources);
  renderWeeks(data.weeks);
  renderTags(data.tags);
  renderHours(data.hours);
}

async function load(days) {
  const version = ++loadVersion;
  document.getElementById('tiles').setAttribute('aria-busy', 'true');
  try {
    const data = await settingsRequest('/stats/data?days=' + encodeURIComponent(days));
    if (version === loadVersion) render(data);
  } catch (error) {
    if (version === loadVersion) status.textContent = error.message || 'Could not load your stats. Try again.';
  }
}
document.getElementById('period').addEventListener('change', event => load(event.target.value));
document.getElementById('weeks-table-toggle').addEventListener('click', event => {
  showWeeksTable = !showWeeksTable;
  event.currentTarget.setAttribute('aria-pressed', String(showWeeksTable));
  event.currentTarget.textContent = showWeeksTable ? 'Show as chart' : 'Show as table';
  if (current) renderWeeks(current.weeks);
});
load(document.querySelector('#period input:checked').value);
