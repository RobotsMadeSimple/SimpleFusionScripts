/* Browser+ panel. Python pushes `state` (all records, parts, checks, map
 * data, the focused part); this renders the tabs and sends actions back. */
(function () {
  'use strict';

  var state = null;
  var byId = {};              // record id -> record
  var activeRec = null;       // record highlighted in Fusion
  var expanded = {};          // record id -> details open
  var activeTab = 'part';
  var $ = function (id) { return document.getElementById(id); };

  var KIND_LABEL = { joint: 'Joint', asBuilt: 'As-built', constraint: 'Relation', rigidGroup: 'Rigid', motionLink: 'Link' };
  var NO_BUSY = ['highlight', 'selectPart', 'zoom', 'edit', 'ready', 'follow', 'mateView', 'clearMates',
                 'selectParts', 'featSelect', 'featEdit'];   // no state comes back for these
  var ICON = {
    zoom: '<svg viewBox="0 0 16 16"><circle cx="7" cy="7" r="4.5"/><path d="m10.5 10.5 3.5 3.5M5 7h4M7 5v4"/></svg>',
    rename: '<svg viewBox="0 0 16 16"><path d="M2.5 4.5h6M5.5 4.5v7M11.5 3v10M10 3h3M10 13h3"/></svg>',
    edit: '<svg viewBox="0 0 16 16"><path d="M10.5 2.5l3 3L6 13H3v-3z"/></svg>',
    eye: '<svg viewBox="0 0 16 16"><path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z"/><circle cx="8" cy="8" r="2"/></svg>',
    eyeOff: '<svg viewBox="0 0 16 16"><path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z"/><path d="M2.5 13.5l11-11"/></svg>',
    roll: '<svg viewBox="0 0 16 16"><path d="M2 12h12M9 4v8M6 7l3-3 3 3"/></svg>',
    del: '<svg viewBox="0 0 16 16"><path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5"/></svg>',
    more: '<svg viewBox="0 0 16 16"><path d="m4 6 4 4 4-4"/></svg>',
    less: '<svg viewBox="0 0 16 16"><path d="m4 10 4-4 4 4"/></svg>',
  };

  // ------------------------------------------------------------ messaging

  var busyTimer = null;
  function send(action, payload) {
    if (NO_BUSY.indexOf(action) < 0) {
      clearTimeout(busyTimer);
      busyTimer = setTimeout(function () { $('busy').classList.add('on'); }, 150);
    }
    if (window.adsk && window.adsk.fusionSendData) {
      window.adsk.fusionSendData(action, JSON.stringify(payload || {}));
    }
  }
  function doneBusy() { clearTimeout(busyTimer); $('busy').classList.remove('on'); }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function partName(path) {
    if (!path) return 'Ground / origin';
    var p = state.parts[path];
    return p ? p.name : path.split('+').pop();
  }

  function store(k, v) { try { localStorage.setItem('browserplus.' + k, v); } catch (e) { /* ignore */ } }
  function recall(k) { try { return localStorage.getItem('browserplus.' + k); } catch (e) { return null; } }

  // ------------------------------------------------------------ tabs

  var renderHooks = [], tabHooks = [];   // tree.js / bom.js (see window.BP below)

  function showTab(name) {
    var previous = activeTab;
    activeTab = name;
    document.body.setAttribute('data-tab', name);
    // The relationship view (see-through / hidden parts) belongs to the joint tabs: end it on Tree / BOM.
    if ((name === 'tree' || name === 'bom') && state && state.mateView && state.mateView.showing) send('clearMates');
    store('tab', name);
    Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (t) {
      t.classList.toggle('active', t.getAttribute('data-tab') === name);
    });
    Array.prototype.forEach.call(document.querySelectorAll('.pane'), function (p) {
      p.classList.toggle('active', p.getAttribute('data-pane') === name);
    });
    if (name === 'map' && state) renderMap();
    tabHooks.forEach(function (fn) { fn(name, previous); });
  }
  $('tabs').addEventListener('click', function (e) {
    var t = e.target.closest('.tab');
    if (t) showTab(t.getAttribute('data-tab'));
  });

  // ------------------------------------------------------------ render

  var renaming = false, renderLater = false;   // no redraw while a name is being typed
  function render() {
    doneBusy();
    if (renaming) { renderLater = true; return; }
    if (state.error) {
      $('error').textContent = state.error;
      $('error').classList.remove('hidden');
      return;
    }
    $('error').classList.add('hidden');
    var part = state.mode === 'part';
    document.body.setAttribute('data-mode', part ? 'part' : 'assembly');
    if (part && ['part', 'all', 'map'].indexOf(activeTab) >= 0) showTab('tree');
    byId = {};
    state.records.forEach(function (r) { byId[r.id] = r; });
    if (activeRec && !byId[activeRec]) activeRec = null;
    var s = state.summary;
    $('summary').innerHTML = s.total + ' total' +
      (s.error ? ' <span class="err">' + s.error + ' error' + (s.error > 1 ? 's' : '') + '</span>' : '') +
      (s.warning ? ' <span class="warn">' + s.warning + ' warning' + (s.warning > 1 ? 's' : '') + '</span>' : '');
    $('countAll').textContent = s.total;
    var issues = state.problems.length + state.floating.length + state.duplicates.length;
    var featProblems = part ? state.features.items.filter(function (i) { return i.health !== 'ok'; }) : [];
    if (part) {
      issues = featProblems.length;
      var nf = state.features.items.length;
      $('summary').textContent = state.component + ' · ' + nf + ' item' + (nf === 1 ? '' : 's');
    }
    $('countHealth').textContent = issues;
    $('countHealth').classList.toggle('bad', part ? featProblems.some(function (i) { return i.health === 'error'; })
                                                  : state.problems.length > 0);
    $('healthFeatures').innerHTML = featProblems.length ? featProblems.map(function (i) {
      return '<div class="feat-problem" data-feat="' + esc(i.id) + '"><span class="dot ' + i.health + '"></span>' +
        '<span><b>' + esc(i.name) + '</b><br><span class="sub">' + esc(i.message || i.health) + '</span></span>' +
        '<span class="sub">' + esc(i.type) + '</span></div>';
    }).join('') : '<div class="sub">No errors or warnings.</div>';
    $('follow').checked = !!state.follow;
    if (state.notice) {
      $('notice').textContent = state.notice;
      $('notice').classList.remove('hidden');
    }
    $('btnRollEnd').classList.toggle('hidden', !state.rolledBack);
    var mv = state.mateView || {};
    $('mateGhost').checked = !!mv.ghost;
    $('mateIsolate').checked = !!mv.isolate;
    document.querySelector('.mate-bar').classList.toggle('on', !!mv.showing);
    document.querySelector('.mate-bar .mate-label').textContent = mv.showing ? 'Showing ' + mv.showing : 'Relationship view';
    $('btnMateClear').classList.toggle('hidden', !mv.showing);
    renderPart();
    renderTypeFilter();
    renderAll();
    renderHealth();
    if (activeTab === 'map') renderMap();
    renderHooks.forEach(function (fn) {
      try { fn(state, activeTab); } catch (e) { console.error(e); $('error').textContent = e.message; $('error').classList.remove('hidden'); }
    });
  }

  // One joint / relationship row.
  function recRow(r, opts) {
    opts = opts || {};
    var sub = [r.type];
    if (opts.showParts) sub.push(r.parts.map(partName).join(' ↔ ') || 'Ground');
    if (r.context) sub.push('in ' + partName(r.context));
    if (r.locked) sub.push('locked');
    var open = !!expanded[r.id];
    var html = '<div class="rec' + (r.suppressed ? ' suppressed' : '') + (r.id === activeRec ? ' active' : '') +
      '" data-id="' + esc(r.id) + '">' +
      '<span class="dot ' + r.health + '" title="' + (r.health === 'ok' ? 'Healthy' : r.health) + '"></span>' +
      '<span class="kind ' + r.kind + '">' + KIND_LABEL[r.kind] + '</span>' +
      '<div class="rec-main"><div class="rec-name" title="Double-click (or F2) to rename">' + esc(r.name) + '</div>' +
        '<div class="rec-sub">' + esc(sub.join(' · ')) + '</div>' +
        (r.message ? '<div class="rec-msg ' + r.health + '">' + esc(r.message) + '</div>' : '') + '</div>' +
      '<span class="rec-tools">' +
        '<button class="icon-btn" data-act="zoom" title="Zoom to it">' + ICON.zoom + '</button>' +
        '<button class="icon-btn" data-act="rename" title="Rename (F2)">' + ICON.rename + '</button>' +
        (state.canEdit ? '<button class="icon-btn" data-act="edit" title="' +
          (r.kind === 'constraint' ? 'Edit: rolls the timeline to it so you can double-click its icon' : 'Edit in Fusion') +
          '">' + ICON.edit + '</button>' : '') +
        '<button class="icon-btn" data-act="suppress" title="' + (r.suppressed ? 'Unsuppress' : 'Suppress') + '">' +
          (r.suppressed ? ICON.eyeOff : ICON.eye) + '</button>' +
        '<button class="icon-btn" data-act="rollTo" title="Roll the timeline to it">' + ICON.roll + '</button>' +
        '<button class="icon-btn danger" data-act="delete" title="Delete">' + ICON.del + '</button>' +
      '</span>' +
      (r.details.length ? '<button class="icon-btn" data-act="details" title="Details">' + (open ? ICON.less : ICON.more) + '</button>' : '') +
      '</div>';
    if (open && r.details.length) {
      html += '<div class="rec-details">' + r.details.map(function (d) {
        // Part names in the colours the mated faces are drawn in (side one / side two).
        var sides = (d.parts || []).map(function (p, i) {
          return '<span class="side side' + (i % 2) + '">' + esc(partName(p)) + '</span>';
        }).join(' ↔ ');
        var bits = [d.kind, d.name].concat(d.offset ? [d.offset] : []);
        return '<div>' + esc(bits.join(' · ')) + (sides ? ' · ' + sides : '') +
          (d.suppressed ? ' (suppressed)' : '') + '</div>';
      }).join('') + '</div>';
    }
    return html;
  }

  function recList(ids, opts) {
    return ids.map(function (id) { return byId[id]; }).filter(Boolean).map(function (r) { return recRow(r, opts); }).join('');
  }

  function renderPart() {
    var f = state.focus;
    $('partEmpty').classList.toggle('hidden', !!f);
    $('partView').classList.toggle('hidden', !f);
    if (!f) return;
    $('partName').textContent = f.name;
    $('partComponent').textContent = f.component + (f.path.indexOf('+') >= 0 ? ' · ' + f.path : '');
    $('partGrounded').classList.toggle('hidden', !f.grounded);
    var html = '';
    if (!f.direct.length && !f.inherited.length) {
      html = '<div class="empty-state"><p>Nothing holds this part.</p><p class="sub">' +
        (f.grounded ? 'It is grounded.' : 'It isn\'t grounded either, so it can be dragged freely.') + '</p></div>';
    }
    f.direct.forEach(function (g) {
      html += group(g[0], g[1], true);
    });
    if (f.inherited.length) {
      html += '<div class="section-label">Held through a parent assembly</div>';
      f.inherited.forEach(function (g) { html += group(g[0], g[1], true, 'via '); });
    }
    $('partGroups').innerHTML = html;
  }

  function group(path, ids, linkable, prefix) {
    return '<div class="group"><div class="group-head">' +
      '<span class="name' + (linkable && path ? ' link' : '') + '" data-focus="' + esc(path) + '" title="' +
        (path ? 'Show what holds ' + esc(partName(path)) : '') + '">' + esc((prefix || '') + partName(path)) + '</span>' +
      '<span class="count">' + ids.length + '</span></div>' + recList(ids) + '</div>';
  }

  var TYPES = [];
  function renderTypeFilter() {
    var seen = {};
    state.records.forEach(function (r) { seen[r.type] = true; });
    var types = Object.keys(seen).sort();
    if (types.join('|') === TYPES.join('|')) return;
    TYPES = types;
    var cur = $('fType').value;
    $('fType').innerHTML = '<option value="">All types</option>' + types.map(function (t) {
      return '<option' + (t === cur ? ' selected' : '') + '>' + esc(t) + '</option>';
    }).join('');
  }

  function filtered() {
    var q = $('search').value.trim().toLowerCase();
    var kind = $('fKind').value, type = $('fType').value, status = $('fStatus').value;
    return state.records.filter(function (r) {
      if (kind && r.kind !== kind) return false;
      if (type && r.type !== type) return false;
      if (status === 'problems' && r.health === 'ok') return false;
      if (status === 'suppressed' && !r.suppressed) return false;
      if (status === 'active' && r.suppressed) return false;
      if (!q) return true;
      var hay = (r.name + ' ' + r.type + ' ' + r.parts.map(partName).join(' ') + ' ' + r.parts.join(' ')).toLowerCase();
      return q.split(/\s+/).every(function (w) { return hay.indexOf(w) >= 0; });
    });
  }

  function renderAll() {
    var list = filtered();
    $('allCount').textContent = list.length + ' of ' + state.records.length;
    var by = $('fGroup').value;
    if (!by) {
      $('allList').innerHTML = '<div class="list-box">' + list.map(function (r) { return recRow(r, { showParts: true }); }).join('') + '</div>';
      return;
    }
    var groups = {};
    list.forEach(function (r) {
      var key = by === 'pair' ? r.parts.map(partName).sort().join(' ↔ ') || 'Ground'
        : by === 'type' ? r.type : (r.context ? partName(r.context) : 'Top level');
      (groups[key] = groups[key] || []).push(r);
    });
    $('allList').innerHTML = Object.keys(groups).sort().map(function (key) {
      return '<div class="group-title">' + esc(key) + ' (' + groups[key].length + ')</div><div class="list-box">' +
        groups[key].map(function (r) { return recRow(r, { showParts: by !== 'pair' }); }).join('') + '</div>';
    }).join('');
  }

  function renderHealth() {
    $('healthProblems').innerHTML = state.problems.length
      ? '<div class="list-box">' + recList(state.problems, { showParts: true }) + '</div>'
      : '<div class="sub">No errors or warnings.</div>';
    $('healthFloating').innerHTML = state.floating.map(function (p) {
      return '<li data-focus="' + esc(p) + '"><span class="name">' + esc(partName(p)) + '</span><span class="sub">' + esc(p) + '</span></li>';
    }).join('');
    $('healthDuplicates').innerHTML = state.duplicates.length ? state.duplicates.map(function (d) {
      return '<div class="group-title">' + esc(d.pair.map(partName).join(' ↔ ')) + '</div><div class="list-box">' +
        recList(d.ids) + '</div>';
    }).join('') : '<div class="sub">None.</div>';
  }

  // ------------------------------------------------------------ record actions


  function armed(button, fn) {
    if (button.getAttribute('data-armed')) { button.removeAttribute('data-armed'); fn(); return; }
    var label = button.innerHTML;
    button.setAttribute('data-armed', '1');
    button.innerHTML = 'Sure?';
    setTimeout(function () {
      if (button.isConnected && button.getAttribute('data-armed')) { button.removeAttribute('data-armed'); button.innerHTML = label; }
    }, 3000);
  }

  function rename(nameEl, r) {
    if (!nameEl || renaming) return;
    renaming = true;
    var input = document.createElement('input');
    input.value = r.name;
    input.style.width = '100%';
    nameEl.replaceWith(input);
    input.focus();
    input.select();
    var done = false;
    function finish(commit) {
      if (done) return;
      done = true;
      renaming = false;
      renderLater = false;
      var v = input.value.trim();
      if (commit && v && v !== r.name) send('rename', { id: r.id, name: v }); else render();
    }
    input.addEventListener('blur', function () { finish(true); });
    input.addEventListener('keydown', function (e) {
      e.stopPropagation();
      if (e.key === 'Enter') finish(true);
      if (e.key === 'Escape') finish(false);
    });
    input.addEventListener('click', function (e) { e.stopPropagation(); });
  }

  document.addEventListener('click', function (e) {
    var focusEl = e.target.closest('[data-focus]');
    if (focusEl && focusEl.getAttribute('data-focus') !== null && !e.target.closest('.rec')) {
      var path = focusEl.getAttribute('data-focus');
      if (path) { send('focus', { path: path, select: true }); showTab('part'); }
      return;
    }
    var row = e.target.closest('.rec');
    if (!row) return;
    var id = row.getAttribute('data-id');
    var r = byId[id];
    var btn = e.target.closest('[data-act]');
    var act = btn && btn.getAttribute('data-act');
    if (!act) {
      activeRec = id;
      Array.prototype.forEach.call(document.querySelectorAll('.rec'), function (el) {
        el.classList.toggle('active', el.getAttribute('data-id') === id);
      });
      send('highlight', { id: id });
    }
    else if (act === 'zoom') send('zoom', { id: id });
    else if (act === 'edit') send('edit', { id: id });
    else if (act === 'suppress') send('suppress', { id: id, on: !r.suppressed });
    else if (act === 'rollTo') send('rollTo', { id: id });
    else if (act === 'delete') armed(btn, function () { send('delete', { id: id }); });
    else if (act === 'details') { expanded[id] = !expanded[id]; render(); }
    else if (act === 'rename') rename(row.querySelector('.rec-name'), r);
  });

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'F2' || !activeRec || !byId[activeRec]) return;
    var row = document.querySelector('.rec.active');
    if (row) { e.preventDefault(); rename(row.querySelector('.rec-name'), byId[activeRec]); }
  });

  document.addEventListener('dblclick', function (e) {
    var nameEl = e.target.closest('.rec-name');
    if (!nameEl) return;
    var r = byId[nameEl.closest('.rec').getAttribute('data-id')];
    if (r) rename(nameEl, r);
  });

  $('notice').addEventListener('click', function () { $('notice').classList.add('hidden'); });
  $('healthFeatures').addEventListener('click', function (e) {
    var row = e.target.closest('[data-feat]');
    if (row) send('featSelect', { ids: [row.getAttribute('data-feat')] });
  });
  $('btnRefresh').addEventListener('click', function () { send('refresh'); });
  $('btnRollEnd').addEventListener('click', function () { send('rollEnd'); });
  $('follow').addEventListener('change', function (e) { send('follow', { on: e.target.checked }); });
  $('btnPartZoom').addEventListener('click', function () { if (state.focus) send('zoom', { path: state.focus.path }); });
  $('btnPartSelect').addEventListener('click', function () { if (state.focus) send('selectPart', { path: state.focus.path }); });
  ['search', 'fKind', 'fType', 'fStatus', 'fGroup'].forEach(function (id) {
    $(id).addEventListener(id === 'search' ? 'input' : 'change', function () { if (state) renderAll(); });
  });

  // ------------------------------------------------------------ connection map

  var pos = {};               // node id -> {x, y}
  var view = null;            // svg viewBox {x, y, w, h}
  var mapKey = '';            // what the current layout is for

  function mapData() {
    var g = state.graph;
    var scope = $('mapScope').value;
    if (scope === 'all' || !state.focus) {
      if (scope !== 'all' && !state.focus) return null;
      return g;
    }
    // Two steps out from the focused part.
    var keep = {}; keep[state.focus.path] = true;
    for (var depth = 0; depth < 2; depth++) {
      var add = {};
      g.edges.forEach(function (e) {
        if (keep[e.source]) add[e.target] = true;
        if (keep[e.target]) add[e.source] = true;
      });
      Object.keys(add).forEach(function (k) { keep[k] = true; });
    }
    return {
      nodes: g.nodes.filter(function (n) { return keep[n.id]; }),
      edges: g.edges.filter(function (e) { return keep[e.source] && keep[e.target]; }),
    };
  }

  function layout(data, fresh) {
    var nodes = data.nodes, edges = data.edges, n = nodes.length;
    var index = {};
    nodes.forEach(function (node, i) {
      index[node.id] = i;
      if (fresh || !pos[node.id]) {
        var a = 2 * Math.PI * i / Math.max(n, 1);
        pos[node.id] = { x: Math.cos(a) * 100 + Math.random(), y: Math.sin(a) * 100 + Math.random() };
      }
    });
    if (state.focus && pos[state.focus.path] && fresh) pos[state.focus.path] = { x: 0, y: 0 };
    var P = nodes.map(function (node) { return pos[node.id]; });
    var ideal = 70, iterations = n > 150 ? 150 : 300;
    for (var it = 0; it < iterations; it++) {
      var temp = 20 * (1 - it / iterations) + 1;
      var dx = new Array(n).fill(0), dy = new Array(n).fill(0);
      for (var i = 0; i < n; i++) {
        for (var j = i + 1; j < n; j++) {
          var x = P[i].x - P[j].x, y = P[i].y - P[j].y;
          var d2 = x * x + y * y + 0.01, f = ideal * ideal / d2;
          dx[i] += x * f; dy[i] += y * f; dx[j] -= x * f; dy[j] -= y * f;
        }
      }
      edges.forEach(function (e) {
        var a = index[e.source], b = index[e.target];
        if (a === undefined || b === undefined || a === b) return;
        var x = P[a].x - P[b].x, y = P[a].y - P[b].y;
        var d = Math.sqrt(x * x + y * y) + 0.01, f = d / ideal;
        dx[a] -= x * f; dy[a] -= y * f; dx[b] += x * f; dy[b] += y * f;
      });
      for (i = 0; i < n; i++) {
        var len = Math.sqrt(dx[i] * dx[i] + dy[i] * dy[i]) + 0.01;
        var step = Math.min(len, temp);
        P[i].x += dx[i] / len * step;
        P[i].y += dy[i] / len * step;
      }
    }
  }

  function fit(data) {
    if (!data.nodes.length) { view = { x: -100, y: -100, w: 200, h: 200 }; return; }
    var xs = data.nodes.map(function (n) { return pos[n.id].x; }), ys = data.nodes.map(function (n) { return pos[n.id].y; });
    var minX = Math.min.apply(null, xs) - 60, maxX = Math.max.apply(null, xs) + 60;
    var minY = Math.min.apply(null, ys) - 40, maxY = Math.max.apply(null, ys) + 40;
    view = { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
  }

  function renderMap(relayout) {
    var data = mapData();
    var svg = $('map');
    $('mapEmpty').classList.toggle('hidden', !!data);
    svg.classList.toggle('hidden', !data);
    if (!data) return;
    var key = $('mapScope').value + '|' + (state.focus ? state.focus.path : '') + '|' +
      data.nodes.map(function (n) { return n.id; }).join(',') + '|' + data.edges.length;
    if (relayout || key !== mapKey) {
      layout(data, relayout || key.split('|').slice(0, 2).join('|') !== mapKey.split('|').slice(0, 2).join('|'));
      fit(data);
      mapKey = key;
    }
    var focus = state.focus && state.focus.path;
    var near = {};
    if (focus) {
      near[focus] = true;
      data.edges.forEach(function (e) {
        if (e.source === focus) near[e.target] = true;
        if (e.target === focus) near[e.source] = true;
      });
    }
    var edgeHtml = data.edges.map(function (e) {
      var a = pos[e.source], b = pos[e.target];
      var dim = focus && !(e.source === focus || e.target === focus);
      var cls = 'edge ' + e.kind + (e.health !== 'ok' ? ' ' + e.health : '') + (e.suppressed ? ' suppressed' : '') + (dim ? ' dim' : '');
      var r = byId[e.record];
      return '<line class="' + cls + '" data-rec="' + esc(e.record) + '" x1="' + a.x + '" y1="' + a.y + '" x2="' + b.x + '" y2="' + b.y + '">' +
        '<title>' + esc((r ? r.name + ' · ' + r.type : '')) + '</title></line>';
    }).join('');
    var nodeHtml = data.nodes.map(function (n) {
      var p = pos[n.id];
      var cls = 'node' + (n.grounded ? ' grounded' : '') + (n.floating ? ' floating' : '') + (n.id === '' ? ' ground' : '') +
        (n.id === focus ? ' focus' : '') + (focus && !near[n.id] ? ' dim' : '');
      var r = 6 + Math.min(n.degree, 8);
      var label = n.name.length > 22 ? n.name.slice(0, 21) + '…' : n.name;
      return '<g class="' + cls + '" data-node="' + esc(n.id) + '" transform="translate(' + p.x + ',' + p.y + ')">' +
        '<circle r="' + r + '"><title>' + esc(n.name + (n.id ? '\n' + n.id : '')) + '</title></circle>' +
        '<text x="' + (r + 3) + '" y="3">' + esc(label) + '</text></g>';
    }).join('');
    svg.setAttribute('viewBox', [view.x, view.y, view.w, view.h].join(' '));
    svg.innerHTML = '<g>' + edgeHtml + '</g><g>' + nodeHtml + '</g>';
  }

  // Map interaction: click a part / line, drag a part, drag to pan, wheel to zoom.
  var mapDrag = null;

  function svgPoint(e) {
    var svg = $('map'), r = svg.getBoundingClientRect();
    var scale = Math.max(view.w / r.width, view.h / r.height);
    var ox = view.x + (view.w - r.width * scale) / 2, oy = view.y + (view.h - r.height * scale) / 2;
    return { x: ox + (e.clientX - r.left) * scale, y: oy + (e.clientY - r.top) * scale, scale: scale };
  }

  $('map').addEventListener('mousedown', function (e) {
    if (e.button !== 0 || !view) return;
    var node = e.target.closest('[data-node]');
    var p = svgPoint(e);
    mapDrag = { node: node ? node.getAttribute('data-node') : null, start: p, clientX: e.clientX, clientY: e.clientY,
                view: { x: view.x, y: view.y }, moved: false };
    e.preventDefault();
  });
  document.addEventListener('mousemove', function (e) {
    if (!mapDrag) return;
    if (Math.abs(e.clientX - mapDrag.clientX) + Math.abs(e.clientY - mapDrag.clientY) > 3) mapDrag.moved = true;
    if (!mapDrag.moved) return;
    var scale = mapDrag.start.scale;
    if (mapDrag.node !== null) {
      var p = svgPoint(e);
      pos[mapDrag.node] = { x: p.x, y: p.y };
    } else {
      $('map').classList.add('panning');
      view.x = mapDrag.view.x - (e.clientX - mapDrag.clientX) * scale;
      view.y = mapDrag.view.y - (e.clientY - mapDrag.clientY) * scale;
    }
    renderMap();
  });
  document.addEventListener('mouseup', function (e) {
    if (!mapDrag) return;
    var d = mapDrag;
    mapDrag = null;
    $('map').classList.remove('panning');
    if (d.moved) return;
    if (d.node !== null) {
      if (d.node) send('focus', { path: d.node, select: true });
      return;
    }
    var line = e.target.closest && e.target.closest('[data-rec]');
    if (line) {
      activeRec = line.getAttribute('data-rec');
      send('highlight', { id: activeRec });
    }
  });
  $('map').addEventListener('wheel', function (e) {
    if (!view) return;
    e.preventDefault();
    var p = svgPoint(e);
    var k = e.deltaY > 0 ? 1.15 : 1 / 1.15;
    view = { x: p.x - (p.x - view.x) * k, y: p.y - (p.y - view.y) * k, w: view.w * k, h: view.h * k };
    renderMap();
  }, { passive: false });
  $('mapScope').addEventListener('change', function () { renderMap(true); });
  $('btnMapFit').addEventListener('click', function () { var d = mapData(); if (d) { fit(d); renderMap(); } });
  $('btnMapRelayout').addEventListener('click', function () { renderMap(true); });

  // ------------------------------------------------------------ shared with tree.js / bom.js

  window.BP = {
    send: send,
    esc: esc,
    partName: partName,
    store: store,
    recall: recall,
    armed: armed,
    ICON: ICON,
    state: function () { return state; },
    activeTab: function () { return activeTab; },
    showTab: showTab,
    // fn(state, activeTab) after every state push (only when state has no error).
    onRender: function (fn) { renderHooks.push(fn); },
    // fn(newTab, previousTab) whenever a tab is shown (also once at start-up).
    onTab: function (fn) { tabHooks.push(fn); },
    // Open a part on the Part tab ("what holds it") and select it in Fusion.
    focusPart: function (path) { send('focus', { path: path, select: true }); showTab('part'); },
    // While typing in an inline editor, hold back redraws (they'd wipe the input); end with editing(false).
    editing: function (on) {
      renaming = !!on;
      if (!on && renderLater && state) { renderLater = false; render(); }
    },
  };

  // ------------------------------------------------------------ boot

  window.fusionJavaScriptHandler = {
    handle: function (action, data) {
      try {
        if (action === 'state') { state = JSON.parse(data); render(); }
      } catch (e) {
        doneBusy();
        $('error').textContent = e.message;
        $('error').classList.remove('hidden');
      }
      return 'OK';
    }
  };

  function mateView() {
    var opts = { ghost: $('mateGhost').checked, isolate: $('mateIsolate').checked };
    store('mateGhost', opts.ghost ? '1' : '0');
    store('mateIsolate', opts.isolate ? '1' : '0');
    send('mateView', opts);
  }
  $('mateGhost').addEventListener('change', mateView);
  $('mateIsolate').addEventListener('change', mateView);
  $('btnMateClear').addEventListener('click', function () { send('clearMates'); });

  function boot() { showTab(recall('tab') || 'tree'); }
  window.addEventListener('load', boot);   // after tree.js / bom.js have registered
  (function ready(tries) {
    if (window.adsk && window.adsk.fusionSendData) {
      send('ready');
      send('mateView', { ghost: recall('mateGhost') !== '0', isolate: recall('mateIsolate') === '1' });
    }
    else if (tries < 50) setTimeout(function () { ready(tries + 1); }, 100);
  })(0);
})();
