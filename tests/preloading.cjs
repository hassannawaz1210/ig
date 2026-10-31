const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2], 'utf8');

// A fake reels page: just enough DOM for reels.js, with every request recorded.
// `respond(url, options)` returns the fake fetch result.
function createPage(respond, {chips = []} = {}) {
  const elements = new Map();
  const calls = [];
  const timers = [];
  function element(id) {
    if (id === 'feed-picker' || id === 'shortcut-help') return null;
    if (!elements.has(id)) elements.set(id, {
      id,
      textContent: id === 'boot' ? JSON.stringify({feed_accounts: [], blocked: [], selected_feeds: null}) : '', style: {}, dataset: {},
      getBoundingClientRect() {return {top:0,bottom:800,height:800};},
      scrollIntoView() {},
      setAttribute(name, value) { this[name] = value; },
      remove() { this.removed = true; },
      querySelector: () => null, querySelectorAll: () => [],
      insertAdjacentHTML() {
        const video = {src: '', load() {this.loads = (this.loads || 0) + 1;},
          play() {this.paused = false; this.plays = (this.plays || 0) + 1; return Promise.resolve();},
          pause() {this.paused = true;}, removeAttribute() {this.src = null;}, remove() {this.removed = true;},
          getAttribute() {return this.src;}};
        this.querySelector = () => video;
      },
    });
    return elements.get(id);
  }
  const ctx = vm.createContext({
    document: {getElementById: element, addEventListener() {},
      querySelectorAll: selector => selector === '.feed-choice' ? chips : []},
    IntersectionObserver: class {constructor(callback) {ctx.observerCallback = callback;} observe() {} unobserve() {}}, AbortSignal,
    performance: {now: () => 2000},
    sessionStorage: {data: {}, getItem(key) {return this.data[key] || null;},
      setItem(key, value) {this.data[key] = value;}},
    // Timers never run by themselves; tests run them explicitly.
    setTimeout(fn, ms) {timers.push({run: fn, ms}); return timers.length;},
    clearTimeout(id) { if (id) timers[id - 1].cancelled = true; },
    console: {error() {}, warn() {}, log: console.log},
    window: {location: {reload() { ctx.didReload = true; }}, addEventListener() {}},
    fetch: async (url, options) => {
      calls.push({url, options});
      return respond(url, options);
    },
  });
  vm.runInContext(source, ctx);
  return {ctx, element, calls, timers};
}

let response = {posts: [], has_more: true};
const {ctx, element, calls, timers} = createPage(async url =>
  ({ok: true, json: async () => url.endsWith('/media') ? {video: 'https://example.test/video.mp4'} : response}));
