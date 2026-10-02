/* BuildBook palette. Python owns the data; this renders the `state` it pushes
 * and sends actions back. Every action triggers a fresh state push.
 *
 * Layout: tabs (Steps, Step, Parts, Export, Settings). A thin busy bar shows
 * while an action is waiting for its state. Arrow keys move between steps,
 * Esc goes back to the assembled model. */
(function () {
  'use strict';

  var state = null;
  var checked = {};          // step item path -> true
  var checkedStep = null;    // the step `checked` belongs to
  var checkedUnassigned = {};
  var expandedEx = {};       // explode move id -> parts list open
  var collapsed = {};        // section id -> collapsed in the Steps tab
  var activeTab = 'steps';
  var lastStepId = null;
  var AXES = ['+X', '-X', '+Y', '-Y', '+Z', '-Z'];
  var SPACINGS = [['uniform', 'Uniform'], ['stacked', 'Stacked by position'], ['stackedSelection', 'Stacked in pick order']];
  var $ = function (id) { return document.getElementById(id); };

  var CONTEXT = [['shown', 'Shown'], ['ghosted', 'Ghosted'], ['hidden', 'Hidden']];
  var CONTEXT_STEP = [['inherit', 'Default']].concat(CONTEXT);
  var UNASSIGNED = [['asEarlier', 'Same as earlier steps']].concat(CONTEXT);

  // Actions that open a Fusion dialog or only select: no busy bar (the reply
  // may not come until the dialog closes).
  var NO_BUSY = ['pick', 'editLines', 'addExplode', 'editExplode', 'setAnchor', 'selectItems',
                 'checkItems', 'exportManual', 'importManual', 'chooseExportFolder', 'openExportFolder', 'ready'];

  var ICON = {
    camera: '<svg viewBox="0 0 16 16"><path d="M2 5h2.6l1.2-1.8h4.4L11.4 5H14v7.5H2z"/><circle cx="8" cy="8.6" r="2.2"/></svg>',
    image: '<svg viewBox="0 0 16 16"><rect x="2" y="3" width="12" height="10" rx="1"/><path d="m2.5 11 3.5-3.5 3 3 2-2 2.5 2.5"/></svg>',
    chev: '<svg viewBox="0 0 16 16"><path d="m4 6 4 4 4-4"/></svg>',
    caret: '<svg viewBox="0 0 16 16"><path d="m6 4 4 4-4 4"/></svg>',
    edit: '<svg viewBox="0 0 16 16"><path d="M10.5 2.5l3 3L6 13H3v-3z"/></svg>',
    close: '<svg viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8"/></svg>',
    anchor: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="3"/><path d="M8 1.5v3M8 11.5v3M1.5 8h3M11.5 8h3"/></svg>',
    up: '<svg viewBox="0 0 16 16"><path d="m4 10 4-4 4 4"/></svg>',
    down: '<svg viewBox="0 0 16 16"><path d="m4 6 4 4 4-4"/></svg>',
    grip: '<svg viewBox="0 0 16 16" class="grip-dots"><circle cx="5.5" cy="3.5" r="1.2"/><circle cx="10.5" cy="3.5" r="1.2"/><circle cx="5.5" cy="8" r="1.2"/><circle cx="10.5" cy="8" r="1.2"/><circle cx="5.5" cy="12.5" r="1.2"/><circle cx="10.5" cy="12.5" r="1.2"/></svg>',
    plus: '<svg viewBox="0 0 16 16"><path d="M8 3v10M3 8h10"/></svg>',
    details: '<svg viewBox="0 0 16 16"><path d="M3.5 2.5h6l3 3v8h-9z"/><path d="M9.5 2.5v3h3M5.5 8.5h5M5.5 11h3.5"/></svg>',
    parts: '<svg viewBox="0 0 16 16"><path d="M8 1.5 14 4.8v6.4L8 14.5 2 11.2V4.8z M2 4.8 8 8l6-3.2 M8 8v6.5"/></svg>',
    pick: '<svg viewBox="0 0 16 16"><path d="M3 2.5l9 4.2-3.8 1.3 2.7 4-1.6 1-2.6-4-2.4 2.9z"/></svg>',
    lines: '<svg viewBox="0 0 16 16"><path d="M2 13 4 11M6 9l1.5-1.5M9.5 5.5 11 4"/><circle cx="12.8" cy="3.2" r="1.3"/></svg>',
    linesOff: '<svg viewBox="0 0 16 16"><path d="M2 13 4 11M6 9l1.5-1.5M9.5 5.5 11 4M3 3l10 10"/></svg>',
    explode: '<svg viewBox="0 0 16 16"><rect x="6" y="6" width="4" height="4"/><path d="M2.5 2.5l2.5 2.5M2.5 2.5h2.5M2.5 2.5v2.5M13.5 13.5 11 11M13.5 13.5h-2.5M13.5 13.5v-2.5"/></svg>',
    unexplode: '<svg viewBox="0 0 16 16"><rect x="6" y="6" width="4" height="4"/><path d="M2.5 2.5 5 5M5 5H3M5 5V3M13.5 13.5 11 11M11 11h2M11 11v2"/></svg>',
    bomOff: '<svg viewBox="0 0 16 16"><path d="M3 4h10M3 8h10M3 12h6"/><path d="M11 10.5l3 3M14 10.5l-3 3"/></svg>',
    split: '<svg viewBox="0 0 16 16"><rect x="2" y="4" width="5" height="8"/><rect x="9" y="4" width="5" height="8" stroke-dasharray="2 1.5"/></svg>',
    trash: '<svg viewBox="0 0 16 16"><path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 9h5.8l.6-9M7 7v4.5M9 7v4.5"/></svg>',
    target: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="5.5"/><circle cx="8" cy="8" r="2"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2"/></svg>',
    pen: '<svg viewBox="0 0 16 16"><path d="M10.5 2.5l3 3L6 13H3v-3z"/><path d="M9 4l3 3"/></svg>',
    download: '<svg viewBox="0 0 16 16"><path d="M8 2v8M4.5 6.5 8 10l3.5-3.5M2.5 12.5v1h11v-1"/></svg>',
    upload: '<svg viewBox="0 0 16 16"><path d="M8 10V2M4.5 5.5 8 2l3.5 3.5M2.5 12.5v1h11v-1"/></svg>',
    list: '<svg viewBox="0 0 16 16"><path d="M5.5 4h8M5.5 8h8M5.5 12h8"/><circle cx="2.8" cy="4" r=".6"/><circle cx="2.8" cy="8" r=".6"/><circle cx="2.8" cy="12" r=".6"/></svg>',
    folder: '<svg viewBox="0 0 16 16"><path d="M1.5 4V12.5h13V5.5H7.5L6 4z"/></svg>',
    open: '<svg viewBox="0 0 16 16"><path d="M9 2.5h4.5V7M13.5 2.5 7.5 8.5M12 9.5v4H2.5V4h4"/></svg>',
    pdf: '<svg viewBox="0 0 16 16"><path d="M3.5 1.5h6l3 3v10h-9z"/><path d="M9.5 1.5v3h3"/><path d="M5.5 9h5M5.5 11.5h3"/></svg>',
    eye: '<svg viewBox="0 0 16 16"><path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z"/><circle cx="8" cy="8" r="2"/></svg>',
    book: '<svg viewBox="0 0 16 16"><path d="M2 3.5c2-1 4-1 6 .5 2-1.5 4-1.5 6-.5v9c-2-1-4-1-6 .5-2-1.5-4-1.5-6-.5z M8 4v9"/></svg>',
    plug: '<svg viewBox="0 0 16 16"><path d="M6 1.5v3M10 1.5v3M4 4.5h8v2.5a4 4 0 0 1-8 0zM8 11v3.5"/></svg>',
    refresh: '<svg viewBox="0 0 16 16"><path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2.5v3h-3"/></svg>',
    broom: '<svg viewBox="0 0 16 16"><path d="M11.5 1.5 8 7M5 7.5l4 2.5-2 4.5c-2-.5-4-2-5-4z"/></svg>',
    info: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="6"/><path d="M8 7v4.5M8 4.6v.2"/></svg>',
    keys: '<svg viewBox="0 0 16 16"><rect x="1.5" y="4" width="13" height="8" rx="1"/><path d="M4 7h1M7 7h1M10 7h2M4.5 9.5h7"/></svg>',
  };

  // Static markup asks for icons with data-icon (the label stays in a .lbl span, so code that
  // relabels a button uses setLabel), and card headings carry their explanation in data-info
  // (shown on hover of an (i) icon instead of a paragraph).
  function decorate(root) {
    Array.prototype.forEach.call((root || document).querySelectorAll('[data-icon]'), function (el) {
      if (el.getAttribute('data-decorated')) return;
      el.setAttribute('data-decorated', '1');
      var svg = ICON[el.getAttribute('data-icon')] || '';
      var text = el.innerHTML.trim();
      el.innerHTML = svg + (text ? '<span class="lbl">' + text + '</span>' : '');
    });
    Array.prototype.forEach.call((root || document).querySelectorAll('h3[data-info]'), function (h) {
      if (h.querySelector('.info')) return;
      var i = document.createElement('span');
      i.className = 'info';
      i.title = h.getAttribute('data-info');
      i.innerHTML = ICON.info;
      h.appendChild(i);
    });
  }
  // Cards fold to their header: click the title. Remembered per card (by its title) in this
  // browser; buttons in the header keep working while folded. On the Step tab every card but
  // Step details (data-nofold: always open) starts folded until you open it.
  var folded = {};
  try { folded = JSON.parse(localStorage.getItem('bb.folded') || '{}') || {}; } catch (e) { folded = {}; }
  function cardKey(card) {
    var h = card.querySelector('.card-head h3');
    var lbl = h && h.querySelector('.lbl');
    return ((lbl || h || {}).textContent || '').replace(/\s+\d.*$/, '').trim().toLowerCase();
  }
  function setupFolding() {
    Array.prototype.forEach.call(document.querySelectorAll('.card'), function (card) {
      var h = card.querySelector('.card-head h3');
      if (!h || h.querySelector('.fold') || card.hasAttribute('data-nofold')) return;
      var key = cardKey(card);
      var byDefault = !!card.closest('[data-pane="step"]');
      card.setAttribute('data-card', key);
      var chev = document.createElement('span');
      chev.className = 'fold';
      chev.innerHTML = ICON.chev;
      h.insertBefore(chev, h.firstChild);
      h.title = 'Click to fold / unfold';
      card.classList.toggle('collapsed', key in folded ? !!folded[key] : byDefault);
      h.addEventListener('click', function (e) {
        if (e.target.closest('.info')) return;          // the (i) only explains
        var on = !card.classList.contains('collapsed');
        card.classList.toggle('collapsed', on);
        folded[key] = on ? 1 : 0;           // (an explicit choice, kept over the default)
        try { localStorage.setItem('bb.folded', JSON.stringify(folded)); } catch (err) { /* ignore */ }
      });
    });
  }

  function setLabel(el, text) {
    var l = el.querySelector('.lbl');
    if (l) l.textContent = text; else el.textContent = text;
  }
  decorate();
  setupFolding();

  // ------------------------------------------------------------ messaging + busy

  var busyTimer = null, busyLimit = null;

  function send(action, payload) {
    if (NO_BUSY.indexOf(action) < 0) setBusy(true);
    if (window.adsk && window.adsk.fusionSendData) {
      window.adsk.fusionSendData(action, JSON.stringify(payload || {}));
    }
  }

  function setBusy(on) {
    clearTimeout(busyTimer);
    clearTimeout(busyLimit);
    if (on) {
      // Only show for actions that take a moment, and never forever.
      busyTimer = setTimeout(function () { $('busy').classList.add('on'); }, 150);
      busyLimit = setTimeout(function () { setBusy(false); }, 60000);
    } else {
      $('busy').classList.remove('on');
    }
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function fillSelect(el, options, value) {
    el.innerHTML = options.map(function (o) {
      return '<option value="' + o[0] + '"' + (o[0] === value ? ' selected' : '') + '>' + o[1] + '</option>';
    }).join('');
  }

  function stepById(id) {
    if (!state || !state.manual) return null;
    var found = null;
    state.manual.sections.forEach(function (sec) {
      sec.steps.forEach(function (st) { if (st.id === id) found = st; });
    });
    return found;
  }

  function allSteps() {
    var out = [];
    state.manual.sections.forEach(function (sec, si) {
      sec.steps.forEach(function (st, ti) { out.push({ step: st, section: sec, si: si + 1, ti: ti + 1 }); });
    });
    return out;
  }

  function store(key, value) { try { localStorage.setItem('buildbook.' + key, value); } catch (e) { /* private mode */ } }
  function recall(key) { try { return localStorage.getItem('buildbook.' + key); } catch (e) { return null; } }

  // ------------------------------------------------------------ tabs

  function showTab(name) {
    activeTab = name;
    store('tab', name);
    Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (t) {
      t.classList.toggle('active', t.getAttribute('data-tab') === name);
    });
    Array.prototype.forEach.call(document.querySelectorAll('.pane'), function (p) {
      p.classList.toggle('active', p.getAttribute('data-pane') === name);
    });
  }

  $('tabs').addEventListener('click', function (e) {
    var tab = e.target.closest('.tab');
    if (tab && !tab.disabled) showTab(tab.getAttribute('data-tab'));
  });

  // ------------------------------------------------------------ render

  function render() {
    setBusy(false);
    if (state.error) {
      $('error').textContent = state.error;
      $('error').classList.remove('hidden');
      return;
    }
    $('error').classList.add('hidden');
    if (document.activeElement !== $('manualTitle')) $('manualTitle').value = state.manual.title;
    var cover = state.manual.cover || {};
    $('setCoverPic').checked = cover.enabled !== false;
    $('coverActions').classList.toggle('hidden', cover.enabled === false);
    var coverThumb = state.thumbs && state.thumbs.cover;
    $('coverThumb').style.backgroundImage = coverThumb ? "url('" + coverThumb + "')" : '';
    $('coverThumb').textContent = coverThumb ? '' : 'No preview yet';
    $('coverInfo').textContent = (cover.camera ? '\u2713 View saved' : 'No saved view') +
      ((cover.annotations || []).length ? ' \u00b7 ' + cover.annotations.length + ' note' + (cover.annotations.length === 1 ? '' : 's') : '');
    $('coverInfo').title = cover.camera ? '' : 'No view saved: the cover uses the current camera';
    // Opening a step from the list shows it; switching steps with the keyboard stays put.
    if (state.currentStepId && state.currentStepId !== lastStepId && pendingOpen) {
      showTab('step');
    }
    pendingOpen = false;
    lastStepId = state.currentStepId;
    renderWarnings();
    renderTree();
    renderStep();
    renderUnassigned();
    renderSettings();
    renderExport();
    renderNotice();
  }

  var noticeTimer = null;
  function renderNotice() {
    if (!state.notice) return;
    $('notice').textContent = state.notice;
    $('notice').classList.remove('hidden');
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(function () { $('notice').classList.add('hidden'); }, 8000);
  }

  function renderWarnings() {
    var parts = [];
    if (state.missing.length) {
      parts.push('<b>' + state.missing.length + ' part(s) no longer in the design</b><ul>' +
        state.missing.slice(0, 8).map(function (m) {
          return '<li>' + esc(m.name || m.path) + ' <span class="sub">(' + esc(m.step) + ')</span></li>';
        }).join('') + '</ul>');
    }
    $('warnings').innerHTML = parts.join('');
    $('warnings').classList.toggle('hidden', !parts.length);
  }

  function thumbStyle(id) {
    var url = state.thumbs && state.thumbs[id];
    return url ? ' style="background-image:url(\'' + url + '\')"' : '';
  }

  // Steps with a saved view but no thumbnail on this computer (thumbnails are kept per computer).
  function renderThumbsMissing() {
    var missing = 0;
    state.manual.sections.forEach(function (sec) {
      sec.steps.forEach(function (st) { if (st.camera && !(state.thumbs && state.thumbs[st.id])) missing++; });
    });
    $('thumbsMissing').classList.toggle('hidden', missing < 1);
    $('thumbsMissingText').textContent = missing + ' step' + (missing === 1 ? '' : 's') +
      ' without a thumbnail on this computer';
  }

  function renderTree() {
    renderThumbsMissing();
    var n = 0;
    var html = state.manual.sections.map(function (sec) {
      var views = 0, exported = 0;
      var steps = sec.steps.map(function (st) {
        n += 1;
        if (st.camera) views += 1;
        if (st.exportedAt) exported += 1;
        var thumb = state.thumbs && state.thumbs[st.id];
        return '<div class="step' + (st.id === state.currentStepId ? ' active' : '') + '" data-step="' + st.id + '" data-act="open">' +
          '<span class="grip" title="Drag to reorder (or into another section)">' + ICON.grip + '</span>' +
          '<span class="thumb"' + thumbStyle(st.id) + '>' + (thumb ? '' : n) + '</span>' +
          '<span class="num">' + n + '</span>' +
          '<span class="name" title="' + esc(st.title) + '">' + esc(st.title) + '</span>' +
          (st.prep ? '<span class="prep-tag" title="Preparation step">prep</span>' : '') +
          ((st.repeat || 1) > 1 ? '<span class="prep-tag" title="Do this step ' + st.repeat + ' times">×' + st.repeat + '</span>' : '') +
          '<span class="meta" title="Parts">' + st.items.length + '</span>' +
          '<span class="status-icons">' +
            '<span class="' + (st.camera ? 'on' : 'off') + '" title="' +
              (st.camera ? 'Screenshot view saved' : 'No screenshot view saved yet') + '">' + ICON.camera + '</span>' +
            '<span class="' + (st.exportedAt ? 'ok' : 'off') + '" title="' +
              (st.exportedAt ? 'Exported ' + esc(st.exportedAt) : 'Not exported yet') + '">' + ICON.image + '</span>' +
          '</span>' +
          '<span class="tools">' +
            '<button data-act="stepRename" title="Rename">' + ICON.edit + '</button>' +
            '<button data-act="stepDelete" class="danger" title="Delete step">' + ICON.close + '</button>' +
          '</span></div>';
      }).join('');
      var total = sec.steps.length;
      var progress = total ? '<span class="progress">' +
        '<span class="' + (views === total ? 'done' : '') + '" title="Steps with a saved view">' + ICON.camera + views + '/' + total + '</span>' +
        '<span class="' + (exported === total ? 'done' : '') + '" title="Steps exported">' + ICON.image + exported + '/' + total + '</span>' +
        '</span>' : '';
      return '<div class="section' + (collapsed[sec.id] ? ' collapsed' : '') + '" data-section="' + sec.id + '">' +
        '<div class="section-head">' +
          '<span class="chev" data-act="secToggle" title="Collapse / expand">' + ICON.chev + '</span>' +
          '<span class="name" data-act="secRename" title="Double-click to rename">' + esc(sec.title) + '</span>' +
          progress +
          '<span class="tools">' +
            '<button data-act="secPicture" class="' + (sec.image && sec.image.enabled ? 'on' : '') +
              '" title="Section picture in the PDF (the assembly at the end of this section)">' + ICON.image + '</button>' +
            '<button data-act="secUp" title="Move up">' + ICON.up + '</button>' +
            '<button data-act="secDown" title="Move down">' + ICON.down + '</button>' +
            '<button data-act="secDelete" class="danger" title="Delete section">' + ICON.close + '</button>' +
          '</span></div>' +
        (sec.image && sec.image.enabled ? (function () {
          // The section's picture (in the PDF, at the end of the section): a big preview like the
          // step's View & image, with its view and annotate buttons beside it.
          var url = state.thumbs && state.thumbs['section-' + sec.id];
          var nAnn = (sec.image.annotations || []).length;
          return '<div class="sec-picture">' +
            '<span class="thumb big" title="Section picture preview (updates when you save its view or annotate it)"' +
              (url ? ' style="background-image:url(&quot;' + url + '&quot;)"' : '') + '>' + (url ? '' : 'No preview yet') + '</span>' +
            '<div class="sec-pic-side">' +
              '<div class="sec-pic-title">' + ICON.image + '<span>Section picture</span></div>' +
              '<div class="sub">' + (sec.image.camera ? '✓ View saved' : 'Uses the last step’s view') + '</div>' +
              '<button class="btn small" data-act="secPicView" title="Save the current camera as this section picture’s view">' +
                ICON.camera + '<span class="lbl">' + (sec.image.camera ? 'Update view' : 'Save view') + '</span></button>' +
              '<button class="btn small" data-act="secPicGo" title="Show the assembly at the end of this section, at its view">' +
                ICON.target + '<span class="lbl">Go to view</span></button>' +
              '<button class="btn small" data-act="secPicAnnotate" title="Draw on the section picture">' +
                ICON.pen + '<span class="lbl">Annotate' + (nAnn ? ' (' + nAnn + ')' : '') + '</span></button>' +
            '</div></div>';
        })() : '') +
        // A dashed "+ Add step" row closes every section's list: the quick way to add one.
        '<div class="steps">' + (steps || '') +
          '<button class="add-step" data-act="secAddStep" title="Add a step at the end of ' + esc(sec.title) + '">' +
            ICON.plus + '<span>Add step</span></button></div>' +
        '</div>';
    }).join('');
    // A dashed "+ Add section" card closes the list (like "+ Add step" inside each section).
    // No sections yet: the card is the blue, obvious first thing to click.
    $('tree').innerHTML = (html || '<div class="empty-state"><p>No sections yet.</p><p class="sub">Start with a section, e.g. "Frame".</p></div>') +
      '<button class="add-step add-section' + (html ? '' : ' first') + '" data-act="addSection" title="Add a section at the end">' +
        ICON.plus + '<span>Add section</span></button>';
  }

  // The step's parts grouped with quantities, as the PDF lists them ("each time" for a repeated step).
  function renderStepBom(detail, step) {
    var rows = {}, order = [];
    detail.items.forEach(function (it) {
      var elsewhere = it.bom === false;
      // One line per component (like the PDF), named after it, not the instance ("KP001_12", not "KP001_12:4").
      var key = (it.bomKey || it.name) + (elsewhere ? '|x' : '');
      if (!rows[key]) { rows[key] = { name: it.bomName || it.name, component: it.component, qty: 0, missing: false, elsewhere: elsewhere, hw: !!it.hw }; order.push(key); }
      rows[key].qty += 1;
      if (it.missing) rows[key].missing = true;
    });
    var times = Math.max(1, step.repeat || 1);
    var list = order.map(function (k) { return rows[k]; }).sort(function (a, b) {
      if (a.hw !== b.hw) return a.hw ? 1 : -1;               // parts, then hardware (as in the PDF)
      return a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });
    });
    var total = list.reduce(function (n, r) { return n + r.qty; }, 0);
    $('bomCount').textContent = total ? total + (times > 1 ? ' × ' + times : '') : '';
    if (!list.length) { $('stepBom').innerHTML = '<div class="empty">No parts in this step yet.</div>'; return; }
    $('stepBom').innerHTML = '<table class="bom-table"><thead><tr><th class="q">Qty</th><th>Part</th>' +
      (times > 1 ? '<th class="q" title="Qty × ' + times + ' repeats">Total</th>' : '') + '</tr></thead><tbody>' +
      list.map(function (r, i) {
        var head = r.hw && (i === 0 || !list[i - 1].hw)
          ? '<tr class="hw-head"><td colspan="' + (times > 1 ? 3 : 2) + '">Hardware</td></tr>' : '';
        return head + '<tr class="' + (r.missing ? 'missing' : '') + (r.elsewhere ? ' elsewhere' : '') + '"' +
          (r.missing ? ' title="Missing from the design"' : r.elsewhere ? ' title="Flagged Not in BOM: left out of the PDF"' : '') +
          '><td class="q">' + r.qty + '</td>' +
          '<td title="' + esc(r.component || r.name) + '">' + esc(r.name) + (r.elsewhere ? ' <span class="nobom-tag" title="Flagged Not in BOM: left out of the PDF">not in PDF</span>' : '') + '</td>' +
          (times > 1 ? '<td class="q">' + r.qty * times + '</td>' : '') + '</tr>';
      }).join('') + '</tbody></table>';
  }

  function renderStep() {
    var detail = state.step;
    var step = detail && stepById(detail.id);
    $('stepPanel').classList.toggle('hidden', !step);
    $('noStep').classList.toggle('hidden', !!step);
    var list = allSteps();
    var pos = -1;
    list.forEach(function (x, i) { if (step && x.step.id === step.id) pos = i; });
    $('tabStep').innerHTML = step ? 'Step <span class="tab-count">' + (pos + 1) + '/' + list.length + '</span>' : 'Step';
    if (!step) return;
    renderStepBom(detail, step);

    var where = list[pos];
    $('stepCrumb').textContent = where.section.title + ' · Step ' + where.ti;
    $('stepTitle').textContent = step.title;
    $('btnPrevStep').disabled = pos <= 0;
    $('btnNextStep').disabled = pos >= list.length - 1;
    $('editBadge').classList.toggle('hidden', !state.edit);
    $('stepPrep').checked = !!step.prep;
    fillSelect($('ctxEarlier'), CONTEXT_STEP, step.earlier);
    fillSelect($('ctxLater'), CONTEXT_STEP, step.later);
    var note = $('stepDistanceNote');
    note.classList.toggle('hidden', !detail.defaultOwn);
    if (detail.defaultOwn) {
      note.innerHTML = 'This step uses its own default explode distance (' + esc(String(detail.defaultDistance)) + ' ' +
        esc(state.units || '') + '), not the one in Settings. ' +
        '<button class="btn link" id="useGlobalDistance">Use the Settings default</button>';
    }
    if (document.activeElement !== $('notes')) { $('notes').value = step.notes || ''; fitNotes(); }
    if (document.activeElement !== $('stepName')) $('stepName').value = step.title || '';
    if (document.activeElement !== $('stepRepeat')) $('stepRepeat').value = step.repeat || 1;

    $('btnGoView').disabled = !detail.hasCamera;
    setLabel($('btnSaveView'), detail.hasCamera ? 'Update view' : 'Save view');
    var thumb = state.thumbs && state.thumbs[step.id];
    $('stepThumb').style.backgroundImage = thumb ? "url('" + thumb + "')" : '';
    $('stepThumb').textContent = thumb ? '' : 'No preview yet';
    var nAnn = (step.annotations || []).length;
    setLabel($('btnAnnotate'), nAnn ? 'Annotate (' + nAnn + ')' : 'Annotate');
    $('viewHint').innerHTML = (detail.hasCamera ? '<span class="ok">&#x2713; View saved</span>'
                                                : '<span class="warn-text">No saved view</span>') +
      ' &middot; ' + esc(detail.image.text);
    $('viewHint').title = (detail.hasCamera ? 'Opening this step goes to its saved view.'
                                            : 'No saved view yet: export uses the current camera.') +
      '\nFile: ' + detail.imageName + (step.exportedAt ? '\nLast exported ' + step.exportedAt : '');

    if (detail.id !== checkedStep) { checked = {}; checkedStep = detail.id; }   // ticks belong to one step
    var paths = detail.items.map(function (i) { return i.path; });
    Object.keys(checked).forEach(function (p) { if (paths.indexOf(p) < 0) delete checked[p]; });

    $('items').innerHTML = detail.items.map(function (it) {
      return '<li class="' + (it.missing ? 'missing' : '') + (it.bom === false ? ' no-bom' : '') + '" data-path="' + esc(it.path) + '">' +
        '<input type="checkbox" data-act="check"' + (checked[it.path] ? ' checked' : '') + '>' +
        partThumbHtml(it.thumb, it.path) +
        '<span class="name" data-act="select" title="Select in Fusion">' +
          '<div>' + esc(it.name) + '</div>' +
          '<div class="sub">' + esc(it.component) +
            (it.bom === false ? ' <span class="nobom-tag" title="Not counted in the section and full parts lists">not in BOM</span>' : '') +
          '</div></span>' +
        '<span class="in-moves" title="' + (it.moves.length ? 'In explode move ' + it.moves.join(', ') : 'Not exploded') + '">' +
          it.moves.map(function (m) { return '<b>' + m + '</b>'; }).join('') + '</span>' +
        '<button class="icon-btn anchor-btn' + (it.anchor ? ' on' : '') + '" data-act="anchor" title="' +
          (it.anchor ? 'Trail lines start at a picked point. Click to change or reset.'
                     : 'Trail lines start at the part centre. Click to pick another point (e.g. a hole).') +
          '">' + ICON.anchor + '</button>' +
        '<button class="icon-btn danger" data-act="remove" title="Remove from this step">' + ICON.close + '</button>' +
        '</li>';
    }).join('');
    renderExplodes(detail);
    $('itemCount').textContent = detail.items.length;
    $('exCount').textContent = detail.explodes.length || '';
    $('viewSum').textContent = detail.hasCamera ? '\u2713' : 'no view';
    $('viewSum').title = detail.hasCamera ? 'View saved' : 'No saved view yet';
    $('viewSum').classList.toggle('ok', !!detail.hasCamera);
    $('checkAll').checked = detail.items.length > 0 && paths.every(function (p) { return checked[p]; });
    // Bulk bar: only while parts are ticked.
    var ticked = checkedPaths();
    $('bulkBar').classList.toggle('hidden', !ticked.length);
    $('checkedCount').textContent = ticked.length ? ticked.length + ' ticked' : '';
    setLabel($('btnNoBom'), ticked.length && allOutOfBom(ticked) ? 'Count in BOM' : 'Not in BOM');
  }

  // ------------------------------------------------------------ part pictures
  // Python sends a coarse mesh of each part (lib/partthumbs.py); it's drawn here as a small shaded
  // isometric picture and kept in localStorage under the part's key (component + its revision).
  var PT_SIZE = 96;                    // drawn at 2x for a 48 px slot
  var ptCache = {}, ptWanted = {}, ptAsked = {}, ptTimer = null;
  function ptGet(key) {
    if (!key) return null;
    if (ptCache[key] === undefined) {
      try { ptCache[key] = localStorage.getItem('bb.pt.' + key) || null; } catch (e) { ptCache[key] = null; }
    }
    return ptCache[key];
  }
  function partThumbHtml(key, path) {
    var url = ptGet(key);
    if (!url && key && !ptAsked[key]) { ptWanted[key] = path; schedulePartMeshes(); }
    return '<span class="pthumb" data-pt="' + esc(key || '') + '"' +
      (url ? ' style="background-image:url(' + url + ')"' : '') + '></span>';
  }
  function schedulePartMeshes() {
    clearTimeout(ptTimer);
    ptTimer = setTimeout(function () {
      var keys = Object.keys(ptWanted).slice(0, 24);
      if (!keys.length) return;
      var paths = keys.map(function (k) { ptAsked[k] = true; var p = ptWanted[k]; delete ptWanted[k]; return p; });
      send('partMeshes', { paths: paths, keys: keys });
    }, 150);
  }
  function gotPartMeshes(meshes) {
    Object.keys(meshes).forEach(function (key) {
      var url = drawPartThumb(meshes[key]);
      ptCache[key] = url;
      try { localStorage.setItem('bb.pt.' + key, url); } catch (e) { /* storage full: still shown this session */ }
      Array.prototype.forEach.call(document.querySelectorAll('.pthumb[data-pt="' + key + '"]'), function (el) {
        el.style.backgroundImage = 'url(' + url + ')';
      });
    });
    if (Object.keys(ptWanted).length) schedulePartMeshes();     // the next batch
  }
  function drawPartThumb(mesh) {
    var p = mesh.p, t = mesh.t, n = p.length / 3;
    // Isometric view: turn 45 deg about Z (Fusion is Z up when the design is), tilt 35 deg.
    var cy = Math.cos(Math.PI / 4), sy = Math.sin(Math.PI / 4), cp = Math.cos(0.615), sp = Math.sin(0.615);
    var X = new Float32Array(n), Y = new Float32Array(n), Z = new Float32Array(n);
    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (var i = 0; i < n; i++) {
      var x = p[3 * i], y = p[3 * i + 1], z = p[3 * i + 2];
      var x1 = x * cy - y * sy, y1 = x * sy + y * cy;            // yaw
      var y2 = y1 * cp - z * sp, z2 = y1 * sp + z * cp;          // pitch (y2 = depth, z2 = up)
      X[i] = x1; Y[i] = -z2; Z[i] = y2;
      minX = Math.min(minX, x1); maxX = Math.max(maxX, x1); minY = Math.min(minY, -z2); maxY = Math.max(maxY, -z2);
    }
    var span = Math.max(maxX - minX, maxY - minY) || 1, pad = PT_SIZE * 0.08, k = (PT_SIZE - 2 * pad) / span;
    var ox = pad + ((PT_SIZE - 2 * pad) - (maxX - minX) * k) / 2, oy = pad + ((PT_SIZE - 2 * pad) - (maxY - minY) * k) / 2;
    var tris = [];
    for (var j = 0; j < t.length; j += 3) {
      var a = t[j], b = t[j + 1], c = t[j + 2];
      // face normal (view space) for flat shading; depth for painter's order
      var ux = X[b] - X[a], uy = Y[b] - Y[a], uz = Z[b] - Z[a], vx = X[c] - X[a], vy = Y[c] - Y[a], vz = Z[c] - Z[a];
      var nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
      var len = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1;
      tris.push([a, b, c, (Z[a] + Z[b] + Z[c]) / 3, nx / len, ny / len, nz / len]);
    }
    tris.sort(function (m, q) { return q[3] - m[3]; });          // far first
    var cv = document.createElement('canvas');
    cv.width = cv.height = PT_SIZE;
    var ctx = cv.getContext('2d');
    ctx.lineJoin = 'round';
    tris.forEach(function (tr) {
      var light = Math.abs(tr[4] * -0.35 + tr[5] * -0.55 + tr[6] * -0.76);   // both sides lit (meshes vary)
      var v = Math.round(110 + 120 * light);
      var col = 'rgb(' + Math.round(v * 0.86) + ',' + Math.round(v * 0.92) + ',' + v + ')';
      ctx.fillStyle = col; ctx.strokeStyle = col; ctx.lineWidth = 0.6;
      ctx.beginPath();
      ctx.moveTo(ox + (X[tr[0]] - minX) * k, oy + (Y[tr[0]] - minY) * k);
      ctx.lineTo(ox + (X[tr[1]] - minX) * k, oy + (Y[tr[1]] - minY) * k);
      ctx.lineTo(ox + (X[tr[2]] - minX) * k, oy + (Y[tr[2]] - minY) * k);
      ctx.closePath(); ctx.fill(); ctx.stroke();
    });
    return cv.toDataURL('image/png');
  }

  // Parts tab: a tree. Assemblies (and split components) start collapsed; tick one to add it
  // whole, or open it and tick its parts. A ticked assembly covers its parts (shown ticked, locked).
  var openUnassigned = {};
  try { openUnassigned = JSON.parse(localStorage.getItem('bb.openUnassigned') || '{}'); } catch (e) { /* none */ }
  function parentPath(p) { var i = p.lastIndexOf('+'); return i < 0 ? '' : p.slice(0, i); }
  function hasCheckedAncestor(p) {
    for (var a = parentPath(p); a; a = parentPath(a)) if (checkedUnassigned[a]) return true;
    return false;
  }
  function checkedUnassignedPaths() {
    return Object.keys(checkedUnassigned).filter(function (p) { return checkedUnassigned[p] && !hasCheckedAncestor(p); });
  }
  function renderUnassigned() {
    var list = state.unassigned;
    var filter = $('unassignedFilter').value.trim().toLowerCase();
    $('unassignedCount').textContent = list.filter(function (u) { return !u.group; }).length;
    var byPath = {};
    list.forEach(function (u) { byPath[u.path] = u; });
    Object.keys(checkedUnassigned).forEach(function (p) { if (!byPath[p]) delete checkedUnassigned[p]; });
    var shown;
    if (filter) {
      // Matches, with the assemblies they're in (opened).
      var keep = {};
      list.forEach(function (u) {
        if ((u.name + ' ' + u.path + ' ' + u.component).toLowerCase().indexOf(filter) < 0) return;
        for (var p = u.path; p; p = parentPath(p)) keep[p] = true;
      });
      shown = list.filter(function (u) { return keep[u.path]; });
    } else {
      // Rows whose assemblies are all open.
      shown = list.filter(function (u) {
        for (var a = parentPath(u.path); a; a = parentPath(a)) if (byPath[a] && !openUnassigned[a]) return false;
        return true;
      });
    }
    $('unassigned').innerHTML = shown.map(function (u) {
      var locked = hasCheckedAncestor(u.path);
      var open = filter ? true : !!openUnassigned[u.path];
      var sub = u.group
        ? (u.split ? 'Split into bodies' : 'Assembly') + ' · ' +
          (u.leaves < u.total ? u.leaves + ' of ' + u.total + ' parts not in a step' : u.total + ' part' + (u.total === 1 ? '' : 's'))
        : u.path;
      return '<li data-path="' + esc(u.path) + '" class="' + (u.group ? 'u-group' : '') + '" style="--ud:' + (u.depth || 0) + '">' +
        (u.group ? '<button class="u-caret' + (open ? ' open' : '') + '" data-act="toggleU" title="' + (open ? 'Collapse' : 'Show its parts') + '">' + ICON.caret + '</button>'
          : '<span class="u-caret none"></span>') +
        '<input type="checkbox" data-act="checkU"' + (checkedUnassigned[u.path] || locked ? ' checked' : '') + (locked ? ' disabled title="Added with its assembly"' : '') + '>' +
        partThumbHtml(u.thumb, u.path) +
        '<span class="name" data-act="select" title="Select in Fusion"><div>' + esc(u.name) + '</div>' +
        '<div class="sub">' + esc(sub) + '</div></span></li>';
    }).join('');
    $('btnAddChecked').disabled = !state.step;
    $('btnAddChecked').title = state.step ? 'Add the checked parts to the open step' : 'Open a step first';
  }

  function renderExplodes(detail) {
    var upto = detail.upto;
    $('btnShowAll').classList.toggle('hidden', !upto);
    $('explodes').innerHTML = detail.explodes.map(function (ex) {
      var axisOptions = AXES.map(function (a) {
        return '<option value="' + a + '"' + (a === ex.axis ? ' selected' : '') + '>' + a + '</option>';
      }).join('');
      if (AXES.indexOf(ex.axis) < 0) {
        axisOptions = '<option value="" selected disabled>' + esc(ex.direction) + '</option>' + axisOptions;
      }
      var open = !!expandedEx[ex.id];
      return '<li class="ex' + (ex.id === upto ? ' active' : '') + '" data-id="' + ex.id + '">' +
        '<div class="ex-row">' +
          '<span class="grip" title="Drag to reorder">' + ICON.grip + '</span>' +
          '<span class="num">' + ex.number + '</span>' +
          '<span class="name" data-act="scrub" title="Show the step up to this move. Double-click to rename.">' +
            esc(ex.label) + '<span class="sub"> &middot; ' + ex.parts.length + '</span></span>' +
          '<select class="ex-axis" title="Direction (X/Y/Z amounts and picked directions: use edit)">' + axisOptions + '</select>' +
          '<input class="ex-dist dist-input' + (ex.own ? ' own' : '') + '" value="' + (ex.own ? ex.distance : '') + '"' +
            ' placeholder="' + ex.distance + '" title="' + (ex.own ? 'This move\'s own distance. Clear to follow the step default.' : 'Follows the step default.') + '">' +
          trailButton(ex.trail, 'Trail lines for this move', 'exTrail') +
          '<button class="icon-btn" data-act="exEdit" title="Edit: parts, direction, distance, spacing">' + ICON.edit + '</button>' +
          '<button class="icon-btn" data-act="exExpand" title="Parts in this move">' + (open ? ICON.up : ICON.down) + '</button>' +
          '<button class="icon-btn danger" data-act="exDelete" title="Delete this move">' + ICON.close + '</button>' +
        '</div>' +
        (open ? explodeParts(ex) : '') +
        '</li>';
    }).join('');
    if (!detail.explodes.length) {
      $('explodes').innerHTML = '<li class="empty">No moves yet. Check parts above (or select them in Fusion), then <b>+ Add move</b>.</li>';
    }
  }

  function explodeParts(ex) {
    var spacing = SPACINGS.map(function (s) {
      return '<option value="' + s[0] + '"' + (s[0] === ex.spacing ? ' selected' : '') + '>' + s[1] + '</option>';
    }).join('');
    return '<div class="ex-parts">' +
      '<div class="ex-part"><span class="mdir">Spacing</span><select class="ex-spacing">' + spacing + '</select></div>' +
      ex.parts.map(function (p) {
        return '<div class="ex-part' + (p.missing ? ' missing' : '') + '" data-path="' + esc(p.path) + '">' +
          '<span class="mdir" data-act="partSelect" title="Select in Fusion">' + esc(p.name) + '</span>' +
          '<input class="part-dist dist-input' + (p.own ? ' own' : '') + '" value="' + (p.own ? p.distance : '') + '"' +
            ' placeholder="' + ex.distance + '" title="' + (p.own ? 'This part\'s own distance. Clear to use the move\'s.' : 'Uses the move\'s distance' + (ex.spacing !== 'uniform' ? ' (times its stack position)' : '')) + '">' +
          trailButton(p.trail, 'Trail line for this part in this move', 'partTrail') +
          '<button class="icon-btn danger" data-act="partRemove" title="Take this part out of the move">' + ICON.close + '</button>' +
          '</div>';
      }).join('') + '</div>';
  }

  // state: true/false, or "all" | "some" | "none" for several lines together.
  function trailButton(state, title, act) {
    var on = state === true || state === 'all';
    var partial = state === 'some';
    return '<button class="trail-toggle' + (on ? ' on' : '') + (partial ? ' partial' : '') + '" data-act="' + act + '"' +
      ' data-on="' + (on ? '1' : '') + '"' +
      ' title="' + esc(title + (on ? '. On, click to turn off' : partial ? '. Some on, click to turn all on' : '. Off, click to turn on')) + '">' +
      '<svg viewBox="0 0 22 10" aria-hidden="true">' +
      '<line x1="1" y1="5" x2="21" y2="5" stroke-width="2" stroke-dasharray="4 3"/>' +
      (on || partial ? '' : '<line x1="4" y1="9" x2="18" y2="1" stroke-width="1.5"/>') +
      '</svg></button>';
  }

  function renderExport() {
    var img = state.manual.settings.image || {};
    $('exportFolder').textContent = state.exportFolder;
    $('exportFolder').title = state.exportFolder;
    if (document.activeElement !== $('setImgW')) $('setImgW').value = img.width;
    fillSelect($('setRatio'), state.ratios, img.ratio || '4:3');
    $('setTransparent').checked = !!img.transparent;
    var anySteps = state.manual.sections.some(function (s) { return s.steps.length; });
    $('btnExportAll').disabled = !anySteps;
    $('btnExportAllTo').disabled = !anySteps;
    $('setAskFolder').checked = !!state.manual.settings.askFolder;
    $('setShowFrame').checked = !!state.manual.settings.showCropFrame;
    setLabel($('btnExportAll'), state.manual.settings.askFolder ? 'Export all…' : 'Export all');
  }

  function renderSettings() {
    var s = state.manual.settings;
    fillSelect($('setEarlier'), CONTEXT, s.earlier);
    fillSelect($('setLater'), CONTEXT, s.later);
    fillSelect($('setUnassigned'), UNASSIGNED, s.unassigned);
    $('setGhost').value = s.ghostOpacity;
    if (document.activeElement !== $('setDistance')) $('setDistance').value = state.manualDefault;
    Array.prototype.forEach.call(document.querySelectorAll('[data-units]'), function (el) {
      el.textContent = state.units;
    });
    $('setTrailColor').value = s.trail.color;
    $('setTrailWeight').value = s.trail.weight;
    $('setTrailStyle').value = s.trail.style;
    $('setEdges').checked = !!s.drawEdges;
    $('setShortHw').checked = s.shortHardwareNames !== false;
  }

  // ------------------------------------------------------------ helpers

  function checkedPaths() {
    return Object.keys(checked).filter(function (p) { return checked[p]; });
  }

  function inlineRename(el, current, done) {
    var input = document.createElement('input');
    input.className = 'rename';
    input.value = current;
    el.replaceWith(input);
    input.focus();
    input.select();
    var finish = function (commit) {
      input.removeEventListener('blur', onBlur);
      var v = input.value.trim();
      if (commit && v && v !== current) done(v); else render();
    };
    var onBlur = function () { finish(true); };
    input.addEventListener('blur', onBlur);
    input.addEventListener('keydown', function (e) {
      e.stopPropagation();
      if (e.key === 'Enter') finish(true);
      if (e.key === 'Escape') finish(false);
    });
    input.addEventListener('click', function (e) { e.stopPropagation(); });
  }

  // Browser dialogs are unreliable in Fusion palettes: destructive buttons
  // need a second click within 3 s instead.
  function armed(button, fn) {
    if (button.getAttribute('data-armed')) { button.removeAttribute('data-armed'); fn(); return; }
    var label = button.innerHTML;
    button.setAttribute('data-armed', '1');
    button.innerHTML = 'Sure?';
    setTimeout(function () {
      if (button.isConnected && button.getAttribute('data-armed')) {
        button.removeAttribute('data-armed');
        button.innerHTML = label;
      }
    }, 3000);
  }

  function settings(patch) { send('setSettings', { settings: patch }); }

  var pendingOpen = false;
  function openStep(id, switchTab) {
    pendingOpen = !!switchTab;
    send('openStep', { id: id });
  }

  function stepOffset(delta) {
    var list = allSteps();
    if (!list.length) return;
    var pos = -1;
    list.forEach(function (x, i) { if (x.step.id === state.currentStepId) pos = i; });
    var next = pos < 0 ? (delta > 0 ? 0 : list.length - 1) : pos + delta;
    if (next < 0 || next >= list.length) return;
    openStep(list[next].step.id, false);
  }

  // ------------------------------------------------------------ keyboard

  document.addEventListener('keydown', function (e) {
    var t = e.target;
    var typing = t && (t.tagName === 'INPUT' || t.tagName === 'SELECT' || t.tagName === 'TEXTAREA');
    if (e.key === 'Enter' && t.matches && t.matches('.dist-input, #setDistance')) {
      t.blur();   // commits the distance box (fires its change event)
      return;
    }
    if (typing || !state || state.error || drag || exDrag) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowRight') { stepOffset(1); e.preventDefault(); }
    else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') { stepOffset(-1); e.preventDefault(); }
    else if (e.key === 'Escape') { send('closeView'); }
  });

  // ------------------------------------------------------------ drag to reorder steps
  // Mouse events rather than HTML5 drag-and-drop, which is unreliable in
  // Fusion's embedded browser. Panes scroll, not the page.

  var drag = null;

  function activePane() { return document.querySelector('.pane.active'); }

  function autoScroll(e) {
    var pane = activePane();
    if (!pane) return;
    var r = pane.getBoundingClientRect();
    if (e.clientY < r.top + 24) pane.scrollTop -= 12;
    else if (e.clientY > r.bottom - 24) pane.scrollTop += 12;
  }

  function dropTarget(y) {
    var sections = Array.prototype.slice.call(document.querySelectorAll('#tree .section'));
    if (!sections.length) return null;
    var sec = sections[0];
    sections.forEach(function (s) { if (s.getBoundingClientRect().top <= y) sec = s; });
    var steps = Array.prototype.slice.call(sec.querySelectorAll('.step'))
      .filter(function (el) { return el !== drag.el; });
    var index = steps.length;
    for (var i = 0; i < steps.length; i++) {
      var r = steps[i].getBoundingClientRect();
      if (y < r.top + r.height / 2) { index = i; break; }
    }
    var lineY;
    if (index < steps.length) lineY = steps[index].getBoundingClientRect().top;
    else if (steps.length) lineY = steps[steps.length - 1].getBoundingClientRect().bottom;
    else lineY = sec.querySelector('.section-head').getBoundingClientRect().bottom;
    return { sectionId: sec.getAttribute('data-section'), index: index, lineY: lineY, sec: sec };
  }

  function onDragMove(e) {
    if (!drag) return;
    if (!drag.started && Math.abs(e.clientY - drag.startY) < 3) return;
    drag.started = true;
    drag.el.classList.add('dragging');
    document.body.classList.add('is-dragging');
    autoScroll(e);
    drag.target = dropTarget(e.clientY);
    if (!drag.target) return;
    var secRect = drag.target.sec.getBoundingClientRect();
    drag.line.style.display = 'block';
    drag.line.style.top = (drag.target.lineY - 1) + 'px';
    drag.line.style.left = (secRect.left + 6) + 'px';
    drag.line.style.width = (secRect.width - 12) + 'px';
  }

  function endDrag(commit) {
    if (!drag) return;
    var d = drag;
    drag = null;
    document.removeEventListener('mousemove', onDragMove);
    document.removeEventListener('mouseup', onDragUp);
    document.removeEventListener('keydown', onDragKey);
    d.el.classList.remove('dragging');
    document.body.classList.remove('is-dragging');
    d.line.remove();
    if (!commit || !d.started || !d.target) return;
    if (d.target.sectionId === d.fromSection && d.target.index === d.fromIndex) return;
    send('moveStep', { id: d.id, sectionId: d.target.sectionId, index: d.target.index });
  }

  function onDragUp() { endDrag(true); }
  function onDragKey(e) { if (e.key === 'Escape') endDrag(false); }

  $('tree').addEventListener('mousedown', function (e) {
    if (e.button !== 0 || !!!(e.target.closest && e.target.closest('.grip'))) return;
    e.preventDefault();
    var el = e.target.closest('.step');
    var sec = el.closest('.section');
    var line = document.createElement('div');
    line.className = 'drop-line';
    document.body.appendChild(line);
    drag = {
      id: el.getAttribute('data-step'), el: el, line: line, startY: e.clientY, started: false, target: null,
      fromSection: sec.getAttribute('data-section'),
      fromIndex: Array.prototype.indexOf.call(sec.querySelectorAll('.step'), el),
    };
    document.addEventListener('mousemove', onDragMove);
    document.addEventListener('mouseup', onDragUp);
    document.addEventListener('keydown', onDragKey);
  });

  $('tree').addEventListener('click', function (e) {
    var actEl = e.target.closest('[data-act]');
    if (!actEl || !!(e.target.closest && e.target.closest('.grip'))) return;
    var act = actEl.getAttribute('data-act');
    var stepEl = e.target.closest('[data-step]');
    var secEl = e.target.closest('[data-section]');
    var stepId = stepEl && stepEl.getAttribute('data-step');
    var secId = secEl && secEl.getAttribute('data-section');

    if (act === 'open') {
      if (stepId === state.currentStepId) showTab('step'); else openStep(stepId, true);
    }
    else if (act === 'stepUp') send('moveStep', { id: stepId, delta: -1 });
    else if (act === 'stepDown') send('moveStep', { id: stepId, delta: 1 });
    else if (act === 'stepRename') {
      var nameEl = stepEl.querySelector('.name');
      inlineRename(nameEl, nameEl.textContent, function (v) { send('renameStep', { id: stepId, title: v }); });
    }
    else if (act === 'stepDelete') {
      var st = stepById(stepId);
      if (!st.items.length) send('deleteStep', { id: stepId });
      else armed(actEl, function () { send('deleteStep', { id: stepId }); });
    }
    else if (act === 'secToggle') { collapsed[secId] = !collapsed[secId]; renderTree(); }
    else if (act === 'secAddStep') send('addStep', { sectionId: secId });
    else if (act === 'addSection') send('addSection');
    else if (act === 'secUp') send('moveSection', { id: secId, delta: -1 });
    else if (act === 'secDown') send('moveSection', { id: secId, delta: 1 });
    else if (act === 'secDelete') armed(actEl, function () { send('deleteSection', { id: secId }); });
    else if (act === 'secPicture') {
      var sp = (state.manual.sections.filter(function (s) { return s.id === secId; })[0] || {}).image || {};
      send('pictureEnabled', { kind: 'section', id: secId, on: !sp.enabled });
    }
    else if (act === 'secPicView') send('pictureView', { kind: 'section', id: secId });
    else if (act === 'secPicGo') send('pictureGo', { kind: 'section', id: secId });
    else if (act === 'secPicAnnotate') send('annotate', { kind: 'section', id: secId });
  });

  $('tree').addEventListener('dblclick', function (e) {
    if (e.target.getAttribute('data-act') !== 'secRename') return;
    var secId = e.target.closest('[data-section]').getAttribute('data-section');
    inlineRename(e.target, e.target.textContent, function (v) { send('renameSection', { id: secId, title: v }); });
  });

  $('items').addEventListener('click', function (e) {
    var li = e.target.closest('li');
    if (!li) return;
    var path = li.getAttribute('data-path');
    var actEl = e.target.closest('[data-act]');
    var act = actEl && actEl.getAttribute('data-act');
    if (act === 'check') { checked[path] = e.target.checked; renderStep(); sendChecked(); }
    else if (act === 'select') send('selectItems', { paths: [path] });
    else if (act === 'anchor') send('setAnchor', { path: path });
    else if (act === 'remove') { delete checked[path]; send('removeItems', { paths: [path] }); }
  });

  // ------------------------------------------------------------ explode moves

  function exId(el) { return el.closest('li.ex').getAttribute('data-id'); }

  $('explodes').addEventListener('click', function (e) {
    var actEl = e.target.closest('[data-act]');
    if (!actEl || !e.target.closest('li.ex')) return;
    var act = actEl.getAttribute('data-act');
    var id = exId(actEl);
    var partEl = actEl.closest('.ex-part');
    var path = partEl && partEl.getAttribute('data-path');
    var on = !!actEl.getAttribute('data-on');
    if (act === 'scrub') send('scrubExplode', { id: id });
    else if (act === 'exEdit') send('editExplode', { id: id });
    else if (act === 'exExpand') { expandedEx[id] = !expandedEx[id]; renderStep(); }
    else if (act === 'exDelete') armed(actEl, function () { send('deleteExplode', { id: id }); });
    else if (act === 'exTrail') send('setTrailExplode', { id: id, trail: !on });
    else if (act === 'partTrail') send('setPartTrail', { id: id, path: path, trail: !on });
    else if (act === 'partRemove') send('removeExplodePart', { id: id, path: path });
    else if (act === 'partSelect') send('selectItems', { paths: [path] });
  });

  $('explodes').addEventListener('dblclick', function (e) {
    var nameEl = e.target.closest('[data-act="scrub"]');
    if (!nameEl) return;
    var id = exId(nameEl);
    var ex = state.step.explodes.filter(function (x) { return x.id === id; })[0];
    inlineRename(nameEl, ex.name || ex.label, function (v) { send('renameExplode', { id: id, name: v }); });
  });

  $('explodes').addEventListener('change', function (e) {
    var t = e.target;
    if (!t.closest('li.ex')) return;
    var id = exId(t);
    if (t.classList.contains('ex-axis')) send('setAxisExplode', { id: id, axis: t.value });
    else if (t.classList.contains('ex-spacing')) send('setSpacingExplode', { id: id, spacing: t.value });
    else if (t.classList.contains('ex-dist')) send('setDistanceExplode', { id: id, value: t.value });
    else if (t.classList.contains('part-dist')) {
      send('setPartDistance', { id: id, path: t.closest('.ex-part').getAttribute('data-path'), value: t.value });
    }
  });

  // Drag a move's grip to reorder the sequence.
  var exDrag = null;

  function exDragMove(e) {
    if (!exDrag) return;
    if (!exDrag.started && Math.abs(e.clientY - exDrag.startY) < 3) return;
    exDrag.started = true;
    exDrag.el.classList.add('dragging');
    document.body.classList.add('is-dragging');
    autoScroll(e);
    var rows = Array.prototype.slice.call(document.querySelectorAll('#explodes li.ex'))
      .filter(function (el) { return el !== exDrag.el; });
    var index = rows.length;
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i].getBoundingClientRect();
      if (e.clientY < r.top + r.height / 2) { index = i; break; }
    }
    var listRect = $('explodes').getBoundingClientRect();
    var y = index < rows.length ? rows[index].getBoundingClientRect().top
      : (rows.length ? rows[rows.length - 1].getBoundingClientRect().bottom : listRect.top);
    exDrag.index = index;
    exDrag.line.style.display = 'block';
    exDrag.line.style.top = (y - 1) + 'px';
    exDrag.line.style.left = listRect.left + 'px';
    exDrag.line.style.width = listRect.width + 'px';
  }

  function exDragEnd(commit) {
    if (!exDrag) return;
    var d = exDrag;
    exDrag = null;
    document.removeEventListener('mousemove', exDragMove);
    document.removeEventListener('mouseup', exDragUp);
    document.removeEventListener('keydown', exDragKey);
    d.el.classList.remove('dragging');
    document.body.classList.remove('is-dragging');
    d.line.remove();
    if (commit && d.started && d.index != null && d.index !== d.from) {
      send('moveExplode', { id: d.id, index: d.index });
    }
  }

  function exDragUp() { exDragEnd(true); }
  function exDragKey(e) { if (e.key === 'Escape') exDragEnd(false); }

  $('explodes').addEventListener('mousedown', function (e) {
    if (e.button !== 0 || !!!(e.target.closest && e.target.closest('.grip'))) return;
    e.preventDefault();
    var el = e.target.closest('li.ex');
    var line = document.createElement('div');
    line.className = 'drop-line';
    document.body.appendChild(line);
    exDrag = {
      id: el.getAttribute('data-id'), el: el, line: line, startY: e.clientY, started: false, index: null,
      from: Array.prototype.indexOf.call(document.querySelectorAll('#explodes li.ex'), el),
    };
    document.addEventListener('mousemove', exDragMove);
    document.addEventListener('mouseup', exDragUp);
    document.addEventListener('keydown', exDragKey);
  });

  $('unassigned').addEventListener('click', function (e) {
    var li = e.target.closest('li');
    if (!li) return;
    var path = li.getAttribute('data-path');
    var actEl = e.target.closest('[data-act]');
    var act = actEl && actEl.getAttribute('data-act');
    if (act === 'checkU') {
      checkedUnassigned[path] = e.target.checked;
      if (li.classList.contains('u-group')) renderUnassigned();     // its parts show ticked / free
    } else if (act === 'toggleU') {
      openUnassigned[path] = !openUnassigned[path];
      if (!openUnassigned[path]) delete openUnassigned[path];
      try { localStorage.setItem('bb.openUnassigned', JSON.stringify(openUnassigned)); } catch (err) { /* ignore */ }
      renderUnassigned();
    } else if (act === 'select') send('selectItems', { paths: [path] });
  });

  document.addEventListener('click', function (e) {
    if (e.target && e.target.id === 'useGlobalDistance') send('setStepDistance', { value: '' });
  });
  $('setDistance').addEventListener('change', function (e) { send('setDefaultDistance', { value: e.target.value }); });

  $('checkAll').addEventListener('change', function (e) {
    (state.step ? state.step.items : []).forEach(function (it) { checked[it.path] = e.target.checked; });
    renderStep();
    sendChecked();
  });

  // Checked parts are selected in Fusion (and exploded copies highlighted).
  function sendChecked() { send('checkItems', { paths: checkedPaths() }); }

  $('btnPrevStep').addEventListener('click', function () { stepOffset(-1); });
  $('btnNextStep').addEventListener('click', function () { stepOffset(1); });
  $('btnAssembled').addEventListener('click', function () { send('closeView'); });
  $('btnRefresh').addEventListener('click', function () { send('refresh'); });
  $('btnPick').addEventListener('click', function () { send('pick'); });
  $('btnAddSelected').addEventListener('click', function () { send('addSelected'); });
  $('btnSplit').addEventListener('click', function () { send('toggleSplit', { paths: checkedPaths() }); });

  // ------------------------------------------------------------ part right-click menu
  // Acts on the right-clicked part, or on all ticked parts when it's one of them.
  function allOutOfBom(paths) {
    return state.step.items.filter(function (i) { return paths.indexOf(i.path) >= 0; })
      .every(function (i) { return i.bom === false; });
  }
  function closeItemMenu() { $('itemMenu').classList.add('hidden'); }
  $('items').addEventListener('contextmenu', function (e) {
    var li = e.target.closest('li[data-path]');
    if (!li || !state.step) return;
    e.preventDefault();
    var path = li.getAttribute('data-path');
    var ticked = checkedPaths();
    var paths = ticked.indexOf(path) >= 0 ? ticked : [path];
    var many = paths.length > 1;
    var items = [
      ['select', 'Select in Fusion'],
      null,
      ['trailOn', 'Trail lines on'],
      ['trailOff', 'Trail lines off'],
      many ? null : ['anchor', 'Trail line start point\u2026'],
      ['unexplode', 'Unexplode (out of every move)'],
      null,
      ['bom', allOutOfBom(paths) ? 'Count in BOM' : 'Not in BOM'],
      ['split', 'Split / join bodies'],
      null,
      ['remove', 'Remove from step', 'danger']
    ].filter(function (it, i, all) { return it !== null || (i > 0 && all[i - 1] !== null); });
    var m = $('itemMenu');
    m.innerHTML = (many ? '<div class="ctx-title">' + paths.length + ' ticked parts</div>' : '') + items.map(function (it) {
      if (it === null) return '<div class="ctx-sep"></div>';
      return '<button class="ctx-item' + (it[2] ? ' ' + it[2] : '') + '" data-ctx="' + it[0] + '">' + esc(it[1]) + '</button>';
    }).join('');
    m.setAttribute('data-paths', JSON.stringify(paths));
    m.classList.remove('hidden');
    var r = m.getBoundingClientRect();
    m.style.left = Math.max(4, Math.min(e.clientX, window.innerWidth - r.width - 4)) + 'px';
    m.style.top = Math.max(4, Math.min(e.clientY, window.innerHeight - r.height - 4)) + 'px';
  });
  $('itemMenu').addEventListener('click', function (e) {
    var b = e.target.closest('[data-ctx]');
    if (!b) return;
    var paths = JSON.parse($('itemMenu').getAttribute('data-paths') || '[]');
    var act = b.getAttribute('data-ctx');
    closeItemMenu();
    if (act === 'select') send('selectItems', { paths: paths });
    else if (act === 'trailOn') send('setTrail', { paths: paths, trail: true });
    else if (act === 'trailOff') send('setTrail', { paths: paths, trail: false });
    else if (act === 'anchor') send('setAnchor', { path: paths[0] });
    else if (act === 'unexplode') send('resetOffsets', { paths: paths });
    else if (act === 'bom') send('setItemBom', { paths: paths, on: allOutOfBom(paths) });
    else if (act === 'split') send('toggleSplit', { paths: paths });
    else if (act === 'remove') send('removeItems', { paths: paths });
  });
  document.addEventListener('mousedown', function (e) { if (!e.target.closest('#itemMenu')) closeItemMenu(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeItemMenu(); });
  window.addEventListener('blur', closeItemMenu);
  document.addEventListener('scroll', closeItemMenu, true);
  $('btnSaveView').addEventListener('click', function () { send('saveCamera'); });
  $('btnGoView').addEventListener('click', function () { send('goCamera'); });
  $('btnExportStep').addEventListener('click', function () { send('exportStep'); });
  $('btnExportAll').addEventListener('click', function () { send('exportAll'); });
  $('btnExportPdf').addEventListener('click', function () { send('exportPdf'); });
  $('btnAnnotate').addEventListener('click', function () { send('annotate', { kind: 'step' }); });
  $('setCoverPic').addEventListener('change', function (e) { send('pictureEnabled', { kind: 'cover', on: e.target.checked }); });
  $('coverActions').addEventListener('click', function (e) {
    var b = e.target.closest('[data-pic]');
    if (b) send(b.getAttribute('data-pic'), { kind: 'cover' });
  });
  $('btnExportAllTo').addEventListener('click', function () { send('exportAll', { ask: true }); });
  $('setShowFrame').addEventListener('change', function (e) { settings({ showCropFrame: e.target.checked }); });
  $('setAskFolder').addEventListener('change', function (e) { settings({ askFolder: e.target.checked }); });
  $('btnChangeFolder').addEventListener('click', function () { send('chooseExportFolder'); });
  $('btnOpenFolder').addEventListener('click', function () { send('openExportFolder'); });
  $('notice').addEventListener('click', function () { $('notice').classList.add('hidden'); });
  $('setImgW').addEventListener('change', function (e) { settings({ image: { width: parseInt(e.target.value, 10) || 1600 } }); });
  $('setRatio').addEventListener('change', function (e) { send('setRatio', { ratio: e.target.value }); });
  $('setTransparent').addEventListener('change', function (e) { settings({ image: { transparent: e.target.checked } }); });
  $('btnExportManual').addEventListener('click', function () { send('exportManual'); });
  $('btnImportManual').addEventListener('click', function () { send('importManual'); });
  $('btnLeftovers').addEventListener('click', function () { send('removeLeftovers'); });
  $('btnReloadAddin').addEventListener('click', function () { setBusy(true); send('reloadAddin'); });
  $('btnRefreshThumbs').addEventListener('click', function () { setBusy(true); send('refreshThumbs'); });
  $('btnThumbsMissing').addEventListener('click', function () { setBusy(true); send('refreshThumbs'); });
  $('btnLines').addEventListener('click', function () { send('editLines'); });
  $('btnAddExplode').addEventListener('click', function () { send('addExplode', { paths: checkedPaths() }); });
  $('btnShowAll').addEventListener('click', function () { send('scrubExplode', { id: null }); });
  $('btnTrailOn').addEventListener('click', function () { send('setTrail', { paths: checkedPaths(), trail: true }); });
  $('btnNoBom').addEventListener('click', function () {
    // Toggle: if every ticked part is already out of the BOM, put them back in.
    var paths = checkedPaths();
    if (!paths.length || !state.step) return;
    send('setItemBom', { paths: paths, on: allOutOfBom(paths) });
  });
  $('btnTrailOff').addEventListener('click', function () { send('setTrail', { paths: checkedPaths(), trail: false }); });
  $('btnReset').addEventListener('click', function () {
    var paths = checkedPaths();
    if (paths.length) send('resetOffsets', { paths: paths });
  });
  $('btnRemove').addEventListener('click', function (e) {
    var paths = checkedPaths();
    if (paths.length) send('removeItems', { paths: paths });
  });
  $('btnAddChecked').addEventListener('click', function () {
    var paths = checkedUnassignedPaths();
    if (!paths.length) return;
    checkedUnassigned = {};
    send('addPaths', { paths: paths });
  });
  $('unassignedFilter').addEventListener('input', function () { if (state) renderUnassigned(); });

  $('stepPrep').addEventListener('change', function (e) { send('setPrep', { prep: e.target.checked }); });
  $('ctxEarlier').addEventListener('change', function (e) { send('setContext', { earlier: e.target.value }); });
  $('ctxLater').addEventListener('change', function (e) { send('setContext', { later: e.target.value }); });
  $('notes').addEventListener('change', function (e) { send('setNotes', { notes: e.target.value }); });
  // The notes box is as tall as its text (2 lines when empty, up to about 10).
  function fitNotes() {
    var t = $('notes');
    t.style.height = 'auto';
    t.style.height = Math.min(t.scrollHeight + 2, 160) + 'px';
  }
  $('notes').addEventListener('input', fitNotes);
  $('stepName').addEventListener('change', function (e) {
    var v = e.target.value.trim();
    if (v && state.step) send('renameStep', { id: state.step.id, title: v });
    else if (state.step) e.target.value = (stepById(state.step.id) || {}).title || '';
  });
  $('stepName').addEventListener('keydown', function (e) { if (e.key === 'Enter') e.target.blur(); });
  $('stepRepeat').addEventListener('change', function (e) { send('setRepeat', { repeat: parseInt(e.target.value, 10) || 1 }); });
  $('manualTitle').addEventListener('change', function (e) { send('renameManual', { title: e.target.value.trim() }); });

  $('setEarlier').addEventListener('change', function (e) { settings({ earlier: e.target.value }); });
  $('setLater').addEventListener('change', function (e) { settings({ later: e.target.value }); });
  $('setUnassigned').addEventListener('change', function (e) { settings({ unassigned: e.target.value }); });
  $('setGhost').addEventListener('change', function (e) { settings({ ghostOpacity: parseFloat(e.target.value) || 0.2 }); });
  $('setTrailColor').addEventListener('change', function (e) { settings({ trail: { color: e.target.value } }); });
  $('setTrailWeight').addEventListener('change', function (e) { settings({ trail: { weight: parseFloat(e.target.value) || 1.5 } }); });
  $('setTrailStyle').addEventListener('change', function (e) { settings({ trail: { style: e.target.value } }); });
  $('setEdges').addEventListener('change', function (e) { settings({ drawEdges: e.target.checked }); });
  $('setShortHw').addEventListener('change', function (e) { settings({ shortHardwareNames: e.target.checked }); });

  window.fusionJavaScriptHandler = {
    handle: function (action, data) {
      try {
        if (action === 'state') {
          state = JSON.parse(data);
          render();
        } else if (action === 'partMeshes') {
          gotPartMeshes(JSON.parse(data));
        } else if (action === 'compose') {
          // An exported PNG: draw its annotations on it and send it back to be saved.
          var job = JSON.parse(data);
          BBAnnot.compose(job.image, job.annotations, function (err, url) {
            if (!err) send('composed', { path: job.path, data: url });
          });
        }
      } catch (e) {
        setBusy(false);
        $('error').textContent = e.message;
        $('error').classList.remove('hidden');
      }
      return 'OK';
    }
  };

  showTab('steps');                  // always open on the step list (not the last tab used)

  // Fusion injects `adsk` after load; wait for it before asking for state.
  (function ready(tries) {
    if (window.adsk && window.adsk.fusionSendData) send('ready');
    else if (tries < 50) setTimeout(function () { ready(tries + 1); }, 100);
  })(0);
})();
