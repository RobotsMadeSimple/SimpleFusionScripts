/* Browser+ Tree tab: a part tree with folders (kept by Browser+ only), an automatic
 * Hardware folder, show/hide, select/isolate, search, and mouse-driven drag and drop.
 * Plain ES5, no dependencies. Talks to Python only through window.BP (see app.js). */
(function () {
  'use strict';

  var BP = window.BP;
  var pane = document.getElementById('treePane');
  if (!BP || !pane) return;
  var esc = BP.esc;

  var S = null;                 // last state
  var dirty = true, rendered = false;
  var query = '', words = [];
  var open = {};                // key -> bool (explicit choices only)
  try { open = JSON.parse(BP.recall('treeOpen') || '{}') || {}; } catch (e) { open = {}; }
  var sel = { paths: {}, folder: null, anchor: null };
  var creating = null;          // {parent: id|null} while the new-folder input is shown
  var model = null;             // {roots, fmap, gmap, pmap} of the last build
  var order = [];               // part paths in visible order (for shift-click ranges)
  var body, bar;

  var I = {
    one: '<svg viewBox="0 0 16 16"><rect x="2.5" y="2.5" width="11" height="11" rx="1.5"/><path d="M7 6l1.5-1v6.5"/></svg>',
    caret: '<svg viewBox="0 0 16 16"><path d="m6 4 4 4-4 4"/></svg>',
    folder: '<svg viewBox="0 0 16 16"><path d="M1.5 4.5v8h13v-7H7.5L6 4H1.5z"/></svg>',
    folderAuto: '<svg viewBox="0 0 16 16"><path d="M1.5 4.5v8h13v-7H7.5L6 4H1.5z M4.5 9h7"/></svg>',
    folderPlus: '<svg viewBox="0 0 16 16"><path d="M1.5 4.5v8h7 M1.5 4.5V3h4l1.5 1.5h7V8 M12 10v4 M10 12h4"/></svg>',
    part: '<svg viewBox="0 0 16 16"><path d="M8 1.5 14 4.8v6.4L8 14.5 2 11.2V4.8z M2 4.8 8 8l6-3.2 M8 8v6.5"/></svg>',
    group: '<svg viewBox="0 0 16 16"><path d="M8 1.5 14 4.8v6.4L8 14.5 2 11.2V4.8z M2 4.8 8 8l6-3.2 M8 8v6.5 M4.5 15.5h7"/></svg>',
    select: '<svg viewBox="0 0 16 16"><path d="M3.5 2l9 5-4 1.2L6.5 13z"/></svg>',
    isolate: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="2.2"/><path d="M2 5V2h3 M11 2h3v3 M14 11v3h-3 M5 14H2v-3"/></svg>',
    link: '<svg viewBox="0 0 16 16"><path d="M6.5 9.5l3-3 M7 4.5l1-1a2.5 2.5 0 0 1 3.5 3.5l-1 1 M9 11.5l-1 1A2.5 2.5 0 0 1 4.5 9l1-1"/></svg>',
    reset: '<svg viewBox="0 0 16 16"><path d="M3 8a5 5 0 1 0 1.6-3.7 M3 2.5v3h3"/></svg>',
    expandAll: '<svg viewBox="0 0 16 16"><path d="m4 3.5 4 3 4-3 M4 9.5l4 3 4-3"/></svg>',
    collapseAll: '<svg viewBox="0 0 16 16"><path d="m4 6.5 4-3 4 3 M4 12.5l4-3 4 3"/></svg>'
  };

  // ------------------------------------------------------------ helpers

  function natCmp(a, b) {
    var x = String(a).toLowerCase().match(/\d+|\D+/g) || [], y = String(b).toLowerCase().match(/\d+|\D+/g) || [];
    var n = Math.min(x.length, y.length);
    for (var i = 0; i < n; i++) {
      var p = x[i], q = y[i];
      if (p === q) continue;
      if (/^\d/.test(p) && /^\d/.test(q)) { var d = parseInt(p, 10) - parseInt(q, 10); if (d) return d; }
      else return p < q ? -1 : 1;
    }
    return x.length - y.length;
  }

  function saveOpen() { BP.store('treeOpen', JSON.stringify(open)); }
  function plural(n, w) { return n + ' ' + w + (n === 1 ? '' : 's'); }
  function fltTitle(n) { return plural(n, 'part') + ' nothing holds (not grounded, no joints)'; }
  function isVisible(p) { var i = S.parts[p]; return !!i && i.visible !== false; }
  function anyVisible(paths) { for (var i = 0; i < paths.length; i++) if (isVisible(paths[i])) return true; return false; }
  function selCount() { return Object.keys(sel.paths).length; }

  // ------------------------------------------------------------ model

  function build(st) {
    var parts = st.parts || {}, tree = st.tree || {}, place = tree.place || {};
    var folders = tree.folders || [];
    var flt = {};
    (st.floating || []).forEach(function (p) { flt[p] = 1; });
    var fmap = {}, gmap = {}, pmap = {};
    var fold = {}, inFolder = { '': [] }, under = {}, childFolders = {}, rootFolders = [];
    folders.forEach(function (f) { fold[f.id] = f; inFolder[f.id] = []; });
    folders.forEach(function (f) {
      if (f.parent && fold[f.parent] && f.parent !== f.id) (childFolders[f.parent] = childFolders[f.parent] || []).push(f);
      else rootFolders.push(f);
    });
    // your folders first, automatic ones after
    rootFolders = rootFolders.filter(function (f) { return !f.auto; }).concat(rootFolders.filter(function (f) { return f.auto; }));
    Object.keys(parts).forEach(function (p) {
      var pl = place[p] || {};
      if (pl.under && parts[pl.under] && pl.under !== p) (under[pl.under] = under[pl.under] || []).push(p);
      else {
        var f = pl.folder;
        if (f == null || !fold[f]) f = '';
        inFolder[f].push(p);
      }
    });

    function label(p) { return parts[p].hw || parts[p].name || ''; }
    function sum(n, kids) { kids.forEach(function (k) { n.count += k.count; n.flt += k.flt; }); }

    function partNode(p) {
      var info = parts[p];
      var n = { t: 'part', path: p, info: info, kids: [], count: info.asOne ? 1 : info.inOne ? 0 : (info.leaf && info.bodies) ? 1 : 0, flt: flt[p] ? 1 : 0,
                explicit: !!(place[p] && place[p].explicit) };
      n.kids = groupList(under[p] || [], 'p:' + p, false);
      sum(n, n.kids);
      pmap[p] = n;
      return n;
    }

    function groupList(paths, scope, sort) {
      if (sort) paths = paths.slice().sort(function (a, b) { return natCmp(label(a), label(b)); });
      var byC = {}, done = {}, out = [];
      paths.forEach(function (p) {
        var c = parts[p].componentId;
        if (c) (byC[c] = byC[c] || []).push(p);
      });
      paths.forEach(function (p) {
        var c = parts[p].componentId;
        if (c && byC[c].length > 1) {
          if (done[c]) return;
          done[c] = 1;
          var first = parts[p];
          var g = { t: 'group', key: 'g:' + scope + ':' + c, paths: byC[c], count: 0, flt: 0,
                    label: first.hw || first.component || first.name };
          g.items = byC[c].map(partNode);
          sum(g, g.items);
          gmap[g.key] = g;
          out.push(g);
        } else out.push(partNode(p));
      });
      return out;
    }

    function folderNode(f) {
      var subs = (childFolders[f.id] || []).map(folderNode);
      var own = groupList(inFolder[f.id], 'f:' + f.id, !!f.auto);
      var n = { t: 'folder', id: f.id, f: f, kids: subs.concat(own), count: 0, flt: 0, paths: inFolder[f.id].slice() };
      sum(n, n.kids);
      subs.forEach(function (s) { n.paths = n.paths.concat(s.paths); });
      fmap[f.id] = n;
      return n;
    }

    var roots = rootFolders.map(folderNode).concat(groupList(inFolder[''], 'f:', false));
    return { roots: roots, fmap: fmap, gmap: gmap, pmap: pmap, folders: folders, total: Object.keys(parts).length };
  }

  // ------------------------------------------------------------ search

  function partMatches(p) {
    var i = S.parts[p];
    var hay = ((i.name || '') + ' ' + (i.component || '') + ' ' + (i.hw || '')).toLowerCase();
    for (var k = 0; k < words.length; k++) if (hay.indexOf(words[k]) < 0) return false;
    return true;
  }
  function clone(n, key, val) { var c = {}; for (var k in n) c[k] = n[k]; c[key] = val; return c; }
  function filt(n) {
    if (n.t === 'part') {
      var kids = filtList(n.kids);
      if (!kids.length && !partMatches(n.path)) return null;
      return clone(n, 'kids', kids);
    }
    if (n.t === 'group') {
      var items = filtList(n.items);
      if (!items.length) return null;
      return items.length === 1 ? items[0] : clone(n, 'items', items);
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
  function eyeBtn(visible, what) {
    return '<button class="t-eye" data-act="eye" title="' + (visible ? 'Hide ' : 'Show ') + what + '">' +
      (visible ? BP.ICON.eye : BP.ICON.eyeOff) + '</button>';
  }
  function folderOpen(id) {
    if (query) return true;
    if (creating && creating.parent === id) return true;
    var k = 'f:' + id;
    return k in open ? !!open[k] : id.indexOf('hw:') !== 0;
  }
  function keyOpen(key) { return query ? true : !!open[key]; }

  function newRow(depth) {
    return '<div class="t-row t-newrow" style="--d:' + depth + '"><span class="t-caret none"></span>' +
      '<span class="t-ico">' + I.folder + '</span>' +
      '<input class="t-newinput" value="New folder" maxlength="80" spellcheck="false" aria-label="Folder name"></div>';
  }

  function nodes(list, depth, h) {
    list.forEach(function (n) {
      if (n.t === 'folder') folderRow(n, depth, h);
      else if (n.t === 'group') groupRow(n, depth, h);
      else partRow(n, depth, h);
    });
  }

  function folderRow(n, depth, h) {
    var f = n.f, o = folderOpen(n.id), has = n.paths.length > 0, vis = has && anyVisible(n.paths);
    h.push('<div class="t-row t-folder' + (f.auto ? ' t-auto' : '') + (o ? ' open' : '') + (sel.folder === n.id ? ' sel' : '') +
      (has && !vis ? ' t-off' : '') + '" data-k="folder" data-id="' + esc(n.id) + '" data-key="f:' + esc(n.id) + '" style="--d:' + depth + '">' +
      '<span class="t-caret" data-act="toggle">' + I.caret + '</span>' +
      (has ? eyeBtn(vis, 'this folder') : '<span class="t-eye-gap"></span>') +
      '<span class="t-ico">' + (f.auto ? I.folderAuto : I.folder) + '</span>' +
      '<span class="t-name">' + esc(f.name) + '</span>' +
      (f.auto ? '<span class="t-autotag" title="Filled in automatically, sorted by kind then size. Can\'t be renamed or dropped into.">auto</span>' : '') +
      (n.flt ? '<span class="t-warn" title="' + esc(fltTitle(n.flt)) + '">' + n.flt + '</span>' : '') +
      '<span class="t-count">' + n.count + '</span>' +
      '<span class="t-tools">' +
        (has ? tb('select', 'Select in Fusion', I.select) + tb('isolate', 'Isolate (hide everything else)', I.isolate) : '') +
        (f.auto ? '' : tb('sub', 'New sub-folder', I.folderPlus) + tb('rename', 'Rename (F2)', BP.ICON.rename) +
          tb('delete', 'Delete folder (its contents move up)', BP.ICON.del, 'danger')) +
      '</span></div>');
    if (!o) return;
    if (creating && creating.parent === n.id) h.push(newRow(depth + 1));
    else if (!n.kids.length && !query) h.push('<div class="t-row t-emptyrow" style="--d:' + (depth + 1) + '">Empty \u2014 drag parts here</div>');
    nodes(n.kids, depth + 1, h);
  }

  function partRow(n, depth, h) {
    var info = n.info, p = n.path, key = 'p:' + p, hasKids = n.kids.length > 0, o = hasKids && keyOpen(key);
    var vis = info.visible !== false;
    // Hardware: just the short name (the full name is in the tooltip) so rows stay one line.
    var lab = info.hw ? '<span class="t-hwname">' + esc(info.hw) + '</span>' : esc(info.name);
    var tip = info.hw ? (info.component || info.name) + ' · ' + p : p;
    order.push(p);
    h.push('<div class="t-row t-part' + (o ? ' open' : '') + (sel.paths[p] ? ' sel' : '') + (vis ? '' : ' t-off') +
      '" data-k="part" data-path="' + esc(p) + '" data-key="' + esc(key) + '" style="--d:' + depth + '">' +
      (hasKids ? '<span class="t-caret" data-act="toggle">' + I.caret + '</span>' : '<span class="t-caret none"></span>') +
      eyeBtn(vis, 'this part') +
      '<span class="t-ico">' + I.part + '</span>' +
      '<span class="t-name" title="' + esc(tip) + '">' + lab + '</span>' +
      (n.flt && !hasKids ? '<span class="t-dot" title="' + esc(fltTitle(1)) + '"></span>' : '') +
      (hasKids && n.flt ? '<span class="t-warn" title="' + esc(fltTitle(n.flt)) + '">' + n.flt + '</span>' : '') +
      oneTag(info) +
      (hasKids && !info.asOne ? '<span class="t-count">' + n.count + '</span>' : '') +
      '<span class="t-tools">' +
        tb('select', 'Select in Fusion', I.select) + tb('isolate', 'Isolate (hide everything else)', I.isolate) +
        tb('holds', 'What holds it (opens the Part tab)', I.link) +
        (info.asOne || info.split ? tb('one', info.asOne ? 'Split: list its parts in the BOM instead of this assembly'
          : 'Count as one part in the BOM again', I.one) : '') +
        (n.explicit ? tb('reset', 'Back to its default place', I.reset) : '') +
      '</span></div>');
    if (o) nodes(n.kids, depth + 1, h);
  }

  function oneTag(info) {
    // Assemblies count as one BOM part by default; tag the ones split into their parts.
    return info.split ? '<span class="t-autotag" title="Split: its parts are listed in the BOM, not the assembly">split</span>' : '';
  }

  function groupRow(g, depth, h) {
    var o = keyOpen(g.key), vis = anyVisible(g.paths), all = true;
    g.paths.forEach(function (p) { if (!sel.paths[p]) all = false; });
    if (!o) g.paths.forEach(function (p) { order.push(p); });
    h.push('<div class="t-row t-group' + (o ? ' open' : '') + (all ? ' sel' : '') + (vis ? '' : ' t-off') +
      '" data-k="group" data-key="' + esc(g.key) + '" style="--d:' + depth + '">' +
      '<span class="t-caret" data-act="toggle">' + I.caret + '</span>' +
      eyeBtn(vis, 'all ' + g.paths.length + ' instances') +
      '<span class="t-ico">' + I.group + '</span>' +
      '<span class="t-name">' + esc(g.label) + ' <span class="t-x">\u00d7' + g.paths.length + '</span></span>' +
      (g.flt ? '<span class="t-warn" title="' + esc(fltTitle(g.flt)) + '">' + g.flt + '</span>' : '') +
      '<span class="t-tools">' +
        tb('select', 'Select all in Fusion', I.select) + tb('isolate', 'Isolate (hide everything else)', I.isolate) +
      '</span></div>');
    if (o) nodes(g.items, depth + 1, h);
  }

  function renderTree() {
    if (!S || !S.parts) return;
    dirty = false; rendered = true;
    var scroll = pane.scrollTop;
    model = build(S);
    Object.keys(sel.paths).forEach(function (p) { if (!S.parts[p]) delete sel.paths[p]; });
    if (sel.folder && !model.fmap[sel.folder]) sel.folder = null;

    var list = query ? filtList(model.roots) : model.roots;
    order = [];
    var h = [];
    if (creating && creating.parent == null) h.push(newRow(0));
    nodes(list, 0, h);
    if (!model.total && !model.folders.length && !creating) {
      h = ['<div class="empty-state"><svg viewBox="0 0 16 16"><path d="M8 1.5 14 4.8v6.4L8 14.5 2 11.2V4.8z M2 4.8 8 8l6-3.2 M8 8v6.5"/></svg>' +
        '<p>No parts in this design yet.</p><p class="sub">Parts you add appear here. Group them into folders to keep the tree tidy.</p></div>'];
    } else if (query && !list.length) {
      h = ['<div class="empty-state"><p>No parts match</p></div>'];
    }
    body.innerHTML = h.join('');
    pane.scrollTop = scroll;

    bar.querySelector('.t-hwauto').checked = !!(S.tree && S.tree.hardwareAuto);
    var iso = bar.querySelector('.t-iso');
    iso.classList.toggle('hidden', !S.isolated);
    bar.querySelector('.t-isolabel').textContent = S.isolated || '';

    if (creating) bindNewInput();
  }

  function updateSelection() {
    var rows = body.querySelectorAll('.t-row[data-k]');
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i], k = r.getAttribute('data-k'), on = false;
      if (k === 'folder') on = sel.folder === r.getAttribute('data-id');
      else if (k === 'part') on = !!sel.paths[r.getAttribute('data-path')];
      else {
        var g = model.gmap[r.getAttribute('data-key')];
        on = !!g && g.paths.every(function (p) { return sel.paths[p]; });
      }
      r.classList.toggle('sel', on);
    }
  }

  // ------------------------------------------------------------ inline inputs

  function startCreate(parent) {
    if (creating) return;
    creating = { parent: parent };
    BP.editing(true);
    renderTree();
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
        var payload = { name: v, parent: c.parent };
        var ps = Object.keys(sel.paths);
        if (ps.length) payload.paths = ps;
        BP.send('folderAdd', payload);
        if (c.parent) { open['f:' + c.parent] = true; saveOpen(); }
      }
      renderTree();
    }
    inp.addEventListener('keydown', function (e) {
      e.stopPropagation();
      if (e.key === 'Enter') { e.preventDefault(); finish(true); }
      else if (e.key === 'Escape') finish(false);
    });
    inp.addEventListener('blur', function () { finish(true); });
  }

  function startRename(row) {
    var id = row.getAttribute('data-id'), f = model.fmap[id];
    var nameEl = row.querySelector('.t-name');
    if (!f || f.f.auto || !nameEl) return;
    BP.editing(true);
    var input = document.createElement('input');
    input.value = f.f.name;
    input.className = 't-renameinput';
    input.maxLength = 80;
    nameEl.replaceWith(input);
    input.focus();
    input.select();
    var done = false;
    function finish(commit) {
      if (done) return;
      done = true;
      var v = input.value.trim();
      BP.editing(false);
      if (commit && v && v !== f.f.name) BP.send('folderRename', { id: id, name: v });
      else renderTree();
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

  function rowPaths(row) {
    var k = row.getAttribute('data-k');
    if (k === 'part') return [row.getAttribute('data-path')];
    if (k === 'group') return model.gmap[row.getAttribute('data-key')].paths.slice();
    return model.fmap[row.getAttribute('data-id')].paths.slice();
  }
  function rowLabel(row) {
    var k = row.getAttribute('data-k');
    if (k === 'part') return BP.partName(row.getAttribute('data-path'));
    if (k === 'group') return model.gmap[row.getAttribute('data-key')].label;
    return model.fmap[row.getAttribute('data-id')].f.name;
  }
  function toggleRow(row) {
    open[row.getAttribute('data-key')] = !row.classList.contains('open');
    saveOpen();
    renderTree();
  }
  function setAllOpen(on) {
    function walk(list) {
      list.forEach(function (n) {
        if (n.t === 'folder') { open['f:' + n.id] = on; walk(n.kids); }
        else if (n.t === 'group') { open[n.key] = on; walk(n.items); }
        else if (n.kids.length) { open['p:' + n.path] = on; walk(n.kids); }
      });
    }
    if (model) walk(model.roots);
    saveOpen();
    renderTree();
  }

  function selectRow(row, e) {
    var k = row.getAttribute('data-k');
    if (k === 'folder') {
      var id = row.getAttribute('data-id');
      sel.folder = id; sel.paths = {}; sel.anchor = null;
      updateSelection();
      BP.send('selectParts', { paths: model.fmap[id].paths });
      return;
    }
    var toggle = e.ctrlKey || e.metaKey;
    if (k === 'part') {
      var p = row.getAttribute('data-path');
      var a = sel.anchor ? order.indexOf(sel.anchor) : -1, b = order.indexOf(p);
      if (toggle) {
        if (sel.paths[p]) delete sel.paths[p]; else sel.paths[p] = 1;
        sel.anchor = p;
      } else if (e.shiftKey && a >= 0 && b >= 0) {
        sel.paths = {};
        for (var i = Math.min(a, b); i <= Math.max(a, b); i++) sel.paths[order[i]] = 1;
      } else {
        sel.paths = {}; sel.paths[p] = 1; sel.anchor = p;
      }
    } else {
      var g = model.gmap[row.getAttribute('data-key')];
      if (!toggle) sel.paths = {};
      var allOn = toggle && g.paths.every(function (q) { return sel.paths[q]; });
      g.paths.forEach(function (q) { if (allOn) delete sel.paths[q]; else sel.paths[q] = 1; });
      sel.anchor = g.paths[0];
    }
    sel.folder = null;
    updateSelection();
    BP.send('selectParts', { paths: Object.keys(sel.paths) });
  }

  function rowAct(act, row, btn) {
    var k = row.getAttribute('data-k');
    if (act === 'toggle') return toggleRow(row);
    if (act === 'eye') {
      var ps = rowPaths(row);
      return BP.send('setVisible', { paths: ps, on: !anyVisible(ps) });
    }
    if (act === 'select') return BP.send('selectParts', { paths: rowPaths(row) });
    if (act === 'isolate') return BP.send('isolate', { paths: rowPaths(row), label: rowLabel(row) });
    if (act === 'holds') return BP.focusPart(row.getAttribute('data-path'));
    if (act === 'one') {
      var info = BP.state().parts[row.getAttribute('data-path')];
      if (info) BP.send('asOne', { componentId: info.componentId, on: !info.asOne });
      return;
    }
    if (act === 'reset') {
      var p = row.getAttribute('data-path');
      var list = sel.paths[p] ? Object.keys(sel.paths).filter(function (q) { return model.pmap[q] && model.pmap[q].explicit; }) : [p];
      return BP.send('assign', { paths: list.length ? list : [p], folder: null });
    }
    if (act === 'sub') return startCreate(row.getAttribute('data-id'));
    if (act === 'rename') return startRename(row);
    if (act === 'delete' && k === 'folder') {
      row.classList.add('t-armed');
      setTimeout(function () { row.classList.remove('t-armed'); }, 3200);
      BP.armed(btn, function () {
        var id = row.getAttribute('data-id');
        if (sel.folder === id) sel.folder = null;
        BP.send('folderDelete', { id: id });
      });
    }
  }

  // ------------------------------------------------------------ events (delegated)

  var justDragged = false;

  pane.addEventListener('click', function (e) {
    if (justDragged) return;
    var t = e.target;
    var barAct = t.closest('.t-bar [data-act]');
    if (barAct) {
      var a = barAct.getAttribute('data-act');
      if (a === 'newFolder') startCreate(null);
      else if (a === 'expandAll') setAllOpen(true);
      else if (a === 'collapseAll') setAllOpen(false);
      else if (a === 'unisolate') BP.send('unisolate', {});
      return;
    }
    if (t.closest('.t-bar') || !model) return;
    var row = t.closest('.t-row');
    if (!row) {
      if (t.closest('.t-body') && (selCount() || sel.folder)) { sel.paths = {}; sel.folder = null; updateSelection(); }
      return;
    }
    if (!row.getAttribute('data-k')) return;
    var actEl = t.closest('[data-act]');
    if (actEl) { rowAct(actEl.getAttribute('data-act'), row, actEl); return; }
    selectRow(row, e);
  });

  pane.addEventListener('dblclick', function (e) {
    if (!model || e.target.closest('[data-act], input, .t-bar')) return;
    var row = e.target.closest('.t-row[data-k]');
    if (!row) return;
    var k = row.getAttribute('data-k');
    if (k === 'part') BP.focusPart(row.getAttribute('data-path'));
    else toggleRow(row);
  });

  bar = document.createElement('div');
  bar.className = 't-bar';
  bar.innerHTML =
    '<input class="search t-search" type="search" placeholder="Search parts" spellcheck="false" aria-label="Search parts">' +
    '<div class="t-bar-row">' +
      '<button class="btn small" data-act="newFolder" title="New folder (groups the selected parts into it)">New folder</button>' +
      '<label class="check" title="Sort screws, nuts, washers\u2026 into an automatic Hardware folder"><input type="checkbox" class="t-hwauto"> Hardware folder</label>' +
      '<span class="t-spacer"></span>' +
      '<button class="icon-btn" data-act="expandAll" title="Expand all">' + I.expandAll + '</button>' +
      '<button class="icon-btn" data-act="collapseAll" title="Collapse all">' + I.collapseAll + '</button>' +
    '</div>' +
    '<div class="t-iso hidden">Isolated: <b class="t-isolabel"></b><span class="t-spacer"></span>' +
      '<button class="btn small" data-act="unisolate" title="Bring everything back">Show all</button></div>';
  body = document.createElement('div');
  body.className = 't-body';
  pane.appendChild(bar);
  pane.appendChild(body);

  bar.querySelector('.t-hwauto').addEventListener('change', function (e) {
    BP.send('hardwareAuto', { on: e.target.checked });
  });
  var searchEl = bar.querySelector('.t-search');
  searchEl.addEventListener('input', function () {
    query = searchEl.value.trim().toLowerCase();
    words = query ? query.split(/\s+/) : [];
    renderTree();
  });
  searchEl.addEventListener('keydown', function (e) {
    e.stopPropagation();
    if (e.key === 'Escape') {
      if (searchEl.value) { searchEl.value = ''; query = ''; words = []; renderTree(); } else searchEl.blur();
    }
  });

  // F2 renames the selected folder (capture, so app.js's F2 handler for joints doesn't also fire).
  document.addEventListener('keydown', function (e) {
    if (BP.activeTab() !== 'tree' || drag) return;
    var tag = e.target && e.target.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
    if (e.key === 'F2' && sel.folder && model && model.fmap[sel.folder] && !model.fmap[sel.folder].f.auto) {
      var row = body.querySelector('.t-row[data-k="folder"][data-id="' + sel.folder.replace(/["\\]/g, '\\$&') + '"]');
      if (row) { e.preventDefault(); e.stopPropagation(); startRename(row); }
    }
  }, true);
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || BP.activeTab() !== 'tree' || drag) return;
    var tag = e.target && e.target.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
    if (selCount() || sel.folder) { sel.paths = {}; sel.folder = null; sel.anchor = null; if (model) updateSelection(); }
  });

  // ------------------------------------------------------------ drag and drop (mouse events)

  var drag = null, ghost = null, zone = null, hot = null;

  pane.addEventListener('mousedown', function (e) {
    if (e.button !== 0 || !model) return;
    if (e.target.closest('input, button, [data-act], .t-bar')) return;
    var row = e.target.closest('.t-row[data-k]');
    if (!row) return;
    var k = row.getAttribute('data-k');
    var d = { kind: k, sx: e.clientX, sy: e.clientY, started: false, row: row, target: null };
    if (k === 'folder') {
      var fn = model.fmap[row.getAttribute('data-id')];
      if (!fn || fn.f.auto) return;
      d.id = fn.id;
      d.label = fn.f.name;
      d.sub = {};
      (function mark(n) { d.sub[n.id] = 1; n.kids.forEach(function (c) { if (c.t === 'folder') mark(c); }); })(fn);
    } else if (k === 'part') {
      var p = row.getAttribute('data-path');
      d.paths = (sel.paths[p] && selCount() > 1) ? Object.keys(sel.paths) : [p];
      d.label = d.paths.length === 1 ? BP.partName(p) : plural(d.paths.length, 'part');
    } else {
      var g = model.gmap[row.getAttribute('data-key')];
      d.paths = g.paths.slice();
      d.label = g.label + ' \u00d7' + g.paths.length;
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
    var row = el.closest('.t-row[data-k="folder"]');
    if (!row || !pane.contains(row)) return null;
    var id = row.getAttribute('data-id'), fn = model.fmap[id];
    if (!fn || fn.f.auto) return null;
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
      return f.auto ? null : f.id;
    }
    return null;
  }

  function drop(t) {
    if (drag.kind === 'folder') {
      var tf = t.kind === 'folder' ? model.fmap[t.id] : null;
      if (t.kind === 'top') BP.send('folderMove', { id: drag.id, parent: null, before: null });
      else if (t.pos === 'into') { BP.send('folderMove', { id: drag.id, parent: t.id, before: null }); open['f:' + t.id] = true; saveOpen(); }
      else if (t.pos === 'before') BP.send('folderMove', { id: drag.id, parent: tf.f.parent || null, before: t.id });
      else BP.send('folderMove', { id: drag.id, parent: tf.f.parent || null, before: nextSibling(tf, drag.id) });
      return;
    }
    if (t.kind === 'folder') { open['f:' + t.id] = true; saveOpen(); }
    BP.send('assign', { paths: drag.paths, folder: t.kind === 'top' ? '' : t.id });
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
    if (tab === 'tree') renderTree(); else dirty = true;
  });
  BP.onTab(function (tab) {
    if (tab !== 'tree') return;
    if (!S) S = BP.state();
    if (S && S.parts && (dirty || !rendered)) renderTree();
  });
})();
