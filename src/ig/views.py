from .icons import icon, icons_js
from .config import prefer_memes, blocked_accounts
import json
from .feeds import feed_accounts



def feed_picker_js() -> str:
    return icons_js() + """
let accountChanged = () => {};
function notifyAccountChange(username, saved) {
  accountChanged(username, saved);
  try { localStorage.setItem('ig-account-change', JSON.stringify({username, saved, at:Date.now()})); } catch (_) {}
}
if (typeof window !== 'undefined') window.addEventListener('storage', event => {
  if (event.key !== 'ig-account-change' || !event.newValue) return;
  try { const change = JSON.parse(event.newValue); accountChanged(change.username, change.saved); } catch (_) {}
});
async function chooseFeed(username) {
  if (document.getElementById('feed-picker')) return false;
  if (!document.getElementById('feed-picker-style')) {
    const style = document.createElement('style');
    style.id = 'feed-picker-style';
    style.textContent = `
      #feed-picker {color-scheme:dark;box-sizing:border-box;width:400px;max-width:calc(100vw - 32px);
        padding:0;border:1px solid #34343e;border-radius:22px;background:#17171d;color:#f5f5f7;
        box-shadow:0 28px 100px #0009;font:14px/1.5 system-ui,sans-serif;overflow:hidden;}
      #feed-picker::backdrop {background:#080810b8;backdrop-filter:blur(8px);}
      #feed-picker .fp-head {padding:24px 24px 18px;position:relative;}
      #feed-picker h2 {margin:0 36px 18px 0;font-size:21px;line-height:1.3;letter-spacing:-.4px;}
      #feed-picker .fp-close {position:absolute;right:18px;top:18px;width:32px;height:32px;
        border:0;border-radius:50%;background:#26262f;color:#a9a9b7;font-size:22px;cursor:pointer;}
      #feed-picker .fp-account {display:flex;align-items:center;gap:12px;}
      #feed-picker .fp-avatar {display:grid;place-items:center;width:42px;height:42px;
        border-radius:14px;background:#302b48;color:#c4b5fd;font-size:20px;font-weight:600;}
      #feed-picker .fp-username {font-weight:600;font-size:15px;overflow-wrap:anywhere;}
      #feed-picker .fp-hint {color:#9696a6;font-size:12px;margin-top:2px;}
      #feed-picker .fp-list {padding:0 12px 12px;max-height:45vh;overflow-y:auto;}
      #feed-picker .fp-row {width:100%;display:flex;align-items:center;gap:12px;text-align:left;
        padding:14px 12px;border:1px solid transparent;border-radius:12px;background:transparent;
        color:inherit;font:inherit;cursor:pointer;}
      #feed-picker .fp-row:hover:not(:disabled) {background:#24232f;border-color:#3b3552;}
      #feed-picker button:focus-visible {outline:2px solid #b9a3ff;outline-offset:-2px;}
      #feed-picker .fp-symbol {display:grid;place-items:center;width:36px;height:36px;
        border-radius:10px;background:#262630;color:#c4b5fd;font-size:18px;}
      #feed-picker .fp-name {font-weight:600;text-transform:capitalize;}
      #feed-picker .fp-count {color:#858596;font-size:12px;}
      #feed-picker .fp-action .icon {width:16px;height:16px;}
      #feed-picker .fp-close {display:grid;place-items:center;}
      #feed-picker .fp-action {margin-left:auto;color:#c4b5fd;font-size:13px;}
      #feed-picker .fp-row:disabled {cursor:default;opacity:.6;}
      #feed-picker .fp-footer {padding:14px 24px;border-top:1px solid #292932;color:#858596;font-size:12px;}
      #feed-picker .fp-status {margin:0;padding:0 24px 12px;color:#c4b5fd;}
      #feed-picker .fp-status:empty {display:none;}`;
    document.head.append(style);
  }
  const dialog = document.createElement('dialog');
  dialog.id = 'feed-picker';
  dialog.setAttribute('aria-labelledby', 'feed-picker-title');
  dialog.innerHTML = `<div class="fp-head">
    <h2 id="feed-picker-title">Add to a feed</h2>
    <button class="fp-close" aria-label="Close">${icon('close')}</button>
    <div class="fp-account"><span class="fp-avatar">${icon('user')}</span><div>
      <div class="fp-username"></div><div class="fp-hint">Choose a feed for this account</div>
    </div></div></div>
    <div class="fp-list" aria-label="Your feeds"></div>
    <p class="fp-status" role="status" aria-live="polite">Loading feeds…</p>
    <div class="fp-footer">Select a feed to save · Esc to close</div>`;
  document.body.append(dialog);
  dialog.querySelector('.fp-username').textContent = '@' + username;
  const list = dialog.querySelector('.fp-list');
  const status = dialog.querySelector('.fp-status');
  let saving = false;
  const result = new Promise(resolve => {
    dialog.addEventListener('close', () => { dialog.remove(); resolve(dialog.returnValue === 'added'); }, {once:true});
    dialog.querySelector('.fp-close').onclick = () => dialog.close('cancel');
  });
  dialog.showModal();
  try {
    const r = await fetch('/feeds?username=' + encodeURIComponent(username));
    if (!r.ok) throw new Error('Could not load feeds. Close and try again.');
    const feeds = await r.json();
    if (!dialog.isConnected) return result;
    status.textContent = '';
    feeds.forEach(feed => {
      const row = document.createElement('button');
      row.className = 'fp-row';
      row.innerHTML = `<span class="fp-symbol">${icon('folder')}</span><span><span class="fp-name"></span>
        <div class="fp-count"></div></span><span class="fp-action"></span>`;
      row.querySelector('.fp-name').textContent = feed.name;
      row.querySelector('.fp-count').textContent = feed.count + (feed.count === 1 ? ' account' : ' accounts');
      row.querySelector('.fp-action').innerHTML = (feed.saved ? icon('check') : icon('plus')) + (feed.saved ? ' Added' : ' Add');
      row.disabled = feed.saved;
      row.onclick = async () => {
        if (saving) return;
        saving = true;
        status.textContent = 'Adding to ' + feed.name + '…';
        try {
          const response = await fetch('/add', {method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({username, feed:feed.name})});
          if (!response.ok) throw new Error('Could not save. Try again.');
          notifyAccountChange(username, true);
          if (dialog.isConnected) dialog.close('added');
        } catch (error) { status.textContent = error.message; }
        finally { saving = false; }
      };
      list.append(row);
    });
    list.querySelector('button:not(:disabled)')?.focus();
  } catch (error) { status.textContent = error.message; }
  return result;
}
"""


