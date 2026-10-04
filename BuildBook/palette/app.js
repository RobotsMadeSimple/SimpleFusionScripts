/* BuildBook palette. Python owns the data; this renders the `state` it pushes
 * and sends actions back. Every action triggers a fresh state push.
 *
 * Layout: tabs (Steps, Step, Parts, Export, Settings). A thin busy bar shows
 * while an action is waiting for its state. Arrow keys move between steps,
 * Esc goes back to the assembled model. */
(function () {
  'use strict';

  // Script errors go to BuildBook's log (the panel can't always show them: a blank panel).
  function reportError(message, source, line) {
    try {
      if (window.adsk && window.adsk.fusionSendData) {
        window.adsk.fusionSendData('jsError', JSON.stringify({ message: String(message), source: String(source || ''), line: line || 0 }));
      }
    } catch (e) { /* nothing more to do */ }
  }
  window.addEventListener('error', function (e) {
    reportError(e.message, (e.filename || '').split('/').pop(), e.lineno);
  });

  var state = null;
  var checked = {};          // step item path -> true
  var checkedStep = null;    // the step `checked` belongs to
  var checkedUnassigned = {};
  var expandedEx = {};       // explode move id -> parts list open
  var collapsed = {};        // section id -> collapsed in the Steps tab
  var activeTab = 'steps';
  var lastStepId = null;
  var SPACINGS = [['uniform', 'Uniform'], ['stacked', 'Stacked by position'], ['stackedReverse', 'Stacked by position, reversed'], ['stackedSelection', 'Stacked in pick order']];
  var $ = function (id) { return document.getElementById(id); };

  var CONTEXT = [['shown', 'Shown'], ['ghosted', 'Ghosted'], ['hidden', 'Hidden']];
  var CONTEXT_STEP = [['inherit', 'Default']].concat(CONTEXT);
  var UNASSIGNED = [['asEarlier', 'Same as earlier steps']].concat(CONTEXT);

  // Actions that open a Fusion dialog or only select: no busy bar (the reply
  // may not come until the dialog closes).
  var NO_BUSY = ['pick', 'editLines', 'addExplode', 'editExplode', 'setAnchor', 'selectItems', 'hoverExplode',
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
    star: '<svg viewBox="0 0 16 16"><path d="M8 1.8l1.9 3.9 4.3.6-3.1 3 .7 4.3L8 11.6l-3.8 2 .7-4.3-3.1-3 4.3-.6z"/></svg>',
    lock: '<svg viewBox="0 0 16 16"><rect x="3.5" y="7" width="9" height="6.5" rx="1"/><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2"/></svg>',
    unlock: '<svg viewBox="0 0 16 16"><rect x="3.5" y="7" width="9" height="6.5" rx="1"/><path d="M5.5 7V5a2.5 2.5 0 0 1 4.8-1"/></svg>',
    plus: '<svg viewBox="0 0 16 16"><path d="M8 3v10M3 8h10"/></svg>',
    details: '<svg viewBox="0 0 16 16"><path d="M3.5 2.5h6l3 3v8h-9z"/><path d="M9.5 2.5v3h3M5.5 8.5h5M5.5 11h3.5"/></svg>',
    parts: '<svg viewBox="0 0 16 16"><path d="M8 1.5 14 4.8v6.4L8 14.5 2 11.2V4.8z M2 4.8 8 8l6-3.2 M8 8v6.5"/></svg>',
    pick: '<svg viewBox="0 0 16 16"><path d="M3 2.5l9 4.2-3.8 1.3 2.7 4-1.6 1-2.6-4-2.4 2.9z"/></svg>',
    lines: '<svg viewBox="0 0 16 16"><path d="M2 13 4 11M6 9l1.5-1.5M9.5 5.5 11 4"/><circle cx="12.8" cy="3.2" r="1.3"/></svg>',
    // Parts card: a part with its dashed trail behind it (on / crossed out).
    trailOn: '<svg viewBox="0 0 16 16"><rect x="9" y="2" width="5" height="5"/><path d="M2 14 8.5 7.5" stroke-dasharray="2 1.6"/></svg>',
    trailOff: '<svg viewBox="0 0 16 16"><rect x="9" y="2" width="5" height="5"/><path d="M2 14 8.5 7.5" stroke-dasharray="2 1.6"/><path d="M2.5 2.5l11 11"/></svg>',
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
      if (card.closest('[data-pane="settings"], [data-pane="export"]')) return;   // Settings / Export: always open
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
    // Only when it changes: replacing the text (even with the same words) under the cursor makes
    // the browser drop the hover tooltip and show it again, so it flickered on every refresh.
    var l = el.querySelector('.lbl') || el;
    if (l.textContent !== text) l.textContent = text;
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
    if (activeTab === 'parts' && name !== 'parts') clearUnassignedTicks();
    if (activeTab === 'step' && name !== 'step' && state && state.step) clearStepTicks();
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

  // While a rename box is open, re-renders wait: replacing the HTML would remove the focused box,
  // its blur would commit and render again in the middle of the first render (a DOM error).
  var renaming = false, renderPending = false;

  function render() {
    if (renaming) { renderPending = true; return; }
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
    $('btnCoverDeleteView').disabled = !cover.camera;
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
    var html = state.manual.sections.map(function (sec, si) {
      var views = 0;
      var steps = sec.steps.map(function (st, ti) {
        n += 1;
        var label = (si + 1) + '.' + (ti + 1);      // as the PDF numbers it: section.step
        if (st.camera) views += 1;
        var thumb = state.thumbs && state.thumbs[st.id];
        return '<div class="step' + (st.id === state.currentStepId ? ' active' : '') + '" data-step="' + st.id + '" data-act="open">' +
          '<span class="grip" title="Drag to reorder (or into another section)">' + ICON.grip + '</span>' +
          '<span class="thumb"' + thumbStyle(st.id) + '>' + (thumb ? '' : label) + '</span>' +
          // Title, with its prep / repeat chips on a line underneath
          '<span class="name step-title"><span class="step-name" title="' + esc(st.title) + '">' +
            '<span class="step-num">' + label + '</span>' + esc(st.title) + '</span>' +
          (st.prep || (st.repeat || 1) > 1 ? '<span class="step-chips">' +
            (st.prep ? '<span class="prep-tag" title="Preparation step">prep</span>' : '') +
            ((st.repeat || 1) > 1 ? '<span class="prep-tag" title="Do this step ' + st.repeat + ' times">×' + st.repeat + '</span>' : '') +
          '</span>' : '') + '</span>' +
          '<span class="tools">' +
            '<button data-act="stepRename" title="Rename">' + ICON.edit + '</button>' +
            '<button data-act="stepDelete" class="danger" title="Delete step">' + ICON.close + '</button>' +
          '</span></div>';
      }).join('');
      var total = sec.steps.length;
      var progress = total ? '<span class="progress">' +
        '<span class="' + (views === total ? 'done' : '') + '" title="Steps with a saved view">' + ICON.camera + views + '/' + total + '</span>' +
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
              (sec.image.camera ? '<button class="btn small danger" data-act="secPicDeleteView" title="Forget this section picture’s saved view (and its preview)">' +
                ICON.trash + '<span class="lbl">Delete view</span></button>' : '') +
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
    // "+ Add step" pressed here: once the new step is in, start typing its name.
    if (renameNewIn && state.currentStepId && renameNewIn.before.indexOf(state.currentStepId) < 0) {
      var newId = state.currentStepId;
      renameNewIn = null;
      var row = document.querySelector('#tree [data-step="' + newId + '"] .step-name');
      if (row) {
        row.closest('.step').scrollIntoView({ block: 'nearest' });
        inlineRename(row, stepById(newId).title, function (v) { send('renameStep', { id: newId, title: v }); });
      }
    }
  }
  var renameNewIn = null;            // {before: step ids} while waiting for a step added in this tab

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

  var scrollStepTop = null;         // id of the step the "+ Add step" was pressed on
  function renderStep() {
    if (renaming) { renderPending = true; return; }
    if (scrollStepTop && state.step && state.step.id !== scrollStepTop) {
      scrollStepTop = null;
      document.querySelector('.pane[data-pane="step"]').scrollTop = 0;
    }
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
    $('btnStepAddStep').title = 'Add a step right after this one in ' + where.section.title;
    $('stepTitle').textContent = step.title;
    $('btnPrevStep').disabled = pos <= 0;
    $('btnNextStep').disabled = pos >= list.length - 1;
    $('editBadge').classList.toggle('hidden', !state.edit);
    $('stepPrep').checked = !!step.prep;
    fillSelect($('ctxEarlier'), CONTEXT_STEP, step.earlier);
    fillSelect($('ctxLater'), CONTEXT_STEP, step.later);
    // Folded, the card still says what it's set to.
    var optText = function (sel) { var o = sel.options[sel.selectedIndex]; return o ? o.text : ''; };
    $('ctxSum').textContent = 'Earlier ' + optText($('ctxEarlier')).toLowerCase() +
      ' · later ' + optText($('ctxLater')).toLowerCase();
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
    $('btnDeleteView').disabled = !detail.hasCamera;
    $('hdrGoView').disabled = !detail.hasCamera;
    setLabel($('btnSaveView'), detail.hasCamera ? 'Update view' : 'Save view');
    $('hdrSaveView').title = detail.hasCamera ? 'Update view' : 'Save view';
    var thumb = state.thumbs && state.thumbs[step.id];
    drawPicturePreview($('stepThumb'), thumb, step.annotations || [], (state.manual.settings.image || {}).ratio);
    var nAnn = (step.annotations || []).length;
    setLabel($('btnAnnotate'), nAnn ? 'Annotate (' + nAnn + ')' : 'Annotate');
    $('hdrAnnotate').title = nAnn ? 'Annotate (' + nAnn + ')' : 'Annotate';
    $('viewHint').innerHTML = (detail.hasCamera ? '<span class="ok">&#x2713; View saved</span>'
                                                : '<span class="warn-text">No saved view</span>') +
      ' &middot; ' + esc(detail.image.text);
    $('viewHint').title = (detail.hasCamera ? 'Opening this step goes to its saved view.'
                                            : 'No saved view yet: export uses the current camera.') +
      '\nFile: ' + detail.imageName + (step.exportedAt ? '\nLast exported ' + step.exportedAt : '');

    if (detail.id !== checkedStep) { checked = {}; checkedStep = detail.id; }   // ticks belong to one step
    var paths = detail.items.map(function (i) { return i.path; });
    Object.keys(checked).forEach(function (p) { if (paths.indexOf(p) < 0) delete checked[p]; });

    var itemsTop = $('items').scrollTop;      // (redrawn on every tick: keep the scroll position)
    // Copies of one component fold into one "Name ×N" row (like the Parts tab); its arrow shows
    // each copy. A split part's bodies aren't copies: each keeps its own row.
    function isLit(it) {
      return checked[it.path] || fusionPaths.some(function (p) { return p === it.path || p.indexOf(it.path + '+') === 0; });
    }
    function movesHtml(moves) {
      return '<span class="in-moves" title="' + (moves.length ? 'In explode move ' + moves.join(', ') : 'Not exploded') + '">' +
        moves.map(function (m) { return '<b>' + m + '</b>'; }).join('') + '</span>';
    }
    function itemHtml(it, copy) {
      return '<li class="' + (it.missing ? 'missing' : '') + (it.bom === false ? ' no-bom' : '') + (isLit(it) ? ' on' : '') +
        (copy ? ' copy' : '') + '" data-path="' + esc(it.path) + '">' +
        '<input type="checkbox" data-act="check"' + (checked[it.path] ? ' checked' : '') + '>' +
        partThumbHtml(it.thumb, it.path) +
        '<span class="name">' +
          '<div>' + esc(it.name) + '</div>' +
          '<div class="sub">' + esc(it.component) +
            (it.bom === false ? ' <span class="nobom-tag" title="Not counted in this step: left out of the step, section and full parts lists">not in step BOM</span>' : '') +
          '</div></span>' +
        movesHtml(it.moves) +
        '<button class="icon-btn anchor-btn' + (it.anchor ? ' on' : '') + '" data-act="anchor" title="' +
          (it.anchor ? 'Trail lines start at a picked point. Click to change or reset.'
                     : 'Trail lines start at the part centre. Click to pick another point (e.g. a hole).') +
          '">' + ICON.anchor + '</button>' +
        '<button class="icon-btn danger" data-act="remove" title="Remove from this step">' + ICON.close + '</button>' +
        '</li>';
    }
    function groupHtml(key, copies, open) {
      var first = copies[0];
      var ticks = copies.filter(function (c) { return checked[c.path]; }).length;
      var moves = [];
      copies.forEach(function (c) { c.moves.forEach(function (m) { if (moves.indexOf(m) < 0) moves.push(m); }); });
      moves.sort(function (a, b) { return a - b; });
      var noBom = copies.every(function (c) { return c.bom === false; });
      return '<li class="u-copies' + (copies.some(isLit) ? ' on' : '') + (noBom ? ' no-bom' : '') + '" data-copies="' + esc(key) + '">' +
        '<input type="checkbox" data-act="checkCopies"' + (ticks === copies.length ? ' checked' : '') + (ticks && ticks < copies.length ? ' data-partial="1"' : '') + '>' +
        partThumbHtml(first.thumb, first.path) +
        '<span class="name"><div>' + esc(copyName(first.name)) + ' <span class="copies-count">\u00d7' + copies.length + '</span></div>' +
          '<div class="sub">' + esc(first.component) + ' · ' + copies.length + ' copies</div></span>' +
        movesHtml(moves) +
        '<button class="icon-btn danger" data-act="removeCopies" title="Remove all ' + copies.length + ' from this step">' + ICON.close + '</button>' +
        // (the arrow at the far right: the copies then need only a small indent)
        '<button class="u-caret" data-act="toggleCopies" title="' + (open ? 'Hide the copies' : 'Show each copy') + '">' +
          (open ? ICON.up : ICON.down) + '</button>' +
        '</li>';
    }
    var byComp = {}, html = [], done = {};
    function itemKey(it) { return it.path.indexOf('+#') >= 0 ? bodyKey(it.path) : (it.component || it.path); }
    detail.items.forEach(function (it) { (byComp[itemKey(it)] = byComp[itemKey(it)] || []).push(it); });
    itemCopies = {};
    detail.items.forEach(function (it) {
      var key = itemKey(it), copies = byComp[key];
      if (copies.length < 2) { html.push(itemHtml(it, false)); return; }
      if (done[key]) return;
      done[key] = true;
      itemCopies[key] = copies.map(function (c) { return c.path; });
      var open = !!openItemCopies[key];
      html.push(groupHtml(key, copies, open));
      if (open) copies.forEach(function (c) { html.push(itemHtml(c, true)); });
    });
    $('items').innerHTML = html.join('');
    Array.prototype.forEach.call($('items').querySelectorAll('input[data-partial]'), function (b) { b.indeterminate = true; });
    $('items').scrollTop = itemsTop;
    renderExplodes(detail);
    $('itemCount').textContent = detail.items.length;
    $('exCount').textContent = detail.explodes.length || '';
    $('viewSum').textContent = detail.hasCamera ? '\u2713' : 'no view';
    $('viewSum').title = detail.hasCamera ? 'View saved' : 'No saved view yet';
    $('viewSum').classList.toggle('ok', !!detail.hasCamera);
    $('checkAll').checked = detail.items.length > 0 && paths.every(function (p) { return checked[p]; });
    // Bulk bar: only while parts are ticked.
    var ticked = checkedPaths();
    // Always shown; greyed out until parts are ticked.
    Array.prototype.forEach.call($('bulkBar').querySelectorAll('button'), function (b) { b.disabled = !ticked.length; });
    $('bulkBar').classList.toggle('idle', !ticked.length);
    $('checkedCount').textContent = ticked.length ? ticked.length + ' ticked' : '';
    setLabel($('btnNoBom'), ticked.length && allOutOfBom(ticked) ? 'Count in step BOM' : 'Not in step BOM');
    // Split / join: only for multi-body parts (or ones already split) among the ticked.
    var splitLabel = splitAction(ticked);
    // Always there; greyed out unless a ticked part has bodies to split (or is split already).
    $('btnSplit').disabled = !splitLabel;
    setLabel($('btnSplit'), splitLabel || 'Split / join');
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
  // Forgiving search: every word of the query has to fit, but spacing / dashes don't matter
  // ("m3x12" finds "M3 x 12"), words can be shortened ("mot brk" finds "Motor Bracket") and a
  // small typo is let through ("braket", "pully").
  function squash(s) { return s.toLowerCase().replace(/[\s_\-.:()\[\]]+/g, ''); }
  function editDistance(a, b, limit) {
    if (Math.abs(a.length - b.length) > limit) return limit + 1;
    var prev = [], cur, i, j;
    for (j = 0; j <= b.length; j++) prev[j] = j;
    for (i = 1; i <= a.length; i++) {
      cur = [i];
      var best = i;
      for (j = 1; j <= b.length; j++) {
        cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
        if (cur[j] < best) best = cur[j];
      }
      if (best > limit) return limit + 1;
      prev = cur;
    }
    return prev[b.length];
  }
  // "brk" for "bracket": same first letter, the rest in order.
  function abbreviates(q, w) {
    if (q[0] !== w[0] || q.length > w.length) return false;
    for (var i = 1, j = 1; i < q.length; i++) {
      while (j < w.length && w[j] !== q[i]) j++;
      if (j++ >= w.length) return false;
    }
    return true;
  }
  function fuzzyMatch(query, text) {
    var flat = squash(text);
    var words = text.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
    return query.toLowerCase().split(/\s+/).filter(Boolean).every(function (q) {
      var sq = squash(q);
      if (!sq || flat.indexOf(sq) >= 0) return true;
      if (words.some(function (w) { return w.indexOf(sq) === 0; })) return true;          // a shortened word
      if (sq.length >= 3 && words.some(function (w) { return abbreviates(sq, w); })) return true;
      if (sq.length < 4) return false;
      var limit = sq.length >= 8 ? 2 : 1;                                                 // a typo or two
      return words.some(function (w) {
        return editDistance(sq, w, limit) <= limit ||
          (w.length > sq.length && editDistance(sq, w.slice(0, sq.length), limit) <= limit);
      });
    });
  }

  // A picture's preview as it exports: its thumbnail (the whole canvas when the view was saved)
  // cut to the manual's crop ratio, with its annotations drawn on top. Drawn on a canvas: the
  // thumbnail is a local file, which a canvas can show but not turn back into an image.
  var RATIO_VALUES = { '16:9': 16 / 9, '3:2': 3 / 2, '4:3': 4 / 3, '1:1': 1, '4:5': 4 / 5,
                       letterL: 11 / 8.5, letterP: 8.5 / 11 };
  var previewImages = {};
  function drawPicturePreview(box, url, annotations, ratioKey) {
    var canvas = box.querySelector('canvas.pic-preview');
    if (!url) {
      if (canvas) canvas.remove();
      box.textContent = 'No preview yet';
      return;
    }
    if (!canvas) {
      box.textContent = '';
      canvas = document.createElement('canvas');
      canvas.className = 'pic-preview';
      box.appendChild(canvas);
      if (window.ResizeObserver) new ResizeObserver(function () { if (box._redraw) box._redraw(); }).observe(box);
    }
    box.style.backgroundImage = '';
    var img = previewImages[url];
    if (!img) {
      img = previewImages[url] = new Image();
      img.src = url;
    }
    box._redraw = function () {
      if (!img.complete || !img.naturalWidth) { img.onload = box._redraw; return; }
      var dpr = window.devicePixelRatio || 1, cw = box.clientWidth, ch = box.clientHeight;
      if (!cw || !ch) return;
      canvas.width = Math.round(cw * dpr); canvas.height = Math.round(ch * dpr);
      var ctx = canvas.getContext('2d');
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cw, ch);
      // The export's crop: the largest rectangle of the ratio, centred in the saved view.
      var iw = img.naturalWidth, ih = img.naturalHeight, r = RATIO_VALUES[ratioKey] || iw / ih;
      var sw = iw / ih > r ? ih * r : iw, sh = iw / ih > r ? ih : iw / r;
      var sx = (iw - sw) / 2, sy = (ih - sh) / 2;
      // Fitted into the box (letterboxed when the ratios differ).
      var scale = Math.min(cw / sw, ch / sh), dw = sw * scale, dh = sh * scale;
      var dx = (cw - dw) / 2, dy = (ch - dh) / 2;
      ctx.drawImage(img, sx, sy, sw, sh, dx, dy, dw, dh);
      if (annotations.length && window.BBAnnot) {
        ctx.save();
        ctx.beginPath(); ctx.rect(dx, dy, dw, dh); ctx.clip();
        ctx.translate(dx, dy);
        BBAnnot.draw(ctx, annotations, dw, dh);
        ctx.restore();
      }
    };
    box._redraw();
  }

  var copyGroups = {};               // "parent|component" -> paths of the copies folded into one row
  // "Bolt:3" -> "Bolt"; a body "Pin (2)" -> "Pin" (Fusion numbers bodies copied in a part).
  function copyName(name) { return name.replace(/:\d+$/, '').replace(/\s*\(\d+\)$/, ''); }
  // A split part's body: same part + same name apart from the "(N)" -> one group of copies.
  function bodyKey(path) {
    var i = path.indexOf('+#');
    return 'b:' + path.slice(0, i) + '+#' + copyName(path.slice(i + 2));
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
      tickAllTargets = [];
      list.forEach(function (u) {
        if (!fuzzyMatch(filter, u.name + ' ' + u.path + ' ' + u.component)) return;
        tickAllTargets.push(u.path);
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
    // Copies of one component side by side (same assembly) fold into one "Name ×N" row, like
    // Browser+. Ticking it ticks every copy; its arrow shows them.
    var kids = {}, order = [];
    shown.forEach(function (u) {
      var parent = byPath[parentPath(u.path)] ? parentPath(u.path) : '';
      if (!kids[parent]) kids[parent] = [];
      kids[parent].push(u);
    });
    copyGroups = {};
    var html = [];
    function rowHtml(u, depth) {
      var locked = hasCheckedAncestor(u.path);
      var open = filter ? true : !!openUnassigned[u.path];
      var sub = u.group
        ? (u.split ? 'Split into bodies' : 'Assembly') + ' · ' +
          (u.leaves < u.total ? u.leaves + ' of ' + u.total + ' parts not in a step' : u.total + ' part' + (u.total === 1 ? '' : 's'))
        : u.path;
      if (u.prep) sub = 'Prepared in “' + u.prep + '” · ' + sub;
      return '<li data-path="' + esc(u.path) + '" class="' + (u.group ? 'u-group' : '') + (fusionSelected[u.path] ? ' fsel' : '') +
        '" style="--ud:' + depth + '">' +
        (u.group ? '<button class="u-caret' + (open ? ' open' : '') + '" data-act="toggleU" title="' + (open ? 'Collapse' : 'Show its parts') + '">' + ICON.caret + '</button>'
          : '<span class="u-caret none"></span>') +
        '<input type="checkbox" data-act="checkU"' + (checkedUnassigned[u.path] || locked ? ' checked' : '') + (locked ? ' disabled title="Added with its assembly"' : '') + '>' +
        partThumbHtml(u.thumb, u.path) +
        '<span class="name"><div>' + esc(u.name) + '</div>' +
        '<div class="sub">' + esc(sub) + '</div></span></li>';
    }
    function groupHtml(key, copies, depth, open) {
      var first = copies[0];
      var states = copies.map(function (u) { return checkedUnassigned[u.path] || hasCheckedAncestor(u.path); });
      var all = states.every(Boolean), some = states.some(Boolean);
      var locked = copies.every(function (u) { return hasCheckedAncestor(u.path); });
      var anySel = copies.some(function (u) { return fusionSelected[u.path]; });
      return '<li data-copies="' + esc(key) + '" class="u-copies' + (first.group ? ' u-group' : '') + (anySel ? ' fsel' : '') +
        '" style="--ud:' + depth + '">' +
        '<button class="u-caret' + (open ? ' open' : '') + '" data-act="toggleCopies" title="' + (open ? 'Hide the copies' : 'Show each copy') + '">' + ICON.caret + '</button>' +
        '<input type="checkbox" data-act="checkCopies"' + (all ? ' checked' : '') + (some && !all ? ' data-partial="1"' : '') +
          (locked ? ' disabled title="Added with its assembly"' : '') + '>' +
        partThumbHtml(first.thumb, first.path) +
        '<span class="name"><div>' + esc(copyName(first.name)) + ' <span class="copies-count">\u00d7' + copies.length + '</span></div>' +
        '<div class="sub">' + copies.length + ' copies' + (first.group ? ' · assembly' : '') + '</div></span></li>';
    }
    function emit(parent, depth) {
      var list = kids[parent] || [];
      var byComp = {};
      // (A split part's bodies share its component but aren't copies: each keeps its own row.)
      function copyKey(u) { return u.path.indexOf('+#') >= 0 ? bodyKey(u.path) : (u.component || u.path); }
      list.forEach(function (u) { var k = copyKey(u); (byComp[k] = byComp[k] || []).push(u); });
      var done = {};
      list.forEach(function (u) {
        var comp = copyKey(u), copies = byComp[comp];
        if (copies.length > 1) {
          if (done[comp]) return;
          done[comp] = true;
          var key = parent + '|' + comp;
          copyGroups[key] = copies.map(function (c) { return c.path; });
          var open = filter ? true : !!openUnassigned['g:' + key];
          html.push(groupHtml(key, copies, depth, open));
          if (open) copies.forEach(function (c) { html.push(rowHtml(c, depth + 1)); emit(c.path, depth + 2); });
          return;
        }
        html.push(rowHtml(u, depth));
        emit(u.path, depth + 1);
      });
    }
    emit('', 0);
    var scroller = $('unassigned'), keepTop = scroller.scrollTop;
    scroller.innerHTML = html.join('');
    scroller.scrollTop = keepTop;   // (redrawn on every tick: don't jump back to the top)
    Array.prototype.forEach.call(document.querySelectorAll('#unassigned input[data-partial]'), function (b) { b.indeterminate = true; });
    if (!filter) tickAllTargets = list.filter(function (u) { return !byPath[parentPath(u.path)]; }).map(function (u) { return u.path; });
    // Outermost only: a matching assembly brings its matching parts with it.
    tickAllTargets = tickAllTargets.filter(function (p) {
      for (var a = parentPath(p); a; a = parentPath(a)) if (tickAllTargets.indexOf(a) >= 0) return false;
      return true;
    });
    $('tickAllUnassigned').checked = tickAllTargets.length > 0 &&
      tickAllTargets.every(function (p) { return checkedUnassigned[p] || hasCheckedAncestor(p); });
    $('btnAddChecked').disabled = !state.step;
    $('btnAddChecked').title = state.step ? 'Add the checked parts to the open step' : 'Open a step first';
  }

  // Parts selected in Fusion (e.g. double-clicked in the canvas): highlighted in the Parts tab,
  // their assemblies opened so they show, and the first one scrolled into view.
  var fusionSelected = {};
  // Ticked parts in the Parts tab are selected in Fusion too. Selecting something else in Fusion
  // (e.g. clicking in the canvas) or leaving the tab unticks them.
  // A click on the Parts tab's empty space (not a row or a control) unticks everything too.
  $('unassigned').addEventListener('click', function (e) {
    if (e.target === e.currentTarget) clearUnassignedTicks();
  });
  var tickAllTargets = [];          // what "All" ticks: every row shown, or the filter's matches
  $('tickAllUnassigned').addEventListener('change', function (e) {
    if (e.target.checked) tickAllTargets.forEach(function (p) { checkedUnassigned[p] = true; });
    else tickAllTargets.forEach(function (p) { delete checkedUnassigned[p]; });
    selectTickedUnassigned();
    renderUnassigned();
  });
  function selectTickedUnassigned() {
    send('selectItems', { paths: checkedUnassignedPaths() });
  }
  function clearUnassignedTicks() {
    if (!checkedUnassignedPaths().length) return;
    checkedUnassigned = {};
    send('selectItems', { paths: [] });
    renderUnassigned();
  }
  function untickIfSelectionChanged(paths) {
    var ticked = checkedUnassignedPaths();
    if (!ticked.length) return;
    var same = paths.length === ticked.length && ticked.every(function (p) { return paths.indexOf(p) >= 0; });
    if (!same) { checkedUnassigned = {}; renderUnassigned(); }
  }

  var fusionPaths = [];             // selected in Fusion now (the step's rows light up for them)
  function showFusionSelection(paths, ours) {
    var changed = paths.join('|') !== fusionPaths.join('|');
    fusionPaths = paths;
    if (changed && activeTab === 'step' && state && state.step) renderStep();
    if (!ours) {                                    // (our own selecting reports with ours set)
      untickIfSelectionChanged(paths);
      if (state && state.step) untickStepIfSelectionChanged(paths);
    }
    fusionSelected = {};
    if (!state || !state.unassigned) return;
    var byPath = {};
    state.unassigned.forEach(function (u) { byPath[u.path] = true; });
    paths.forEach(function (p) {
      var row = p;
      while (row && !byPath[row]) row = parentPath(row);    // (a body or sub-part: its listed row)
      if (!row) return;
      fusionSelected[row] = true;
      for (var a = parentPath(row); a; a = parentPath(a)) if (byPath[a]) openUnassigned[a] = true;
      Object.keys(copyGroups).forEach(function (k) {
        if (copyGroups[k].indexOf(row) >= 0) openUnassigned['g:' + k] = true;
      });
    });
    if (activeTab !== 'parts') return;
    renderUnassigned();
    if (ours) return;               // (ticking in the list: stay where you are)
    var first = document.querySelector('#unassigned li.fsel');
    if (first) first.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  function renderExplodes(detail) {
    var upto = detail.upto;
    $('explodes').innerHTML = detail.explodes.map(function (ex) {
      var open = !!expandedEx[ex.id];
      return '<li class="ex' + (ex.id === upto ? ' active' : '') + (ex.id === hoverEx ? ' peek' : '') + (open ? ' open' : '') +
        '" data-id="' + ex.id + '">' +
        '<div class="ex-row">' +
          '<span class="grip" title="Drag to reorder">' + ICON.grip + '</span>' +
          '<span class="num">' + ex.number + '</span>' +
          '<span class="name" data-act="exEdit" title="Edit this move: name, parts, direction, distance, spacing">' +
            '<span class="ex-title">' + esc(ex.label) + '</span>' +
            '<span class="ex-sum">' + esc(explodeSummary(ex)) + '</span></span>' +
          '<button class="icon-btn ex-del danger" data-act="exDelete" title="Delete this move">' + ICON.close + '</button>' +
          '<button class="icon-btn" data-act="exExpand" title="' + (open ? 'Hide' : 'Show') + ' the parts and their trail lines">' +
            (open ? ICON.up : ICON.down) + '</button>' +
        '</div>' +
        (open ? explodeParts(ex) : '') +
        '</li>';
    }).join('');
    if (!detail.explodes.length) {
      $('explodes').innerHTML = '<li class="empty">No moves yet. Check parts above (or select them in Fusion), then <b>+ Add move</b>.</li>';
    }
  }

  // "3 parts · +Z 40 mm · stacked": what the move does, at a glance (changed with edit).
  function explodeSummary(ex) {
    var n = ex.parts.length;
    var bits = [n + (n === 1 ? ' part' : ' parts')];
    if (ex.kind === 'axis') {
      bits.push((ex.distance < 0 ? '\u2212' : '+') + ex.axis + ' ' + Math.abs(ex.distance) + ' ' + (state.units || ''));
    } else {
      bits.push(ex.direction + (ex.kind === 'xyz' ? '' : ' ' + Math.abs(ex.distance) + ' ' + (state.units || '')));
    }
    var spacing = SPACINGS.filter(function (s) { return s[0] === ex.spacing; })[0];
    if (spacing && ex.spacing !== 'uniform') bits.push(spacing[1].toLowerCase());
    return bits.join(' \u00b7 ');
  }

  function explodeParts(ex) {
    return '<div class="ex-parts">' +
      '<div class="ex-parts-head"><span>Trail lines</span>' +
        trailButton(ex.trail, 'Trail lines for every part in this move', 'exTrail') + '</div>' +
      ex.parts.map(function (p) {
        return '<div class="ex-part' + (p.missing ? ' missing' : '') + '" data-path="' + esc(p.path) + '">' +
          partThumbHtml(p.thumb, p.path) +
          '<span class="mdir" data-act="partSelect" title="Select in Fusion">' + esc(p.name) +
            (p.own ? ' <span class="sub" title="Has its own distance">(' + p.distance + ' ' + esc(state.units || '') + ')</span>' : '') +
          '</span>' +
          trailButton(p.trail, 'Trail line for this part in this move', 'partTrail') +
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
    var brand = state.branding || {};
    // Field by field: the saved default (greyed out, "unlock" to give this book its own) or the
    // book's own ("lock" goes back to the default, when there is one).
    var lockedF = state.brandingLocked || [], defaults = state.brandingDefaults || {};
    ['company', 'author', 'logo'].forEach(function (key) {
      var locked = lockedF.indexOf(key) >= 0;
      var row = document.querySelector('[data-brand="' + key + '"]');
      Array.prototype.forEach.call(row.querySelectorAll('input, .btn'), function (el) { el.disabled = locked; });
      row.classList.toggle('locked', locked);
      var lock = row.querySelector('[data-brand-lock]');
      lock.classList.toggle('hidden', !locked && !defaults[key]);
      lock.innerHTML = locked ? ICON.unlock : ICON.lock;   // (shows what a click does)
      lock.title = locked ? 'Unlock: give this book its own ' + key : 'Use the saved default ' + key + ' again';
      document.querySelector('[data-brand-note="' + key + '"]').textContent =
        locked ? 'Using your saved default.' : '';
    });
    $('btnBrandSave').disabled = !(brand.company || brand.author || brand.logo) || lockedF.length === 3;
    if (document.activeElement !== $('setCompany')) $('setCompany').value = brand.company || '';
    if (document.activeElement !== $('setAuthor')) $('setAuthor').value = brand.author || '';
    var logo = brand.logo || '';
    $('logoPreview').style.backgroundImage = logo ? 'url(' + logo + ')' : '';
    $('logoPreview').textContent = logo ? '' : 'No logo';
    $('btnRemoveLogo').classList.toggle('hidden', !logo);
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
    $('btnUseDefaults').disabled = !state.myDefaults;
    $('btnClearDefaults').classList.toggle('hidden', !state.myDefaults);
    $('defaultsHint').textContent = state.myDefaults
      ? 'New manuals start with your saved defaults.'
      : 'None saved yet: new manuals start with BuildBook\'s settings.';
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
    renaming = true;
    var finished = false;
    var finish = function (commit) {
      if (finished) return;
      finished = true;
      input.removeEventListener('blur', onBlur);
      var v = input.value.trim();
      // Out of this event first: the render below replaces the box that is firing it.
      setTimeout(function () {
        renaming = false;
        renderPending = false;
        if (commit && v && v !== current) done(v);
        render();
      }, 0);
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
    if (button.getAttribute('data-armed')) {
      // Confirmed: put the label back (buttons in lists are redrawn anyway; fixed ones aren't).
      button.removeAttribute('data-armed');
      button.innerHTML = button._label;
      fn();
      return;
    }
    button._label = button.innerHTML;
    button.setAttribute('data-armed', '1');
    button.innerHTML = 'Sure?';
    setTimeout(function () {
      if (button.isConnected && button.getAttribute('data-armed')) {
        button.removeAttribute('data-armed');
        button.innerHTML = button._label;
      }
    }, 3000);
  }

  function settings(patch) { send('setSettings', { settings: patch }); }
  function branding(key, value) { send('setBranding', { key: key, value: value }); }
  $('setCompany').addEventListener('change', function (e) { branding('company', e.target.value.trim()); });
  $('setAuthor').addEventListener('change', function (e) { branding('author', e.target.value.trim()); });
  Array.prototype.forEach.call(document.querySelectorAll('[data-brand-lock]'), function (b) {
    b.addEventListener('click', function () {
      var key = b.getAttribute('data-brand-lock');
      send('setBrandingField', { key: key, own: (state.brandingLocked || []).indexOf(key) >= 0 });
    });
  });
  $('btnBrandSave').addEventListener('click', function () { send('saveBranding'); });
  $('btnChooseLogo').addEventListener('click', function () { send('chooseLogo'); });
  $('btnRemoveLogo').addEventListener('click', function () { branding('logo', ''); });
  // A chosen logo: scaled to at most 600 px on its long side and kept as a PNG (small enough to
  // save in the design, sharp enough for print at the cover's size).
  function shrinkLogo(url) {
    var img = new Image();
    img.onload = function () {
      var k = Math.min(1, 600 / Math.max(img.naturalWidth, img.naturalHeight));
      var c = document.createElement('canvas');
      c.width = Math.max(1, Math.round(img.naturalWidth * k));
      c.height = Math.max(1, Math.round(img.naturalHeight * k));
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      branding('logo', c.toDataURL('image/png'));
    };
    img.onerror = function () { reportError('logo picture could not be read', 'app.js', 0); };
    img.src = url;
  }
  $('btnSaveDefaults').addEventListener('click', function () { send('saveMyDefaults'); });
  $('btnDeleteManual').addEventListener('click', function (e) {
    armed(e.currentTarget, function () { send('deleteManual'); });
  });
  $('btnUseDefaults').addEventListener('click', function () { send('useMyDefaults'); });
  $('btnClearDefaults').addEventListener('click', function (e) {
    armed(e.currentTarget, function () { send('clearMyDefaults'); });
  });

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
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && (e.key === 'z' || e.key === 'Z')) {
      send('undo');                 // takes back the last deletion (steps, sections, moves, parts)
      e.preventDefault();
      return;
    }
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
      // Opens the step in the canvas and stays on this list; clicking the open step again goes to
      // the Step tab to edit it.
      if (stepId === state.currentStepId) showTab('step'); else openStep(stepId, false);
    }
    else if (act === 'stepUp') send('moveStep', { id: stepId, delta: -1 });
    else if (act === 'stepDown') send('moveStep', { id: stepId, delta: 1 });
    else if (act === 'stepRename') {
      // Only the title (not its prep / ×N chips underneath)
      var nameEl = stepEl.querySelector('.step-name') || stepEl.querySelector('.name');
      inlineRename(nameEl, stepById(stepId).title, function (v) { send('renameStep', { id: stepId, title: v }); });
    }
    else if (act === 'stepDelete') {
      var st = stepById(stepId);
      if (!st.items.length) send('deleteStep', { id: stepId });
      else armed(actEl, function () { send('deleteStep', { id: stepId }); });
    }
    else if (act === 'secToggle') { collapsed[secId] = !collapsed[secId]; renderTree(); }
    else if (act === 'secAddStep') {
      renameNewIn = { before: allSteps().map(function (w) { return w.step.id; }) };
      send('addStep', { sectionId: secId });
    }
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
    else if (act === 'secPicDeleteView') armed(actEl, function () { send('pictureClearView', { kind: 'section', id: secId }); });
  });

  $('tree').addEventListener('dblclick', function (e) {
    if (e.target.getAttribute('data-act') !== 'secRename') return;
    var secId = e.target.closest('[data-section]').getAttribute('data-section');
    inlineRename(e.target, e.target.textContent, function (v) { send('renameSection', { id: secId, title: v }); });
  });

  var itemCopies = {}, openItemCopies = {};   // step list: component -> paths of its copies / opened
  $('items').addEventListener('click', function (e) {
    var li = e.target.closest('li');
    if (!li) return;
    var path = li.getAttribute('data-path');
    var actEl = e.target.closest('[data-act]');
    var act = actEl && actEl.getAttribute('data-act');
    var copiesKey = li.getAttribute('data-copies');
    if (copiesKey !== null) {
      var copies = itemCopies[copiesKey] || [];
      if (act === 'toggleCopies') {
        openItemCopies[copiesKey] = !openItemCopies[copiesKey];
        renderStep();
      } else if (act === 'removeCopies') {
        copies.forEach(function (c) { delete checked[c]; });
        send('removeItems', { paths: copies });
      } else {
        // The checkbox or anywhere on the row: tick every copy (or untick them all).
        var allOn = copies.every(function (c) { return checked[c]; });
        copies.forEach(function (c) { if (allOn) delete checked[c]; else checked[c] = true; });
        renderStep();
        sendChecked();
      }
      return;
    }
    if (act === 'check') { checked[path] = e.target.checked; renderStep(); sendChecked(); }
    else if (act === 'anchor') send('setAnchor', { path: path });
    else if (act === 'remove') { delete checked[path]; send('removeItems', { paths: [path] }); }
    else {
      // Anywhere else on the row ticks / unticks it (ticked parts are selected in Fusion).
      checked[path] = !checked[path];
      if (!checked[path]) delete checked[path];
      renderStep();
      sendChecked();
    }
  });
  // Clicking the list's empty space, selecting something else in Fusion (e.g. clicking in the
  // canvas) or leaving the Step tab unticks them, like the Parts tab.
  $('items').addEventListener('click', function (e) {
    if (e.target === e.currentTarget) clearStepTicks();
  });
  function clearStepTicks() {
    if (!checkedPaths().length) return;
    checked = {};
    renderStep();
    sendChecked();
  }
  function untickStepIfSelectionChanged(paths) {
    var ticked = checkedPaths();
    if (!ticked.length) return;
    var same = paths.length === ticked.length && ticked.every(function (p) { return paths.indexOf(p) >= 0; });
    if (!same) { checked = {}; renderStep(); send('checkItems', { paths: [], keepSelection: true }); }
  }

  // ------------------------------------------------------------ explode moves

  function exId(el) { return el.closest('li.ex').getAttribute('data-id'); }

  // Hovering a move's row shows the step as it stands just after that move; leaving the list
  // shows every move again. (Sent after a short pause, so sweeping over rows doesn't redraw each.)
  var hoverEx = null, hoverExTimer = null;
  function peekExplode(id) {
    if (id === hoverEx) return;
    hoverEx = id;
    Array.prototype.forEach.call($('explodes').querySelectorAll('li.ex'), function (li) {
      li.classList.toggle('peek', li.getAttribute('data-id') === id);
    });
    clearTimeout(hoverExTimer);
    hoverExTimer = setTimeout(function () { send('hoverExplode', { id: hoverEx }); }, id ? 120 : 60);
  }
  $('explodes').addEventListener('mouseover', function (e) {
    if (exDrag || renaming) return;
    var li = e.target.closest('li.ex');
    if (li) peekExplode(li.getAttribute('data-id'));
  });
  $('explodes').addEventListener('mouseleave', function () { peekExplode(null); });

  $('explodes').addEventListener('click', function (e) {
    var actEl = e.target.closest('[data-act]');
    if (!actEl || !e.target.closest('li.ex')) return;
    var act = actEl.getAttribute('data-act');
    var id = exId(actEl);
    var partEl = actEl.closest('.ex-part');
    var path = partEl && partEl.getAttribute('data-path');
    var on = !!actEl.getAttribute('data-on');
    if (act === 'exEdit') send('editExplode', { id: id });
    else if (act === 'exExpand') { expandedEx[id] = !expandedEx[id]; renderStep(); }
    else if (act === 'exDelete') armed(actEl, function () { send('deleteExplode', { id: id }); });
    else if (act === 'exTrail') send('setTrailExplode', { id: id, trail: !on });
    else if (act === 'partTrail') send('setPartTrail', { id: id, path: path, trail: !on });
    else if (act === 'partSelect') send('selectItems', { paths: [path] });
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
    var copiesKey = li.getAttribute('data-copies');
    if (copiesKey !== null) {
      if (act === 'toggleCopies') {
        openUnassigned['g:' + copiesKey] = !openUnassigned['g:' + copiesKey];
        if (!openUnassigned['g:' + copiesKey]) delete openUnassigned['g:' + copiesKey];
        try { localStorage.setItem('bb.openUnassigned', JSON.stringify(openUnassigned)); } catch (err) { /* ignore */ }
      } else {
        // The checkbox or anywhere on the row: tick every copy (or untick them all).
        var copies = copyGroups[copiesKey] || [];
        var free = copies.filter(function (c) { return !hasCheckedAncestor(c); });
        if (!free.length) return;
        var allOn = free.every(function (c) { return checkedUnassigned[c]; });
        free.forEach(function (c) { if (allOn) delete checkedUnassigned[c]; else checkedUnassigned[c] = true; });
        selectTickedUnassigned();
      }
      renderUnassigned();
      return;
    }
    if (act === 'checkU') {
      checkedUnassigned[path] = e.target.checked;
      selectTickedUnassigned();
      if (li.classList.contains('u-group')) renderUnassigned();     // its parts show ticked / free
    } else if (act === 'toggleU') {
      openUnassigned[path] = !openUnassigned[path];
      if (!openUnassigned[path]) delete openUnassigned[path];
      try { localStorage.setItem('bb.openUnassigned', JSON.stringify(openUnassigned)); } catch (err) { /* ignore */ }
      renderUnassigned();
    } else {
      // Anywhere else on the row ticks / unticks it (which also selects it in Fusion).
      var box = li.querySelector('input[data-act="checkU"]');
      if (!box || box.disabled) return;
      checkedUnassigned[path] = !box.checked;
      selectTickedUnassigned();
      renderUnassigned();
    }
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
  $('btnSplit').addEventListener('click', function () { send('toggleSplit', { paths: checkedPaths() }); });

  // ------------------------------------------------------------ part right-click menu
  // Acts on the right-clicked part, or on all ticked parts when it's one of them.
  function allOutOfBom(paths) {
    return state.step.items.filter(function (i) { return paths.indexOf(i.path) >= 0; })
      .every(function (i) { return i.bom === false; });
  }
  // "Split bodies" / "Join bodies" / "Split / join" for these parts, or null if none has bodies to split.
  function splitAction(paths) {
    var kinds = {};
    state.step.items.forEach(function (i) { if (paths.indexOf(i.path) >= 0 && i.split) kinds[i.split] = true; });
    if (kinds.split && kinds.join) return 'Split / join';
    return kinds.split ? 'Split bodies' : kinds.join ? 'Join bodies' : null;
  }
  // The menu lives on <body>: inside a card (a CSS container) a fixed menu is placed and stacked
  // within that card, so the cards after it drew on top.
  document.body.appendChild($('itemMenu'));
  // Icons for the right-click menus (parts list and steps).
  var CTX_ICONS = {
    trailOn: ICON.trailOn, trailOff: ICON.trailOff, anchor: ICON.anchor, unexplode: ICON.unexplode,
    bom: ICON.bomOff, split: ICON.split, remove: ICON.trash,
    edit: ICON.open, rename: ICON.edit, prep: ICON.parts, addBefore: ICON.plus, addAfter: ICON.plus,
    up: ICON.up, down: ICON.down, delete: ICON.trash
  };
  function closeItemMenu() { $('itemMenu').classList.add('hidden'); }
  $('items').addEventListener('contextmenu', function (e) {
    var li = e.target.closest('li[data-path], li[data-copies]');
    if (!li || !state.step) return;
    e.preventDefault();
    var ticked = checkedPaths();
    var own = li.hasAttribute('data-copies') ? (itemCopies[li.getAttribute('data-copies')] || [])   // a "×N" row: all its copies
                                             : [li.getAttribute('data-path')];
    var paths = own.every(function (p) { return ticked.indexOf(p) >= 0; }) ? ticked : own;
    var many = paths.length > 1;
    var items = [
      ['trailOn', 'Trail lines on'],
      ['trailOff', 'Trail lines off'],
      many ? false : ['anchor', 'Trail line start point\u2026'],
      ['unexplode', 'Unexplode (out of every move)'],
      null,
      ['bom', allOutOfBom(paths) ? "Count in this step's BOM" : "Not in this step's BOM"],
      splitAction(paths) ? ['split', splitAction(paths)] : false,
      null,
      ['remove', 'Remove from step', 'danger']
    ].filter(function (it) { return it !== false; })
     .filter(function (it, i, all) { return it !== null || (i > 0 && all[i - 1] !== null); });
    var m = $('itemMenu');
    m.innerHTML = (many ? '<div class="ctx-title">' + paths.length + ' ticked parts</div>' : '') + items.map(function (it) {
      if (it === null) return '<div class="ctx-sep"></div>';
      return '<button class="ctx-item' + (it[2] ? ' ' + it[2] : '') + '" data-ctx="' + it[0] + '">' +
        (CTX_ICONS[it[0]] || '<svg viewBox="0 0 16 16"></svg>') + '<span>' + esc(it[1]) + '</span></button>';
    }).join('');
    m.setAttribute('data-paths', JSON.stringify(paths));
    m.setAttribute('data-mode', 'parts');
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
    if ($('itemMenu').getAttribute('data-mode') === 'step') { stepMenuAction(act, $('itemMenu').getAttribute('data-step')); return; }
    if (act === 'trailOn') send('setTrail', { paths: paths, trail: true });
    else if (act === 'trailOff') send('setTrail', { paths: paths, trail: false });
    else if (act === 'anchor') send('setAnchor', { path: paths[0] });
    else if (act === 'unexplode') send('resetOffsets', { paths: paths });
    else if (act === 'bom') send('setItemBom', { paths: paths, on: allOutOfBom(paths) });
    else if (act === 'split') send('toggleSplit', { paths: paths });
    else if (act === 'remove') send('removeItems', { paths: paths });
  });
  // Steps tab: right-click a step for its options (same menu look as the parts list).
  $('tree').addEventListener('contextmenu', function (e) {
    var row = e.target.closest('.step[data-step]');
    if (!row) return;
    e.preventDefault();
    var id = row.getAttribute('data-step'), st = stepById(id);
    var w = allSteps().filter(function (x) { return x.step.id === id; })[0];
    var first = w.ti === 1, last = w.ti === w.section.steps.length;
    var items = [
      ['edit', 'Edit in the Step tab'],
      ['rename', 'Rename'],
      null,
      ['prep', st.prep ? 'Not a preparation step' : 'Preparation step'],
      ['addBefore', 'Add a step before this one'],
      ['addAfter', 'Add a step after this one'],
      null,
      first ? false : ['up', 'Move up'],
      last ? false : ['down', 'Move down'],
      null,
      ['delete', 'Delete step', 'danger']
    ].filter(function (it) { return it !== false; })
     .filter(function (it, i, all) { return it !== null || (i > 0 && all[i - 1] !== null); });
    var m = $('itemMenu');
    m.innerHTML = '<div class="ctx-title">' + esc(w.si + '.' + w.ti + '  ' + st.title) + '</div>' + items.map(function (it) {
      if (it === null) return '<div class="ctx-sep"></div>';
      return '<button class="ctx-item' + (it[2] ? ' ' + it[2] : '') + '" data-ctx="' + it[0] + '">' +
        (CTX_ICONS[it[0]] || '<svg viewBox="0 0 16 16"></svg>') + '<span>' + esc(it[1]) + '</span></button>';
    }).join('');
    m.setAttribute('data-mode', 'step');
    m.setAttribute('data-step', id);
    m.classList.remove('hidden');
    var r = m.getBoundingClientRect();
    m.style.left = Math.max(4, Math.min(e.clientX, window.innerWidth - r.width - 4)) + 'px';
    m.style.top = Math.max(4, Math.min(e.clientY, window.innerHeight - r.height - 4)) + 'px';
  });
  function stepMenuAction(act, id) {
    var w = allSteps().filter(function (x) { return x.step.id === id; })[0];
    if (!w) return;
    if (act === 'edit') { if (id === state.currentStepId) showTab('step'); else openStep(id, true); }
    else if (act === 'rename') {
      var el = document.querySelector('#tree [data-step="' + id + '"] .step-name');
      if (el) inlineRename(el, w.step.title, function (v) { send('renameStep', { id: id, title: v }); });
    }
    else if (act === 'prep') send('setPrep', { stepId: id, prep: !w.step.prep });
    else if (act === 'addAfter') {
      renameNewIn = { before: allSteps().map(function (x) { return x.step.id; }) };
      send('addStep', { sectionId: w.section.id, after: id });
    }
    else if (act === 'addBefore') {
      renameNewIn = { before: allSteps().map(function (x) { return x.step.id; }) };
      send('addStep', { sectionId: w.section.id, before: id });
    }
    else if (act === 'up') send('moveStep', { id: id, delta: -1 });
    else if (act === 'down') send('moveStep', { id: id, delta: 1 });
    else if (act === 'delete') send('deleteStep', { id: id });     // (Ctrl+Z brings it back)
  }
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
  // The folded card's icon buttons do the same (and don't fold / unfold the card).
  [['hdrSaveView', 'btnSaveView'], ['hdrGoView', 'btnGoView'], ['hdrAnnotate', 'btnAnnotate']].forEach(function (pair) {
    $(pair[0]).addEventListener('click', function (e) { e.stopPropagation(); $(pair[1]).click(); });
  });
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
  $('btnDeleteView').addEventListener('click', function (e) {
    armed(e.currentTarget, function () { send('clearCamera'); });
  });
  $('btnCoverDeleteView').addEventListener('click', function (e) {
    e.stopPropagation();
    armed(e.currentTarget, function () { send('pictureClearView', { kind: 'cover' }); });
  });
  $('btnDeleteAllViews').addEventListener('click', function (e) {
    armed(e.currentTarget, function () { send('clearAllViews'); });
  });
  $('btnThumbsMissing').addEventListener('click', function () { setBusy(true); send('refreshThumbs'); });
  $('btnLines').addEventListener('click', function () { send('editLines'); });
  $('btnAddExplode').addEventListener('click', function () { send('addExplode', { paths: checkedPaths() }); });
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
  $('btnStepAddStep').addEventListener('click', function () {
    var list = allSteps(), here = state.step && list.filter(function (w) { return w.step.id === state.step.id; })[0];
    if (!here) return;
    scrollStepTop = here.step.id;   // once the new step shows: back to the top of the tab
    send('addStep', { sectionId: here.section.id, after: here.step.id });
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
        } else if (action === 'selection') {
          var sel = JSON.parse(data);
          showFusionSelection(sel.paths || [], !!sel.ours);
        } else if (action === 'partMeshes') {
          gotPartMeshes(JSON.parse(data));
        } else if (action === 'logoPicked') {
          shrinkLogo(JSON.parse(data).url);
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
        reportError(e.message + ' (handling ' + action + ')', 'app.js', 0);
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