(async () => {
  vm.runInContext("posts.push({shortcode:'first'}, {shortcode:'second'}); slides=2; current=0;", ctx);
  await vm.runInContext('Promise.all([prepare(1), prepare(1)])', ctx);
  assert.equal(calls.filter(c => c.url.endsWith('/media')).length, 1, 'deduplicate in-flight media requests');
  const video = element('s1').querySelector('video');
  assert.equal(video.loads, 1, 'download video before it becomes visible');
  assert.equal(video.plays, undefined, 'preloaded video stays paused');
  await vm.runInContext('current=1; prepare(1)', ctx);
  assert.equal(video.loads, 1, 'activation does not restart the download');
  assert.equal(video.plays, 1);
  element('s0').id = 's0';
  element('s0').getBoundingClientRect = () => ({top:-800,bottom:0,height:800});
  ctx.observerCallback([{target:element('s0'),isIntersecting:true,intersectionRatio:1}]);
  assert.equal(vm.runInContext('current',ctx),1,'stale observer entries cannot undo a keyboard jump');
  await vm.runInContext('more()', ctx);
  assert.equal(vm.runInContext('exhausted', ctx), false, 'temporary empty response is not exhaustion');
  assert.equal(timers.length, 1, 'retry without requiring another scroll');
  response = {posts: [], has_more: false};
  await vm.runInContext('more()', ctx);
  assert.equal(vm.runInContext('exhausted', ctx), true, 'honor actual exhaustion');
  vm.runInContext("posts[1].owner='creator';", ctx);
  vm.runInContext('togglePlayback()', ctx);
  assert.equal(video.paused, true, 'Space pauses current video');
  vm.runInContext('togglePlayback()', ctx);
  assert.equal(video.paused, false, 'Space resumes current video');
  vm.runInContext('toggleMute()', ctx);
  assert.equal(vm.runInContext('muted', ctx), false, 'mute toggles');
  assert.equal(element('mute')['aria-label'], 'Mute (M)');
  assert.ok(element('mute').innerHTML.includes('<svg'));
  assert.ok(!element('mute').innerHTML.includes('sound on'));
  const accountButtons = [0,1].map(i => {
    const slide = element('s'+i), original = slide.querySelector;
    const button = {style:{}, innerHTML:''};
    slide.querySelector = selector => selector === '.account-action' ? button : original(selector);
    return button;
  });
  vm.runInContext("posts[0].owner='creator'; accountChanged('creator', true);",ctx);
  assert.ok(accountButtons.every(button=>button.innerHTML.includes('in feed')), 'all reels update after save');
  response = {posts:[],has_more:false,feed_accounts:[]};
  await vm.runInContext('exhausted=false; more()',ctx);
  assert.ok(accountButtons.every(button=>button.innerHTML.includes('in feed')), 'old batches cannot undo a save');
  const likeButton = {setAttribute(name,value){this[name]=value;}};
  const originalQuery = element('s1').querySelector;
  element('s1').querySelector = selector => selector === '.reel-like' ? likeButton : originalQuery(selector);
  response = {feedback:'like'};
  const savedBefore = vm.runInContext('feeded.size',ctx);
  await vm.runInContext("rateReel(1, 'like')",ctx);
  assert.equal(likeButton['aria-pressed'],'true');
  assert.equal(vm.runInContext('posts[1].feedback',ctx),'like');
  assert.equal(vm.runInContext('feeded.size',ctx),savedBefore,'liking a reel must not save the account');
  response = {feedback:null};
  await vm.runInContext("rateReel(1, 'clear')",ctx);
  assert.equal(likeButton['aria-pressed'],'false');
  const saveButton = {label:{textContent:'save'}, setAttribute(name,value){this[name]=value;}, querySelector(){return this.label;}};
  const downloadButton = {disabled:false};
  const queryBeforeSave = element('s1').querySelector;
  element('s1').querySelector = selector => selector === '.reel-save' ? saveButton
    : selector === '.download' ? downloadButton : queryBeforeSave(selector);
  response = {saved:true};
  await vm.runInContext('toggleSavedReel(1)',ctx);
  assert.equal(calls.at(-1).url,'/saved-reels');
  assert.deepEqual(JSON.parse(calls.at(-1).options.body),{code:'second',saved:true});
  assert.equal(vm.runInContext('posts[1].saved',ctx),true);
  assert.equal(saveButton['aria-pressed'],'true');
  assert.equal(saveButton.label.textContent,'saved');
  assert.equal(vm.runInContext('posts[1].feedback',ctx),null,'saving a reel is not a like');
  response = {saved:false};
  await vm.runInContext('toggleSavedReel(1)',ctx);
  assert.equal(JSON.parse(calls.at(-1).options.body).saved,false);
  assert.equal(saveButton['aria-pressed'],'false');
  const workingFetch = ctx.fetch;
  ctx.fetch = async () => ({ok:false, status:500});
  await vm.runInContext('toggleSavedReel(1)',ctx);
  assert.equal(vm.runInContext('posts[1].saved',ctx),false,'a failed save keeps the old state');
  assert.equal(element('action-status').textContent,'Could not update Saved reels · try again');
  ctx.fetch = workingFetch;
  vm.runInContext('var realDownload = downloadReel; var downloads = []; downloadReel = i => downloads.push(i);',ctx);
  downloadButton.disabled = true;
  vm.runInContext('current=1; downloadCurrent()',ctx);
  assert.equal(vm.runInContext('downloads.length',ctx),0,'D ignores a download that is already running');
  downloadButton.disabled = false;
  vm.runInContext('downloadCurrent()',ctx);
  assert.deepEqual(Array.from(vm.runInContext('downloads',ctx)),[1],'D downloads the current reel');
  vm.runInContext('downloadReel = realDownload',ctx);
  vm.runInContext('posts[1]._viewStarted=1000; recordQuickSkip(1); recordQuickSkip(1)',ctx);
  assert.equal(calls.filter(c=>c.url==='/feedback' && JSON.parse(c.options.body).action==='skip').length,1,'record each quick skip only once');
  vm.runInContext(`
    let keyCalls = [];
    togglePlayback = () => keyCalls.push('play');
    toggleMute = () => keyCalls.push('mute');
    addCurrent = () => keyCalls.push('add');
    openProfile = () => keyCalls.push('profile');
    removeCurrent = () => keyCalls.push('remove');
    const realBlock = blockCurrent;
    blockCurrent = session => keyCalls.push(session ? 'session' : 'permanent');
  `, ctx);
  for (const [key, shiftKey] of [[' ',false],['m',false],['a',false],['p',false],['b',false],['B',true]]) {
    ctx.event = {key, shiftKey, target: {closest: () => null}, preventDefault() {this.prevented = true;}};
    vm.runInContext('handleReelKey(event)', ctx);
    assert.equal(ctx.event.prevented, true);
    vm.runInContext('releaseReelKey(event)', ctx);
  }
  assert.deepEqual(Array.from(vm.runInContext('keyCalls', ctx)), ['play','mute','add','profile','permanent','session']);
  ctx.event = {key:'m', code:'KeyM', target:{closest:()=>null}, preventDefault(){this.prevented=true;}};
  vm.runInContext('handleReelKey(event); handleReelKey(event)',ctx);
  assert.equal(vm.runInContext('keyCalls.length',ctx),7,'one action until physical key release');
  ctx.event.repeat = true; ctx.event.prevented = false;
  vm.runInContext('handleReelKey(event)',ctx);
  assert.equal(ctx.event.prevented,true,'repeated key must not trigger native browser scrolling');
  vm.runInContext('releaseReelKey(event); keyCalls.pop()',ctx);
  ctx.event = {key:'b', target:{closest:()=>({})}, preventDefault(){throw Error('typing intercepted');}};
  vm.runInContext('handleReelKey(event)',ctx);
  assert.equal(vm.runInContext('keyCalls.length',ctx),6);
  ctx.event = {key:'r', code:'KeyR', target:{closest:()=>null}, preventDefault(){}};
  vm.runInContext('handleReelKey(event); releaseReelKey(event)',ctx);
  assert.equal(vm.runInContext('keyCalls.at(-1)',ctx),'remove');
  ctx.event = {key:'ArrowUp',code:'ArrowUp',target:{closest:selector=>selector==='button, a'?{}:null},preventDefault(){this.prevented=true;}};
  vm.runInContext('current=1; handleReelKey(event); handleReelKey(event); releaseReelKey(event)',ctx);
  assert.equal(ctx.event.prevented,true);
  assert.equal(vm.runInContext('current',ctx),0,'focused controls still navigate exactly one reel');
  vm.runInContext("toggleSavedReel = i => keyCalls.push('save:' + i); downloadCurrent = () => keyCalls.push('download');",ctx);
  for (const [key, code] of [['s','KeyS'],['D','KeyD']]) {
    ctx.event = {key, code, shiftKey:key === 'D', target:{closest:()=>null}, preventDefault(){this.prevented=true;}};
    vm.runInContext('handleReelKey(event); releaseReelKey(event)',ctx);
    assert.equal(ctx.event.prevented,true);
  }
  assert.deepEqual(Array.from(vm.runInContext('keyCalls.slice(-2)',ctx)),['save:0','download'],'S saves and D downloads the current reel');
  vm.runInContext('current=1',ctx);
  vm.runInContext(`
    blockCurrent = realBlock;
    more = () => {};
    maybeMore = () => {};
    showSlide = i => {current = i;};
  `,ctx);
  await vm.runInContext('blockCurrent(true)',ctx);
  assert.deepEqual(JSON.parse(ctx.sessionStorage.data['ig-session-blocked']), ['creator']);
  assert.equal(element('s1').hidden,true,'session block hides loaded clip');
  assert.equal(vm.runInContext("blockedAccounts.has('creator')",ctx),false,'session block is not permanent');
  assert.equal(calls.filter(c=>c.url === '/block').length,0,'session block does not write permanent list');
  vm.runInContext("current=0; posts[0].owner='another';",ctx);
  response = {blocked:['another']};
  await vm.runInContext('blockCurrent(false)',ctx);
  assert.equal(vm.runInContext("blockedAccounts.has('another')",ctx),true);
  assert.equal(element('s0').hidden,true,'permanent block hides loaded clip');
  assert.equal(calls.filter(c=>c.url === '/block').length,1);
  console.log('Reel preloading, keyboard shortcuts and blocks passed');
  await feedSelectionTests();
  await loadingStateTests();
  await earlyRefillTests();
  console.log('Feed selection and loading state passed');
})().catch(error => {console.error(error); process.exitCode = 1;});

