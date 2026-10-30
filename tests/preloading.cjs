const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = new Map();
const calls = [];
let response = {posts: [], has_more: true};
const timers = [];
function element(id) {
  if (id === 'feed-picker' || id === 'shortcut-help') return null;
  if (!elements.has(id)) elements.set(id, {
    textContent: id === 'seed' ? '[]' : '', style: {}, dataset: {},
    getBoundingClientRect() {return {top:0,bottom:800,height:800};},
    scrollIntoView() {},
    setAttribute(name, value) { this[name] = value; },
    querySelector: () => null, querySelectorAll: () => [],
    insertAdjacentHTML() {
      const video = {src: '', load() {this.loads = (this.loads || 0) + 1;},
        play() {this.paused = false; this.plays = (this.plays || 0) + 1; return Promise.resolve();},
        pause() {this.paused = true;}, removeAttribute() {this.src = null;}, remove() {},
        getAttribute() {return this.src;}};
      this.querySelector = () => video;
    },
  });
  return elements.get(id);
}
const ctx = vm.createContext({
  document: {getElementById: element, addEventListener() {}, querySelectorAll: () => []},
  IntersectionObserver: class {constructor(callback) {ctx.observerCallback = callback;} observe() {} unobserve() {}}, AbortSignal,
  performance: {now: () => 2000},
  sessionStorage: {data: {}, getItem(key) {return this.data[key] || null;},
    setItem(key, value) {this.data[key] = value;}},
  setTimeout(fn) {timers.push(fn); return timers.length;},
  fetch: async (url, options) => {
    calls.push({url, options});
    return {ok: true, json: async () => url.endsWith('/media') ? {video: 'https://example.test/video.mp4'} : response};
  },
});
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), ctx);
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
  console.log('Reel preloading, keyboard shortcuts, and both block modes passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
