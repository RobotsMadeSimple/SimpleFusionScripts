/* Browser+ BOM tab. Python pushes state.bom only while this tab is open
 * (bomOpen); this renders the parts list, groups, custom columns, CSV export. */
(function () {
  'use strict';
  var BP = window.BP;
  var pane = document.getElementById('bomPane');
  if (!BP || !pane) return;
  var esc = BP.esc;

  var ICON = {
    chevR: '<svg viewBox="0 0 16 16"><path d="m6 4 4 4-4 4"/></svg>',
    chevD: '<svg viewBox="0 0 16 16"><path d="m4 6 4 4 4-4"/></svg>',
    x: '<svg viewBox="0 0 16 16"><path d="m4 4 8 8M12 4l-8 8"/></svg>',
    link: '<svg viewBox="0 0 16 16"><path d="M6.5 9.5l3-3M7 4.5l1-1a2.5 2.5 0 013.5 3.5l-1 1M9 11.5l-1 1A2.5 2.5 0 014.5 9l1-1"/></svg>',
    up: '<svg viewBox="0 0 16 16"><path d="m4 10 4-4 4 4"/></svg>',
    down: '<svg viewBox="0 0 16 16"><path d="m4 6 4 4 4-4"/></svg>',
    one: '<svg viewBox="0 0 16 16"><rect x="2.5" y="2.5" width="11" height="11" rx="1.5"/><path d="M7 6l1.5-1v6.5"/></svg>',
    copy: '<svg viewBox="0 0 16 16"><rect x="5.5" y="5.5" width="8" height="8" rx="1"/><path d="M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2"/></svg>',
    check: '<svg viewBox="0 0 16 16"><path d="m3 8.5 3 3 7-7"/></svg>',
    box: '<svg viewBox="0 0 16 16"><path d="M8 1.5l6 3v7l-6 3-6-3v-7zM2 4.5l6 3 6-3M8 7.5v7"/></svg>'
  };
  var HIDEABLE = ['partNumber', 'description', 'material', 'mass'];
  var NUMERIC = { qty: 1, mass: 1 };

  // ---- persisted view settings
  function loadJson(k, dflt) {
    try { var v = JSON.parse(BP.recall(k)); return v && typeof v === 'object' ? v : dflt; } catch (e) { return dflt; }
  }
  // Built-in column id -> true. Only Qty and Name show until you turn others on (Columns menu).
  var hidden = loadJson('bom.hiddenCols', { partNumber: true, description: true, material: true, mass: true });
  var collapsed = loadJson('bom.collapsed', {});  // section id -> true
  var sort = loadJson('bom.sort', null);          // {col, dir}
  var group = BP.recall('bom.group') !== '0';

  // ---- runtime
  var bom = null, filter = '', editingNow = false, massBusy = false, activeCid = null, matchCount = 0;
  var rowById = {};
  var scroller, searchEl, groupEl, massBtn, sumEl, menuEl, addHost;

  function buildShell() {
    pane.innerHTML =
      '<div class="b-bar">' +
        '<input class="search b-search" type="search" placeholder="Filter BOM" spellcheck="false">' +
        '<div class="b-ctl">' +
          '<label class="check"><input type="checkbox" class="b-group"> Group by folder</label>' +
          '<button class="btn small b-mass" data-b="mass">Calculate masses</button>' +
          '<span class="b-addhost"></span>' +
          '<span class="b-menuwrap"><button class="btn small" data-b="menu">Columns</button><div class="b-menu hidden"></div></span>' +
          '<button class="btn small" data-b="copyall" title="Copy the BOM (as shown) to paste into Excel / Sheets">Copy</button>' +
          '<button class="btn small" data-b="export">Export CSV</button>' +
        '</div>' +
        '<div class="b-sum sub"></div>' +
      '</div>' +
      '<div class="b-scroll"></div>';
    scroller = pane.querySelector('.b-scroll');
    searchEl = pane.querySelector('.b-search');
    groupEl = pane.querySelector('.b-group');
    massBtn = pane.querySelector('.b-mass');
    sumEl = pane.querySelector('.b-sum');
    menuEl = pane.querySelector('.b-menu');
    addHost = pane.querySelector('.b-addhost');
    groupEl.checked = group;
    resetAddHost();
  }

  function resetAddHost() {
    addHost.innerHTML = '<button class="btn small" data-b="addcol">Add column</button>';
  }

  // ---- helpers
  function fmtMass(v) {
    if (v == null || isNaN(v)) return '';
    v = Number(v);
    if (v === 0) return '0 kg';
    var a = Math.abs(v), s;
    if (a >= 1000) s = String(Math.round(v));
    else if (a < 0.001) s = v.toExponential(2);
    else s = String(Number(v.toPrecision(3)));
    return s + ' kg';
  }
  function visibleColumns() {
    return bom.columns.filter(function (c) {
      if (c.custom) return true;
      if (c.id === 'mass') return !!bom.hasMass && !hidden.mass;
      return !(HIDEABLE.indexOf(c.id) >= 0 && hidden[c.id]);
    });
  }
  // ---- copy (tab-separated: pastes into Excel / Sheets as columns). What's shown: filter, sort, columns.
  function shownSections(onlyId) {
    var words = filter.toLowerCase().split(/\s+/).filter(Boolean);
    return bom.sections.filter(function (s) { return !onlyId || s.id === onlyId; }).map(function (s) {
      var rows = s.rows.filter(function (r) {
        if (!words.length) return true;
        var hs = haystack(r);
        return words.every(function (w) { return hs.indexOf(w) >= 0; });
      });
      return { name: s.name, rows: sortedRows(rows) };
    }).filter(function (s) { return s.rows.length; });
  }
  function tsvCell(v) { return String(v == null ? '' : v).replace(/[\t\r\n]+/g, ' '); }
  function bomText(onlyId) {
    var cols = visibleColumns(), withSection = bom.grouped && !onlyId;
    var lines = [(withSection ? ['Section'] : []).concat(cols.map(function (c) { return c.name; })).map(tsvCell).join('\t')];
    shownSections(onlyId).forEach(function (s) {
      s.rows.forEach(function (r) {
        var cells = cols.map(function (c) { return c.id === 'name' ? (r.hw || r.name) : cellText(r, c); });
        lines.push((withSection ? [s.name] : []).concat(cells).map(tsvCell).join('\t'));
      });
    });
    return lines.join('\r\n') + '\r\n';
  }
  function copyRows(onlyId, btn) {
    var text = bomText(onlyId);
    var label = btn.innerHTML;
    function done() {
      btn.innerHTML = btn.classList.contains('icon-btn') ? ICON.check : 'Copied';
      setTimeout(function () { if (btn.isConnected) btn.innerHTML = label; }, 1200);
    }
    // Always through Python: in Fusion's panel the browser's clipboard calls report success
    // without copying anything.
    BP.send('copyText', { text: text, done: onlyId ? 'Section copied.' : 'BOM copied.' });
    done();
  }

  function cellText(row, col) {
    if (col.custom) return (row.values && row.values[col.id]) || '';
    if (col.id === 'mass') return fmtMass(row.mass);
    var v = row[col.id];
    return v == null ? '' : String(v);
  }
  function haystack(row) {
    var parts = [row.name, row.hw, row.partNumber, row.description, row.material];
    if (row.values) for (var k in row.values) parts.push(row.values[k]);
    return parts.join(' ').toLowerCase();
  }
  function sortKey(row, col) {
    if (col === 'name') return row.hw || row.name || '';
    return row[col];
  }
  function sortedRows(rows) {
    if (!sort || !sort.col) return rows;
    var col = sort.col, dir = sort.dir === 'desc' ? -1 : 1, num = NUMERIC[col];
    var out = rows.map(function (r, i) { return { r: r, i: i }; });
    out.sort(function (a, b) {
      var x = sortKey(a.r, col), y = sortKey(b.r, col), c;
      if (num) {
        var xn = x == null, yn = y == null;
        if (xn || yn) return xn && yn ? a.i - b.i : (xn ? 1 : -1);   // empty last either way
        c = x - y;
      } else {
        c = String(x == null ? '' : x).localeCompare(String(y == null ? '' : y), undefined, { numeric: true, sensitivity: 'base' });
      }
      return c ? c * dir : a.i - b.i;
    });
    return out.map(function (o) { return o.r; });
  }
  function pathTitle(row) {
    var p = row.paths || [], shown = p.slice(0, 5).map(function (x) { return x.split('+').join(' \u203a '); });
    if (p.length > 5) shown.push('\u2026 and ' + (p.length - 5) + ' more');
    return shown.join('\n');
  }
  function cssEsc(s) { return String(s).replace(/["\\]/g, '\\$&'); }

  // ---- render
  function renderBar() {
    if (!bom) return;
    groupEl.checked = group;
    massBtn.textContent = massBusy ? 'Calculating\u2026' : (bom.hasMass ? 'Recalculate' : 'Calculate masses');
    massBtn.disabled = massBusy;
    var filtering = filter.split(/\s+/).filter(Boolean).length > 0;
    sumEl.textContent = bom.totalQty + (bom.totalQty === 1 ? ' part' : ' parts') + ' \u00b7 ' + bom.unique + ' unique' +
      (filtering ? ' \u00b7 ' + matchCount + ' shown' : '') +
      // Part numbers / descriptions / materials fill in in the background (slow to read in Fusion).
      (bom.loading ? ' \u00b7 reading part details ' + (bom.loading.total - bom.loading.left) + '/' + bom.loading.total + '\u2026' : '');
    var h = '';
    bom.columns.forEach(function (c) {
      if (HIDEABLE.indexOf(c.id) < 0 || (c.id === 'mass' && !bom.hasMass)) return;
      h += '<label class="check"><input type="checkbox" data-col="' + esc(c.id) + '"' + (hidden[c.id] ? '' : ' checked') + '> ' + esc(c.name) + '</label>';
    });
    if (menuEl.getAttribute('data-h') !== h) { menuEl.innerHTML = h; menuEl.setAttribute('data-h', h); }
  }

  function render() {
    if (BP.activeTab() !== 'bom' || editingNow) return;
    var s = BP.state();
    if (s && s.bom) bom = s.bom;
    if (!bom) {
      scroller.innerHTML = '<div class="msg">Loading parts list\u2026</div>';
      sumEl.textContent = '';
      return;
    }
    renderTable();
  }

  function renderTable() {
    if (!bom) return;
    var words = filter.toLowerCase().split(/\s+/).filter(Boolean);
    var cols = visibleColumns();
    rowById = {};
    matchCount = 0;
    var body = '', any = false, total = 0;
    bom.sections.forEach(function (sec) {
      total += sec.rows.length;
      var rows = sec.rows.filter(function (r) {
        if (!words.length) return true;
        var hs = haystack(r);
        return words.every(function (w) { return hs.indexOf(w) >= 0; });
      });
      if (!rows.length) return;
      any = true;
      rows = sortedRows(rows);
      var sub = 0, open = words.length > 0 || !collapsed[sec.id] || !bom.grouped;
      // Row key = section + component: grouped, the same part can be in two folders.
      rows.forEach(function (r) { sub += r.qty || 0; matchCount += r.qty || 0; r._key = sec.id + '|' + r.componentId; rowById[r._key] = r; });
      if (bom.grouped) {
        body += '<tr class="b-sec" data-sec="' + esc(sec.id) + '"><td colspan="' + cols.length + '"><div class="b-sechead">' +
          (open ? ICON.chevD : ICON.chevR) + '<span class="b-secname">' + esc(sec.name) + '</span>' +
          '<button class="icon-btn b-seccopy" data-b="copysec" title="Copy this section">' + ICON.copy + '</button>' +
          '<span class="b-secqty">' + sub + '</span></div></td></tr>';
      }
      if (!open) return;
      rows.forEach(function (r) { body += rowHtml(r, cols); });
    });
    if (!total) {
      scroller.innerHTML = '<div class="empty-state">' + ICON.box + '<p>No parts with bodies in this design.</p></div>';
    } else if (!any) {
      scroller.innerHTML = '<div class="msg">No parts match</div>';
    } else {
      var head = '<tr>';
      cols.forEach(function (c) { head += headHtml(c); });
      head += '</tr>';
      scroller.innerHTML = '<table class="b-table"><thead>' + head + '</thead><tbody>' + body + '</tbody></table>';
    }
    renderBar();
  }

  function headHtml(c) {
    var cls = 'b-th' + (NUMERIC[c.id] && !c.custom ? ' b-r' : '') + ' b-c-' + (c.custom ? 'custom' : c.id);
    if (c.custom) {
      return '<th class="' + cls + '" data-col="' + esc(c.id) + '"><span class="b-hwrap"><span class="b-hname" title="Double-click to rename">' +
        esc(c.name) + '</span><button class="icon-btn danger b-delcol" data-b="delcol" title="Delete column">' + ICON.x + '</button></span></th>';
    }
    var on = sort && sort.col === c.id;
    return '<th class="' + cls + ' b-sortable' + (on ? ' b-sorted' : '') + '" data-sort="' + esc(c.id) + '">' + esc(c.name) +
      (on ? '<span class="b-arrow">' + (sort.dir === 'desc' ? ICON.down : ICON.up) + '</span>' : '') + '</th>';
  }

  // "Count as one part": on a piece of an assembly, offer to count that assembly (e.g. a motor)
  // as one BOM line; on such an assembly's own row, offer to list its pieces again.
  function oneInfo(r) {
    var parts = BP.state().parts || {}, p = r.paths && r.paths[0];
    if (!p) return null;
    var me = parts[p];
    if (me && me.asOne) return { componentId: me.componentId, on: false, name: me.component || r.name };
    var cut = p.lastIndexOf('+');
    var parent = cut > 0 ? parts[p.slice(0, cut)] : null;
    return parent ? { componentId: parent.componentId, on: true, name: parent.component || parent.name } : null;
  }
  function oneBtn(r) {
    var o = oneInfo(r);
    if (!o) return '';
    var tip = o.on ? 'Count “' + o.name + '” as one part (lists it instead of its pieces)'
                   : 'Split “' + o.name + '”: list its parts instead of the assembly';
    return (o.on ? '' : '<span class="b-onetag" title="Counted as one part">assembly</span>') +
      '<button class="icon-btn b-holds" data-b="one" title="' + esc(tip) + '">' + ICON.one + '</button>';
  }

  function rowHtml(r, cols) {
    var h = '<tr class="b-row' + (activeCid === r._key ? ' b-active' : '') + '" data-key="' + esc(r._key) + '">';
    cols.forEach(function (c) {
      if (c.custom) {
        h += '<td class="b-cell b-edit" data-col="' + esc(c.id) + '">' + esc(cellText(r, c)) + '</td>';
      } else if (c.id === 'qty') {
        h += '<td class="b-r b-qty" title="' + esc(pathTitle(r)) + '">' + (r.qty == null ? '' : r.qty) + '</td>';
      } else if (c.id === 'name') {
        // Hardware: just the short name (full name in the tooltip) so rows stay one line.
        h += '<td class="b-name"><div class="b-namewrap"><div class="b-nametext"' + (r.hw ? ' title="' + esc(r.name) + '"' : '') + '>' +
          (r.hw ? '<b class="b-hw">' + esc(r.hw) + '</b>' : esc(r.name)) +
          '</div>' + oneBtn(r) +
          '<button class="icon-btn b-holds" data-b="holds" title="What holds it">' + ICON.link + '</button></div></td>';
      } else if (c.id === 'mass') {
        h += '<td class="b-r b-mass-c">' + esc(fmtMass(r.mass)) + '</td>';
      } else {
        h += '<td class="b-c-' + esc(c.id) + '">' + esc(cellText(r, c)) + '</td>';
      }
    });
    return h + '</tr>';
  }

  // ---- inline editing
  // opts: commit(value), onTab() optional, restore() optional
  function inlineEdit(host, value, opts) {
    if (editingNow) return;
    editingNow = true;
    BP.editing(true);
    var input = document.createElement('input');
    input.className = 'b-input';
    input.value = value || '';
    host.innerHTML = '';
    host.appendChild(input);
    input.focus();
    input.select();
    var done = false;
    function finish(commit, tab) {
      if (done) return;
      done = true;
      editingNow = false;
      var v = input.value.trim();
      if (commit && v !== (value || '')) opts.commit(v);
      if (opts.restore) opts.restore();
      BP.editing(false);
      renderTable();
      if (tab && opts.onTab) opts.onTab(tab);
    }
    input.addEventListener('keydown', function (e) {
      e.stopPropagation();
      if (e.key === 'Enter') { e.preventDefault(); finish(true); }
      else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
      else if (e.key === 'Tab') { e.preventDefault(); finish(true, e.shiftKey ? -1 : 1); }
    });
    input.addEventListener('blur', function () { finish(true); });
    input.addEventListener('click', function (e) { e.stopPropagation(); });
    input.addEventListener('dblclick', function (e) { e.stopPropagation(); });
  }

  function editCell(td) {
    var tr = td.closest('tr'), key = tr.getAttribute('data-key'), col = td.getAttribute('data-col');
    var row = rowById[key];
    if (!row) return;
    var cid = row.componentId;
    inlineEdit(td, (row.values && row.values[col]) || '', {
      commit: function (v) {
        if (!row.values) row.values = {};
        if (v) row.values[col] = v; else delete row.values[col];   // optimistic; Python pushes the real state
        BP.send('bomValue', { componentId: cid, column: col, value: v });
      },
      onTab: function (dir) {
        var cur = scroller.querySelector('tr[data-key="' + cssEsc(key) + '"]');
        var next = cur && (dir < 0 ? cur.previousElementSibling : cur.nextElementSibling);
        while (next && !next.classList.contains('b-row')) next = dir < 0 ? next.previousElementSibling : next.nextElementSibling;
        var cell = next && next.querySelector('td[data-col="' + cssEsc(col) + '"]');
        if (cell) { cell.scrollIntoView({ block: 'nearest' }); editCell(cell); }
      }
    });
  }

  function renameCol(th) {
    var id = th.getAttribute('data-col');
    var col = bom.columns.filter(function (c) { return c.id === id; })[0];
    if (!col) return;
    inlineEdit(th, col.name, {
      commit: function (v) {
        if (!v) return;
        col.name = v;
        BP.send('bomColumnRename', { id: id, name: v });
      }
    });
  }

  function addColumn() {
    inlineEdit(addHost, '', {
      commit: function (v) { if (v) BP.send('bomColumnAdd', { name: v }); },
      restore: resetAddHost
    });
    var inp = addHost.querySelector('input');
    if (inp) inp.placeholder = 'Column name';
  }

  // ---- events
  function closeMenu() { menuEl.classList.add('hidden'); }

  pane.addEventListener('click', function (e) {
    var t = e.target;
    if (t.closest('.b-menu')) return;
    var btn = t.closest('[data-b]');
    var act = btn && btn.getAttribute('data-b');
    if (act !== 'menu') closeMenu();
    if (act === 'mass') {
      massBusy = true;
      delete hidden.mass;                       // you asked for masses: show them
      BP.store('bom.hiddenCols', JSON.stringify(hidden));
      renderBar();
      BP.send('bomMass', {});
      return;
    }
    if (act === 'export') { BP.send('bomExport', {}); return; }
    if (act === 'copyall') { copyRows(null, btn); return; }
    if (act === 'copysec') { copyRows(btn.closest('tr').getAttribute('data-sec'), btn); return; }
    if (act === 'menu') { menuEl.classList.toggle('hidden'); return; }
    if (act === 'addcol') { addColumn(); return; }
    if (act === 'delcol') {
      var id = btn.closest('th').getAttribute('data-col');
      BP.armed(btn, function () { BP.send('bomColumnDelete', { id: id }); });
      return;
    }
    if (act === 'one') {
      var orow = rowById[btn.closest('tr').getAttribute('data-key')], o = orow && oneInfo(orow);
      if (o) BP.send('asOne', { componentId: o.componentId, on: o.on });
      return;
    }
    if (act === 'holds') {
      var hr = rowById[btn.closest('tr').getAttribute('data-key')];
      if (hr && hr.paths && hr.paths.length) BP.focusPart(hr.paths[0]);
      return;
    }
    var th = t.closest('th[data-sort]');
    if (th) {
      var c = th.getAttribute('data-sort');
      if (sort && sort.col === c) sort = sort.dir === 'asc' ? { col: c, dir: 'desc' } : null;
      else sort = { col: c, dir: 'asc' };
      BP.store('bom.sort', JSON.stringify(sort));
      renderTable();
      return;
    }
    var sec = t.closest('tr.b-sec');
    if (sec) {
      var sid = sec.getAttribute('data-sec');
      if (collapsed[sid]) delete collapsed[sid]; else collapsed[sid] = true;
      BP.store('bom.collapsed', JSON.stringify(collapsed));
      renderTable();
      return;
    }
    var cell = t.closest('td.b-edit');
    if (cell && !cell.querySelector('input')) { editCell(cell); return; }
    var tr = t.closest('tr.b-row');
    if (tr && !t.closest('input')) {
      var key = tr.getAttribute('data-key'), row = rowById[key];
      activeCid = key;
      Array.prototype.forEach.call(scroller.querySelectorAll('tr.b-row'), function (el) {
        el.classList.toggle('b-active', el.getAttribute('data-key') === key);
      });
      if (row && row.paths && row.paths.length) BP.send('selectParts', { paths: row.paths });
    }
  });

  pane.addEventListener('dblclick', function (e) {
    var name = e.target.closest('.b-hname');
    if (name && !editingNow) renameCol(name.closest('th'));
  });

  pane.addEventListener('change', function (e) {
    var t = e.target;
    if (t === groupEl) {
      group = groupEl.checked;
      BP.store('bom.group', group ? '1' : '0');
      BP.send('bomOpen', { open: true, group: group });
    } else if (t.getAttribute('data-col') && t.closest('.b-menu')) {
      var id = t.getAttribute('data-col');
      if (t.checked) delete hidden[id]; else hidden[id] = true;
      BP.store('bom.hiddenCols', JSON.stringify(hidden));
      renderTable();
    }
  });

  pane.addEventListener('input', function (e) {
    if (e.target === searchEl) { filter = searchEl.value; renderTable(); }
  });
  pane.addEventListener('keydown', function (e) {
    if (e.target === searchEl) {
      e.stopPropagation();
      if (e.key === 'Escape' && searchEl.value) { searchEl.value = ''; filter = ''; renderTable(); }
    }
  });
  document.addEventListener('click', function (e) {
    if (!e.target.closest('.b-menuwrap')) closeMenu();
  });

  // ---- wiring
  var openSent = false;
  function sendOpen() {
    if (window.adsk && window.adsk.fusionSendData) { BP.send('bomOpen', { open: true, group: group }); openSent = true; return; }
    // Python bridge not ready yet (BOM was the remembered tab): retry while still on this tab
    var tries = 0;
    (function wait() {
      if (BP.activeTab() !== 'bom' || openSent) return;
      if (window.adsk && window.adsk.fusionSendData) { BP.send('bomOpen', { open: true, group: group }); openSent = true; }
      else if (++tries < 60) setTimeout(wait, 100);
    })();
  }

  BP.onTab(function (tab, prev) {
    if (tab === 'bom') {
      openSent = false;
      sendOpen();
      render();
    } else if (prev === 'bom') {
      openSent = false;
      BP.send('bomOpen', { open: false });
      bom = null;
      massBusy = false;
    }
  });
  BP.onRender(function (state, tab) {
    if (tab !== 'bom') return;
    massBusy = false;
    if (state.bom) bom = state.bom;
    render();
  });

  buildShell();
})();