// Requests stay open until the test answers them, so races can be staged.
function createControlledPage(options) {
  const open = [];
  const page = createPage((url, request) => {
    if (url.endsWith('/media')) return {ok: true, json: async () => ({video: 'https://example.test/' + JSON.parse(request.body).code + '.mp4'})};
    return new Promise((resolve, reject) => open.push({url, body: JSON.parse(request.body || '{}'), resolve, reject}));
  }, options);
  page.next = url => {
    const index = open.findIndex(request => request.url === url);
    assert.ok(index >= 0, 'expected an open request to ' + url);
    return open.splice(index, 1)[0];
  };
  page.reply = (request, body, status = 200) => request.resolve({ok: status < 400, status,
    json: async () => body, text: async () => JSON.stringify(body)});
  page.open = open;
  return page;
}
const settle = () => new Promise(resolve => setImmediate(resolve));
async function settleAll() { for (let i = 0; i < 5; i++) await settle(); }
const reels = (...codes) => codes.map(code => ({shortcode: code, owner: 'owner_' + code, image: ''}));
function runTimer(page, ms) {
  const timer = pendingTimers(page, ms).at(-1);
  assert.ok(timer, `expected a pending ${ms} ms timer`);
  timer.ran = true;
  timer.run();
}
function pendingTimers(page, ms) {
  return page.timers.filter(timer => timer.ms === ms && !timer.cancelled && !timer.ran);
}

