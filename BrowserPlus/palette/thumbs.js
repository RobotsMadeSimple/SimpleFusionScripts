/* Browser+ part pictures (Tree and BOM rows).
 *
 * Python sends a coarse mesh of a component (lib/partthumbs.py); it's drawn here as a small
 * shaded isometric picture and kept in localStorage under the component id with its key
 * (component + body revisions). A cached picture shows at once; each component is checked once
 * per design read (state.partsGen) and drawn again only if its key changed.
 */
(function () {
  var SIZE = 160;                      // drawn large so the row and hover pictures stay sharp
  var PREFIX = 'browserplus.pt.';
  var cache = {}, checked = {}, wanted = {}, waiting = false, timer = null, lastGen = null;

  function esc(s) { return BP.esc(s); }
  function get(cid) {
    if (cache[cid] === undefined) {
      try { cache[cid] = JSON.parse(localStorage.getItem(PREFIX + cid) || 'null'); } catch (e) { cache[cid] = null; }
    }
    return cache[cid];        // {k: key, u: data url} or null
  }

  // A picture span for a row, or the fallback (the usual icon) when there's no picture yet.
  function want(cid, path) {
    var c = get(cid);
    if (!checked[cid]) { wanted[cid] = { cid: cid, path: path, have: c ? c.k : '' }; schedule(); }
    return c;
  }

  // The picture's data url for drawing it yourself (the Map's SVG), '' while there isn't one;
  // tag the element data-pt="<cid>" and an <image> gets its href when the picture arrives.
  function url(cid, path) {
    if (!cid || !path) return '';
    var c = want(cid, path);
    return c && c.u ? c.u : '';
  }

  function html(cid, path, fallback) {
    if (!cid || !path) return fallback;
    var c = want(cid, path);
    if (c && c.u) return '<span class="pthumb" data-pt="' + esc(cid) + '" style="background-image:url(' + c.u + ')"></span>';
    if (c && c.k === '' && checked[cid]) return fallback;
    return '<span class="pthumb-wait" data-pt="' + esc(cid) + '">' + fallback + '</span>';
  }

  function schedule() {
    if (waiting) return;
    clearTimeout(timer);
    timer = setTimeout(function () {
      var items = Object.keys(wanted).slice(0, 24).map(function (cid) { var w = wanted[cid]; delete wanted[cid]; return w; });
      if (!items.length) return;
      waiting = true;
      BP.send('partThumbs', { items: items });
      setTimeout(function () { if (waiting) { waiting = false; schedule(); } }, 15000);   // no reply: carry on
    }, 200);
  }

  function got(reply) {
    waiting = false;
    Object.keys(reply).forEach(function (cid) {
      var r = reply[cid], c = get(cid);
      checked[cid] = true;
      if (r.mesh) c = { k: r.key, u: draw(r.mesh) };
      else if (!c || c.k !== r.key) c = { k: r.key, u: '' };
      cache[cid] = c;
      try { localStorage.setItem(PREFIX + cid, JSON.stringify(c)); } catch (e) { /* storage full: shown this session */ }
      if (!c.u) return;
      Array.prototype.forEach.call(document.querySelectorAll('[data-pt="' + cid.replace(/["\\]/g, '\\$&') + '"]'), function (el) {
        if (el.tagName.toLowerCase() === 'image') {          // the Map's SVG
          el.setAttribute('href', c.u);
          var node = el.closest('.node');
          if (node) node.classList.add('pic');
          return;
        }
        if (el.classList.contains('pthumb-wait')) {
          var s = document.createElement('span');
          s.className = 'pthumb'; s.setAttribute('data-pt', cid);
          el.parentNode.replaceChild(s, el); el = s;
        }
        el.style.backgroundImage = 'url(' + c.u + ')';
      });
    });
    schedule();       // the next batch (and anything not answered in time)
  }

  // The design was read again (parts may have changed): check pictures again as rows show.
  function gen(g) {
    if (g === lastGen) return;
    lastGen = g; checked = {}; wanted = {};
  }

  function draw(mesh) {
    var p = mesh.p, t = mesh.t, n = p.length / 3;
    // Isometric view: turn 45 deg about Z, tilt 35 deg (Z up, like Fusion's Home view).
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
    var span = Math.max(maxX - minX, maxY - minY) || 1, pad = SIZE * 0.06, k = (SIZE - 2 * pad) / span;
    var ox = pad + ((SIZE - 2 * pad) - (maxX - minX) * k) / 2, oy = pad + ((SIZE - 2 * pad) - (maxY - minY) * k) / 2;
    var tris = [];
    for (var j = 0; j < t.length; j += 3) {
      var a = t[j], b = t[j + 1], c = t[j + 2];
      var ux = X[b] - X[a], uy = Y[b] - Y[a], uz = Z[b] - Z[a], vx = X[c] - X[a], vy = Y[c] - Y[a], vz = Z[c] - Z[a];
      var nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
      var len = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1;
      tris.push([a, b, c, (Z[a] + Z[b] + Z[c]) / 3, nx / len, ny / len, nz / len]);
    }
    tris.sort(function (m, q) { return q[3] - m[3]; });          // far first (painter's order)
    var cv = document.createElement('canvas');
    cv.width = cv.height = SIZE;
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

  // Hover a picture for a bigger one.
  var pop = null, popTimer = null, popOn = null;
  document.addEventListener('mouseover', function (e) {
    var el = e.target.closest && e.target.closest('.pthumb');
    if (el === popOn) return;
    popOn = el;
    clearTimeout(popTimer);
    if (pop) pop.style.display = 'none';
    if (!el) return;
    popTimer = setTimeout(function () {
      if (!el.isConnected) return;
      if (!pop) { pop = document.createElement('div'); pop.className = 'pthumb-pop'; document.body.appendChild(pop); }
      var r = el.getBoundingClientRect();
      pop.style.backgroundImage = el.style.backgroundImage;
      pop.style.display = 'block';
      pop.style.left = Math.min(r.right + 8, window.innerWidth - 168) + 'px';
      pop.style.top = Math.max(4, Math.min(r.top - 70, window.innerHeight - 168)) + 'px';
    }, 350);
  });

  window.BPThumbs = { html: html, url: url, got: got, gen: gen, draw: draw };
})();
