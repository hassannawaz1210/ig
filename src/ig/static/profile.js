// Page data from the server: {posts, username, empty_message, android}.
const boot = JSON.parse(document.getElementById('boot').textContent);
if (boot.android) document.body.classList.add('android-app');
const incoming = boot.posts;

async function toggleAcc(u, el) {
  if (el.dataset.state !== 'in') {
    if (await chooseFeed(u)) {
      el.dataset.state = 'in'; el.innerHTML = icon('check') + ' in feed';
    }
    return;
  }
  el.textContent = '…';
  try {
    const r = await fetch('/remove', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({username:u})});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    notifyAccountChange(u, false);
  } catch (error) { console.warn('Could not remove @' + u, error); el.textContent = 'retry'; }
}
accountChanged = (username, saved) => {
  if (username !== boot.username) return;
  const button = document.getElementById('addacc');
  if (button) { button.dataset.state = saved ? 'in' : 'out';
    button.innerHTML = saved ? icon('check') + ' in feed' : icon('plus') + ' add to feed'; }
};
const store = [];
const grid = document.getElementById('grid');
const btn = document.getElementById('more');
const count = document.getElementById('count');
const esc = s => (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;')
                         .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const fmt = n => (n??0).toLocaleString();
const short = n => n == null ? '' : Intl.NumberFormat(undefined, {notation:'compact', maximumFractionDigits:1}).format(n);
const fdate = t => t ? new Date(t*1000).toLocaleDateString(
    undefined, {month:'short', day:'numeric', year:'numeric'}) : '';

// Layout choice (grid, cards or list), remembered in this browser.
const LAYOUTS = ['grid', 'cards', 'list'];
const LAYOUT_KEY = 'ig-profile-layout';
function savedLayout() {
  try { const value = localStorage.getItem(LAYOUT_KEY); return LAYOUTS.includes(value) ? value : 'grid'; }
  catch (error) { return 'grid'; }
}
function setLayout(layout) {
  grid.dataset.layout = layout;
  document.querySelectorAll('.layouts button').forEach(button =>
    button.setAttribute('aria-checked', String(button.dataset.layout === layout)));
  try { localStorage.setItem(LAYOUT_KEY, layout); }
  catch (error) { console.warn('Could not remember the layout', error); }
}
document.querySelectorAll('.layouts button').forEach(button =>
  button.addEventListener('click', () => setLayout(button.dataset.layout)));
setLayout(savedLayout());

let rendered = 0;
// Instagram image links expire, so old thumbnails (e.g. in Saved reels) can fail.
function showMissingImage(img) {
  const placeholder = document.createElement('div');
  placeholder.className = 'missing';
  placeholder.textContent = 'no preview';
  img.replaceWith(placeholder);
}
function card(p, i) {
  const badge = p.is_video ? '▶'
              : p.is_carousel ? `⧉ ${p.carousel_count || ''}` : '';
  const plays = p.play_count ? `<span>${icon('play') || '▶'} ${short(p.play_count)}</span>` : '';
  return `<div class="card" role="button" tabindex="0" onclick="lb(${i})" onkeydown="if(event.key==='Enter')lb(${i})">
    <div class="imgwrap">${p.image ? `<img loading="lazy" src="${esc(p.image)}" alt=""
      onerror="showMissingImage(this)">` : '<div class="missing">no preview</div>'}
      ${badge ? `<div class="badge">${badge}</div>` : ''}
      <div class="overlay"><span>${icon('heart')} ${short(p.like_count)}</span>${plays}</div></div>
    <div class="meta"><div class="stats"><span>${icon('heart')} ${fmt(p.like_count)}</span>
      <span>${icon('comment')} ${fmt(p.comment_count)}</span><span class="date">${fdate(p.taken_at)}</span></div>
    <div class="cap">${esc((p.caption||'').slice(0,240))}</div></div></div>`;
}
function render() {
  const chunk = incoming.splice(0);
  store.push(...chunk);
  grid.insertAdjacentHTML('beforeend', chunk.map((p,k)=>card(p, rendered+k)).join(''));
  rendered += chunk.length;
  count.textContent = fmt(rendered) + ' posts';
  if (!rendered && boot.empty_message && !grid.querySelector('.empty')) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = boot.empty_message;
    grid.append(empty);
  }
}
render();

