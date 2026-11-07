// Page data from the server: {feed_accounts, blocked, selected_feeds}. The page
// arrives without reels and fetches its first batch from /reels/more.
const boot = JSON.parse(document.getElementById('boot').textContent);
const playbackBoot = initialPlaybackPreferences(boot);
let desiredSound = playbackBoot.sound === true;
let localPage = null;
let localPageVersion = 0;
let scrollAnimation = boot.scroll_animation === 'snappy' ? 'snappy' : 'smooth';
let scrollTarget = null;
window.addEventListener('storage', event => {
  if (event.key === 'ig-scroll-animation' && ['smooth', 'snappy'].includes(event.newValue))
    scrollAnimation = event.newValue;
  if (event.key === 'ig-playback-change' && event.newValue) {
    try { applyPlaybackPreferences(JSON.parse(event.newValue).changes); } catch (_) {}
  }
});
window.addEventListener('ig-playback-restored', event => {
  playbackVersion += 1;
  applyPlaybackPreferences(event.detail);
  if (['smooth','snappy'].includes(event.detail.scroll_animation)) scrollAnimation = event.detail.scroll_animation;
});
if (boot.android) document.body.classList.add('android-app');
const feed = document.getElementById('feed');
const countEl = document.getElementById('count');
const esc = s => (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;')
                         .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const fmt = n => (n??0).toLocaleString();
// How old a post is: "5 minutes ago", "3 days ago", "2 years ago".
const AGE_UNITS = [['year', 31536000], ['month', 2592000], ['week', 604800], ['day', 86400], ['hour', 3600], ['minute', 60]];
function postAge(t, now = Date.now()) {
  if (!t) return '';
  const seconds = Math.max(0, Math.round(now / 1000 - t));
  for (const [unit, size] of AGE_UNITS) {
    const count = Math.floor(seconds / size);
    if (count >= 1) return `${count} ${unit}${count === 1 ? '' : 's'} ago`;
  }
  return 'just now';
}
// "3 days ago · Oct 8" (with the year when it isn't this year).
function postDate(t) {
  if (!t) return '';
  const date = new Date(t * 1000);
  const day = date.toLocaleDateString(undefined, date.getFullYear() === new Date().getFullYear()
    ? {month:'short', day:'numeric'} : {month:'short', day:'numeric', year:'numeric'});
  return `<time class="date" datetime="${date.toISOString()}" title="Posted ${esc(date.toLocaleString())}">${postAge(t)} · ${day}</time>`;
}

const posts = [];
let autoScroll = playbackBoot.auto_scroll === true;
let creatorTrail = null;
const creatorTrails = new Map();
let shownCodes = new Set();
try { shownCodes = new Set(JSON.parse(sessionStorage.getItem('ig-shown-reels') || '[]')); } catch (_) {}
function rememberShown(code) {
  if (!code) return;
  shownCodes.add(code);
  if (shownCodes.size > 1000) shownCodes.delete(shownCodes.values().next().value);
  try { sessionStorage.setItem('ig-shown-reels', JSON.stringify([...shownCodes])); } catch (_) {}
}
function excludedReelCodes() {
  return [...new Set([...shownCodes, ...posts.filter(p => !p._dropped).map(p => p.shortcode), ...[...creatorTrails.values()].flatMap(trail => trail.items.map(p => p.shortcode))])].slice(-1000);
}
const queueClient = globalThis.crypto?.randomUUID?.() || 'tab-' + Date.now() + '-' + Math.random().toString(36).slice(2);
let reservationTimer = null;
const pendingReleases = new Set();
let slides = 0, muted = !desiredSound, fetching = false, exhausted = false;
const base = "/reels";
let feeded = new Set(boot.feed_accounts);
const accountUpdates = new Map();
const blockUpdates = new Map();
let accountChangeVersion = 0;
function syncAccountButtons() {
  posts.forEach((p, i) => {
    const button = document.getElementById('s'+i)?.querySelector('.account-action');
    if (!button) return;
    const saved = feeded.has(p.owner);
    button.innerHTML = '<span class="em">' + icon(saved ? 'check' : 'plus') + '</span><span>' + (saved ? 'in feed' : 'add') + '</span>';
    button.style.opacity = saved ? '.7' : '1';
    button.title = saved ? 'Remove account from feeds (R)' : 'Add this account to a feed';
    button.onclick = saved ? () => confirmRemoveCreator(p.owner, button) : () => addAcc(p.owner, button);
  });
}
accountsChanged = (usernames, saved) => {
  if (!usernames.length) return;
  const version = ++accountChangeVersion;
  for (const username of usernames) {
    accountUpdates.set(username, {value:saved, version});
    saved ? feeded.add(username) : feeded.delete(username);
    if (saved) {
      // Adding explicitly unblocks on the server. Unfollowing never blocks.
      blockUpdates.set(username, {value:false, version});
      blockedAccounts.delete(username);
    } else {
      const removed = posts.filter(p => p.owner === username);
      removed.forEach(p => { p._dropped = true; });
      releaseReservations(removed.map(p => p.reservation).filter(Boolean));
    }
  }
  syncAccountButtons();
  hideFilteredReels();
  exhausted = false;
  if (current < 0) more(); else maybeMore();
};
accountChanged = (username, saved) => accountsChanged([username], saved);
let blockedAccounts = new Set(boot.blocked);
let sessionBlocked = new Set();
try { sessionBlocked = new Set(JSON.parse(sessionStorage.getItem('ig-session-blocked') || '[]')); } catch (_) {}
// A dropped reel was queued for an earlier feed choice and removed from the page.
function isHidden(p) { return p && (p._dropped || (p.feedback === 'dislike' && p.shortcode !== boot.post_code) || blockedAccounts.has(p.owner) || sessionBlocked.has(p.owner)); }
function visibleIndices() { return posts.map((p,i) => isHidden(p) ? -1 : i).filter(i => i >= 0); }
// Short notices hide by themselves. `stay` keeps one up until it is replaced
// (loading, or waiting for a tap); `hideAfterMs` gives an Undo more time.
const NOTICE_HIDE_MS = 4000;
let actionStatusTimer = null;
function actionStatus(message, {stay = false, hideAfterMs = NOTICE_HIDE_MS} = {}) {
  const status = document.getElementById('action-status');
  status.textContent = message;
  clearTimeout(actionStatusTimer);
  actionStatusTimer = null;
  if (!message || stay) return;
  const hide = () => {
    // Keep it while the user is reaching for a button in it (e.g. Undo).
    if (status.matches(':hover, :focus-within')) actionStatusTimer = setTimeout(hide, 1000);
    else { actionStatusTimer = null; status.textContent = ''; }
  };
  actionStatusTimer = setTimeout(hide, hideAfterMs);
}

// Feed chips. A tap changes the selection at once; saving waits a moment so
// several quick taps become one change. null means All.
const SELECTION_DELAY_MS = 350;
const STATUS_CLEAR_MS = 4000;
let selectedFeeds = boot.selected_feeds;            // last saved selection
let chosenFeeds = copyFeeds(selectedFeeds);         // what the chips show
let selectionTimer = null;
let savingFeeds = false;
let feedSavePromise = Promise.resolve(), feedSaveError = null;
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
  } else if (at >= 0 && event.key === 'Delete' && choices[at].dataset.feed) {
    event.preventDefault();
    if (!event.repeat) deleteFeed(choices[at].dataset.feed);
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
document.querySelectorAll('.feed-delete').forEach(button => {
  button.onclick = () => deleteFeed(button.dataset.feed);
});
function syncFeedList(names) {
  boot.feeds = names;
  const container = document.getElementById('feed-choices');
  for (const button of container.querySelectorAll('.feed-choice[data-feed]')) {
    if (!boot.feeds.includes(button.dataset.feed)) (button.closest('.feed-row') || button).remove();
  }
  for (const name of boot.feeds) {
    if ([...container.querySelectorAll('.feed-choice[data-feed]')].some(b => b.dataset.feed === name)) continue;
    const row = document.createElement('div'); row.className = 'feed-row';
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'feed-choice'; button.dataset.feed = name;
    button.innerHTML = icon('check') + '<span>' + esc(name.replaceAll('_', ' ')) + '</span>';
    button.onclick = () => chooseFeedSelection(name);
    const remove = document.createElement('button');
    remove.type = 'button'; remove.className = 'feed-delete';
    remove.setAttribute('aria-label', 'Delete ' + name + ' feed');
    remove.title = 'Delete feed'; remove.innerHTML = icon('trash');
    remove.onclick = () => deleteFeed(name);
    row.append(button, remove); container.append(row);
  }
}
async function refreshFeedList(detail) {
  const response = await fetch('/preferences');
  if (!response.ok) throw new Error('Could not refresh feeds');
  const data = await response.json();
  syncFeedList(data.feeds);
  if (detail.deleted || detail.restored || (detail.imported && detail.settings_values && 'selected_feeds' in detail.settings_values)) {
    clearTimeout(selectionTimer); selectionTimer = null;
    selectedFeeds = data.selected_feeds;
    chosenFeeds = copyFeeds(selectedFeeds);
    document.querySelector('#feed-menu summary').focus();
    feedsChanged();
  } else {
    actionStatus(detail.imported ? 'Feeds imported' : detail.created ? 'Created ' + detail.name.replaceAll('_', ' ') : 'Accounts added');
    selectionVersion += 1;
    emptySince = null;
    lastNavigation = null;
    fetching = false;
    retryLoading();
  }
  renderFeedSelection();
}
window.addEventListener('ig-feeds-changed', event => {
  if (event.detail.imported) {
    for (const code of event.detail.saved_codes || []) showSavedState(code, true);
    const version = ++accountChangeVersion;
    const importedAccounts = new Set(event.detail.feed_accounts);
    const importedBlocks = new Set(event.detail.blocked_accounts);
    for (const username of new Set([...feeded, ...importedAccounts]))
      accountUpdates.set(username, {value:importedAccounts.has(username), version});
    for (const username of new Set([...blockedAccounts, ...importedBlocks]))
      blockUpdates.set(username, {value:importedBlocks.has(username), version});
    feeded = importedAccounts;
    blockedAccounts = importedBlocks;
    syncAccountButtons();
    hideFilteredReels();
  }
  refreshFeedList(event.detail).catch(() => actionStatus('Saved · refresh to update the feed list'));
});
let deletingFeed = false;
function cancelFeedDeletion() {
  document.getElementById('feed-delete-confirm')?.remove();
}
document.getElementById('feed-menu').addEventListener('toggle', event => {
  if (!event.target.open) cancelFeedDeletion();
});
function deleteFeed(name) {
  if (deletingFeed || savingFeeds) return;
  cancelFeedDeletion();
  const trigger = document.activeElement;
  const confirmation = document.createElement('div');
  confirmation.id = 'feed-delete-confirm';
  confirmation.setAttribute('role', 'group');
  confirmation.setAttribute('aria-label', 'Confirm feed deletion');
  const label = document.createElement('p');
  label.textContent = 'Delete “' + name.replaceAll('_', ' ') + '”?';
  const cancel = document.createElement('button');
  cancel.type = 'button'; cancel.textContent = 'Cancel';
  cancel.onclick = () => { cancelFeedDeletion(); trigger?.focus(); };
  const confirm = document.createElement('button');
  confirm.type = 'button'; confirm.textContent = 'Delete feed';
  confirm.className = 'confirm-delete';
  confirm.onclick = () => { cancelFeedDeletion(); performFeedDeletion(name); };
  confirmation.append(label, cancel, confirm);
  const menu = document.getElementById('feed-menu');
  menu.open = true;
  menu.querySelector('.feed-panel-heading').after(confirmation);
  cancel.focus();
}
async function performFeedDeletion(name) {
  if (deletingFeed || savingFeeds) return;
  deletingFeed = true;
  try {
    const response = await fetch('/feeds/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not delete feed');
    try { await refreshFeedList(data); } catch (_) { /* Undo must remain available if refreshing fails. */ }
    actionStatus('Deleted ' + name.replaceAll('_', ' '), {hideAfterMs:10000});
    const undo = document.createElement('button');
    undo.textContent = 'Undo'; undo.type = 'button';
    undo.onclick = async () => {
      undo.disabled = true;
      try {
        const response = await fetch('/feeds/restore', {method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({deletion_id:data.deletion_id})});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Could not restore feed');
        await refreshFeedList(result);
        actionStatus('Restored ' + name.replaceAll('_', ' '));
      } catch (error) { undo.disabled = false; undo.textContent = 'Retry undo'; }
    };
    document.getElementById('action-status').append(undo);
  } catch (error) { actionStatus(error.message); }
  finally { deletingFeed = false; }
}
function saveFeedSelection() {
  clearTimeout(selectionTimer); selectionTimer = null;
  // One save at a time; the running save checks for newer taps when it ends.
  if (savingFeeds) return feedSavePromise;
  if (sameFeeds(chosenFeeds, selectedFeeds)) return Promise.resolve();
  const wanted = copyFeeds(chosenFeeds);
  feedSaveError = null;
  feedSavePromise = (async () => {
  savingFeeds = true;
  renderFeedSelection();
  try {
    const r = await fetch('/preferences', {method:'POST', keepalive:true,
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({selected_feeds:wanted})});
    if (!r.ok) throw new Error(`Saving the feed choice failed (HTTP ${r.status}): ${await r.text().catch(() => '')}`);
    const saved = await r.json();
    selectedFeeds = saved.selected_feeds === undefined ? wanted : saved.selected_feeds;
    if (selectionTimer === null && sameFeeds(chosenFeeds, wanted)) chosenFeeds = copyFeeds(selectedFeeds);
    preferenceStatus('');
    feedsChanged();
  } catch (error) {
    feedSaveError = error;
    console.error('Could not change feeds', error);
    clearTimeout(selectionTimer);
    selectionTimer = null;
    chosenFeeds = copyFeeds(selectedFeeds);
    preferenceStatus('Could not change feeds · try again', 'error');
  } finally {
    savingFeeds = false;
    renderFeedSelection();
    if (selectionTimer === null && !sameFeeds(chosenFeeds, selectedFeeds)) await saveFeedSelection();
  }
  })();
  return feedSavePromise;
}
beforeLocalNavigation = async () => {
  const pendingSelection = savingFeeds || selectionTimer !== null || !sameFeeds(chosenFeeds, selectedFeeds);
  await saveFeedSelection();
  if (pendingSelection && feedSaveError) throw new Error('Could not save your feed selection');
  await playbackWrite;
};
// Keep the current reel and earlier ones, and replace what was queued after
// it with a batch for the new selection. Nothing reloads.
function feedsChanged() {
  if (boot.post_code) { window.location.href = '/'; return; }
  selectionVersion += 1;
  emptySince = null;
  lastNavigation = null;
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
  leaveCreatorBrowse();
  const released = [];
  posts.forEach((p, i) => {
    if (i <= current || p._dropped) return;
    p._dropped = true;
    creatorTrails.delete(i);
    if (p.reservation && !(p._watchedSeconds > 0)) released.push(p.reservation);
    const slide = document.getElementById('s'+i);
    if (!slide) return;
    io.unobserve(slide);
    releaseVideo(slide);
    slide.remove();
  });
  releaseReservations(released);
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
    text = boot.post_code ? `Couldn’t open reel: ${loadError}` : `Couldn’t load reels: ${loadError} · retrying`;
  } else if (exhausted && !queued) {
    state = 'empty';
    text = boot.post_code ? 'This creator is blocked' : 'No reels available right now';
  }
  box.hidden = mode === null || Boolean(boot.post_code && visible.length > 0);
  box.dataset.mode = mode || 'overlay';
  box.dataset.state = state;
  document.getElementById('feed-status-text').textContent = text;
  document.getElementById('feed-retry').hidden = state === 'loading';
  const empty = !boot.post_code && Array.isArray(boot.feeds) && boot.feeds.length === 0;
  const needsAccounts = !boot.post_code && Array.isArray(boot.feeds) && boot.feeds.length > 0 && feeded.size === 0;
  document.getElementById('feed-create').hidden = !empty;
  document.getElementById('feed-add').hidden = !needsAccounts || state !== 'empty';
  if (empty) {
    box.hidden = false; box.dataset.state = 'empty';
    document.getElementById('feed-status-text').textContent = 'Create your first feed to start watching';
    document.getElementById('feed-retry').hidden = true;
  }
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
    <span class="pause-indicator" id="pause${i}" role="status" aria-label="Paused" hidden>${icon('play')}</span>
    <div class="reel-seek">
      <input id="seek${i}" type="range" min="0" max="1000" step="1" value="0" disabled aria-label="Seek within reel"
        onpointerdown="stopReelHold();this.dataset.scrubbing='1'" onpointerup="delete this.dataset.scrubbing"
        onpointercancel="delete this.dataset.scrubbing" onchange="delete this.dataset.scrubbing"
        oninput="seekReel(${i}, this)">
      <output id="seek-time${i}" for="seek${i}"></output>
    </div>
    <div class="info">
      <a class="user" href="/u/${esc(p.owner)}" title="View profile (P)">@${esc(p.owner)}</a>
      ${postDate(p.taken_at)}
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
      <button class="ico reel-share" type="button" title="Share reel" aria-label="Share reel" onclick="shareReel(${i})">
        <span class="em">${icon('share')}</span><span>share</span></button>
      ${p.reason ? `<button class="ico why-button" type="button" title="Why am I seeing this? (W)"
        aria-label="Why am I seeing this?" aria-expanded="false" aria-controls="why${i}" onclick="toggleWhy(${i})">
        <span class="em">${icon('info')}</span><span>why</span></button>` : ''}
      ${feeded.has(p.owner)
          ? `<div class="ico account-action" style="cursor:pointer;opacity:.7"
             title="Remove account from feeds (R)"
             onclick="confirmRemoveCreator('${esc(p.owner)}', this)">
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
      <button class="ico android-reel-more" type="button" aria-label="More reel actions" onclick="showReelActions(${i})"><span class="em">…</span></button>
      <a class="ico" href="${p.url}" target="_blank" rel="noopener">
        <span class="em">${icon('external')}</span><span>open</span></a>
    </div>
  </div>`;
}
function append(chunk) {
  const existing = new Set(posts.filter(p => !p._dropped).map(p => p.shortcode));
  const heldTokens = new Set(posts.filter(p => !p._dropped).map(p => p.reservation));
  const released = [];
  chunk = chunk.filter(p => {
    if (isHidden(p) || existing.has(p.shortcode) || (shownCodes.has(p.shortcode) && p.shortcode !== boot.post_code)) {
      if (p.reservation && !heldTokens.has(p.reservation)) released.push(p.reservation);
      return false;
    }
    existing.add(p.shortcode);
    heldTokens.add(p.reservation);
    return true;
  });
  releaseReservations(released);
  posts.push(...chunk);
  scheduleReservationRenewal();
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
const BUFFER_AHEAD = 24; // conservative starting target before pace is known
let secondsPerReel = 3, paceSamples = 0, refillSeconds = 12;
let lastNavigation = null, emptySince = null;
function noteReelNavigation(i) {
  const now = performance.now();
  const previous = lastNavigation;
  lastNavigation = {index:i, at:now, version:selectionVersion};
  if (document.hidden || !previous || previous.version !== selectionVersion || i <= previous.index) return;
  const elapsed = (now - previous.at) / 1000;
  // Ignore idle gaps; pause and hidden-tab transitions also reset the sample.
  if (elapsed < .15 || elapsed > 60) return;
  const seconds = Math.max(.25, elapsed / (i - previous.index));
  secondsPerReel = paceSamples ? .65 * secondsPerReel + .35 * seconds : seconds;
  paceSamples += 1;
}
function refillTarget() {
  if (paceSamples < 3) return BUFFER_AHEAD;
  // Two loading cycles plus six seconds of margin, bounded metadata only.
  return Math.max(12, Math.min(48, Math.ceil((2 * refillSeconds + 6) / secondsPerReel)));
}
function noteRefillResponse(data, started) {
  if (Number.isFinite(data.refill_seconds))
    refillSeconds = Math.max(1, Math.min(60, data.refill_seconds));
  if (!data.posts?.length) {
    if (data.has_more !== false && emptySince === null) emptySince = started;
    return;
  }
  const elapsed = (performance.now() - (emptySince ?? started)) / 1000;
  refillSeconds = Math.max(refillSeconds, Math.min(60, elapsed));
  emptySince = null;
}
const RETRY_DELAY_MS = 3000;  // wait before asking again after an empty or failed batch
let retryTimer = null;
function setVideo(p, i, play = false) {
  const slide = document.getElementById('s'+i);
  let v = slide.querySelector('video');
  if (!v) {
    slide.insertAdjacentHTML('beforeend',
      `<video muted loop playsinline preload="auto"
        onplaying="this.parentNode.querySelector('.poster').style.opacity=0; onReelPlaying(${i}); updatePauseIcon(${i}, false)"
        ontimeupdate="onReelProgress(${i}, this)"
        onloadedmetadata="updateSeekBar(${i}, this)" ondurationchange="updateSeekBar(${i}, this)"
        onwaiting="interruptPlayback(${i})" onerror="interruptPlayback(${i})"
        onseeking="onReelSeeking(${i}, this)" onpause="if(posts[${i}]) posts[${i}]._lastSample=null; updatePauseIcon(${i}, true)"
        onclick="tap(event)"></video>`);
    v = slide.querySelector('video');
  }
  v.loop = !autoScroll;
  v.muted = muted;
  v.poster = p.poster || p.image || '';
  if (v.getAttribute('src') !== p.video) {
    v.src = p.video;
    v.load();
  }
  if (play && current === i && !p._paused) playVideo(v);
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
  if (posts[i] !== p || isHidden(p) || i < current || !visibleIndices().filter(j => j >= current).slice(0, PRELOAD_AHEAD + 1).includes(i)) return;
  document.getElementById('likes'+i).textContent = fmt(p.like_count);
  document.getElementById('cm'+i).textContent = fmt(p.comment_count);
  if (commentsVisible(p)) renderComments(p);
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
  if (isHidden(posts[i])) return;
  if (lastNavigation?.index !== i) noteReelNavigation(i);
  rememberShown(posts[i]?.shortcode);
  if (panel.hidden === false && panel.dataset.code !== posts[i]?.shortcode) toggleComments(i);
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
  if (scrollTarget) return; // Intermediate animation frames must not select another reel.
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
      leaveCreatorBrowse();
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
  if (visibleIndices().filter(i => i > current).length < refillTarget()) more();
}
function retryMore(delay = RETRY_DELAY_MS) {
  if (!retryTimer) retryTimer = setTimeout(() => {
    retryTimer = null; if (current < 0) more(); else maybeMore();
  }, delay);
}
async function loadDirectReel() {
  fetching = true;
  updateFeedStatus();
  try {
    const response = await fetch('/reels/media', {method:'POST',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({code:boot.post_code}),
      signal:AbortSignal.timeout(45000)});
    const data = await response.json();
    if (!response.ok || data.error || !data.video) throw new Error(data.error || 'No playable video in this post');
    exhausted = true;
    loadFailures = 0;
    append([{...data, shortcode:boot.post_code, _rich:true,
      image:data.poster, url:'https://www.instagram.com/reel/' + boot.post_code + '/'}]);
  } catch (error) {
    loadFailures = FAILURES_BEFORE_ERROR;
    loadError = error.message || 'Could not load this reel';
  } finally {
    fetching = false;
    updateFeedStatus();
  }
}
async function more() {
  if (fetching || exhausted) return;
  if (boot.post_code) return loadDirectReel();
  fetching = true;
  const version = selectionVersion;
  const accountVersion = accountChangeVersion;
  const started = performance.now();
  let retry = false;
  let retryDelay = RETRY_DELAY_MS;
  updateFeedStatus();
  try {
    const r = await fetch(base + '/more', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({session_blocked:[...sessionBlocked], client:queueClient, release:[...pendingReleases], exclude_codes:excludedReelCodes()}), signal:AbortSignal.timeout(BATCH_TIMEOUT_MS)})
      .catch(error => { throw new BatchRequestError(error); });
    const d = await r.json().catch(() => ({}));  // error pages may not be JSON
    // A batch asked for under an earlier feed choice no longer belongs here.
    if (version !== selectionVersion) {
      releaseReservations((d.posts || []).map(p => p.reservation).filter(Boolean));
      return;
    }
    if (!r.ok || d.error) throw new Error(d.error || `server error (HTTP ${r.status})`);
    loadFailures = 0;
    noteRefillResponse(d, started);
    if (Number.isFinite(d.retry_after_ms)) retryDelay = Math.max(500, Math.min(30000, d.retry_after_ms));
    if (d.feed_accounts) {
      feeded = new Set(d.feed_accounts);
      // A batch started before an add/remove may contain an older account list.
      accountUpdates.forEach(({value:saved, version}, username) => {
        if (version > accountVersion) saved ? feeded.add(username) : feeded.delete(username);
      });
      syncAccountButtons();
    }
    if (d.blocked) {
      blockedAccounts = new Set(d.blocked);
      blockUpdates.forEach(({value:blocked, version}, username) => {
        if (version > accountVersion) blocked ? blockedAccounts.add(username) : blockedAccounts.delete(username);
      });
      hideFilteredReels();
    }
    if (d.posts?.length) append(d.posts.map(post => {
      const change = accountUpdates.get(post.owner);
      return change && !change.value && change.version > accountVersion
        ? {...post, _dropped:true} : post;
    }));
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
let pendingTap = null;
function cancelPendingTap() {
  if (pendingTap) clearTimeout(pendingTap.timer);
  pendingTap = null;
}
function showLikeHeart(i) {
  const slide = document.getElementById('s'+i);
  if (!slide) return;
  slide.querySelector('.tap-heart')?.remove();
  const heart = document.createElement('span'); heart.className = 'tap-heart';
  heart.setAttribute('aria-hidden','true'); heart.innerHTML = icon('heart');
  slide.append(heart); setTimeout(() => heart.remove(),700);
}
function tap(e) {
  if (e.target.closest('a') || e.target.closest('.wb')) return;
  const v = e.target.closest('.slide')?.querySelector('video');
  if (!v) return;
  if (e.pointerType !== 'touch' && !e.sourceCapabilities?.firesTouchEvents) { togglePlayback(); return; }
  const now = performance.now(), index = current;
  if (pendingTap && pendingTap.index === index && now - pendingTap.at < 300 &&
      Math.hypot(e.clientX-pendingTap.x,e.clientY-pendingTap.y) < 48) {
    cancelPendingTap();
    if (posts[index]?.feedback !== 'like') likeReel(index);
    showLikeHeart(index); return;
  }
  cancelPendingTap();
  pendingTap = {index,at:now,x:e.clientX,y:e.clientY,timer:setTimeout(() => {
    pendingTap = null;
    if (current === index && v.isConnected && !localPage) togglePlayback();
  },300)};
}
// soundBlocked: the saved choice is sound on, but the browser refused to play
// with sound before the user interacted with this page.
let soundBlocked = false;
function setMuted(value) {
  muted = value;
  document.querySelectorAll('video').forEach(v => v.muted = muted);
  const button = document.getElementById('mute');
  button.innerHTML = icon(muted ? 'muted' : 'volume');
  button.title = boot.android ? 'Mute or unmute' : muted ? 'Unmute (M)' : 'Mute (M)';
  button.setAttribute('aria-label', button.title);
  button.setAttribute('aria-pressed', String(muted));
}
function applyPlaybackPreferences(data) {
  if (typeof data?.sound === 'boolean') {
    desiredSound = data.sound; soundBlocked = false; setMuted(!desiredSound);
  }
  if (typeof data?.auto_scroll === 'boolean') setAutoScroll(data.auto_scroll);
}
window.addEventListener('ig-playback-migrated', event => applyPlaybackPreferences(event.detail));
function toggleMute() {
  soundBlocked = false;
  desiredSound = muted;
  setMuted(!desiredSound);
  savePlaybackPreferences({sound:desiredSound}).catch(() => actionStatus('Sound changed for now · could not remember it'));
  const video = document.querySelector('#s'+current+' video');
  if (video && !posts[current]?._paused) playVideo(video);
}
function playVideo(video) {
  if (localPage) {
    if (video === document.querySelector('#s'+current+' video') && !posts[current]?._paused) {
      localPage.video = video; localPage.resume = true;
    }
    return;
  }
  video.play().catch(error => {
    if (localPage) return;
    if (error?.name !== 'NotAllowedError' || video.muted || !desiredSound) return;
    // Browsers may block sound until the first tap or key press. Play muted now
    // and turn sound back on at the first interaction.
    soundBlocked = true;
    setMuted(true);
    actionStatus('Tap anywhere to turn sound on', {stay:true});
    video.play().catch(() => {});
  });
}
function restoreBlockedSound(event) {
  if (!soundBlocked || event.target.closest?.('#mute') || (event.type === 'keydown' && event.key?.toLowerCase() === 'm')) return;
  soundBlocked = false;
  if (desiredSound) {
    setMuted(false); actionStatus('');
    const video = document.querySelector('#s'+current+' video');
    if (video && !posts[current]?._paused) playVideo(video);
  }
}
document.addEventListener('pointerdown', restoreBlockedSound, true);
document.addEventListener('keydown', restoreBlockedSound, true);
// Tapping "in feed" removes a creator from feeds, so ask first. The R shortcut
// stays immediate because a key press is deliberate.
function confirmRemoveCreator(u, el) {
  if (removingAccounts.has(u)) return;
  const dialog = androidSheet('remove-creator-confirm', 'Remove @' + u + '?');
  dialog.classList.add('confirm-sheet');
  const message = document.createElement('p');
  message.textContent = 'Remove this account from all your feeds? You can add it again later.';
  const actions = document.createElement('div'); actions.className = 'confirm-actions';
  const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = 'Cancel';
  const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'confirm-remove';
  remove.textContent = 'Remove from feeds';
  cancel.onclick = () => dialog.close();
  remove.onclick = () => { dialog.close(); rmAcc(u, el); };
  actions.append(cancel, remove); dialog.append(message, actions);
  dialog.showModal(); cancel.focus();
}
async function addAcc(u, el) {
  await chooseFeed(u);
}
const removingAccounts = new Set();
async function rmAcc(u, el) {
  if (removingAccounts.has(u)) return;
  removingAccounts.add(u);
  const label = el?.querySelector('span:last-child');
  if (label) label.textContent = '…';
  try {
    const response = await fetch('/remove', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({username:u})});
    if (!response.ok) throw new Error('Could not remove');
    notifyAccountChange(u, false);
    actionStatus('@' + u + ' removed from feeds');
  } catch (error) {
    if (label) label.textContent = 'retry';
    actionStatus('Could not remove @' + u + ' · try again');
  } finally { removingAccounts.delete(u); }
}

function releaseReservations(tokens) {
  if (tokens && !tokens.length) return;
  for (const token of tokens || []) pendingReleases.add(token);
  return fetch('/reels/release', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({client:queueClient, ...(tokens ? {tokens} : {})}), keepalive:true})
    .then(response => { if (response.ok) for (const token of tokens || []) pendingReleases.delete(token); }).catch(()=>{});
}
function scheduleReservationRenewal() {
  if (reservationTimer || !posts.some((p, i) => i >= current && p.reservation && !p._viewRecorded && !isHidden(p))) return;
  reservationTimer = setTimeout(() => {
    reservationTimer = null;
    const tokens = posts.filter((p, i) => i >= current && p.reservation && !p._viewRecorded && !isHidden(p)).map(p => p.reservation);
    if (!document.hidden && tokens.length) {
      fetch('/reels/renew', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({client:queueClient, tokens})}).catch(()=>{});
    }
    scheduleReservationRenewal();
  }, 30000);
}
function interruptPlayback(i) {
  const p = posts[i];
  if (!p) return;
  p._lastSample = null;
  p._interrupted = true;
}
function onReelSeeking(i, video) {
  const p = posts[i];
  // Native looping seeks to zero before firing playing again.
  if (p && video.loop && video.currentTime < 0.1 && p._watchedSeconds > 0) p._looped = true;
  interruptPlayback(i);
}
function onReelPlaying(i) {
  if (current !== i || !posts[i]) return;
  const video = document.getElementById('s'+i)?.querySelector('video');
  if (video && posts[i]._lastSample && video.currentTime < posts[i]._lastSample.time) posts[i]._looped = true;
  posts[i]._lastSample = !document.hidden && video ? {time:video.currentTime, wall:performance.now()} : null;
  queueSong(i);
}
function updateSeekBar(i, video) {
  const input = document.getElementById('seek'+i);
  if (!input) return;
  const duration = video.duration;
  input.disabled = !Number.isFinite(duration) || duration <= 0;
  if (input.disabled || input.dataset.scrubbing) return;
  input.value = Math.round(video.currentTime / duration * 1000);
  const time = seconds => Math.floor(seconds / 60) + ':' + String(Math.floor(seconds % 60)).padStart(2,'0');
  const label = time(video.currentTime) + ' / ' + time(duration);
  input.setAttribute('aria-valuetext', label);
  const output = document.getElementById('seek-time'+i);
  if (output) output.textContent = label;
  input.style.setProperty('--progress', (video.currentTime / duration * 100) + '%');
}
function seekReel(i, input) {
  const video = document.querySelector('#s'+i+' video');
  if (!video || !Number.isFinite(video.duration) || video.duration <= 0) return;
  stopReelHold();
  video.currentTime = video.duration * Math.max(0, Math.min(1000, Number(input.value))) / 1000;
  if (posts[i]) posts[i]._lastSample = null;
  const scrubbing = input.dataset.scrubbing;
  delete input.dataset.scrubbing; updateSeekBar(i, video);
  if (scrubbing) input.dataset.scrubbing = scrubbing;
}
function onReelProgress(i, video) {
  updateSeekBar(i, video);
  const p = posts[i];
  if (!p) return;
  const sample = {time:video.currentTime, wall:performance.now()};
  const previous = p._lastSample;
  p._lastSample = sample;
  if (current !== i || document.hidden || video.paused || video.seeking || video.readyState < 2 || video.error) {
    p._lastSample = null;
    return;
  }
  if (!previous) return;
  const delta = sample.time - previous.time;
  if (delta < 0) { p._looped = true; return; }
  const wall = (sample.wall - previous.wall) / 1000;
  if (delta <= 0 || wall <= 0 || wall > 3 || delta > wall * (video.playbackRate || 1) + 0.2) return;
  const ranges = [...(p._watchRanges || []), [previous.time, sample.time]].sort((a,b) => a[0]-b[0]);
  const merged = [];
  for (const range of ranges) {
    const last = merged.at(-1);
    if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
    else merged.push([...range]);
  }
  p._watchRanges = merged;
  p._watchedSeconds = Math.min(3600, merged.reduce((sum, [a,b]) => sum + b-a, 0));
  reportWatch(p);
}
function reportWatch(p, force = false) {
  if (!p?.reservation || !(p._watchedSeconds > 0)) return;
  if (!force && performance.now() < (p._reportAfter || 0)) return;
  if (p._reporting) { p._flushWatch ||= force; return; }
  if (!force && p._viewRecorded && p._watchedSeconds - (p._sentSeconds || 0) < 5) return;
  const seconds = p._watchedSeconds;
  p._reporting = true;
  p._reportAfter = performance.now() + 5000;
  fetch('/reels/view', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({client:queueClient, code:p.shortcode, reservation:p.reservation, seconds}), keepalive:true})
    .then(response => {
      if (response.ok) { p._viewRecorded = true; p._sentSeconds = seconds; }
    }).catch(()=>{}).finally(() => {
      p._reporting = false;
      if (p._flushWatch) { p._flushWatch = false; if (p._watchedSeconds > seconds) reportWatch(p, true); }
    });
}
function recordQuickSkip(i) {
  const p = posts[i];
  if (!p) return;
  reportWatch(p, true);
  if (!(p._watchedSeconds > 0)) {
    if (p.reservation) releaseReservations([p.reservation]);
    return;
  }
  if (p.feedback || p._skipSent || p._paused || document.hidden || p._interrupted || p._looped) return;
  const seconds = p._watchedSeconds;
  if (seconds < 0.3 || seconds > 2.5) return;
  p._skipSent = true;
  fetch('/feedback', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({code:p.shortcode, action:'skip'}), keepalive:true}).catch(()=>{});
}
window.addEventListener('pagehide', () => {
  reportWatch(posts[current], true);
  // Keep a playing clip's lease until its final view request can arrive.
  releaseReservations(posts.filter((p, i) => i >= current && !p._dropped && !(p._watchedSeconds > 0)).map(p => p.reservation).filter(Boolean));
});
// A restored page missed imports made elsewhere (e.g. in Settings); re-read
// which creators are in feeds so the add / in feed buttons are right.
async function refreshFeedAccounts() {
  const version = accountChangeVersion, playbackAtStart = playbackVersion;
  try {
    await playbackWrite;
    const response = await fetch('/settings/data');
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'HTTP ' + response.status);
    if (playbackAtStart === playbackVersion) {
      applyPlaybackPreferences(data);
      if (['smooth','snappy'].includes(data.scroll_animation)) scrollAnimation = data.scroll_animation;
    }
    if (version !== accountChangeVersion) return;  // a newer local change wins
    feeded = new Set(Object.values(data.feeds || {}).flat());
    syncAccountButtons();
    if (Array.isArray(data.blocked_accounts)) {
      const changed = data.blocked_accounts.length !== blockedAccounts.size || data.blocked_accounts.some(name => !blockedAccounts.has(name));
      blockedAccounts = new Set(data.blocked_accounts);
      if (changed) hideFilteredReels();
    }
    syncFeedList(Object.keys(data.feeds || {}));
    if ('selected_feeds' in data && !sameFeeds(data.selected_feeds, selectedFeeds)) {
      selectedFeeds = copyFeeds(data.selected_feeds); chosenFeeds = copyFeeds(selectedFeeds);
      feedsChanged();
    }
    renderFeedSelection();
  } catch (error) {
    console.warn('Could not refresh feed accounts', error);
  }
}
function replaceExpiredQueue() {
  if (!(posts[current]?._watchedSeconds > 0)) current = -1;
  dropQueuedReels();
  selectionVersion += 1;
  emptySince = null;
  lastNavigation = null;
  fetching = false;
  clearTimeout(retryTimer); retryTimer = null;
  exhausted = false;
  more();
}
window.addEventListener('pageshow', event => {
  if (event.persisted) {
    // A cached page must read the profile again, not stale origin storage.
    refreshFeedAccounts();
    replaceExpiredQueue();
  }
});
// Settings and other local pages sit over the mounted player. Returning never
// reconstructs its queue, seeks its video, or closes its comments.
function showLocalPage(destination, push = true) {
  cancelPendingTap();
  stopReelHold();
  localPageVersion += 1;
  if (!localPage) {
    const video = document.querySelector('#s'+current+' video');
    const layer = document.createElement('section'); layer.id = 'local-page';
    const back = document.createElement('button'); back.id = 'local-page-back';
    back.type = 'button'; back.textContent = '← Back to reels';
    back.onclick = () => { if (!localPage?.closing) history.go(-(history.state?.igLocalDepth || 1)); };
    const frame = document.createElement('iframe'); frame.title = 'ig';
    localPage = {layer,frame,back,video,resume:!!posts[current] && !posts[current]._paused};
    video?.pause();
    layer.append(back,frame); document.body.append(layer);
    for (const node of [feed,document.querySelector('.top'),document.querySelector('.android-nav')]) if (node) node.inert = true;
    frame.src = destination.href;
  } else if (localPage.href !== destination.href) {
    localPage.frame.contentWindow.location.replace(destination.href);
  }
  localPage.href = destination.href;
  localPage.closing = false;
  // Settings, profiles and Saved reels have their own header with a back arrow.
  localPage.back.hidden = !/^\/reel\//.test(destination.pathname);
  localPage.frame.title = destination.pathname === '/settings' ? 'Settings' : 'ig page';
  if (push) history.pushState({igLocalPage:destination.href,igLocalDepth:(history.state?.igLocalDepth || 0)+1},'',destination.href);
}
async function closeLocalPage() {
  if (!localPage) return;
  const previous = localPage;
  const version = ++localPageVersion;
  previous.closing = true;
  let saveError = null;
  try { await previous.frame.contentWindow.flushLocalChanges?.(); }
  catch (error) { saveError = error; }
  // A newer navigation may have reopened or replaced the page while saving.
  if (localPage !== previous || version !== localPageVersion) return;
  localPage = null;
  previous.layer.remove();
  for (const node of [feed,document.querySelector('.top'),document.querySelector('.android-nav')]) if (node) node.inert = false;
  if (saveError) actionStatus(saveError.message + ' · changes were not saved');
  else await refreshFeedAccounts();
  if (!localPage && previous.resume && previous.video?.isConnected &&
      previous.video === document.querySelector('#s'+current+' video') && !posts[current]?._paused) playVideo(previous.video);
}
routeLocalPage = destination => {
  if (['/','/reels'].includes(destination.pathname)) {
    if (localPage?.closing) return true;
    if (localPage) history.go(-(history.state?.igLocalDepth || 1));
    else if (boot.post_code) return false;
    return true;
  }
  if (destination.origin !== location.origin || !(/^\/(?:settings(?:\/feeds)?|saved-reels)$/.test(destination.pathname) || /^\/(?:u|reel)\//.test(destination.pathname))) return false;
  showLocalPage(destination); return true;
};
window.addEventListener('message', event => {
  if (!localPage || event.source !== localPage.frame.contentWindow || event.origin !== location.origin ||
      event.data?.type !== 'ig-local-navigation') return;
  try {
    const destination = new URL(event.data.href);
    if (destination.origin === location.origin) routeLocalPage(destination);
  } catch (_) {}
});
window.addEventListener('popstate', event => {
  if (event.state?.igLocalPage) {
    const destination = new URL(event.state.igLocalPage);
    if (destination.origin === location.origin) showLocalPage(destination,false);
  } else closeLocalPage();
});
window.handleLocalBack = () => {
  if (!localPage) return false;
  if (localPage.closing) return true;
  const child = localPage.frame.contentWindow;
  const dialog = child.document.querySelector('dialog[open]');
  if (dialog) {dialog.close(); return true;}
  const menu = child.document.querySelector('.feed-menu[open]');
  if (menu) {menu.open = false; return true;}
  const lightbox = child.document.querySelector('.lb:not([hidden])');
  if (lightbox && typeof child.closeLb === 'function') {child.closeLb(); return true;}
  Promise.resolve(child.flushLocalChanges?.()).then(() => history.back()).catch(error => {
    const status = child.document.getElementById('settings-status');
    if (status) status.textContent = error.message;
  });
  return true;
};
let hiddenSince = null;
document.addEventListener('visibilitychange', () => {
  lastNavigation = null;
  emptySince = null;
  if (document.hidden) {
    hiddenSince = Date.now();
    reportWatch(posts[current], true);
    interruptPlayback(current);
  } else {
    // A hidden tab's unseen queue may have expired or been viewed elsewhere.
    if (hiddenSince !== null && Date.now() - hiddenSince >= 60000) replaceExpiredQueue();
    hiddenSince = null;
    scheduleReservationRenewal();
  }
});
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
let songTimerFor = null;
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
    : result.status === 'loading' ? 'Identifying song…'
    : result.status === 'queued' ? 'Waiting to identify song…' : 'Song recognition unavailable';
}
function queueSong(i) {
  if (boot.song_recognition === false) return;
  const p = posts[i];
  if (!p || current !== i || isHidden(p)) return;
  if (p._song) { renderSong(i, p._song); return; }
  if (songTimer && songTimerFor === i) return; // buffering/loops must not restart the delay
  clearTimeout(songTimer);
  songTimer = null;
  songTimerFor = null;
  if (songBusy) {
    if (!p._recognizing) renderSong(i, {status:'queued'});
    return; // completion of the active request schedules the current reel
  }
  songTimerFor = i;
  songTimer = setTimeout(() => {
    songTimer = null;
    songTimerFor = null;
    if (current === i && !p._paused && !isHidden(p)) recognizeSong(i);
  }, Math.max(3000, (p._songRetryAt || 0) - Date.now()));
}
async function recognizeSong(i) {
  const p = posts[i];
  if (songBusy || !p || p._song || isHidden(p) || current !== i) return;
  songBusy = true;
  p._recognizing = true;
  try {
    renderSong(i, {status:'loading'});
    const r = await fetch('/recognize/' + encodeURIComponent(p.shortcode),
      {method:'POST', signal:AbortSignal.timeout(110000)});
    if (!r.ok) throw new Error();
    const result = await r.json();
    if (result.status === 'matched' || result.status === 'no_match') p._song = result;
    else p._songRetryAt = Date.now() + (result.status === 'busy' ? 5000 : 60000);
    if (posts[i] === p) renderSong(i, result);
  } catch (_) {
    p._songRetryAt = Date.now() + 60000;
    if (posts[i] === p) renderSong(i, {status:'unavailable'});
  } finally {
    songBusy = false;
    p._recognizing = false;
    queueSong(current);
  }
}
function showSearch() {
  const dialog = androidSheet('reel-search', 'Search');
  const form = document.createElement('form');
  const input = document.createElement('input');
  input.type = 'search'; input.placeholder = 'Search accounts or paste a link';
  input.setAttribute('aria-label', 'Search accounts or open Instagram link');
  input.autocomplete = 'off'; input.autocapitalize = 'none'; input.spellcheck = false;
  const results = document.createElement('div'); results.className = 'search-results';
  const status = document.createElement('p'); status.setAttribute('role', 'status');
  let accounts = [...feeded], accountsError = false;
  function destination(value) {
    if (/^@?[A-Za-z0-9._]{1,30}$/.test(value)) return '/u/' + value.replace(/^@/, '');
    try {
      const url = new URL(value.startsWith('www.') ? 'https://' + value : value);
      if (url.protocol !== 'https:' || !['instagram.com','www.instagram.com','m.instagram.com'].includes(url.hostname)) return null;
      const code = reelLinkCode(url.href);
      if (code) return '/reel/' + code;
      const match = url.pathname.match(/^\/(?:_u\/)?([A-Za-z0-9._]{1,30})\/?$/);
      if (match && !['explore','accounts','direct','reels','stories','p','reel'].includes(match[1])) return '/u/' + match[1];
    } catch (_) {}
    return null;
  }
  function open(path) {
    visitLocalPage(path).then(() => dialog.close()).catch(error => { status.textContent = error.message; });
  }
  function render() {
    results.replaceChildren(); status.textContent = '';
    const value = input.value.trim(), query = value.replace(/^@/, '').toLowerCase();
    const path = destination(value);
    const matches = accounts.filter(name => name.toLowerCase().includes(query)).sort().slice(0,50);
    const add = (label, path) => {
      const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
      button.onclick = () => open(path); results.append(button);
    };
    if (path && !matches.some(name => path === '/u/' + name)) add(path.startsWith('/reel/') ? 'Open reel' : 'Open @' + path.slice(3), path);
    for (const name of matches) add('@' + name, '/u/' + name);
    if (!results.children.length) status.textContent = value ? 'Enter an exact @handle or an Instagram profile or reel link.' :
      accountsError ? 'Couldn’t load your accounts. You can still enter an @handle or Instagram link.' : 'Enter an @handle or Instagram link.';
  }
  input.oninput = render;
  form.onsubmit = event => { event.preventDefault(); const path = destination(input.value.trim()); if (path) open(path); };
  const viewport = window.visualViewport;
  function fitSearch() {
    const height = viewport?.height || innerHeight;
    dialog.style.setProperty('--search-viewport-height', height + 'px');
    dialog.style.setProperty('--search-bottom', Math.max(0,innerHeight-height-(viewport?.offsetTop || 0)) + 'px');
  }
  viewport?.addEventListener('resize',fitSearch);
  viewport?.addEventListener('scroll',fitSearch);
  window.addEventListener('resize',fitSearch);
  dialog.addEventListener('close',() => {
    viewport?.removeEventListener('resize',fitSearch);
    viewport?.removeEventListener('scroll',fitSearch);
    window.removeEventListener('resize',fitSearch);
  },{once:true});
  form.append(input); dialog.append(form, status, results); render(); fitSearch(); dialog.showModal(); input.focus();
  fetch('/settings/data').then(response => { if (!response.ok) throw new Error(); return response.json(); }).then(data => {
    if (!dialog.isConnected) return;
    accounts = [...new Set(Object.values(data.feeds || {}).flat())]; render();
  }).catch(() => { if (dialog.isConnected) { accountsError = true; render(); } });
}
let sharingReel = false;
async function shareReel(i) {
  const code = posts[i]?.shortcode;
  if (sharingReel || !/^[A-Za-z0-9_-]{5,30}$/.test(code || '')) return;
  // Share a public, clean Instagram URL, never this device's local server URL.
  const url = 'https://www.instagram.com/reel/' + code + '/';
  if (boot.android) {
    window.location.href = '/share/' + code;
    return;
  }
  sharingReel = true;
  try {
    if (navigator.share) {
      try { await navigator.share({url}); return; }
      catch (error) { if (error.name === 'AbortError') return; }
    }
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(url);
    } catch (_) {
      // HTTP phone access may not expose the Clipboard API.
      const previous = document.activeElement;
      const input = document.createElement('textarea');
      input.value = url; input.readOnly = true;
      input.style.cssText = 'position:fixed;top:0;left:-9999px';
      document.body.append(input);
      try {
        input.select();
        if (!document.execCommand('copy')) throw new Error('Could not copy reel link');
      } finally { input.remove(); previous?.focus({preventScroll:true}); }
    }
    actionStatus('Reel link copied');
  } catch (error) { actionStatus(error.message || 'Could not share reel'); }
  finally { sharingReel = false; }
}
async function downloadReel(i, button) {
  const post = posts[i];
  if (!post || button.disabled) return;
  if (boot.android) {
    window.location.href = '/download/' + encodeURIComponent(post.shortcode);
    return;
  }
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
function setAutoScroll(value) {
  autoScroll = value;
  document.querySelectorAll('#feed video').forEach(v => { v.loop = !autoScroll; });
}
function toggleAutoScroll() {
  autoScroll = !autoScroll;
  savePlaybackPreferences({auto_scroll:autoScroll}).catch(() => actionStatus('Auto-scroll changed for now · could not remember it'));
  document.querySelectorAll('#feed video').forEach(v => { v.loop = !autoScroll; });
  // Returning to looping should resume an ended reel, unless explicitly paused.
  const video = document.querySelector('#s'+current+' video');
  if (!autoScroll && video?.ended && !posts[current]?._paused) playVideo(video);
  actionStatus('Auto-scroll ' + (autoScroll ? 'on' : 'off'));
}
function advanceAutoScroll() {
  if (localPage || !autoScroll || document.hidden || posts[current]?._paused ||
      !document.getElementById('comments-panel').hidden ||
      document.getElementById('feed-menu').open || document.getElementById('feed-picker') ||
      document.getElementById('shortcut-help') || document.querySelector('dialog[open]')) return;
  const video = document.querySelector('#s'+current+' video');
  if (!video?.ended || video.error) return;
  // At the queue's end, keep waiting for refill instead of replaying or skipping.
  if (visibleIndices().some(i => i > current)) goRel(1);
  else maybeMore();
}
setInterval(advanceAutoScroll, 500);
function updatePauseIcon(i, paused) {
  const indicator = document.getElementById('pause'+i);
  if (indicator) indicator.hidden = !paused;
}
function togglePlayback() {
  lastNavigation = null;
  const p = posts[current];
  if (!p || isHidden(p)) return;
  const video = document.getElementById('s'+current)?.querySelector('video');
  p._paused = video ? !video.paused : !p._paused;
  updatePauseIcon(current, p._paused);
  stopReelHold();
  if (video) p._paused ? video.pause() : playVideo(video);
}
function showSlide(i) {
  if (typeof reelZoom !== 'undefined') reelZoom?.reset();
  cancelPendingTap();
  stopReelHold();
  if (creatorTrail && i !== creatorTrail.index) leaveCreatorBrowse();
  if (i === undefined || isHidden(posts[i])) return;
  document.querySelectorAll('video').forEach(v => v.pause());
  if (current !== i) recordQuickSkip(current);
  const animate = current >= 0 && current !== i && scrollAnimation === 'smooth';
  if (scrollTarget?.frame) cancelAnimationFrame(scrollTarget.frame);
  scrollTarget = null;
  current = i;
  scrollBurst = null; // a deliberate move ends any scroll burst; see noteScroll
  const target = document.getElementById('s'+i);
  if (animate && target) {
    // Native smooth scrolling combined with mandatory snap is inconsistent
    // across browsers/WebView. Slide one viewport ourselves, then restore snap.
    const from = feed.scrollTop;
    const to = from + slideOffset(i);
    const pending = {index:i, started:performance.now(), frame:0, position:from};
    scrollTarget = pending;
    feed.style.scrollSnapType = 'none';
    const frame = now => {
      if (scrollTarget !== pending) return;
      const progress = Math.min(1, Math.max(0, (now - pending.started) / 280));
      const eased = 1 - Math.pow(1 - progress, 3);
      pending.position = from + (to - from) * eased;
      feed.scrollTop = pending.position;
      if (progress < 1 && !isHidden(posts[i])) pending.frame = requestAnimationFrame(frame);
      else {
        if (!isHidden(posts[i])) target.scrollIntoView({behavior:'instant', block:'start'});
        scrollTarget = null;
        feed.style.scrollSnapType = '';
        lastScrollTop = feed.scrollTop;
      }
    };
    pending.frame = requestAnimationFrame(frame);
  } else {
    target?.scrollIntoView({behavior:'instant', block:'start'});
    feed.style.scrollSnapType = '';
  }
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
  const accountVersion = accountChangeVersion;
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
      blockUpdates.set(owner, {value:true, version:++accountChangeVersion});
      blockedAccounts = new Set(d.blocked);
      blockUpdates.forEach(({value:blocked, version}, username) => {
        if (version > accountVersion) blocked ? blockedAccounts.add(username) : blockedAccounts.delete(username);
      });
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
  if (!p || isHidden(p)) { chooseFeed(); return; }
  const button = document.getElementById('s'+current)?.querySelector('.account-action');
  if (button) addAcc(p.owner, button);
}
function removeCurrent() {
  const p = posts[current];
  if (!p || isHidden(p)) return;
  const button = document.getElementById('s'+current)?.querySelector('.account-action');
  return rmAcc(p.owner, button);
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
function replaceCreatorSlide(post, play = true) {
  const i = current;
  recordQuickSkip(i);
  clearTimeout(songTimer); songTimer = null; songTimerFor = null;
  const old = document.getElementById('s'+i);
  if (old) { io.unobserve(old); releaseVideo(old); }
  posts[i] = post;
  if (old) old.outerHTML = slideHtml(post, i);
  observeNew();
  if (play) showSlide(i);
}
function leaveCreatorBrowse() {
  const trail = creatorTrail;
  if (!trail) return;
  creatorTrail = null;
  clearTimeout(trail.timer); trail.timer = null;
  // Leaving vertically keeps this slot and its horizontal position intact.
  // Invalidate any pending response even if we return before it arrives.
  trail.request = (trail.request || 0) + 1; trail.busy = false;
  actionStatus('');
}
async function browseCreator(direction) {
  const p = posts[current];
  if (!p || isHidden(p)) return;
  if (!creatorTrail) {
    creatorTrail = creatorTrails.get(current) || null;
    if (!creatorTrail) {
      if (direction < 0) return;
      creatorTrail = {index:current, owner:p.owner, items:[p], at:0, busy:false, timer:null};
      creatorTrails.set(current, creatorTrail);
    }
  }
  const trail = creatorTrail;
  if (direction < 0) {
    clearTimeout(trail.timer); trail.timer = null;
    // Cancel a pending next-reel response without disrupting the cached trail.
    trail.request = (trail.request || 0) + 1; trail.busy = false;
    if (trail.at === 0) { leaveCreatorBrowse(); return; }
    replaceCreatorSlide(trail.items[--trail.at]);
  } else if (trail.at + 1 < trail.items.length) {
    replaceCreatorSlide(trail.items[++trail.at]);
  } else {
    if (trail.busy || trail.timer) return;
    trail.busy = true;
    const request = trail.request = (trail.request || 0) + 1;
    actionStatus('Loading more from @' + trail.owner + '…', {stay:true});
    try {
      const r = await fetch('/creator/' + encodeURIComponent(trail.owner) + '/more', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({client:queueClient, exclude_codes:excludedReelCodes(),
          session_blocked:[...sessionBlocked]}), signal:AbortSignal.timeout(BATCH_TIMEOUT_MS)});
      const data = await r.json();
      if (creatorTrail !== trail || current !== trail.index || request !== trail.request) {
        releaseReservations((data.posts || []).map(p => p.reservation).filter(Boolean)); return;
      }
      if (!r.ok || data.error) throw new Error(data.error || 'Could not load this creator');
      const next = (data.posts || []).find(p => p.owner === trail.owner &&
        !isHidden(p) && !excludedReelCodes().includes(p.shortcode));
      releaseReservations((data.posts || []).filter(p => p !== next).map(p => p.reservation).filter(Boolean));
      if (next) {
        trail.items.push(next); trail.at += 1; replaceCreatorSlide(next);
        actionStatus('');
      } else if (data.has_more) {
        trail.timer = setTimeout(() => {trail.timer=null; if (creatorTrail === trail) browseCreator(1);},
          Math.max(750, Math.min(30000, data.retry_after_ms || 1500)));
      } else actionStatus('No more unseen reels from @' + trail.owner);
    } catch (error) {
      if (creatorTrail === trail && request === trail.request) actionStatus(error.message);
    } finally {
      if (request === trail.request) trail.busy = false;
    }
  }
}
function openProfile(i = current) {
  const owner = posts[i]?.owner;
  if (!owner) return;
  const url = '/u/' + encodeURIComponent(owner);
  // The Android app has one WebView; open the profile in place so Back returns here.
  if (boot.android) visitLocalPage(url).catch(error => actionStatus(error.message));
  else window.open(url, '_blank', 'noopener');
}
const heldKeys = new Set();
function releaseReelKey(e) { heldKeys.delete(e.code || e.key.toLowerCase()); }
function handleReelKey(e) {
  if (e.defaultPrevented || e.ctrlKey || e.altKey || e.metaKey ||
      document.getElementById('feed-picker') || document.getElementById('shortcut-help') ||
      (e.target?.closest('input, textarea, select, [contenteditable="true"], dialog') &&
       !(e.key.toLowerCase() === 'h' && e.target.matches('input[type="range"]') && !e.target.closest('dialog')))) return;
  const key = e.key.toLowerCase();
  const menu = document.getElementById('feed-menu');
  // Multi-select leaves this menu open. J/K resumes watching immediately;
  // other keys retain their normal behavior inside the selector.
  if (menu.open) {
    if (!['j', 'k', '+', 'a'].includes(key)) return;
    menu.open = false;
  }
  // Space retains native activation for a focused button/link; navigation keys
  // must always use our one-step handler, including after clicking controls.
  if (key === ' ' && e.target?.closest('button, a, summary')) return;
  const actions = {'?':showShortcuts, '+':()=>chooseFeed('', true), ' ':togglePlayback, m:toggleMute, c:()=>toggleComments(current), l:()=>likeReel(current), n:()=>rateReel(current, 'dislike'), a:()=>e.shiftKey ? chooseFeed() : addCurrent(), r:removeCurrent, p:()=>openProfile(), w:()=>toggleWhy(current),
    s:()=>toggleSavedReel(current), d:downloadCurrent, t:toggleAutoScroll, h:toggleCleanView,
    b:()=>blockCurrent(e.shiftKey), arrowdown:()=>goRel(1), j:()=>goRel(1),
    arrowup:()=>goRel(-1), k:()=>goRel(-1),
    arrowright:()=>browseCreator(1), arrowleft:()=>browseCreator(-1)};
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
  leaveCreatorBrowse();
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
  if (el.closest?.('.reel-seek')) return true;
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
// Other scrolling we did not start (such as a keyboard-scrolling extension
// like Vimium, which handles j/k before this page sees them) can also arrive
// as a burst of small steps, and snapping turns each step into a whole reel.
// Let each burst move at most one reel from where it started.
let scrollBurst = null; // {to, step, last}
let burstFrame = 0;
let lastScrollTop = 0;
function slideOffset(i) {
  return document.getElementById('s'+i).getBoundingClientRect().top - feed.getBoundingClientRect().top;
}
function limitScrollBurst() {
  if (scrollTarget) return true;
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
  if (scrollTarget) {
    // An extension may keep issuing scrollBy steps after its key was consumed.
    // Keep those steps from moving or snapping past our animated target.
    if (Math.abs(top - scrollTarget.position) > 1) {
      if (scrollBurst?.external) scrollBurst.last = now;
      feed.scrollTop = scrollTarget.position;
    }
    return;
  }
  if (scrollBurst && now - scrollBurst.last > SCROLL_BURST_GAP_MS) scrollBurst = null;
  if (scrollBurst?.external) {
    if (Math.abs(slideOffset(scrollBurst.to)) > 1) {
      scrollBurst.last = now;
      document.getElementById('s'+scrollBurst.to)?.scrollIntoView({behavior:'instant'});
    }
    return;
  }
  if (!scrollBurst) {
    if (current < 0 || isHidden(posts[current]) || !document.getElementById('s'+current)) return;
    // Our own scrollIntoView, or a layout change that kept the reel in place.
    if (Math.abs(slideOffset(current)) < 1 || !moved) return;
    const step = Math.sign(moved);
    const indices = visibleIndices();
    if (scrollAnimation === 'smooth') {
      const to = indices[indices.indexOf(current) + step] ?? current;
      const origin = top + slideOffset(current);
      feed.style.scrollSnapType = 'none';
      feed.scrollTop = origin;
      showSlide(to);
      scrollBurst = {to, step, last:now, external:true};
      return;
    }
    scrollBurst = {to: indices[indices.indexOf(current) + step] ?? current, step};
  }
  scrollBurst.last = now;
  limitScrollBurst();
  if (!burstFrame) burstFrame = requestAnimationFrame(watchScrollBurst);
}
feed.addEventListener('scroll', noteScroll, {passive:true});
// Touch gestures have explicit start/end boundaries. Do not infer a new swipe
// from pauses between scroll events: slow dragging and momentum can both pause.
// Cancel native vertical panning and commit one move when the finger lifts.
// Clean view is deliberately per page: navigation overlays leave the player intact.
const reelZoom = window.createMediaZoom?.();
let cleanView = false, cleanViewHintShown = false, cleanSeekTimer = null;
function setCleanView(enabled) {
  cleanView = Boolean(enabled);
  document.body.classList.toggle('clean-view', cleanView);
  document.body.classList.remove('clean-seek-visible');
  clearTimeout(cleanSeekTimer);
  if (cleanView && !cleanViewHintShown) {
    cleanViewHintShown = true;
    actionStatus(matchMedia('(pointer:coarse)').matches ? 'Two-finger tap to show controls.' : 'Press H to show controls.');
  }
}
function toggleCleanView() { if (!localPage) setCleanView(!cleanView); }
window.exitCleanView = () => {
  if (!cleanView || localPage) return false;
  setCleanView(false); return true;
};
function revealCleanSeek() {
  if (!cleanView) return;
  document.body.classList.add('clean-seek-visible');
  clearTimeout(cleanSeekTimer);
  cleanSeekTimer = setTimeout(() => {
    if (document.querySelector('.reel-seek input[data-scrubbing],.reel-seek input:focus-visible')) revealCleanSeek();
    else document.body.classList.remove('clean-seek-visible');
  }, 2500);
}
let cleanGesture = null, cleanFirstTouch = null, cleanSeekTouch = false;
function cleanVideoTarget(target) {
  return !localPage && document.getElementById('comments-panel').hidden && !document.querySelector('dialog[open]') &&
    !target.closest('button,a,input,.info,.bar,.reel-seek,#comments-panel') &&
    target.closest('.slide')?.id === 's'+current;
}
function consumeCleanGesture(event) {
  if (event.cancelable) event.preventDefault();
  event.stopImmediatePropagation();
  cancelPendingTap(); stopReelHold(); reelTouch = null;
  ignoreHoldClickUntil = performance.now() + 800;
}
document.addEventListener('touchstart', event => {
  if (event.touches.length > 1 || cleanGesture) {
    if (!cleanGesture && !cleanFirstTouch && ![...event.touches].some(t => cleanVideoTarget(t.target))) return;
    if (cleanSeekTouch?.range) delete cleanSeekTouch.range.dataset.scrubbing;
    if (!cleanGesture) {
      const points = [...event.touches];
      if (points.length === 2 && points.every(t => cleanVideoTarget(t.target))) {
        const slide = points[0].target.closest('.slide');
        reelZoom?.begin(slide.querySelector('video') || slide.querySelector('img.poster'), points);
      }
      cleanGesture = {at:cleanFirstTouch?.at ?? performance.now(), valid:!cleanSeekTouch && !cleanFirstTouch?.moved && points.length === 2 && points.every(t => cleanVideoTarget(t.target)),
        points:new Map(points.map(t => [t.identifier,{x:t.clientX,y:t.clientY}]))};
    } else cleanGesture.valid = false;
    consumeCleanGesture(event); return;
  }
  if (!cleanVideoTarget(event.target)) { cleanFirstTouch = null; return; }
  const first = event.touches[0];
  cleanFirstTouch = {at:performance.now(),x:first.clientX,y:first.clientY,moved:false};
  if (cleanView && !document.body.classList.contains('clean-seek-visible') && cleanVideoTarget(event.target)) {
    const bounds = event.target.closest('.slide').getBoundingClientRect();
    if (event.touches[0].clientY >= bounds.bottom - 48) {
      revealCleanSeek(); cleanSeekTouch = {x:first.clientX,index:current,range:document.querySelector('#s'+current+' .reel-seek input')}; consumeCleanGesture(event);
    }
  }
}, {capture:true,passive:false});
document.addEventListener('touchmove', event => {
  if (cleanFirstTouch && event.touches.length === 1) {
    const touch = event.touches[0];
    if (Math.hypot(touch.clientX-cleanFirstTouch.x,touch.clientY-cleanFirstTouch.y) > 12) cleanFirstTouch.moved = true;
  }
  if (cleanSeekTouch && !cleanGesture) {
    consumeCleanGesture(event);
    const touch = event.touches[0], range = cleanSeekTouch.range;
    if (range && !range.disabled && current === cleanSeekTouch.index && Math.abs(touch.clientX-cleanSeekTouch.x)>6) {
      const bounds = range.getBoundingClientRect();
      range.dataset.scrubbing = '1';
      range.value = Math.max(0,Math.min(1000,(touch.clientX-bounds.left)/bounds.width*1000));
      seekReel(current,range); revealCleanSeek();
    }
    return;
  }
  if (!cleanGesture) return;
  reelZoom?.move(event.touches);
  for (const touch of event.touches) {
    const start = cleanGesture.points.get(touch.identifier);
    if (!start || Math.hypot(touch.clientX-start.x,touch.clientY-start.y) > 12) cleanGesture.valid = false;
  }
  consumeCleanGesture(event);
}, {capture:true,passive:false});
document.addEventListener('touchend', event => {
  if (!event.touches.length) cleanFirstTouch = null;
  if (cleanSeekTouch && !cleanGesture) { consumeCleanGesture(event); if (!event.touches.length) resetCleanGesture(); return; }
  if (!cleanGesture) return;
  for (const touch of event.changedTouches) {
    const start = cleanGesture.points.get(touch.identifier);
    if (!start || Math.hypot(touch.clientX-start.x,touch.clientY-start.y) > 12) cleanGesture.valid = false;
  }
  consumeCleanGesture(event);
  if (!event.touches.length) {
    const toggle = cleanGesture.valid && performance.now()-cleanGesture.at <= 300;
    reelZoom?.end();
    cleanGesture = null; cleanSeekTouch = false;
    if (toggle) toggleCleanView();
  }
}, {capture:true,passive:false});
document.addEventListener('touchcancel', event => {
  if (cleanGesture || cleanSeekTouch) consumeCleanGesture(event);
  resetCleanGesture();
}, {capture:true,passive:false});
function resetCleanGesture() {
  reelZoom?.reset();
  if (cleanSeekTouch?.range) delete cleanSeekTouch.range.dataset.scrubbing;
  cleanGesture = null; cleanFirstTouch = null; cleanSeekTouch = false;
}
window.addEventListener('blur', resetCleanGesture);
document.addEventListener('visibilitychange', () => { if (document.hidden) resetCleanGesture(); });
feed.addEventListener('pointermove', event => {
  if (!cleanView || event.pointerType === 'touch') return;
  const slide = event.target.closest('.slide');
  if (slide && event.clientY >= slide.getBoundingClientRect().bottom-48) revealCleanSeek();
});
feed.addEventListener('input', event => { if (event.target.closest('.reel-seek')) revealCleanSeek(); });
feed.addEventListener('wheel', event => {
  if (!cleanVideoTarget(event.target)) return;
  const slide = event.target.closest('.slide');
  reelZoom?.wheel(slide.querySelector('video') || slide.querySelector('img.poster'), event);
}, {capture:true,passive:false});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && reelZoom?.isZoomed()) {
    event.preventDefault(); event.stopImmediatePropagation(); reelZoom.reset();
  }
}, true);
function zoomCurrentReel() {
  const slide = document.getElementById('s'+current);
  if (!slide) return;
  if (reelZoom?.isZoomed()) reelZoom.reset();
  else reelZoom?.zoom(slide.querySelector('video') || slide.querySelector('img.poster'));
}

