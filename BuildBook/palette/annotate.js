/* BuildBook annotation editor (palette/annotate.html).
 * Draws with BBAnnot (annot_draw.js) so the editor matches the exports. Annotations are stored in
 * normalized image coordinates (0..1); all hit-testing / handles work in CSS pixels of the displayed picture.
 *
 * Python -> JS:  fusionJavaScriptHandler.handle('load', {kind, id, title, image, width, height, annotations, defaults})
 * JS -> Python:  ready {} | save {kind, id, annotations} | defaults {style} | refresh {} | close {}
 */
(function () {
  'use strict';

  var A = window.BBAnnot;
  function $(id) { return document.getElementById(id); }
  var stage = $('stage'), wrap = $('wrap'), img = $('pic'), cv = $('cv'), ctx = cv.getContext('2d');
  var txtEd = $('txtEd'), numEd = $('numEd');

  var SWATCHES = ['#d9463e', '#0696d7', '#3c9a2e', '#d99a1e', '#222222', '#ffffff'];
  var SIX = ['color', 'weight', 'dashed', 'size', 'bold', 'box'];
  var RELEVANT = { heads: ['arrow', 'line'], fill: ['rect', 'ellipse'], fillOpacity: ['rect', 'ellipse'],
                   size: ['text', 'callout'],
                   bold: ['text'], box: ['text'], leader: ['text'] };
  var TOOL_TYPE = { arrow: 'arrow', line: 'line', rect: 'rect', ellipse: 'ellipse', text: 'text', callout: 'callout' };
  var HINTS = {
    select: 'Click to select (Shift = add), drag empty space to box-select, drag handles to reshape, double-click text / balloon to edit. Del removes, Ctrl+Z undoes.',
    arrow: 'Drag to draw an arrow (Shift = 45 degree steps).',
    line: 'Drag to draw a line (Shift = 45 degree steps).',
    rect: 'Drag to draw a rectangle (Shift = square).',
    ellipse: 'Drag to draw an ellipse (Shift = circle).',
    text: 'Click to place text. Ctrl+Enter or click elsewhere to finish, Esc cancels. Turn on Leader for an arrow.',
    callout: 'Click to drop a numbered balloon, or drag from the thing to point at to where the balloon should sit. Esc = Select.'
  };

  // ------------------------------------------------------------------ state
  var kind = '', picId = null, title = '';
  var anns = [], sel = [];
  var tool = 'select';
  var defaults = { color: '#d9463e', weight: 3, dashed: false, size: 28, bold: false, box: false };
  var extra = { arrowHeads: 'end', lineHeads: 'none', fill: false, fillOpacity: 0.15, leader: false };
  var loaded = false, loadTok = 0;
  var dw = 1, dh = 1, S = 1;                 // displayed size (CSS px), canvas px per CSS px
  var drag = null, editing = null, marquee = null;
  var clip = [], pasteN = 0;
  var hist = [], hpos = 0, lastKey = null, lastT = 0;
  var saveTimer = null, dirty = false, defTimer = null, clearTimer = null;

  function isNum(v) { return typeof v === 'number' && !isNaN(v); }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function uid() { return 'a' + Math.random().toString(36).slice(2, 9) + Date.now().toString(36).slice(-4); }
  function copy(o) { return JSON.parse(JSON.stringify(o)); }

  function send(action, payload) {
    try {
      if (window.adsk && window.adsk.fusionSendData) window.adsk.fusionSendData(action, JSON.stringify(payload || {}));
    } catch (e) { /* ignore */ }
  }

  // ------------------------------------------------------------------ saving & history
  function cleanList() {
    return anns.filter(function (a) { return !(a.type === 'text' && !String(a.text || '').trim()); })
               .map(function (a) { return a; });
  }
  function saveNow() {
    clearTimeout(saveTimer);
    if (!dirty || !kind) return;
    dirty = false;
    send('save', { kind: kind, id: picId, annotations: cleanList() });
  }
  function scheduleSave() { dirty = true; clearTimeout(saveTimer); saveTimer = setTimeout(saveNow, 300); }
  function flushAll() { if (editing) closeEditor(true); saveNow(); }

  function snap() { return JSON.stringify(anns); }
  function resetHistory() { hist = [snap()]; hpos = 0; lastKey = null; updateButtons(); }
  function commit(key) {
    var s = snap(), now = Date.now();
    if (s === hist[hpos]) { scheduleSave(); return; }
    if (key && key === lastKey && now - lastT < 700 && hpos === hist.length - 1 && hpos > 0) hist[hpos] = s;
    else {
      hist.length = hpos + 1; hist.push(s); hpos++;
      if (hist.length > 300) { hist.shift(); hpos--; }
    }
    lastKey = key || null; lastT = now;
    updateButtons(); scheduleSave();
  }
  function restore(s) {
    var ids = sel.map(function (a) { return a.id; });
    anns = JSON.parse(s);
    sel = anns.filter(function (a) { return ids.indexOf(a.id) >= 0; });
    lastKey = null; updateButtons(); syncStyle(); render(); scheduleSave();
  }
  function undo() { if (hpos > 0) { hpos--; restore(hist[hpos]); } }
  function redo() { if (hpos < hist.length - 1) { hpos++; restore(hist[hpos]); } }
  function updateButtons() {
    $('btnUndo').disabled = hpos <= 0;
    $('btnRedo').disabled = hpos >= hist.length - 1;
    $('btnDelete').disabled = !sel.length;
  }

  // ------------------------------------------------------------------ geometry (CSS px)
  function P(a) {
    return { x1: a.x1 * dw, y1: a.y1 * dh, x2: (isNum(a.x2) ? a.x2 : a.x1) * dw, y2: (isNum(a.y2) ? a.y2 : a.y1) * dh };
  }
  function unit() { return dw / 1000; }
  function textPx(a) {
    var b = A.textBox(ctx, a, cv.width, cv.height);
    return { x: b.x / S, y: b.y / S, w: b.w / S, h: b.h / S, pad: b.pad / S };
  }
  function calloutR(a) { return 0.8 * (isNum(a.size) ? a.size : 28) * unit(); }
  function hasLeader(a) {
    var p = P(a);
    if (a.type === 'callout') return Math.hypot(p.x2 - p.x1, p.y2 - p.y1) > calloutR(a);
    if (a.type === 'text') return !!a.leader;
    return false;
  }
  function segDist(px, py, ax, ay, bx, by) {
    var dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
    var t = l2 ? clamp(((px - ax) * dx + (py - ay) * dy) / l2, 0, 1) : 0;
    return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
  }
  function textLeaderStart(a, p) {
    var t = textPx(a);
    return [clamp(p.x2, t.x, t.x + t.w), clamp(p.y2, t.y, t.y + t.h)];
  }
  function bounds(a, withLeader) {
    var p = P(a), U = unit(), w = isNum(a.weight) ? a.weight : 3, pad = w * U / 2;
    var x0, y0, x1, y1;
    if (a.type === 'text') {
      var t = textPx(a);
      x0 = t.x; y0 = t.y; x1 = t.x + t.w; y1 = t.y + t.h;
      if (withLeader && a.leader) { x0 = Math.min(x0, p.x2); y0 = Math.min(y0, p.y2); x1 = Math.max(x1, p.x2); y1 = Math.max(y1, p.y2); }
      pad = a.box ? pad : 0;
    } else if (a.type === 'callout') {
      var r = calloutR(a);
      x0 = p.x1 - r; y0 = p.y1 - r; x1 = p.x1 + r; y1 = p.y1 + r;
      if (withLeader && hasLeader(a)) { x0 = Math.min(x0, p.x2); y0 = Math.min(y0, p.y2); x1 = Math.max(x1, p.x2); y1 = Math.max(y1, p.y2); }
    } else {
      x0 = Math.min(p.x1, p.x2); x1 = Math.max(p.x1, p.x2); y0 = Math.min(p.y1, p.y2); y1 = Math.max(p.y1, p.y2);
      if (a.type === 'arrow' || (a.type === 'line' && a.heads && a.heads !== 'none')) pad = Math.max(pad, 0.45 * (4 * w + 6) * U);
    }
    return { x0: x0 - pad, y0: y0 - pad, x1: x1 + pad, y1: y1 + pad };
  }

  function hit(a, x, y) {
    var p = P(a), tol = 6, w = (isNum(a.weight) ? a.weight : 3) * unit() / 2;
    if (a.type === 'arrow' || a.type === 'line') return segDist(x, y, p.x1, p.y1, p.x2, p.y2) <= tol + w;
    if (a.type === 'rect') {
      var x0 = Math.min(p.x1, p.x2), x1 = Math.max(p.x1, p.x2), y0 = Math.min(p.y1, p.y2), y1 = Math.max(p.y1, p.y2);
      var inside = x >= x0 && x <= x1 && y >= y0 && y <= y1;
      if (a.fill && inside) return true;
      var d = inside ? Math.min(x - x0, x1 - x, y - y0, y1 - y) : Math.hypot(Math.max(x0 - x, 0, x - x1), Math.max(y0 - y, 0, y - y1));
      return d <= tol + w;
    }
    if (a.type === 'ellipse') {
      var cx = (p.x1 + p.x2) / 2, cy = (p.y1 + p.y2) / 2;
      var rx = Math.max(Math.abs(p.x2 - p.x1) / 2, 1), ry = Math.max(Math.abs(p.y2 - p.y1) / 2, 1);
      var nx = (x - cx) / rx, ny = (y - cy) / ry, dd = Math.sqrt(nx * nx + ny * ny);
      if (a.fill && dd <= 1) return true;
      if (dd < 1e-6) return Math.min(rx, ry) <= tol + w;
      var g = Math.sqrt(Math.pow(nx / rx, 2) + Math.pow(ny / ry, 2)) / dd;
      return Math.abs(dd - 1) / g <= tol + w;
    }
    if (a.type === 'text') {
      var t = textPx(a);
      if (x >= t.x - 3 && x <= t.x + t.w + 3 && y >= t.y - 3 && y <= t.y + t.h + 3) return true;
      if (a.leader) { var s = textLeaderStart(a, p); return segDist(x, y, s[0], s[1], p.x2, p.y2) <= tol; }
      return false;
    }
    if (a.type === 'callout') {
      if (Math.hypot(x - p.x1, y - p.y1) <= calloutR(a) + 3) return true;
      if (hasLeader(a)) return segDist(x, y, p.x1, p.y1, p.x2, p.y2) <= tol || Math.hypot(x - p.x2, y - p.y2) <= tol;
    }
    return false;
  }
  function topHit(x, y) {
    for (var i = anns.length - 1; i >= 0; i--) if (hit(anns[i], x, y)) return anns[i];
    return null;
  }

  // handles: [{id, x, y, ghost}] in CSS px
  function handlesOf(a) {
    var p = P(a), h = [];
    if (a.type === 'arrow' || a.type === 'line') {
      h.push({ id: 'p1', x: p.x1, y: p.y1 }, { id: 'p2', x: p.x2, y: p.y2 });
    } else if (a.type === 'rect' || a.type === 'ellipse') {
      [[0, 0], [1, 0], [0, 1], [1, 1]].forEach(function (c) {
        h.push({ id: 'c' + c[0] + c[1], x: c[0] ? p.x2 : p.x1, y: c[1] ? p.y2 : p.y1 });
      });
    } else if (a.type === 'text') {
      if (a.leader) h.push({ id: 'p2', x: p.x2, y: p.y2 });
    } else if (a.type === 'callout') {
      if (hasLeader(a)) h.push({ id: 'p2', x: p.x2, y: p.y2 });
      else { var r = calloutR(a) * 0.7071; h.push({ id: 'p2', x: p.x1 + r, y: p.y1 + r, ghost: true }); }
    }
    return h;
  }
  function hitHandle(a, x, y) {
    var hs = handlesOf(a);
    for (var i = hs.length - 1; i >= 0; i--) if (Math.hypot(hs[i].x - x, hs[i].y - y) <= 8) return hs[i];
    return null;
  }
  function setHandle(a, h, nx, ny) {
    if (h.id === 'p1') { a.x1 = nx; a.y1 = ny; }
    else if (h.id === 'p2') { a.x2 = nx; a.y2 = ny; }
    else { a[h.id.charAt(1) === '1' ? 'x2' : 'x1'] = nx; a[h.id.charAt(2) === '1' ? 'y2' : 'y1'] = ny; }
  }

  // ------------------------------------------------------------------ annotations
  function nextNumber() {
    var m = 0;
    anns.forEach(function (a) { if (a.type === 'callout' && isNum(a.number)) m = Math.max(m, a.number); });
    return m + 1;
  }
  function newAnn(type, x1, y1, x2, y2) {
    var a = { id: uid(), type: type, x1: x1, y1: y1, x2: x2, y2: y2,
              color: defaults.color, weight: defaults.weight, dashed: defaults.dashed };
    if (type === 'arrow') a.heads = extra.arrowHeads;
    else if (type === 'line') a.heads = extra.lineHeads;
    else if (type === 'rect' || type === 'ellipse') { a.fill = extra.fill; a.fillOpacity = extra.fillOpacity; }
    else if (type === 'text') { a.text = ''; a.size = defaults.size; a.bold = defaults.bold; a.box = defaults.box; a.leader = extra.leader; }
    else if (type === 'callout') { a.size = defaults.size; a.number = nextNumber(); }
    return a;
  }
  function ensureLeader(a) {
    var p = P(a);
    if (isNum(a.x2) && isNum(a.y2) && Math.hypot(p.x2 - p.x1, p.y2 - p.y1) > 6) return;
    var t = textPx(a);
    a.x2 = clamp(a.x1 + (t.w / 2) / dw + 0.05, 0, 1);
    a.y2 = clamp(a.y1 + t.h / dh + 0.08, 0, 1);
  }
  function coordsOf(list) {
    var c = [];
    list.forEach(function (a) { ['x1', 'x2'].forEach(function (k) { if (isNum(a[k])) c.push(['x', a[k]]); });
                                ['y1', 'y2'].forEach(function (k) { if (isNum(a[k])) c.push(['y', a[k]]); }); });
    return c;
  }
  function clampDelta(list, dx, dy) {   // keep every anchor inside the picture
    var minx = 1, maxx = 0, miny = 1, maxy = 0;
    coordsOf(list).forEach(function (c) {
      if (c[0] === 'x') { minx = Math.min(minx, c[1]); maxx = Math.max(maxx, c[1]); }
      else { miny = Math.min(miny, c[1]); maxy = Math.max(maxy, c[1]); }
    });
    if (minx > maxx) return [0, 0];
    return [clamp(dx, -minx, 1 - maxx), clamp(dy, -miny, 1 - maxy)];
  }
  function shift(a, dx, dy) {
    a.x1 += dx; a.y1 += dy;
    if (isNum(a.x2)) a.x2 += dx;
    if (isNum(a.y2)) a.y2 += dy;
  }

  function select(list) { sel = list; syncStyle(); updateButtons(); render(); }
  function deleteSelected() {
    if (!sel.length) return;
    anns = anns.filter(function (a) { return sel.indexOf(a) < 0; });
    sel = []; commit('delete'); syncStyle(); updateButtons(); render();
  }
  function duplicate(list, offset) {
    if (!list.length) return;
    var copies = copy(list), d = clampDelta(copies, offset, offset);
    var n = nextNumber();
    copies.forEach(function (c) {
      c.id = uid(); shift(c, d[0], d[1]);
      if (c.type === 'callout') c.number = n++;
      anns.push(c);
    });
    sel = copies.map(function (c) { return anns[anns.indexOf(c)]; });
    commit('dup'); syncStyle(); updateButtons(); render();
  }
  function renumber() {
    var cs = anns.filter(function (a) { return a.type === 'callout'; });
    if (!cs.length) return;
    cs.sort(function (p, q) { return p.y1 - q.y1; });
    var rows = [], tol = 0.03;
    cs.forEach(function (c) {
      var r = rows[rows.length - 1];
      if (r && Math.abs(c.y1 - r.y) < tol) r.items.push(c); else rows.push({ y: c.y1, items: [c] });
    });
    var n = 1;
    rows.forEach(function (r) {
      r.items.sort(function (p, q) { return p.x1 - q.x1; });
      r.items.forEach(function (c) { c.number = n++; });
    });
    commit('renumber'); render();
  }
  function clearAll() {
    if (!anns.length) return;
    anns = []; sel = []; commit('clear'); syncStyle(); updateButtons(); render();
  }

  // ------------------------------------------------------------------ rendering
  function plain(a) {       // the annotation being edited: hide its text (the textarea shows it)
    var c = {}, k;
    for (k in a) if (Object.prototype.hasOwnProperty.call(a, k)) c[k] = a[k];
    c.text = ''; c.box = false;
    return c;
  }
  function render() {
    if (!loaded) return;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, cv.width, cv.height);
    var list = anns.map(function (a) { return editing && editing.a === a && editing.kind === 'text' ? plain(a) : a; });
    A.draw(ctx, list, cv.width, cv.height);
    var accent = (getComputedStyle(document.documentElement).getPropertyValue('--accent') || '#0696d7').trim();
    ctx.save();
    ctx.scale(S, S);
    ctx.lineWidth = 1; ctx.setLineDash([4, 3]); ctx.strokeStyle = accent;
    sel.forEach(function (a) {
      var b = bounds(a, a.type === 'text' ? false : true);
      ctx.strokeRect(b.x0 - 3 + 0.5, b.y0 - 3 + 0.5, b.x1 - b.x0 + 6, b.y1 - b.y0 + 6);
    });
    ctx.setLineDash([]);
    if (sel.length === 1 && !editing) {
      handlesOf(sel[0]).forEach(function (h) {
        ctx.beginPath();
        if (h.id === 'p2' || h.id === 'p1') ctx.arc(h.x, h.y, 5, 0, 2 * Math.PI); else ctx.rect(h.x - 4, h.y - 4, 8, 8);
        ctx.fillStyle = h.ghost ? 'rgba(255,255,255,.6)' : '#ffffff'; ctx.fill();
        ctx.strokeStyle = accent; ctx.lineWidth = 1.5; ctx.stroke();
      });
    }
    if (marquee) {
      var x = Math.min(marquee.x0, marquee.x1), y = Math.min(marquee.y0, marquee.y1);
      var w = Math.abs(marquee.x1 - marquee.x0), h2 = Math.abs(marquee.y1 - marquee.y0);
      ctx.globalAlpha = 0.12; ctx.fillStyle = accent; ctx.fillRect(x, y, w, h2);
      ctx.globalAlpha = 1; ctx.lineWidth = 1; ctx.setLineDash([3, 3]); ctx.strokeStyle = accent; ctx.strokeRect(x + 0.5, y + 0.5, w, h2);
    }
    ctx.restore();
  }

  function layout() {
    if (!loaded) return;
    var aw = stage.clientWidth - 24, ah = stage.clientHeight - 24;
    if (aw < 10 || ah < 10) return;
    var iw = img.naturalWidth || 1, ih = img.naturalHeight || 1;
    var sc = Math.min(aw / iw, ah / ih, 6);
    dw = Math.max(1, Math.floor(iw * sc)); dh = Math.max(1, Math.floor(ih * sc));
    var dpr = window.devicePixelRatio || 1;
    wrap.style.width = dw + 'px'; wrap.style.height = dh + 'px';
    cv.width = Math.max(1, Math.round(dw * dpr)); cv.height = Math.max(1, Math.round(dh * dpr));
    S = cv.width / dw;
    wrap.classList.remove('hidden'); $('msg').classList.add('hidden');
    positionEditor();
    render();
  }

  // ------------------------------------------------------------------ inline editors
  function positionEditor() {
    if (!editing) return;
    var a = editing.a;
    if (editing.kind === 'text') {
      var U = unit(), size = (isNum(a.size) ? a.size : 28) * U, pad = a.box ? 0.3 * size : 0;
      a.text = txtEd.value;
      var t = textPx(a);
      txtEd.style.left = a.x1 * dw + 'px'; txtEd.style.top = a.y1 * dh + 'px';
      txtEd.style.fontSize = size + 'px'; txtEd.style.fontWeight = a.bold ? 'bold' : 'normal';
      txtEd.style.padding = pad + 'px'; txtEd.style.color = a.color || '#d9463e'; txtEd.style.caretColor = a.color || '#d9463e';
      txtEd.style.background = a.box ? '#ffffff' : 'transparent';
      txtEd.style.width = Math.max(t.w, size * 1.5 + 2 * pad) + 2 + 'px';
      txtEd.style.height = Math.max(t.h, 1.25 * size + 2 * pad) + 'px';
    } else {
      numEd.style.left = (a.x1 * dw - 26) + 'px'; numEd.style.top = (a.y1 * dh - 12) + 'px';
    }
  }
  function openTextEditor(a, isNew) {
    if (!loaded) return;
    closeEditor(true);
    editing = { kind: 'text', a: a, isNew: isNew, old: a.text || '' };
    txtEd.value = a.text || '';
    txtEd.classList.remove('hidden');
    positionEditor(); render();
    setTimeout(function () { if (editing && editing.a === a) { txtEd.focus(); txtEd.select(); } }, 0);
  }
  function openNumberEditor(a) {
    if (!loaded) return;
    closeEditor(true);
    editing = { kind: 'number', a: a, isNew: false, old: a.number };
    numEd.value = a.number == null ? '' : String(a.number);
    numEd.classList.remove('hidden');
    positionEditor(); render();
    setTimeout(function () { if (editing && editing.a === a) { numEd.focus(); numEd.select(); } }, 0);
  }
  function closeEditor(doCommit) {
    if (!editing) return;
    var e = editing, a = e.a;
    editing = null;                         // before hiding: hiding fires blur
    txtEd.classList.add('hidden'); numEd.classList.add('hidden');
    if (e.kind === 'text') {
      var text = txtEd.value;
      if (doCommit) {
        a.text = text;
        if (!text.trim()) { anns = anns.filter(function (x) { return x !== a; }); sel = sel.filter(function (x) { return x !== a; }); if (!e.isNew) commit('text'); }
        else if (e.isNew) { commit('create'); }
        else if (text !== e.old) commit('text');
        if (e.isNew && !text.trim()) { /* nothing was created */ }
      } else {
        if (e.isNew) { anns = anns.filter(function (x) { return x !== a; }); sel = sel.filter(function (x) { return x !== a; }); }
        else a.text = e.old;
      }
    } else if (doCommit) {
      var n = parseInt(numEd.value, 10);
      if (!isNaN(n) && n !== a.number) { a.number = n; commit('number'); }
    }
    syncStyle(); updateButtons(); render();
  }
  txtEd.addEventListener('input', function () { positionEditor(); render(); });
  txtEd.addEventListener('blur', function () { if (editing && editing.kind === 'text') closeEditor(true); });
  txtEd.addEventListener('keydown', function (e) {
    e.stopPropagation();
    if (e.key === 'Escape') { e.preventDefault(); closeEditor(false); }
    else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); closeEditor(true); }
  });
  numEd.addEventListener('blur', function () { if (editing && editing.kind === 'number') closeEditor(true); });
  numEd.addEventListener('keydown', function (e) {
    e.stopPropagation();
    if (e.key === 'Escape') { e.preventDefault(); closeEditor(false); }
    else if (e.key === 'Enter') { e.preventDefault(); closeEditor(true); }
  });

  // ------------------------------------------------------------------ pointer input
  function ptr(e) {
    var r = cv.getBoundingClientRect();
    var x = (e.clientX - r.left) * (dw / (r.width || dw)), y = (e.clientY - r.top) * (dh / (r.height || dh));
    return { x: x, y: y, nx: clamp(x / dw, 0, 1), ny: clamp(y / dh, 0, 1) };
  }
  function constrain(p0, p, type, shiftKey) {     // returns CSS px end point
    if (!shiftKey) return { x: p.x, y: p.y };
    var dx = p.x - p0.x, dy = p.y - p0.y;
    if (type === 'arrow' || type === 'line') {
      var len = Math.hypot(dx, dy), ang = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4);
      return { x: p0.x + Math.cos(ang) * len, y: p0.y + Math.sin(ang) * len };
    }
    var m = Math.max(Math.abs(dx), Math.abs(dy));
    return { x: p0.x + (dx < 0 ? -m : m), y: p0.y + (dy < 0 ? -m : m) };
  }

  function onDown(e) {
    if (e.button !== 0 || !loaded) return;
    if (editing) closeEditor(true);
    try { cv.setPointerCapture(e.pointerId); } catch (x) { /* ignore */ }
    var p = ptr(e);
    if (tool !== 'select') { drag = { mode: 'create', p0: p, temp: null, moved: false }; e.preventDefault(); return; }
    // handles of a single selection
    if (sel.length === 1) {
      var h = hitHandle(sel[0], p.x, p.y);
      if (h) { drag = { mode: 'handle', a: sel[0], h: h, moved: false, before: snap() }; e.preventDefault(); return; }
    }
    var a = topHit(p.x, p.y);
    if (a) {
      if (e.shiftKey) {
        var i = sel.indexOf(a);
        if (i >= 0) sel = sel.filter(function (x) { return x !== a; }); else sel = sel.concat([a]);
        syncStyle(); updateButtons(); render();
        drag = null; e.preventDefault(); return;
      }
      var collapse = null;
      if (sel.indexOf(a) < 0) select([a]); else if (sel.length > 1) collapse = a;
      drag = { mode: 'move', p0: p, moved: false, collapse: collapse,
               origs: sel.map(function (x) { return { a: x, x1: x.x1, y1: x.y1, x2: x.x2, y2: x.y2 }; }) };
    } else {
      var keep = e.shiftKey ? sel.slice() : [];
      if (!e.shiftKey && sel.length) select([]);
      drag = { mode: 'marquee', keep: keep, p0: p };
      marquee = { x0: p.x, y0: p.y, x1: p.x, y1: p.y };
    }
    e.preventDefault();
  }

  function onMove(e) {
    if (!loaded) return;
    var p = ptr(e);
    if (!drag) { hover(p); return; }
    if (drag.mode === 'create') {
      var dist = Math.hypot(p.x - drag.p0.x, p.y - drag.p0.y);
      if (dist >= 4) drag.moved = true;
      if (tool === 'text') return;
      var q = constrain(drag.p0, p, tool, e.shiftKey);
      var qx = clamp(q.x / dw, 0, 1), qy = clamp(q.y / dh, 0, 1);
      if (!drag.temp) {
        if (!drag.moved) return;
        if (tool === 'callout') drag.temp = newAnn('callout', qx, qy, drag.p0.nx, drag.p0.ny);
        else drag.temp = newAnn(tool, drag.p0.nx, drag.p0.ny, qx, qy);
        anns.push(drag.temp);
      } else if (tool === 'callout') { drag.temp.x1 = qx; drag.temp.y1 = qy; }
      else { drag.temp.x2 = qx; drag.temp.y2 = qy; }
      render();
    } else if (drag.mode === 'move') {
      var d = clampDelta(drag.origs.map(function (o) { return o; }), (p.x - drag.p0.x) / dw, (p.y - drag.p0.y) / dh);
      if (!drag.moved && Math.hypot(p.x - drag.p0.x, p.y - drag.p0.y) < 3) return;
      drag.moved = true;
      drag.origs.forEach(function (o) {
        o.a.x1 = o.x1 + d[0]; o.a.y1 = o.y1 + d[1];
        if (isNum(o.x2)) o.a.x2 = o.x2 + d[0];
        if (isNum(o.y2)) o.a.y2 = o.y2 + d[1];
      });
      render();
    } else if (drag.mode === 'handle') {
      drag.moved = true;
      var nx = p.nx, ny = p.ny;
      if (e.shiftKey && (drag.a.type === 'arrow' || drag.a.type === 'line') && (drag.h.id === 'p1' || drag.h.id === 'p2')) {
        var o = drag.h.id === 'p2' ? { x: drag.a.x1 * dw, y: drag.a.y1 * dh } : { x: drag.a.x2 * dw, y: drag.a.y2 * dh };
        var c = constrain(o, p, 'line', true); nx = clamp(c.x / dw, 0, 1); ny = clamp(c.y / dh, 0, 1);
      }
      setHandle(drag.a, drag.h, nx, ny);
      render();
    } else if (drag.mode === 'marquee') {
      marquee.x1 = p.x; marquee.y1 = p.y; render();
    }
  }

  function onUp(e) {
    if (!drag) return;
    var d = drag; drag = null;
    try { cv.releasePointerCapture(e.pointerId); } catch (x) { /* ignore */ }
    var p = ptr(e);
    if (d.mode === 'create') {
      var dist = Math.hypot(p.x - d.p0.x, p.y - d.p0.y);
      if (d.temp) {
        if (dist < 4) { anns = anns.filter(function (a) { return a !== d.temp; }); render(); return; }
        sel = [d.temp]; commit('create');
        if (tool !== 'callout') setTool('select', true);
        sel = [d.temp]; syncStyle(); updateButtons(); render();
        return;
      }
      if (dist >= 4) return;                                    // ignored tiny/odd drags
      if (tool === 'text') {
        var t = topHit(d.p0.x, d.p0.y);
        if (t && t.type === 'text') { sel = [t]; openTextEditor(t, false); syncStyle(); return; }
        var a = newAnn('text', d.p0.nx, d.p0.ny, d.p0.nx, d.p0.ny);
        anns.push(a); sel = [a];
        if (a.leader) ensureLeader(a);
        syncStyle(); updateButtons();
        openTextEditor(a, true);
      } else if (tool === 'callout') {
        var c = newAnn('callout', d.p0.nx, d.p0.ny, d.p0.nx, d.p0.ny);
        anns.push(c); sel = [c]; commit('create'); syncStyle(); updateButtons(); render();
      }
    } else if (d.mode === 'move') {
      if (d.moved) commit('move');
      else if (d.collapse) select([d.collapse]);
      render();
    } else if (d.mode === 'handle') {
      commit('reshape'); render();
    } else if (d.mode === 'marquee') {
      var m = marquee; marquee = null;
      if (Math.abs(m.x1 - m.x0) > 3 || Math.abs(m.y1 - m.y0) > 3) {
        var rx0 = Math.min(m.x0, m.x1), rx1 = Math.max(m.x0, m.x1), ry0 = Math.min(m.y0, m.y1), ry1 = Math.max(m.y0, m.y1);
        var inside = anns.filter(function (a) {
          var b = bounds(a, true);
          return b.x0 <= rx1 && b.x1 >= rx0 && b.y0 <= ry1 && b.y1 >= ry0;
        });
        var out = d.keep.slice();
        inside.forEach(function (a) { if (out.indexOf(a) < 0) out.push(a); });
        select(out);
      } else { render(); }
    }
  }
  function onCancel() {
    if (!drag) return;
    if (drag.temp) anns = anns.filter(function (a) { return a !== drag.temp; });
    if (drag.mode === 'move') drag.origs.forEach(function (o) { o.a.x1 = o.x1; o.a.y1 = o.y1; o.a.x2 = o.x2; o.a.y2 = o.y2; });
    if (drag.mode === 'handle') { var s = JSON.parse(drag.before); anns = s; sel = []; }
    drag = null; marquee = null; render();
  }
  function hover(p) {
    var c = 'default';
    if (tool !== 'select') c = 'crosshair';
    else {
      if (sel.length === 1 && hitHandle(sel[0], p.x, p.y)) c = 'pointer';
      else if (topHit(p.x, p.y)) c = 'move';
    }
    if (cv.style.cursor !== c) cv.style.cursor = c;
  }
  cv.addEventListener('pointerdown', onDown);
  cv.addEventListener('pointermove', onMove);
  cv.addEventListener('pointerup', onUp);
  cv.addEventListener('pointercancel', onCancel);
  cv.addEventListener('dblclick', function (e) {
    if (!loaded || tool !== 'select') return;
    var p = ptr(e), a = topHit(p.x, p.y);
    if (!a) return;
    sel = [a]; syncStyle(); updateButtons();
    if (a.type === 'text') openTextEditor(a, false);
    else if (a.type === 'callout') openNumberEditor(a);
    e.preventDefault();
  });
  stage.addEventListener('pointerdown', function (e) {       // grey area around the picture
    if (e.target === stage && sel.length) select([]);
  });

  // ------------------------------------------------------------------ tools
  function setTool(t, keepSel) {
    if (editing) closeEditor(true);
    tool = t;
    var bs = document.querySelectorAll('.tool');
    for (var i = 0; i < bs.length; i++) bs[i].classList.toggle('on', bs[i].getAttribute('data-tool') === t);
    if (t !== 'select' && !keepSel) sel = [];
    $('hint').textContent = HINTS[t];
    cv.style.cursor = t === 'select' ? 'default' : 'crosshair';
    syncStyle(); updateButtons(); render();
  }
  Array.prototype.forEach.call(document.querySelectorAll('.tool'), function (b) {
    b.addEventListener('click', function () { setTool(b.getAttribute('data-tool')); });
  });

  // ------------------------------------------------------------------ style bar
  function setOn(el, on) { el.classList.toggle('on', !!on); el.setAttribute('aria-pressed', on ? 'true' : 'false'); }
  function setOff(el, off) { el.classList.toggle('off', !!off); }

  function context() {
    if (sel.length) return sel.map(function (a) { return a.type; });
    if (tool === 'select') return ['arrow', 'line', 'rect', 'ellipse', 'text', 'callout'];
    return [TOOL_TYPE[tool]];
  }
  function relevant(prop, types) {
    return types.some(function (t) { return RELEVANT[prop].indexOf(t) >= 0; });
  }
  function syncStyle() {
    var ref = sel[0], types = context();
    function v(prop, fallback) { return ref && ref[prop] !== undefined ? ref[prop] : fallback; }
    var color = String(v('color', defaults.color)).toLowerCase();
    var weight = v('weight', defaults.weight), size = v('size', defaults.size);
    var headsDefault = tool === 'line' ? extra.lineHeads : extra.arrowHeads;
    if (ref && ref.type === 'line' && ref.heads === undefined) headsDefault = 'none';
    var heads = v('heads', headsDefault);
    var swatches = document.querySelectorAll('.swatch');
    for (var i = 0; i < swatches.length; i++) swatches[i].classList.toggle('on', swatches[i].getAttribute('data-color') === color);
    if (/^#[0-9a-f]{6}$/.test(color)) $('colorCustom').value = color;
    $('weight').value = Math.min(12, Math.max(1, weight));      // the slider covers 1-12; the box any weight
    if (document.activeElement !== $('weightNum')) $('weightNum').value = weight;
    setOn($('dashed'), v('dashed', defaults.dashed));
    if (document.activeElement !== $('size')) $('size').value = size;
    setOn($('bold'), v('bold', defaults.bold)); setOn($('box'), v('box', defaults.box));
    $('heads').value = heads;
    setOn($('fill'), v('fill', extra.fill)); setOn($('leader'), v('leader', extra.leader));
    setOff($('fHeads'), !relevant('heads', types)); setOff($('fill'), !relevant('fill', types));
    setOff($('fOpacity'), !relevant('fillOpacity', types));
    if (document.activeElement !== $('fillOpacity')) $('fillOpacity').value = Math.round(100 * v('fillOpacity', extra.fillOpacity));
    setOff($('fSize'), !relevant('size', types)); setOff($('bold'), !relevant('bold', types));
    setOff($('box'), !relevant('box', types)); setOff($('leader'), !relevant('leader', types));
    $('styleTarget').textContent = sel.length ? (sel.length === 1 ? 'Style of the selected ' + sel[0].type : 'Style of ' + sel.length + ' selected')
                                              : 'Default style for new annotations';
  }
  function scheduleDefaults() {
    clearTimeout(defTimer);
    defTimer = setTimeout(function () {
      var s = {}; SIX.forEach(function (k) { s[k] = defaults[k]; });
      send('defaults', { style: s });
    }, 150);
  }
  function applyStyle(prop, val) {
    if (sel.length) {
      var n = 0;
      sel.forEach(function (a) {
        if (RELEVANT[prop] && RELEVANT[prop].indexOf(a.type) < 0) return;
        a[prop] = val;
        if (prop === 'leader' && val) ensureLeader(a);
        n++;
      });
      if (n) { commit('style:' + prop); render(); }
    } else if (SIX.indexOf(prop) >= 0) { defaults[prop] = val; scheduleDefaults(); }
    else if (prop === 'heads') extra[tool === 'line' ? 'lineHeads' : 'arrowHeads'] = val;
    else extra[prop] = val;
    syncStyle();
  }

  SWATCHES.forEach(function (c) {
    var b = document.createElement('button');
    b.className = 'swatch'; b.style.background = c; b.setAttribute('data-color', c); b.title = c;
    b.addEventListener('click', function () { applyStyle('color', c); });
    $('swatches').appendChild(b);
  });
  $('colorCustom').addEventListener('input', function (e) { applyStyle('color', e.target.value.toLowerCase()); });
  $('weight').addEventListener('input', function (e) { applyStyle('weight', parseInt(e.target.value, 10) || 3); });
  $('weightNum').addEventListener('input', function (e) {
    var n = parseFloat(e.target.value);
    if (!isNaN(n) && n >= 0.5 && n <= 100) applyStyle('weight', n);
  });
  $('weightNum').addEventListener('keydown', function (e) { e.stopPropagation(); });
  $('dashed').addEventListener('click', function () { applyStyle('dashed', !$('dashed').classList.contains('on')); });
  $('heads').addEventListener('change', function (e) { applyStyle('heads', e.target.value); });
  $('fill').addEventListener('click', function () { applyStyle('fill', !$('fill').classList.contains('on')); });
  $('fillOpacity').addEventListener('input', function (e) {
    var n = parseFloat(e.target.value);
    if (isNaN(n) || n < 5 || n > 100) return;
    applyStyle('fillOpacity', n / 100);
    if (!$('fill').classList.contains('on')) applyStyle('fill', true);   // an opacity means you want a fill
  });
  $('fillOpacity').addEventListener('keydown', function (e) { e.stopPropagation(); });
  $('size').addEventListener('input', function (e) {
    var n = parseFloat(e.target.value);
    if (!isNaN(n) && n >= 10 && n <= 400) applyStyle('size', n);
  });
  $('size').addEventListener('change', function (e) {
    var n = clamp(parseFloat(e.target.value) || 28, 10, 120); e.target.value = n; applyStyle('size', n);
  });
  $('bold').addEventListener('click', function () { applyStyle('bold', !$('bold').classList.contains('on')); });
  $('box').addEventListener('click', function () { applyStyle('box', !$('box').classList.contains('on')); });
  $('leader').addEventListener('click', function () { applyStyle('leader', !$('leader').classList.contains('on')); });

  // ------------------------------------------------------------------ actions
  $('btnUndo').addEventListener('click', undo);
  $('btnRedo').addEventListener('click', redo);
  $('btnDelete').addEventListener('click', deleteSelected);
  $('btnRenumber').addEventListener('click', renumber);
  $('btnClear').addEventListener('click', function () {
    var b = $('btnClear');
    if (b.classList.contains('armed')) { clearTimeout(clearTimer); b.classList.remove('armed'); b.textContent = 'Clear all'; clearAll(); return; }
    b.classList.add('armed'); b.textContent = 'Click again to clear';
    clearTimer = setTimeout(function () { b.classList.remove('armed'); b.textContent = 'Clear all'; }, 3000);
  });
  $('btnRefresh').addEventListener('click', function () { flushAll(); send('refresh', {}); });
  $('btnDone').addEventListener('click', function () { flushAll(); send('close', {}); });
  window.addEventListener('beforeunload', function () { try { flushAll(); } catch (e) { /* ignore */ } });

  // ------------------------------------------------------------------ keyboard
  document.addEventListener('keydown', function (e) {
    var t = e.target, tag = t && t.tagName;
    if (tag === 'TEXTAREA' || tag === 'SELECT' || (tag === 'INPUT' && t.type !== 'range' && t.type !== 'checkbox')) return;
    var k = e.key, ctrl = e.ctrlKey || e.metaKey, lower = k.length === 1 ? k.toLowerCase() : k;
    var handled = true;
    if (ctrl && !e.altKey) {
      if (lower === 'z' && !e.shiftKey) undo();
      else if (lower === 'y' || (lower === 'z' && e.shiftKey)) redo();
      else if (lower === 'c') { if (sel.length) { clip = copy(sel); pasteN = 0; } }
      else if (lower === 'v') { if (clip.length) { pasteN++; duplicateFrom(clip, 0.02 * pasteN); } }
      else if (lower === 'd') duplicate(sel, 0.02);
      else if (lower === 'a') { select(anns.slice()); }
      else handled = false;
    } else if (k === 'Delete' || k === 'Backspace') deleteSelected();
    else if (k === 'Escape') { if (tool !== 'select') setTool('select'); else if (sel.length) select([]); else handled = false; }
    else if (k.indexOf('Arrow') === 0 && sel.length) {
      var step = (e.shiftKey ? 10 : 1), dx = 0, dy = 0;
      if (k === 'ArrowLeft') dx = -step / dw; else if (k === 'ArrowRight') dx = step / dw;
      else if (k === 'ArrowUp') dy = -step / dh; else dy = step / dh;
      var d = clampDelta(sel, dx, dy);
      sel.forEach(function (a) { shift(a, d[0], d[1]); });
      commit('nudge'); render();
    } else if (!e.altKey && !ctrl && 'valretc'.indexOf(lower) >= 0 && lower.length === 1) {
      setTool({ v: 'select', a: 'arrow', l: 'line', r: 'rect', e: 'ellipse', t: 'text', c: 'callout' }[lower]);
    } else handled = false;
    if (handled) e.preventDefault();
  });
  function duplicateFrom(list, offset) {      // paste: list is detached data
    var copies = copy(list), d = clampDelta(copies, offset, offset), n = nextNumber();
    copies.forEach(function (c) {
      c.id = uid(); shift(c, d[0], d[1]);
      if (c.type === 'callout') c.number = n++;
      anns.push(c);
    });
    sel = copies; commit('paste'); syncStyle(); updateButtons(); render();
  }

  // ------------------------------------------------------------------ load from Python
  function load(d) {
    flushAll();
    var tok = ++loadTok;
    closeEditor(false);
    kind = d.kind || 'step'; picId = d.id; title = d.title || '';
    var df = d.defaults || {};
    SIX.forEach(function (k) { if (df[k] !== undefined && df[k] !== null) defaults[k] = df[k]; });
    anns = (d.annotations || []).filter(function (a) { return a && a.type; }).map(function (a) {
      var c = copy(a); if (!c.id) c.id = uid(); return c;
    });
    sel = []; drag = null; marquee = null;
    dirty = false; clearTimeout(saveTimer);
    resetHistory();
    $('title').textContent = 'Editing ' + title;
    $('hint').textContent = HINTS[tool];
    loaded = false; wrap.classList.add('hidden');
    $('msg').textContent = 'Loading the picture...'; $('msg').classList.remove('hidden');
    syncStyle();
    img.onload = function () { if (tok !== loadTok) return; loaded = true; layout(); };
    img.onerror = function () { if (tok !== loadTok) return; $('msg').textContent = 'The picture could not be loaded.'; };
    img.src = d.image || '';
  }
  window.fusionJavaScriptHandler = {
    handle: function (action, data) {
      try {
        if (action === 'load') load(typeof data === 'string' ? JSON.parse(data) : data);
      } catch (err) {
        if (window.console) console.error('annotate: ' + action + ' failed', err);
      }
      return 'OK';
    }
  };

  window.addEventListener('resize', layout);
  if (window.ResizeObserver) new ResizeObserver(function () { layout(); }).observe(stage);

  $('hint').textContent = HINTS.select;
  syncStyle(); updateButtons();

  // Fusion injects `adsk` after load; wait for it before announcing.
  var readyTimer = setInterval(function () {
    if (window.adsk && window.adsk.fusionSendData) { clearInterval(readyTimer); send('ready'); }
  }, 100);
  if (window.adsk && window.adsk.fusionSendData) { clearInterval(readyTimer); send('ready'); }
})();
