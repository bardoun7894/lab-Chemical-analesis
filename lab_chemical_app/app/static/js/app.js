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
