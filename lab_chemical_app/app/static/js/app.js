/**
 * Lab Chemical Analysis App - JavaScript Helpers
 */

// Initialize Bootstrap tooltips and popovers
document.addEventListener('DOMContentLoaded', function() {
    // Initialize tooltips
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function(tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });

    // Initialize popovers
    var popoverTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="popover"]'));
    popoverTriggerList.map(function(popoverTriggerEl) {
        return new bootstrap.Popover(popoverTriggerEl);
    });

    // Auto-dismiss alerts after 5 seconds
    setTimeout(function() {
        var alerts = document.querySelectorAll('.alert:not(.alert-permanent)');
        alerts.forEach(function(alert) {
            var bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        });
    }, 5000);
});

/**
 * Format number with specified decimal places
 */
function formatNumber(value, decimals = 4) {
    if (value === null || value === undefined || value === '') {
        return '-';
    }
    return parseFloat(value).toFixed(decimals);
}

/**
 * Validate element value against specification
 */
function validateElementValue(value, minValue, maxValue) {
    if (value === null || value === undefined || value === '') {
        return { valid: true, status: 'empty' };
    }

    const numValue = parseFloat(value);

    if (minValue !== null && numValue < minValue) {
        return { valid: false, status: 'below', message: `Below minimum (${minValue})` };
    }

    if (maxValue !== null && numValue > maxValue) {
        return { valid: false, status: 'above', message: `Above maximum (${maxValue})` };
    }

    return { valid: true, status: 'ok' };
}

/**
 * Calculate Carbon Equivalent
 */
function calculateCE(carbon, silicon) {
    if (!carbon || !silicon) return null;
    return parseFloat(carbon) + (parseFloat(silicon) / 3);
}

/**
 * Calculate Manganese Equivalent
 */
function calculateMnE(manganese, sulfur) {
    if (!manganese) return null;
    return parseFloat(manganese) - (1.7 * (parseFloat(sulfur) || 0));
}

/**
 * Calculate Magnesium Equivalent
 */
function calculateMgE(magnesium, sulfur) {
    if (!magnesium) return null;
    return parseFloat(magnesium) - (0.76 * (parseFloat(sulfur) || 0));
}

/**
 * Generate Ladle ID from ladle number and date
 */
function generateLadleId(ladleNo, dateStr) {
    if (!ladleNo || !dateStr) return '';

    const date = new Date(dateStr);
    const day = String(date.getDate()).padStart(2, '0');
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const year = date.getFullYear();

    return `${ladleNo}${day}${month}${year}`;
}

/**
 * Parse Ladle ID to components
 */
function parseLadleId(ladleId) {
    if (!ladleId || ladleId.length < 9) return null;

    const year = parseInt(ladleId.slice(-4));
    const month = parseInt(ladleId.slice(-6, -4));
    const day = parseInt(ladleId.slice(-8, -6));
    const ladleNo = parseInt(ladleId.slice(0, -8));

    return { ladleNo, day, month, year };
}

/**
 * Confirm delete action
 */
function confirmDelete(message, formAction) {
    if (confirm(message)) {
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = formAction;

        const csrfInput = document.createElement('input');
        csrfInput.type = 'hidden';
        csrfInput.name = 'csrf_token';
        csrfInput.value = document.querySelector('meta[name="csrf-token"]')?.content ||
                          document.querySelector('input[name="csrf_token"]')?.value || '';

        form.appendChild(csrfInput);
        document.body.appendChild(form);
        form.submit();
    }
}

/**
 * Show loading spinner on button
 */
function showButtonLoading(button) {
    button.disabled = true;
    const originalText = button.innerHTML;
    button.dataset.originalText = originalText;
    button.innerHTML = '<span class="loading-spinner me-2"></span>Loading...';
    return originalText;
}

/**
 * Hide loading spinner on button
 */
function hideButtonLoading(button) {
    button.disabled = false;
    button.innerHTML = button.dataset.originalText || 'Submit';
}

/**
 * Format date for display
 */
function formatDate(dateStr, locale = 'en-US') {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    return date.toLocaleDateString(locale, {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
    });
}

/**
 * Export table to CSV
 */
