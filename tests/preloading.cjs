const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2], 'utf8');

// A fake reels page: just enough DOM for reels.js, with every request recorded.
// `respond(url, options)` returns the fake fetch result.
function createPage(respond, {chips = [], boot = {}} = {}) {
  const elements = new Map();
  const calls = [];
  const timers = [];
  function element(id) {
    if (id === 'feed-picker' || id === 'shortcut-help') return null;
    if (!elements.has(id)) elements.set(id, {
      id,
      textContent: id === 'boot' ? JSON.stringify({feed_accounts: [], blocked: [], selected_feeds: null, scroll_animation: 'snappy', ...boot}) : '', style: {}, dataset: {},
      getBoundingClientRect() {return {top:0,bottom:800,height:800};},
      scrollIntoView() {},
      matches() {return false;},
      listeners: {}, addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); },
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
      querySelector: selector => selector === '.comments-heading' ? element('comments-heading') : null,
      querySelectorAll: selector => selector === '.feed-choice' ? chips : []},
    IntersectionObserver: class {constructor(callback) {ctx.observerCallback = callback;} observe() {} unobserve() {}}, AbortSignal,
    performance: {now: () => 2000},
    sessionStorage: {data: {}, getItem(key) {return this.data[key] || null;},
      setItem(key, value) {this.data[key] = value;}},
    // Timers never run by themselves; tests run them explicitly.
    setInterval() {return 0;},
    setTimeout(fn, ms) {timers.push({run: fn, ms}); return timers.length;},
    clearTimeout(id) { if (id) timers[id - 1].cancelled = true; },
    console: {error() {}, warn() {}, log: console.log},
    window: {location: {reload() { ctx.didReload = true; }}, listeners:{},
      addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }},
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
  // A following import updates the page once, even for thousands of accounts.
  const bulk = createPage(async url => ({ok:true,json:async()=>url === '/settings/data' ?
    {feeds:{},sound:true,auto_scroll:true,scroll_animation:'smooth'} : {posts:[],has_more:false}}));
  await new Promise(setImmediate);
  vm.runInContext(`
    let bulkRenders=0, bulkRefills=0;
    syncAccountButtons=()=>{bulkRenders++}; hideFilteredReels=()=>{};
    maybeMore=()=>{bulkRefills++}; current=0;
    accountsChanged(Array.from({length:7500}, (_,i)=>'imported'+i), true);
  `, bulk.ctx);
  assert.equal(vm.runInContext('feeded.size',bulk.ctx),7500);
  assert.equal(vm.runInContext('bulkRenders',bulk.ctx),1);
  assert.equal(vm.runInContext('bulkRefills',bulk.ctx),1);
  assert.equal(vm.runInContext('new Set([...accountUpdates.values()].map(v=>v.version)).size',bulk.ctx),1);
  vm.runInContext(`
    localStorage={getItem:()=> 'snappy'}; replaceExpiredQueue=()=>{};
    window.listeners.pageshow.forEach(listener=>listener({persisted:true}));
  `,bulk.ctx);
  await new Promise(setImmediate);
  assert.equal(vm.runInContext('scrollAnimation',bulk.ctx),'smooth','Back from Settings applies saved motion preference');
  assert.equal(vm.runInContext('muted',bulk.ctx),false,'Back restores sound from SQLite, not stale browser data');
  assert.equal(vm.runInContext('autoScroll',bulk.ctx),true);
  // R must also remove discovery creators; a later server block must override
  // an older local add, while in-flight responses cannot undo newer actions.
  let finishBatch;
  let deferBatch = false;
  let failRemoval = false;
  const removal = createPage(async url => {
    if (url === '/remove') return {ok:!failRemoval};
    if (deferBatch && url === '/reels/more') return new Promise(resolve => {finishBatch=resolve;});
    return {ok:true,json:async()=>({posts:[],has_more:false,feed_accounts:[],blocked:['trial']})};
  });
  await new Promise(setImmediate);
  vm.runInContext(`
    posts.push({shortcode:'trial1',owner:'trial'}, {shortcode:'trial2',owner:'trial'},
               {shortcode:'other1',owner:'other'}); current=0;
    blockedAccounts.clear(); maybeMore=()=>{}; showSlide=i=>{current=i;};
    notifyAccountChange=(u,saved)=>accountChanged(u,saved);
  `,removal.ctx);
  await vm.runInContext('removeCurrent()',removal.ctx);
  assert.equal(removal.calls.filter(c=>c.url==='/remove').length,1,'R sends removal for an unsaved discovery creator');
  assert.equal(vm.runInContext('blockedAccounts.has("trial")',removal.ctx),true);
  assert.equal(removal.element('s0').style.display,'none');
  assert.equal(removal.element('s1').style.display,'none','all queued reels from the removed creator disappear');
  assert.equal(vm.runInContext('current',removal.ctx),2);
  assert.match(removal.element('action-status').textContent,/removed and blocked/);
  vm.runInContext('accountChanged("trial",true); exhausted=false',removal.ctx);
  await vm.runInContext('more()',removal.ctx);
  assert.equal(vm.runInContext('blockedAccounts.has("trial")',removal.ctx),true,'server removal wins over an earlier local add');
  assert.equal(vm.runInContext('feeded.has("trial")',removal.ctx),false);
  deferBatch=true;
  vm.runInContext('exhausted=false',removal.ctx);
  const pending=vm.runInContext('more()',removal.ctx);
  vm.runInContext('accountChanged("other",false)',removal.ctx);
  finishBatch({ok:true,json:async()=>({posts:[],has_more:false,feed_accounts:['other'],blocked:[]})});
  await pending;
  assert.equal(vm.runInContext('blockedAccounts.has("other")',removal.ctx),true,'an older response cannot undo a removal made in flight');
  assert.equal(vm.runInContext('feeded.has("other")',removal.ctx),false);
  failRemoval=true;
  vm.runInContext('posts.push({shortcode:"failure",owner:"failure"});current=3',removal.ctx);
  await vm.runInContext('removeCurrent()',removal.ctx);
  assert.equal(vm.runInContext('blockedAccounts.has("failure")',removal.ctx),false,'failed removal does not pretend to block');
  assert.match(removal.element('action-status').textContent,/Could not remove/);
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
  assert.ok(accountButtons.every(button=>button.innerHTML.includes('add')), 'a new request accepts server membership changes');
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
  vm.runInContext('posts[1]._watchedSeconds=1; recordQuickSkip(1); recordQuickSkip(1)',ctx);
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
  // A wheel or trackpad burst (events less than 150ms apart) moves one reel.
  const feedEl = element('feed');
  const wheelAt = (time, deltaY) => {
    ctx.performance.now = () => time;
    const event = {deltaY, deltaX:0, target:{parentElement:null}, preventDefault() {this.prevented = true;}};
    feedEl.listeners.wheel.forEach(listener => listener(event));
    return event;
  };
  assert.equal(wheelAt(5000, 4).prevented, true, 'wheel events must not scroll natively');
  [5016, 5032, 5048, 5064].forEach(time => wheelAt(time, 4));
  assert.equal(vm.runInContext('current',ctx),1,'one trackpad swipe moves one reel');
  wheelAt(5300, -100);
  assert.equal(vm.runInContext('current',ctx),0,'the next wheel notch moves one more reel');
  // Vimium handles j/k itself and scrolls in small scrollBy() steps, one per
  // frame. Mandatory snapping turns each step into a whole reel; the burst may
  // still move only one reel, in either direction.
  vm.runInContext("posts.push({shortcode:'third', owner:'creator'}, {shortcode:'fourth', owner:'creator'})", ctx);
  feedEl.scrollTop = 0;
  for (let i = 0; i < 4; i++) {
    element('s'+i).getBoundingClientRect = () => ({top:i*800 - feedEl.scrollTop, bottom:(i+1)*800 - feedEl.scrollTop, height:800});
    element('s'+i).scrollIntoView = () => { feedEl.scrollTop = i*800; };
  }
  const frames = [];
  ctx.requestAnimationFrame = callback => frames.push(callback);
  const extensionScroll = (start, step) => {
    for (let n = 0; n < 6; n++) {
      ctx.performance.now = () => start + 16*n;
      feedEl.scrollTop = Math.max(0, Math.min(3*800, feedEl.scrollTop + step*800)); // a snapped scrollBy
      feedEl.listeners.scroll.forEach(listener => listener());
      frames.splice(0).forEach(callback => callback());
    }
    ctx.performance.now = () => start + 1000;
    frames.splice(0).forEach(callback => callback()); // the burst ends
  };
  vm.runInContext('current=0; lastScrollTop=0', ctx);
  extensionScroll(6000, 1);
  assert.equal(feedEl.scrollTop, 800, 'one extension scroll down moves one reel');
  vm.runInContext('current=1', ctx); // the observer's job in a browser
  extensionScroll(8000, -1);
  assert.equal(feedEl.scrollTop, 0, 'one extension scroll up moves one reel');
  ctx.performance.now = () => 2000;
  vm.runInContext('posts.length = 2', ctx);

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
  await viewingTests();
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
  assert.deepEqual(JSON.parse(calls.filter(call => call.url === '/reels/more').at(-1).options.body).session_blocked,
    [], 'batch requests still carry session blocks');

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


