/* Traceability canvas — pan / zoom / highlight / inspector.
 *
 * The SVG is server-rendered (reports/_traceability_canvas.html); this file
 * never builds nodes. Expand/collapse and the refresh poll swap the whole
 * fragment in, then the saved view transform is re-applied — so the picture
 * updates without the viewport jumping.
 *
 * Viewport math is the godhome block (canvas.ts:314-341), kept verbatim:
 * cursor-anchored zoom is t' = c - (c - t)·(k'/k), and fitView deliberately
 * ignores ZOOM_MIN — showing everything is the point of the control.
 */
(function () {
    'use strict';

    var board = document.querySelector('.trace-board');
    if (!board) { return; }
    var wrap = document.getElementById('traceCanvasWrap');
    var canvasUrl = board.dataset.canvasUrl;

    // ── viewport math (pure) ───────────────────────────────────────────────
    var ZOOM_MIN = 0.25, ZOOM_MAX = 2;
    var V = { tx: 0, ty: 0, k: 1 };

    function zoomAt(v, sx, sy, next) {
        var k = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, next)), r = k / v.k;
        return { tx: sx - (sx - v.tx) * r, ty: sy - (sy - v.ty) * r, k: k };
    }
    function fitView(w, h, vw, vh, pad) {
        var k = Math.min(ZOOM_MAX, (vw - 2 * pad) / Math.max(1, w), (vh - 2 * pad) / Math.max(1, h));
        return { tx: (vw - w * k) / 2, ty: (vh - h * k) / 2, k: k };
    }

    function viewport() { return wrap.querySelector('.trace-viewport'); }

    /* Move the transform, not the DOM — drags stay 60fps. */
    function applyView() {
        var g = viewport();
        if (g) {
            g.setAttribute('transform',
                'translate(' + V.tx + ',' + V.ty + ') scale(' + V.k + ')');
        }
    }

    function fit() {
        var svg = wrap.querySelector('.trace-svg');
        if (!svg) { return; }
        var w = parseFloat(svg.dataset.w) || 800;
        var h = parseFloat(svg.dataset.h) || 600;
        V = fitView(w, h, wrap.clientWidth, wrap.clientHeight, 20);
        applyView();
    }

    // ── pan + zoom ─────────────────────────────────────────────────────────
    var SPACE = false, panning = null;

    wrap.addEventListener('wheel', function (ev) {
        ev.preventDefault();
        var r = wrap.getBoundingClientRect();
        var factor = ev.deltaY < 0 ? 1.15 : 1 / 1.15;
        V = zoomAt(V, ev.clientX - r.left, ev.clientY - r.top, V.k * factor);
        applyView();
    }, { passive: false });

    wrap.addEventListener('mousedown', function (ev) {
        // space-drag or middle-drag pans (n8n convention); plain drag pans
        // too here — this canvas has no marquee, nothing else claims it.
        if (ev.button !== 0 && ev.button !== 1) { return; }
        if (ev.target.closest('.trace-node') && !SPACE && ev.button === 0) { return; }
        panning = { sx: ev.clientX, sy: ev.clientY, tx: V.tx, ty: V.ty };
        wrap.classList.add('panning');
        ev.preventDefault();
    });
    document.addEventListener('mousemove', function (ev) {
        if (!panning) { return; }
        V.tx = panning.tx + (ev.clientX - panning.sx);
        V.ty = panning.ty + (ev.clientY - panning.sy);
        applyView();
    });
    document.addEventListener('mouseup', function () {
        panning = null;
        wrap.classList.remove('panning');
    });

    document.addEventListener('keydown', function (ev) {
        if (ev.target.matches('input, select, textarea')) { return; }
        if (ev.key === ' ') { SPACE = true; ev.preventDefault(); }
        if (ev.key === '0') { fit(); }
    });
    document.addEventListener('keyup', function (ev) {
        if (ev.key === ' ') { SPACE = false; }
    });

    var zi = document.getElementById('traceZoomIn');
    var zo = document.getElementById('traceZoomOut');
    var zf = document.getElementById('traceZoomFit');
    function centerZoom(factor) {
        V = zoomAt(V, wrap.clientWidth / 2, wrap.clientHeight / 2, V.k * factor);
        applyView();
    }
    if (zi) { zi.onclick = function () { centerZoom(1.25); }; }
    if (zo) { zo.onclick = function () { centerZoom(1 / 1.25); }; }
    if (zf) { zf.onclick = fit; }

    // ── hover: light the path through a node, dim the rest ─────────────────
    function neighbours() {
        var up = {}, down = {};
        wrap.querySelectorAll('path.trace-edge').forEach(function (p) {
            var f = p.dataset.from, t = p.dataset.to;
            if (!f || !t) { return; }
            (down[f] = down[f] || []).push(t);
            (up[t] = up[t] || []).push(f);
        });
        return { up: up, down: down };
    }

    function closure(id, adj) {
        var seen = {}, stack = [id];
        while (stack.length) {
            var n = stack.pop();
            if (seen[n]) { continue; }
            seen[n] = true;
            (adj[n] || []).forEach(function (m) { stack.push(m); });
        }
        return seen;
    }

    function highlight(id) {
        var nb = neighbours();
        var lit = closure(id, nb.up);
        var fwd = closure(id, nb.down);
        Object.keys(fwd).forEach(function (k) { lit[k] = true; });
        wrap.querySelectorAll('.trace-node').forEach(function (g) {
            g.classList.toggle('faded', !lit[g.dataset.id]);
        });
        wrap.querySelectorAll('path.trace-edge, text.trace-elabel').forEach(function (p) {
            p.classList.toggle('faded', !(lit[p.dataset.from] && lit[p.dataset.to]));
        });
    }
    function unhighlight() {
        wrap.querySelectorAll('.faded').forEach(function (el) {
            el.classList.remove('faded');
        });
    }

    // ── clicks: expand groups, inspect nodes ───────────────────────────────
    var dockTitle = document.getElementById('traceDockTitle');
    var dockBack = document.getElementById('traceDockBack');
    var issuesPane = document.getElementById('traceIssues');
    var nodePane = document.getElementById('traceNodePane');

    function showIssues() {
        if (nodePane) { nodePane.classList.add('d-none'); }
        if (issuesPane) { issuesPane.classList.remove('d-none'); }
        if (dockBack) { dockBack.classList.add('d-none'); }
    }
    function showNode(html) {
        if (issuesPane) { issuesPane.classList.add('d-none'); }
        if (nodePane) {
            nodePane.innerHTML = html;
            nodePane.classList.remove('d-none');
        }
        if (dockBack) { dockBack.classList.remove('d-none'); }
    }
    if (dockBack) { dockBack.onclick = showIssues; }

    function currentParams() {
        var p = new URLSearchParams(window.location.search);
        return p;
    }

    function expandTokens() {
        var raw = (currentParams().get('expand') || '');
        return raw ? raw.split(',').filter(Boolean) : [];
    }

    function setExpand(tokens) {
        var p = currentParams();
        if (tokens.length) { p.set('expand', tokens.join(',')); }
        else { p.delete('expand'); }
        history.replaceState(null, '', '?' + p.toString());
        var hidden = document.querySelector('#traceForm input[name="expand"]');
        if (hidden) { hidden.value = tokens.join(','); }
        refresh();
    }

    function refresh() {
        var p = currentParams();
        fetch(canvasUrl + '?' + p.toString(), { credentials: 'same-origin' })
            .then(function (r) { return r.text(); })
            .then(function (html) {
                var focus = wrap.dataset.focus;
                wrap.innerHTML = html;
                wrap.dataset.focus = focus || '';
                applyView();   // keep the operator's viewport across the swap
                bindCanvas();
            })
            .catch(function () { /* offline blip — next poll retries */ });
    }

    function bindCanvas() {
        wrap.querySelectorAll('.trace-node').forEach(function (g) {
            g.addEventListener('mouseenter', function () { highlight(g.dataset.id); });
            g.addEventListener('mouseleave', unhighlight);
            g.addEventListener('click', function (ev) {
                ev.stopPropagation();
                var token = g.dataset.expand;
                if (token && !g.dataset.expanded) {
                    var t = expandTokens();
                    if (t.indexOf(token) < 0) { t.push(token); }
                    setExpand(t);
                    return;
                }
                if (token && g.dataset.expanded && ev.detail === 2) {
                    // double-click collapses an expanded group again
                    setExpand(expandTokens().filter(function (x) { return x !== token; }));
                    return;
                }
                var url = g.dataset.detail;
                if (url) {
                    fetch(url, { credentials: 'same-origin' })
                        .then(function (r) { return r.text(); })
                        .then(showNode);
                }
            });
        });
    }

    // Issues panel → jump the canvas to the node and open its inspector.
    if (issuesPane) {
        issuesPane.addEventListener('click', function (ev) {
            var btn = ev.target.closest('.trace-issue');
            if (!btn) { return; }
            var g = wrap.querySelector('.trace-node[data-id="' + btn.dataset.node + '"]');
            if (!g) { return; }
            var m = /translate\(([-\d.]+),([-\d.]+)\)/.exec(g.getAttribute('transform'));
            if (m) {
                V.tx = wrap.clientWidth / 2 - (parseFloat(m[1]) + 95) * V.k;
                V.ty = wrap.clientHeight / 2 - (parseFloat(m[2]) + 37) * V.k;
                applyView();
            }
            g.classList.add('flash');
            setTimeout(function () { g.classList.remove('flash'); }, 1200);
            var url = g.dataset.detail;
            if (url) {
                fetch(url, { credentials: 'same-origin' })
                    .then(function (r) { return r.text(); })
                    .then(showNode);
            }
        });
    }

    // ── live refresh: the floor enters decisions, the wall screen follows ──
    setInterval(refresh, 20000);

    // ── boot ───────────────────────────────────────────────────────────────
    bindCanvas();
    fit();
    var focus = wrap.dataset.focus;
    if (focus) {
        var g = wrap.querySelector('.trace-node[data-id="' + focus + '"]');
        if (g) {
            g.classList.add('flash');
            setTimeout(function () { g.classList.remove('flash'); }, 2000);
        }
    }
})();