function exportTableToCSV(tableId, filename) {
    const table = document.getElementById(tableId);
    if (!table) return;

    let csv = [];
    const rows = table.querySelectorAll('tr');

    rows.forEach(row => {
        const cols = row.querySelectorAll('td, th');
        const rowData = [];
        cols.forEach(col => {
            let text = col.innerText.replace(/"/g, '""');
            rowData.push(`"${text}"`);
        });
        csv.push(rowData.join(','));
    });

    const csvContent = csv.join('\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename || 'export.csv';
    link.click();
}

/**
 * Debounce function for search inputs
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * Toggle table row selection
 */
function toggleRowSelection(checkbox, rowClass = 'table-primary') {
    const row = checkbox.closest('tr');
    if (checkbox.checked) {
        row.classList.add(rowClass);
    } else {
        row.classList.remove(rowClass);
    }
}

/**
 * Select/Deselect all checkboxes
 */
function toggleAllCheckboxes(masterCheckbox, checkboxName) {
    const checkboxes = document.querySelectorAll(`input[name="${checkboxName}"]`);
    checkboxes.forEach(cb => {
        cb.checked = masterCheckbox.checked;
        toggleRowSelection(cb);
    });
}

// Global error handler for fetch requests
window.handleFetchError = function(error) {
    console.error('Fetch error:', error);
    alert('An error occurred. Please try again.');
};


/* ============================================================
 * Table Tools — field picker + row select + print + export
 * ------------------------------------------------------------
 * <table data-table-tools="ID"> + toolbar <div data-table-tools-for="ID">
 * (components/table_tools.html). The picker is populated from a curated
 * server registry (input.tt-field checkboxes). A checked field:
 *   - is included in the .xlsx export and the print view (?format=print);
 *   - if its key matches a th[data-col] on the page, also shows/hides that
 *     column on screen (extra DB fields have no on-screen column).
 * Print opens the print view (data-print-view) so ANY chosen field prints;
 * pages without a registry fall back to window.print() of the live table.
 * Select-all / None + per-field choices persist per-table in localStorage.
 * ============================================================ */
(function () {
    function fields(toolbar) {
        return Array.prototype.slice.call(toolbar.querySelectorAll('input.tt-field'));
    }

    function initTableTools(table) {
        var id = table.getAttribute('data-table-tools');
        var toolbar = document.querySelector('[data-table-tools-for="' + id + '"]');
        if (!toolbar) return;

        var panel = toolbar.querySelector('[data-col-panel]');
        var exportLink = toolbar.querySelector('[data-export-link]');
        var printView = toolbar.getAttribute('data-print-view');   // export base URL or null
        var rowSelect = table.hasAttribute('data-row-select');
        var storageKey = 'tabletools:' + id + ':hidden';

        var hidden = {};
        try {
            JSON.parse(localStorage.getItem(storageKey) || '[]')
                .forEach(function (k) { hidden[k] = true; });
        } catch (e) { /* ignore corrupt state */ }

        function colCells(key) { return table.querySelectorAll('[data-col="' + key + '"]'); }
        function applyColumn(key, show) {
            colCells(key).forEach(function (c) { c.classList.toggle('tt-col-hidden', !show); });
        }

        var cbs = fields(toolbar);

        // Legacy fallback: no registry checkboxes -> build them from th[data-col]
        // (localized labels), for any page that opts in without a registry.
        if (cbs.length === 0 && panel) {
            Array.prototype.slice.call(table.querySelectorAll('thead th[data-col]'))
                .forEach(function (th) {
                    var key = th.getAttribute('data-col');
                    var label = (th.getAttribute('data-col-label') || th.textContent || key).trim();
                    var item = document.createElement('label');
                    item.className = 'dropdown-item d-flex align-items-center gap-2 mb-0';
                    item.style.cursor = 'pointer';
                    item.addEventListener('click', function (e) { e.stopPropagation(); });
                    var cb = document.createElement('input');
                    cb.type = 'checkbox';
                    cb.className = 'form-check-input mt-0 tt-field';
                    cb.value = key;
                    var initHidden = th.classList.contains('tt-col-hidden');
                    cb.checked = hidden.hasOwnProperty(key) ? !hidden[key] : !initHidden;
                    item.appendChild(cb);
                    item.appendChild(document.createTextNode(' ' + label));
                    panel.appendChild(item);
                });
            cbs = fields(toolbar);
        } else {
            // Registry checkboxes: server sets the default checked state; restore
            // any persisted override, and localize labels that map to a column.
            cbs.forEach(function (cb) {
                if (hidden.hasOwnProperty(cb.value)) cb.checked = !hidden[cb.value];
                var th = table.querySelector('thead th[data-col="' + cb.value + '"]');
                if (th) {
                    var span = cb.parentNode.querySelector('span');
                    if (span) span.textContent = (th.getAttribute('data-col-label') || th.textContent || span.textContent).trim();
                }
            });
        }

        // Apply initial on-screen visibility for columns that exist in the table.
        cbs.forEach(function (cb) { applyColumn(cb.value, cb.checked); });

        function checkedKeys() { return cbs.filter(function (c) { return c.checked; }).map(function (c) { return c.value; }); }
        function persist() {
            var hk = cbs.filter(function (c) { return !c.checked; }).map(function (c) { return c.value; });
            try { localStorage.setItem(storageKey, JSON.stringify(hk)); } catch (e) {}
        }
        function selectedIds() {
            return Array.prototype.slice.call(table.querySelectorAll('tbody .tt-row-check:checked'))
                .map(function (c) { return c.value; });
        }
        function buildParams() {
            var params = new URLSearchParams(window.location.search);
            params.delete('cols'); params.delete('ids'); params.delete('page'); params.delete('format');
            var keys = checkedKeys();
            if (keys.length && keys.length < cbs.length) params.set('cols', keys.join(','));
            var ids = rowSelect ? selectedIds() : [];
            if (ids.length) params.set('ids', ids.join(','));
            return params;
        }
        function syncExport() {
            if (!exportLink) return;
            var base = exportLink.getAttribute('data-export-base') || '';
            var qs = buildParams().toString();
            exportLink.setAttribute('href', base + (qs ? '?' + qs : ''));
        }

        cbs.forEach(function (cb) {
            cb.addEventListener('change', function () { applyColumn(cb.value, cb.checked); persist(); syncExport(); });
        });

        var selAll = toolbar.querySelector('[data-select-all]');
        var selNone = toolbar.querySelector('[data-select-none]');
        if (selAll) selAll.addEventListener('click', function () {
            cbs.forEach(function (c) { c.checked = true; applyColumn(c.value, true); }); persist(); syncExport();
        });
        if (selNone) selNone.addEventListener('click', function () {
            cbs.forEach(function (c) { c.checked = false; applyColumn(c.value, false); }); persist(); syncExport();
        });

        if (rowSelect) buildRowSelect(table);
        table.addEventListener('change', function (e) {
            if (e.target.classList.contains('tt-row-check')) syncExport();
            if (e.target.classList.contains('tt-row-all')) {
                table.querySelectorAll('tbody .tt-row-check').forEach(function (c) { c.checked = e.target.checked; });
                syncExport();
            }
        });

        var printBtn = toolbar.querySelector('[data-print-btn]');
        if (printBtn) {
            printBtn.addEventListener('click', function () {
                if (printView) {
                    var params = buildParams();
                    params.set('format', 'print');
                    window.open(printView + '?' + params.toString(), '_blank');
                    return;
                }
                // Fallback: print the on-screen table (hide unselected rows).
                var ids = rowSelect ? selectedIds() : [];
                var rows = Array.prototype.slice.call(table.querySelectorAll('tbody tr[data-row-id]'));
                if (ids.length) {
                    rows.forEach(function (tr) {
                        if (ids.indexOf(tr.getAttribute('data-row-id')) === -1) tr.classList.add('d-print-hide');
                    });
                }
                var cleanup = function () {
                    rows.forEach(function (tr) { tr.classList.remove('d-print-hide'); });
                    window.removeEventListener('afterprint', cleanup);
                };
                window.addEventListener('afterprint', cleanup);
                window.print();
            });
        }

        syncExport();
    }

    // Inject a leading select-all header cell + per-row checkbox cells.
    // Group/summary rows (a single cell spanning the table) get their colspan
    // widened by one instead of a checkbox, so alignment is preserved.
    function buildRowSelect(table) {
        var headRow = table.querySelector('thead tr');
        if (headRow && !headRow.querySelector('.tt-row-all-cell')) {
            var th = document.createElement('th');
            th.className = 'tt-row-all-cell d-print-hide';
            var all = document.createElement('input');
            all.type = 'checkbox';
            all.className = 'form-check-input tt-row-all';
            all.setAttribute('title', 'Select all');
            th.appendChild(all);
            headRow.insertBefore(th, headRow.firstChild);
        }
        Array.prototype.slice.call(table.querySelectorAll('tbody tr')).forEach(function (tr) {
            var spanCell = tr.querySelector('td[colspan], th[colspan]');
            if (spanCell) {
                spanCell.colSpan = (parseInt(spanCell.getAttribute('colspan'), 10) || 1) + 1;
                return;
            }
            var td = document.createElement('td');
            td.className = 'tt-row-cell d-print-hide';
            var rowId = tr.getAttribute('data-row-id');
            if (rowId) {
                var cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.className = 'form-check-input tt-row-check';
                cb.value = rowId;
                td.appendChild(cb);
            }
            tr.insertBefore(td, tr.firstChild);
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('table[data-table-tools]').forEach(initTableTools);
    });
})();


/* ============================================================
 * Table Enhance — click-to-sort headers + client-side pagination
 * ------------------------------------------------------------
 * Opt in per table (full dataset in the DOM only — NEVER on a
 * server-paginated table, or it would sort/paginate one page):
 *   <table data-enhance>                     sort + paginate (15/page)
 *   <table data-enhance data-page-size="25"> custom page size
 *   <table data-enhance="sort">              sort only
 *   <table data-enhance="paginate">          paginate only
 * Per-header opt-out: <th data-no-sort>. A column whose sample cell
 * holds a button / .btn link / form (e.g. Actions) is auto non-sortable.
 * Rows with a colspan cell (group headers, summary, empty-state) are
 * never sorted, paginated, or counted — they always render.
 * ============================================================ */
(function () {
    var LOCALE = document.documentElement.getAttribute('lang') || 'en';
    var IS_AR = LOCALE.indexOf('ar') === 0;
    function t(ar, en) { return IS_AR ? ar : en; }

    function dataRows(tbody) {
        return Array.prototype.slice.call(tbody.rows).filter(function (tr) {
            return !tr.querySelector('td[colspan], th[colspan]');
        });
    }
    function cellText(tr, idx) {
        var c = tr.cells[idx];
        return c ? c.textContent.replace(/\s+/g, ' ').trim() : '';
    }
    // Type-aware key: {empty} | {n} number | {d} date | {s} string.
    function parseVal(s) {
        if (s === '' || s === '-' || s === '—') return { empty: true };
        var num = s.replace(/[,%\s]/g, '');
        if (/^[-+]?\d*\.?\d+$/.test(num)) return { n: parseFloat(num) };
        if (/\d{2,4}[-/]\d{1,2}[-/]\d{1,4}/.test(s)) {
            var d = Date.parse(s);
            if (!isNaN(d)) return { d: d };
        }
        return { s: s };
    }
    function cmpNonEmpty(a, b) {
        if ('n' in a && 'n' in b) return a.n - b.n;
        if ('d' in a && 'd' in b) return a.d - b.d;
        var as = 's' in a ? a.s : String(a.n != null ? a.n : a.d);
        var bs = 's' in b ? b.s : String(b.n != null ? b.n : b.d);
        return as.localeCompare(bs, LOCALE, { numeric: true, sensitivity: 'base' });
    }

    function initEnhance(table) {
        var mode = (table.getAttribute('data-enhance') || '').toLowerCase();
        var doSort = mode === '' || mode.indexOf('sort') !== -1;
        var doPage = mode === '' || mode.indexOf('paginate') !== -1;
        var tbody = table.tBodies[0];
        var thead = table.tHead;
        if (!tbody || !thead) return;

        var pageSize = parseInt(table.getAttribute('data-page-size'), 10) || 15;
        var sortIdx = -1, sortDir = 'asc';

        var headers = Array.prototype.slice.call(
            thead.rows[thead.rows.length - 1].cells);

        function render() {
            var rows = dataRows(tbody);
            if (doSort && sortIdx >= 0) {
                var dec = rows.map(function (tr, i) {
                    return { tr: tr, v: parseVal(cellText(tr, sortIdx)), i: i };
                });
                dec.sort(function (x, y) {
                    if (x.v.empty && y.v.empty) return x.i - y.i;
                    if (x.v.empty) return 1;
                    if (y.v.empty) return -1;
                    var c = cmpNonEmpty(x.v, y.v);
                    if (c === 0) return x.i - y.i;
                    return sortDir === 'desc' ? -c : c;
                });
                rows = dec.map(function (d) { return d.tr; });
                rows.forEach(function (tr) { tbody.appendChild(tr); });
            }

            var total = rows.length;
            var pages = doPage ? Math.max(1, Math.ceil(total / pageSize)) : 1;
            if (state.page > pages) state.page = pages;
            var start = doPage ? (state.page - 1) * pageSize : 0;
            var end = doPage ? start + pageSize : total;
            rows.forEach(function (tr, i) {
                tr.classList.toggle('te-page-hidden', doPage && (i < start || i >= end));
            });
            renderPager(total, pages, start, end);
        }

        function renderPager(total, pages, start, end) {
            if (!doPage) return;
            var pager = table.parentNode.querySelector('.te-pager[data-for="' + tid + '"]');
            if (total <= pageSize) { if (pager) pager.remove(); return; }
            if (!pager) {
                pager = document.createElement('nav');
                pager.className = 'te-pager d-print-hide';
                pager.setAttribute('data-for', tid);
                table.parentNode.insertBefore(pager, table.nextSibling);
            }
            var info = t('عرض ', 'Showing ') + (start + 1) + '–' + Math.min(end, total) +
                       t(' من ', ' of ') + total;
            var win = 2, html = '<ul class="pagination pagination-sm mb-0">';
            function item(label, page, disabled, active) {
                return '<li class="page-item' + (disabled ? ' disabled' : '') +
                    (active ? ' active' : '') + '"><a class="page-link" href="#" data-page="' +
                    page + '">' + label + '</a></li>';
            }
            html += item(t('السابق', 'Prev'), state.page - 1, state.page === 1, false);
            for (var p = 1; p <= pages; p++) {
                if (p === 1 || p === pages || Math.abs(p - state.page) <= win) {
                    html += item(p, p, false, p === state.page);
                } else if (p === 2 || p === pages - 1) {
                    html += '<li class="page-item disabled"><span class="page-link">…</span></li>';
                }
            }
            html += item(t('التالي', 'Next'), state.page + 1, state.page === pages, false);
            html += '</ul>';
            pager.innerHTML = '<span class="te-pager-info">' + info + '</span>' + html;
        }

        var state = { page: 1 };

        // Wire sortable headers.
        if (doSort) {
            headers.forEach(function (th, idx) {
                if (th.hasAttribute('data-no-sort')) return;
                if (th.textContent.trim() === '') return;   // skip label-less headers
                // Auto-skip action columns (buttons / .btn links / forms).
                var sample = dataRows(tbody)[0];
                if (sample && sample.cells[idx]) {
                    var cell = sample.cells[idx];
                    if (cell.querySelector('button, a.btn, form, .btn-group')) return;
                }
                th.classList.add('te-sortable');
                var caret = document.createElement('span');
                caret.className = 'te-caret';
                caret.innerHTML = '↕';
                th.appendChild(caret);
                th.addEventListener('click', function () {
                    if (sortIdx === idx) { sortDir = sortDir === 'asc' ? 'desc' : 'asc'; }
                    else { sortIdx = idx; sortDir = 'asc'; }
                    headers.forEach(function (h) {
                        h.classList.remove('te-sort-asc', 'te-sort-desc');
                        var cc = h.querySelector('.te-caret');
                        if (cc) cc.innerHTML = '↕';
                    });
                    th.classList.add(sortDir === 'asc' ? 'te-sort-asc' : 'te-sort-desc');
                    caret.innerHTML = sortDir === 'asc' ? '↑' : '↓';
                    state.page = 1;
                    render();
                });
            });
        }

        // Pager click delegation.
        var tid = table.id || ('te-' + Math.abs(hashStr(table.innerHTML)).toString(36));
        table.id = table.id || tid;
        table.parentNode.addEventListener('click', function (e) {
            var link = e.target.closest && e.target.closest('.te-pager[data-for="' + tid + '"] a[data-page]');
            if (!link) return;
            e.preventDefault();
            var p = parseInt(link.getAttribute('data-page'), 10);
            if (isNaN(p) || p < 1) return;
            state.page = p;
            render();
        });

        render();
    }

    function hashStr(s) {
        var h = 0;
        for (var i = 0; i < s.length; i++) { h = ((h << 5) - h + s.charCodeAt(i)) | 0; }
        return h;
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('table[data-enhance]').forEach(initEnhance);
    });
})();
