/* Hole & Thread Callouts panel. Plain ES5. Talks to HoleThreadCallouts.py through
 * adsk.fusionSendData(action, json); receives window.fusionJavaScriptHandler.handle(action, json).
 * Also does the export stitching (the Python side sends 'stitch', we reply 'stitched'). */
(function () {
  'use strict';

  var state = null;
  var armedDelete = null, armedTimer = null;
  var busyTimer = null, busyLimit = null;
  var stateSeq = 0;
  var SIZES = ['M2.5', 'M3', 'M4', 'M5', 'M6', 'M8', 'M10'];
  var FALLBACK_COLORS = { 'M2': '#009E73', 'M2.5': '#0072B2', 'M3': '#F0E442', 'M4': '#56B4E9', 'M5': '#E69F00',
                          'M6': '#CC79A7', 'M8': '#D55E00', 'M10': '#0072B2' };
  var NO_BUSY = ['ready', 'settings', 'stitched'];

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function svg(inner, cls) {
    var s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    s.setAttribute('viewBox', '0 0 16 16');
    if (cls) s.setAttribute('class', cls);
    s.innerHTML = inner;
    return s;
  }

  var ICONS = {
    goto: '<circle cx="8" cy="8" r="2.2"/><path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z"/>',
    update: '<path d="M2.5 5.5V3h2.5M13.5 10.5V13H11"/><path d="M3.2 6.5A5 5 0 0 1 12 4.5M12.8 9.5A5 5 0 0 1 4 11.5"/>',
    edit: '<path d="M2.5 13.5l.6-3L10.5 3l2.5 2.5-7.4 7.4z"/><path d="M9 4.5L11.5 7"/>',
    up: '<path d="M4 10l4-4 4 4"/>',
    down: '<path d="M4 6l4 4 4-4"/>',
    del: '<path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5"/>',
    wire: '<path d="M2.5 5.5L8 3l5.5 2.5v5L8 13l-5.5-2.5zM2.5 5.5L8 8l5.5-2.5M8 8v5"/>',
    shaded: '<path d="M2.5 5.5L8 3l5.5 2.5v5L8 13l-5.5-2.5z"/><path d="M2.5 5.5L8 8v5l-5.5-2.5z" fill="currentColor" stroke="none" opacity=".5"/>',
    image: '<rect x="2" y="3" width="12" height="10" rx="1"/><path d="M2.5 11l3.5-3.5 3 3 2-2 2.5 2.5"/>'
  };
  function icon(name) { return svg(ICONS[name]); }

  // ------------------------------------------------------------ messaging + busy

  function send(action, payload) {
    if (NO_BUSY.indexOf(action) < 0) setBusy(true);
    if (window.adsk && window.adsk.fusionSendData) {
      window.adsk.fusionSendData(action, JSON.stringify(payload || {}));
    }
  }

  function setBusy(on, label) {
    clearTimeout(busyTimer);
    clearTimeout(busyLimit);
    $('busy').classList.remove('on');
    $('export').disabled = false;
    if (on) {
      busyTimer = setTimeout(function () { $('busy').classList.add('on'); }, 150);
      busyLimit = setTimeout(function () { setBusy(false); }, 60000);
      if (label) $('export').disabled = true;
    }
  }

  function showError(msg) {
    var e = $('error');
    if (msg) { e.textContent = msg; e.classList.remove('hidden'); }
    else { e.textContent = ''; e.classList.add('hidden'); }
  }

  function settings(patch) { send('settings', patch); }

  // ------------------------------------------------------------ render

  function threadIds() {
    return state.views.filter(function (v) { return v.kind === 'thread'; }).map(function (v) { return v.id; });
  }

  function toolBtn(name, title, fn, opts) {
    var b = el('button', 'icon-btn' + (opts && opts.cls ? ' ' + opts.cls : ''));
    b.title = title;
    b.appendChild(icon(name));
    if (opts && opts.disabled) b.disabled = true;
    b.addEventListener('click', function (ev) { ev.stopPropagation(); fn(b); });
    return b;
  }

  // ------------------------------------------------------------ drag to reorder thread views
  // Mouse events (HTML5 drag and drop is unreliable in Fusion palettes): press on a thread view's
  // grip or name, move past a small threshold, drop between the other thread views.
  var vdrag = null;
  document.addEventListener('mousedown', function (e) {
    var row = e.target.closest && e.target.closest('#views .view.draggable');
    if (!row || e.button !== 0 || e.target.closest('.vtools, .thumb')) return;
    vdrag = { id: row.getAttribute('data-id'), row: row, y: e.clientY, moved: false, index: null };
    e.preventDefault();
  });
  document.addEventListener('mousemove', function (e) {
    if (!vdrag) return;
    if (!vdrag.moved && Math.abs(e.clientY - vdrag.y) < 4) return;
    vdrag.moved = true;
    vdrag.row.classList.add('dragging');
    var rows = Array.prototype.slice.call(document.querySelectorAll('#views .view.draggable'));
    var index = rows.length;
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i].getBoundingClientRect();
      if (e.clientY < r.top + r.height / 2) { index = i; break; }
    }
    vdrag.index = index;
    rows.forEach(function (r, i) {
      r.classList.toggle('drop-before', i === index);
      r.classList.toggle('drop-after', index === rows.length && i === rows.length - 1);
    });
  });
  document.addEventListener('mouseup', function () {
    if (!vdrag) return;
    var d = vdrag;
    vdrag = null;
    var rows = Array.prototype.slice.call(document.querySelectorAll('#views .view.draggable'));
    rows.forEach(function (r) { r.classList.remove('dragging', 'drop-before', 'drop-after'); });
    if (!d.moved || d.index === null) return;
    var ids = rows.map(function (r) { return r.getAttribute('data-id'); });
    var from = ids.indexOf(d.id);
    var to = d.index > from ? d.index - 1 : d.index;
    if (to === from) return;
    ids.splice(from, 1);
    ids.splice(to, 0, d.id);
    send('orderViews', { ids: ids });
  });

  function renderViews() {
    var host = $('views');
    host.innerHTML = '';
    var tids = threadIds();
    state.views.forEach(function (v) {
      var row = el('div', 'view' + (v.kind === 'thread' ? ' draggable' : ''));
      row.setAttribute('data-id', v.id);
      if (v.kind === 'thread') {
        var grip = el('span', 'grip', '⋮⋮');
        grip.title = 'Drag to reorder';
        row.appendChild(grip);
      } else {
        row.appendChild(el('span', 'grip none'));
      }
      var th = el('div', 'thumb');
      th.title = 'Go to this view';
      if (v.thumb) th.style.backgroundImage = 'url("' + v.thumb + (v.thumb.indexOf('?') < 0 ? '?v=' + stateSeq : '') + '")';
      else th.appendChild(icon('image'));
      th.style.cursor = 'pointer';
      th.addEventListener('click', function () { send('goTo', { id: v.id }); });
      row.appendChild(th);

      var main = el('div', 'vmain');
      main.appendChild(el('div', 'vname ellipsis', v.name));
      var tag = el('div', 'tag');
      tag.appendChild(icon(v.kind === 'shaded' ? 'shaded' : 'wire'));
      var bits = [v.kind === 'shaded' ? 'Shaded' : 'Wireframe'];
      if (v.annotations) bits.push(v.annotations + (v.annotations === 1 ? ' note' : ' notes'));
      tag.appendChild(el('span', null, bits.join(' · ')));
      main.appendChild(tag);
      if (!v.hasCamera) main.appendChild(el('div', 'nocam', 'No camera saved'));
      row.appendChild(main);

      var tools = el('div', 'vtools');
      tools.appendChild(toolBtn('goto', 'Go to this view', function () { send('goTo', { id: v.id }); }, { disabled: !v.hasCamera }));
      tools.appendChild(toolBtn('update', 'Use the current camera for this view', function () { send('setCamera', { id: v.id }); }));
      tools.appendChild(toolBtn('edit', 'Edit callouts', function () { send('edit', { id: v.id }); }));
      if (v.kind === 'thread') {
        var i = tids.indexOf(v.id);
        tools.appendChild(toolBtn('up', 'Move earlier', function () { send('moveView', { id: v.id, delta: -1 }); }, { disabled: i <= 0 }));
        tools.appendChild(toolBtn('down', 'Move later', function () { send('moveView', { id: v.id, delta: 1 }); }, { disabled: i >= tids.length - 1 }));
      }
      var armed = armedDelete === v.id;
      tools.appendChild(toolBtn('del', armed ? 'Click again to delete' : 'Delete view', function () {
        if (armedDelete === v.id) {
          armedDelete = null; clearTimeout(armedTimer);
          send('deleteView', { id: v.id });
        } else {
          armedDelete = v.id;
          clearTimeout(armedTimer);
          armedTimer = setTimeout(function () { armedDelete = null; if (state) renderViews(); }, 3000);
          renderViews();
        }
      }, { cls: 'danger' + (armed ? ' armed' : '') }));
      row.appendChild(tools);
      host.appendChild(row);
    });
    var hasShaded = state.views.some(function (v) { return v.kind === 'shaded'; });
    $('addShaded').textContent = hasShaded ? 'Update shaded view (current camera)' : 'Set shaded view (current camera)';
  }

  function sourceNote(sources) {
    var names = { thread: 'modelled', tapped: 'tapped', guess: 'guessed', manual: 'marked' };
    var out = [];
    Object.keys(sources || {}).forEach(function (k) {
      if (sources[k]) out.push(sources[k] + ' ' + (names[k] || k));
    });
    return out.join(', ');
  }

  function colorOf(label) {
    var c = state && state.settings && state.settings.colors;
    return (c && c[label]) || FALLBACK_COLORS[label] || '#888888';
  }

  function renderHoles() {
    var host = $('holes');
    host.innerHTML = '';
    if (!state.holes.length) {
      var e = el('div', 'empty');
      e.appendChild(el('b', null, 'No threaded holes found'));
      e.appendChild(el('span', null, 'Tick the tap-drill option below, or select holes in Fusion and mark them.'));
      host.appendChild(e);
    }
    state.holes.forEach(function (h) {
      var row = el('div', 'hole');
      if (h.kind === 'dowel') {
        var ic = svg('<use href="#dowel"/>', 'dowel-ico');
        ic.style.width = '18px'; ic.style.height = '18px';
        row.appendChild(ic);
      } else {
        var sw = el('button', 'sw');
        sw.style.background = h.color || colorOf(h.label);
        sw.title = 'Change colour';
        sw.addEventListener('click', function (ev) { ev.stopPropagation(); openPicker(sw, h.label, h.color); });
        row.appendChild(sw);
      }
      row.appendChild(el('span', 'hlabel', h.label));
      row.appendChild(el('span', 'hcount', String(h.count)));
      row.appendChild(el('span', 'hsrc', sourceNote(h.sources)));
      host.appendChild(row);
    });
    $('guess').checked = !!(state.settings && state.settings.guess);
  }

  function renderMark() {
    var host = $('markSizes');
    host.innerHTML = '';
    SIZES.forEach(function (s) {
      var b = el('button', 'btn small');
      b.setAttribute('data-mark', s);
      var d = el('span', 'dot');
      d.style.background = colorOf(s);
      b.appendChild(d);
      b.appendChild(document.createTextNode(s));
      host.appendChild(b);
    });
  }

  function setValue(input, v) {
    if (document.activeElement === input || v == null) return;
    input.value = v;
  }

  // Configured designs: a tab per configuration (each with its own views).
  function esc(t) { return String(t == null ? '' : t).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function renderConfigs() {
    var cfgs = state.configs || [];
    var nav = $('cfgTabs');
    nav.classList.toggle('hidden', !cfgs.length);
    nav.innerHTML = cfgs.map(function (c) {
      return '<button class="cfg-tab' + (c.active ? ' active' : '') + '" data-cfg="' + esc(c.id) + '" title="' +
        esc(c.name) + (c.views ? ' · ' + c.views + ' thread view' + (c.views === 1 ? '' : 's') : ' · no views yet') + '">' +
        esc(c.name) + (c.views ? ' <span class="cfg-n">' + c.views + '</span>' : '') + '</button>';
    }).join('');
    var active = cfgs.filter(function (c) { return c.active; })[0];
    var sources = cfgs.filter(function (c) { return !c.active && c.views; });
    var empty = active && !state.views.some(function (v) { return v.kind === 'thread'; });
    // Shown whenever another configuration has views: start from them, or (with views here) replace these.
    $('copyViews').classList.toggle('hidden', !(active && sources.length));
    $('copyLabel').textContent = empty ? 'Start from the views of' : 'Replace these views with';
    $('copyBtn').textContent = empty ? 'Copy' : 'Replace';
    $('copyBtn').title = empty ? "Copy that configuration's views (cameras, padding, callouts) here"
      : "Replace this configuration's views with that one's (cameras, padding, callouts); click twice";
    $('copyBtn').setAttribute('data-replace', empty ? '' : '1');
    $('copyFrom').innerHTML = sources.map(function (c) { return '<option value="' + esc(c.id) + '">' + esc(c.name) + '</option>'; }).join('');
    $('exportAll').classList.toggle('hidden', cfgs.filter(function (c) { return c.views; }).length < 1 || cfgs.length < 2);
  }

  function render() {
    stateSeq++;
    closePicker();
    setBusy(false);
    $('component').textContent = state.error ? '' : [state.component, state.document].filter(Boolean)
      .filter(function (x, i, a) { return a.indexOf(x) === i; }).join(' · ') || ' ';
    showError(state.error);
    var n = $('notice');
    if (state.notice) { n.textContent = state.notice; n.classList.remove('hidden'); }
    else n.classList.add('hidden');
    $('pane').classList.toggle('hidden', !!state.error && !state.views);
    if (state.error && !state.views) return;
    state.views = state.views || [];
    state.holes = state.holes || [];
    renderConfigs();
    renderViews();
    renderHoles();
    renderMark();
    var s = state.settings || {};
    setValue($('height'), s.height);
    setValue($('gap'), s.gap);
    var f = $('folder');
    if (s.folder) { f.textContent = s.folder; f.title = s.folder; f.classList.remove('hidden'); }
    else f.classList.add('hidden');
  }

  // ------------------------------------------------------------ colour picker

  function closePicker() { $('pop').classList.add('hidden'); $('pop').innerHTML = ''; }

  function openPicker(anchor, label, current) {
    var pop = $('pop');
    pop.innerHTML = '';
    var grid = el('div', 'swatches');
    var pal = (state && state.palette) || {};
    Object.keys(pal).forEach(function (name) {
      var b = el('button', 'sw');
      b.style.background = pal[name];
      b.title = name;
      b.addEventListener('click', function () { pick(pal[name]); });
      grid.appendChild(b);
    });
    pop.appendChild(grid);
    var cu = el('div', 'custom');
    cu.appendChild(el('span', null, 'Custom'));
    var inp = document.createElement('input');
    inp.type = 'color';
    inp.value = /^#[0-9a-f]{6}$/i.test(current || '') ? current : '#888888';
    inp.addEventListener('change', function () { pick(inp.value); });
    cu.appendChild(inp);
    pop.appendChild(cu);
    pop.classList.remove('hidden');
    var r = anchor.getBoundingClientRect();
    pop.style.left = Math.max(4, Math.min(r.left, window.innerWidth - 184)) + 'px';
    pop.style.top = Math.min(r.bottom + 4, Math.max(4, window.innerHeight - pop.offsetHeight - 4)) + 'px';

    function pick(hex) {
      var c = {}; c[label] = hex;
      closePicker();
      settings({ colors: c });
    }
  }

  document.addEventListener('click', function (ev) {
    if (!$('pop').contains(ev.target)) closePicker();
  });

  // ------------------------------------------------------------ wiring

  $('refresh').addEventListener('click', function () { send('refresh'); });
  $('reloadAddin').addEventListener('click', function () { send('reloadAddin'); });
  $('styleWire').addEventListener('click', function () { send('style', { kind: 'thread' }); });
  $('styleShaded').addEventListener('click', function () { send('style', { kind: 'shaded' }); });
  $('notice').addEventListener('click', function () { $('notice').classList.add('hidden'); });
  $('error').addEventListener('click', function () { if (state && state.error) return; showError(''); });
  $('addThread').addEventListener('click', function () { send('addView', { kind: 'thread' }); });
  $('addShaded').addEventListener('click', function () { send('addView', { kind: 'shaded' }); });
  $('guess').addEventListener('change', function (e) { settings({ guess: e.target.checked }); });
  $('height').addEventListener('change', function (e) {
    var v = Math.max(100, Math.min(8000, Math.round(parseFloat(e.target.value) || 0)));
    if (!v) return;
    e.target.value = v; settings({ height: v });
  });
  $('gap').addEventListener('change', function (e) {
    var v = Math.max(0, Math.min(500, Math.round(parseFloat(e.target.value))));
    if (isNaN(v)) return;
    e.target.value = v; settings({ gap: v });
  });
  $('cfgTabs').addEventListener('click', function (e) {
    var t = e.target.closest('[data-cfg]');
    if (t && !t.classList.contains('active')) { setBusy(true, 'config'); send('activateConfig', { id: t.getAttribute('data-cfg') }); }
  });
  var copyArmed = null;
  $('copyBtn').addEventListener('click', function () {
    var b = $('copyBtn'), from = $('copyFrom').value;
    if (!from) return;
    if (b.getAttribute('data-replace') && !copyArmed) {         // replacing views: confirm with a second click
      b.textContent = 'Click again to replace'; b.classList.add('danger');
      copyArmed = setTimeout(function () { copyArmed = null; b.classList.remove('danger'); if (state) renderConfigs(); }, 3000);
      return;
    }
    clearTimeout(copyArmed); copyArmed = null; b.classList.remove('danger');
    send('copyViews', { from: from });
  });
  $('exportAll').addEventListener('click', function () {
    showError('');
    setBusy(true, 'export');
    send('exportAll', {});
  });
  $('export').addEventListener('click', function () {
    showError('');
    setBusy(true, 'export');
    send('export', {});
    setBusy(true, 'export');
  });
  document.querySelector('.pane').addEventListener('click', function (ev) {
    var b = ev.target.closest ? ev.target.closest('[data-mark]') : null;
    if (b) send('mark', { as: b.getAttribute('data-mark') });
  });

  // ------------------------------------------------------------ stitching

  function loadImage(src) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      img.onload = function () { resolve(img); };
      img.onerror = function () { reject(new Error('Could not load a rendered view image.')); };
      img.src = src;
    });
  }

  function bytesToBase64(bytes) {
    var out = '', CH = 0x8000;
    for (var i = 0; i < bytes.length; i += CH) {
      out += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
    }
    return btoa(out);
  }

  // 24-bit BMP (BITMAPFILEHEADER + BITMAPINFOHEADER, bottom-up, BGR, rows padded to 4 bytes).
  function bmpDataUrl(canvas) {
    var w = canvas.width, h = canvas.height;
    if (w * h > 12e6) return null;
    var px = canvas.getContext('2d').getImageData(0, 0, w, h).data;
    var row = (w * 3 + 3) & ~3, size = 54 + row * h;
    var buf = new Uint8Array(size), dv = new DataView(buf.buffer);
    buf[0] = 0x42; buf[1] = 0x4d;
    dv.setUint32(2, size, true);
    dv.setUint32(10, 54, true);
    dv.setUint32(14, 40, true);
    dv.setInt32(18, w, true);
    dv.setInt32(22, h, true);
    dv.setUint16(26, 1, true);
    dv.setUint16(28, 24, true);
    dv.setUint32(34, row * h, true);
    dv.setInt32(38, 2835, true);
    dv.setInt32(42, 2835, true);
    for (var y = 0; y < h; y++) {
      var src = (h - 1 - y) * w * 4, dst = 54 + y * row;
      for (var x = 0; x < w; x++) {
        buf[dst++] = px[src + 2]; buf[dst++] = px[src + 1]; buf[dst++] = px[src]; src += 4;
      }
    }
    return 'data:image/bmp;base64,' + bytesToBase64(buf);
  }

  function stitch(job) {
    var views = job.views || [];
    if (!views.length) throw new Error('Nothing to stitch.');
    var H = Math.max(50, Math.round(job.height || 1000));
    var gap = Math.max(0, Math.round(job.gap == null ? 40 : job.gap));
    return Promise.all(views.map(function (v) { return loadImage(v.image); })).then(function (imgs) {
      // Size of each view at the target height; shrink everything if the row would be too wide.
      var dims = views.map(function (v, i) {
        var cropped = window.BBAnnot.cropCanvas(imgs[i], v.crop);      // (padding past the image filled)
        return { cropped: cropped, w: Math.max(1, Math.round(cropped.width * H / cropped.height)) };
      });
      var total = gap * (views.length - 1);
      dims.forEach(function (d) { total += d.w; });
      var k = total > 16000 ? 16000 / total : 1;
      var HH = Math.max(1, Math.round(H * k));
      if (k < 1) {
        gap = Math.round(gap * k); total = gap * (views.length - 1);
        dims.forEach(function (d) { d.w = Math.max(1, Math.round(d.w * k)); total += d.w; });
      }
      var out = document.createElement('canvas');
      out.width = total; out.height = HH;
      var octx = out.getContext('2d');
      octx.fillStyle = '#ffffff';
      octx.fillRect(0, 0, total, HH);
      var x = 0;
      views.forEach(function (v, i) {
        var d = dims[i];
        var c = document.createElement('canvas');
        c.width = d.w; c.height = HH;
        var ctx = c.getContext('2d');
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, d.w, HH);
        ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(d.cropped, 0, 0, d.cropped.width, d.cropped.height, 0, 0, d.w, HH);
        if (v.annotations && v.annotations.length) window.BBAnnot.draw(ctx, v.annotations, d.w, HH);
        octx.drawImage(c, x, 0);
        x += d.w + gap;
      });
      return out;
    }).then(function (canvas) {
      var png = canvas.toDataURL('image/png');
      var bmp = null;
      try { bmp = bmpDataUrl(canvas); } catch (e) { bmp = null; }
      send('stitched', { png: png, bmp: bmp });
      // Python normally pushes fresh state next; don't spin forever if it does not.
      clearTimeout(busyLimit);
      busyLimit = setTimeout(function () { setBusy(false); }, 5000);
    });
  }

  window.fusionJavaScriptHandler = {
    handle: function (action, data) {
      try {
        if (action === 'state') {
          state = JSON.parse(data);
          render();
        } else if (action === 'stitch') {
          var job = JSON.parse(data);
          setBusy(true, 'export');
          stitch(job).catch(function (e) {
            setBusy(false);
            showError('Stitching failed: ' + (e && e.message ? e.message : e));
          });
        }
      } catch (e) {
        setBusy(false);
        showError(e.message);
      }
      return 'OK';
    }
  };

  // Fusion injects `adsk` after load; wait for it before asking for state.
  (function ready(tries) {
    if (window.adsk && window.adsk.fusionSendData) send('ready');
    else if (tries < 50) setTimeout(function () { ready(tries + 1); }, 100);
  })(0);
})();
