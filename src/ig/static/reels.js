// Page data from the server: {feed_accounts, blocked, selected_feeds}. The page
// arrives without reels and fetches its first batch from /reels/more.
const boot = JSON.parse(document.getElementById('boot').textContent);
const feed = document.getElementById('feed');
const countEl = document.getElementById('count');
const esc = s => (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;')
                         .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const fmt = n => (n??0).toLocaleString();
const fdate = t => t ? new Date(t*1000).toLocaleDateString(
    undefined, {month:'short', day:'numeric'}) : '';

const posts = [];
let slides = 0, muted = true, fetching = false, exhausted = false;
const base = "/reels";
let feeded = new Set(boot.feed_accounts);
const accountUpdates = new Map();
function syncAccountButtons() {
  posts.forEach((p, i) => {
    const button = document.getElementById('s'+i)?.querySelector('.account-action');
    if (!button) return;
    const saved = feeded.has(p.owner);
    button.innerHTML = '<span class="em">' + icon(saved ? 'check' : 'plus') + '</span><span>' + (saved ? 'in feed' : 'add') + '</span>';
    button.style.opacity = saved ? '.7' : '1';
    button.title = saved ? 'Remove account from all feeds (R)' : 'Add this account to a feed';
    button.onclick = saved ? () => rmAcc(p.owner, button) : () => addAcc(p.owner, button);
  });
}
accountChanged = (username, saved) => {
  accountUpdates.set(username, saved);
  saved ? feeded.add(username) : feeded.delete(username);
  syncAccountButtons();
};
let blockedAccounts = new Set(boot.blocked);
let sessionBlocked = new Set();
try { sessionBlocked = new Set(JSON.parse(sessionStorage.getItem('ig-session-blocked') || '[]')); } catch (_) {}
// A dropped reel was queued for an earlier feed choice and removed from the page.
function isHidden(p) { return p && (p._dropped || p.feedback === 'dislike' || blockedAccounts.has(p.owner) || sessionBlocked.has(p.owner)); }
function visibleIndices() { return posts.map((p,i) => isHidden(p) ? -1 : i).filter(i => i >= 0); }
function actionStatus(message) { document.getElementById('action-status').textContent = message; }

// Feed chips. A tap changes the selection at once; saving waits a moment so
// several quick taps become one change. null means All.
const SELECTION_DELAY_MS = 350;
const STATUS_CLEAR_MS = 4000;
let selectedFeeds = boot.selected_feeds;            // last saved selection
let chosenFeeds = copyFeeds(selectedFeeds);         // what the chips show
let selectionTimer = null;
let savingFeeds = false;
let switchingFeeds = false;  // between a saved change and the first batch for it
let selectionVersion = 0;    // increases with every saved change
let statusTimer = null;
function copyFeeds(names) { return names === null ? null : [...names]; }
function sameFeeds(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
// All -> one feed; then taps toggle feeds; removing the last one returns to All.
function nextFeedSelection(chosen, name) {
  if (name === null || chosen === null) return name === null ? null : [name];
  if (chosen.includes(name)) {
    const rest = chosen.filter(value => value !== name);
    return rest.length ? rest : null;
  }
  return [...chosen, name].sort();
}
function preferenceStatus(message, tone = '') {
  const status = document.getElementById('preference-status');
  status.textContent = message;
  if (tone) status.dataset.tone = tone; else delete status.dataset.tone;
  clearTimeout(statusTimer);
  statusTimer = message ? setTimeout(() => preferenceStatus(''), STATUS_CLEAR_MS) : null;
}
function feedSelectionLabel() {
  if (chosenFeeds === null) return 'All feeds';
  if (!chosenFeeds.length) return 'Choose feeds';
  const first = chosenFeeds[0].replaceAll('_', ' ');
  return first.charAt(0).toUpperCase() + first.slice(1) + (chosenFeeds.length > 1 ? ' +' + (chosenFeeds.length - 1) : '');
}
document.addEventListener('click', event => {
  const menu = document.getElementById('feed-menu');
  if (menu.open && !menu.contains(event.target)) menu.open = false;
});
function closeFeedMenu() {
  const menu = document.getElementById('feed-menu');
  menu.open = false;
  menu.querySelector('summary').focus();
}
function openFeedMenu() {
  const menu = document.getElementById('feed-menu');
  menu.open = true;
  (menu.querySelector('.feed-choice[aria-pressed="true"]') || menu.querySelector('.feed-choice'))?.focus();
}
document.addEventListener('keydown', event => {
  if (event.ctrlKey || event.altKey || event.metaKey || event.isComposing ||
      document.getElementById('feed-picker') || document.getElementById('shortcut-help') ||
      event.target?.closest('input, textarea, select, [contenteditable="true"], dialog')) return;
  const menu = document.getElementById('feed-menu');
  if (event.key === ',') {
    event.preventDefault();
    if (!event.repeat) menu.open ? closeFeedMenu() : openFeedMenu();
    return;
  }
  if (!menu.open) {
    if (menu.contains(event.target) && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
      event.preventDefault();
      openFeedMenu();
    }
    return;
  }
  if (event.key === 'Escape') {
    event.preventDefault();
    closeFeedMenu();
    return;
  }
  const choices = [...menu.querySelectorAll('.feed-choice')];
  const at = choices.indexOf(document.activeElement);
  if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? choices.length - 1 :
      (at + (event.key === 'ArrowDown' ? 1 : -1) + choices.length) % choices.length;
    choices[next]?.focus();
  } else if (at >= 0 && [' ', 'Enter'].includes(event.key)) {
    event.preventDefault();
    if (event.repeat) return;
    const name = choices[at].dataset.all ? null : choices[at].dataset.feed;
    if (event.key === ' ') chooseFeedSelection(name);
    else {
      chosenFeeds = name === null ? null : [name];
      renderFeedSelection();
      clearTimeout(selectionTimer);
      saveFeedSelection();
      closeFeedMenu();
    }
  }
});
function renderFeedSelection() {
  document.getElementById('feed-selection-label').textContent = feedSelectionLabel();
  document.querySelectorAll('.feed-choice').forEach(button => {
    const selected = button.dataset.all ? chosenFeeds === null : Boolean(chosenFeeds?.includes(button.dataset.feed));
    button.setAttribute('aria-pressed', String(selected));
  });
  const busy = savingFeeds || switchingFeeds;
  document.getElementById('feed-choices').setAttribute('aria-busy', String(busy));
  document.getElementById('feed-busy').hidden = !busy;
}
function chooseFeedSelection(name) {
  chosenFeeds = nextFeedSelection(chosenFeeds, name);
  renderFeedSelection();
  clearTimeout(selectionTimer);
  selectionTimer = setTimeout(saveFeedSelection, SELECTION_DELAY_MS);
}
document.querySelectorAll('.feed-choice').forEach(button => {
  button.addEventListener('click', () => chooseFeedSelection(button.dataset.all ? null : button.dataset.feed));
});
renderFeedSelection();
async function saveFeedSelection() {
  selectionTimer = null;
  // One save at a time; the running save checks for newer taps when it ends.
  if (savingFeeds || sameFeeds(chosenFeeds, selectedFeeds)) return;
  const wanted = copyFeeds(chosenFeeds);
  savingFeeds = true;
  renderFeedSelection();
  try {
    const r = await fetch('/preferences', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({selected_feeds:wanted})});
    if (!r.ok) throw new Error(`Saving the feed choice failed (HTTP ${r.status}): ${await r.text().catch(() => '')}`);
    const saved = await r.json();
    selectedFeeds = saved.selected_feeds === undefined ? wanted : saved.selected_feeds;
    if (selectionTimer === null && sameFeeds(chosenFeeds, wanted)) chosenFeeds = copyFeeds(selectedFeeds);
    preferenceStatus('');
    feedsChanged();
  } catch (error) {
    console.error('Could not change feeds', error);
    clearTimeout(selectionTimer);
    selectionTimer = null;
    chosenFeeds = copyFeeds(selectedFeeds);
    preferenceStatus('Could not change feeds · try again', 'error');
  } finally {
    savingFeeds = false;
    renderFeedSelection();
    if (selectionTimer === null && !sameFeeds(chosenFeeds, selectedFeeds)) saveFeedSelection();
  }
}
// Keep the current reel and earlier ones, and replace what was queued after
// it with a batch for the new selection. Nothing reloads.
function feedsChanged() {
  selectionVersion += 1;
  dropQueuedReels();
  clearTimeout(retryTimer);
  retryTimer = null;
  fetching = false;  // a request for the old selection is ignored when it returns
  exhausted = false;
  loadFailures = 0;
  switchingFeeds = true;
  renderFeedSelection();
  more();
}
function releaseVideo(slide) {
  const v = slide.querySelector('video');
  if (v) { v.pause(); v.removeAttribute('src'); v.load(); v.remove(); }
}
function dropQueuedReels() {
  posts.forEach((p, i) => {
    if (i <= current || p._dropped) return;
    p._dropped = true;
    const slide = document.getElementById('s'+i);
    if (!slide) return;
    io.unobserve(slide);
    releaseVideo(slide);
    slide.remove();
  });
  countEl.textContent = fmt(visibleIndices().length) + ' reels';
}