async function viewingTests() {
  const page = createPage(async () => ({ok:true,json:async()=>({posts:[],has_more:false})}));
  const run = code => vm.runInContext(code, page.ctx);
  page.ctx.clock = 0;
  page.ctx.performance.now = () => page.ctx.clock;
  page.ctx.video = {currentTime:0,paused:false,seeking:false,readyState:4,playbackRate:1};
  run("posts.push({shortcode:'watch01',reservation:'a'.repeat(32)}); current=0; posts[0]._lastSample={time:0,wall:0}");
  assert.equal(page.calls.filter(c=>c.url==='/reels/view').length, 0, 'queued reels are not viewed');
  page.ctx.clock=1000; page.ctx.video.currentTime=1;
  run('onReelProgress(0, video)'); await settleAll();
  assert.equal(run('posts[0]._watchedSeconds'),1);
  assert.equal(page.calls.filter(c=>c.url==='/reels/view').length,1,'visible playback reports a view');
  page.ctx.document.hidden=true; page.ctx.clock=2000; page.ctx.video.currentTime=2;
  run('onReelProgress(0, video)');
  assert.equal(run('posts[0]._watchedSeconds'),1,'hidden-tab progress is excluded');
  page.ctx.document.hidden=false; page.ctx.clock=3000; page.ctx.video.currentTime=3;
  run('onReelProgress(0, video)');
  assert.equal(run('posts[0]._watchedSeconds'),1,'returning from hidden does not count the missing interval');
  page.ctx.clock=4000; page.ctx.video.currentTime=4;
  run('onReelProgress(0, video)');
  assert.equal(run('posts[0]._watchedSeconds'),2);
  page.ctx.clock=5000; page.ctx.video.currentTime=0;
  run('onReelProgress(0, video)');
  page.ctx.clock=6000; page.ctx.video.currentTime=1;
  run('onReelProgress(0, video)');
  assert.equal(run('posts[0]._watchedSeconds'),2,'replaying a covered interval does not inflate watch time');
  page.ctx.clock=7000; page.ctx.video.currentTime=50;
  run('onReelProgress(0, video)');
  assert.equal(run('posts[0]._watchedSeconds'),2,'seeking forward is not watch time');
  run('recordQuickSkip(0)');
  assert.equal(page.calls.filter(c=>c.url==='/feedback').length,0,'looping a short clip is not a quick skip');
  run("posts.push({shortcode:'failed1',reservation:'b'.repeat(32)}); current=1; interruptPlayback(1); recordQuickSkip(1)");
  assert.equal(page.calls.filter(c=>c.url==='/feedback').length,0,'failed playback does not create a skip');
  assert.ok(page.calls.some(c=>c.url==='/reels/release' && JSON.parse(c.options.body).tokens.includes('b'.repeat(32))), 'unplayed clip is released on departure');
  run("posts.push({shortcode:'queue01',reservation:'c'.repeat(32)}); dropQueuedReels()");
  assert.ok(page.calls.some(c=>c.url==='/reels/release' && JSON.parse(c.options.body).tokens.includes('c'.repeat(32))), 'feed switching releases unseen queue');
  console.log('Viewing history ignores prefetch, hidden time, loops, seeks and failed playback');
}

