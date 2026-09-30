/* BuildBook annotations: the one canvas renderer, used by the editor (annotate.html) and
 * to draw them onto exported PNGs (app.js). lib/manual_pdf.py draws the same shapes in the PDF
 * with the same geometry -- keep the two in step.
 *
 * An annotation: {id, type: 'arrow'|'line'|'rect'|'ellipse'|'text'|'callout',
 *   x1, y1, x2, y2   -- 0..1 of the image's width / height (y down),
 *   color '#rrggbb', weight (stroke, in 1/1000 of the image width), dashed,
 *   heads 'end'|'both'|'none' (arrow/line), fill (rect/ellipse: fill of the colour),
 *   fillOpacity (0.05..1, default 0.15),
 *   text, size (text height in 1/1000 of the image width), bold, box (text: white box behind),
 *   leader (text: arrow from the text to x2,y2), number (callout)}
 * Geometry (U = image width / 1000):
 *   stroke = weight*U; dashes [4, 3] x stroke; arrow head length L = (4*weight + 6)*U, half-width 0.45 L,
 *   the line stops 0.8 L short of a head; fill = colour at fillOpacity (default 15 %);
 *   text: font size*U px Helvetica/Arial, line height 1.25, top-left at (x1,y1); box padding 0.3*size*U;
 *   callout: circle at (x1,y1), radius 0.8*size*U, white inside, number bold 0.9*size*U;
 *   leaders end in an arrow head (text) or a dot of radius 1.4*weight*U (callout).
 */