// Loading notice: centered while no reel is on screen, a small pill while a
// new feed choice has nothing queued yet.
const FAILURES_BEFORE_ERROR = 2;
let loadFailures = 0;
let loadError = '';
function updateFeedStatus() {
  const box = document.getElementById('feed-status');
  const visible = visibleIndices();
  const queued = visible.some(i => i > current);
  let mode = null;
  if (!visible.length) mode = 'overlay';
  else if (!queued && (switchingFeeds || fetching || retryTimer || exhausted)) mode = 'compact';
  let state = 'loading', text = 'Loading reels…';
  if (loadFailures >= FAILURES_BEFORE_ERROR) {
    state = 'error';
    text = `Couldn’t load reels: ${loadError} · retrying`;
  } else if (exhausted && !queued) {
    state = 'empty';
    text = 'No reels available right now';
  }
  box.hidden = mode === null;
  box.dataset.mode = mode || 'overlay';
  box.dataset.state = state;
  document.getElementById('feed-status-text').textContent = text;
  document.getElementById('feed-retry').hidden = state === 'loading';
}
function retryLoading() {
  clearTimeout(retryTimer);
  retryTimer = null;
  exhausted = false;
  loadFailures = 0;
  updateFeedStatus();
  more();
}
// The batch request itself failed (no response), with a short readable reason.
const BATCH_TIMEOUT_MS = 120000;
class BatchRequestError extends Error {
  constructor(cause) {
    const timedOut = cause?.name === 'TimeoutError' || cause?.name === 'AbortError';
    super(timedOut ? 'the request timed out' : 'the ig server is not reachable', {cause});
    this.name = 'BatchRequestError';
  }
}

