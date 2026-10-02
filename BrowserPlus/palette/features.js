/* Browser+ part mode: the Tree tab shows one component's features in timeline order, with the sketches
 * each feature uses nested inside it, plus Browser+-only folders. Plain ES5, no dependencies.
 * Talks to Python only through window.BP (see app.js). Rows reuse tree.css (t-row ...) but carry
 * data-f (not data-k) so tree.js never reacts to them. */
(function () {
  'use strict';

  var BP = window.BP;
  var pane = document.getElementById('treePane');
  if (!BP || !pane) return;
  var esc = BP.esc;
  var SCOPE = 'features';

  var S = null;                 // last state
  var dirty = true, rendered = false;
  var comp = null;              // component the open/selection state belongs to
  var query = '', words = [];
  var open = {};                // key -> bool (explicit choices only)
  var sel = { ids: {}, folder: null, anchor: null };
  var creating = null;          // {parent: id|null} while the new-folder input is shown
  var model = null;             // {roots, fmap, imap, folders, total}
  var order = [];               // item ids in visible order (for shift-click ranges)
  var root, bar, body, headEl;
  var pendingDelete = null;
  var drag = null, ghost = null, zone = null, hot = null, justDragged = false;

  function svg(d) { return '<svg viewBox="0 0 16 16">' + d + '</svg>'; }
  var I = {
    caret: svg('<path d="m6 4 4 4-4 4"/>'),
    folder: svg('<path d="M1.5 4.5v8h13v-7H7.5L6 4H1.5z"/>'),
    folderPlus: svg('<path d="M1.5 4.5v8h7 M1.5 4.5V3h4l1.5 1.5h7V8 M12 10v4 M10 12h4"/>'),
    select: svg('<path d="M3.5 2l9 5-4 1.2L6.5 13z"/>'),
    suppress: svg('<circle cx="8" cy="8" r="5"/><path d="M4.5 11.5l7-7"/>'),
    expandAll: svg('<path d="m4 3.5 4 3 4-3 M4 9.5l4 3 4-3"/>'),
    collapseAll: svg('<path d="m4 6.5 4-3 4 3 M4 12.5l4-3 4 3"/>'),
    sketch: svg('<path d="M2 13.5 11.5 4l2.5 2.5L4.5 16z" transform="translate(0 -2)"/><path d="M2 13.5h4" stroke-dasharray="1.5 1.5"/>'),
    extrude: svg('<path d="M2.5 6.5 8 4l5.5 2.5v5L8 14l-5.5-2.5z M2.5 6.5 8 9l5.5-2.5 M8 9v5"/>'),
    revolve: svg('<path d="M8 2v12 M8 4.5c3.5 0 5.5 1.6 5.5 3.5S11.5 11.5 8 11.5"/><path d="M8 4.5C4.5 4.5 2.5 6 2.5 8s2 3.5 5.5 3.5" stroke-dasharray="1.5 1.5"/>'),
    fillet: svg('<path d="M2.5 2.5v11h11 M2.5 6.5a7 7 0 0 1 7 7"/>'),
    hole: svg('<rect x="2.5" y="2.5" width="11" height="11" rx="1"/><circle cx="8" cy="8" r="2.5"/>'),
    pattern: svg('<rect x="2" y="2" width="4" height="4"/><rect x="10" y="2" width="4" height="4"/><rect x="2" y="10" width="4" height="4"/><rect x="10" y="10" width="4" height="4"/>'),
    plane: svg('<path d="M1.5 11 4 4.5h10.5L12 11z"/>'),
    axis: svg('<path d="M2 14 14 2 M11 2h3v3"/>'),
    point: svg('<circle cx="8" cy="8" r="1.8"/><path d="M8 2v3 M8 11v3 M2 8h3 M11 8h3"/>'),
    feature: svg('<path d="M8 1.5 14 4.8v6.4L8 14.5 2 11.2V4.8z M2 4.8 8 8l6-3.2 M8 8v6.5"/>')
  };

  // ------------------------------------------------------------ helpers

  function saveOpen() { BP.store('featOpen:' + (comp || ''), JSON.stringify(open)); }
  function loadOpen() {
    try { open = JSON.parse(BP.recall('featOpen:' + (comp || '')) || '{}') || {}; } catch (e) { open = {}; }
  }
  function selCount() { return Object.keys(sel.ids).length; }
  function isPart() { return !!(S && S.mode === 'part'); }
  function humanType(t) { return String(t || '').replace(/([a-z0-9])([A-Z])/g, '$1 $2'); }
  function plural(n, w) { return n + ' ' + w + (n === 1 ? '' : 's'); }
  function bad(it) { return it.health === 'warning' || it.health === 'error'; }
  function cssq(s) { return (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/["\\]/g, '\\$&'); }

  function iconFor(it) {
    var t = String(it.type || '').toLowerCase();
    if (it.kind === 'sketch' || t.indexOf('sketch') >= 0) return I.sketch;
    if (it.kind === 'construction' || t.indexOf('construction') >= 0) {
      if (t.indexOf('axis') >= 0) return I.axis;
      if (t.indexOf('point') >= 0) return I.point;
      return I.plane;
    }
    if (t.indexOf('extrude') >= 0 || t.indexOf('press') >= 0 || t.indexOf('sweep') >= 0 || t.indexOf('loft') >= 0) return I.extrude;
    if (t.indexOf('revolve') >= 0) return I.revolve;
    if (t.indexOf('fillet') >= 0 || t.indexOf('chamfer') >= 0) return I.fillet;
    if (t.indexOf('hole') >= 0) return I.hole;
    if (t.indexOf('pattern') >= 0 || t.indexOf('mirror') >= 0) return I.pattern;
    return I.feature;
  }

  function nameOf(id) {
    var it = model && model.imap[id];
    return it ? it.name : String(id);
  }

  // ------------------------------------------------------------ model

  function build(st) {
    var F = st.features || {}, place = F.place || {}, folders = F.folders || [];
    var items = (F.items || []).slice().sort(function (a, b) { return (a.index || 0) - (b.index || 0); });
    var imap = {}, fold = {}, inFolder = { '': [] }, childFolders = {}, rootFolders = [], nestedRef = {}, fmap = {};
    items.forEach(function (it) { imap[it.id] = it; });
    folders.forEach(function (f) { fold[f.id] = f; inFolder[f.id] = []; });
    folders.forEach(function (f) {
      if (f.parent && fold[f.parent] && f.parent !== f.id) (childFolders[f.parent] = childFolders[f.parent] || []).push(f);
      else rootFolders.push(f);
    });
    items.forEach(function (it) {
      if (it.kind === 'feature') (it.sketches || []).forEach(function (s) { if (imap[s]) nestedRef[s] = 1; });
    });
    items.forEach(function (it) {
      var f = place[it.id];
      if (f == null || !fold[f]) f = '';
      if (it.kind === 'sketch' && it.used && nestedRef[it.id] && f === '') return;   // shown under its features only
      inFolder[f].push(it.id);
    });

    function itemNode(it) {
      var kids = [], seen = {};
      (it.sketches || []).forEach(function (s) {
        var sk = imap[s];
        if (!sk || seen[s] || s === it.id) return;
        seen[s] = 1;
        kids.push({ t: 'item', it: sk, key: 'n:' + it.id + ':' + s, nested: true, parent: it.id, kids: [] });
      });
      return { t: 'item', it: it, key: 'i:' + it.id, kids: kids };
    }
    function folderNode(f) {
      var subs = (childFolders[f.id] || []).map(folderNode);
      var own = inFolder[f.id].map(function (id) { return itemNode(imap[id]); });
      var n = { t: 'folder', id: f.id, f: f, kids: subs.concat(own), ids: [], all: {} };
      own.forEach(function (c) {
        n.ids.push(c.it.id); n.all[c.it.id] = 1;
        c.kids.forEach(function (k) { n.all[k.it.id] = 1; });
      });
      subs.forEach(function (s) {
        n.ids = n.ids.concat(s.ids);
        for (var k in s.all) n.all[k] = 1;
      });
      n.count = n.ids.length;
      n.bad = 0;
      for (var k2 in n.all) if (bad(imap[k2])) n.bad++;
      fmap[f.id] = n;
      return n;
    }
    var roots = rootFolders.map(folderNode).concat(inFolder[''].map(function (id) { return itemNode(imap[id]); }));
    return { roots: roots, fmap: fmap, imap: imap, folders: folders, total: items.length };
  }

  // ------------------------------------------------------------ search

  function matches(it) {
    var hay = ((it.name || '') + ' ' + (it.type || '') + ' ' + humanType(it.type)).toLowerCase();
    for (var k = 0; k < words.length; k++) if (hay.indexOf(words[k]) < 0) return false;
    return true;
  }
  function clone(n, key, val) { var c = {}; for (var k in n) c[k] = n[k]; c[key] = val; return c; }
  function filt(n) {
    if (n.t === 'item') {
      var own = matches(n.it);
      var kids = own ? n.kids : n.kids.filter(function (k) { return matches(k.it); });
      if (!own && !kids.length) return null;
      return clone(n, 'kids', kids);
    }
    var fk = filtList(n.kids);
    return fk.length ? clone(n, 'kids', fk) : null;
  }
  function filtList(list) {
    var out = [];
    list.forEach(function (n) { var r = filt(n); if (r) out.push(r); });
    return out;
  }

  // ------------------------------------------------------------ rendering

  function tb(act, title, icon, cls) {
    return '<button class="icon-btn' + (cls ? ' ' + cls : '') + '" data-act="' + act + '" title="' + esc(title) + '">' + icon + '</button>';
  }
  function folderOpen(id) {
    if (query) return true;
    if (creating && creating.parent === id) return true;
    var k = 'f:' + id;
    return k in open ? !!open[k] : true;
  }
  function itemOpen(id) { return query ? true : !!open['i:' + id]; }

  function newRow(depth) {
    return '<div class="t-row t-newrow" style="--d:' + depth + '"><span class="t-caret none"></span>' +
      '<span class="t-ico">' + I.folder + '</span>' +
      '<input class="t-newinput" value="New folder" maxlength="80" spellcheck="false" aria-label="Folder name"></div>';
  }

  function nodes(list, depth, h) {
    list.forEach(function (n) { if (n.t === 'folder') folderRow(n, depth, h); else itemRow(n, depth, h); });
  }

  function folderRow(n, depth, h) {
    var f = n.f, o = folderOpen(n.id);
    h.push('<div class="t-row t-folder' + (o ? ' open' : '') + (sel.folder === n.id ? ' sel' : '') +
      '" data-f="folder" data-id="' + esc(n.id) + '" data-key="f:' + esc(n.id) + '" style="--d:' + depth + '">' +
      '<span class="t-caret" data-act="toggle">' + I.caret + '</span>' +
      '<span class="t-eye-gap"></span>' +
      '<span class="t-ico">' + I.folder + '</span>' +
      '<span class="t-name">' + esc(f.name) + '</span>' +
      (n.bad ? '<span class="t-warn" title="' + esc(plural(n.bad, 'item') + ' with a warning or error') + '">' + n.bad + '</span>' : '') +
      '<span class="t-count">' + n.count + '</span>' +
      '<span class="t-tools">' +
        (n.ids.length ? tb('select', 'Select in Fusion', I.select) : '') +
        tb('sub', 'New sub-folder', I.folderPlus) + tb('rename', 'Rename (F2)', BP.ICON.rename) +
        tb('delete', 'Delete folder (its contents move up; nothing is deleted from the design)', BP.ICON.del, 'danger') +
      '</span></div>');
    if (!o) return;
    if (creating && creating.parent === n.id) h.push(newRow(depth + 1));
    else if (!n.kids.length && !query) h.push('<div class="t-row t-emptyrow" style="--d:' + (depth + 1) + '">Empty \u2014 drag features here</div>');
    nodes(n.kids, depth + 1, h);
  }

  function itemRow(n, depth, h) {
    var it = n.it, id = it.id, hasKids = n.kids.length > 0, o = hasKids && itemOpen(id);
    var hasVis = it.kind !== 'feature' && it.visible != null, vis = it.visible !== false;
    var isFeat = it.kind === 'feature';
    var type = humanType(it.type), nm = String(it.name || '');
    var showType = type && nm.toLowerCase().indexOf(type.toLowerCase()) < 0 && nm.toLowerCase().indexOf(String(it.type || '').toLowerCase()) < 0;
    var tip = it.rolledBack ? 'After the timeline marker' : (it.message || '');
    var cls = 't-row f-item' + (o ? ' open' : '') + (sel.ids[id] ? ' sel' : '') + (hasVis && !vis ? ' t-off' : '') +
      (it.suppressed ? ' f-supp' : '') + (it.rolledBack ? ' f-rolled' : '') + (n.nested ? ' f-nested' : '') +
      (it.health === 'error' ? ' f-err' : it.health === 'warning' ? ' f-wrn' : '');
    order.push(id);
    h.push('<div class="' + cls + '" data-f="item" data-id="' + esc(id) + '" data-key="' + esc(n.key) + '" style="--d:' + depth + '">' +
      (hasKids ? '<span class="t-caret" data-act="toggle">' + I.caret + '</span>' : '<span class="t-caret none"></span>') +
      (hasVis ? '<button class="t-eye" data-act="eye" title="' + (vis ? 'Hide ' : 'Show ') + esc(nm) + '">' +
        (vis ? BP.ICON.eye : BP.ICON.eyeOff) + '</button>' : '<span class="t-eye-gap"></span>') +
      '<span class="t-ico f-ico">' + iconFor(it) + '</span>' +
      '<span class="t-name" title="' + esc(tip || nm) + '">' + esc(nm) + '</span>' +
      (showType ? '<span class="f-type">' + esc(type) + '</span>' : '') +
      (bad(it) ? '<span class="f-health" title="' + esc(it.message || it.health) + '"></span>' : '') +
      (hasKids && !o ? '<span class="t-count" title="Sketches used">' + n.kids.length + '</span>' : '') +
      '<span class="t-tools">' +
        tb('select', 'Select in Fusion', I.select) +
        tb('rollTo', 'Roll the timeline to it', BP.ICON.roll) +
        (isFeat ? tb('suppress', it.suppressed ? 'Unsuppress' : 'Suppress', I.suppress) : '') +
        tb('rename', 'Rename (F2)', BP.ICON.rename) +
        tb('delItem', 'Delete from the design (click twice; Delete key works too)', BP.ICON.del, 'danger') +
      '</span></div>');
    if (o) nodes(n.kids, depth + 1, h);
  }

  function renderFeat() {
    if (!S) return;
    var part = isPart();
    root.classList.toggle('hidden', !part);
    if (!part) { dirty = false; return; }
    if (S.component !== comp) {
      comp = S.component;
      loadOpen();
      sel = { ids: {}, folder: null, anchor: null };
      creating = null;
      cancelDelete();
    }
    dirty = false; rendered = true;
    var scroll = pane.scrollTop;
    model = build(S);
    Object.keys(sel.ids).forEach(function (p) { if (!model.imap[p]) delete sel.ids[p]; });
    if (sel.folder && !model.fmap[sel.folder]) sel.folder = null;
    headEl.textContent = 'Features of ' + (S.component || 'this design');

    var list = query ? filtList(model.roots) : model.roots;
    order = [];
    var h = [];
    if (creating && creating.parent == null) h.push(newRow(0));
    nodes(list, 0, h);
    if (!model.total && !model.folders.length && !creating) {
      h = ['<div class="empty-state"><svg viewBox="0 0 16 16"><path d="M8 1.5 14 4.8v6.4L8 14.5 2 11.2V4.8z M2 4.8 8 8l6-3.2 M8 8v6.5"/></svg>' +
        '<p>No features yet</p><p class="sub">Features you model appear here in timeline order, with their sketches inside.</p></div>'];
    } else if (query && !list.length) {
      h = ['<div class="empty-state"><p>No features match</p></div>'];
    }
    body.innerHTML = h.join('');
    pane.scrollTop = scroll;
    if (creating) bindNewInput();
  }

  function updateSelection() {
    var rows = body.querySelectorAll('.t-row[data-f]');
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i], on;
      if (r.getAttribute('data-f') === 'folder') on = sel.folder === r.getAttribute('data-id');
      else on = !!sel.ids[r.getAttribute('data-id')];
      r.classList.toggle('sel', on);
    }
    // Nothing selected in the tree any more: the timeline goes back where it was before the click-rolls.
    if ((rolled || (S && S.featRolled)) && !selCount()) goHome();
  }

  // ------------------------------------------------------------ inline inputs

  function startCreate(parent) {
    if (creating) return;
    creating = { parent: parent };
    BP.editing(true);
    renderFeat();
    var inp = body.querySelector('.t-newinput');
    if (inp) inp.scrollIntoView({ block: 'nearest' });
  }

  function bindNewInput() {
    var inp = body.querySelector('.t-newinput');
    if (!inp) return;
    var done = false;
    inp.focus();
    inp.select();
    function finish(commit) {
      if (done) return;
      done = true;
      var c = creating, v = inp.value.trim();
      creating = null;
      BP.editing(false);
      if (commit && v && c) {
        BP.send('folderAdd', { scope: SCOPE, name: v, parent: c.parent, ids: Object.keys(sel.ids) });
        if (c.parent) { open['f:' + c.parent] = true; saveOpen(); }
      }
      renderFeat();
    }
    inp.addEventListener('keydown', function (e) {
      e.stopPropagation();
      if (e.key === 'Enter') { e.preventDefault(); finish(true); }
      else if (e.key === 'Escape') finish(false);
    });
    inp.addEventListener('blur', function () { finish(true); });
  }

  function startRename(row) {
    var kind = row.getAttribute('data-f'), id = row.getAttribute('data-id');
    var cur = kind === 'folder' ? (model.fmap[id] && model.fmap[id].f.name) : (model.imap[id] && model.imap[id].name);
    var nameEl = row.querySelector('.t-name');
    if (cur == null || !nameEl) return;
    BP.editing(true);
    var input = document.createElement('input');
    input.value = cur;
    input.className = 't-renameinput';
    input.maxLength = 120;
    nameEl.replaceWith(input);
    input.focus();
    input.select();
    var done = false;
    function finish(commit) {
      if (done) return;
      done = true;
      var v = input.value.trim();
      BP.editing(false);
      if (commit && v && v !== cur) {
        if (kind === 'folder') BP.send('folderRename', { scope: SCOPE, id: id, name: v });
        else BP.send('featRename', { id: id, name: v });
      } else renderFeat();
    }
    input.addEventListener('keydown', function (e) {
      e.stopPropagation();
      if (e.key === 'Enter') { e.preventDefault(); finish(true); }
      else if (e.key === 'Escape') finish(false);
    });
    input.addEventListener('blur', function () { finish(true); });
    input.addEventListener('click', function (e) { e.stopPropagation(); });
    input.addEventListener('dblclick', function (e) { e.stopPropagation(); });
    input.addEventListener('mousedown', function (e) { e.stopPropagation(); });
  }

  // ------------------------------------------------------------ actions

  function toggleRow(row) {
    var key = row.getAttribute('data-key');
    if (row.getAttribute('data-f') === 'item') key = 'i:' + row.getAttribute('data-id');
    open[key] = !row.classList.contains('open');
    saveOpen();
    renderFeat();
  }
  function setAllOpen(on) {
    function walk(list) {
      list.forEach(function (n) {
        if (n.t === 'folder') { open['f:' + n.id] = on; walk(n.kids); }
        else if (n.kids.length) { open['i:' + n.it.id] = on; }
      });
    }
    if (model) walk(model.roots);
    saveOpen();
    renderFeat();
  }

  function selectRow(row, e) {
    var kind = row.getAttribute('data-f'), id = row.getAttribute('data-id');
    if (kind === 'folder') {
      sel.folder = id; sel.ids = {}; sel.anchor = null;
      updateSelection();
      BP.send('featSelect', { ids: model.fmap[id].ids.slice() });
      return;
    }
    var toggle = e.ctrlKey || e.metaKey;
    var a = sel.anchor ? order.indexOf(sel.anchor) : -1, b = order.indexOf(id);
    if (toggle) {
      if (sel.ids[id]) delete sel.ids[id]; else sel.ids[id] = 1;
      sel.anchor = id;
    } else if (e.shiftKey && a >= 0 && b >= 0) {
      sel.ids = {};
      for (var i = Math.min(a, b); i <= Math.max(a, b); i++) sel.ids[order[i]] = 1;
    } else {
      sel.ids = {}; sel.ids[id] = 1; sel.anchor = id;
      scheduleRoll(id);
    }
    sel.folder = null;
    updateSelection();
    BP.send('featSelect', { ids: Object.keys(sel.ids) });
  }

  // Clicking a feature moves the timeline marker to just after it (like scrubbing Fusion's timeline).
  // Delayed a moment so a double-click (edit) doesn't roll first; the redraw would also eat the double-click.
  var rollOnClick = BP.recall('featRollOnClick') !== '0', rollTimer = null, rolled = false;
  function scheduleRoll(id) {
    clearTimeout(rollTimer);
    if (!rollOnClick) return;
    rollTimer = setTimeout(function () { rolled = true; BP.send('featRollTo', { id: id, quiet: true }); }, 320);
  }
  function goHome() {
    clearTimeout(rollTimer);
    if (!rolled && !(S && S.featRolled)) return;   // (either side may know it's rolled)
    rolled = false;
    BP.send('featRollHome', {});
  }

  function deleteIds(id) { return sel.ids[id] ? Object.keys(sel.ids) : [id]; }
  function sendDelete(ids) {
    cancelDelete();
    if (!ids.length) return;
    ids.forEach(function (i) { delete sel.ids[i]; });
    BP.send('featDelete', { ids: ids });
  }

  function rowAct(act, row, btn) {
    var kind = row.getAttribute('data-f'), id = row.getAttribute('data-id');
    if (act === 'toggle') return toggleRow(row);
    if (act === 'sub') return startCreate(id);
    if (act === 'rename') return startRename(row);
    if (kind === 'folder') {
      if (act === 'select') return BP.send('featSelect', { ids: model.fmap[id].ids.slice() });
      if (act === 'delete') {
        row.classList.add('t-armed');
        setTimeout(function () { row.classList.remove('t-armed'); }, 3200);
        BP.armed(btn, function () {
          if (sel.folder === id) sel.folder = null;
          BP.send('folderDelete', { scope: SCOPE, id: id });
        });
      }
      return;
    }
    var it = model.imap[id];
    if (!it) return;
    if (act === 'select') return BP.send('featSelect', { ids: sel.ids[id] ? Object.keys(sel.ids) : [id] });
    if (act === 'rollTo') return BP.send('featRollTo', { id: id });
    if (act === 'suppress') return BP.send('featSuppress', { id: id, on: !it.suppressed });
    if (act === 'eye') return BP.send('featVisible', { ids: [id], on: it.visible === false });
    if (act === 'delItem') {
      row.classList.add('t-armed');
      setTimeout(function () { row.classList.remove('t-armed'); }, 3200);
      var ids = deleteIds(id);
      return BP.armed(btn, function () { sendDelete(ids); });
    }
  }

  // ------------------------------------------------------------ events (delegated)

  root = document.createElement('div');
  root.className = 'f-root hidden';
  root.innerHTML =
    '<div class="f-bar">' +
      '<div class="f-head"></div>' +
      '<input class="search f-search" type="search" placeholder="Search features" spellcheck="false" aria-label="Search features">' +
      '<div class="f-bar-row">' +
        '<button class="btn small" data-act="newFolder" title="New folder (groups the selected items into it)">New folder</button>' +
        '<label class="check" title="Clicking a feature moves the timeline marker to just after it (Roll to end puts it back)">' +
          '<input type="checkbox" class="f-rollclick"> Roll timeline on click</label>' +
        '<span class="t-spacer"></span>' +
        '<button class="icon-btn" data-act="expandAll" title="Expand all">' + I.expandAll + '</button>' +
        '<button class="icon-btn" data-act="collapseAll" title="Collapse all">' + I.collapseAll + '</button>' +
      '</div>' +
      '<div class="t-confirm hidden"><span class="t-confirm-text"></span><span class="t-spacer"></span>' +
        '<button class="btn small t-danger" data-act="confirmDelete">Delete</button>' +
        '<button class="btn small" data-act="cancelDelete">Cancel</button></div>' +
    '</div>' +
    '<div class="f-body"></div>';
  pane.appendChild(root);
  bar = root.querySelector('.f-bar');
  body = root.querySelector('.f-body');
  headEl = root.querySelector('.f-head');

  root.addEventListener('click', function (e) {
    if (justDragged) return;
    var t = e.target;
    var barAct = t.closest('.f-bar [data-act]');
    if (barAct) {
      var a = barAct.getAttribute('data-act');
      if (a === 'newFolder') startCreate(null);
      else if (a === 'expandAll') setAllOpen(true);
      else if (a === 'collapseAll') setAllOpen(false);
      else if (a === 'confirmDelete' && pendingDelete) sendDelete(pendingDelete.ids);
      else if (a === 'cancelDelete') cancelDelete();
      return;
    }
    if (t.closest('.f-bar') || !model) return;
    var row = t.closest('.t-row[data-f]');
    if (!row) {
      if (t.closest('.f-body') && (selCount() || sel.folder)) { sel.ids = {}; sel.folder = null; updateSelection(); }
      return;
    }
    var actEl = t.closest('[data-act]');
    if (actEl) { rowAct(actEl.getAttribute('data-act'), row, actEl); return; }
    selectRow(row, e);
  });

  root.addEventListener('dblclick', function (e) {
    if (!model || e.target.closest('[data-act], input, .f-bar')) return;
    var row = e.target.closest('.t-row[data-f]');
    if (!row) return;
    clearTimeout(rollTimer);
    // Double-click edits it in Fusion (Edit Sketch / Edit Feature); the caret opens and closes.
    if (row.getAttribute('data-f') === 'item') BP.send('featEdit', { id: row.getAttribute('data-id') });
    else if (row.querySelector(':scope > .t-caret[data-act]')) toggleRow(row);
  });

  var rollBox = root.querySelector('.f-rollclick');
  rollBox.checked = rollOnClick;
  rollBox.addEventListener('change', function () {
    rollOnClick = rollBox.checked;
    BP.store('featRollOnClick', rollOnClick ? '1' : '0');
    if (!rollOnClick) goHome();
  });

  var searchEl = root.querySelector('.f-search');
  searchEl.addEventListener('input', function () {
    query = searchEl.value.trim().toLowerCase();
    words = query ? query.split(/\s+/) : [];
    renderFeat();
  });
  searchEl.addEventListener('keydown', function (e) {
    e.stopPropagation();
    if (e.key === 'Escape') {
      if (searchEl.value) { searchEl.value = ''; query = ''; words = []; renderFeat(); } else searchEl.blur();
    }
  });

  // ------------------------------------------------------------ keyboard

  function active() { return BP.activeTab() === 'tree' && BP.state() && BP.state().mode === 'part'; }
  function typing(e) {
    var tag = e.target && e.target.tagName;
    return tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA';
  }

  function cancelDelete() {
    if (!pendingDelete) return;
    clearTimeout(pendingDelete.timer);
    pendingDelete = null;
    if (bar) bar.querySelector('.t-confirm').classList.add('hidden');
    if (body) Array.prototype.forEach.call(body.querySelectorAll('.t-doomed'), function (el) { el.classList.remove('t-doomed'); });
  }
  function askDelete(ids) {
    cancelDelete();
    pendingDelete = { ids: ids, timer: setTimeout(cancelDelete, 6000) };
    var n = ids.length, txt;
    if (n === 1) {
      var it = model.imap[ids[0]];
      txt = 'Delete ' + nameOf(ids[0]) + '? ' + (it && it.kind === 'feature' ? 'Features built on it may fail.' : 'Features using it may fail.');
    } else txt = 'Delete ' + n + ' items? Features using them may fail.';
    bar.querySelector('.t-confirm-text').textContent = txt + ' Press Delete again.';
    bar.querySelector('.t-confirm').classList.remove('hidden');
    ids.forEach(function (id) {
      Array.prototype.forEach.call(body.querySelectorAll('.t-row[data-f="item"][data-id="' + cssq(id) + '"]'), function (r) {
        r.classList.add('t-doomed');
      });
    });
  }

  // F2 renames the selected item or folder (capture, so app.js's F2 handler doesn't also fire).
  document.addEventListener('keydown', function (e) {
    if (!active() || drag || typing(e) || !model || e.key !== 'F2') return;
    var row = null, ids = Object.keys(sel.ids);
    if (sel.folder) row = body.querySelector('.t-row[data-f="folder"][data-id="' + cssq(sel.folder) + '"]');
    else if (ids.length === 1) row = body.querySelector('.t-row[data-f="item"][data-id="' + cssq(ids[0]) + '"]');
    if (row) { e.preventDefault(); e.stopPropagation(); startRename(row); }
  }, true);

  document.addEventListener('keydown', function (e) {
    if ((e.key !== 'Delete' && e.key !== 'Backspace') || !active() || drag || typing(e) || !model) return;
    var ids = Object.keys(sel.ids);
    if (!ids.length) return;
    e.preventDefault();
    if (pendingDelete && pendingDelete.ids.join('|') === ids.join('|')) sendDelete(ids);
    else askDelete(ids);
  });

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || !active()) return;
    cancelDelete();
    if (drag || typing(e)) return;
    if (selCount() || sel.folder) { sel.ids = {}; sel.folder = null; sel.anchor = null; if (model) updateSelection(); }
  });

  // ------------------------------------------------------------ drag and drop (mouse events)

  root.addEventListener('mousedown', function (e) {
    if (e.button !== 0 || !model) return;
    if (e.target.closest('input, button, [data-act], .f-bar')) return;
    var row = e.target.closest('.t-row[data-f]');
    if (!row) return;
    var kind = row.getAttribute('data-f'), id = row.getAttribute('data-id');
    var d = { kind: kind, sx: e.clientX, sy: e.clientY, started: false, row: row };
    if (kind === 'folder') {
      var fn = model.fmap[id];
      if (!fn) return;
      d.id = id;
      d.label = fn.f.name;
      d.sub = {};
      (function mark(n) { d.sub[n.id] = 1; n.kids.forEach(function (c) { if (c.t === 'folder') mark(c); }); })(fn);
    } else {
      d.ids = (sel.ids[id] && selCount() > 1) ? Object.keys(sel.ids) : [id];
      d.label = d.ids.length === 1 ? nameOf(id) : plural(d.ids.length, 'item');
    }
    drag = d;
    document.addEventListener('mousemove', onDragMove);
    document.addEventListener('mouseup', onDragUp);
    document.addEventListener('keydown', onDragKey, true);
  });

  function startDrag() {
    drag.started = true;
    ghost = document.createElement('div');
    ghost.className = 't-ghost';
    ghost.textContent = drag.label;
    document.body.appendChild(ghost);
    zone = document.createElement('div');
    zone.className = 't-topzone';
    zone.textContent = 'Top level';
    document.body.appendChild(zone);
    document.body.classList.add('t-dragging');
    drag.row.classList.add('t-src');
  }

  function setHot(target) {
    if (hot) {
      if (hot.row) hot.row.classList.remove('t-drop-into', 't-drop-before', 't-drop-after');
      else if (zone) zone.classList.remove('over');
    }
    hot = target;
    if (!target) return;
    if (target.row) target.row.classList.add('t-drop-' + target.pos);
    else if (zone) zone.classList.add('over');
  }

  function findTarget(e) {
    var el = document.elementFromPoint(e.clientX, e.clientY);
    if (!el) return null;
    if (el.closest('.t-topzone')) return { kind: 'top' };
    var row = el.closest('.t-row[data-f="folder"]');
    if (!row || !root.contains(row)) return null;
    var id = row.getAttribute('data-id');
    if (!model.fmap[id]) return null;
    if (drag.kind === 'folder' && drag.sub[id]) return null;
    var pos = 'into';
    if (drag.kind === 'folder') {
      var r = row.getBoundingClientRect(), y = (e.clientY - r.top) / Math.max(1, r.height);
      pos = y < 0.28 ? 'before' : (y > 0.72 ? 'after' : 'into');
    }
    return { kind: 'folder', id: id, pos: pos, row: row };
  }

  function onDragMove(e) {
    if (!drag) return;
    if (e.buttons === 0) { endDrag(false); return; }
    if (!drag.started) {
      if (Math.abs(e.clientX - drag.sx) + Math.abs(e.clientY - drag.sy) < 5) return;
      startDrag();
    }
    ghost.style.left = (e.clientX + 12) + 'px';
    ghost.style.top = (e.clientY + 8) + 'px';
    var t = findTarget(e);
    if (!(t && hot && t.kind === hot.kind && t.id === hot.id && t.pos === hot.pos)) setHot(t);
    var r = pane.getBoundingClientRect();
    if (e.clientY < r.top + 28) pane.scrollTop -= 10;
    else if (e.clientY > r.bottom - 28) pane.scrollTop += 10;
  }

  function nextSibling(fn, dragId) {
    var list = model.folders, found = false, par = fn.f.parent || null;
    for (var i = 0; i < list.length; i++) {
      var f = list[i];
      if (f.id === fn.id) { found = true; continue; }
      if (!found || f.id === dragId || (f.parent || null) !== par) continue;
      return f.id;
    }
    return null;
  }

  function drop(t) {
    if (drag.kind === 'folder') {
      var tf = t.kind === 'folder' ? model.fmap[t.id] : null;
      if (t.kind === 'top') BP.send('folderMove', { scope: SCOPE, id: drag.id, parent: null, before: null });
      else if (t.pos === 'into') { BP.send('folderMove', { scope: SCOPE, id: drag.id, parent: t.id, before: null }); open['f:' + t.id] = true; saveOpen(); }
      else if (t.pos === 'before') BP.send('folderMove', { scope: SCOPE, id: drag.id, parent: tf.f.parent || null, before: t.id });
      else BP.send('folderMove', { scope: SCOPE, id: drag.id, parent: tf.f.parent || null, before: nextSibling(tf, drag.id) });
      return;
    }
    if (t.kind === 'folder') { open['f:' + t.id] = true; saveOpen(); }
    BP.send('assign', { scope: SCOPE, ids: drag.ids, folder: t.kind === 'top' ? '' : t.id });
  }

  function endDrag(commit) {
    if (!drag) return;
    var d = drag, t = hot;
    document.removeEventListener('mousemove', onDragMove);
    document.removeEventListener('mouseup', onDragUp);
    document.removeEventListener('keydown', onDragKey, true);
    if (d.started) {
      setHot(null);
      if (ghost) ghost.remove();
      if (zone) zone.remove();
      ghost = zone = null;
      document.body.classList.remove('t-dragging');
      d.row.classList.remove('t-src');
      justDragged = true;
      setTimeout(function () { justDragged = false; }, 0);
    }
    if (commit && d.started && t) { drag = d; drop(t); }
    drag = null;
  }
  function onDragUp() { endDrag(true); }
  function onDragKey(e) {
    if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); endDrag(false); }
  }

  // ------------------------------------------------------------ wiring

  BP.onRender(function (state, tab) {
    S = state;
    // Python put the timeline back (something else was picked in Fusion): clear the tree's selection too.
    if (rolled && !state.featRolled) { rolled = false; sel.ids = {}; sel.anchor = null; }
    if (!isPart()) { root.classList.add('hidden'); dirty = true; return; }
    if (tab === 'tree') renderFeat(); else { root.classList.remove('hidden'); dirty = true; }
  });
  BP.onTab(function (tab) {
    if (tab !== 'tree') { goHome(); return; }
    if (!S) S = BP.state();
    if (!S) return;
    if (!isPart()) { root.classList.add('hidden'); return; }
    if (dirty || !rendered) renderFeat();
  });
})();