async function feedSelectionTests() {
  const chips = ['all', 'gym', 'memes'].map(name => ({dataset: name === 'all' ? {all: 'true'} : {feed: name},
    setAttribute(key, value) { this[key] = value; }, addEventListener(type, fn) { this.click = fn; }}));
  const page = createControlledPage({chips});
  const {ctx, element, calls} = page;
  const run = code => vm.runInContext(code, ctx);
  const delay = run('SELECTION_DELAY_MS');

  // Selection rules.
  assert.deepEqual(Array.from(run("nextFeedSelection(null, 'gym')")), ['gym'], 'a feed while All is active selects just that feed');
  assert.deepEqual(Array.from(run("nextFeedSelection(['gym'], 'memes')")), ['gym', 'memes'], 'feeds combine');
  assert.deepEqual(Array.from(run("nextFeedSelection(['gym', 'memes'], 'gym')")), ['memes'], 'a selected feed toggles off');
  assert.equal(run("nextFeedSelection(['gym'], 'gym')"), null, 'removing the last feed returns to All');
  assert.equal(run("nextFeedSelection(['gym'], null)"), null, 'All clears the selection');
  assert.deepEqual(Array.from(run("nextFeedSelection([], 'gym')")), ['gym'], 'an emptied saved selection can pick a feed');
  assert.equal(chips[0]['aria-pressed'], 'true', 'All starts selected');

  // First batch: three reels, the first one playing.
  page.reply(page.next('/reels/more'), {posts: reels('a', 'b', 'c'), has_more: true});
  await settleAll();
  assert.equal(run('current'), 0);
  assert.equal(element('feed-status').hidden, true, 'loading notice hides once a reel shows');
  const queuedVideo = element('s1').querySelector('video');
  assert.equal(queuedVideo.loads, 1, 'next reel was preloaded');
  const oldTopUp = page.next('/reels/more');  // asked for under the old selection; answered later

  // Two quick taps become one saved change, applied without Apply.
  run("muted = false");
  chips[1].click();
  chips[2].click();
  assert.equal(chips[0]['aria-pressed'], 'false');
  assert.equal(chips[1]['aria-pressed'], 'true', 'tapped chip shows as selected right away');
  assert.equal(chips[2]['aria-pressed'], 'true');
  assert.equal(calls.filter(call => call.url === '/preferences').length, 0, 'saving waits for the short delay');
  runTimer(page, delay);
  assert.equal(pendingTimers(page, delay).length, 0,'earlier tap timer was cancelled');
  assert.equal(calls.filter(call => call.url === '/preferences').length, 1, 'one save for quick taps');
  assert.equal(element('feed-busy').hidden, false, 'busy while switching');
  assert.equal(element('feed-choices')['aria-busy'], 'true');
  const save = page.next('/preferences');
  assert.deepEqual(save.body, {selected_feeds: ['gym', 'memes']});
  page.reply(save, {selected_feeds: ['gym', 'memes']});
  await settleAll();

  assert.notEqual(ctx.didReload, true, 'changing feeds does not reload the page');
  assert.equal(run('current'), 0, 'current reel stays in place');
  assert.equal(element('s0').removed, undefined, 'current reel is kept');
  assert.equal(element('s1').removed, true, 'reels queued for the old selection are removed');
  assert.equal(element('s2').removed, true);
  assert.equal(queuedVideo.src, null, 'video of a removed reel is released');
  assert.equal(run('muted'), false, 'mute state is kept');
  assert.equal(element('feed-status').hidden, false, 'notice shows while nothing is queued');
  assert.equal(element('feed-status').dataset.mode, 'compact', 'small notice while a reel is playing');
  assert.equal(element('feed-status-text').textContent, 'Loading reels…');

  // A new batch is requested at once, even though the old request is still open.
  const fresh = page.next('/reels/more');
  // The old request answers late; its reels must not appear.
  page.reply(oldTopUp, {posts: reels('old1'), has_more: true});
  await settleAll();
  assert.ok(!run('posts').some(post => post.shortcode === 'old1'), 'a batch for the old selection is discarded');
  assert.equal(page.open.length, 0, 'a discarded batch does not start another request');
  assert.equal(run('switchingFeeds'), true, 'still switching until the new batch arrives');
  assert.equal(run('fetching'), true, 'the new request is still the one in progress');
  return finishSwitch(page, fresh, chips);
}