function slideHtml(p, i) {
  return `<div class="slide" id="s${i}">
    <img class="poster" loading="lazy" src="${esc(p.image)}" alt="">
    <div class="spin" id="spin${i}" hidden>loading…</div>
    <div class="info">
      <a class="user" href="/u/${esc(p.owner)}" target="_blank"
        rel="noopener">@${esc(p.owner)}</a>
      <span class="date">${fdate(p.taken_at)}</span>
      ${p.caption ? `<button type="button" id="caption${i}" class="cap" aria-expanded="false" title="Expand caption" onclick="toggleCaption(${i})">${esc(p.caption)}</button>` : ''}
      <div class="song" id="song${i}" hidden>${icon('music')}<span></span></div>
      <div class="why" id="why${i}" hidden></div>
    </div>
    <div class="bar">
      <button class="ico reel-like" type="button" title="More like this · local like (L)"
        aria-label="Like this reel" aria-pressed="${p.feedback === 'like'}" onclick="likeReel(${i})">
        <span class="em">${icon('heart')}</span><span id="likes${i}">${fmt(p.like_count)}</span></button>
      <button class="ico" type="button" title="Not interested in this reel (N)" aria-label="Not interested"
        onclick="rateReel(${i}, 'dislike')"><span class="em">${icon('less')}</span><span>less</span></button>
      <button class="ico" type="button" title="Comments (C)" aria-label="Show comments" onclick="toggleComments(${i})"><span class="em">${icon('comment')}</span><span id="cm${i}">${fmt(p.comment_count)}</span></button>
      ${p.reason ? `<button class="ico why-button" type="button" title="Why am I seeing this? (W)"
        aria-label="Why am I seeing this?" aria-expanded="false" aria-controls="why${i}" onclick="toggleWhy(${i})">
        <span class="em">${icon('info')}</span><span>why</span></button>` : ''}
      ${feeded.has(p.owner)
          ? `<div class="ico account-action" style="cursor:pointer;opacity:.7"
             title="Remove account from all feeds (R)"
             onclick="rmAcc('${esc(p.owner)}', this)">
             <span class="em">${icon('check')}</span><span>in feed</span></div>`
          : `<div class="ico account-action" style="cursor:pointer"
             title="add this account to your feed"
             onclick="addAcc('${esc(p.owner)}', this)">
             <span class="em">${icon('plus')}</span><span>add</span></div>`}
      <button class="ico reel-save" type="button" title="Save to Saved reels (S)"
        aria-label="Save this reel" aria-pressed="${Boolean(p.saved)}" onclick="toggleSavedReel(${i})">
        <span class="em">${icon('bookmark')}</span><span>${p.saved ? 'saved' : 'save'}</span></button>
      <button class="ico download" type="button" title="Download video" aria-label="Download video"
        onclick="downloadReel(${i}, this)">
        <span class="em">${icon('download')}</span><span>download</span></button>
      <a class="ico" href="${p.url}" target="_blank" rel="noopener">
        <span class="em">${icon('external')}</span><span>open</span></a>
    </div>
  </div>`;
}
function append(chunk) {
  chunk = chunk.filter(p => !isHidden(p));
  posts.push(...chunk);
  feed.insertAdjacentHTML('beforeend', chunk.map((p,k)=>slideHtml(p, slides+k)).join(''));
  slides += chunk.length;
  countEl.textContent = fmt(visibleIndices().length) + ' reels';
  observeNew();
  if (current < 0 && visibleIndices().length) showSlide(visibleIndices()[0]);
  updateFeedStatus();
}
const PRELOAD_AHEAD = 5;
// Keep metadata farther ahead than media: cheap queued reels absorb slow
// profile requests without downloading dozens of videos at once.
const BUFFER_AHEAD = 24;
const RETRY_DELAY_MS = 3000;  // wait before asking again after an empty or failed batch
let retryTimer = null;
function setVideo(p, i, play = false) {
  const slide = document.getElementById('s'+i);
  let v = slide.querySelector('video');
  if (!v) {
    slide.insertAdjacentHTML('beforeend',
      `<video muted loop playsinline preload="auto"
        onplaying="this.parentNode.querySelector('.poster').style.opacity=0; onReelPlaying(${i})"
        onclick="tap(event)"></video>`);
    v = slide.querySelector('video');
  }
  v.muted = muted;
  v.poster = p.poster || p.image || '';
  if (v.getAttribute('src') !== p.video) {
    v.src = p.video;
    v.load();
  }
  if (play && current === i && !p._paused) v.play().catch(()=>{});
}
async function prepare(i) {
  const p = posts[i];
  if (!p || isHidden(p)) return;
  if (!p._rich) {
    if (!p._loading) {
      p._loading = fetch(base + '/media', {method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({code:p.shortcode}), signal:AbortSignal.timeout(45000)})
        .then(async r => {
          const d = await r.json();
          if (!r.ok || d.error || !d.video) throw new Error(d.error || 'Video unavailable');
          Object.assign(p, d, {_rich:true});
        }).finally(()=> { p._loading = null; });
    }
    await p._loading;
  }
  // A slow response may arrive after the user has scrolled away.
  if (isHidden(p) || i < current || !visibleIndices().filter(j => j >= current).slice(0, PRELOAD_AHEAD + 1).includes(i)) return;
  document.getElementById('likes'+i).textContent = fmt(p.like_count);
  document.getElementById('cm'+i).textContent = fmt(p.comment_count);
  setVideo(p, i, current === i);
}
function preloadAhead() {
  for (const i of visibleIndices().filter(j => j > current).slice(0, PRELOAD_AHEAD)) {
    prepare(i).catch(()=>{}); // foreground activation retries failed preloads
  }
  // Release videos outside the nearby window to bound downloaded media memory.
  feed.querySelectorAll('video').forEach(v => {
    const i = +v.parentNode.id.slice(1);
    if (isHidden(posts[i]) || i < current - 1 || i > current + PRELOAD_AHEAD) {
      v.pause(); v.removeAttribute('src'); v.load(); v.remove();
      document.getElementById('s'+i).querySelector('.poster').style.opacity = 1;
    }
  });
}
function toggleCaption(i) {
  const caption = document.getElementById('caption'+i);
  if (!caption) return;
  const expanded = caption.classList.toggle('full');
  caption.setAttribute('aria-expanded', String(expanded));
  caption.title = expanded ? 'Collapse caption' : 'Expand caption';
}
function collapseCaptions() {
  document.querySelectorAll('.cap').forEach(caption => {
    caption.classList.remove('full');
    caption.setAttribute('aria-expanded', 'false');
    caption.title = 'Expand caption';
  });
}
async function activate(i) {
  collapseCaptions();
  const panel = document.getElementById('comments-panel');
  if (panel.dataset.code !== posts[i]?.shortcode) closeComments();
  if (isHidden(posts[i])) return;
  const spin = document.getElementById('spin'+i);
  spin.textContent = 'loading…'; spin.hidden = false;
  maybeMore();
  preloadAhead();
  try {
    await prepare(i);
    spin.hidden = true;
  } catch (error) {
    spin.textContent = error.message + ' — tap to retry';
    spin.onclick = () => { if (current === i) activate(i); };
  }
}
let current = -1;
const io = new IntersectionObserver(es => {
  noteScroll(); // may put back a reel the burst went past; the check below then skips it
  for (const e of es) if (e.isIntersecting && e.intersectionRatio > 0.6) {
    // Observer entries describe a past frame. Ignore entries for slides that
    // have already left the viewport after a keyboard jump or layout change.
    const bounds = e.target.getBoundingClientRect();
    const viewport = feed.getBoundingClientRect();
    const visible = Math.min(bounds.bottom, viewport.bottom) - Math.max(bounds.top, viewport.top);
    if (visible <= bounds.height * 0.6) continue;
    const i = +e.target.id.slice(1);
    if (!isHidden(posts[i]) && i !== current) {
      recordQuickSkip(current);
      current = i;
      document.querySelectorAll('video').forEach(v => v.pause());
      activate(i);
    }
  }
}, { root:feed, threshold:[0.6] });
function observeNew() {
  feed.querySelectorAll('.slide').forEach(s => { if (!s.hidden && !s.dataset.obs) {
    s.dataset.obs = 1; io.observe(s); } });
}
function maybeMore() {
  if (fetching || exhausted || current < 0 || retryTimer) return;
  if (visibleIndices().filter(i => i > current).length < BUFFER_AHEAD) more();
}
function retryMore(delay = RETRY_DELAY_MS) {
  if (!retryTimer) retryTimer = setTimeout(() => {
    retryTimer = null; if (current < 0) more(); else maybeMore();
  }, delay);
}
async function more() {
  if (fetching || exhausted) return;
  fetching = true;
  const version = selectionVersion;
  let retry = false;
  let retryDelay = RETRY_DELAY_MS;
  updateFeedStatus();
  try {
    const r = await fetch(base + '/more', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({session_blocked:[...sessionBlocked]}), signal:AbortSignal.timeout(BATCH_TIMEOUT_MS)})
      .catch(error => { throw new BatchRequestError(error); });
    const d = await r.json().catch(() => ({}));  // error pages may not be JSON
    // A batch asked for under an earlier feed choice no longer belongs here.
    if (version !== selectionVersion) return;
    if (!r.ok || d.error) throw new Error(d.error || `server error (HTTP ${r.status})`);
    loadFailures = 0;
    if (Number.isFinite(d.retry_after_ms)) retryDelay = Math.max(500, Math.min(30000, d.retry_after_ms));
    if (d.feed_accounts) {
      feeded = new Set(d.feed_accounts);
      // A batch started before an add/remove may contain an older account list.
      accountUpdates.forEach((saved, username) => saved ? feeded.add(username) : feeded.delete(username));
      syncAccountButtons();
    }
    if (d.blocked) { blockedAccounts = new Set(d.blocked); hideFilteredReels(); }
    if (d.posts?.length) append(d.posts);
    exhausted = d.has_more === false;
    retry = (!d.posts?.length || !visibleIndices().length) && !exhausted;
    if (exhausted || visibleIndices().some(i => i > current)) switchingFeeds = false;
    preloadAhead();
  } catch (error) {
    if (version !== selectionVersion) return;
    loadFailures += 1;
    loadError = error.message || 'unknown error';
    console.warn(`Could not load reels (attempt ${loadFailures})`, error.cause || error);
    retry = true;
  } finally {
    // An older request must not touch the state of the newer one.
    if (version === selectionVersion) {
      fetching = false;
      if (retry) retryMore(retryDelay);
      else maybeMore();
      renderFeedSelection();
      updateFeedStatus();
    }
  }
}
function tap(e) {
  if (e.target.closest('a') || e.target.closest('.wb')) return;
  const v = e.target.closest('.slide')?.querySelector('video');
  if (v) togglePlayback();
}
function toggleMute() {
  muted = !muted;
  document.querySelectorAll('video').forEach(v => v.muted = muted);
  const button = document.getElementById('mute');
  button.innerHTML = icon(muted ? 'muted' : 'volume');
  button.title = muted ? 'Unmute (M)' : 'Mute (M)';
  button.setAttribute('aria-label', button.title);
  button.setAttribute('aria-pressed', String(muted));
}
async function addAcc(u, el) {
  await chooseFeed(u);
}
async function rmAcc(u, el) {
  el.querySelector('span:last-child').textContent = '…';
  try {
    const response = await fetch('/remove', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({username:u})});
    if (!response.ok) throw new Error('Could not remove');
    notifyAccountChange(u, false);
  } catch (error) { el.querySelector('span:last-child').textContent = 'retry'; }
}