(function (root) {
  'use strict';

  var DEFAULTS = { color: '#d9463e', weight: 3, dashed: false, heads: 'end', fill: false,
                   size: 28, bold: false, box: false, leader: false };

  function num(v, d) { return typeof v === 'number' && !isNaN(v) ? v : d; }

  function headPoints(x1, y1, x2, y2, L) {
    var a = Math.atan2(y2 - y1, x2 - x1), w = 0.45 * L;
    var bx = x2 - Math.cos(a) * L, by = y2 - Math.sin(a) * L;
    return [[x2, y2], [bx + Math.sin(a) * w, by - Math.cos(a) * w], [bx - Math.sin(a) * w, by + Math.cos(a) * w]];
  }

  function shorten(x1, y1, x2, y2, d) {
    var len = Math.hypot(x2 - x1, y2 - y1);
    if (len < 1e-6) return [x2, y2];
    var k = Math.max(0, len - d) / len;
    return [x1 + (x2 - x1) * k, y1 + (y2 - y1) * k];
  }

  function setStroke(ctx, a, U) {
    var w = num(a.weight, DEFAULTS.weight) * U;
    ctx.lineWidth = w;
    ctx.strokeStyle = a.color || DEFAULTS.color;
    ctx.fillStyle = a.color || DEFAULTS.color;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.setLineDash(a.dashed ? [4 * w, 3 * w] : []);
    return w;
  }

  function arrowLine(ctx, x1, y1, x2, y2, heads, weight, U) {
    var L = (4 * weight + 6) * U;
    var s = [x1, y1], e = [x2, y2];
    if (heads === 'end' || heads === 'both') e = shorten(x1, y1, x2, y2, 0.8 * L);
    if (heads === 'both') s = shorten(x2, y2, x1, y1, 0.8 * L);
    ctx.beginPath(); ctx.moveTo(s[0], s[1]); ctx.lineTo(e[0], e[1]); ctx.stroke();
    ctx.setLineDash([]);
    [['end', x1, y1, x2, y2], ['start', x2, y2, x1, y1]].forEach(function (h) {
      if (!(heads === 'both' || (heads === 'end' && h[0] === 'end'))) return;
      var p = headPoints(h[1], h[2], h[3], h[4], L);
      ctx.beginPath(); ctx.moveTo(p[0][0], p[0][1]); ctx.lineTo(p[1][0], p[1][1]); ctx.lineTo(p[2][0], p[2][1]);
      ctx.closePath(); ctx.fill();
    });
  }

  function font(a, U, scale, bold) {
    return (bold ? 'bold ' : '') + (num(a.size, DEFAULTS.size) * U * (scale || 1)) + 'px Helvetica, Arial, sans-serif';
  }

  // Text box in pixels: {x, y, w, h, lines, lh} (x,y top-left, padding included when boxed).
  function textBox(ctx, a, W, H) {
    var U = W / 1000, size = num(a.size, DEFAULTS.size) * U;
    ctx.font = font(a, U, 1, a.bold);
    var lines = String(a.text || '').split('\n'), lh = 1.25 * size, w = 0;
    lines.forEach(function (l) { w = Math.max(w, ctx.measureText(l).width); });
    var pad = a.box ? 0.3 * size : 0;
    return { x: a.x1 * W, y: a.y1 * H, w: w + 2 * pad, h: lines.length * lh + 2 * pad, lines: lines, lh: lh, pad: pad, size: size };
  }

  function drawOne(ctx, a, W, H) {
    var U = W / 1000, weight = num(a.weight, DEFAULTS.weight);
    var x1 = a.x1 * W, y1 = a.y1 * H, x2 = num(a.x2, a.x1) * W, y2 = num(a.y2, a.y1) * H;
    ctx.save();
    setStroke(ctx, a, U);
    if (a.type === 'arrow' || a.type === 'line') {
      arrowLine(ctx, x1, y1, x2, y2, a.type === 'line' && !a.heads ? 'none' : (a.heads || (a.type === 'arrow' ? 'end' : 'none')), weight, U);
    } else if (a.type === 'rect' || a.type === 'ellipse') {
      var x = Math.min(x1, x2), y = Math.min(y1, y2), w = Math.abs(x2 - x1), h = Math.abs(y2 - y1);
      ctx.beginPath();
      if (a.type === 'rect') ctx.rect(x, y, w, h);
      else ctx.ellipse(x + w / 2, y + h / 2, Math.max(w / 2, 0.5), Math.max(h / 2, 0.5), 0, 0, 2 * Math.PI);
      if (a.fill) { ctx.save(); ctx.globalAlpha = Math.min(1, Math.max(0.05, num(a.fillOpacity, 0.15))); ctx.fill(); ctx.restore(); }
      ctx.stroke();
    } else if (a.type === 'text') {
      var b = textBox(ctx, a, W, H);
      if (a.leader) {
        var tx = Math.max(b.x, Math.min(x2, b.x + b.w)), ty = Math.max(b.y, Math.min(y2, b.y + b.h));
        arrowLine(ctx, tx, ty, x2, y2, 'end', weight, U);
        setStroke(ctx, a, U);
      }
      if (a.box) {
        ctx.setLineDash([]);
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(b.x, b.y, b.w, b.h);
        ctx.strokeRect(b.x, b.y, b.w, b.h);
      }
      ctx.fillStyle = a.color || DEFAULTS.color;
      ctx.font = font(a, U, 1, a.bold);
      ctx.textBaseline = 'alphabetic';
      b.lines.forEach(function (l, i) { ctx.fillText(l, b.x + b.pad, b.y + b.pad + (i + 0.8) * b.lh); });
    } else if (a.type === 'callout') {
      var r = 0.8 * num(a.size, DEFAULTS.size) * U;
      if (Math.hypot(x2 - x1, y2 - y1) > r) {
        var s = shorten(x2, y2, x1, y1, r);          // from the circle's edge
        ctx.beginPath(); ctx.moveTo(s[0], s[1]); ctx.lineTo(x2, y2); ctx.stroke();
        ctx.setLineDash([]);
        ctx.beginPath(); ctx.arc(x2, y2, 1.4 * weight * U, 0, 2 * Math.PI); ctx.fill();
      }
      ctx.setLineDash([]);
      ctx.beginPath(); ctx.arc(x1, y1, r, 0, 2 * Math.PI);
      ctx.fillStyle = '#ffffff'; ctx.fill(); ctx.stroke();
      ctx.fillStyle = a.color || DEFAULTS.color;
      ctx.font = font(a, U, 0.9, true);
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(String(a.number == null ? '' : a.number), x1, y1 + 0.05 * r);
    }
    ctx.restore();
  }

  function draw(ctx, annotations, W, H) {
    (annotations || []).forEach(function (a) { drawOne(ctx, a, W, H); });
  }

  // An image (data URL) with the annotations drawn on it, as a PNG data URL (for exports).
  function compose(dataUrl, annotations, done) {
    var img = new Image();
    img.onload = function () {
      var c = document.createElement('canvas');
      c.width = img.naturalWidth; c.height = img.naturalHeight;
      var ctx = c.getContext('2d');
      ctx.drawImage(img, 0, 0);
      draw(ctx, annotations, c.width, c.height);
      done(null, c.toDataURL('image/png'));
    };
    img.onerror = function () { done(new Error('image failed to load')); };
    img.src = dataUrl;
  }

  root.BBAnnot = { DEFAULTS: DEFAULTS, draw: draw, drawOne: drawOne, textBox: textBox, compose: compose };
})(window);
