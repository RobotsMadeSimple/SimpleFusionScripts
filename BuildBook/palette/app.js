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
    edit: '<svg viewBox="0 0 16 16"><path d="M10.5 2.5l3 3L6 13H3v-3z"/></svg>',
    close: '<svg viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8"/></svg>',
    anchor: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="3"/><path d="M8 1.5v3M8 11.5v3M1.5 8h3M11.5 8h3"/></svg>',
    up: '<svg viewBox="0 0 16 16"><path d="m4 10 4-4 4 4"/></svg>',
    down: '<svg viewBox="0 0 16 16"><path d="m4 6 4 4 4-4"/></svg>',
  };

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

  function renderTree() {
    var n = 0;
    var html = state.manual.sections.map(function (sec) {
      var views = 0, exported = 0;
      var steps = sec.steps.map(function (st) {
        n += 1;
        if (st.camera) views += 1;
        if (st.exportedAt) exported += 1;
        var thumb = state.thumbs && state.thumbs[st.id];
        return '<div class="step' + (st.id === state.currentStepId ? ' active' : '') + '" data-step="' + st.id + '" data-act="open">' +
          '<span class="grip" title="Drag to reorder">&#x22EE;&#x22EE;</span>' +
          '<span class="thumb"' + thumbStyle(st.id) + '>' + (thumb ? '' : n) + '</span>' +
          '<span class="num">' + n + '</span>' +
          '<span class="name" title="' + esc(st.title) + '">' + esc(st.title) + '</span>' +
          (st.prep ? '<span class="prep-tag" title="Preparation step">prep</span>' : '') +
          '<span class="meta" title="Parts">' + st.items.length + '</span>' +
          '<span class="status-icons">' +
            '<span class="' + (st.camera ? 'on' : 'off') + '" title="' +
              (st.camera ? 'Screenshot view saved' : 'No screenshot view saved yet') + '">' + ICON.camera + '</span>' +
            '<span class="' + (st.exportedAt ? 'ok' : 'off') + '" title="' +
              (st.exportedAt ? 'Exported ' + esc(st.exportedAt) : 'Not exported yet') + '">' + ICON.image + '</span>' +
          '</span>' +
          '<span class="tools">' +
            '<button data-act="stepUp" title="Move up">' + ICON.up + '</button>' +
            '<button data-act="stepDown" title="Move down">' + ICON.down + '</button>' +
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
            '<button data-act="secAddStep" title="Add step">+ Step</button>' +
            '<button data-act="secUp" title="Move up">' + ICON.up + '</button>' +
            '<button data-act="secDown" title="Move down">' + ICON.down + '</button>' +
            '<button data-act="secDelete" class="danger" title="Delete section">' + ICON.close + '</button>' +
          '</span></div>' +
        '<div class="steps">' + (steps || '<div class="empty">No steps yet. Hover the section header and click + Step.</div>') + '</div>' +
        '</div>';
    }).join('');
    $('tree').innerHTML = html || '<div class="empty-state"><p>No sections yet.</p><p class="sub">Start with <b>+ Section</b>, e.g. "Frame".</p></div>';
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

    var where = list[pos];
    $('stepCrumb').textContent = where.section.title + ' · Step ' + where.ti;
    $('stepTitle').textContent = step.title;
    $('btnPrevStep').disabled = pos <= 0;
    $('btnNextStep').disabled = pos >= list.length - 1;
    $('editBadge').classList.toggle('hidden', !state.edit);
    $('stepPrep').checked = !!step.prep;
    fillSelect($('ctxEarlier'), CONTEXT_STEP, step.earlier);
    fillSelect($('ctxLater'), CONTEXT_STEP, step.later);
    if (document.activeElement !== $('stepDistance')) {
      $('stepDistance').value = detail.defaultOwn ? detail.defaultDistance : '';
    }
    $('stepDistance').placeholder = state.manualDefault + ' (manual default)';
    if (document.activeElement !== $('notes')) $('notes').value = step.notes || '';

    $('btnGoView').disabled = !detail.hasCamera;
    $('btnSaveView').textContent = detail.hasCamera ? 'Update view' : 'Save view';
    var thumb = state.thumbs && state.thumbs[step.id];
    $('stepThumb').style.backgroundImage = thumb ? "url('" + thumb + "')" : '';
    $('stepThumb').textContent = thumb ? '' : 'No preview yet';
    $('viewHint').innerHTML = esc(detail.image.text) + '<br>' +
      (detail.hasCamera ? '&#x2713; Saved view: opening this step goes there.'
                        : 'No saved view yet: export uses the current camera.') +
      (step.exportedAt ? '<br>Last exported ' + esc(step.exportedAt) : '') +
      '<br><span title="' + esc(detail.imageName) + '">' + esc(detail.imageName) + '</span>';

    if (detail.id !== checkedStep) { checked = {}; checkedStep = detail.id; }   // ticks belong to one step
    var paths = detail.items.map(function (i) { return i.path; });
    Object.keys(checked).forEach(function (p) { if (paths.indexOf(p) < 0) delete checked[p]; });

    $('items').innerHTML = detail.items.map(function (it) {
      return '<li class="' + (it.missing ? 'missing' : '') + '" data-path="' + esc(it.path) + '">' +
        '<input type="checkbox" data-act="check"' + (checked[it.path] ? ' checked' : '') + '>' +
        '<span class="name" data-act="select" title="Select in Fusion">' +
          '<div>' + esc(it.name) + '</div>' +
          '<div class="sub">' + esc(it.component) + '</div></span>' +
        '<span class="in-moves" title="Explode moves this part is in">' +
          (it.moves.length ? 'Move ' + it.moves.join(', ') : 'not exploded') + '</span>' +
        '<button class="icon-btn anchor-btn' + (it.anchor ? ' on' : '') + '" data-act="anchor" title="' +
          (it.anchor ? 'Trail lines start at a picked point. Click to change or reset.'
                     : 'Trail lines start at the part centre. Click to pick another point (e.g. a hole).') +
          '">' + ICON.anchor + '</button>' +
        '</li>';
    }).join('');
    renderExplodes(detail);
    $('itemCount').textContent = detail.items.length;
    $('checkAll').checked = detail.items.length > 0 && paths.every(function (p) { return checked[p]; });
  }

  function renderUnassigned() {
    var list = state.unassigned;
    var filter = $('unassignedFilter').value.trim().toLowerCase();
    $('unassignedCount').textContent = list.length;
    var shown = filter ? list.filter(function (u) {
      return (u.name + ' ' + u.path + ' ' + u.component).toLowerCase().indexOf(filter) >= 0;
    }) : list;
    $('unassigned').innerHTML = shown.map(function (u) {
      return '<li data-path="' + esc(u.path) + '">' +
        '<input type="checkbox" data-act="checkU"' + (checkedUnassigned[u.path] ? ' checked' : '') + '>' +
        '<span class="name" data-act="select" title="Select in Fusion"><div>' + esc(u.name) + '</div>' +
        '<div class="sub">' + esc(u.path) + '</div></span></li>';
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
          '<span class="grip" title="Drag to reorder">&#x22EE;&#x22EE;</span>' +
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
      $('explodes').innerHTML = '<li class="empty">No moves yet. Check parts below (or select them in Fusion), then <b>+ Add move</b>.</li>';
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
    fillSelect($('setRatio'), state.ratios, img.ratio || 'viewport');
    $('setTransparent').checked = !!img.transparent;
    var anySteps = state.manual.sections.some(function (s) { return s.steps.length; });
    $('btnExportAll').disabled = !anySteps;
    $('btnExportAllTo').disabled = !anySteps;
    $('setAskFolder').checked = !!state.manual.settings.askFolder;
    $('setShowFrame').checked = !!state.manual.settings.showCropFrame;
    $('btnExportAll').textContent = state.manual.settings.askFolder ? 'Export all steps…' : 'Export all steps';
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
    if (e.key === 'Enter' && t.matches && t.matches('.dist-input, #stepDistance, #setDistance')) {
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
    if (e.button !== 0 || !e.target.classList.contains('grip')) return;
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
    if (!actEl || e.target.classList.contains('grip')) return;
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
    else if (act === 'secUp') send('moveSection', { id: secId, delta: -1 });
    else if (act === 'secDown') send('moveSection', { id: secId, delta: 1 });
    else if (act === 'secDelete') armed(actEl, function () { send('deleteSection', { id: secId }); });
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
    if (e.button !== 0 || !e.target.classList.contains('grip')) return;
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
    if (act === 'checkU') checkedUnassigned[path] = e.target.checked;
    else if (act === 'select') send('selectItems', { paths: [path] });
  });

  $('stepDistance').addEventListener('change', function (e) { send('setStepDistance', { value: e.target.value }); });
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
  $('btnAddSection').addEventListener('click', function () { send('addSection'); });
  $('btnAssembled').addEventListener('click', function () { send('closeView'); });
  $('btnRefresh').addEventListener('click', function () { send('refresh'); });
  $('btnPick').addEventListener('click', function () { send('pick'); });
  $('btnAddSelected').addEventListener('click', function () { send('addSelected'); });
  $('btnSaveView').addEventListener('click', function () { send('saveCamera'); });
  $('btnGoView').addEventListener('click', function () { send('goCamera'); });
  $('btnExportStep').addEventListener('click', function () { send('exportStep'); });
  $('btnExportAll').addEventListener('click', function () { send('exportAll'); });
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
  $('btnLines').addEventListener('click', function () { send('editLines'); });
  $('btnAddExplode').addEventListener('click', function () { send('addExplode', { paths: checkedPaths() }); });
  $('btnShowAll').addEventListener('click', function () { send('scrubExplode', { id: null }); });
  $('btnTrailOn').addEventListener('click', function () { send('setTrail', { paths: checkedPaths(), trail: true }); });
  $('btnTrailOff').addEventListener('click', function () { send('setTrail', { paths: checkedPaths(), trail: false }); });
  $('btnReset').addEventListener('click', function (e) {
    var paths = checkedPaths();
    if (paths.length) send('resetOffsets', { paths: paths });
    else armed(e.target, function () { send('resetOffsets', { paths: [] }); });
  });
  $('btnRemove').addEventListener('click', function () {
    var paths = checkedPaths();
    if (paths.length) send('removeItems', { paths: paths });
  });
  $('btnAddChecked').addEventListener('click', function () {
    var paths = Object.keys(checkedUnassigned).filter(function (p) { return checkedUnassigned[p]; });
    if (!paths.length) return;
    checkedUnassigned = {};
    send('addPaths', { paths: paths });
  });
  $('unassignedFilter').addEventListener('input', function () { if (state) renderUnassigned(); });

  $('stepPrep').addEventListener('change', function (e) { send('setPrep', { prep: e.target.checked }); });
  $('ctxEarlier').addEventListener('change', function (e) { send('setContext', { earlier: e.target.value }); });
  $('ctxLater').addEventListener('change', function (e) { send('setContext', { later: e.target.value }); });
  $('notes').addEventListener('change', function (e) { send('setNotes', { notes: e.target.value }); });
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
        }
      } catch (e) {
        setBusy(false);
        $('error').textContent = e.message;
        $('error').classList.remove('hidden');
      }
      return 'OK';
    }
  };

  showTab(recall('tab') || 'steps');

  // Fusion injects `adsk` after load; wait for it before asking for state.
  (function ready(tries) {
    if (window.adsk && window.adsk.fusionSendData) send('ready');
    else if (tries < 50) setTimeout(function () { ready(tries + 1); }, 100);
  })(0);
})();
