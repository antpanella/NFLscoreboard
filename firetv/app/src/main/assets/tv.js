/* Injected by the Fire TV app after each load of the Board. It never changes
   the Board itself: it presses the Board's own buttons and keys for the remote. */
(function(){
  if (window.__tv) return 'ok';

  // Element fullscreen inside an Android web view hands the page to the app and
  // can blank the screen. The app is already full screen, so it is a no-op here.
  var noop = function(){ return Promise.resolve(); };
  try { Element.prototype.requestFullscreen = noop; Element.prototype.webkitRequestFullscreen = noop; } catch(e){}

  var st = document.createElement('style');
  st.textContent =
    '.tvc{outline:4px solid #FFB000 !important;outline-offset:3px !important;border-radius:6px}' +
    '#tvToast{position:fixed;left:50%;bottom:6vh;transform:translateX(-50%);z-index:99999;' +
    'background:#122019;color:#F2F5F0;border:1px solid #24382C;border-radius:10px;padding:.5em 1.1em;' +
    'font:700 24px "Barlow Condensed",sans-serif;opacity:0;transition:opacity .2s;pointer-events:none}' +
    '#tvToast.on{opacity:1}';
  document.head.appendChild(st);

  var toastEl = document.createElement('div');
  toastEl.id = 'tvToast';
  document.body.appendChild(toastEl);
  var toastT = null;
  function toast(t){
    toastEl.textContent = t; toastEl.className = 'on';
    clearTimeout(toastT); toastT = setTimeout(function(){ toastEl.className = ''; }, 1800);
  }

  function $(id){ return document.getElementById(id); }
  function panelOpen(){ var p = $('panel'); return !!(p && p.classList.contains('open')); }
  function press(key){
    var ev;
    try { ev = new KeyboardEvent('keydown', {key: key, bubbles: true, cancelable: true}); }
    catch(e){ ev = document.createEvent('Event'); ev.initEvent('keydown', true, true); ev.key = key; }
    document.dispatchEvent(ev);
  }
  function sig(){
    var l = $('live'); if (!l) return '';
    var s = l.className + '|';
    for (var i = 0; i < l.children.length; i++) s += l.children[i].className + ',';
    return s + '|' + document.body.className;
  }

  /* ---- a cursor for Settings: the arrows move between controls, OK presses ---- */
  var cur = null;
  var SEL = 'button, input:not([type=hidden]), select, textarea, a[href], summary, [tabindex]:not([tabindex="-1"])';
  function controls(){
    var p = $('panel'); if (!p) return [];
    var all = p.querySelectorAll(SEL), out = [];
    for (var i = 0; i < all.length; i++){
      var el = all[i];
      if (el.disabled) continue;
      var r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      out.push(el);
    }
    return out;
  }
  function setCur(el){
    if (cur) cur.classList.remove('tvc');
    cur = el;
    if (cur){
      cur.classList.add('tvc');
      try { cur.scrollIntoView({block: 'nearest', inline: 'nearest'}); } catch(e){ cur.scrollIntoView(false); }
    }
  }
  function move(dir){
    var list = controls();
    if (!list.length) return;
    if (!cur || list.indexOf(cur) < 0){ setCur(list[0]); return; }
    var a = cur.getBoundingClientRect(), ax = a.left + a.width / 2, ay = a.top + a.height / 2;
    var best = null, bestScore = Infinity;
    for (var i = 0; i < list.length; i++){
      var el = list[i]; if (el === cur) continue;
      var b = el.getBoundingClientRect(), dx = b.left + b.width / 2 - ax, dy = b.top + b.height / 2 - ay;
      var main, side;
      if (dir === 'down'){ main = dy; side = dx; }
      else if (dir === 'up'){ main = -dy; side = dx; }
      else if (dir === 'right'){ main = dx; side = dy; }
      else { main = -dx; side = dy; }
      if (main < 6) continue;
      // sideways stays on the same row: the box must overlap the cursor's line
      if ((dir === 'left' || dir === 'right') && (b.bottom <= a.top + 2 || b.top >= a.bottom - 2)) continue;
      var score = main + Math.abs(side) * (dir === 'up' || dir === 'down' ? 0.5 : 3);
      if (score < bestScore){ bestScore = score; best = el; }
    }
    if (best) setCur(best);
  }
  function activate(){
    if (!cur) return;
    var t = (cur.type || '').toLowerCase(), tag = cur.tagName;
    if (tag === 'SELECT'){
      // OK steps through the choices: no pop-up list to fight with the remote
      if (cur.options.length){
        cur.selectedIndex = (cur.selectedIndex + 1) % cur.options.length;
        cur.dispatchEvent(new Event('input', {bubbles: true}));
        cur.dispatchEvent(new Event('change', {bubbles: true}));
        toast(cur.options[cur.selectedIndex].text);
      }
    } else if (tag === 'TEXTAREA' || (tag === 'INPUT' && t !== 'checkbox' && t !== 'radio' && t !== 'button' && t !== 'submit')){
      cur.focus(); cur.click();          // brings up the Fire TV keyboard
    } else {
      cur.click();
    }
  }
  window.addEventListener('keydown', function(e){
    if (!panelOpen()){ if (cur){ cur.classList.remove('tvc'); cur = null; } return; }
    var dirs = {ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right'};
    var a = document.activeElement;
    var typing = a && (a.tagName === 'TEXTAREA' || (a.tagName === 'INPUT' && !/^(checkbox|radio|button|submit)$/i.test(a.type || '')));
    if (dirs[e.key]){
      if (typing && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) return;   // move the caret
      if (typing) a.blur();
      move(dirs[e.key]);
      e.preventDefault(); e.stopPropagation();
    } else if (e.key === 'Enter' && !typing){
      activate();
      e.preventDefault(); e.stopPropagation();
    }
  }, true);

  window.__tv = {
    menu: function(){
      if (panelOpen()){ var c = $('sClose'); if (c) c.click(); }
      else { var g = $('gear'); if (g) g.click(); setTimeout(function(){ move('down'); }, 150); }
      return 'ok';
    },
    key: function(k){
      press(k);
      var d = $('sDelay');
      if ((k === '[' || k === ']') && d) toast('Delay ' + d.value + ' s');
      else if (k === 'r') toast('Refreshing');
      return 'ok';
    },
    back: function(){
      if (panelOpen()){
        if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
        var c = $('sClose'); if (c) c.click();
        return 'handled';
      }
      var before = sig();
      press('Escape');
      return sig() !== before ? 'handled' : 'none';
    }
  };

  // straight past the start screen: this is a TV, there is nobody to click it
  var start = $('start');
  if (start && !/^#(bets|remote)/.test(location.hash)) start.click();
  return 'ok';
})();