let reelTouch = null;
let reelHold = null, ignoreHoldClickUntil = 0;
function stopReelHold() {
  if (!reelHold) return;
  clearTimeout(reelHold.timer);
  if (reelHold.active) {
    reelHold.video.playbackRate = reelHold.rate;
    ignoreHoldClickUntil = performance.now() + 700;
  }
  reelHold.badge?.remove();
  reelHold = null;
}
feed.addEventListener('click', event => {
  if (performance.now() < ignoreHoldClickUntil && !event.target.closest('button,a,.bar,.info,.reel-seek')) {
    event.preventDefault(); event.stopImmediatePropagation();
  }
}, true);
feed.addEventListener('contextmenu', event => {
  if (reelHold) event.preventDefault();
});
window.addEventListener('blur', () => { stopReelHold(); cancelPendingTap(); });
document.addEventListener('visibilitychange', () => { if (document.hidden) { stopReelHold(); cancelPendingTap(); } });
feed.addEventListener('touchstart', e => {
  stopReelHold();
  if (pendingTap) clearTimeout(pendingTap.timer);
  if (e.target.closest('.reel-seek')) { reelTouch = null; return; }
  if (e.touches.length !== 1 || scrollsItself(e.target)) { reelTouch = null; return; }
  const t = e.touches[0];
  reelTouch = {id:t.identifier, x:t.clientX, y:t.clientY, dx:0, dy:0, vertical:false};
  const video = document.querySelector('#s'+current+' video');
  if (!video || e.target.closest('button,a,.bar,.info') || e.target.closest('.slide')?.id !== 's'+current) return;
  const hold = reelHold = {video,rate:video.playbackRate,active:false};
  hold.timer = setTimeout(() => {
    if (reelHold !== hold || !reelTouch || video.paused || localPage) return;
    cancelPendingTap();
    hold.active = true; reelTouch.held = true; video.playbackRate = 2;
    hold.badge = document.createElement('span'); hold.badge.className = 'hold-speed';
    hold.badge.textContent = '2×'; video.closest('.slide').append(hold.badge);
  }, 350);
}, {passive:true});
feed.addEventListener('touchmove', e => {
  if (!reelTouch) return;
  if (e.touches.length !== 1) { stopReelHold(); reelTouch = null; return; }
  const t = [...e.touches].find(t => t.identifier === reelTouch.id);
  if (!t) { reelTouch = null; return; }
  reelTouch.dx = t.clientX - reelTouch.x;
  reelTouch.dy = t.clientY - reelTouch.y;
  if (Math.hypot(reelTouch.dx, reelTouch.dy) > 12) { stopReelHold(); cancelPendingTap(); }
  if (reelTouch.horizontal) return;
  if (!reelTouch.vertical && Math.abs(reelTouch.dx) > Math.abs(reelTouch.dy)) {
    // A sideways swipe browses the current creator, like Left/Right on a keyboard.
    if (Math.abs(reelTouch.dx) > 10) reelTouch.horizontal = true;
    return;
  }
  if (e.cancelable) {
    e.preventDefault();
    reelTouch.vertical = true;
  } else {
    reelTouch = null; // The browser already owns this gesture; don't also navigate.
  }
}, {passive:false});
feed.addEventListener('touchend', e => {
  const gesture = reelTouch;
  stopReelHold();
  reelTouch = null;
  if (gesture?.held) return;
  if (!gesture || e.touches.length || !(gesture.vertical || gesture.horizontal)) return;
  const t = [...e.changedTouches].find(t => t.identifier === gesture.id);
  if (!t) return;
  if (gesture.horizontal) {
    const sideways = t.clientX - gesture.x;
    // Swipe left for the creator's next reel, right to go back.
    if (Math.abs(sideways) >= 60) browseCreator(sideways < 0 ? 1 : -1);
    return;
  }
  const distance = gesture.y - t.clientY;
  if (Math.abs(distance) >= 48) goRel(Math.sign(distance));
}, {passive:true});
feed.addEventListener('touchcancel', () => { stopReelHold(); cancelPendingTap(); reelTouch = null; }, {passive:true});
updateFeedStatus();
if (boot.android && typeof setupAndroidUI === 'function') setupAndroidUI();
setMuted(muted);
setAutoScroll(autoScroll);
more();

