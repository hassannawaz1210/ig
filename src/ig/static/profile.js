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
// @usernames link to their profiles (common.js); plain escaped text without it.
const mentions = text => typeof linkifyMentions === 'function' ? linkifyMentions(text) : esc(text);
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
      onerror="showMissingImage(this)">` : `<div class="missing" data-fill="${i}">${p.is_carousel ? '' : 'no preview'}</div>`}
      ${badge ? `<div class="badge">${badge}</div>` : ''}
      <div class="overlay"><span>${icon('heart')} ${short(p.like_count)}</span>${plays}
        ${p.taken_at ? `<span class="age" title="${esc(fdate(p.taken_at))}">${postAge(p.taken_at, {short:true})}</span>` : ''}</div></div>
    <div class="meta"><div class="stats"><span>${icon('heart')} ${fmt(p.like_count)}</span>
      <span>${icon('comment')} ${fmt(p.comment_count)}</span><span class="date" title="${esc(fdate(p.taken_at))}">${p.taken_at ? postAge(p.taken_at) + ' · ' + fdate(p.taken_at) : ''}</span></div>
    <div class="cap">${esc((p.caption||'').slice(0,240))}</div></div></div>`;
}
// Some older multi-image posts arrive without a preview image. When such a
// tile comes into view, load the post and use its first item instead.
const previewFiller = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    previewFiller.unobserve(entry.target);
    const placeholder = entry.target, post = store[+placeholder.dataset.fill];
    if (!post) continue;
    ensureProfileMedia(post).then(full => {
      const first = full.slides?.find(item => item.image)?.image || full.poster;
      if (!first) { placeholder.textContent = 'no preview'; return; }
      post.image = first;
      const img = document.createElement('img');
      img.alt = ''; img.src = first; img.onerror = () => showMissingImage(img);
      placeholder.replaceWith(img);
    }).catch(error => {
      console.warn('Could not load a preview for ' + post.shortcode, error);
      placeholder.textContent = 'no preview';
    });
  }
}, {rootMargin: '300px'}) : null;
function fillMissingPreviews() {
  document.querySelectorAll('#grid .missing[data-fill]').forEach(el => {
    if (previewFiller) previewFiller.observe(el); else el.textContent = 'no preview';
  });
}
function render() {
  const chunk = incoming.splice(0);
  store.push(...chunk);
  grid.insertAdjacentHTML('beforeend', chunk.map((p,k)=>card(p, rendered+k)).join(''));
  rendered += chunk.length;
  fillMissingPreviews();
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
    // Instagram-style: swipe sideways, with a "2/5" counter and dots.
    const many = p.slides.length > 1;
    return '<div class="carousel"><div class="slides">' + p.slides.map(s =>
      s.video
        ? `<video controls muted loop playsinline src="${esc(s.video)}"></video>`
        : `<img src="${esc(s.image)}" alt="">`).join('') + '</div>' +
      (many ? `<span class="slide-count">1/${p.slides.length}</span><div class="slide-dots" aria-hidden="true">` +
        p.slides.map((_, k) => `<i${k ? '' : ' class="on"'}></i>`).join('') + '</div>' : '') + '</div>';
  }
  if (p.video) {
    return `<video controls muted loop playsinline
      poster="${esc(p.poster || p.image)}" src="${esc(p.video)}"></video>`;
  }
  // Full post details carry the full-size photo as the poster.
  return `<img src="${esc(p.poster || p.image)}" alt="">`;
}
function updateSlideDots() {
  const carousel = document.querySelector('#lbcontent .slides');
  if (!carousel) return;
  const at = Math.round(carousel.scrollLeft / (carousel.clientWidth || 1));
  const count = document.querySelector('#lbcontent .slide-count');
  if (count) count.textContent = `${at + 1}/${carousel.children.length}`;
  document.querySelectorAll('#lbcontent .slide-dots i').forEach((dot, k) => dot.classList.toggle('on', k === at));
}
// While the full post loads, show its grid preview so opening is instant.
function previewHtml(p) {
  return p.image ? `<div class="preview-wait"><img src="${esc(p.image)}" alt=""><span class="spinner" aria-hidden="true"></span></div>`
    : '<div class="loading">Loading…</div>';
}
// Fetch the next posts' full details in the background, one at a time,
// so swiping on doesn't wait. Failures only mean that post loads when opened.
let prefetchChain = Promise.resolve();
function prefetchAround(i) {
  for (const k of [i + 1, i + 2, i - 1]) {
    const post = store[k];
    if (!post || post._rich) continue;
    prefetchChain = prefetchChain.then(() => ensureProfileMedia(post)).catch(error => {
      console.warn('Could not preload ' + post.shortcode, error);
    });
  }
}
const lbEl = document.getElementById('lb');
let profileMediaVersion = 0;
let profileSound = (typeof initialPlaybackPreferences === 'function' ? initialPlaybackPreferences(boot) : boot).sound === true;
let profilePlayingVideo = null;
const profileMuteState = new WeakMap();
const profileBoundVideos = new WeakSet();
function muteProfileVideo(video, muted) {
  profileMuteState.set(video, muted);
  video.muted = muted;
}
function syncProfilePlayback() {
  const box = document.getElementById('lbcontent');
  const carousel = box.querySelector('.slides');
  const item = carousel ? carousel.children[Math.round(carousel.scrollLeft / (carousel.clientWidth || 1))] : box.querySelector('video');
  const video = !lbEl.hidden && !document.hidden && item?.tagName === 'VIDEO' ? item : null;
  box.querySelectorAll('video').forEach(other => { if (other !== video) other.pause(); });
  if (profilePlayingVideo === video) return; // scrolling must not undo a manual pause
  profilePlayingVideo?.pause();
  profilePlayingVideo = video;
  if (!video) return;
  if (!profileBoundVideos.has(video)) {
    profileBoundVideos.add(video);
    video.addEventListener('volumechange', () => {
      if (profileMuteState.get(video) === video.muted) return;
      profileMuteState.set(video, video.muted);
      profileSound = !video.muted;
      if (typeof savePlaybackPreferences === 'function') savePlaybackPreferences({sound:profileSound})
        .catch(error => console.warn('Could not remember sound preference', error));
    });
  }
  muteProfileVideo(video, !profileSound);
  const currentVideo = () => profilePlayingVideo === video && !lbEl.hidden && !document.hidden;
  const started = () => { if (!currentVideo()) video.pause(); };
  Promise.resolve(video.play()).then(started).catch(error => {
    if (!currentVideo() || error.name !== 'NotAllowedError' || video.muted) return;
    // Browser autoplay policy may require silence. Keep the user's saved choice.
    muteProfileVideo(video, true);
    Promise.resolve(video.play()).then(started).catch(() => {});
  });
}
let profileResumeOnVisible = false;
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    profileResumeOnVisible = Boolean(profilePlayingVideo && !profilePlayingVideo.paused);
    profilePlayingVideo?.pause();
  } else if (profileResumeOnVisible) {
    profileResumeOnVisible = false;
    profilePlayingVideo = null;
    syncProfilePlayback();
  }
});
window.addEventListener('pagehide', () => { profilePlayingVideo?.pause(); profilePlayingVideo = null; });
window.addEventListener('pageshow', () => { if (!lbEl.hidden) syncProfilePlayback(); });

