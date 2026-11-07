// Transform the existing media element: playback and the surrounding layout stay intact.
(function () {
  function createMediaZoom() {
    let media = null, original = null, scale = 1, x = 0, y = 0, gesture = null, resetButton = null;
    const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
    const points = touches => Array.from(touches).slice(0, 2);
    const center = pair => ({x:(pair[0].clientX + pair[1].clientX)/2, y:(pair[0].clientY + pair[1].clientY)/2});
    const distance = pair => Math.hypot(pair[0].clientX-pair[1].clientX, pair[0].clientY-pair[1].clientY);
    function reset() {
      if (media && original) {
        for (const [key,value] of Object.entries(original)) media.style[key] = value;
        media.classList.remove('media-zoomed');
      }
      resetButton?.remove(); resetButton = null;
      media = null; original = null; gesture = null; scale = 1; x = y = 0;
    }
    function select(element) {
      if (media === element) return;
      reset();
      if (!element) return;
      media = element;
      original = Object.fromEntries(['transform','transformOrigin','willChange'].map(key => [key,element.style[key]]));
      media.style.transformOrigin = '50% 50%'; media.style.willChange = 'transform';
    }
    function paint() {
      if (!media) return;
      media.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
      media.classList.toggle('media-zoomed', scale > 1.01);
    }
    function begin(element, touches) {
      if (touches.length !== 2 || !element) return false;
      select(element);
      const pair = points(touches), mid = center(pair), bounds = media.getBoundingClientRect();
      gesture = {distance:Math.max(1,distance(pair)), mid, scale, x, y,
        anchorX:(mid.x-bounds.left-bounds.width/2)/scale,
        anchorY:(mid.y-bounds.top-bounds.height/2)/scale,
        width:bounds.width/scale,height:bounds.height/scale};
      return true;
    }
    function move(touches) {
      if (!gesture || touches.length !== 2) return false;
      const pair = points(touches), mid = center(pair);
      scale = clamp(gesture.scale * distance(pair)/gesture.distance, 1, 4);
      const extra = scale - gesture.scale;
      x = gesture.x + mid.x-gesture.mid.x - gesture.anchorX*extra;
      y = gesture.y + mid.y-gesture.mid.y - gesture.anchorY*extra;
      x = clamp(x,-gesture.width*(scale-1)/2,gesture.width*(scale-1)/2);
      y = clamp(y,-gesture.height*(scale-1)/2,gesture.height*(scale-1)/2);
      paint(); return true;
    }
    function zoom(element, amount = 2) {
      select(element);
      if (!media) return;
      scale = clamp(amount, 1, 4); x = y = 0; paint();
      if (scale <= 1) { reset(); return; }
      if (!resetButton) {
        resetButton = document.createElement('button'); resetButton.type = 'button';
        resetButton.className = 'media-zoom-reset'; resetButton.textContent = 'Reset zoom';
        resetButton.setAttribute('aria-label','Reset zoom');
        resetButton.onclick = event => { event.stopPropagation(); reset(); };
        (media.closest('.slide,#lbcontent') || media.parentElement).append(resetButton);
      }
    }
    function wheel(element, event) {
      if (!event.ctrlKey || !element) return false;
      if (event.cancelable) event.preventDefault(); event.stopImmediatePropagation();
      zoom(element, (media === element ? scale : 1) * Math.exp(-event.deltaY * .005));
      return true;
    }
    return {begin, move, end:reset, reset, zoom, wheel, isZoomed:() => scale > 1.01,
      element:() => media, scale:() => scale};
  }
  function installMediaZoom(container, {getMedia = target => target.closest('img,video'), onGesture = () => {}} = {}) {
    const zoom = createMediaZoom();
    let active = false, ignoreClickUntil = 0;
    function consume(event) {
      if (event.cancelable) event.preventDefault(); event.stopImmediatePropagation();
      ignoreClickUntil = performance.now() + 800; onGesture();
    }
    document.addEventListener('touchstart', event => {
      if (active) { consume(event); return; }
      if (!container.contains(event.target) || event.touches.length !== 2) return;
      const first = getMedia(event.touches[0].target), second = getMedia(event.touches[1].target);
      if (!first || first !== second) return;
      active = zoom.begin(first,event.touches);
      if (active) consume(event);
    }, {capture:true,passive:false});
    document.addEventListener('touchmove', event => {
      if (!active) return;
      zoom.move(event.touches); consume(event);
    }, {capture:true,passive:false});
    const finish = event => {
      if (!active) return;
      consume(event);
      if (!event.touches.length || event.type === 'touchcancel') { active = false; zoom.end(); }
    };
    document.addEventListener('touchend', finish, {capture:true,passive:false});
    document.addEventListener('touchcancel', finish, {capture:true,passive:false});
    container.addEventListener('click', event => {
      if (performance.now() < ignoreClickUntil) { event.preventDefault(); event.stopImmediatePropagation(); }
    }, true);
    container.addEventListener('wheel', event => zoom.wheel(getMedia(event.target),event), {capture:true,passive:false});
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && zoom.isZoomed()) { event.preventDefault(); event.stopImmediatePropagation(); zoom.reset(); }
    }, true);
    window.addEventListener('blur', () => {active = false; zoom.reset();});
    document.addEventListener('visibilitychange', () => {if (document.hidden) {active = false; zoom.reset();}});
    return zoom;
  }
  window.createMediaZoom = createMediaZoom;
  window.installMediaZoom = installMediaZoom;
})();
