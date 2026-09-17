(function () {
    'use strict';

    function boot(root) {
        if (root.dataset.selectionReady === 'true') return;
        root.dataset.selectionReady = 'true';

        var isAr = root.dataset.isAr === 'true';
        var lookupUrl = root.dataset.lookupUrl;
        var storageKey = root.dataset.storageKey;
        var reconcileUrl = root.dataset.selectionReconcileUrl;
        var targetForm = document.getElementById(root.dataset.targetForm);
        var fieldName = root.dataset.fieldName || 'pipe_ids';
        var rowsNode = root.querySelector('[data-selection-rows]');
        var messageNode = root.querySelector('[data-selection-message]');
        var candidatesNode = root.querySelector('[data-selection-candidates]');
        var basketNode = root.querySelector('[data-selection-basket]');
        var countNode = root.querySelector('[data-selection-count]');
        var rows = [];
        try { rows = JSON.parse(rowsNode.textContent || '[]'); } catch (e) { rows = []; }

        var known = {};
        rows.forEach(function (pipe) { known[String(pipe.id)] = pipe; });
        var basket = rows.filter(function (pipe) { return pipe.selected && pipe.selectable !== false; });
        var storedIds = [];
        var pendingReconcile = false;

        if (storageKey) {
            try {
                var stored = JSON.parse(sessionStorage.getItem(storageKey) || '[]');
                if (Array.isArray(stored)) {
                    storedIds = stored.map(function (item) {
                        return Number(item && typeof item === 'object' ? item.id : item);
                    }).filter(function (id) { return Number.isInteger(id) && id > 0; });
                    basket = basket.concat(rows.filter(function (pipe) {
                        return storedIds.indexOf(Number(pipe.id)) !== -1 && pipe.selectable !== false;
                    }));
                }
            } catch (e) {
                storedIds = [];
            }
            if (/[?&]saved=/.test(window.location.search)) {
                basket = [];
                storedIds = [];
                try { sessionStorage.removeItem(storageKey); } catch (e) {}
            }
            pendingReconcile = Boolean(reconcileUrl && storedIds.some(function (id) {
                return !known[String(id)];
            }));
        }

        basket = unique(basket);

        function t(ar, en) { return isAr ? ar : en; }
        function esc(value) {
            return String(value == null ? '' : value).replace(/[&<>"']/g, function (char) {
                return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char];
            });
        }
        function unique(items) {
            var seen = {};
            return items.filter(function (item) {
                var id = String(item.id);
                if (seen[id]) return false;
                seen[id] = true;
                return true;
            });
        }
        function showMessage(text, kind) {
            messageNode.innerHTML = text ? '<div class="alert alert-' + kind + ' py-2 mb-0">' + text + '</div>' : '';
        }
        function persist() {
            if (!storageKey || pendingReconcile) return;
            try {
                sessionStorage.setItem(storageKey, JSON.stringify(
                    basket.map(function (pipe) { return pipe.id; })
                ));
            } catch (e) {}
        }
        function remove(id) {
            basket = basket.filter(function (pipe) { return String(pipe.id) !== String(id); });
            render();
        }
        function add(pipe) {
            known[String(pipe.id)] = pipe;
            if (pipe.selectable === false) {
                showMessage('<strong>' + esc(pipe.code) + '</strong> — ' + esc(pipe.reason || pipe.status), 'warning');
                return;
            }
            if (!basket.some(function (item) { return String(item.id) === String(pipe.id); })) basket.push(pipe);
            render();
        }
        function render() {
            root.querySelectorAll('[data-selection-hidden]').forEach(function (node) { node.remove(); });
            basketNode.innerHTML = '';
            basket.forEach(function (pipe) {
                var item = document.createElement('div');
                item.className = 'list-group-item d-flex justify-content-between align-items-start gap-2';
                item.innerHTML = '<div><strong>' + esc(pipe.code) + '</strong>' +
                    '<div class="small text-muted">' + esc([pipe.order, pipe.dn, pipe.pipe_class, pipe.status].filter(Boolean).join(' · ')) + '</div>' +
                    (pipe.reason ? '<div class="small text-muted">' + esc(pipe.reason) + '</div>' : '') + '</div>' +
                    '<button type="button" class="btn btn-sm btn-outline-secondary" data-remove-id="' + esc(pipe.id) + '">' +
                    '<span aria-hidden="true">×</span><span class="visually-hidden">' + t('إزالة', 'Remove') + '</span></button>';
                basketNode.appendChild(item);
                if (targetForm) {
                    var hidden = document.createElement('input');
                    hidden.type = 'hidden';
                    hidden.name = fieldName;
                    hidden.value = pipe.id;
                    hidden.dataset.selectionHidden = 'true';
                    targetForm.appendChild(hidden);
                }
            });
            if (!basket.length) {
                var empty = document.createElement('div');
                empty.className = 'list-group-item text-muted';
                empty.textContent = t('لا توجد مواسير مختارة.', 'No pipes selected.');
                basketNode.appendChild(empty);
            }
            countNode.textContent = basket.length;
            root.querySelectorAll('[data-selection-check]').forEach(function (box) {
                box.checked = basket.some(function (pipe) { return String(pipe.id) === box.value; });
            });
            persist();
        }

        root.querySelectorAll('[data-selection-check]').forEach(function (box) {
            box.removeAttribute('name');
            box.addEventListener('change', function () {
                if (box.checked) add(known[box.value]); else remove(box.value);
            });
        });
        root.querySelector('[data-select-visible]').addEventListener('click', function () {
            root.querySelectorAll('[data-selection-check]:not(:disabled)').forEach(function (box) {
                var pipe = known[box.value];
                if (pipe && !basket.some(function (item) { return String(item.id) === box.value; })) basket.push(pipe);
            });
            render();
        });
        root.querySelector('[data-selection-clear]').addEventListener('click', function () {
            basket = [];
            showMessage('', 'info');
            render();
        });
        basketNode.addEventListener('click', function (event) {
            var button = event.target.closest('[data-remove-id]');
            if (button) remove(button.dataset.removeId);
        });

        function showCandidates(pipes) {
            candidatesNode.innerHTML = '';
            pipes.forEach(function (pipe) {
                known[String(pipe.id)] = pipe;
                var button = document.createElement('button');
                button.type = 'button';
                button.className = 'list-group-item list-group-item-action';
                button.innerHTML = '<strong>' + esc(pipe.code || pipe.pipe_code || pipe.no_code) + '</strong>' +
                    '<span class="small text-muted ms-2">' + esc([pipe.order || pipe.order_number, pipe.dn, pipe.pipe_class, pipe.status].filter(Boolean).join(' · ')) + '</span>' +
                    (pipe.reason ? '<div class="small text-danger">' + esc(pipe.reason) + '</div>' : '');
                button.addEventListener('click', function () { add(pipe); candidatesNode.classList.add('d-none'); });
                candidatesNode.appendChild(button);
            });
            candidatesNode.classList.toggle('d-none', pipes.length === 0);
        }

        var lookupForm = root.querySelector('[data-selection-lookup-form]');
        var term = root.querySelector('[data-selection-term]');
        if (lookupForm && window.fetch) {
            lookupForm.addEventListener('submit', function (event) {
                var value = (term.value || '').trim();
                if (!value) return;
                event.preventDefault();
                showMessage(t('جارٍ البحث…', 'Looking up…'), 'secondary');
                fetch(lookupUrl + (lookupUrl.indexOf('?') === -1 ? '?' : '&') + 'q=' + encodeURIComponent(value) + '&barcode=' + encodeURIComponent(value), {
                    credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}
                }).then(function (response) {
                    if (!response.ok) throw new Error(String(response.status));
                    return response.json();
                }).then(function (data) {
                    var found = data.pipe ? [data.pipe] : (data.pipes || data.matches || []);
                    if (data.ok && data.pipe) {
                        add(data.pipe);
                        term.value = '';
                    } else if (found.length === 1) {
                        add(found[0]);
                        term.value = '';
                    } else if (found.length) {
                        showMessage('', 'info');
                        showCandidates(found);
                    } else {
                        showMessage(t('لم يتم العثور على ماسورة.', 'No matching pipe found.'), 'warning');
                    }
                }).catch(function () {
                    showMessage(t('تعذّر البحث. حاول مرة أخرى.', 'Lookup failed. Please try again.'), 'danger');
                });
            });
        }

        render();
        if (pendingReconcile) {
            var unresolved = storedIds.filter(function (id) { return !known[String(id)]; });
            var query = unresolved.map(function (id) {
                return 'pipe_ids=' + encodeURIComponent(id);
            }).join('&');
            fetch(reconcileUrl + (reconcileUrl.indexOf('?') === -1 ? '?' : '&') + query, {
                credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}
            }).then(function (response) {
                if (!response.ok) throw new Error(String(response.status));
                return response.json();
            }).then(function (data) {
                (data.pipes || []).forEach(function (pipe) {
                    known[String(pipe.id)] = pipe;
                    if (pipe.selectable !== false) basket.push(pipe);
                });
                basket = unique(basket);
                pendingReconcile = false;
                render();
            }).catch(function () {
                pendingReconcile = false;
                showMessage(t(
                    'تعذّر تحديث حالة القائمة المحفوظة؛ لم تتم إضافة العناصر غير المؤكدة.',
                    'Saved basket status could not be refreshed; unverified items were not selected.'
                ), 'warning');
            });
        }
    }

    function start() {
        document.querySelectorAll('[data-pipe-selection]').forEach(boot);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