// Removal also filters stale batches that were requested before the removal.
(async () => {
  let finish, defer = false;
  const {ctx, element} = createPage(async () => defer ? new Promise(resolve => {finish=resolve;}) : {ok:true,json:async()=>({posts:[],has_more:true})});
  await new Promise(setImmediate);
  defer = true;
  vm.runInContext("posts.push({shortcode:'one',owner:'removed'}, {shortcode:'two',owner:'removed'}); slides=2; current=0;", ctx);
  const pending = vm.runInContext('more()',ctx);
  vm.runInContext("accountChanged('removed', false)",ctx);
  assert.equal(element('s0').hidden, true, 'current removed creator disappears');
  assert.equal(element('s1').hidden, true, 'queued removed creator disappears');
  finish({ok:true,json:async()=>({posts:[{shortcode:'lateclip',owner:'removed'}],has_more:false,blocked:[],feed_accounts:['removed']})});
  await pending;
  assert.equal(vm.runInContext("blockedAccounts.has('removed')", ctx), true, 'stale response cannot undo removal');
  assert.equal(vm.runInContext('visibleIndices().length', ctx), 0, 'stale batch cannot show removed creator');
  vm.runInContext("fetching=true; accountChanged('removed', true)", ctx);
  assert.equal(vm.runInContext("blockedAccounts.has('removed')", ctx), false, 'explicit re-add allows creator');
})().catch(error => { console.error(error); process.exitCode = 1; });