async function finishSwitch(page, fresh, chips) {
  const {ctx, element, calls} = page;
  const run = code => vm.runInContext(code, ctx);
  page.reply(fresh, {posts: reels('d', 'e'), has_more: true});
  await settleAll();
  const codes = Array.from(run('visibleIndices().map(i => posts[i].shortcode)'));
  assert.deepEqual(codes, ['a', 'd', 'e'], 'new batch follows the current reel');
  assert.equal(run('switchingFeeds'), false);
  assert.equal(element('feed-busy').hidden, true, 'busy state ends with the new batch');
  assert.equal(element('feed-status').hidden, true);
  assert.deepEqual(calls.filter(call => call.url === '/reels/more').at(-1).options.body,
    JSON.stringify({session_blocked: []}), 'batch requests still carry session blocks');

  // A failed save reverts the chips and keeps the queued reels.
  chips[1].click();
  assert.equal(chips[1]['aria-pressed'], 'false');
  runTimer(page, run('SELECTION_DELAY_MS'));
  page.reply(page.next('/preferences'), 'could not save preference', 500);
  await settleAll();
  assert.equal(chips[1]['aria-pressed'], 'true', 'chips go back to the saved selection');
  assert.deepEqual(Array.from(run('chosenFeeds')), ['gym', 'memes']);
  assert.equal(element('preference-status').textContent, 'Could not change feeds · try again');
  assert.equal(element('preference-status').dataset.tone, 'error');
  assert.equal(element('s3').removed, undefined, 'queued reels stay after a failed save');
  assert.equal(run('switchingFeeds'), false);
}