function onReelPlaying(i) {
  if (current !== i || !posts[i]) return;
  if (!posts[i]._viewStarted) posts[i]._viewStarted = performance.now();
  queueSong(i);
}
function recordQuickSkip(i) {
  const p = posts[i];
  if (!p || p.feedback || p._skipSent || !p._viewStarted || p._paused || document.hidden) return;
  const seconds = (performance.now() - p._viewStarted) / 1000;
  if (seconds < 0.3 || seconds > 2.5) return;
  p._skipSent = true;
  fetch('/feedback', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({code:p.shortcode, action:'skip'}), keepalive:true}).catch(()=>{});
}
function likeReel(i) {
  if (posts[i]) rateReel(i, posts[i].feedback === 'like' ? 'clear' : 'like');
}
async function rateReel(i, action) {
  const p = posts[i];
  if (!p || p._feedbackBusy) return;
  p._feedbackBusy = true;
  try {
    const r = await fetch('/feedback', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({code:p.shortcode, action})});
    if (!r.ok) throw new Error();
    const result = await r.json();
    posts.filter(post => post.shortcode === p.shortcode).forEach(post => post.feedback = result.feedback);
    posts.forEach((post,index) => {
      if (post.shortcode !== p.shortcode) return;
      document.getElementById('s'+index)?.querySelector('.reel-like')?.setAttribute('aria-pressed', String(result.feedback === 'like'));
    });
    actionStatus(result.feedback === 'like' ? 'Liked · we’ll look for similar reels' :
      result.feedback === 'dislike' ? 'We’ll show less like this' : 'Like removed');
    if (result.feedback === 'dislike') {
      hideFilteredReels();
      exhausted = false;
      if (current < 0) more(); else maybeMore();
    }
  } catch (_) { actionStatus('Could not save feedback · try again'); }
  finally { p._feedbackBusy = false; }
}
let songTimer = null;
let songBusy = false;
function renderSong(i, result) {
  const row = document.getElementById('song'+i);
  if (!row || isHidden(posts[i])) return;
  row.hidden = false;
  const label = row.querySelector('span');
  label.textContent = '';
  if (result.status === 'matched') {
    const text = result.title + (result.artist ? ' · ' + result.artist : '');
    row.title = text;
    if (result.url?.startsWith('https://')) {
      const link = document.createElement('a');
      link.href = result.url; link.target = '_blank'; link.rel = 'noopener';
      link.textContent = text; label.append(link);
    } else label.textContent = text;
  } else label.textContent = result.status === 'no_match' ? 'Song not identified'
    : result.status === 'loading' ? 'Identifying song…' : 'Song recognition unavailable';
}
function queueSong(i) {
  const p = posts[i];
  if (!p || current !== i || isHidden(p)) return;
  if (p._song) { renderSong(i, p._song); return; }
  if (songBusy || (p._songRetryAt || 0) > Date.now()) return;
  clearTimeout(songTimer);
  songTimer = setTimeout(() => {
    if (current === i && !p._paused && !isHidden(p)) recognizeSong(i);
  }, 3000);
}
async function recognizeSong(i) {
  const p = posts[i];
  if (songBusy || !p || p._song || isHidden(p) || current !== i) return;
  songBusy = true;
  renderSong(i, {status:'loading'});
  try {
    const r = await fetch('/recognize/' + encodeURIComponent(p.shortcode),
      {method:'POST', signal:AbortSignal.timeout(110000)});
    if (!r.ok) throw new Error();
    const result = await r.json();
    if (result.status === 'matched' || result.status === 'no_match') p._song = result;
    else p._songRetryAt = Date.now() + 60000;
    renderSong(i, result);
  } catch (_) {
    p._songRetryAt = Date.now() + 60000;
    renderSong(i, {status:'unavailable'});
  } finally {
    songBusy = false;
    if (current !== i) queueSong(current);
  }
}
async function downloadReel(i, button) {
  const post = posts[i];
  if (!post || button.disabled) return;
  button.disabled = true;
  const label = button.querySelector('span:last-child');
  label.textContent = 'saving…';
  try {
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
  } catch (error) { actionStatus(error.message || 'Download failed · try again'); }
  finally { button.disabled = false; label.textContent = 'download'; }
}
function downloadCurrent() {
  const p = posts[current];
  if (!p || isHidden(p)) return;
  const button = document.getElementById('s'+current)?.querySelector('.download');
  // A disabled button means this reel's download is already running.
  if (button && !button.disabled) downloadReel(current, button);
}
function showSavedState(code, saved) {
  posts.forEach((post, index) => {
    if (post.shortcode !== code) return;
    post.saved = saved;
    const button = document.getElementById('s'+index)?.querySelector('.reel-save');
    if (!button) return;
    button.setAttribute('aria-pressed', String(saved));
    const label = button.querySelector('span:last-child');
    if (label) label.textContent = saved ? 'saved' : 'save';
  });
}
async function toggleSavedReel(i) {
  const p = posts[i];
  if (!p || isHidden(p) || p._saveBusy) return;
  p._saveBusy = true;
  const saved = !p.saved;
  let message = 'Could not update Saved reels · try again';
  try {
    const r = await fetch('/saved-reels', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({code:p.shortcode, saved})});
    if (r.status === 404) message = 'Reload the page, then try saving this reel again';
    if (!r.ok) throw new Error(message);
    const result = await r.json();
    showSavedState(p.shortcode, result.saved === true);
    actionStatus(result.saved ? 'Added to Saved reels' : 'Removed from Saved reels');
  } catch (_) { actionStatus(message); }
  finally { p._saveBusy = false; }
}
function togglePlayback() {
  const p = posts[current];
  if (!p || isHidden(p)) return;
  const video = document.getElementById('s'+current)?.querySelector('video');
  p._paused = video ? !video.paused : !p._paused;
  if (video) p._paused ? video.pause() : video.play().catch(()=>{});
}
function showSlide(i) {
  if (i === undefined || isHidden(posts[i])) return;
  document.querySelectorAll('video').forEach(v => v.pause());
  if (current !== i) recordQuickSkip(current);
  current = i;
  scrollBurst = null; // a deliberate move ends any scroll burst; see noteScroll
  document.getElementById('s'+i)?.scrollIntoView({behavior:'instant'});
  activate(i);
}
function hideFilteredReels() {
  posts.forEach((p,i) => {
    if (!isHidden(p)) return;
    const slide = document.getElementById('s'+i);
    if (!slide) return;
    io.unobserve(slide);
    const v = slide.querySelector('video');
    if (v) { v.pause(); v.removeAttribute('src'); v.load(); v.remove(); }
    slide.hidden = true;
    slide.style.display = 'none';
  });
  countEl.textContent = fmt(visibleIndices().length) + ' reels';
  updateFeedStatus();
  if (isHidden(posts[current])) {
    const indices = visibleIndices();
    const next = indices.find(i => i > current) ?? indices.at(-1);
    current = -1;
    if (next !== undefined) showSlide(next);
  }
}
let blocking = false;
async function blockCurrent(sessionOnly = false) {
  const owner = posts[current]?.owner;
  if (!owner || blocking) return;
  blocking = true;
  try {
    if (sessionOnly) {
      const updated = new Set([...sessionBlocked, owner]);
      sessionStorage.setItem('ig-session-blocked', JSON.stringify([...updated]));
      sessionBlocked = updated;
    } else {
      const r = await fetch('/block', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({username:owner})});
      if (!r.ok) throw new Error('Could not save block');
      const d = await r.json();
      blockedAccounts = new Set(d.blocked);
    }
    hideFilteredReels();
    actionStatus('@' + owner + (sessionOnly ? ' blocked for this session' : ' blocked permanently'));
    exhausted = false;
    if (current < 0) more(); else maybeMore();
  } catch (error) { actionStatus('Could not block @' + owner + ' · try again'); }
  finally { blocking = false; }
}
function addCurrent() {
  const p = posts[current];
  if (!p || isHidden(p)) return;
  const button = document.getElementById('s'+current)?.querySelector('.account-action');
  if (button) addAcc(p.owner, button);
}
function removeCurrent() {
  const p = posts[current];
  if (!p || isHidden(p) || !feeded.has(p.owner)) return;
  const button = document.getElementById('s'+current)?.querySelector('.account-action');
  if (button) rmAcc(p.owner, button);
}
function toggleWhy(i = current) {
  const p = posts[i];
  const panel = document.getElementById('why'+i);
  if (!p?.reason || !panel) return;
  const open = panel.hidden;
  if (open && !panel.childElementCount) {
    const title = document.createElement('b');
    title.textContent = p.reason.summary;
    const list = document.createElement('ul');
    for (const detail of p.reason.details || []) {
      const item = document.createElement('li');
      item.textContent = detail;
      list.append(item);
    }
    panel.append(title, list);
  }
  panel.hidden = !open;
  document.getElementById('s'+i)?.querySelector('.why-button')?.setAttribute('aria-expanded', String(open));
}
function openProfile() {
  const owner = posts[current]?.owner;
  if (owner) window.open('/u/' + encodeURIComponent(owner), '_blank', 'noopener');
}
const heldKeys = new Set();
function releaseReelKey(e) { heldKeys.delete(e.code || e.key.toLowerCase()); }
function handleReelKey(e) {
  if (e.defaultPrevented || e.ctrlKey || e.altKey || e.metaKey ||
      document.getElementById('feed-picker') || document.getElementById('shortcut-help') ||
      e.target?.closest('input, textarea, select, [contenteditable="true"], dialog')) return;
  const key = e.key.toLowerCase();
  const menu = document.getElementById('feed-menu');
  // Multi-select leaves this menu open. J/K resumes watching immediately;
  // other keys retain their normal behavior inside the selector.
  if (menu.open) {
    if (key !== 'j' && key !== 'k') return;
    menu.open = false;
  }
  // Space retains native activation for a focused button/link; navigation keys
  // must always use our one-step handler, including after clicking controls.
  if (key === ' ' && e.target?.closest('button, a, summary')) return;
  const actions = {'?':showShortcuts, ' ':togglePlayback, m:toggleMute, c:()=>toggleComments(current), l:()=>likeReel(current), n:()=>rateReel(current, 'dislike'), a:addCurrent, r:removeCurrent, p:openProfile, w:()=>toggleWhy(current),
    s:()=>toggleSavedReel(current), d:downloadCurrent,
    b:()=>blockCurrent(e.shiftKey), arrowdown:()=>goRel(1), j:()=>goRel(1),
    arrowup:()=>goRel(-1), k:()=>goRel(-1)};
  if (!actions[key]) return;
  // Consume repeats too: otherwise held arrows/Space trigger native scrolling.
  e.preventDefault();
  const physicalKey = e.code || key;
  if (e.repeat || heldKeys.has(physicalKey)) return;
  heldKeys.add(physicalKey);
  actions[key]();
}
document.addEventListener('keydown', handleReelKey);
document.addEventListener('keyup', releaseReelKey);
if (typeof window !== 'undefined') window.addEventListener('blur', () => heldKeys.clear());
function goRel(d) {
  const indices = visibleIndices();
  const at = indices.indexOf(current);
  const i = indices[Math.max(0, Math.min(indices.length - 1, at + d))];
  // Commit the target before observers or incoming batches can change current.
  if (i !== undefined && i !== current) showSlide(i);
  else if (d > 0 && !exhausted) {
    maybeMore();
    updateFeedStatus();
  }
}
// One wheel notch or trackpad swipe moves one reel. Left to the browser, each
// small trackpad step snaps to the next reel, and a long swipe can pass several.
// Steps less than SCROLL_BURST_GAP_MS apart count as one swipe.
const SCROLL_BURST_GAP_MS = 150;
let lastWheel = -Infinity;
function scrollsItself(el) {
  for (; el && el !== feed; el = el.parentElement)
    if (el.scrollHeight > el.clientHeight && /auto|scroll/.test(getComputedStyle(el).overflowY)) return true;
  return false;
}
feed.addEventListener('wheel', e => {
  if (e.ctrlKey || Math.abs(e.deltaY) <= Math.abs(e.deltaX) || scrollsItself(e.target)) return;
  e.preventDefault();
  const now = performance.now();
  if (now - lastWheel > SCROLL_BURST_GAP_MS) goRel(Math.sign(e.deltaY));
  lastWheel = now;
}, {passive:false});
// Other scrolling we did not start (touch, or a keyboard-scrolling extension
// such as Vimium, which handles j/k before this page sees them) can also arrive
// as a burst of small steps, and snapping turns each step into a whole reel.
// Let each burst move at most one reel from where it started.
let scrollBurst = null; // {to, step, last}
let burstFrame = 0;
let lastScrollTop = 0;
function slideOffset(i) {
  return document.getElementById('s'+i).getBoundingClientRect().top - feed.getBoundingClientRect().top;
}
function limitScrollBurst() {
  if (scrollBurst && performance.now() - scrollBurst.last > SCROLL_BURST_GAP_MS) scrollBurst = null;
  if (!scrollBurst) return false;
  // Went past the neighbouring reel: put it back in view.
  if (slideOffset(scrollBurst.to) * scrollBurst.step < -1)
    document.getElementById('s'+scrollBurst.to).scrollIntoView({behavior:'instant'});
  return true;
}
// Check every frame while a burst lasts, so a step an animation makes in this
// frame is undone before it is painted.
function watchScrollBurst() {
  burstFrame = limitScrollBurst() ? requestAnimationFrame(watchScrollBurst) : 0;
}
// Called on every scroll event and before the observer moves `current`, since
// either can see a step first.
function noteScroll() {
  const now = performance.now(), top = feed.scrollTop, moved = top - lastScrollTop;
  lastScrollTop = top;
  if (scrollBurst && now - scrollBurst.last > SCROLL_BURST_GAP_MS) scrollBurst = null;
  if (!scrollBurst) {
    if (current < 0 || isHidden(posts[current]) || !document.getElementById('s'+current)) return;
    // Our own scrollIntoView, or a layout change that kept the reel in place.
    if (Math.abs(slideOffset(current)) < 1 || !moved) return;
    const step = Math.sign(moved);
    const indices = visibleIndices();
    scrollBurst = {to: indices[indices.indexOf(current) + step] ?? current, step};
  }
  scrollBurst.last = now;
  limitScrollBurst();
  if (!burstFrame) burstFrame = requestAnimationFrame(watchScrollBurst);
}
feed.addEventListener('scroll', noteScroll, {passive:true});
// Each new swipe may move one more reel, even while the last one is settling.
feed.addEventListener('touchstart', () => { scrollBurst = null; }, {passive:true});
updateFeedStatus();
more();

