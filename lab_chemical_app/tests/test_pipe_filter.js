/**
 * TDD test for the pipe filter predicate logic (Task 1).
 * Tests the pure matchesPipeFilter(opt, searchTerm, diameterFilter, ladleFilter)
 * function that will be extracted in the mechanical form template.
 *
 * Run: node tests/test_pipe_filter.js
 */

'use strict';

// --- The function under test (inline, matching what will live in the template) ---
function matchesPipeFilter(opt, searchTerm, diameterFilter, ladleFilter) {
    // Empty-value placeholder option always passes through
    if (opt.value === '') return true;

    // Text search (substring, case-insensitive)
    if (searchTerm && !opt.text.toLowerCase().includes(searchTerm.toLowerCase())) {
        return false;
    }

    // Diameter filter (exact match after parseInt normalization)
    if (diameterFilter) {
        const optDN = parseInt(opt.diameter, 10);
        const filterDN = parseInt(diameterFilter, 10);
        if (isNaN(optDN) || optDN !== filterDN) return false;
    }

    // Ladle filter (substring, case-insensitive)
    if (ladleFilter && !opt.ladle.toLowerCase().includes(ladleFilter.toLowerCase())) {
        return false;
    }

    return true;
}

// --- Test helpers ---
let passed = 0;
let failed = 0;

function assert(condition, description) {
    if (condition) {
        console.log('  PASS:', description);
        passed++;
    } else {
        console.error('  FAIL:', description);
        failed++;
    }
}

// --- Test data ---
const emptyOpt  = { value: '', text: '-- Select --', diameter: '', ladle: '', pipecode: '' };
const pipe300   = { value: '1', text: 'N8739-4713-1', diameter: '300', ladle: '4713012025', pipecode: 'N8739-4713-1' };
const pipe600   = { value: '2', text: 'N8740-5001-2', diameter: '600', ladle: '5001022025', pipecode: 'N8740-5001-2' };
const pipe300b  = { value: '3', text: 'N8741-4713-3', diameter: '300', ladle: '4713012025', pipecode: 'N8741-4713-3' };
const pipeNoDiam = { value: '4', text: 'N8742-0000-1', diameter: '', ladle: '0000011111', pipecode: 'N8742-0000-1' };

// -----------------------------------------------------------------------
// RED phase: these tests should FAIL before the function exists in the
// template (they're testing the extracted pure function defined above — on
// first run they should PASS to confirm the logic is correct, but the
// function itself does not yet exist in the template HTML).
// -----------------------------------------------------------------------

console.log('\n=== Task 1: pipe filter predicate ===\n');

console.log('--- empty placeholder always passes ---');
assert(matchesPipeFilter(emptyOpt, 'N8739', '300', '4713'), 'empty option passes through ALL filters');
assert(matchesPipeFilter(emptyOpt, '', '', ''), 'empty option passes through with no filters');

console.log('\n--- no filters: all pipes visible ---');
assert(matchesPipeFilter(pipe300, '', '', ''), 'pipe300 visible when no filters');
assert(matchesPipeFilter(pipe600, '', '', ''), 'pipe600 visible when no filters');

console.log('\n--- text search filter ---');
assert(matchesPipeFilter(pipe300, 'N8739', '', ''), 'pipe300 matches "N8739" search');
assert(!matchesPipeFilter(pipe600, 'N8739', '', ''), 'pipe600 does NOT match "N8739" search');
assert(matchesPipeFilter(pipe300, 'n8739', '', ''), 'search is case-insensitive');

console.log('\n--- diameter filter ---');
assert(matchesPipeFilter(pipe300, '', '300', ''), 'pipe300 matches diameter 300');
assert(!matchesPipeFilter(pipe600, '', '300', ''), 'pipe600 does NOT match diameter 300');
assert(matchesPipeFilter(pipe600, '', '600', ''), 'pipe600 matches diameter 600');

console.log('\n--- ladle filter ---');
assert(matchesPipeFilter(pipe300, '', '', '4713'), 'pipe300 matches ladle "4713" substring');
assert(!matchesPipeFilter(pipe600, '', '', '4713'), 'pipe600 does NOT match ladle "4713"');
assert(matchesPipeFilter(pipe300b, '', '', '4713'), 'pipe300b matches ladle "4713" substring');

console.log('\n--- AND combination: all three filters ---');
assert(matchesPipeFilter(pipe300, 'N8739', '300', '4713'), 'pipe300 matches text+diameter+ladle');
assert(!matchesPipeFilter(pipe300b, 'N8739', '300', '4713'), 'pipe300b fails text filter in AND combo');
assert(!matchesPipeFilter(pipe600, 'N8740', '300', ''), 'pipe600 fails diameter filter in AND combo');

console.log('\n--- diameter normalization: "300.0" string coerces to 300 ---');
const pipe300Float = { value: '5', text: 'N9000-4713-1', diameter: '300.0', ladle: '4713', pipecode: 'N9000-4713-1' };
assert(matchesPipeFilter(pipe300Float, '', '300', ''), 'diameter "300.0" matches filter "300" via parseInt');

console.log('\n--- missing diameter: passes diameter filter only if no filter set ---');
assert(matchesPipeFilter(pipeNoDiam, '', '', ''), 'pipe with empty diameter passes when no diameter filter');
assert(!matchesPipeFilter(pipeNoDiam, '', '300', ''), 'pipe with empty diameter fails diameter filter');

console.log('\n=== Task 2: diameter auto-fill normalization ===\n');

// Test the parseInt normalization helper used in the change handler.
// This proves that "300.0" from a data-attr gets matched against option values.
function normalizeDiameter(val) {
    const n = parseInt(val, 10);
    return isNaN(n) ? null : String(n);
}

assert(normalizeDiameter('300') === '300', 'normalizeDiameter: integer string unchanged');
assert(normalizeDiameter('300.0') === '300', 'normalizeDiameter: float string normalized');
assert(normalizeDiameter('DN300') === null, 'normalizeDiameter: non-numeric returns null');
assert(normalizeDiameter('') === null, 'normalizeDiameter: empty string returns null');
assert(normalizeDiameter(null) === null, 'normalizeDiameter: null returns null');

// -----------------------------------------------------------------------
console.log('\n=== Results ===');
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
if (failed > 0) {
    process.exit(1);
}
