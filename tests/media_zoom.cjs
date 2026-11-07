const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const events = new Map();
const buttons = [];
const listeners = (target, type, fn) => {const key=target+':'+type; events.set(key,[...(events.get(key)||[]),fn]);};
const doc = {
  addEventListener(type,fn){listeners('document',type,fn)},
  createElement(){const b={type:'',className:'',setAttribute(){},remove(){this.removed=true}};buttons.push(b);return b;},
};
const context = {window:{addEventListener(type,fn){listeners('window',type,fn)}},document:doc,performance:{now:()=>100},console};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
function video() {
 const classes=new Set();
 const host={append(b){this.button=b}};
 return {style:{transform:'',transformOrigin:'',willChange:''},classList:{add(c){classes.add(c)},remove(c){classes.delete(c)},toggle(c,on){on?classes.add(c):classes.delete(c)}},
  getBoundingClientRect:()=>({left:0,top:0,width:300,height:500}),closest:()=>host,parentElement:host,
  currentTime:12,paused:false,muted:false,playbackRate:1,host};
}
const points=(x1,y1,x2,y2)=>[{clientX:x1,clientY:y1},{clientX:x2,clientY:y2}];
const zoom=context.window.createMediaZoom(),media=video();
assert.equal(zoom.begin(media,points(100,200,200,200)),true);
zoom.move(points(50,220,250,220));
assert.equal(zoom.scale(),2);
assert.match(media.style.transform,/translate\(0px, 70px\) scale\(2\)/,'pinch zoom follows its focal point and movement');
assert.equal(media.currentTime,12);assert.equal(media.paused,false);assert.equal(media.muted,false);assert.equal(media.playbackRate,1);
zoom.move(points(-1000,200,1000,200));assert.equal(zoom.scale(),4,'zoom has an upper bound');
zoom.end();assert.equal(media.style.transform,'');assert.equal(zoom.isZoomed(),false,'pinch release restores layout');
zoom.begin(media,points(100,200,200,200));zoom.move(points(110,200,190,200));assert.equal(zoom.scale(),1,'pinching inward never shrinks media below its fitted size');zoom.reset();
let prevented=0,stopped=0;
const event={ctrlKey:true,deltaY:-140,cancelable:true,preventDefault(){prevented++},stopImmediatePropagation(){stopped++}};
assert.equal(zoom.wheel(media,event),true);assert.equal(prevented,1);assert.equal(stopped,1);assert.ok(zoom.isZoomed());
assert.equal(media.host.button.textContent,'Reset zoom');
media.host.button.onclick({stopPropagation(){}});assert.equal(zoom.isZoomed(),false);
const other=video();zoom.zoom(media);zoom.zoom(other);assert.equal(media.style.transform,'','switching media restores the old element');zoom.reset();
const container={contains:t=>t===media,addEventListener(type,fn){listeners('container',type,fn)}};
let cancelled=0;
context.window.installMediaZoom(container,{getMedia:t=>t===media?media:null,onGesture(){cancelled++}});
function dispatch(type,touches,target=media){
 const e={type,target,touches:touches.map(t=>({...t,target})),changedTouches:[],cancelable:true,
  prevented:false,stopped:false,preventDefault(){this.prevented=true},stopImmediatePropagation(){this.stopped=true}};
 for(const fn of events.get('document:'+type)||[])fn(e);
 return e;
}
assert.equal(dispatch('touchstart',points(100,200,200,200)).stopped,true,'second finger cancels post gestures');
assert.equal(dispatch('touchmove',points(60,200,240,200)).prevented,true);
assert.match(media.style.transform,/scale\(1.8\)/);
assert.equal(dispatch('touchend',[{clientX:60,clientY:200}]).stopped,true,'first release cannot navigate');
assert.equal(dispatch('touchend',[]).stopped,true,'last release cannot pause');assert.equal(media.style.transform,'');
assert.ok(cancelled>=4);
const click={preventDefault(){this.prevented=true},stopImmediatePropagation(){this.stopped=true}};
for(const fn of events.get('container:click')||[])fn(click);
assert.equal(click.stopped,true,'synthetic release click is suppressed');
assert.equal(dispatch('touchstart',points(100,200,200,200),{}).stopped,false,'outside controls remain unaffected');
console.log('Media zoom preserves playback and isolates pinch gestures');