(async () => {
  const {ctx, calls} = createPage(async () => ({ok:true, json:async () => ({posts:[],has_more:false})}));
  vm.runInContext("current=0; fetching=true; append([{shortcode:'repeat1',owner:'creator',reservation:'original'}]); append([{shortcode:'repeat1',owner:'creator',reservation:'duplicate'},{shortcode:'repeat2',owner:'creator'},{shortcode:'repeat2',owner:'creator'}]);", ctx);
  assert.equal(vm.runInContext('posts.length',ctx),2,'dedupe both existing slides and a single batch');
  assert.ok(calls.some(c=>c.url==='/reels/release' && JSON.parse(c.options.body).tokens.includes('duplicate')));
  vm.runInContext("rememberShown('skipped1'); append([{shortcode:'skipped1',owner:'creator'}]);",ctx);
  assert.equal(vm.runInContext('posts.length',ctx),2,'shown but unplayed reels cannot reappear');
  await vm.runInContext('fetching=false; more()',ctx);
  const request = JSON.parse(calls.filter(c=>c.url==='/reels/more').at(-1).options.body);
  assert.deepEqual([...request.exclude_codes].sort(), ['repeat1','repeat2','skipped1']);
})().catch(error => { console.error(error); process.exitCode=1; });

(async () => {
  let finish;
  const pending = new Promise(resolve => {finish = resolve;});
  const {ctx, element, timers, calls} = createPage(async url =>
    url.startsWith('/recognize/') ? pending : {ok:true,json:async()=>({posts:[],has_more:false})});
  const labels = [{textContent:''},{textContent:''}];
  element('song0').querySelector = () => labels[0];
  element('song1').querySelector = () => labels[1];
  vm.runInContext("posts.push({shortcode:'music01',owner:'one'},{shortcode:'music02',owner:'two'}); current=0; queueSong(0);",ctx);
  const firstTimer = vm.runInContext('songTimer',ctx);
  vm.runInContext('queueSong(0); queueSong(0)',ctx);
  assert.equal(vm.runInContext('songTimer',ctx),firstTimer,'playing/loop events do not postpone recognition');
  timers[firstTimer-1].run();
  assert.equal(calls.filter(c=>c.url.startsWith('/recognize/')).length,1);
  vm.runInContext('current=1; queueSong(1)',ctx);
  assert.equal(element('song1').hidden,false,'current reel shows queued recognition');
  assert.equal(labels[1].textContent,'Waiting to identify song…');
  finish({ok:true,json:async()=>({status:'no_match'})});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(vm.runInContext('songTimerFor',ctx),1,'old result schedules current reel');
  assert.equal(labels[0].textContent,'Song not identified');
  assert.equal(labels[1].textContent,'Waiting to identify song…','old result cannot overwrite current song');
})().catch(error=>{console.error(error);process.exitCode=1;});