function ensureProfileMedia(post) {
  if (post._rich) return Promise.resolve(post);
  if (post._mediaRequest) return post._mediaRequest;
  post._mediaRequest = fetch(location.pathname + '/media', {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({code:post.shortcode}),
  }).then(async response => {
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || 'Could not load media');
    Object.assign(post, data, {_rich:true});
    return post;
  }).finally(() => { post._mediaRequest = null; });
  return post._mediaRequest;
}

function lb(i) {
  if (!store[i]) return;
  profileZoom?.reset();
  profilePlayingVideo = null;
  document.querySelectorAll('#lb video').forEach(v => v.pause());
  cur = i; const p = store[i];
  const version = ++profileMediaVersion;
  profileNotice('');
  updateProfileActions(p);
  document.getElementById('lbopen').href = p.url;
  document.getElementById('lbpos').textContent = `${i + 1} / ${store.length}`;
  document.querySelector('#lb .prev').disabled = i === 0;
  document.querySelector('#lb .next').disabled = i === store.length - 1 && !hasMorePosts;
  document.getElementById('lbstats').innerHTML =
    `<span>${icon('heart')} ${fmt(p.like_count)}</span><span>${icon('comment')} ${fmt(p.comment_count)}</span><span class="date">${p.taken_at ? postAge(p.taken_at) + ' · ' + fdate(p.taken_at) : ''}</span>`;
  document.getElementById('lbcap').innerHTML = mentions(p.caption || '');  // @usernames link to profiles
  const box = document.getElementById('lbcontent');
  if (p._rich) {
    box.innerHTML = richHtml(p);
    prefetchAround(i);
  } else {
    // Every post type, photos included, swaps the preview for full quality.
    box.innerHTML = previewHtml(p);
    ensureProfileMedia(p).then(() => {
      prefetchAround(i);
      if (!lbEl.hidden && cur === i && version === profileMediaVersion) {
        box.innerHTML = richHtml(p);
        syncProfilePlayback();
        updateProfileActions(p);
      }
    }).catch(error => {
      console.warn('Could not load media for ' + p.shortcode, error);
      if (cur === i && version === profileMediaVersion) {
        // A photo's preview is still worth seeing; videos and albums need the full post.
        if (!p.is_video && !p.is_carousel && p.image) box.innerHTML = `<img src="${esc(p.image)}" alt="">`;
        else box.innerHTML = '<button class="loading" type="button" onclick="lb(cur)">Could not load media · Retry</button>';
      }
    });
  }
  if (lbEl.hidden) {
    lbEl.hidden = false;
    document.body.style.overflow = 'hidden';
    // Back (phone button or browser) closes the viewer instead of leaving the page.
    history.pushState({viewer:true}, '');
  }
  syncProfilePlayback();
  if (store.length - i <= 4) loadMorePosts();
  if (profileCommentsOpen()) showProfileComments(p);
}
function hideLb() {
  profileZoom?.reset();
  ++profileMediaVersion;
  closeProfileComments();
  document.querySelectorAll('#lb video').forEach(v => v.pause());
  lbEl.hidden = true;
  syncProfilePlayback();
  document.body.style.overflow = '';
}
function closeLb() {
  if (lbEl.hidden) return;
  if (profileZoom?.isZoomed()) {profileZoom.reset(); return;}
  if (profileCommentsOpen()) { closeProfileComments(); return; }
  if (history.state?.viewer) history.back();  // popstate hides it
  else hideLb();
}
function handleProfileBack() {
  if (lbEl.hidden) return false;
  closeLb();
  return true;
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
  if (e.key === 'Escape') { e.preventDefault(); closeLb(); return; }
  if (e.key.toLowerCase() === 'c') { e.preventDefault(); if (!e.repeat) toggleProfileComments(); return; }
  if (e.key.toLowerCase() === 's') { e.preventDefault(); if (!e.repeat) saveProfilePost(); return; }
  if (e.key.toLowerCase() === 'd') { e.preventDefault(); if (!e.repeat) downloadProfilePost(document.getElementById('lb-download')); return; }
  if (['ArrowUp','k','ArrowDown','j'].includes(e.key)) {
    e.preventDefault(); if (!e.repeat) step(['ArrowUp','k'].includes(e.key) ? -1 : 1);
  }
});
lbEl.addEventListener('click', e => {
  if (e.target === lbEl || e.target.id === 'lbcontent') closeLb();  // tap outside the media
});
// Vertical swipes move between posts. Horizontal swipes stay inside carousels.
let swipe = null;
const profileZoom = window.installMediaZoom?.(document.getElementById('lbcontent'), {
  getMedia: target => target.closest?.('img,video'),
  onGesture: () => {swipe = null;},
});
function zoomProfilePost() {
  if (!profileZoom) return;
  if (profileZoom.isZoomed()) {profileZoom.reset(); return;}
  const carousel = document.querySelector('#lbcontent .slides');
  const media = carousel ? carousel.children[Math.round(carousel.scrollLeft / (carousel.clientWidth || 1))]
    : document.querySelector('#lbcontent img, #lbcontent video');
  if (media) profileZoom.zoom(media);
}
document.getElementById('lbcontent').addEventListener('scroll', event => {
  if (event.target.classList?.contains('slides')) { profileZoom?.reset(); syncProfilePlayback(); updateSlideDots(); }
}, true);
function scrollsProfileDetails(target) {
  const panel = target.closest('.lbmeta');
  return target.closest('.lbcap,#profile-comments,button,a,input') || (panel && panel.scrollHeight > panel.clientHeight + 1);
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


// Actions stay bound to the selected post, including while requests finish.
let profileNoticeTimer = null;
function profileNotice(message) {
  const status = document.getElementById('profile-action-status');
  if (!status) return;
  clearTimeout(profileNoticeTimer);
  status.textContent = message; status.hidden = !message;
  if (message) profileNoticeTimer = setTimeout(() => {status.hidden = true;}, 4000);
}
function profileActionMessage(post, message) {
  if (!lbEl.hidden && store[cur] === post) profileNotice(message);
}
function updateProfileActions(post = store[cur]) {
  if (!post) return;
  const button = (id, symbol, label, pressed) => {
    const element = document.getElementById(id);
    if (!element) return null;
    element.innerHTML = icon(symbol);
    element.title = label; element.setAttribute('aria-label', label);
    if (pressed !== undefined) element.setAttribute('aria-pressed', String(pressed));
    return element;
  };
  button('lb-like', 'heart', post.feedback === 'like' ? 'Remove local like' : 'Like in ig', post.feedback === 'like');
  button('lb-comments-toggle', 'comment', 'Comments (C)');
  button('lb-share', 'share', 'Share post');
  const save = button('lb-save', 'bookmark', post.saved ? 'Unsave reel (S)' : 'Save reel (S)', Boolean(post.saved));
  if (save) {save.hidden = !post.is_video || post.is_carousel; save.disabled = Boolean(post._saveBusy);}
  const download = button('lb-download', 'download', post.is_carousel ? 'Download current item (D)' : 'Download (D)');
  if (download) download.disabled = Boolean(post._downloadBusy || (post.is_carousel && !post._rich));
}
async function likeProfilePost() {
  const post = store[cur];
  if (!post || post._feedbackBusy) return;
  post._feedbackBusy = true;
  try {
    await ensureProfileMedia(post);
    const response = await fetch('/feedback', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({code:post.shortcode, action:post.feedback === 'like' ? 'clear' : 'like'})});
    const data = await response.json();
    if (!response.ok || data.error) throw new Error('Could not save your like');
    post.feedback = data.feedback;
    if (store[cur] === post) updateProfileActions(post);
    profileActionMessage(post, post.feedback === 'like' ? 'Liked in ig' : 'Like removed');
  } catch (error) { profileActionMessage(post, error.message || 'Could not save your like'); }
  finally { post._feedbackBusy = false; }
}
async function saveProfilePost() {
  const post = store[cur];
  if (!post || !post.is_video || post.is_carousel || post._saveBusy) return;
  post._saveBusy = true; updateProfileActions(post);
  try {
    await ensureProfileMedia(post);
    const response = await fetch('/saved-reels', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({code:post.shortcode, saved:!post.saved})});
    const data = await response.json();
    if (!response.ok || data.error) throw new Error('Could not update saved reels');
    post.saved = data.saved === true;
    profileActionMessage(post, post.saved ? 'Reel saved' : 'Reel unsaved');
  } catch (error) { profileActionMessage(post, error.message || 'Could not update saved reels'); }
  finally { post._saveBusy = false; if (store[cur] === post) updateProfileActions(post); }
}
async function shareProfilePost() {
  const post = store[cur];
  if (!post || post._shareBusy) return;
  const isReel = post.is_video && !post.is_carousel;
  const url = 'https://www.instagram.com/' + (isReel ? 'reel/' : 'p/') + encodeURIComponent(post.shortcode) + '/';
  if (boot.android) {window.location.href = '/share/' + (isReel ? '' : 'post/') + encodeURIComponent(post.shortcode); return;}
  post._shareBusy = true;
  try {
    if (navigator.share) {
      try {await navigator.share({url}); return;}
      catch (error) {if (error.name === 'AbortError') return;}
    }
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(url);
    } catch (_) {
      const input = document.createElement('textarea');
      input.value = url; input.readOnly = true; input.style.cssText = 'position:fixed;left:-9999px';
      document.body.append(input);
      try {input.select(); if (!document.execCommand('copy')) throw new Error('Could not copy link');}
      finally {input.remove();}
    }
    profileActionMessage(post, 'Post link copied');
  } catch (error) {profileActionMessage(post, error.message || 'Could not share post');}
  finally {post._shareBusy = false;}
}
async function downloadProfilePost() {
  const post = store[cur];
  if (!post || post._downloadBusy) return;
  if (post.is_carousel && !post._rich) {profileNotice('Wait for the post to load, then download'); return;}
  const carousel = document.querySelector('#lbcontent .slides');
  const slide = post.is_carousel ? Math.max(0, Math.round((carousel?.scrollLeft || 0) / (carousel?.clientWidth || 1))) : null;
  const item = slide === null ? post : post.slides?.[slide];
  const image = item?.image || '';
  const format = item?.video || (slide === null && post.is_video) ? null :
    (/\.(png|webp|avif)(?:[?#]|$)/i.exec(image)?.[1]?.toLowerCase() || 'jpg');
  const query = new URLSearchParams();
  if (slide !== null) query.set('slide', String(slide));
  if (format) query.set('format', format);
  const path = '/download/' + encodeURIComponent(post.shortcode) + (query.size ? '?' + query : '');
  if (boot.android) {window.location.href = path; return;}
  post._downloadBusy = true; updateProfileActions(post);
  try {
    const response = await fetch(path);
    if (!response.ok) throw new Error('Could not download · try again');
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    const extension = blob.type.startsWith('video/') ? 'mp4' : blob.type.includes('png') ? 'png' : blob.type.includes('webp') ? 'webp' : blob.type.includes('avif') ? 'avif' : 'jpg';
    link.href = url; link.download = post.shortcode + (slide === null ? '' : '-' + (slide + 1)) + '.' + extension;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    profileActionMessage(post, 'Download ready');
  } catch (error) {profileActionMessage(post, error.message || 'Could not download · try again');}
  finally {post._downloadBusy = false; if (store[cur] === post) updateProfileActions(post);}
}
function profileCommentsOpen() {return document.getElementById('profile-comments')?.hidden === false;}
function closeProfileComments() {
  const panel = document.getElementById('profile-comments');
  if (panel) panel.hidden = true;
  lbEl.classList.remove('comments-open');
  document.getElementById('lb-comments-toggle')?.setAttribute('aria-expanded', 'false');
}
function toggleProfileComments() {
  if (profileCommentsOpen()) closeProfileComments();
  else if (store[cur]) showProfileComments(store[cur]);
}
function showProfileComments(post) {
  const panel = document.getElementById('profile-comments');
  if (!panel) return;
  const changed = panel.dataset.code !== post.shortcode;
  panel.hidden = false; panel.dataset.code = post.shortcode;
  lbEl.classList.add('comments-open');
  document.getElementById('lb-comments-toggle')?.setAttribute('aria-expanded', 'true');
  if (changed) document.getElementById('profile-comments-body').scrollTop = 0;
  renderProfileComments(post);
  if (!post._profileComments && !post._profileCommentsBusy) loadProfileComments(post);
}
function profileCommentsVisible(post) {
  return !lbEl.hidden && profileCommentsOpen() && document.getElementById('profile-comments').dataset.code === post.shortcode;
}
function renderProfileComments(post) {
  if (!profileCommentsVisible(post)) return;
  const body = document.getElementById('profile-comments-body'), top = body.scrollTop;
  body.innerHTML = (post._profileComments || []).map(comment => `<article class="comment-row">
    <a href="/u/${encodeURIComponent(comment.username)}">@${esc(comment.username)}</a>
    ${comment.is_owner ? '<span class="comment-creator">Creator</span>' : ''}
    <p>${mentions(comment.text)}</p><small>${fmt(comment.like_count)} likes</small></article>`).join('');
  if (post._profileCommentsBusy) body.insertAdjacentHTML('beforeend', '<p class="comments-status" role="status">Loading…</p>');
  else if (post._profileCommentsError || post._profileCommentCursor) {
    const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'comments-more';
    retry.textContent = post._profileCommentsError ? 'Couldn’t load comments · Retry' : 'More comments';
    retry.onclick = () => loadProfileComments(post); body.append(retry);
  } else if (!post._profileComments?.length) {
    body.textContent = post.comment_count > 0 ? 'Instagram returned no public comments.' : 'No comments yet.';
  }
  body.scrollTop = top;
}
async function loadProfileComments(post = store[cur]) {
  if (!post || post._profileCommentsBusy || (post._profileComments && !post._profileCommentCursor && !post._profileCommentsError)) return;
  const after = post._profileCommentCursor || null;
  post._profileCommentsBusy = true; post._profileCommentsError = false;
  renderProfileComments(post);
  let added = 0;
  try {
    const response = await fetch('/comments/' + encodeURIComponent(post.shortcode) + (after ? '?after=' + encodeURIComponent(after) : ''),
      {method:'POST', signal:AbortSignal.timeout(45000)});
    const data = await response.json();
    if (!response.ok || data.error) throw new Error('Could not load comments');
    post._profileComments ||= []; post._profileCommentKeys ||= new Set(); post._profileCommentCursors ||= new Set();
    if (after) post._profileCommentCursors.add(after);
    for (const comment of data.comments || []) {
      const key = comment.id ? 'id:' + comment.id : JSON.stringify([comment.username, comment.text, comment.created_at]);
      if (post._profileCommentKeys.has(key)) continue;
      post._profileCommentKeys.add(key); post._profileComments.push(comment); added++;
    }
    const next = data.next_cursor || null;
    post._profileCommentCursor = post._profileCommentCursors.has(next) ? null : next;
    post._profileCommentAutoPaused = added === 0;
  } catch (_) {post._profileCommentsError = true;}
  finally {post._profileCommentsBusy = false; renderProfileComments(post);}
  const body = document.getElementById('profile-comments-body');
  if (added && post._profileCommentCursor && profileCommentsVisible(post) && body.scrollHeight <= body.clientHeight) {
    setTimeout(() => {if (profileCommentsVisible(post)) loadProfileComments(post);}, 0);
  }
}
document.getElementById('profile-comments-body')?.addEventListener('scroll', event => {
  const body = event.target, post = store[cur];
  if (!post || !profileCommentsVisible(post) || post._profileCommentsError || post._profileCommentAutoPaused || !post._profileCommentCursor) return;
  if (body.scrollHeight - body.scrollTop - body.clientHeight < 140) loadProfileComments(post);
});