function closeComments() {
  const panel = document.getElementById('comments-panel');
  panel.hidden = true;
  panel.classList.remove('dragging');
  panel.style.removeProperty('--sheet-drag');
  delete panel.dataset.code;
}
const commentsHeading = document.querySelector('.comments-heading');
let sheetDrag = null;
commentsHeading.addEventListener('pointerdown', e => {
  if (!matchMedia('(max-width:640px)').matches || e.target.closest('button')) return;
  sheetDrag = {id:e.pointerId, y:e.clientY};
  commentsHeading.setPointerCapture(e.pointerId);
});
commentsHeading.addEventListener('pointermove', e => {
  if (!sheetDrag || sheetDrag.id !== e.pointerId) return;
  const panel = document.getElementById('comments-panel');
  panel.classList.add('dragging');
  panel.style.setProperty('--sheet-drag', Math.max(0, e.clientY-sheetDrag.y)+'px');
});
function finishSheetDrag(e) {
  if (!sheetDrag || sheetDrag.id !== e.pointerId) return;
  const dismiss = e.type === 'pointerup' && e.clientY-sheetDrag.y > 80;
  sheetDrag = null;
  const panel = document.getElementById('comments-panel');
  panel.classList.remove('dragging');
  panel.style.removeProperty('--sheet-drag');
  if (dismiss) closeComments();
}
commentsHeading.addEventListener('pointerup', finishSheetDrag);
commentsHeading.addEventListener('pointercancel', finishSheetDrag);
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') closeComments();
});
function commentRow(comment) {
  return `<article class="comment-row">
    <a href="/u/${encodeURIComponent(comment.username)}" target="_blank" rel="noopener">@${esc(comment.username)}</a>
    ${comment.is_owner ? '<span class="comment-creator">Creator</span>' : ''}
    <p>${esc(comment.text)}</p><small>${fmt(comment.like_count)} likes</small></article>`;
}
function commentKey(comment) {
  return comment.id ? 'id:' + comment.id : JSON.stringify([comment.username, comment.text, comment.created_at]);
}
function commentsPage(p, after) {
  return fetch('/comments/' + encodeURIComponent(p.shortcode) + (after ? '?after=' + encodeURIComponent(after) : ''),
    {method:'POST', signal:AbortSignal.timeout(45000)}).then(async response => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Could not load comments');
      return data;
    });
}
function commentsVisible(p) {
  const panel = document.getElementById('comments-panel');
  return !panel.hidden && panel.dataset.code === p.shortcode;
}
function renderComments(p) {
  if (!commentsVisible(p)) return;
  const body = document.getElementById('comments-body');
  const top = body.scrollTop;
  body.innerHTML = (p._comments || []).map(commentRow).join('');
  const missing = !p._comments?.length && p.comment_count > 0 && !p._commentCursor;
  if (missing && !p._commentsLoading && !p._commentError) {
    body.textContent = `Instagram lists ${fmt(p.comment_count)} ${p.comment_count === 1 ? 'comment' : 'comments'}, but returned none.`;
  }
  if (p._commentsLoading) {
    body.insertAdjacentHTML('beforeend', '<div id="comments-more" role="status">Loading comments…</div>');
  } else if (p._commentError || p._commentCursor || missing) {
    const button = document.createElement('button');
    button.id = 'comments-more';
    button.type = 'button';
    button.textContent = p._commentError ? 'Couldn’t load comments · Retry' : missing ? 'Retry' : 'Load more comments';
    button.onclick = () => loadMoreComments(p);
    body.appendChild(button);
  } else if (!p._comments?.length) {
    body.textContent = p.comment_count === 0 ? 'No comments yet.' : 'No comments returned.';
  }
  body.scrollTop = top;
}
async function toggleComments(i) {
  const p = posts[i];
  if (!p || isHidden(p)) return;
  const panel = document.getElementById('comments-panel');
  if (!panel.hidden && panel.dataset.code === p.shortcode) { closeComments(); return; }
  panel.hidden = false;
  panel.dataset.code = p.shortcode;
  document.getElementById('comments-body').scrollTop = 0;
  if (!p._comments && !p._commentsLoading) await loadMoreComments(p);
  else renderComments(p);
}
async function loadMoreComments(p) {
  const retryEmpty = p._comments?.length === 0 && p.comment_count > 0;
  if (p._commentsLoading || (p._comments && !p._commentCursor && !retryEmpty)) return;
  const after = p._comments ? p._commentCursor : null;
  let added = 0;
  p._commentError = false;
  p._commentsLoading = commentsPage(p, after).then(data => {
    p._comments ||= [];
    p._commentKeys ||= new Set(p._comments.map(commentKey));
    p._commentCursors ||= new Set();
    if (after) p._commentCursors.add(after);
    for (const comment of data.comments || []) {
      const key = commentKey(comment);
      if (p._commentKeys.has(key)) continue;
      p._commentKeys.add(key);
      p._comments.push(comment);
      added++;
    }
    const next = data.next_cursor || null;
    p._commentCursor = p._commentCursors.has(next) ? null : next;
    p._commentAutoPaused = added === 0;
  }).catch(() => {
    p._commentError = true;
  }).finally(() => {
    p._commentsLoading = null;
    renderComments(p);
    // Keep filling a short panel, but stop automatic requests on errors or
    // duplicate-only pages. The button still works when there is no scrollbar.
    const body = document.getElementById('comments-body');
    if (added && p._commentCursor && commentsVisible(p) && body.scrollHeight <= body.clientHeight) {
      setTimeout(() => {
        if (commentsVisible(p) && !p._commentError) loadMoreComments(p);
      }, 0);
    }
  });
  renderComments(p);
  await p._commentsLoading;
}
document.getElementById('comments-body').addEventListener('scroll', event => {
  const el = event.target;
  const panel = document.getElementById('comments-panel');
  const p = posts.find(x => x && x.shortcode === panel.dataset.code);
  if (panel.hidden || !p || p._commentsLoading || p._commentError || p._commentAutoPaused || !p._commentCursor) return;
  if (el.scrollHeight - el.scrollTop - el.clientHeight > 140) return;
  loadMoreComments(p);
});