function closeComments() {
  const panel = document.getElementById('comments-panel');
  panel.hidden = true;
  delete panel.dataset.code;
}
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') closeComments();
});
function commentRow(comment) {
  return `<article class="comment-row">
    <a href="/u/${encodeURIComponent(comment.username)}" target="_blank" rel="noopener">@${esc(comment.username)}</a>
    ${comment.is_owner ? '<span class="comment-creator">Creator</span>' : ''}
    <p>${esc(comment.text)}</p><small>${fmt(comment.like_count)} likes</small></article>`;
}
function commentKey(comment) { return comment.username + '\u0000' + comment.text; }
function commentsPage(p, after) {
  return fetch('/comments/' + encodeURIComponent(p.shortcode) + (after ? '?after=' + encodeURIComponent(after) : ''),
    {method:'POST', signal:AbortSignal.timeout(45000)}).then(async response => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Could not load comments');
      return data;
    });
}
async function toggleComments(i) {
  const p = posts[i];
  if (!p || isHidden(p)) return;
  const panel = document.getElementById('comments-panel');
  if (!panel.hidden && panel.dataset.code === p.shortcode) { closeComments(); return; }
  panel.hidden = false;
  panel.dataset.code = p.shortcode;
  const body = document.getElementById('comments-body');
  body.textContent = 'Loading comments…';
  try {
    if (!p._comments) {
      if (!p._commentsLoading) p._commentsLoading = commentsPage(p, null).then(data => {
        p._comments = data.comments || [];
        p._commentKeys = new Set(p._comments.map(commentKey));
        p._commentCursor = data.next_cursor || null;
      }).finally(() => { p._commentsLoading = null; });
      await p._commentsLoading;
    }
    if (panel.hidden || panel.dataset.code !== p.shortcode) return;
    body.innerHTML = p._comments.length ? p._comments.map(commentRow).join('') : 'Nothing to show yet.';
    // A short first page on a tall panel should already offer more.
    if (p._commentCursor && body.scrollHeight <= body.clientHeight) loadMoreComments(p, body);
  } catch (error) {
    if (panel.hidden || panel.dataset.code !== p.shortcode) return;
    body.textContent = error.message + ' · close and reopen to retry';
  }
}
async function loadMoreComments(p, body) {
  if (p._commentsLoading || !p._commentCursor) return;
  document.getElementById('comments-more')?.remove();
  const sentinel = document.createElement('div');
  sentinel.id = 'comments-more';
  sentinel.textContent = 'Loading more…';
  body.appendChild(sentinel);
  let failed = false;
  p._commentsLoading = commentsPage(p, p._commentCursor).then(data => {
    for (const comment of data.comments || []) {
      if (p._commentKeys.has(commentKey(comment))) continue;  // cursors may repeat a row
      p._commentKeys.add(commentKey(comment));
      p._comments.push(comment);
      sentinel.insertAdjacentHTML('beforebegin', commentRow(comment));
    }
    p._commentCursor = data.next_cursor || null;
  }).catch(() => {
    failed = true;
  }).finally(() => {
    sentinel.remove();
    p._commentsLoading = null;
    if (failed) body.insertAdjacentHTML('beforeend',
      '<div id="comments-more" role="status">Couldn’t load more · scroll to retry</div>');
  });
  await p._commentsLoading;
}
document.getElementById('comments-body').addEventListener('scroll', event => {
  const el = event.target;
  const panel = document.getElementById('comments-panel');
  const p = posts.find(x => x && x.shortcode === panel.dataset.code);
  if (panel.hidden || !p || p._commentsLoading || !p._commentCursor) return;
  if (el.scrollHeight - el.scrollTop - el.clientHeight > 140) return;
  loadMoreComments(p, el);
});
