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
const moreIndicator = document.getElementById('more');
const count = document.getElementById('count');
const esc = s => (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;')
                         .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const fmt = n => (n??0).toLocaleString();
const short = n => n == null ? '' : Intl.NumberFormat(undefined, {notation:'compact', maximumFractionDigits:1}).format(n);
const fdate = t => t ? new Date(t*1000).toLocaleDateString(
    undefined, {month:'short', day:'numeric', year:'numeric'}) : '';
// How old a post is, short for thumbnails ("3d", "2w", "1y") or written out.
const AGE_UNITS = [['y', 'year', 31536000], ['mo', 'month', 2592000], ['w', 'week', 604800],
                   ['d', 'day', 86400], ['h', 'hour', 3600], ['m', 'minute', 60]];
function postAge(t, {short = false} = {}, now = Date.now()) {
  if (!t) return '';
  const seconds = Math.max(0, Math.round(now / 1000 - t));
  for (const [abbr, unit, size] of AGE_UNITS) {
    const count = Math.floor(seconds / size);
    if (count >= 1) return short ? count + abbr : `${count} ${unit}${count === 1 ? '' : 's'} ago`;
  }
  return short ? 'now' : 'just now';
}

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
  const plays = p.play_count ? `<span class="plays">${icon('play') || '▶'} ${short(p.play_count)}</span>` : '';
  return `<div class="card" role="button" tabindex="0" onclick="lb(${i})" onkeydown="if(event.key==='Enter')lb(${i})">
    <div class="imgwrap">${p.image ? `<img loading="lazy" src="${esc(p.image)}" alt=""
      onerror="showMissingImage(this)">` : '<div class="missing">no preview</div>'}
      ${badge ? `<div class="badge">${badge}</div>` : ''}
      <div class="overlay"><span>${icon('heart')} ${short(p.like_count)}</span>${plays}
        ${p.taken_at ? `<span class="age" title="${esc(fdate(p.taken_at))}">${postAge(p.taken_at, {short:true})}</span>` : ''}</div></div>
    <div class="meta"><div class="stats"><span>${icon('heart')} ${fmt(p.like_count)}</span>
      <span>${icon('comment')} ${fmt(p.comment_count)}</span><span class="date" title="${esc(fdate(p.taken_at))}">${p.taken_at ? postAge(p.taken_at) + ' · ' + fdate(p.taken_at) : ''}</span></div>
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
  document.querySelector('#lb .next').disabled = i === store.length - 1 && !hasMorePosts;
  document.getElementById('lbstats').innerHTML =
    `<span>${icon('heart')} ${fmt(p.like_count)}</span><span>${icon('comment')} ${fmt(p.comment_count)}</span><span class="date">${p.taken_at ? postAge(p.taken_at) + ' · ' + fdate(p.taken_at) : ''}</span>`;
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
  if (store.length - i <= 4) loadMorePosts();
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
async function step(d) {
  const from = cur;
  if (d > 0 && cur === store.length - 1 && hasMorePosts) {
    await loadMorePosts(true);
    if (lbEl.hidden || cur !== from) return;
  }
  const next = Math.min(store.length - 1, Math.max(0, cur + d));
  if (next !== cur) lb(next);
}
document.addEventListener('keydown', e => {
  if (document.getElementById('feed-picker') || lbEl.hidden || e.target.closest('input,textarea,select')) return;
  if (e.key === 'Escape') closeLb();
  if (['ArrowUp','k','ArrowDown','j'].includes(e.key)) {
    e.preventDefault(); if (!e.repeat) step(['ArrowUp','k'].includes(e.key) ? -1 : 1);
  }
});
lbEl.addEventListener('click', e => {
  if (e.target === lbEl || e.target.id === 'lbcontent') closeLb();  // tap outside the media
});
// Vertical swipes move between posts. Horizontal swipes stay inside carousels.
let swipe = null;
function scrollsProfileDetails(target) {
  const panel = target.closest('.lbmeta');
  return target.closest('.lbcap,button,a,input') || (panel && panel.scrollHeight > panel.clientHeight + 1);
}
lbEl.addEventListener('touchstart', e => {
  if (e.touches.length !== 1 || scrollsProfileDetails(e.target)) { swipe = null; return; }
  swipe = {x:e.touches[0].clientX, y:e.touches[0].clientY};
}, {passive:true});
lbEl.addEventListener('touchmove', e => {
  if (!swipe) return;
  if (e.touches.length !== 1) { swipe = null; return; }
  const dx = e.touches[0].clientX - swipe.x, dy = e.touches[0].clientY - swipe.y;
  if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx) && e.cancelable) e.preventDefault();
}, {passive:false});
lbEl.addEventListener('touchend', e => {
  if (!swipe || e.touches.length) { swipe = null; return; }
  const t = e.changedTouches[0], dx = t.clientX - swipe.x, dy = t.clientY - swipe.y;
  swipe = null;
  if (Math.abs(dy) > 48 && Math.abs(dy) > Math.abs(dx) * 1.5) step(dy < 0 ? 1 : -1);
}, {passive:true});
lbEl.addEventListener('touchcancel', () => { swipe = null; }, {passive:true});
let lastPostWheel = -Infinity;
lbEl.addEventListener('wheel', e => {
  if (e.ctrlKey || scrollsProfileDetails(e.target) || Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
  e.preventDefault();
  const now = performance.now();
  if (now - lastPostWheel > 300) step(Math.sign(e.deltaY));
  lastPostWheel = now;
}, {passive:false});

// Keep profile pages ahead of both the grid and the open-post viewer.
// Saved reels has no pagination indicator and never makes these requests.
let hasMorePosts = Boolean(moreIndicator), moreRequest = null, retryPostsAfter = 0, moreFailures = 0, moreRetryTimer = null;
function nearProfileEnd() {
  if (document.hidden || !hasMorePosts) return false;
  const bounds = moreIndicator.getBoundingClientRect();
  return (!lbEl.hidden && store.length - cur <= 4) ||
    (lbEl.hidden && bounds.top <= innerHeight + 600 && bounds.bottom >= 0);
}
function retryProfilePosts(delay) {
  clearTimeout(moreRetryTimer);
  moreRetryTimer = setTimeout(() => { if (nearProfileEnd()) loadMorePosts(); }, delay);
}
function loadMorePosts(manual = false) {
  if (moreRequest) return moreRequest;
  if (!hasMorePosts) return Promise.resolve();
  if (!manual && Date.now() < retryPostsAfter) { retryProfilePosts(retryPostsAfter - Date.now()); return Promise.resolve(); }
  moreIndicator.textContent = 'Loading…';
  let refillAfter = false;
  const request = (async () => {
    try {
      const response = await fetch(location.pathname + '/more', {method:'POST'});
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error || 'Could not load posts');
      const known = new Set(store.map(post => post.shortcode));
      const fresh = (data.posts || []).filter(post => {
        if (known.has(post.shortcode)) return false;
        known.add(post.shortcode); return true;
      });
      incoming.push(...fresh); render();
      hasMorePosts = data.has_more !== false;
      refillAfter = hasMorePosts && fresh.length > 0;
      moreFailures = 0;
      if (!fresh.length && hasMorePosts) { retryPostsAfter = Date.now() + 3000; retryProfilePosts(3000); }
      if (!hasMorePosts) {
        postsObserver?.unobserve(moreIndicator);moreIndicator.hidden = true;
        const end = document.createElement('div');end.className='end';end.textContent='End of posts';moreIndicator.after(end);
      } else moreIndicator.textContent = fresh.length ? '' : 'Waiting for more posts…';
      if (!lbEl.hidden) {
        document.getElementById('lbpos').textContent = `${cur + 1} / ${store.length}`;
        document.querySelector('#lb .next').disabled = cur === store.length - 1 && !hasMorePosts;
      }
    } catch (error) {
      const delay = Math.min(30000, 3000 * 2 ** Math.min(moreFailures++,4));
      retryPostsAfter = Date.now() + delay; retryProfilePosts(delay);
      moreIndicator.textContent = 'Couldn’t load posts · retrying…';
      if (!lbEl.hidden) document.getElementById('lbpos').textContent = `${cur + 1} / ${store.length} · retrying…`;
    }
  })();
  moreRequest = request.finally(() => {
    moreRequest = null;
    if (refillAfter) requestAnimationFrame(() => {
      if (nearProfileEnd()) loadMorePosts();
    });
  });
  return moreRequest;
}
let postsObserver = null;
if (moreIndicator) {
  postsObserver = new IntersectionObserver(entries => {
    if (entries.some(entry => entry.isIntersecting)) loadMorePosts();
  }, {rootMargin:'600px'});
  postsObserver.observe(moreIndicator);
}