def viewer_html(title: str, subtitle: str, posts: list[dict],
                username: str = "") -> str:
    seed = json.dumps(posts, ensure_ascii=False).replace("</", "<\\/")
    if username:
        state = "in" if username in feed_accounts() else "out"
        addbtn = (f'<button id="addacc" data-state="{state}" '
                  f'onclick="toggleAcc(\'{username}\', this)">'
                  + (icon("check") + " in feed" if state == "in" else icon("plus") + " add to feed")
                  + "</button>")
    else:
        addbtn = ""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
  .icon {{ width:22px; height:22px; display:inline-block; vertical-align:middle; flex-shrink:0; }}
  .stats .icon, .lbstats .icon {{ width:16px; height:16px; }}
  body {{ margin:0; background:#0b0b0f; color:#eaeaea; font-family:system-ui,sans-serif; }}
  header {{ padding:24px 28px 8px; position:sticky; top:0; background:#0b0b0fee;
            backdrop-filter:blur(6px); z-index:5; display:flex;
            align-items:flex-start; gap:12px; }}
  .hleft {{ flex:1; }}
  header h1 {{ margin:0 0 4px; font-size:22px; }}
  header p {{ margin:0; color:#9a9aa5; font-size:14px; }}
  #addacc {{ border:0; background:#000a; color:#fff; border-radius:18px;
            padding:8px 16px; cursor:pointer; font-size:13px; margin-top:6px; }}
  #addacc[data-state="in"] {{ opacity:.6; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr));
           gap:14px; padding:20px 28px 40px; }}
  .card {{ background:#15151c; border-radius:12px; overflow:hidden; cursor:zoom-in;
           position:relative;
           content-visibility:auto; contain-intrinsic-size:320px; }}
  .card:hover {{ transform:translateY(-3px); }}
  .badge {{ position:absolute; top:8px; right:10px; background:#000000b3;
            border-radius:8px; padding:2px 8px; font-size:13px; }}
  .imgwrap {{ aspect-ratio:1; background:#000; }}
  .imgwrap img {{ width:100%; height:100%; object-fit:cover; display:block; }}
  .meta {{ padding:10px 12px 12px; }}
  .stats {{ font-size:13px; color:#cfcfd6; }}
  .date {{ color:#7d7d8a; }}
  .cap {{ font-size:13px; color:#9a9aa5; margin-top:6px; line-height:1.4;
          display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }}
  #more {{ display:block; margin:0 auto 60px; padding:12px 34px; font-size:15px;
           border:0; border-radius:24px; background:#3737e6; color:#fff; cursor:pointer; }}
  #more:disabled {{ background:#333344; color:#8a8aa0; cursor:wait; }}
  .end {{ text-align:center; color:#7d7d8a; padding:0 0 60px; }}

  .lb {{ position:fixed; inset:0; z-index:50; background:#000000e6;
         display:flex; flex-direction:column; align-items:center;
         justify-content:center; gap:14px; padding:24px; }}
  .lb img, .lb video {{ max-width:min(92vw,1000px); max-height:76vh;
             object-fit:contain; border-radius:8px; }}
  .slides {{ display:flex; gap:8px; overflow-x:auto; max-width:96vw;
             scroll-snap-type:x mandatory; }}
  .slides img, .slides video {{ height:70vh; max-width:92vw;
             scroll-snap-align:center; }}
  .loading {{ color:#7d7d8a; font-size:15px; padding:40px; }}
  .lbmeta {{ max-width:min(92vw,760px); text-align:center; }}
  .lbstats {{ font-size:14px; color:#cfcfd6; }}
  .lbcap {{ font-size:14px; color:#9a9aa5; margin-top:8px; line-height:1.5;
            white-space:pre-wrap; max-height:16vh; overflow:auto; }}
  .lbbtns {{ display:flex; gap:12px; }}
  .lbbtns a, .lbbtns button {{ padding:8px 20px; border-radius:20px; font-size:14px;
      border:1px solid #3a3a52; background:#15151c; color:#cfcfd6;
      text-decoration:none; cursor:pointer; }}
  .lbbtns a:hover, .lbbtns button:hover {{ border-color:#6a6a9a; }}
  .lb .x {{ position:absolute; top:14px; right:18px; font-size:22px; background:none;
            border:0; color:#9a9aa5; cursor:pointer; }}
  .lb .nav {{ position:absolute; top:50%; font-size:34px; background:none; border:0;
              color:#9a9aa5; cursor:pointer; padding:16px 10px; }}
  .lb .prev {{ left:8px; }} .lb .next {{ right:8px; }}
  [hidden] {{ display:none !important; }}
</style></head>
<body>
<header>
  <div class="hleft">
    <h1>{title}</h1>
    <p><span id="count"></span> loaded · {subtitle}</p>
  </div>
  {addbtn}
  
</header>
<div class="grid" id="grid"></div>
<button id="more">Load more</button>

<div class="lb" id="lb" hidden>
  <button class="x" onclick="closeLb()" title="close (Esc)">✕</button>
  <button class="nav prev" onclick="step(-1)" title="previous (←)">‹</button>
  <div id="lbcontent"></div>
  <div class="lbmeta">
    <div class="lbstats" id="lbstats"></div>
    <div class="lbcap" id="lbcap"></div>
  </div>
  <div class="lbbtns">
    <a id="lbopen" href="#" target="_blank" rel="noopener">open on Instagram {icon("external")}</a>
    <button onclick="closeLb()">close</button>
  </div>
  <button class="nav next" onclick="step(1)" title="next (→)">›</button>
</div>

<script id="seed" type="application/json">{seed}</script>
<script>
{feed_picker_js()}
const incoming = JSON.parse(document.getElementById('seed').textContent);

async function toggleAcc(u, el) {{
  if (el.dataset.state !== 'in') {{
    if (await chooseFeed(u)) {{
      el.dataset.state = 'in'; el.innerHTML = icon('check') + ' in feed';
    }}
    return;
  }}
  el.textContent = '…';
  try {{
    const r = await fetch('/remove', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{username:u}})}});
    if (!r.ok) throw new Error();
    notifyAccountChange(u, false);
  }} catch (error) {{ el.textContent = 'retry'; }}
}}
accountChanged = (username, saved) => {{
  if (username !== {json.dumps(username)}) return;
  const button = document.getElementById('addacc');
  if (button) {{ button.dataset.state = saved ? 'in' : 'out';
    button.innerHTML = saved ? icon('check') + ' in feed' : icon('plus') + ' add to feed'; }}
}};
const store = [];
const grid = document.getElementById('grid');
const btn = document.getElementById('more');
const count = document.getElementById('count');
const esc = s => (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;')
                         .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const fmt = n => (n??0).toLocaleString();
const fdate = t => t ? new Date(t*1000).toLocaleDateString(
    undefined, {{month:'short', day:'numeric', year:'numeric'}}) : '';

let rendered = 0;
function card(p, i) {{
  const badge = p.is_video ? '▶'
              : p.is_carousel ? `⧉ ${{p.carousel_count || ''}}` : '';
  return `<div class="card" onclick="lb(${{i}})">
    ${{badge ? `<div class="badge">${{badge}}</div>` : ''}}
    <div class="imgwrap"><img loading="lazy" src="${{esc(p.image)}}" alt=""></div>
    <div class="meta"><div class="stats">${{icon('heart')}} ${{fmt(p.like_count)}} &nbsp; ${{icon('comment')}} ${{fmt(p.comment_count)}}
      &nbsp; <span class="date">${{fdate(p.taken_at)}}</span></div>
    <div class="cap">${{esc((p.caption||'').slice(0,140))}}</div></div></div>`;
}}
function render() {{
  const chunk = incoming.splice(0);
  store.push(...chunk);
  grid.insertAdjacentHTML('beforeend', chunk.map((p,k)=>card(p, rendered+k)).join(''));
  rendered += chunk.length;
  count.textContent = fmt(rendered) + ' posts';
}}
render();

let cur = 0;
function richHtml(p) {{
  if (p.slides && p.slides.length) {{
    return '<div class="slides">' + p.slides.map(s =>
      s.video
        ? `<video controls muted loop playsinline src="${{esc(s.video)}}"></video>`
        : `<img src="${{esc(s.image)}}" alt="">`).join('') + '</div>';
  }}
  if (p.video) {{
    return `<video controls autoplay muted loop playsinline
      poster="${{esc(p.poster || p.image)}}" src="${{esc(p.video)}}"></video>`;
  }}
  return `<img src="${{esc(p.image)}}" alt="">`;
}}
function lb(i) {{
  cur = i; const p = store[i];
  document.getElementById('lbopen').href = p.url;
  document.getElementById('lbstats').innerHTML =
    `${{icon('heart')}} ${{fmt(p.like_count)}} &nbsp; ${{icon('comment')}} ${{fmt(p.comment_count)}} &nbsp; ${{fdate(p.taken_at)}}`;
  document.getElementById('lbcap').textContent = p.caption || '';
  const box = document.getElementById('lbcontent');
  if (p._rich) {{
    box.innerHTML = richHtml(p);
  }} else if (p.is_video || p.is_carousel) {{
    box.innerHTML = '<div class="loading">loading media…</div>';
    fetch(location.pathname + '/media', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{code: p.shortcode}}),
    }}).then(r => r.json()).then(d => {{
      if (d.error) {{ box.innerHTML = `<div class="loading">${{d.error}}</div>`; return; }}
      Object.assign(p, d, {{_rich: true}});
      if (!document.getElementById('lb').hidden && cur === i)
        box.innerHTML = richHtml(p);
    }});
  }} else {{
    box.innerHTML = `<img src="${{esc(p.image)}}" alt="">`;
  }}
  document.getElementById('lb').hidden = false;
}}
function closeLb() {{
  document.querySelectorAll('#lb video').forEach(v => v.pause());
  document.getElementById('lb').hidden = true;
}}
function step(d) {{ lb(Math.min(store.length-1, Math.max(0, cur+d))); }}
document.addEventListener('keydown', e => {{
  if (document.getElementById('feed-picker')) return;
  const open = !document.getElementById('lb').hidden;
  if (!open) return;
  if (e.key === 'Escape') closeLb();
  if (e.key === 'ArrowLeft') step(-1);
  if (e.key === 'ArrowRight') step(1);
}});
document.getElementById('lb').addEventListener('click', e => {{
  if (e.target.id === 'lb') closeLb();  // backdrop click
}});

btn.addEventListener('click', async () => {{
  btn.disabled = true; btn.textContent = 'loading…';
  try {{
    const r = await fetch(location.pathname + '/more', {{method: 'POST'}});
    const data = await r.json();
    if (data.error) {{ btn.textContent = data.error; return; }}
    incoming.push(...data.posts); render();
    if (!data.has_more) {{
      btn.outerHTML = '<div class="end">end of feed 🎉</div>'; return;
    }}
  }} catch (e) {{ btn.textContent = 'network error — retry'; }}
  btn.disabled = false; btn.textContent = 'Load more';
}});
</script>
</body></html>"""


def reels_html(posts: list[dict]) -> str:
    seed = json.dumps(posts, ensure_ascii=False).replace("</", "<\\/")
    feeded = json.dumps(sorted(feed_accounts()))
    blocked = json.dumps(sorted(blocked_accounts()))
    preference = json.dumps(prefer_memes())
    checked = "checked" if prefer_memes() else ""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Reels</title>
<style>
  .icon {{ width:22px; height:22px; display:inline-block; vertical-align:middle; flex-shrink:0; }}
  .stats .icon, .lbstats .icon {{ width:16px; height:16px; }}
  body {{ margin:0; background:#000; color:#fff; font-family:system-ui,sans-serif;
         overflow:hidden; }}
  #feed {{ height:100dvh; overflow-y:scroll; scroll-snap-type:y mandatory;
           scrollbar-width:none; }}
  #feed::-webkit-scrollbar {{ display:none; }}
  .slide {{ height:100dvh; scroll-snap-align:start; scroll-snap-stop:always;
            position:relative; display:flex; align-items:center;
            justify-content:center; }}
  .slide img.poster, .slide video {{ position:absolute; inset:0; width:100%;
            height:100%; object-fit:contain; background:#000; }}
  .spin {{ position:absolute; top:50%; left:50%; transform:translate(-50%,-50%);
           font-size:15px; color:#cfcfd6; z-index:2; }}
  .bar {{ position:absolute; right:10px; bottom:100px; display:flex;
          flex-direction:column; gap:20px; align-items:center; font-size:12px;
          text-align:center; z-index:3; }}
  .ico {{ display:flex; flex-direction:column; align-items:center; gap:5px;
          color:#fff; text-decoration:none; cursor:pointer; }}
  button.ico {{ border:0;background:transparent;padding:0;font:inherit; }}
  button.ico:disabled {{ opacity:.5;cursor:wait; }}
  button.ico:focus-visible {{ outline:2px solid #c4b5fd;outline-offset:5px;border-radius:6px; }}
  .em {{ display:grid;place-items:center;filter:drop-shadow(0 1px 4px #0008); }}
  .em .icon {{ width:27px;height:27px; }}
  .info {{ position:absolute; left:14px; bottom:56px; right:84px; z-index:3;
           text-shadow:0 1px 5px #000; }}
  .info .user {{ font-weight:700; font-size:15px; color:#fff; text-decoration:none; }}
  .info .date {{ font-size:12px; color:#cfcfd6; margin-left:8px; }}
  .cap {{ font-size:13.5px; color:#eee; margin-top:7px; line-height:1.45;
          display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
          overflow:hidden; cursor:pointer; }}
  .cap.full {{ display:block; }}
  .song {{ display:flex;align-items:center;gap:8px;margin-top:10px;color:#c4b5fd;font-size:12px;
           height:18px;line-height:18px;min-width:0; }}
  .song[hidden] {{ display:flex;visibility:hidden; }}
  .song > span {{ min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis; }}
  .song .icon {{ width:16px;height:16px; }}
  .song a {{ color:inherit;text-decoration:none; }}
  .song a:hover {{ text-decoration:underline; }}
  .top {{ position:fixed; top:0; left:0; right:0; z-index:4; display:flex;
          align-items:center; gap:12px; padding:12px 16px;
          background:linear-gradient(#000d, transparent); font-size:13px; }}
  .top b {{ font-size:16px; }}
  .top {{ flex-wrap:wrap; }}
  #preference-status {{ font-size:12px; color:#ccc; }}
  .count {{ color:#9a9aa5; }}
  #mute {{ margin-left:auto; border:0; background:#000a; color:#fff;
            border-radius:50%; width:40px; height:40px; display:grid; place-items:center; cursor:pointer; }}
  #mute:hover {{ background:#ffffff20; }}
  #mute:focus-visible {{ outline:2px solid #c4b5fd;outline-offset:3px; }}
</style></head>
<body>
<div class="top"><b>Reels</b><span class="count" id="count"></span>
  <label title="More saved memes and new accounts discovered through meme posts">
    <input id="prefer-memes" type="checkbox" {checked} onchange="togglePreference(this)">
    Prefer memes
  </label><span id="preference-status" role="status" aria-live="polite"></span>
  
  <span title="Space: play/pause · M: mute · A: add to feed · P: profile · B: block permanently · Shift+B: block this session" aria-label="Keyboard shortcuts">{icon("keyboard")}</span>
  <span id="action-status" role="status" aria-live="polite"></span>
  <button id="mute" onclick="toggleMute()" title="Unmute (M)" aria-label="Unmute (M)" aria-pressed="true">{icon("muted")}</button></div>
<div id="feed"></div>
<script id="seed" type="application/json">{seed}</script>
<script>
{feed_picker_js()}
const incoming = JSON.parse(document.getElementById('seed').textContent);
const feed = document.getElementById('feed');
const countEl = document.getElementById('count');
const esc = s => (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;')
                         .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const fmt = n => (n??0).toLocaleString();
const fdate = t => t ? new Date(t*1000).toLocaleDateString(
    undefined, {{month:'short', day:'numeric'}}) : '';

const posts = [];
let slides = 0, muted = true, fetching = false, exhausted = false;
const base = "/reels";
let feeded = new Set({feeded});
const accountUpdates = new Map();
function syncAccountButtons() {{
  posts.forEach((p, i) => {{
    const button = document.getElementById('s'+i)?.querySelector('.account-action');
    if (!button) return;
    const saved = feeded.has(p.owner);
    button.innerHTML = '<span class="em">' + icon(saved ? 'check' : 'plus') + '</span><span>' + (saved ? 'in feed' : 'add') + '</span>';
    button.style.opacity = saved ? '.7' : '1';
    button.title = saved ? 'Remove this account from your feeds' : 'Add this account to a feed';
    button.onclick = saved ? () => rmAcc(p.owner, button) : () => addAcc(p.owner, button);
  }});
}}
accountChanged = (username, saved) => {{
  accountUpdates.set(username, saved);
  saved ? feeded.add(username) : feeded.delete(username);
  syncAccountButtons();
}};
let preferMemes = {preference};
let blockedAccounts = new Set({blocked});
let sessionBlocked = new Set();
try {{ sessionBlocked = new Set(JSON.parse(sessionStorage.getItem('ig-session-blocked') || '[]')); }} catch (_) {{}}
function isBlocked(p) {{ return p && (blockedAccounts.has(p.owner) || sessionBlocked.has(p.owner)); }}
function visibleIndices() {{ return posts.map((p,i) => isBlocked(p) ? -1 : i).filter(i => i >= 0); }}
function actionStatus(message) {{ document.getElementById('action-status').textContent = message; }}

async function togglePreference(input) {{
  const status = document.getElementById('preference-status');
  input.disabled = true;
  try {{
    const r = await fetch('/preferences', {{method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{prefer_memes:input.checked}})}});
    if (!r.ok) throw new Error('Could not save');
    preferMemes = input.checked;
    status.textContent = 'Saved · applies to new batches';
  }} catch (error) {{
    input.checked = preferMemes;
    status.textContent = 'Could not save · try again';
  }} finally {{ input.disabled = false; }}
}}

function slideHtml(p, i) {{
  return `<div class="slide" id="s${{i}}">
    <img class="poster" loading="lazy" src="${{esc(p.image)}}" alt="">
    <div class="spin" id="spin${{i}}" hidden>loading…</div>
    <div class="info">
      <a class="user" href="/u/${{esc(p.owner)}}" target="_blank"
        rel="noopener">@${{esc(p.owner)}}</a>
      <span class="date">${{fdate(p.taken_at)}}</span>
      <div class="cap" onclick="event.stopPropagation();
        this.classList.toggle('full')">${{esc(p.caption)}}</div>
      <div class="song" id="song${{i}}" hidden>${{icon('music')}}<span></span></div>
    </div>
    <div class="bar">
      <div class="ico"><span class="em">${{icon('heart')}}</span><span id="likes${{i}}">${{fmt(p.like_count)}}</span></div>
      <div class="ico"><span class="em">${{icon('comment')}}</span><span id="cm${{i}}">${{fmt(p.comment_count)}}</span></div>
      ${{feeded.has(p.owner)
          ? `<div class="ico account-action" style="cursor:pointer;opacity:.7"
             title="remove this account from your feeds"
             onclick="rmAcc('${{esc(p.owner)}}', this)">
             <span class="em">${{icon('check')}}</span><span>in feed</span></div>`
          : `<div class="ico account-action" style="cursor:pointer"
             title="add this account to your feed"
             onclick="addAcc('${{esc(p.owner)}}', this)">
             <span class="em">${{icon('plus')}}</span><span>add</span></div>`}}
      <button class="ico download" type="button" title="Download video" aria-label="Download video"
        onclick="downloadReel(${{i}}, this)">
        <span class="em">${{icon('download')}}</span><span>download</span></button>
      <a class="ico" href="${{p.url}}" target="_blank" rel="noopener">
        <span class="em">${{icon('external')}}</span><span>open</span></a>
    </div>
  </div>`;
}}
function append(chunk) {{
  chunk = chunk.filter(p => !isBlocked(p));
  posts.push(...chunk);
  feed.insertAdjacentHTML('beforeend', chunk.map((p,k)=>slideHtml(p, slides+k)).join(''));
  slides += chunk.length;
  countEl.textContent = fmt(visibleIndices().length) + ' reels';
  observeNew();
  if (current < 0 && visibleIndices().length) showSlide(visibleIndices()[0]);
}}
const PRELOAD_AHEAD = 3;
const BUFFER_AHEAD = 8;
let retryTimer = null;
function setVideo(p, i, play = false) {{
  const slide = document.getElementById('s'+i);
  let v = slide.querySelector('video');
  if (!v) {{
    slide.insertAdjacentHTML('beforeend',
      `<video muted loop playsinline preload="auto"
        onplaying="this.parentNode.querySelector('.poster').style.opacity=0; queueSong(${{i}})"
        onclick="tap(event)"></video>`);
    v = slide.querySelector('video');
  }}
  v.muted = muted;
  v.poster = p.poster || p.image || '';
  if (v.getAttribute('src') !== p.video) {{
    v.src = p.video;
    v.load();
  }}
  if (play && current === i && !p._paused) v.play().catch(()=>{{}});
}}
async function prepare(i) {{
  const p = posts[i];
  if (!p || isBlocked(p)) return;
  if (!p._rich) {{
    if (!p._loading) {{
      p._loading = fetch(base + '/media', {{method:'POST',
          headers:{{'Content-Type':'application/json'}},
          body:JSON.stringify({{code:p.shortcode}}), signal:AbortSignal.timeout(45000)}})
        .then(async r => {{
          const d = await r.json();
          if (!r.ok || d.error || !d.video) throw new Error(d.error || 'Video unavailable');
          Object.assign(p, d, {{_rich:true}});
        }}).finally(()=> {{ p._loading = null; }});
    }}
    await p._loading;
  }}
  // A slow response may arrive after the user has scrolled away.
  if (isBlocked(p) || i < current || !visibleIndices().filter(j => j >= current).slice(0, PRELOAD_AHEAD + 1).includes(i)) return;
  document.getElementById('likes'+i).textContent = fmt(p.like_count);
  document.getElementById('cm'+i).textContent = fmt(p.comment_count);
  setVideo(p, i, current === i);
}}
function preloadAhead() {{
  for (const i of visibleIndices().filter(j => j > current).slice(0, PRELOAD_AHEAD)) {{
    prepare(i).catch(()=>{{}}); // foreground activation retries failed preloads
  }}
  // Release videos outside the nearby window to bound downloaded media memory.
  feed.querySelectorAll('video').forEach(v => {{
    const i = +v.parentNode.id.slice(1);
    if (isBlocked(posts[i]) || i < current - 1 || i > current + PRELOAD_AHEAD) {{
      v.pause(); v.removeAttribute('src'); v.load(); v.remove();
      document.getElementById('s'+i).querySelector('.poster').style.opacity = 1;
    }}
  }});
}}
async function activate(i) {{
  if (isBlocked(posts[i])) return;
  const spin = document.getElementById('spin'+i);
  spin.textContent = 'loading…'; spin.hidden = false;
  maybeMore();
  preloadAhead();
  try {{
    await prepare(i);
    spin.hidden = true;
  }} catch (error) {{
    spin.textContent = error.message + ' — tap to retry';
    spin.onclick = () => {{ if (current === i) activate(i); }};
  }}
}}
let current = -1;
const io = new IntersectionObserver(es => {{
  for (const e of es) if (e.isIntersecting && e.intersectionRatio > 0.6) {{
    const i = +e.target.id.slice(1);
    if (!isBlocked(posts[i]) && i !== current) {{
      current = i;
      document.querySelectorAll('video').forEach(v => v.pause());
      activate(i);
    }}
  }}
}}, {{ threshold:[0.6] }});
function observeNew() {{
  feed.querySelectorAll('.slide').forEach(s => {{ if (!s.hidden && !s.dataset.obs) {{
    s.dataset.obs = 1; io.observe(s); }} }});
}}
function maybeMore() {{
  if (fetching || exhausted || current < 0 || retryTimer) return;
  if (visibleIndices().filter(i => i > current).length < BUFFER_AHEAD) more();
}}
function retryMore() {{
  if (!retryTimer) retryTimer = setTimeout(() => {{
    retryTimer = null; if (current < 0) more(); else maybeMore();
  }}, 3000);
}}
async function more() {{
  if (fetching || exhausted) return;
  fetching = true;
  let retry = false;
  try {{
    const r = await fetch(base + '/more', {{method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{session_blocked:[...sessionBlocked]}}), signal:AbortSignal.timeout(120000)}});
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || 'Could not load more reels');
    if (d.feed_accounts) {{
      feeded = new Set(d.feed_accounts);
      // A batch started before an add/remove may contain an older account list.
      accountUpdates.forEach((saved, username) => saved ? feeded.add(username) : feeded.delete(username));
      syncAccountButtons();
    }}
    if (d.blocked) {{ blockedAccounts = new Set(d.blocked); hideBlocked(); }}
    if (d.posts?.length) append(d.posts);
    exhausted = d.has_more === false;
    retry = (!d.posts?.length || !visibleIndices().length) && !exhausted;
    preloadAhead();
  }} catch (error) {{
    retry = true;
  }} finally {{
    fetching = false;
    if (retry) retryMore();
    else maybeMore();
  }}
}}
function tap(e) {{
  if (e.target.closest('a') || e.target.closest('.wb')) return;
  const v = e.target.closest('.slide')?.querySelector('video');
  if (v) togglePlayback();
}}
function toggleMute() {{
  muted = !muted;
  document.querySelectorAll('video').forEach(v => v.muted = muted);
  const button = document.getElementById('mute');
  button.innerHTML = icon(muted ? 'muted' : 'volume');
  button.title = muted ? 'Unmute (M)' : 'Mute (M)';
  button.setAttribute('aria-label', button.title);
  button.setAttribute('aria-pressed', String(muted));
}}
async function addAcc(u, el) {{
  await chooseFeed(u);
}}
async function rmAcc(u, el) {{
  el.querySelector('span:last-child').textContent = '…';
  try {{
    const response = await fetch('/remove', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{username:u}})}});
    if (!response.ok) throw new Error('Could not remove');
    notifyAccountChange(u, false);
  }} catch (error) {{ el.querySelector('span:last-child').textContent = 'retry'; }}
}}

let songTimer = null;
let songBusy = false;
function renderSong(i, result) {{
  const row = document.getElementById('song'+i);
  if (!row || isBlocked(posts[i])) return;
  row.hidden = false;
  const label = row.querySelector('span');
  label.textContent = '';
  if (result.status === 'matched') {{
    const text = result.title + (result.artist ? ' · ' + result.artist : '');
    row.title = text;
    if (result.url?.startsWith('https://')) {{
      const link = document.createElement('a');
      link.href = result.url; link.target = '_blank'; link.rel = 'noopener';
      link.textContent = text; label.append(link);
    }} else label.textContent = text;
  }} else label.textContent = result.status === 'no_match' ? 'Song not identified'
    : result.status === 'loading' ? 'Identifying song…' : 'Song recognition unavailable';
}}
function queueSong(i) {{
  const p = posts[i];
  if (!p || current !== i || isBlocked(p)) return;
  if (p._song) {{ renderSong(i, p._song); return; }}
  if (songBusy || (p._songRetryAt || 0) > Date.now()) return;
  clearTimeout(songTimer);
  songTimer = setTimeout(() => {{
    if (current === i && !p._paused && !isBlocked(p)) recognizeSong(i);
  }}, 3000);
}}
async function recognizeSong(i) {{
  const p = posts[i];
  if (songBusy || !p || p._song || isBlocked(p) || current !== i) return;
  songBusy = true;
  renderSong(i, {{status:'loading'}});
  try {{
    const r = await fetch('/recognize/' + encodeURIComponent(p.shortcode),
      {{method:'POST', signal:AbortSignal.timeout(110000)}});
    if (!r.ok) throw new Error();
    const result = await r.json();
    if (result.status === 'matched' || result.status === 'no_match') p._song = result;
    else p._songRetryAt = Date.now() + 60000;
    renderSong(i, result);
  }} catch (_) {{
    p._songRetryAt = Date.now() + 60000;
    renderSong(i, {{status:'unavailable'}});
  }} finally {{
    songBusy = false;
    if (current !== i) queueSong(current);
  }}
}}
async function downloadReel(i, button) {{
  const post = posts[i];
  if (!post || button.disabled) return;
  button.disabled = true;
  const label = button.querySelector('span:last-child');
  label.textContent = 'saving…';
  try {{
    const response = await fetch('/download/' + encodeURIComponent(post.shortcode));
    if (!response.ok) throw new Error(await response.text());
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = post.shortcode + '.mp4';
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    actionStatus('Download ready');
  }} catch (error) {{ actionStatus(error.message || 'Download failed · try again'); }}
  finally {{ button.disabled = false; label.textContent = 'download'; }}
}}
function togglePlayback() {{
  const p = posts[current];
  if (!p || isBlocked(p)) return;
  const video = document.getElementById('s'+current)?.querySelector('video');
  p._paused = video ? !video.paused : !p._paused;
  if (video) p._paused ? video.pause() : video.play().catch(()=>{{}});
}}
function showSlide(i) {{
  if (i === undefined || isBlocked(posts[i])) return;
  document.querySelectorAll('video').forEach(v => v.pause());
  current = i;
  document.getElementById('s'+i)?.scrollIntoView({{behavior:'instant'}});
  activate(i);
}}
function hideBlocked() {{
  posts.forEach((p,i) => {{
    if (!isBlocked(p)) return;
    const slide = document.getElementById('s'+i);
    if (!slide) return;
    io.unobserve(slide);
    const v = slide.querySelector('video');
    if (v) {{ v.pause(); v.removeAttribute('src'); v.load(); v.remove(); }}
    slide.hidden = true;
    slide.style.display = 'none';
  }});
  countEl.textContent = fmt(visibleIndices().length) + ' reels';
  if (isBlocked(posts[current])) {{
    const indices = visibleIndices();
    const next = indices.find(i => i > current) ?? indices.at(-1);
    current = -1;
    if (next !== undefined) showSlide(next);
  }}
}}
let blocking = false;
async function blockCurrent(sessionOnly = false) {{
  const owner = posts[current]?.owner;
  if (!owner || blocking) return;
  blocking = true;
  try {{
    if (sessionOnly) {{
      const updated = new Set([...sessionBlocked, owner]);
      sessionStorage.setItem('ig-session-blocked', JSON.stringify([...updated]));
      sessionBlocked = updated;
    }} else {{
      const r = await fetch('/block', {{method:'POST', headers:{{'Content-Type':'application/json'}},
        body:JSON.stringify({{username:owner}})}});
      if (!r.ok) throw new Error('Could not save block');
      const d = await r.json();
      blockedAccounts = new Set(d.blocked);
    }}
    hideBlocked();
    actionStatus('@' + owner + (sessionOnly ? ' blocked for this session' : ' blocked permanently'));
    exhausted = false;
    if (current < 0) more(); else maybeMore();
  }} catch (error) {{ actionStatus('Could not block @' + owner + ' · try again'); }}
  finally {{ blocking = false; }}
}}
function addCurrent() {{
  const p = posts[current];
  if (!p || isBlocked(p)) return;
  const button = document.getElementById('s'+current)?.querySelector('.account-action');
  if (button) addAcc(p.owner, button);
}}
function openProfile() {{
  const owner = posts[current]?.owner;
  if (owner) window.open('/u/' + encodeURIComponent(owner), '_blank', 'noopener');
}}
const heldKeys = new Set();
function releaseReelKey(e) {{ heldKeys.delete(e.code || e.key.toLowerCase()); }}
function handleReelKey(e) {{
  if (e.ctrlKey || e.altKey || e.metaKey ||
      document.getElementById('feed-picker') ||
      e.target?.closest('input, textarea, select, button, a, [contenteditable="true"], dialog')) return;
  const key = e.key.toLowerCase();
  const actions = {{' ':togglePlayback, m:toggleMute, a:addCurrent, p:openProfile,
    b:()=>blockCurrent(e.shiftKey), arrowdown:()=>goRel(1), j:()=>goRel(1),
    arrowup:()=>goRel(-1), k:()=>goRel(-1)}};
  if (!actions[key]) return;
  // Consume repeats too: otherwise held arrows/Space trigger native scrolling.
  e.preventDefault();
  const physicalKey = e.code || key;
  if (e.repeat || heldKeys.has(physicalKey)) return;
  heldKeys.add(physicalKey);
  actions[key]();
}}
document.addEventListener('keydown', handleReelKey);
document.addEventListener('keyup', releaseReelKey);
if (typeof window !== 'undefined') window.addEventListener('blur', () => heldKeys.clear());
function goRel(d) {{
  const indices = visibleIndices();
  const at = indices.indexOf(current);
  const i = indices[Math.max(0, Math.min(indices.length - 1, at + d))];
  // Commit the target before observers or incoming batches can change current.
  if (i !== undefined && i !== current) showSlide(i);
}}
append(incoming.splice(0));
if (current < 0 && sessionBlocked.size) more();
</script>
</body></html>"""
