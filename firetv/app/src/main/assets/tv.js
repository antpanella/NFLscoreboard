/* Injected by the Fire TV app after each page load. It never changes the Board
   itself: it drives the Board's own buttons, settings and keys from the remote.

   The app sends every remote key here (__tv.nav / ok / menu / back / key), so
   the web view never does its own focus or scrolling.
     Board      arrows show a yellow cursor on the game cards; OK spotlights the
                card, OK twice quickly shows just that game; Back lets go.
                On the pregame screen left/right turn the panels.
     Settings   arrows move the cursor (left/right on the tab row switches tab);
                OK ticks a box, presses a button, opens a list picker for a
                dropdown, an adjuster for a number, or the keyboard for text.
                Moving onto an "i" shows its explanation. */
(function(){
  if (window.__tv) return 'ok';

  function $(id){ return document.getElementById(id); }
  var BOARD = !!($('panel') && $('live'));

  // Element fullscreen inside an Android web view hands the page to the app and
  // can blank the screen. The app is already full screen, so it is a no-op here.
  var noop = function(){ return Promise.resolve(); };
  try { Element.prototype.requestFullscreen = noop; Element.prototype.webkitRequestFullscreen = noop; } catch(e){}

  var st = document.createElement('style');
  st.textContent =
    '.tvc{outline:4px solid #FFB000 !important;outline-offset:3px !important;border-radius:6px}' +
    '#live .game.tvc,#alsolive .al.tvc{outline-offset:-4px !important;outline-width:6px !important}' +
    '.tvedit{background:#FFB000 !important;color:#0A1410 !important;font-weight:700 !important}' +
    '#tvToast{position:fixed;left:50%;bottom:6vh;transform:translateX(-50%);z-index:99999;' +
      'background:#122019;color:#F2F5F0;border:1px solid #24382C;border-radius:10px;padding:.45em 1em;' +
      'font:700 22px "Barlow Condensed",sans-serif;opacity:0;transition:opacity .2s;pointer-events:none;white-space:nowrap}' +
    '#tvToast.on{opacity:1}' +
    '#tvPick{position:fixed;z-index:99998;background:#0E1A14;border:2px solid #FFB000;border-radius:10px;' +
      'padding:.3rem 0;max-height:70vh;overflow-y:auto;min-width:12rem;box-shadow:0 0 0 100vmax rgba(0,0,0,.45)}' +
    '#tvPick div{padding:.45rem 1.1rem;font-size:1.05rem;color:#F2F5F0;white-space:nowrap}' +
    '#tvPick div.sel{color:#FFB000;font-weight:700}' +
    '#tvPick div.hi{background:#FFB000;color:#0A1410;font-weight:700}' +
    '#tvPick .ttl{color:#8FA396;font-size:.85rem;padding:.2rem 1.1rem .35rem}';
  document.head.appendChild(st);

  var toastEl = document.createElement('div');
  toastEl.id = 'tvToast';
  document.body.appendChild(toastEl);
  var toastT = null;
  function toast(t, ms){
    toastEl.textContent = t; toastEl.className = 'on';
    clearTimeout(toastT); toastT = setTimeout(function(){ toastEl.className = ''; }, ms || 1800);
  }
  function fire(el, type){
    var ev;
    try { ev = new Event(type, {bubbles: true}); }
    catch(e){ ev = document.createEvent('Event'); ev.initEvent(type, true, true); }
    el.dispatchEvent(ev);
  }
  function press(key){
    var ev;
    try { ev = new KeyboardEvent('keydown', {key: key, bubbles: true, cancelable: true}); }
    catch(e){ ev = document.createEvent('Event'); ev.initEvent('keydown', true, true); ev.key = key; }
    document.dispatchEvent(ev);
  }
  function panelOpen(){ var p = $('panel'); return !!(p && p.classList.contains('open')); }
  function visible(el){
    if (!el || el.disabled) return false;
    var r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  function isText(el){
    if (!el) return false;
    if (el.tagName === 'TEXTAREA') return true;
    return el.tagName === 'INPUT' && /^(text|password|search|email|url|tel|)$/i.test(el.type || '');
  }
  function isNum(el){ return el && el.tagName === 'INPUT' && (el.type || '').toLowerCase() === 'number'; }

  /* Things a TV can't do: pick a file from the device or save one. */
  if (BOARD){
    ['sShot', 'sRecFile', 'sCfgFile', 'sCfgExp'].forEach(function(id){
      var el = $(id), lab = el && el.closest && el.closest('label');
      if (lab) lab.style.display = 'none';
      if (id === 'sShot' && lab && lab.nextElementSibling && lab.nextElementSibling.tagName === 'P') lab.nextElementSibling.style.display = 'none';
    });
  }

  /* ---------------- the cursor ---------------- */
  var cur = null, idleT = null;
  function setCur(el, quiet){
    if (cur && cur !== el){
      cur.classList.remove('tvc');
      if (cur.classList.contains('tipb')) cur.classList.remove('open');
    }
    cur = el;
    if (!cur) return;
    cur.classList.add('tvc');
    if (panelOpen() && $('panel').contains(cur)) keepInPanel(cur);
    else if (cur.scrollIntoView) { try { cur.scrollIntoView({block: 'nearest'}); } catch(e){} }
    if (cur.classList.contains('tipb')){          // landing on an "i" shows what it says
      var p = $('panel');
      cur.classList.toggle('up', cur.getBoundingClientRect().bottom > p.getBoundingClientRect().bottom - 230);
      cur.classList.add('open');
    }
    if (!quiet && !panelOpen()){                   // the board's cursor fades out when left alone
      clearTimeout(idleT);
      idleT = setTimeout(function(){ if (!panelOpen()) clearCur(); }, 8000);
    }
  }
  function clearCur(){ if (cur){ cur.classList.remove('tvc'); if (cur.classList.contains('tipb')) cur.classList.remove('open'); } cur = null; }

  // The panel scrolls under a sticky title and tab row: keep the cursor clear of both.
  function keepInPanel(el){
    var p = $('panel'), tabs = $('ptabs');
    if (!p || (tabs && tabs.contains(el)) || el.id === 'sClose') return;
    var r = el.getBoundingClientRect(), pr = p.getBoundingClientRect();
    var top = tabs ? tabs.getBoundingClientRect().bottom + 10 : pr.top + 10;
    if (r.top < top) p.scrollTop -= (top - r.top);
    else if (r.bottom > pr.bottom - 14) p.scrollTop += (r.bottom - pr.bottom + 14 + Math.min(120, pr.height * 0.15));
  }

  function best(list, from, dir, sameRow){
    var a = from.getBoundingClientRect(), ax = a.left + a.width / 2, ay = a.top + a.height / 2;
    var pick = null, bestScore = Infinity;
    for (var i = 0; i < list.length; i++){
      var el = list[i]; if (el === from) continue;
      var b = el.getBoundingClientRect(), dx = b.left + b.width / 2 - ax, dy = b.top + b.height / 2 - ay;
      var main, side;
      if (dir === 'down'){ main = dy; side = dx; }
      else if (dir === 'up'){ main = -dy; side = dx; }
      else if (dir === 'right'){ main = dx; side = dy; }
      else { main = -dx; side = dy; }
      if (main < 6) continue;
      if (sameRow && (dir === 'left' || dir === 'right') && (b.bottom <= a.top + 2 || b.top >= a.bottom - 2)) continue;
      var score = main + Math.abs(side) * (dir === 'up' || dir === 'down' ? 0.5 : 3);
      // Settings is a list of rows: up/down takes the very next row first, then
      // the control in it nearest across, so a setting is never skipped
      if (sameRow && (dir === 'up' || dir === 'down')) score = Math.round(main / 14) * 100000 + Math.abs(side);
      if (score < bestScore){ bestScore = score; pick = el; }
    }
    return pick;
  }

  /* ---------------- Settings ---------------- */
  var SEL = 'button, input, select, textarea, a[href], summary';
  function panelControls(){
    var p = $('panel'); if (!p) return [];
    var all = p.querySelectorAll(SEL), out = [];
    for (var i = 0; i < all.length; i++){
      var el = all[i], t = (el.type || '').toLowerCase();
      if (el.tagName === 'INPUT' && (t === 'hidden' || t === 'file' || t === 'color')) continue;
      if (visible(el)) out.push(el);
    }
    return out;
  }
  function activeTab(){ return document.querySelector('#ptabs button.on') || document.querySelector('#ptabs button'); }
  function tabButtons(){
    return [].filter.call(document.querySelectorAll('#ptabs button'), visible);
  }
  function firstInTab(){
    var tabs = $('ptabs'), line = tabs ? tabs.getBoundingClientRect().bottom : 0;
    var list = panelControls().filter(function(el){ return !(tabs && tabs.contains(el)) && el.id !== 'sClose'; });
    var pick = null, top = Infinity;
    list.forEach(function(el){
      var r = el.getBoundingClientRect();
      var y = r.top < line ? r.top + 100000 : r.top;   // anything scrolled under the tabs comes last
      if (y < top - 2 || (Math.abs(y - top) <= 2 && pick && r.left < pick.getBoundingClientRect().left)){ top = y; pick = el; }
    });
    return pick;
  }
  function panelNav(dir){
    var list = panelControls();
    if (!list.length) return;
    if (!cur || list.indexOf(cur) < 0){ setCur(activeTab() || list[0]); return; }
    var tabs = $('ptabs');
    // the tab row: left/right switches tab straight away, down goes into it
    if (tabs && tabs.contains(cur)){
      var tb = tabButtons(), i = tb.indexOf(cur);
      if (dir === 'left' || dir === 'right'){
        var n = tb[i + (dir === 'right' ? 1 : -1)];
        if (n){ n.click(); setCur(n); $('panel').scrollTop = 0; }
        return;
      }
      if (dir === 'down'){ var f = firstInTab(); if (f) setCur(f); return; }
      if (dir === 'up'){ setCur($('sClose')); return; }
    }
    if (cur.id === 'sClose' && dir === 'down'){ setCur(activeTab()); return; }
    // up/down go row to row and land on the setting itself; an "i" is reached
    // sideways from its setting, so a column of them never traps the cursor
    var pool = (dir === 'up' || dir === 'down')
      ? list.filter(function(el){ return !el.classList.contains('tipb'); }) : list;
    var next = best(pool, cur, dir, true);
    if (next && tabs && tabs.contains(next)) next = activeTab();   // back up to the tab you're on
    if (next){ setCur(next); return; }
    if (dir === 'up' || dir === 'down'){                            // nothing that way: read on
      var p = $('panel');
      p.scrollTop += (dir === 'down' ? 1 : -1) * p.clientHeight * 0.35;
    }
  }

  /* A dropdown opens as a list on the TV: up/down to choose, OK to set, Back to leave it. */
  var pick = null;
  function openPicker(sel){
    var box = document.createElement('div');
    box.id = 'tvPick';
    var lab = sel.closest && sel.closest('label'), title = '';
    if (lab){                                     // the label's own words, without its controls
      var c = lab.cloneNode(true);
      [].forEach.call(c.querySelectorAll('select, button, input'), function(x){ x.remove(); });
      title = (c.textContent || '').replace(/\s+/g, ' ').trim();
    }
    var opts = [].slice.call(sel.options);
    if (title){ var t = document.createElement('div'); t.className = 'ttl'; t.textContent = title.slice(0, 60); box.appendChild(t); }
    var rows = opts.map(function(o, i){
      var d = document.createElement('div');
      d.textContent = o.text;
      if (i === sel.selectedIndex) d.className = 'sel';
      box.appendChild(d);
      return d;
    });
    document.body.appendChild(box);
    var r = sel.getBoundingClientRect(), bw = box.offsetWidth, bh = box.offsetHeight;
    box.style.left = Math.max(8, Math.min(innerWidth - bw - 8, r.right - bw)) + 'px';
    box.style.top = Math.max(8, Math.min(innerHeight - bh - 8, r.top - 10)) + 'px';
    pick = {sel: sel, box: box, rows: rows, i: Math.max(0, sel.selectedIndex)};
    hi();
  }
  function hi(){
    pick.rows.forEach(function(d, i){ d.classList.toggle('hi', i === pick.i); });
    var d = pick.rows[pick.i], b = pick.box;
    if (d.offsetTop < b.scrollTop) b.scrollTop = d.offsetTop - 8;
    else if (d.offsetTop + d.offsetHeight > b.scrollTop + b.clientHeight) b.scrollTop = d.offsetTop + d.offsetHeight - b.clientHeight + 8;
  }
  function closePicker(set){
    if (!pick) return;
    if (set && pick.sel.selectedIndex !== pick.i){
      pick.sel.selectedIndex = pick.i;
      fire(pick.sel, 'input'); fire(pick.sel, 'change');
      toast(pick.sel.options[pick.i].text);
    }
    pick.box.remove(); pick = null;
  }

  /* A number becomes an adjuster: left/right (or up/down) in its own steps, OK or Back when done. */
  var num = null;
  function startNum(el){
    num = {el: el, was: el.value};
    el.classList.add('tvedit');
    toast('◀ ▶ to change · OK when done', 2600);
  }
  function stepNum(d){
    var el = num.el, step = Number(el.step) || 1;
    var min = el.min === '' ? -Infinity : Number(el.min), max = el.max === '' ? Infinity : Number(el.max);
    var v = Number(el.value) || 0;
    v = Math.max(min, Math.min(max, Math.round((v + d * step) * 1000) / 1000));
    el.value = String(v);
    fire(el, 'input'); fire(el, 'change');
    toast(String(v));
  }
  function endNum(){ if (!num) return; num.el.classList.remove('tvedit'); num = null; }

  function panelOk(){
    if (!cur) { panelNav('down'); return 'ok'; }
    var el = cur, t = (el.type || '').toLowerCase();
    if (el.tagName === 'SELECT'){ openPicker(el); return 'ok'; }
    if (isNum(el)){ startNum(el); return 'ok'; }
    if (isText(el)){
      if (document.activeElement === el){ el.blur(); return 'ok'; }   // Done on the keyboard
      el.focus();
      return 'text';                                                   // the app opens the keyboard
    }
    if (t === 'checkbox' || t === 'radio'){ el.click(); return 'ok'; }
    el.click();
    // a button can rebuild the panel (Add, Connect, a new code): keep a cursor
    setTimeout(function(){ if (panelOpen() && (!cur || !document.contains(cur) || !visible(cur))){ cur = null; setCur(firstInTab() || activeTab()); } }, 400);
    return 'ok';
  }

  /* ---------------- the board ---------------- */
  function boardTargets(){
    var all = document.querySelectorAll('#live .game, #alsolive .al'), out = [];
    for (var i = 0; i < all.length; i++) if (visible(all[i])) out.push(all[i]);
    return out;
  }
  var lastOk = {el: null, t: 0}, hinted = false;
  function boardNav(dir){
    var lobby = document.querySelector('#empty.lobby .pane');
    var list = boardTargets();
    if (!list.length){
      if (lobby && (dir === 'left' || dir === 'right')) press(dir === 'left' ? 'ArrowLeft' : 'ArrowRight');
      return;
    }
    if (!cur || list.indexOf(cur) < 0){
      setCur(document.querySelector('#live .game.spot') || list[0]);
      if (!hinted){ hinted = true; toast('OK spotlights a game · OK twice: just that game', 3200); }
      return;
    }
    var next = best(list, cur, dir, false);
    if (next) setCur(next);
  }
  function boardOk(){
    var list = boardTargets();
    if (!cur || list.indexOf(cur) < 0){ boardNav('down'); return 'ok'; }
    var el = cur, now = Date.now();
    setCur(el);                                  // keeps the cursor from fading mid-press
    if (lastOk.el === el && now - lastOk.t < 500){
      lastOk = {el: null, t: 0};
      var ev;
      try { ev = new MouseEvent('dblclick', {bubbles: true}); }
      catch(e){ ev = document.createEvent('MouseEvents'); ev.initEvent('dblclick', true, true); }
      el.dispatchEvent(ev);
    } else {
      lastOk = {el: el, t: now};
      el.click();
    }
    // the board redraws its cards: find the same game again
    var id = el.getAttribute('data-g');
    setTimeout(function(){
      if (!id || panelOpen()) return;
      var again = document.querySelector('#live .game[data-g="' + id + '"]');
      if (again) setCur(again);
    }, 450);
    return 'ok';
  }
  function sig(){
    var l = $('live'); if (!l) return '';
    var s = l.className + '|';
    for (var i = 0; i < l.children.length; i++) s += l.children[i].className + ',';
    return s + '|' + document.body.className;
  }

  /* ---------------- what the app calls ---------------- */
  window.__tv = {
    nav: function(dir){
      if (!BOARD){ window.scrollBy(0, (dir === 'down' ? 1 : dir === 'up' ? -1 : 0) * innerHeight * 0.4); return 'ok'; }
      if (pick){
        if (dir === 'up' || dir === 'down'){ pick.i = Math.max(0, Math.min(pick.rows.length - 1, pick.i + (dir === 'down' ? 1 : -1))); hi(); }
        return 'ok';
      }
      if (num){ stepNum(dir === 'right' || dir === 'up' ? 1 : -1); return 'ok'; }
      if (panelOpen()){
        var a = document.activeElement;
        if (isText(a)) a.blur();
        panelNav(dir);
      } else boardNav(dir);
      return 'ok';
    },
    ok: function(){
      if (!BOARD) return 'ok';
      if (pick){ closePicker(true); return 'ok'; }
      if (num){ endNum(); return 'ok'; }
      return panelOpen() ? panelOk() : boardOk();
    },
    menu: function(){
      if (pick) closePicker(false);
      endNum();
      if (!BOARD) return 'ok';
      if (panelOpen()){ var c = $('sClose'); if (c) c.click(); clearCur(); }
      else {
        clearCur();
        var g = $('gear'); if (g) g.click();
        setTimeout(function(){ setCur(activeTab()); }, 150);
      }
      return 'ok';
    },
    key: function(k){
      if (!BOARD) return 'ok';
      press(k);
      var d = $('sDelay');
      if ((k === '[' || k === ']') && d) toast('Delay ' + d.value + ' s');
      else if (k === 'r') toast('Refreshing');
      return 'ok';
    },
    back: function(){
      if (!BOARD) return 'none';
      if (pick){ closePicker(false); return 'handled'; }
      if (num){ endNum(); return 'handled'; }
      if (panelOpen()){
        var a = document.activeElement;
        if (isText(a)){ a.blur(); return 'handled'; }
        var c = $('sClose'); if (c) c.click();
        clearCur();
        return 'handled';
      }
      if (cur){ clearCur(); return 'handled'; }
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