async function loadingStateTests() {
  const page = createControlledPage();
  const {ctx, element} = page;
  const run = code => vm.runInContext(code, ctx);
  const status = element('feed-status');
  assert.equal(status.hidden, false, 'loading notice shows before the first batch');
  assert.equal(status.dataset.mode, 'overlay', 'centered while no reel is showing');
  assert.equal(status.dataset.state, 'loading');
  assert.equal(element('feed-status-text').textContent, 'Loading reels…');
  assert.equal(element('feed-retry').hidden, true);

  page.next('/reels/more').reject(new TypeError('Failed to fetch'));
  await settleAll();
  assert.equal(status.dataset.state, 'loading', 'one failure keeps the loading notice while retrying');
  runTimer(page, run('RETRY_DELAY_MS'));
  page.next('/reels/more').reject(new TypeError('Failed to fetch'));
  await settleAll();
  assert.equal(status.hidden, false);
  assert.equal(status.dataset.state, 'error', 'repeated failures show an error');
  assert.match(element('feed-status-text').textContent, /not reachable/);
  assert.equal(element('feed-retry').hidden, false, 'error offers a retry button');
  assert.ok(pendingTimers(page, run('RETRY_DELAY_MS')).length,'still retries automatically');

  run('retryLoading()');
  assert.equal(status.dataset.state, 'loading', 'retry shows loading again');
  const request = page.next('/reels/more');
  page.reply(request, {error: 'Instagram is rate limiting requests'}, 429);
  await settleAll();
  runTimer(page, run('RETRY_DELAY_MS'));
  page.reply(page.next('/reels/more'), {error: 'Instagram is rate limiting requests'}, 429);
  await settleAll();
  assert.match(element('feed-status-text').textContent, /rate limiting/, 'server error message is shown');
  runTimer(page, run('RETRY_DELAY_MS'));
  page.reply(page.next('/reels/more'), {posts: reels('x'), has_more: true});
  await settleAll();
  assert.equal(status.dataset.mode, 'compact', 'one reel with no buffer shows a compact refill notice');
  assert.equal(run('current'), 0);
  assert.equal(run('loadFailures'), 0);
}


async function earlyRefillTests() {
  const page=createControlledPage();
  page.reply(page.next('/reels/more'), {posts:reels(...Array.from({length:14},(_,i)=>'warm'+i)),has_more:true});
  await settleAll();
  assert.equal(vm.runInContext('current',page.ctx),0);
  assert.ok(page.open.some(r=>r.url==='/reels/more'),'refill begins while thirteen reels remain, not near the end');
  const requestCount=page.calls.filter(r=>r.url==='/reels/more').length;
  vm.runInContext('maybeMore(); maybeMore()',page.ctx);
  assert.equal(page.calls.filter(r=>r.url==='/reels/more').length,requestCount,'keep one refill request in flight');
  assert.ok(page.calls.some(r=>r.url.endsWith('/media')&&JSON.parse(r.options.body).code==='warm5'), 'fifth upcoming video is prepared before navigation');
}
