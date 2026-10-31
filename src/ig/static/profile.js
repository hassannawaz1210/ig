// Page data from the server: {posts, username}.
const boot = JSON.parse(document.getElementById('boot').textContent);
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
    if (!r.ok) throw new Error();
    notifyAccountChange(u, false);
  } catch (error) { el.textContent = 'retry'; }
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
const fdate = t => t ? new Date(t*1000).toLocaleDateString(
    undefined, {month:'short', day:'numeric', year:'numeric'}) : '';

let rendered = 0;
function card(p, i) {
  const badge = p.is_video ? '▶'
              : p.is_carousel ? `⧉ ${p.carousel_count || ''}` : '';
  return `<div class="card" onclick="lb(${i})">
    ${badge ? `<div class="badge">${badge}</div>` : ''}
    <div class="imgwrap"><img loading="lazy" src="${esc(p.image)}" alt=""></div>
    <div class="meta"><div class="stats">${icon('heart')} ${fmt(p.like_count)} &nbsp; ${icon('comment')} ${fmt(p.comment_count)}
      &nbsp; <span class="date">${fdate(p.taken_at)}</span></div>
    <div class="cap">${esc((p.caption||'').slice(0,140))}</div></div></div>`;
}
function render() {
  const chunk = incoming.splice(0);
  store.push(...chunk);
  grid.insertAdjacentHTML('beforeend', chunk.map((p,k)=>card(p, rendered+k)).join(''));
  rendered += chunk.length;
  count.textContent = fmt(rendered) + ' posts';
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
    return `<video controls autoplay muted loop playsinline
      poster="${esc(p.poster || p.image)}" src="${esc(p.video)}"></video>`;
  }
  return `<img src="${esc(p.image)}" alt="">`;
}
function lb(i) {
  cur = i; const p = store[i];
  document.getElementById('lbopen').href = p.url;
  document.getElementById('lbstats').innerHTML =
    `${icon('heart')} ${fmt(p.like_count)} &nbsp; ${icon('comment')} ${fmt(p.comment_count)} &nbsp; ${fdate(p.taken_at)}`;
  document.getElementById('lbcap').textContent = p.caption || '';
  const box = document.getElementById('lbcontent');
  if (p._rich) {
    box.innerHTML = richHtml(p);
  } else if (p.is_video || p.is_carousel) {
    box.innerHTML = '<div class="loading">loading media…</div>';
    fetch(location.pathname + '/media', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({code: p.shortcode}),
    }).then(r => r.json()).then(d => {
      if (d.error) { box.innerHTML = `<div class="loading">${d.error}</div>`; return; }
      Object.assign(p, d, {_rich: true});
      if (!document.getElementById('lb').hidden && cur === i)
        box.innerHTML = richHtml(p);
    });
  } else {
    box.innerHTML = `<img src="${esc(p.image)}" alt="">`;
  }
  document.getElementById('lb').hidden = false;
}
function closeLb() {
  document.querySelectorAll('#lb video').forEach(v => v.pause());
  document.getElementById('lb').hidden = true;
}
function step(d) { lb(Math.min(store.length-1, Math.max(0, cur+d))); }
document.addEventListener('keydown', e => {
  if (document.getElementById('feed-picker')) return;
  const open = !document.getElementById('lb').hidden;
  if (!open) return;
  if (e.key === 'Escape') closeLb();
  if (e.key === 'ArrowLeft') step(-1);
  if (e.key === 'ArrowRight') step(1);
});
document.getElementById('lb').addEventListener('click', e => {
  if (e.target.id === 'lb') closeLb();  // backdrop click
});

btn.addEventListener('click', async () => {
  btn.disabled = true; btn.textContent = 'loading…';
  try {
    const r = await fetch(location.pathname + '/more', {method: 'POST'});
    const data = await r.json();
    if (data.error) { btn.textContent = data.error; return; }
    incoming.push(...data.posts); render();
    if (!data.has_more) {
      btn.outerHTML = '<div class="end">end of feed 🎉</div>'; return;
    }
  } catch (e) { btn.textContent = 'network error — retry'; }
  btn.disabled = false; btn.textContent = 'Load more';
});
