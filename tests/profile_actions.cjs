const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2], 'utf8');
function page(zoom) {
  const nodes = new Map(), calls = [], requests = [];
  function element(id) {
    if (!nodes.has(id)) nodes.set(id, {id,hidden: ['lb','profile-comments'].includes(id),dataset:{},style:{},
      textContent:'',innerHTML:'',scrollTop:0,scrollHeight:1000,clientHeight:200,
      classList:{add(){},remove(){},contains(){return false;}},setAttribute(name,value){this[name]=value;},
      addEventListener(){},append(){},appendChild(){},insertAdjacentHTML(position,html){this.innerHTML+=html;},
      querySelector(){return null;},querySelectorAll(){return [];},getBoundingClientRect(){return {top:0,bottom:0};},
      focus(){},pause(){},remove(){},click(){},select(){}});
    return nodes.get(id);
  }
  element('boot').textContent = JSON.stringify({posts:[],username:'creator'});
  const document = {hidden:false,body:element('body'),getElementById:id=>id==='more'?null:element(id),
    querySelector:selector=>element(selector),querySelectorAll:()=>[],addEventListener(){},createElement:()=>element('created')};
  const ctx = vm.createContext({document,window:{location:{},addEventListener(){},installMediaZoom:zoom ? () => zoom : undefined},location:{pathname:'/u/creator'},
    history:{state:null,pushState(){},back(){}},localStorage:{getItem(){return null;},setItem(){}},navigator:{},
    icon:name=>`<svg>${name}</svg>`,accountChanged:null,chooseFeed:async()=>{},notifyAccountChange(){},
    console,Intl,Date,Map,Set,Promise,URL,URLSearchParams,AbortSignal,performance:{now:()=>0},innerHeight:800,
    setTimeout(){return 1;},clearTimeout(){},requestAnimationFrame(){},
    fetch:(url,options)=>{calls.push({url,options});return new Promise(resolve=>requests.push(resolve));},
  });
  vm.runInContext(source,ctx);
  return {ctx,element,calls,requests,run:code=>vm.runInContext(code,ctx)};
}
const response = data=>({ok:true,json:async()=>data});
(async()=>{
  const p=page();
  p.run(`store.push({shortcode:'first01',is_video:true,_rich:true},{shortcode:'second1',is_video:true,_rich:true});lbEl.hidden=false;`);
  const first=p.run('loadProfileComments(store[0])');
  p.run("document.getElementById('profile-comments').hidden=false;document.getElementById('profile-comments').dataset.code='second1';cur=1");
  p.element('profile-comments-body').innerHTML='SECOND';
  p.requests.shift()(response({comments:[{id:'1',username:'one',text:'first comment'}],next_cursor:'cursor'}));
  await first;
  assert.equal(p.element('profile-comments-body').innerHTML,'SECOND','late comments never replace the current post');
  p.run("cur=0;document.getElementById('profile-comments').dataset.code='first01'");
  let next=p.run('loadProfileComments(store[0])');
  assert.match(p.calls.at(-1).url,/after=cursor/);
  p.requests.shift()(response({comments:[{id:'1',username:'one',text:'duplicate'},{id:'2',username:'two',text:'second'}],next_cursor:'cursor'}));
  await next;
  assert.equal(p.run('store[0]._profileComments.length'),2,'duplicates are removed');
  assert.equal(p.run('store[0]._profileCommentCursor'),null,'repeated cursor stops pagination');
  const errors=page();errors.run("store.push({shortcode:'failed1'});lbEl.hidden=false;document.getElementById('profile-comments').hidden=false;document.getElementById('profile-comments').dataset.code='failed1'");
  let failed=errors.run('loadProfileComments()');errors.requests.shift()({ok:false,json:async()=>({error:'Unavailable'})});await failed;
  assert.equal(errors.run('store[0]._profileCommentsError'),true);
  let retry=errors.run('loadProfileComments()');errors.requests.shift()(response({comments:[{id:'ok',username:'test',text:'works'}]}));await retry;
  assert.equal(errors.run('store[0]._profileCommentsError'),false);
  assert.equal(errors.run('store[0]._profileComments.length'),1);
  const saving=page();saving.run("store.push({shortcode:'saved01',is_video:true,_rich:true,saved:true});lbEl.hidden=false");
  const save=saving.run('saveProfilePost()');await new Promise(setImmediate);
  assert.equal(JSON.parse(saving.calls.at(-1).options.body).saved,false,'existing saved state toggles off');
  saving.requests.shift()(response({saved:false}));await save;
  assert.equal(saving.run('store[0].saved'),false);
  const native=page();native.run("boot.android=true;store.push({shortcode:'photo01',is_carousel:true,_rich:true,slides:[{image:'https://cdn.test/a.jpg'},{image:'https://cdn.test/b.webp'}]});lbEl.hidden=false");
  native.element('#lbcontent .slides').scrollLeft=100;native.element('#lbcontent .slides').clientWidth=100;
  await native.run('downloadProfilePost()');
  assert.equal(native.ctx.window.location.href,'/download/photo01?slide=1&format=webp');
  await native.run('shareProfilePost()');
  assert.equal(native.ctx.window.location.href,'/share/post/photo01');
  native.run('updateProfileActions()');
  assert.equal(native.element('lb-save').hidden,true,'photo/carousel cannot enter the reels-only library');
  let zoomed = true;
  const back=page({reset(){zoomed=false;},isZoomed(){return zoomed;}});
  back.run("lbEl.hidden=false;document.getElementById('profile-comments').hidden=false");
  assert.equal(back.run('handleProfileBack()'),true);
  assert.equal(zoomed,false,'Android Back resets zoom first');
  assert.equal(back.element('profile-comments').hidden,false,'zoom reset keeps comments open');
  assert.equal(back.run('handleProfileBack()'),true);
  assert.equal(back.element('profile-comments').hidden,true,'next Back closes comments');
  assert.equal(back.element('lb').hidden,false,'closing comments keeps viewer open');
  assert.equal(back.run('handleProfileBack()'),true);
  assert.equal(back.element('lb').hidden,true,'next Back closes viewer');
  assert.equal(back.run('handleProfileBack()'),false,'closed viewer leaves normal navigation alone');
  console.log('Profile actions, stale comments, pagination, retries, saves and Android downloads passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