(async () => {
  const {ctx} = createPage(async () => ({ok:true,json:async()=>({posts:[],has_more:false})}));
  let now = 0;
  ctx.performance.now = () => now;
  vm.runInContext('lastNavigation=null; paceSamples=0; refillSeconds=12; noteReelNavigation(0)',ctx);
  assert.equal(vm.runInContext('refillTarget()',ctx),24,'start with a conservative queue');
  for (let i=1;i<=3;i++) {now+=500;vm.runInContext('noteReelNavigation('+i+')',ctx);}
  assert.equal(vm.runInContext('refillTarget()',ctx),48,'fast scrolling starts refill farther ahead');
  const before = vm.runInContext('paceSamples',ctx);
  now+=500;vm.runInContext('noteReelNavigation(2)',ctx);
  assert.equal(vm.runInContext('paceSamples',ctx),before,'backward navigation is not consumption');
  now+=90000;vm.runInContext('noteReelNavigation(3)',ctx);
  assert.equal(vm.runInContext('paceSamples',ctx),before,'idle gaps are ignored');
  for(let i=4;i<=18;i++) {now+=10000;vm.runInContext('noteReelNavigation('+i+')',ctx);}
  assert.equal(vm.runInContext('refillTarget()',ctx),12,'slower browsing reduces queued inventory');
  vm.runInContext('secondsPerReel=2; refillSeconds=2; emptySince=null',ctx);
  assert.equal(vm.runInContext('refillTarget()',ctx),12);
  vm.runInContext('noteRefillResponse({posts:[],has_more:true},performance.now())',ctx);
  now+=40000;
  vm.runInContext("noteRefillResponse({posts:[{shortcode:'new'}],refill_seconds:10},performance.now())",ctx);
  assert.equal(vm.runInContext('refillTarget()',ctx),43,'empty polling time counts toward replenishment latency');
  vm.runInContext('refillSeconds=10000',ctx);
  assert.equal(vm.runInContext('refillTarget()',ctx),48,'latency cannot create an unbounded queue');
})().catch(error=>{console.error(error);process.exitCode=1;});

// Explicit links work without feeds and do not refill from the mixed feed.
(async () => {
  const page = createPage(async url => ({ok:true,json:async()=>({
    shortcode:'direct1',owner:'creator',video:'https://example.test/video.mp4',caption:'linked reel'
  })}), {boot:{post_code:'direct1',feeds:[]}});
  vm.runInContext("shownCodes.add('direct1')",page.ctx);
  await new Promise(setImmediate);
  assert.equal(vm.runInContext('posts.length',page.ctx),1,'explicit link bypasses watched exclusion');
  assert.equal(vm.runInContext('posts[0]._rich',page.ctx),true,'reuse fetched media');
  assert.equal(page.element('feed-status').hidden,true,'no create-feed or exhausted banner over the linked reel');
  assert.equal(page.calls.filter(c=>c.url==='/reels/media').length,1,'fetch linked media once');
  assert.equal(page.calls.filter(c=>c.url==='/reels/more').length,0,'do not fetch mixed feed');
  await vm.runInContext("boot.android=true; downloadReel(0, {disabled:false})",page.ctx);
  assert.equal(page.ctx.window.location.href,'/download/direct1','Android hands downloads to the native shell');
  assert.equal(page.calls.filter(c=>c.url.startsWith('/download/')).length,0,'Android avoids buffering a blob in JavaScript');
  const failure = createPage(async()=>({ok:false,json:async()=>({error:'Unavailable'})}),
    {boot:{post_code:'direct2',feeds:[]}});
  await new Promise(setImmediate);
  assert.match(failure.element('feed-status-text').textContent,/Unavailable/);
  assert.equal(failure.element('feed-retry').hidden,false,'failed link can be retried');
})().catch(error => {console.error(error);process.exitCode=1;});

// Empty account lists still play discovered topic reels.
(async () => {
  const page = createPage(async () => ({ok:true,json:async()=>({posts:reels('topicclip', 'topicclip2', 'topicclip3'),has_more:false})}),
    {boot:{feeds:['edits']}});
  await new Promise(setImmediate);
  vm.runInContext('feeded.clear(); updateFeedStatus()', page.ctx);
  assert.equal(page.element('feed-status').hidden,true,'empty feeds must not cover discovered reels with an add-accounts notice');
  assert.equal(page.element('feed-add').hidden,true,'adding profiles is optional');
})().catch(error => {console.error(error);process.exitCode=1;});