let cur = 0;
function richHtml(p) {
  if (p.slides && p.slides.length) {
    return '<div class="slides">' + p.slides.map(s =>
      s.video
        ? `<video controls muted loop playsinline src="${esc(s.video)}"></video>`
        : `<img src="${esc(s.image)}" alt="">`).join('') + '</div>';
  }
  if (p.video) {
    return `<video controls autoplay loop playsinline
      poster="${esc(p.poster || p.image)}" src="${esc(p.video)}"></video>`;
  }
  return `<img src="${esc(p.image)}" alt="">`;
}
const lbEl = document.getElementById('lb');
function lb(i) {
  if (!store[i]) return;
  document.querySelectorAll('#lb video').forEach(v => v.pause());
  cur = i; const p = store[i];
  document.getElementById('lbopen').href = p.url;
  document.getElementById('lbpos').textContent = `${i + 1} / ${store.length}`;
  document.querySelector('#lb .prev').disabled = i === 0;
  document.querySelector('#lb .next').disabled = i === store.length - 1;
  document.getElementById('lbstats').innerHTML =
    `<span>${icon('heart')} ${fmt(p.like_count)}</span><span>${icon('comment')} ${fmt(p.comment_count)}</span><span class="date">${fdate(p.taken_at)}</span>`;
  document.getElementById('lbcap').textContent = p.caption || '';
  const box = document.getElementById('lbcontent');
  if (p._rich) {
    box.innerHTML = richHtml(p);
  } else if (p.is_video || p.is_carousel) {
    box.innerHTML = '<div class="loading">Loading…</div>';
    fetch(location.pathname + '/media', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({code: p.shortcode}),
    }).then(r => r.json()).then(d => {
      if (d.error) { if (cur === i) box.innerHTML = `<div class="loading">${esc(d.error)}</div>`; return; }
      Object.assign(p, d, {_rich: true});
      if (!lbEl.hidden && cur === i) box.innerHTML = richHtml(p);
    }).catch(error => {
      console.warn('Could not load media for ' + p.shortcode, error);
      if (cur === i) box.innerHTML = '<div class="loading">Could not load media · try again</div>';
    });
  } else {
    box.innerHTML = `<img src="${esc(p.image)}" alt="">`;
  }
  if (lbEl.hidden) {
    lbEl.hidden = false;
    document.body.style.overflow = 'hidden';
    // Back (phone button or browser) closes the viewer instead of leaving the page.
    history.pushState({viewer:true}, '');
  }
}
function hideLb() {
  document.querySelectorAll('#lb video').forEach(v => v.pause());
  lbEl.hidden = true;
  document.body.style.overflow = '';
}
function closeLb() {
  if (lbEl.hidden) return;
  if (history.state?.viewer) history.back();  // popstate hides it
  else hideLb();
}
window.addEventListener('popstate', () => { if (!lbEl.hidden) hideLb(); });
function step(d) { lb(Math.min(store.length-1, Math.max(0, cur+d))); }
document.addEventListener('keydown', e => {
  if (document.getElementById('feed-picker') || lbEl.hidden) return;
  if (e.key === 'Escape') closeLb();
  if (e.key === 'ArrowLeft') step(-1);
  if (e.key === 'ArrowRight') step(1);
});
lbEl.addEventListener('click', e => {
  if (e.target === lbEl || e.target.id === 'lbcontent') closeLb();  // tap outside the media
});
// Swipe left/right for the next/previous post; swipe down to close.
// Carousels scroll sideways themselves, so they keep their own swipes.
let swipe = null;
lbEl.addEventListener('touchstart', e => {
  if (e.touches.length !== 1 || e.target.closest('.slides, .lbcap')) { swipe = null; return; }
  swipe = {x:e.touches[0].clientX, y:e.touches[0].clientY};
}, {passive:true});
lbEl.addEventListener('touchend', e => {
  if (!swipe) return;
  const t = e.changedTouches[0], dx = t.clientX - swipe.x, dy = t.clientY - swipe.y;
  swipe = null;
  if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) step(dx < 0 ? 1 : -1);
  else if (dy > 100 && dy > Math.abs(dx) * 1.5) closeLb();
}, {passive:true});

// Pages that show every post at once (Saved reels) have no Load more button.
if (btn) btn.addEventListener('click', async () => {
  btn.disabled = true; btn.textContent = 'Loading…';
  try {
    const r = await fetch(location.pathname + '/more', {method: 'POST'});
    const data = await r.json();
    if (data.error) { btn.textContent = data.error + ' · tap to retry'; btn.disabled = false; return; }
    incoming.push(...data.posts); render();
    if (!data.has_more) {
      btn.outerHTML = '<div class="end">End of posts</div>'; return;
    }
  } catch (error) {
    console.warn('Could not load more posts', error);
    btn.textContent = 'Network error · tap to retry'; btn.disabled = false; return;
  }
  btn.disabled = false; btn.textContent = 'Load more';
});
