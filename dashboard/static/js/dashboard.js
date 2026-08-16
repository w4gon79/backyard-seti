/* Backyard SETI Dashboard - Frontend Logic */

// ─── State ────────────────────────────────────────────────────────────
let allHits = [];
let rejectionCandidates = [];
let statsInterval = null;
let logInterval = null;
let celestialTargets = {};  // Map of target name -> {ra, dec} driven by file selection
let selectedFiles = new Set();
let localDataCache = {};
let fileHeaderCache = {};
let scansList = [];       // Array of scan metadata objects
let currentScanId = null; // Currently selected scan_id

function scanEpochLabel(s) {
    // Scan ids created after 2026-08-15 embed the epoch: TARGET_MJD_DATE_TIME.
    // Older ids lack it; recover the BL MJD from mjd_start/bary_mjd if known.
    if (/^[A-Za-z0-9]+_\d{5}_\d{4}-\d{2}-\d{2}/.test(s.scan_id || '')) return null;
    var mj = s.mjd_start || s.bary_mjd;
    if (mj && isFinite(mj) && mj > 0) return String(Math.round(mj));
    return null;
}

function scanLabel(s) {
    var id = s.scan_id || '';
    var ep = scanEpochLabel(s);
    if (!ep) return id;
    var m = /^([A-Za-z0-9]+)_(\d{4}-\d{2}-\d{2}_\d{4})/.exec(id);
    if (!m) return id;
    return m[1] + '_' + ep + '_' + m[2];
}
let scanStateActive = false; // Tracks whether a scan is currently running

// DB-backed hit pagination state
let allHitsTotal = 0;
let allHitsScanId = null;
let allHitsOffset = 0;
let allHitsLimit = 500;

// Results pagination state
let resultsPage = 0;
let resultsPageSize = 100;
let resultsSort = { col: 'snr', dir: 'desc' };  // default: SNR descending

// BL API search state
var blResults = [];
var blPage = 0;
var blPageSize = 50;
var blFilterRes = 'all';
var blFilterType = 'all';
var blFilterFileType = 'all';

// ─── Init ─────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initSkyMap();
    loadLocalData();
    loadScansList();  // Load scan history, then load results for most recent

    document.getElementById('btn-search-bl').onclick = searchBL;
document.getElementById('btn-blcatalog').onclick = blCatalogBrowse;
    document.getElementById('btn-search-local').onclick = loadLocalData;
    document.getElementById('btn-start-scan').onclick = startScan;
    document.getElementById('btn-resume-scan').onclick = resumeScan;
    document.getElementById('btn-stop-scan').onclick = stopScan;
    document.getElementById('btn-refresh').onclick = () => { loadResults(); loadStats(); loadScansList(); };
    document.getElementById('btn-delete-scan').onclick = deleteCurrentScan;
document.getElementById('btn-archive-scan').onclick = archiveCurrentScan;
    document.getElementById('btn-select-all').onclick = selectAllFiles;
    document.getElementById('btn-select-none').onclick = selectNoneFiles;
    document.getElementById('btn-full-band').onclick = autoFillBandRange;
    document.getElementById('target-input').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') searchBL();
    });
    document.getElementById('results-filter').onchange = applyFilters;
    // Fix 4: Debounce filter inputs (300ms delay) instead of re-sorting 30k hits on every keystroke
    var _filterDebounce = null;
    function debouncedFilter() {
        clearTimeout(_filterDebounce);
        _filterDebounce = setTimeout(applyFilters, 300);
    }
    document.getElementById('results-snr-min').oninput = debouncedFilter;
    document.getElementById('results-drift-min').oninput = debouncedFilter;
    document.getElementById('results-drift-max').oninput = debouncedFilter;
    document.getElementById('results-freq-min').oninput = debouncedFilter;
    document.getElementById('results-freq-max').oninput = debouncedFilter;
    document.getElementById('btn-run-rejection').onclick = runRejection;
    document.getElementById('scan-selector').onchange = onScanSelected;

    // Barycentric init
    loadBarycentricTargets();
    updateBaryScanCheckboxes();
    loadCrossEpochHistory();

    logInterval = setInterval(pollScanStatus, 3000);
    pollScanStatus();  // Fire immediately so button states sync on page load
    setInterval(pollDownloadStatus, 10000);
setInterval(auditPollStatus, 5000);

    // Waterfall modal close handlers
    document.getElementById('waterfall-close').onclick = closeWaterfallModal;
    document.getElementById('waterfall-modal').addEventListener('click', function(e) {
        if (e.target === this) closeWaterfallModal();
    });
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') closeWaterfallModal();
    });
});

// ─── Sky Map (d3-celestial) ───────────────────────────────────────────

function initSkyMap() {
    var config = {
        width: 0,
        location: false,
        interactive: true,
        controls: true,
        projection: "aitoff",
        transform: "equatorial",
        datapath: "/static/data/",
        stars: {
            show: true, limit: 6, colors: true,
            style: { fill: "#ffffff", opacity: 1 },
            names: false, proper: false, desig: false,
            namelimit: 2.5,
            namestyle: { fill: "#ddddbb", font: "11px Georgia, Times, 'Times Roman', serif", align: "left", baseline: "top" },
            size: 4, data: "stars.6.json"
        },
        dsos: {
            show: false, limit: 6, names: true, desig: true, namelimit: 4,
            namestyle: { fill: "#cccccc", font: "11px Helvetica, Arial, serif", align: "left", baseline: "top" },
            data: "dsos.bright.json"
        },
        constellations: {
            show: true, names: true, desig: true,
            namestyle: { fill: "#9999cc", font: "12px Helvetica, Arial, sans-serif", align: "center", baseline: "middle" },
            lines: true,
            linestyle: { stroke: "#3a3a5a", width: 1, opacity: 0.6 },
            bounds: false,
            boundstyle: { stroke: "#cccc00", width: 0.5, opacity: 0.8, dash: [2, 4] }
        },
        mw: { show: true, style: { fill: "#ffffff", opacity: 0.15 } },
        lines: {
            graticule: { show: true, stroke: "#444466", width: 0.8, opacity: 0.6 },
            equatorial: { show: true, stroke: "#6688aa", width: 1.3, opacity: 0.8 },
            ecliptic: { show: true, stroke: "#66aa66", width: 1.3, opacity: 0.6 },
            galactic: { show: false, stroke: "#cc6666", width: 1.3, opacity: 0.7 },
            supergalactic: { show: false, stroke: "#cc66cc", width: 1.3, opacity: 0.7 }
        },
        background: { fill: "#0a0a1a", stroke: "#1e3a5f", opacity: 1, width: 1 },
        horizon: { show: false },
        container: "celestial-map",
        adapt: true,
        zoom: false,
    };

    // Register multi-target marker renderer BEFORE display
    Celestial.add({
        type: "line",
        callback: function(error, json) {
            if (error) return console.warn(error);
        },
        redraw: function() {
            var ctx = Celestial.context;
            if (!ctx) return;

            // Draw all targets currently in celestialTargets
            for (var name in celestialTargets) {
                if (!celestialTargets.hasOwnProperty(name)) continue;
                var t = celestialTargets[name];
                var colDot = t.color || '#ff4444';
                var colRing = t.color || '#ff6666';
                var colLabel = t.color || '#ff8888';

                var pt = null;
                try { pt = Celestial.mapProjection([t.ra * 15, t.dec]); } catch(e) { continue; }
                if (!pt) continue;

                var visible = false;
                try { visible = Celestial.clip([t.ra * 15, t.dec]); } catch(e) { visible = true; }
                if (!visible) continue;

                // Crosshair ring
                ctx.strokeStyle = colRing;
                ctx.lineWidth = 1.5;
                ctx.globalAlpha = 0.7;
                ctx.beginPath();
                ctx.arc(pt[0], pt[1], 12, 0, 2 * Math.PI);
                ctx.stroke();

                // Solid dot
                ctx.globalAlpha = 1.0;
                ctx.fillStyle = colDot;
                ctx.strokeStyle = "#ffffff";
                ctx.lineWidth = 1.5;
                ctx.beginPath();
                ctx.arc(pt[0], pt[1], 4, 0, 2 * Math.PI);
                ctx.fill();
                ctx.stroke();

                // Crosshair lines
                ctx.strokeStyle = colRing;
                ctx.lineWidth = 1.5;
                ctx.globalAlpha = 0.7;
                ctx.beginPath();
                ctx.moveTo(pt[0] - 22, pt[1]); ctx.lineTo(pt[0] - 14, pt[1]);
                ctx.moveTo(pt[0] + 14, pt[1]); ctx.lineTo(pt[0] + 22, pt[1]);
                ctx.moveTo(pt[0], pt[1] - 22); ctx.lineTo(pt[0], pt[1] - 14);
                ctx.moveTo(pt[0], pt[1] + 14); ctx.lineTo(pt[0], pt[1] + 22);
                ctx.stroke();

                // Label
                ctx.globalAlpha = 1.0;
                ctx.fillStyle = colLabel;
                ctx.font = "bold 12px sans-serif";
                ctx.fillText(name, pt[0] + 16, pt[1] + 4);
            }
        }
    });

    try {
        Celestial.display(config);
    } catch(e) {
        console.warn("Celestial.display error:", e);
    }

    // Zoom is disabled in config, but celestial still binds a d3 wheel
    // handler whose projection.invert throws "Cannot set properties of
    // undefined" when the map is wheel-scrolled. Strip it.
    try {
        var _cvs = document.querySelectorAll('#celestial-map canvas');
        for (var ci = 0; ci < _cvs.length; ci++) {
            if (_cvs[ci].__onwheel) delete _cvs[ci].__onwheel;
        }
    } catch(e) {}

    // No default markers - sky map starts clean
    document.getElementById('sky-map-info').innerHTML =
        '<p style="color:#546e7a;">Select a file from Target Search to mark its target on the sky map.</p>';
}

// Add a target marker to the sky map
function addTargetMarker(name, raHours, decDeg) {
    celestialTargets[name] = { ra: raHours, dec: decDeg };
    try { Celestial.redraw(); } catch(e) {}
    updateSkyMapInfo();
}

// Remove a target marker from the sky map
function removeTargetMarker(name) {
    delete celestialTargets[name];
    try { Celestial.redraw(); } catch(e) {}
    updateSkyMapInfo();
}

// Clear all target markers
function clearTargetMarkers() {
    celestialTargets = {};
    try { Celestial.redraw(); } catch(e) {}
    updateSkyMapInfo();
}

// Update the info bar below the sky map
function updateSkyMapInfo() {
    var keys = Object.keys(celestialTargets);
    var info = document.getElementById('sky-map-info');
    if (keys.length === 0) {
        info.innerHTML = '<p style="color:#546e7a;">Select a file from Target Search to mark its target on the sky map.</p>';
        return;
    }
    var html = '';
    for (var i = 0; i < keys.length; i++) {
        var t = celestialTargets[keys[i]];
        html += '<p style="margin:2px 0;"><span style="color:#ff6666;">\u25cf</span> <strong>' + keys[i] + '</strong> &nbsp;RA: ' + formatRA(t.ra) + ', Dec: ' + formatDec(t.dec) + (t.status ? ' <span style="color:' + (t.color || '#90a4ae') + ';">[' + t.status + ']</span>' : '') + '</p>';
    }
    info.innerHTML = html;
}

// Also support BL API search click plotting a marker.
// Click behavior toggles: clicking a target already on the map removes
// it. forceAdd (search auto-plot) always adds.
function plotTargetOnMap(name, ra, dec, forceAdd) {
    if (!forceAdd && celestialTargets[name] !== undefined) {
        removeTargetMarker(name);
        return;
    }
    addTargetMarker(name, ra, dec);
}

// --- 3E-lite: registry sky view, colored by scan status -------------

var registrySkyNames = [];
var registrySkyBusy = false;

async function toggleRegistrySky() {
    var btn = document.getElementById('btn-registry-sky');
    if (registrySkyBusy) return;
    if (registrySkyNames.length) {
        // toggle off: remove only the registry-plotted markers
        registrySkyNames.forEach(function (n) { removeTargetMarker(n); });
        registrySkyNames = [];
        if (btn) btn.textContent = 'Registry Sky';
        return;
    }
    registrySkyBusy = true;
    if (btn) btn.textContent = 'Loading...';
    try {
        var resp = await fetch('/api/registry');
        var d = await resp.json();
        var targets = (d.targets || []).filter(function (t) {
            return t.ra_hours != null;
        });
        await Promise.all(targets.map(async function (t) {
            var status = 'no local data';
            var color = '#90a4ae';
            try {
                var r = await fetch('/api/stack/epochs?target=' +
                                    encodeURIComponent(t.name));
                var ed = await r.json();
                var eps = ed.epochs || [];
                var scanned = eps.some(function (e) {
                    return e.scan_status === 'complete'; });
                if (scanned) { status = 'scanned'; color = '#66bb6a'; }
                else if (eps.length > 0) {
                    status = eps.length + ' ep unscanned';
                    color = '#ffb74d';
                }
            } catch (e) { /* status stays no-data */ }
            celestialTargets[t.name] = { ra: t.ra_hours, dec: t.dec_deg,
                                         color: color, status: status };
            registrySkyNames.push(t.name);
        }));
        try { Celestial.redraw(); } catch (e) {}
        updateSkyMapInfo();
        if (btn) btn.textContent = 'Registry Sky (on)';
    } catch (e) {
        alert('Registry sky view failed: ' + e.message);
    }
    registrySkyBusy = false;
}

function formatRA(raHours) {
    var h = Math.floor(raHours);
    var m = Math.floor((raHours - h) * 60);
    var s = ((raHours - h) * 60 - m) * 60;
    return h + 'h ' + m + 'm ' + s.toFixed(1) + 's';
}

function formatDec(decDeg) {
    var sign = decDeg < 0 ? '-' : '+';
    decDeg = Math.abs(decDeg);
    var d = Math.floor(decDeg);
    var m = Math.floor((decDeg - d) * 60);
    var s = ((decDeg - d) * 60 - m) * 60;
    return sign + d + '\u00b0 ' + m + '\u2032 ' + s.toFixed(1) + '\u2033';
}

// ─── Target Search (BL API) ───────────────────────────────────────────

async function searchBL() {
    var target = document.getElementById('target-input').value.trim();
    if (!target) return;

    var resultsDiv = document.getElementById('bl-search-results');
    resultsDiv.innerHTML = '<p style="color:#8ab4f8;">Searching BL API...</p>';

    try {
        var resp = await fetch('/api/blsearch?target=' + encodeURIComponent(target));
        var data = await resp.json();

        if (data.error) {
            resultsDiv.innerHTML = '<p style="color:#ef5350;">Error: ' + data.error + '</p>';
            return;
        }

        if (data.data && data.data.length > 0) {
            blResults = data.data;
            blPage = 0;
            blFilterRes = 'all';
            blFilterType = 'all';
            blFilterFileType = 'all';

            // Plot first result on sky map
            var first = data.data[0];
            var raVal = first.ra;
            var decVal = first.decl;
            if (raVal && decVal) {
                var raHours = typeof raVal === 'number' ? raVal / 15.0 : parseRA(raVal);
                var decDeg = typeof decVal === 'number' ? decVal : parseDec(decVal);
                if (raHours !== null && decDeg !== null) {
                    plotTargetOnMap(target.toUpperCase(), raHours, decDeg, true);
                }
            }
            renderBLResults();
            if (gbtDetect(blResults)) gbtShowBanner(target.toUpperCase());
        } else {
            resultsDiv.innerHTML = '<p style="color:#546e7a;">No observations found.</p>';
        }
    } catch(e) {
        resultsDiv.innerHTML = '<p style="color:#ef5350;">Error: ' + e.message + '</p>';
    }
}

// --- GBT session browser (ABACAD epochs, no ON/OFF files) ----------------

function gbtDetect(list) {
    return (list || []).some(function (f) { return /guppi_/.test(f.url || ''); });
}

function gbtShowBanner(target) {
    var d = document.getElementById('bl-search-results');
    var old = document.getElementById('gbt-session-banner');
    if (old) old.remove();
    var b = document.createElement('div');
    b.id = 'gbt-session-banner';
    b.style.cssText = 'margin:6px 0;padding:6px 8px;border:1px solid #2a3b4d;border-radius:4px;';
    b.innerHTML = '<span style="color:#ffb74d;">GBT data detected</span> ' +
        '<button class="btn-small" onclick="gbtLoadSessions(\'' + target.replace(/'/g, '') + '\')">Browse GBT Sessions</button>' +
        ' <span style="color:#90a4ae;font-size:0.8em;">(ABACAD cadence: companion targets are the OFFs)</span>';
    d.insertBefore(b, d.firstChild);
}

async function gbtLoadSessions(target) {
    var band = window._gbtBand || 'L';
    var d = document.getElementById('bl-search-results');
    d.innerHTML = '<p style="color:#8ab4f8;">Loading GBT sessions for ' + escapeHtml(target) + '...</p>';
    try {
        var resp = await fetch('/api/gbt/sessions?target=' + encodeURIComponent(target) + '&band=' + band);
        var data = await resp.json();
        if (data.error) { d.innerHTML = '<p style="color:#ef5350;">' + data.error + '</p>'; return; }
        renderGbtSessions(target, data);
    } catch (e) {
        d.innerHTML = '<p style="color:#ef5350;">' + e.message + '</p>';
    }
}

function renderGbtSessions(target, data) {
    var d = document.getElementById('bl-search-results');
    var html = '<div class="bl-summary"><span style="color:#66bb6a;font-weight:600;">' + escapeHtml(target) +
        ' GBT sessions</span> <span style="color:#90a4ae;">(' + data.n_exact_files +
        ' exact files; prefix hits from other targets ignored)</span></div>';
    html += '<div class="bl-filters"><label>Band:</label><select onchange="window._gbtBand=this.value;gbtLoadSessions(\'' + target.replace(/'/g, '') + '\')">';
    ['L', 'S', 'C', 'X'].forEach(function (b) {
        html += '<option value="' + b + '"' + ((window._gbtBand || 'L') === b ? ' selected' : '') + '>' + b + '</option>';
    });
    html += '</select>';
    html += '<label style="margin-left:8px;"><input type="checkbox" ' + (window._gbtComps ? 'checked' : '') +
        ' onchange="window._gbtComps=this.checked"> + companions (ABACAD OFFs)</label>';
    html += ' <button class="btn-small" onclick="searchBL()">Back to file list</button></div>';
    html += '<table class="bl-table"><thead><tr><th>Session MJD</th><th>Fine files</th><th>Fine GB</th><th>In band</th><th>Band GB</th><th>Companions</th><th></th></tr></thead><tbody>';
    (data.sessions || []).forEach(function (s) {
        var compNames = (s.companions || []).map(function (c) {
            return c.target + (c.n_band ? ' (' + c.n_band + ')' : '');
        }).join(', ') || 'none found';
        html += '<tr><td style="color:#4fc3f7;">' + s.mjd + '</td><td>' + s.n_fine + '</td><td>' + s.gb_fine + '</td>' +
            '<td>' + s.n_band + '</td><td>' + s.gb_band + '</td>' +
            '<td style="color:#90a4ae;font-size:0.85em;">' + compNames + '</td>' +
            '<td><button class="btn-small" onclick="gbtDownloadSession(\'' + target.replace(/'/g, '') + '\',' + s.mjd + ')">Download ' +
            (window._gbtBand || 'L') + '-band</button></td></tr>';
    });
    html += '</tbody></table>';
    if (!(data.sessions || []).length) {
        html += '<p style="color:#546e7a;">No fine-res GBT sessions for this target.</p>';
    }
    d.innerHTML = html;
}

async function gbtDownloadSession(target, mjd) {
    try {
        var resp = await fetch('/api/gbt/download', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target: target, mjd: mjd, band: window._gbtBand || 'L', companions: !!window._gbtComps })
        });
        var d = await resp.json();
        if (d.error) { alert(d.error); return; }
        alert('Queued ' + d.queued + ' files (' + d.gb_queued + ' GB)' +
              (d.skipped ? ', ' + d.skipped + ' skipped (already present or downloading)' : ''));
        showDownloadPanel();
    } catch (e) {
        alert('Queue failed: ' + e.message);
    }
}

// --- BL Catalog: open browse over the cached sweep ----------------------

var blCatalogTimer = null;
var blSweepWasActive = false;

async function blCatalogBrowse() {
    var resultsDiv = document.getElementById('bl-search-results');
    var q = document.getElementById('target-input').value.trim();
    var url = '/api/blcatalog?fine_only=1&min_epochs=' + (window._blCatMinEp || 0) +
              (window._blCatOnOff ? '&require_onoff=1' : '') +
              (q ? '&q=' + encodeURIComponent(q) : '');
    resultsDiv.innerHTML = '<p style="color:#8ab4f8;">Loading catalog...</p>';
    try {
        var resp = await fetch(url);
        var d = await resp.json();
        if (d.error) {
            resultsDiv.innerHTML = '<p style="color:#ef5350;">' + d.error + '</p>';
            return;
        }
        renderBLCatalog(d);
    } catch (e) {
        resultsDiv.innerHTML = '<p style="color:#ef5350;">' + e.message + '</p>';
        return;
    }
    if (!blCatalogTimer) blCatalogTimer = setInterval(blCatalogPoll, 5000);
    blCatalogPoll();
}

async function blCatalogPoll() {
    try {
        var r = await fetch('/api/blcatalog/sweep/status');
        var s = await r.json();
        var el = document.getElementById('blcat-sweepline');
        if (!el) return;
        if (s.active) {
            blSweepWasActive = true;
            var pct = s.total > 0 ? Math.round(100 * s.done / s.total) : 0;
            el.innerHTML = '<span style="color:#4fc3f7;">Sweeping BL: ' + s.done + '/' +
                s.total + ' (' + pct + '%)</span>, ' + s.catalog_rows + ' cataloged' +
                (s.errors ? ', <span style="color:#ffb74d;">' + s.errors + ' errors</span>' : '');
        } else if (blSweepWasActive) {
            el.innerHTML = 'Sweep done: ' + s.catalog_rows + ' targets cataloged, ' +
                s.catalog_fine_rows + ' with fine-res' +
                (s.last_error ? ' <span style="color:#ffb74d;">(last: ' +
                 escapeHtml(String(s.last_error).slice(0, 70)) + ')</span>' : '');
            if (blCatalogTimer) { clearInterval(blCatalogTimer); blCatalogTimer = null; }
            blSweepWasActive = false;
            blCatalogBrowse();
        } else {
            el.innerHTML = 'Catalog: ' + s.catalog_rows + ' targets, ' +
                s.catalog_fine_rows + ' with fine-res.' +
                (s.catalog_rows === 0 ? ' Click Resweep to build it (one-time, runs a few hours in the background; resumable).' : '');
            if (blCatalogTimer) { clearInterval(blCatalogTimer); blCatalogTimer = null; }
        }
    } catch (e) { /* transient */ }
}

function renderBLCatalog(d) {
    var resultsDiv = document.getElementById('bl-search-results');
    var rows = d.targets || [];
    var html = '<div class="bl-summary">';
    html += '<span style="color:#66bb6a;font-weight:600;">' + rows.length +
            ' fine-res targets</span>' +
            ' <span style="color:#90a4ae;">(of ' + d.total_rows + ' cataloged)</span>';
    html += '</div>';
    html += '<div class="bl-filters">';
    html += '<label>Min epochs:</label><select onchange="window._blCatMinEp=parseInt(this.value);blCatalogBrowse()">';
    [0, 1, 2, 3, 5, 10].forEach(function (n) {
        html += '<option value="' + n + '"' + ((window._blCatMinEp || 0) === n ? ' selected' : '') + '>' + n + '</option>';
    });
    html += '</select>';
    html += '<label style="margin-left:8px;"><input type="checkbox" ' +
            (window._blCatOnOff ? 'checked' : '') +
            ' onchange="window._blCatOnOff=this.checked;blCatalogBrowse()"> ON+OFF cadence</label>';
    html += '<button class="btn-small" style="margin-left:8px;" onclick="blCatalogSweep()" title="Resume the sweep: query targets not yet cataloged (hours if incomplete)">Resweep</button>';
    html += '<button class="btn-small" style="margin-left:4px;" onclick="blCatalogSweep(\'fine\')" title="Re-query only targets that already have fine-res data: fast check for new epochs/files (minutes)">Refresh</button>';
    html += '</div>';
    html += '<div id="blcat-sweepline" style="font-size:0.8em;color:#90a4ae;margin:4px 0;"></div>';
    html += '<table class="bl-table"><thead><tr><th>Target</th><th>Fine</th><th>Epochs</th><th>ON/OFF</th><th>Total files</th><th>Fine GB</th><th></th></tr></thead><tbody>';
    for (var i = 0; i < rows.length; i++) {
        var t = rows[i];
        var raV = t.ra_hours != null ? t.ra_hours : 'null';
        var decV = t.dec != null ? t.dec : 'null';
        html += '<tr style="cursor:pointer;" title="Click to mark on sky map"' +
            ' onclick="onBLRowClick(\'' + t.target.replace(/'/g, '') + '\',' + raV + ',' + decV + ')">' +
            '<td style="color:#4fc3f7;">' + escapeHtml(t.target) + '</td>' +
            '<td>' + t.n_fine + '</td>' +
            '<td>' + t.fine_epochs + '</td>' +
            '<td>' + t.fine_on + '/' + t.fine_off + '</td>' +
            '<td style="color:#90a4ae;">' + t.n_files + '</td>' +
            '<td>' + ((t.fine_bytes || 0) / 1e9).toFixed(1) + '</td>' +
            '<td><button class="btn-small" onclick="event.stopPropagation();blCatalogAdd(\'' + t.target.replace(/'/g, '') + '\')">Add to Registry</button></td>' +
            '</tr>';
    }
    html += '</tbody></table>';
    if (rows.length === 0) {
        html += '<p style="color:#546e7a;">No targets match. ';
        if (d.total_rows === 0) html += 'Catalog is empty: click Resweep to build it.';
        html += '</p>';
    }
    resultsDiv.innerHTML = html;
}

async function blCatalogSweep(mode) {
    try {
        var resp = await fetch('/api/blcatalog/sweep', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({action: 'start', mode: mode || undefined})
        });
        var d = await resp.json();
        if (d.error) { alert(d.error); return; }
        blSweepWasActive = true;
        if (!blCatalogTimer) blCatalogTimer = setInterval(blCatalogPoll, 5000);
        blCatalogPoll();
    } catch (e) { alert('Sweep failed: ' + e.message); }
}

async function blCatalogAdd(name) {
    try {
        var resp = await fetch('/api/registry', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name: name})
        });
        var d = await resp.json();
        if (d.error) { alert(name + ': ' + d.error); return; }
        alert(name + ' added to registry (coords via ' +
              (d.target.coord_source || '?') + ').');
        if (typeof loadRegistry === 'function') {
            try { loadRegistry(); } catch (e) {}
        }
    } catch (e) { alert('Add failed: ' + e.message); }
}

function getFilteredBL() {
    return blResults.filter(function(obs) {
        var url = obs.url || '';
        var res = '';
        if (url.indexOf('_fine.') !== -1) res = 'fine';
        else if (url.indexOf('_mid.') !== -1) res = 'mid';
        else if (url.indexOf('_time.') !== -1) res = 'time';
        else if (url.indexOf('_coarse.') !== -1) res = 'coarse';
        else if (url.indexOf('guppi_') !== -1) res = url.indexOf('.0000.h5') !== -1 ? 'fine' : 'mid';

        var fileType = (obs.file_type || '').toLowerCase();
        var targetName = obs.target || '';
        var isOn = targetName.indexOf('_S') !== -1;
        var isOff = targetName.indexOf('_R') !== -1;

        if (blFilterRes !== 'all' && res !== blFilterRes) return false;
        if (blFilterType === 'on' && !isOn) return false;
        if (blFilterType === 'off' && !isOff) return false;
        if (blFilterFileType !== 'all' && fileType !== blFilterFileType) return false;
        return true;
    });
}

function renderBLResults() {
    var resultsDiv = document.getElementById('bl-search-results');
    var filtered = getFilteredBL();
    var totalPages = Math.max(1, Math.ceil(filtered.length / blPageSize));
    if (blPage >= totalPages) blPage = totalPages - 1;
    if (blPage < 0) blPage = 0;
    var startIdx = blPage * blPageSize;
    var endIdx = Math.min(startIdx + blPageSize, filtered.length);
    var pageData = filtered.slice(startIdx, endIdx);

    var html = '<div class="bl-summary">';
    html += '<span style="color:#66bb6a;font-weight:600;">' + blResults.length + ' observations found</span>';
    if (filtered.length !== blResults.length) {
        html += '<span style="color:#8ab4f8;"> (' + filtered.length + ' after filter)</span>';
    }
    html += '</div>';

    html += '<div class="bl-filters">';
    html += '<label>Res:</label><select onchange="blFilterRes=this.value;blPage=0;renderBLResults()">';
    ['all','fine','mid','time'].forEach(function(r) {
        html += '<option value="' + r + '"' + (blFilterRes === r ? ' selected' : '') + '>' + (r === 'all' ? 'All' : r.charAt(0).toUpperCase() + r.slice(1)) + '</option>';
    });
    html += '</select>';
    html += '<label>Type:</label><select onchange="blFilterType=this.value;blPage=0;renderBLResults()">';
    [['all','All'],['on','ON only'],['off','OFF only']].forEach(function(t) {
        html += '<option value="' + t[0] + '"' + (blFilterType === t[0] ? ' selected' : '') + '>' + t[1] + '</option>';
    });
    html += '</select>';
    html += '<label>Format:</label><select onchange="blFilterFileType=this.value;blPage=0;renderBLResults()">';
    ['all','hdf5','filterbank'].forEach(function(ft) {
        html += '<option value="' + ft + '"' + (blFilterFileType === ft ? ' selected' : '') + '>' + (ft === 'all' ? 'All' : ft === 'hdf5' ? 'HDF5' : 'Filterbank') + '</option>';
    });
    html += '</select>';
    html += '</div>';

    html += '<table class="bl-table"><thead><tr>';
    html += '<th>MJD</th><th>Date</th><th>Target</th><th>RA</th><th>Dec</th><th>Type</th><th>Res</th><th>Size</th><th>DL</th>';
    html += '</tr></thead><tbody>';

    for (var i = 0; i < pageData.length; i++) {
        var obs = pageData[i];
        var rv = obs.ra || obs.ra_hours || '?';
        var dv = obs.decl || obs.dec || '?';
        var rH = typeof rv === 'number' ? rv / 15.0 : (parseRA(rv) || 0);
        var dD = typeof dv === 'number' ? dv : (parseDec(dv) || 0);
        var sn = (obs.target || 'target').replace(/'/g, '');
        var sz = obs.size || obs.file_size;
        var obsUrl = obs.url || '';
        var resDisplay = '?';
        if (obsUrl.indexOf('_fine.') !== -1) resDisplay = 'fine';
        else if (obsUrl.indexOf('_mid.') !== -1) resDisplay = 'mid';
        else if (obsUrl.indexOf('_time.') !== -1) resDisplay = 'time';
        else if (obsUrl.indexOf('_coarse.') !== -1) resDisplay = 'coarse';
        var fname = obsUrl.split('/').pop();

        html += '<tr onclick="onBLRowClick(\'' + sn + '\',' + rH + ',' + dD + ')">';
        var mjdVal = obs.mjd || obs.tstart || 0;
        html += '<td>' + (mjdVal ? mjdVal.toFixed(4) : '?') + '</td>';
        html += '<td>' + (mjdVal ? mjdToDate(mjdVal) : '?') + '</td>';
        html += '<td>' + (obs.target || '?') + '</td>';
        html += '<td>' + (typeof rv === 'number' ? rv.toFixed(3) : rv) + '</td>';
        html += '<td>' + (typeof dv === 'number' ? dv.toFixed(3) : dv) + '</td>';
        html += '<td>' + (obs.file_type || '') + '</td>';
        html += '<td>' + resDisplay + '</td>';
        html += '<td>' + (sz ? (sz / 1e9).toFixed(1) + ' GB' : '?') + '</td>';
        if (obsUrl) {
            html += '<td><button class="dl-btn" onclick="event.stopPropagation();downloadFile(\'' + obsUrl + '\',\'' + fname + '\')" title="Download">\u2b07</button></td>';
        } else {
            html += '<td>\u2014</td>';
        }
        html += '</tr>';
    }
    html += '</tbody></table>';

    if (totalPages > 1) {
        html += '<div class="bl-pagination">';
        html += '<button onclick="blPage=0;renderBLResults()"' + (blPage === 0 ? ' disabled' : '') + '>\u00ab First</button>';
        html += '<button onclick="blPage=Math.max(0,blPage-1);renderBLResults()"' + (blPage === 0 ? ' disabled' : '') + '>\u2039 Prev</button>';
        html += '<span class="bl-page-info">Page ' + (blPage + 1) + ' of ' + totalPages + ' (' + (startIdx + 1) + '-' + endIdx + ' of ' + filtered.length + ')</span>';
        html += '<button onclick="blPage=Math.min(' + (totalPages - 1) + ',blPage+1);renderBLResults()"' + (blPage >= totalPages - 1 ? ' disabled' : '') + '>Next \u203a</button>';
        html += '<button onclick="blPage=' + (totalPages - 1) + ';renderBLResults()"' + (blPage >= totalPages - 1 ? ' disabled' : '') + '>Last \u00bb</button>';
        html += '</div>';
    }

    resultsDiv.innerHTML = html;
}

function onBLRowClick(name, ra, dec) {
    if (ra && dec) {
        plotTargetOnMap(name, ra, dec);
    }
}

function mjdToDate(mjd) {
    var epoch = Date.UTC(1858, 10, 17);
    var dateMs = epoch + (mjd * 86400000);
    var d = new Date(dateMs);
    var y = d.getUTCFullYear();
    var m = String(d.getUTCMonth() + 1).padStart(2, '0');
    var day = String(d.getUTCDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
}

function parseRA(ra) {
    if (typeof ra === 'number') return ra;
    var m = String(ra).match(/(\d+)h\s*(\d+)m\s*([\d.]+)s/);
    if (m) return parseInt(m[1]) + parseInt(m[2]) / 60 + parseFloat(m[3]) / 3600;
    return parseFloat(ra) || null;
}

function parseDec(dec) {
    if (typeof dec === 'number') return dec;
    var m = String(dec).match(/(-?)(\d+)d\s*(\d+)m?\s*([\d.]+)?s?/);
    if (m) {
        var sign = m[1] === '-' ? -1 : 1;
        return sign * (parseInt(m[2]) + parseInt(m[3]) / 60 + (parseFloat(m[4]) || 0) / 3600);
    }
    return parseFloat(dec) || null;
}

// ─── Local Data with File Selection ──────────────────────────────────

// Collapsible target groups in Local Data (state persisted in localStorage)
var collapsedTargets = {};
try { collapsedTargets = JSON.parse(localStorage.getItem('collapsedTargets') || '{}') || {}; } catch (e) {}

function toggleTargetGroup(target) {
    var el = document.getElementById('target-files-' + target);
    if (!el) return;
    var hide = el.style.display !== 'none';
    el.style.display = hide ? 'none' : '';
    if (hide) collapsedTargets[target] = true;
    else delete collapsedTargets[target];
    try { localStorage.setItem('collapsedTargets', JSON.stringify(collapsedTargets)); } catch (e) {}
    var arrow = document.getElementById('target-arrow-' + target);
    if (arrow) arrow.textContent = hide ? '\u25B8' : '\u25BE';
}

async function loadLocalData() {
    var div = document.getElementById('local-data-list');
    div.innerHTML = '<p style="color:#8ab4f8;">Loading local data...</p>';

    try {
        var resp = await fetch('/api/targets');
        var data = await resp.json();
        localDataCache = data;

        var html = '';
        var totalFiles = 0;
        for (var target in data) {
            if (!data.hasOwnProperty(target)) continue;
            var files = data[target];
            var fineCount = (files.fine || []).length;
            var midCount = (files.mid || []).length;
            var fbCount = (files.filterbank || []).length;
            var h5Count = (files.h5 || []).length;
            var totalCount = fineCount + midCount + fbCount + h5Count;
            if (totalCount === 0) continue;

            var isCollapsed = !!collapsedTargets[target];
            html += '<div class="target-header" style="cursor:pointer;user-select:none;" onclick="toggleTargetGroup(\'' + target + '\')" title="Click to collapse/expand">' +
                '<span id="target-arrow-' + target + '" style="color:#90a4ae;display:inline-block;width:14px;">' + (isCollapsed ? '\u25B8' : '\u25BE') + '</span>' +
                '<strong style="color:#4fc3f7;">' + target + '</strong><span style="color:#546e7a;"> ' + totalCount + ' files</span></div>';
            html += '<div class="target-files" id="target-files-' + target + '"' + (isCollapsed ? ' style="display:none;"' : '') + '>';

            // Helper to render a format section
            function renderSection(label, labelClass, fileList) {
                if (!fileList || fileList.length === 0) return;
                html += '<div class="format-section"><div class="format-label ' + labelClass + '">' + label + ' (' + fileList.length + ')</div>';
                for (var i = 0; i < fileList.length; i++) {
                    var f = fileList[i];
                    var isOn = f.name.indexOf('_S_') !== -1;
                    var isSelected = selectedFiles.has(f.path);
                    var sizeStr = f.size_gb < 1 ? (f.size_gb * 1000).toFixed(0) + ' MB' : f.size_gb + ' GB';
                    var dateStr = f.date ? ' <span style="color:#546e7a;font-size:0.85em;">' + f.date + '</span>' : '';
                    html += '<div class="data-file-row' + (isSelected ? ' selected' : '') + '" data-path="' + f.path + '">';
                    html += '<span class="file-name-click" onclick="toggleFileSelection(\'' + f.path + '\')">' + f.name + '</span>';
                    html += '<span><span class="file-type ' + labelClass.replace('-label','') + '">' + (isOn ? 'ON' : 'OFF') + '</span> ' + sizeStr + dateStr + ' <button class="btn-del" onclick="event.stopPropagation();deleteFile(\'' + f.path + '\',\'' + f.name + '\')" title="Delete">\u2715</button></span>';
                    html += '</div>';
                    totalFiles++;
                }
                html += '</div>';
            }

            renderSection('FINE-RES', 'fine-label', files.fine);
            renderSection('MID-RES', 'mid-label', files.mid);
            renderSection('FILTERBANK', 'fb-label', files.filterbank);
            renderSection('HDF5', 'h5-label', files.h5);
            html += '</div>';  // close target-files collapsible wrapper
        }
        div.innerHTML = html || '<p style="color:#546e7a;">No local data found.</p>';

        var selControls = document.getElementById('file-selection-controls');
        if (totalFiles > 0) { selControls.style.display = 'flex'; }
        else { selControls.style.display = 'none'; }
        updateSelectionBadge();
        updateScanBadge();
        refreshFileDetailPanel();
    } catch(e) {
        div.innerHTML = '<p style="color:#ef5350;">Error: ' + e.message + '</p>';
    }
}

function toggleFileSelection(path) {
    if (selectedFiles.has(path)) {
        selectedFiles.delete(path);
    } else {
        selectedFiles.add(path);
        if (!fileHeaderCache[path]) {
            fetchFileHeader(path);
        }
    }

    var row = document.querySelector('.data-file-row[data-path="' + CSS.escape(path) + '"]');
    if (row) {
        if (selectedFiles.has(path)) { row.classList.add('selected'); }
        else { row.classList.remove('selected'); }
    }

    updateSelectionBadge();
    updateScanBadge();
    refreshFileDetailPanel();
    refreshSkyMapMarkers();
}

// Rebuild sky map markers from selected files
function refreshSkyMapMarkers() {
    // Group selected files by target name
    var targetCoords = {};  // target name -> {ra, dec}
    var neededHeaders = [];

    selectedFiles.forEach(function(path) {
        // Find the target name and header for this file
        for (var target in localDataCache) {
            if (!localDataCache.hasOwnProperty(target)) continue;
            var allFiles = (localDataCache[target].fine || []).concat(localDataCache[target].mid || [])
                .concat(localDataCache[target].filterbank || []).concat(localDataCache[target].h5 || []);
            for (var i = 0; i < allFiles.length; i++) {
                if (allFiles[i].path === path) {
                    // Found the target for this file
                    var header = fileHeaderCache[path];
                    if (header && header.header) {
                        var h = header.header;
                        var raHours = typeof h.src_raj === 'number' ? h.src_raj : parseFloat(h.src_raj);
                        var decDeg = typeof h.src_dej === 'number' ? h.src_dej : parseFloat(h.src_dej);
                        if (!isNaN(raHours) && !isNaN(decDeg)) {
                            targetCoords[target] = { ra: raHours, dec: decDeg };
                        }
                    } else {
                        // Header not loaded yet, will need to fetch
                        neededHeaders.push(path);
                    }
                    break;
                }
            }
        }
    });

    // Update the markers
    celestialTargets = targetCoords;

    // Fetch any missing headers, then refresh markers again
    if (neededHeaders.length > 0) {
        neededHeaders.forEach(function(p) {
            if (!fileHeaderCache[p]) fetchFileHeader(p);
        });
    }

    try { Celestial.redraw(); } catch(e) {}
    updateSkyMapInfo();
}

async function fetchFileHeader(path) {
    try {
        var resp = await fetch('/api/header?file=' + encodeURIComponent(path));
        var data = await resp.json();
        if (!data.error) {
            fileHeaderCache[path] = data;
            refreshFileDetailPanel();
            refreshSkyMapMarkers();
        }
    } catch(e) {}
}

function selectAllFiles() {
    for (var target in localDataCache) {
        if (!localDataCache.hasOwnProperty(target)) continue;
        var files = localDataCache[target];
        var allFiles = (files.fine || []).concat(files.mid || []).concat(files.filterbank || []).concat(files.h5 || []);
        for (var i = 0; i < allFiles.length; i++) {
            selectedFiles.add(allFiles[i].path);
            if (!fileHeaderCache[allFiles[i].path]) fetchFileHeader(allFiles[i].path);
        }
    }
    var rows = document.querySelectorAll('.data-file-row');
    for (var i = 0; i < rows.length; i++) rows[i].classList.add('selected');
    updateSelectionBadge();
    updateScanBadge();
    refreshFileDetailPanel();
    refreshSkyMapMarkers();
}

function selectNoneFiles() {
    selectedFiles.clear();
    var rows = document.querySelectorAll('.data-file-row');
    for (var i = 0; i < rows.length; i++) rows[i].classList.remove('selected');
    updateSelectionBadge();
    updateScanBadge();
    refreshFileDetailPanel();
    refreshSkyMapMarkers();
}

function updateSelectionBadge() {
    var badge = document.getElementById('file-selection-count');
    var count = selectedFiles.size;
    badge.textContent = count + ' file' + (count !== 1 ? 's' : '') + ' selected';
    if (count === 0) badge.classList.add('zero');
    else badge.classList.remove('zero');
}

function updateScanBadge() {
    var badge = document.getElementById('scan-target-badge');
    if (selectedFiles.size > 0) {
        var total = 0;
        for (var target in localDataCache) {
            if (!localDataCache.hasOwnProperty(target)) continue;
            total += ((localDataCache[target].fine || []).length + (localDataCache[target].mid || []).length +
                      (localDataCache[target].filterbank || []).length + (localDataCache[target].h5 || []).length);
        }
        badge.textContent = 'Scanning: ' + selectedFiles.size + ' of ' + total + ' files';
    } else {
        badge.textContent = 'Scanning: All fine-res files';
    }
}

function refreshFileDetailPanel() {
    var panel = document.getElementById('file-detail-panel');
    var content = document.getElementById('file-detail-content');
    if (selectedFiles.size === 0) { panel.style.display = 'none'; return; }
    panel.style.display = 'block';
    var html = '';
    selectedFiles.forEach(function(path) {
        var fileInfo = null;
        for (var target in localDataCache) {
            if (!localDataCache.hasOwnProperty(target)) continue;
            var allFiles = (localDataCache[target].fine || []).concat(localDataCache[target].mid || [])
                .concat(localDataCache[target].filterbank || []).concat(localDataCache[target].h5 || []);
            for (var i = 0; i < allFiles.length; i++) {
                if (allFiles[i].path === path) { fileInfo = allFiles[i]; break; }
            }
            if (fileInfo) break;
        }
        if (!fileInfo) return;
        var isOn = fileInfo.name.indexOf('_S_') !== -1;
        var header = fileHeaderCache[path];
        var headerData = header ? header.header : null;
        html += '<div class="file-detail-card">';
        html += '<div class="fd-name">' + fileInfo.name + '</div>';
        html += '<div class="fd-grid">';
        var sourceName = headerData ? (headerData.source_name || '?') : '?';
        html += '<span>Source:</span><span class="fd-val">' + sourceName + '</span>';
        html += '<span>Cadence:</span><span class="fd-val"><span class="on-badge ' + (isOn ? 'on' : 'off') + '">' + (isOn ? 'ON' : 'OFF') + '</span></span>';
        if (headerData) {
            var mjd = headerData.tstart || headerData.mjd || '?';
            html += '<span>MJD:</span><span class="fd-val">' + (typeof mjd === 'number' ? mjd.toFixed(4) : mjd) + '</span>';
            var ra = headerData.src_raj || '?';
            var dec = headerData.src_dej || '?';
            html += '<span>RA:</span><span class="fd-val">' + (typeof ra === 'number' ? ra.toFixed(5) : ra) + '</span>';
            html += '<span>Dec:</span><span class="fd-val">' + (typeof dec === 'number' ? dec.toFixed(5) : dec) + '</span>';
            var fch1 = headerData.fch1 || '?';
            var nchans = headerData.nchans || '?';
            var chBw = headerData.foff || 0;
            var fEnd = (typeof fch1 === 'number' && typeof nchans === 'number' && typeof chBw === 'number') ? (fch1 + nchans * chBw) : '?';
            html += '<span>Freq:</span><span class="fd-val">' + (typeof fch1 === 'number' ? fch1.toFixed(3) : fch1) + ' \u2192 ' + (typeof fEnd === 'number' ? fEnd.toFixed(3) : fEnd) + ' MHz</span>';
            html += '<span>Channels:</span><span class="fd-val">' + nchans + '</span>';
            var tsamp = headerData.tsamp || '?';
            html += '<span>Tsamp:</span><span class="fd-val">' + (typeof tsamp === 'number' ? tsamp.toFixed(3) + 's' : tsamp) + '</span>';
        } else {
            html += '<span style="grid-column:1/-1;color:#546e7a;">Loading header...</span>';
        }
        html += '<span>Size:</span><span class="fd-val">' + fileInfo.size_gb + ' GB</span>';
        html += '</div></div>';
    });
    content.innerHTML = html;
}

// ─── Scan Control ─────────────────────────────────────────────────────
async function startScan() {
    // 3D: derive target + resolution from the selected filenames
    // (e.g. fine/Parkes_57910_34684_PROXCEN_S_fine.h5 -> PROXCEN/fine)
    var scanTarget = 'PROXCEN';
    var scanRes = 'fine';
    if (selectedFiles.size > 0) {
        var fn = Array.from(selectedFiles)[0].split('/').pop();
        var parts = fn.split('_');
        if (parts.length >= 4) scanTarget = parts[3];
        if (fn.indexOf('_mid.') !== -1) scanRes = 'mid';
        else if (fn.indexOf('_time.') !== -1) scanRes = 'time';
    }
    var params = {
        target: scanTarget, resolution: scanRes,
        sub_band_chans: parseInt(document.getElementById('ctrl-subband-width').value),
        overlap: parseInt(document.getElementById('ctrl-overlap').value),
        max_drift: parseFloat(document.getElementById('ctrl-max-drift').value),
        snr: parseFloat(document.getElementById('ctrl-snr').value),
    };
    if (selectedFiles.size > 0) params.files = Array.from(selectedFiles);
    var fStart = document.getElementById('ctrl-f-start').value;
    var fStop = document.getElementById('ctrl-f-stop').value;
    if (fStart) params.f_start = parseFloat(fStart);
    if (fStop) params.f_stop = parseFloat(fStop);
    document.getElementById('btn-start-scan').disabled = true;
    document.getElementById('btn-stop-scan').disabled = false;
    document.getElementById('btn-resume-scan').disabled = true;
    try {
        var resp = await fetch('/api/scan/start', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(params),
        });
        var data = await resp.json();
        if (data.error) {
            alert(data.error);
            document.getElementById('btn-start-scan').disabled = false;
            document.getElementById('btn-stop-scan').disabled = true;
            document.getElementById('btn-resume-scan').disabled = false;
        } else if (data.scan_id) {
            currentScanId = data.scan_id;
        }
    } catch(e) {
        alert('Error: ' + e.message);
        document.getElementById('btn-start-scan').disabled = false;
        document.getElementById('btn-stop-scan').disabled = true;
        document.getElementById('btn-resume-scan').disabled = false;
    }
}

async function stopScan() {
    try { await fetch('/api/scan/stop', { method: 'POST' }); } catch(e) {}
    document.getElementById('btn-start-scan').disabled = false;
    document.getElementById('btn-stop-scan').disabled = true;
    document.getElementById('btn-resume-scan').disabled = false;
}

async function resumeScan() {
    document.getElementById('btn-resume-scan').disabled = true;
    document.getElementById('btn-start-scan').disabled = true;
    document.getElementById('btn-stop-scan').disabled = false;
    try {
        var resp = await fetch('/api/scan/resume', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({scan_id: currentScanId}),
        });
        var data = await resp.json();
        if (data.error) {
            alert(data.error);
            document.getElementById('btn-resume-scan').disabled = false;
            document.getElementById('btn-start-scan').disabled = false;
            document.getElementById('btn-stop-scan').disabled = true;
        } else if (data.scan_id) {
            currentScanId = data.scan_id;
            var cp = data.checkpoint || {};
            console.log('Resumed scan ' + data.scan_id + ' from file ' +
                        (cp.file_index || '?') + '/' + (cp.file_total || '?') +
                        ' sub-band ' + (cp.sub_band_index || 0) + '/' + (cp.sub_band_total || 0));
        }
    } catch(e) {
        alert('Error: ' + e.message);
        document.getElementById('btn-resume-scan').disabled = false;
        document.getElementById('btn-start-scan').disabled = false;
        document.getElementById('btn-stop-scan').disabled = true;
    }
}

async function pollScanStatus() {
    try {
        var resp = await fetch('/api/scan/status' + (currentScanId ? '?scan_id=' + encodeURIComponent(currentScanId) : ''));
        var data = await resp.json();
        scanStateActive = data.active;
        var statusDiv = document.getElementById('scan-status');
        var progBar = document.getElementById('progress-bar-container');
        if (data.active) {
            // Sync button states for active scan (handles page refresh during scan)
            document.getElementById('btn-start-scan').disabled = true;
            document.getElementById('btn-stop-scan').disabled = false;
            document.getElementById('btn-resume-scan').disabled = true;
            // Build status line with real structured data
            var target = data.target || '---';
            var subDone = data.sub_bands_done || 0;
            var subTotal = data.sub_bands_total || 0;
            var totalHits = data.total_hits || 0;
            var currentFreq = data.current_freq || 0;
            statusDiv.innerHTML = '<p class="status-running">Scanning ' + target + '</p>';
            progBar.style.display = 'block';
            var logDiv = document.getElementById('scan-log');
            var lines = data.log_tail || [];
            // Accumulate logs: track what we've already rendered and only append new lines
            if (!window._lastLogCount) window._lastLogCount = 0;
            if (!window._logLineSet) window._logLineSet = new Set();
            var newLines = lines.filter(function(l) {
                var key = l;
                // Use line content + position in stream as dedup key
                // Lines repeat across polls, so track by index offset from end
                return true;
            });
            // Fix 3: Skip log rebuild if unchanged (last line signature matches)
            var lastSig = lines.length > 0 ? lines[lines.length-1].substring(0, 40) : '';
            if (window._dashLastLogSig === lastSig && window._dashLastLogCount === lines.length) {
                // Log unchanged, skip DOM rebuild
            } else {
                window._dashLastLogSig = lastSig;
                window._dashLastLogCount = lines.length;
                var wasNearBottom = logDiv.scrollHeight - logDiv.scrollTop - logDiv.clientHeight < 50;
                var prevScrollTop = logDiv.scrollTop;
                logDiv.innerHTML = lines.map(function(l) { return '<div>' + escapeHtml(l) + '</div>'; }).join('');
                if (wasNearBottom) {
                    logDiv.scrollTop = logDiv.scrollHeight;
                } else {
                    logDiv.scrollTop = prevScrollTop;
                }
            }
            // Use structured progress if available, fall back to log parsing
            if (subTotal > 0) {
                var pct = (subDone / subTotal) * 100;
                document.getElementById('progress-fill').style.width = pct + '%';
                document.getElementById('progress-text').textContent = subDone + '/' + subTotal + ' sub-bands (' + pct.toFixed(1) + '%) | ' + totalHits + ' hits' + (currentFreq > 0 ? ' | ' + currentFreq.toFixed(3) + ' MHz' : '');
            } else {
                var lastLine = (data.progress && data.progress.last_line) || '';
                var match = lastLine.match(/\[(\d+)\/(\d+)\]/);
                if (match) {
                    var pct = (parseInt(match[1]) / parseInt(match[2])) * 100;
                    document.getElementById('progress-fill').style.width = pct + '%';
                    document.getElementById('progress-text').textContent = match[1] + '/' + match[2] + ' sub-bands (' + pct.toFixed(1) + '%)';
                }
            }
        } else {
            if (statusDiv.querySelector('.status-running')) {
                statusDiv.innerHTML = '<p class="status-idle">Scan complete.</p>';
                document.getElementById('btn-start-scan').disabled = false;
                document.getElementById('btn-stop-scan').disabled = true;
                document.getElementById('btn-resume-scan').disabled = true;
                // Reload scan list to pick up the new/updated scan
                loadScansList();
                loadResults(); loadStats();
                // After reload, check if selected scan has checkpoint
                setTimeout(updateResumeButton, 500);
            } else {
                // Not running, not just completed: sync resume button with selected scan
                document.getElementById('btn-resume-scan').disabled = !data.can_resume;
            }
        }
    } catch(e) {}
}

// ─── Scan History ────────────────────────────────────────────────────

async function loadScansList() {
    try {
        // Use fast SQLite endpoint
        var resp = await fetch('/api/db/scans');
        var dbScans = await resp.json();
        if (dbScans && !dbScans.error && dbScans.length > 0) {
            // Convert DB format to the format the dashboard expects
            scansList = dbScans.map(function(s) {
                return {
                    scan_id: s.scan_id,
                    target: s.target,
                    timestamp: s.timestamp,
                    mjd_start: s.mjd_start,
                    bary_mjd: s.bary_mjd,
                    status: s.status,
                    parameters: {
                        sub_band_chans: s.sub_band_chans,
                        overlap: s.overlap,
                        max_drift: s.max_drift,
                        snr: s.snr_threshold,
                        f_start: s.f_start,
                        f_stop: s.f_stop,
                    },
                    stats: {
                        total_hits: s.total_hits,
                        on_hits: s.on_hits,
                        off_hits: s.off_hits,
                        duration_s: s.duration_s,
                    },
                    _bary_corrected: s.bary_corrected,
                    _bary_velocity: s.bary_velocity,
                };
            });
        } else {
            scansList = [];
        }
        // Merge in disk-only scans (in-progress scans not yet in DB)
        try {
            var resp2 = await fetch('/api/scans');
            var diskScans = await resp2.json();
            if (diskScans && diskScans.length) {
                var dbIds = new Set(scansList.map(function(s) { return s.scan_id; }));
                for (var d = 0; d < diskScans.length; d++) {
                    if (!dbIds.has(diskScans[d].scan_id)) {
                        scansList.push(diskScans[d]);
                    }
                }
            }
        } catch(e) { /* disk discovery failed, use DB only */ }
        // Sort merged list by timestamp descending (newest first)
        scansList.sort(function(a, b) {
            return (b.timestamp || '').localeCompare(a.timestamp || '');
        });
        renderScanSelector();
        // Auto-select the most recent scan if none selected
        if (!currentScanId && scansList.length > 0) {
            currentScanId = scansList[0].scan_id;
            renderScanSelector();
            loadScanResults(currentScanId);
            loadScanStats(currentScanId);
            updateResumeButton();
        } else if (scansList.length === 0) {
            // No scans at all, load legacy results
            loadResults();
            loadStats();
        }
        // Refresh barycentric scan checkboxes
        updateBaryScanCheckboxes();
    } catch(e) {
        console.error('Error loading scans:', e);
        loadResults();  // Fallback to legacy
        loadStats();
    }
}

function renderScanSelector() {
    var sel = document.getElementById('scan-selector');
    var delBtn = document.getElementById('btn-delete-scan');
    if (scansList.length === 0) {
        sel.innerHTML = '<option value="">No scans yet</option>';
        document.getElementById('scan-meta-display').innerHTML = '';
        if (delBtn) delBtn.disabled = true;
        return;
    }
    if (delBtn) delBtn.disabled = false;
    var html = '';
    for (var i = 0; i < scansList.length; i++) {
        var s = scansList[i];
        var label = scanLabel(s);
        // Shorten label if too long
        if (label.length > 50) label = label.substring(0, 50);
        var selAttr = (currentScanId === s.scan_id) ? ' selected' : '';
        html += '<option value="' + s.scan_id + '"' + selAttr + '>' + label + '</option>';
    }
    sel.innerHTML = html;
}

async function deleteCurrentScan() {
    if (!currentScanId) {
        alert('Select a scan to delete first.');
        return;
    }
    if (!confirm('Delete scan "' + currentScanId + '" and all its hits from the database?\nThis cannot be undone.'))
        return;
    try {
        var resp = await fetch('/api/db/scans/' + encodeURIComponent(currentScanId), {method: 'DELETE'});
        var data = await resp.json();
        if (data.error) {
            alert('Error: ' + data.error);
            return;
        }
        // Clear selection and reload
        currentScanId = null;
        await loadScansList();
        // Auto-select newest if available
        if (scansList.length > 0) {
            currentScanId = scansList[0].scan_id;
            renderScanSelector();
            loadScanResults(currentScanId);
            loadScanStats(currentScanId);
            updateResumeButton();
        } else {
            allHits = [];
            rejectionCandidates = [];
            loadResults();
            loadStats();
            document.getElementById('scan-meta-display').innerHTML = '';
        }
    } catch(e) {
        console.error('Delete scan error:', e);
        alert('Failed to delete scan: ' + e.message);
    }
}

function onScanSelected() {
    var sel = document.getElementById('scan-selector');
    currentScanId = sel.value;
    if (!currentScanId) {
        // Load all results (legacy)
        allHits = [];
        rejectionCandidates = [];
        loadResults();
        loadStats();
        document.getElementById('scan-meta-display').innerHTML = '';
        document.getElementById('btn-resume-scan').disabled = true;
        return;
    }
    loadScanResults(currentScanId);
    loadScanStats(currentScanId);
    updateResumeButton();
}

async function updateResumeButton() {
    // Enable Resume button only if the selected scan has a checkpoint
    var btn = document.getElementById('btn-resume-scan');
    if (!currentScanId || scanStateActive) {
        btn.disabled = true;
        return;
    }
    try {
        var resp = await fetch('/api/scan/status?scan_id=' + encodeURIComponent(currentScanId));
        var data = await resp.json();
        btn.disabled = !data.can_resume;
    } catch(e) {
        btn.disabled = true;
    }
}

async function loadScanResults(scanId) {
    try {
        // Use fast SQLite endpoint with pagination
        var resp = await fetch('/api/db/scans/' + encodeURIComponent(scanId) + '/hits?limit=500&offset=0&order=snr%20DESC');
        var data = await resp.json();
        if (data.error) {
            console.error('Scan results error:', data.error);
            // Fallback to legacy
            return loadScanResultsLegacy(scanId);
        }
        
        // Find scan meta from scansList
        var meta = null;
        for (var i = 0; i < scansList.length; i++) {
            if (scansList[i].scan_id === scanId) { meta = scansList[i]; break; }
        }
        renderScanMetaDisplay(meta || {}, []);
        
        // Convert DB hits to the format expected by renderHitTable
        allHits = (data.hits || []).map(function(h) {
            return {
                drift_rate: h.drift_rate,
                snr: h.snr,
                freq: h.freq,
                channel: h.channel,
                sub_band: h.sub_band,
                file: h.source_file,
                _source: h.source_file,
                on_off: h.on_off,
                barycentric_freq: h.barycentric_freq,
            };
        });
        
        // Store total for pagination display
        allHitsTotal = data.total || allHits.length;
        allHitsScanId = scanId;
        allHitsOffset = 0;
        allHitsLimit = 500;
        
        // Load rejection results from legacy endpoint (still JSON-based)
        try {
            var rresp = await fetch('/api/scans/' + encodeURIComponent(scanId) + '/results');
            var rdata = await rresp.json();
            if (rdata.rejection && rdata.rejection.candidates) {
                rejectionCandidates = rdata.rejection.candidates;
                renderRejectionSummary(rdata.rejection.summary, rdata.rejection.parameters);
            } else {
                rejectionCandidates = [];
                document.getElementById('rejection-summary').innerHTML =
                    '<p style="color:#546e7a;">No rejection run for this scan yet.</p>';
            }
        } catch(e2) {
            rejectionCandidates = [];
        }
        
        renderHitTable();
        renderHitChart();
        renderHitsPagination();
    } catch(e) {
        console.error('Error loading scan results:', e);
        // Fallback to legacy
        loadScanResultsLegacy(scanId);
    }
}

// Legacy results loader (JSON-based, kept as fallback)
async function loadScanResultsLegacy(scanId) {
    try {
        var resp = await fetch('/api/scans/' + encodeURIComponent(scanId) + '/results');
        var data = await resp.json();
        if (data.error) { console.error('Scan results error:', data.error); return; }
        renderScanMetaDisplay(data.meta || {}, data.results);
        allHits = [];
        var results = data.results || [];
        for (var i = 0; i < results.length; i++) {
            var result = results[i];
            if (result.data && result.data.hits) {
                for (var j = 0; j < result.data.hits.length; j++) {
                    result.data.hits[j]._source = result.name;
                    allHits.push(result.data.hits[j]);
                }
            }
        }
        if (data.rejection && data.rejection.candidates) {
            rejectionCandidates = data.rejection.candidates;
            renderRejectionSummary(data.rejection.summary, data.rejection.parameters);
        }
        renderHitTable();
        renderHitChart();
    } catch(e) {
        console.error('Error loading legacy scan results:', e);
    }
}

async function loadScanStats(scanId) {
    try {
        // Use fast SQLite endpoint
        var resp = await fetch('/api/db/scans/' + encodeURIComponent(scanId) + '/stats');
        var data = await resp.json();
        if (data.error) {
            // Fallback to legacy
            var resp2 = await fetch('/api/stats?scan_id=' + encodeURIComponent(scanId));
            data = await resp2.json();
        }
        document.getElementById('stat-total').textContent = 'Total Hits: ' + (data.total_hits || 0).toLocaleString();
        document.getElementById('stat-on').textContent = 'ON: ' + (data.on_hits || 0).toLocaleString();
        document.getElementById('stat-off').textContent = 'OFF: ' + (data.off_hits || 0).toLocaleString();
        document.getElementById('stat-top').textContent = 'Top SNR: ' + (data.top_snr || 0);
    } catch(e) {}
}

function renderScanMetaDisplay(meta, results) {
    var display = document.getElementById('scan-meta-display');
    if (!meta || !meta.scan_id) {
        display.innerHTML = '<span style="color:#546e7a;">' + (results ? results.length + ' result files' : '') + '</span>';
        return;
    }
    var html = '';
    // Status badge
    var status = meta.status || 'unknown';
    html += '<span class="sm-tag status-' + status + '">' + status.toUpperCase() + '</span>';
    // Target
    if (meta.target) html += '<span class="sm-sep">|</span><span><strong>Target:</strong> ' + meta.target + '</span>';
    // Date
    if (meta.timestamp) {
        var dt = meta.timestamp.replace('T', ' ').substring(0, 16);
        html += '<span class="sm-sep">|</span><span>' + dt + '</span>';
    }
    // Hit count
    var stats = meta.stats || {};
    if (stats.total_hits !== undefined) {
        html += '<span class="sm-sep">|</span><span>Hits: ' + stats.total_hits;
        if (stats.on_hits !== undefined) html += ' (ON:' + stats.on_hits + ' / OFF:' + stats.off_hits + ')';
        html += '</span>';
    }
    // Duration
    if (stats.duration_s && stats.duration_s > 0) {
        var mins = Math.floor(stats.duration_s / 60);
        var secs = Math.round(stats.duration_s % 60);
        html += '<span class="sm-sep">|</span><span>' + mins + 'm ' + secs + 's</span>';
    }
    // Parameters
    var p = meta.parameters || {};
    if (p.max_drift) html += '<span class="sm-sep">|</span><span>drift \u2264 ' + p.max_drift + ' Hz/s</span>';
    if (p.snr) html += '<span class="sm-sep">|</span><span>SNR \u2265 ' + p.snr + '</span>';
    display.innerHTML = html;
}

function renderRejectionSummary(summary, params) {
    var summaryDiv = document.getElementById('rejection-summary');
    if (!summary) {
        summaryDiv.innerHTML = '<p style="color:#546e7a;">No rejection run for this scan yet.</p>';
        return;
    }
    summaryDiv.innerHTML = '<div class="rejection-stats">' +
        '<span class="rstat"><span class="rstat-label">Total ON</span><span class="rstat-val">' + summary.total_on.toLocaleString() + '</span></span>' +
        '<span class="rstat"><span class="rstat-label">Total OFF</span><span class="rstat-val">' + summary.total_off.toLocaleString() + '</span></span>' +
        '<span class="rstat"><span class="rstat-label">Rejected (RFI)</span><span class="rstat-val" style="color:#ef5350;">' + summary.rejected_rfi.toLocaleString() + ' (' + summary.rejection_rate + '%)</span></span>' +
        '<span class="rstat"><span class="rstat-label">Candidates</span><span class="rstat-val" style="color:' + (summary.candidates > 0 ? '#66bb6a' : '#546e7a') + ';font-weight:bold;font-size:1.3em;">' + summary.candidates.toLocaleString() + '</span></span></div>';
}

// ─── Results ──────────────────────────────────────────────────────────
async function loadResults() {
    try {
        var url = '/api/results';
        if (currentScanId) url += '?scan_id=' + encodeURIComponent(currentScanId);
        var resp = await fetch(url);
        var data = await resp.json();
        allHits = [];
        for (var i = 0; i < data.length; i++) {
            var result = data[i];
            if (result.data && result.data.hits) {
                for (var j = 0; j < result.data.hits.length; j++) {
                    result.data.hits[j]._source = result.name;
                    allHits.push(result.data.hits[j]);
                }
            }
            if (result.type === 'summary' && result.data.files) {
                for (var fname in result.data.files) {
                    if (!result.data.files.hasOwnProperty(fname)) continue;
                    var info = result.data.files[fname];
                    if (info.top_hits) {
                        for (var k = 0; k < info.top_hits.length; k++) {
                            info.top_hits[k]._source = fname;
                            info.top_hits[k].on_off = info.on_off;
                            allHits.push(info.top_hits[k]);
                        }
                    }
                }
            }
        }
        renderHitTable();
        renderHitChart();
    } catch(e) { console.error('Error loading results:', e); }
}

function getFilteredHits() {
    var filter = document.getElementById('results-filter').value;
    var snrMin = parseFloat(document.getElementById('results-snr-min').value);
    var driftMin = parseFloat(document.getElementById('results-drift-min').value);
    var driftMax = parseFloat(document.getElementById('results-drift-max').value);
    var freqMin = parseFloat(document.getElementById('results-freq-min').value);
    var freqMax = parseFloat(document.getElementById('results-freq-max').value);

    // Fix 6: Build cache key from filter params + sort + data source. Only re-sort if changed.
    var cacheKey = filter + '|' + snrMin + '|' + driftMin + '|' + driftMax + '|' + freqMin + '|' + freqMax + '|' + resultsSort.col + '|' + resultsSort.dir + '|' + allHits.length + '|' + rejectionCandidates.length;
    if (window._filteredHitsCache && window._filteredHitsCacheKey === cacheKey) {
        return window._filteredHitsCache;
    }

    var hits = allHits.slice();
    if (filter === 'on') hits = hits.filter(function(h) { return h.on_off === 'ON' || (h._source || '').indexOf('_S_') !== -1 || (h.source_file || '').indexOf('_S_') !== -1 || (h.file || '').indexOf('_S_') !== -1; });
    else if (filter === 'off') hits = hits.filter(function(h) { return h.on_off === 'OFF' || (h._source || '').indexOf('_R_') !== -1 || (h.source_file || '').indexOf('_R_') !== -1 || (h.file || '').indexOf('_R_') !== -1; });
    else if (filter === 'candidates') hits = rejectionCandidates.slice();

    if (!isNaN(snrMin)) hits = hits.filter(function(h) { return (h.snr || 0) >= snrMin; });
    if (!isNaN(driftMin)) hits = hits.filter(function(h) { return Math.abs(h.drift_rate || 0) >= driftMin; });
    if (!isNaN(driftMax)) hits = hits.filter(function(h) { return Math.abs(h.drift_rate || 0) <= driftMax; });
    if (!isNaN(freqMin)) hits = hits.filter(function(h) { return (h.freq || 0) >= freqMin; });
    if (!isNaN(freqMax)) hits = hits.filter(function(h) { return (h.freq || 0) <= freqMax; });

    hits.sort(function(a, b) {
        var col = resultsSort.col;
        var dir = resultsSort.dir === 'asc' ? 1 : -1;
        var av = a[col] || 0;
        var bv = b[col] || 0;
        if (typeof av === 'string' && typeof bv === 'string') {
            return av < bv ? -dir : (av > bv ? dir : 0);
        }
        return (av - bv) * dir;
    });
    
    // Cache the result
    window._filteredHitsCache = hits;
    window._filteredHitsCacheKey = cacheKey;
    return hits;
}

function applyFilters() {
    resultsPage = 0;
    renderHitTable();
    renderHitChart();
}

function toggleSort(col) {
    if (resultsSort.col === col) {
        resultsSort.dir = resultsSort.dir === 'asc' ? 'desc' : 'asc';
    } else {
        resultsSort.col = col;
        resultsSort.dir = 'desc';
    }
    updateSortIndicators();
    renderHitTable();
}

function updateSortIndicators() {
    var cols = ['snr', 'freq', 'drift_rate', 'channel'];
    for (var i = 0; i < cols.length; i++) {
        var el = document.getElementById('sort-' + cols[i]);
        if (el) {
            if (resultsSort.col === cols[i]) {
                el.textContent = resultsSort.dir === 'asc' ? ' \u25b2' : ' \u25bc';
            } else {
                el.textContent = '';
            }
        }
    }
}

function renderHitTable() {
    var tbody = document.getElementById('hit-table-body');
    var pagDiv = document.getElementById('results-pagination');
    var hits = getFilteredHits();

    if (hits.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="empty">No hits found.</td></tr>';
        pagDiv.style.display = 'none';
        return;
    }

    var totalPages = Math.max(1, Math.ceil(hits.length / resultsPageSize));
    if (resultsPage >= totalPages) resultsPage = totalPages - 1;
    if (resultsPage < 0) resultsPage = 0;
    var startIdx = resultsPage * resultsPageSize;
    var endIdx = Math.min(startIdx + resultsPageSize, hits.length);
    var pageData = hits.slice(startIdx, endIdx);

    var html = '';
    for (var i = 0; i < pageData.length; i++) {
        var h = pageData[i];
        var rowNum = startIdx + i + 1;
        var isOn = h.on_off === 'ON' || (h._source || '').indexOf('_S_') !== -1 || (h.source_file || '').indexOf('_S_') !== -1 || (h.file || '').indexOf('_S_') !== -1;
        var statusHtml = '';
        if (h.status === 'CANDIDATE') statusHtml = '<span style="color:#66bb6a;font-weight:bold;">CANDIDATE</span>';
        else if (h.status === 'RFI') statusHtml = '<span style="color:#ef5350;">RFI</span>';
        // Store hit data as data attributes for the click handler
        var hitJson = encodeURIComponent(JSON.stringify(h));
        html += '<tr class="hit-row" data-hit="' + hitJson + '" onclick="showWaterfall(this)" style="cursor:pointer;"><td>' + rowNum + '</td><td style="color:#66bb6a;font-weight:600;">' + (h.snr||0).toFixed(2) + '</td><td style="color:#4fc3f7;">' + (h.freq||0).toFixed(6) + '</td><td>' + (h.drift_rate||0).toFixed(4) + '</td><td>' + (h.channel||'-') + '</td><td><span class="on-badge ' + (isOn?'on':'off') + '">' + (isOn?'ON':'OFF') + '</span></td><td>' + statusHtml + '</td><td style="font-size:0.8em;color:#546e7a;">' + (h._source||h.file||'').substring(0,30) + '</td></tr>';
    }
    tbody.innerHTML = html;

    // Render pagination controls
    if (totalPages > 1) {
        pagDiv.style.display = 'flex';
        var html2 = '';
        html2 += '<button onclick="resultsPage=0;renderHitTable()"' + (resultsPage === 0 ? ' disabled' : '') + '>\u00ab First</button>';
        html2 += '<button onclick="resultsPage=Math.max(0,resultsPage-1);renderHitTable()"' + (resultsPage === 0 ? ' disabled' : '') + '>\u2039 Prev</button>';
        html2 += '<span class="bl-page-info">Page ' + (resultsPage + 1) + ' of ' + totalPages + ' (' + (startIdx + 1) + '-' + endIdx + ' of ' + hits.length + ')</span>';
        html2 += '<button onclick="resultsPage=Math.min(' + (totalPages - 1) + ',resultsPage+1);renderHitTable()"' + (resultsPage >= totalPages - 1 ? ' disabled' : '') + '>Next \u203a</button>';
        html2 += '<button onclick="resultsPage=' + (totalPages - 1) + ';renderHitTable()"' + (resultsPage >= totalPages - 1 ? ' disabled' : '') + '>Last \u00bb</button>';
        pagDiv.innerHTML = html2;
    } else {
        pagDiv.style.display = 'none';
    }
}

function renderHitChart() {
    var chartDiv = document.getElementById('hit-chart');
    // Always clear previous content (including stale empty-state messages)
    chartDiv.innerHTML = '';
    if (allHits.length === 0) {
        chartDiv.innerHTML = '<p style="text-align:center;color:#546e7a;padding:40px;">No hits to plot yet.</p>';
        return;
    }
    // Use shared filter function
    var hits = getFilteredHits();
    
    if (hits.length === 0) {
        chartDiv.innerHTML = '<p style="text-align:center;color:#546e7a;padding:40px;">No hits match the current filter.</p>';
        return;
    }
    
    var onHits = hits.filter(function(h) { return h.on_off === 'ON' || (h._source || '').indexOf('_S_') !== -1 || (h.source_file || '').indexOf('_S_') !== -1 || (h.file || '').indexOf('_S_') !== -1; });
    var offHits = hits.filter(function(h) { return h.on_off === 'OFF' || (h._source || '').indexOf('_R_') !== -1 || (h.source_file || '').indexOf('_R_') !== -1 || (h.file || '').indexOf('_R_') !== -1; });
    var traces = [{
        x: onHits.map(function(h) { return h.freq; }), y: onHits.map(function(h) { return h.snr; }),
        mode: 'markers', type: 'scatter', name: 'ON',
        marker: { color: '#66bb6a', size: 8, opacity: 0.7 },
        text: onHits.map(function(h) { return 'Drift: ' + (h.drift_rate||0).toFixed(4) + ' Hz/s'; }),
        hovertemplate: '%{x:.6f} MHz<br>SNR: %{y:.1f}<br>%{text}<extra></extra>',
    }];
    if (offHits.length > 0) {
        traces.push({
            x: offHits.map(function(h) { return h.freq; }), y: offHits.map(function(h) { return h.snr; }),
            mode: 'markers', type: 'scatter', name: 'OFF',
            marker: { color: '#ef5350', size: 8, opacity: 0.7 },
            text: offHits.map(function(h) { return 'Drift: ' + (h.drift_rate||0).toFixed(4) + ' Hz/s'; }),
            hovertemplate: '%{x:.6f} MHz<br>SNR: %{y:.1f}<br>%{text}<extra></extra>',
        });
    }
    Plotly.newPlot(chartDiv, traces, {
        paper_bgcolor: 'transparent', plot_bgcolor: '#0a0a1a',
        font: { color: '#c8c8e0', size: 11 },
        xaxis: { title: 'Frequency (MHz)', gridcolor: '#1e3a5f' },
        yaxis: { title: 'SNR', gridcolor: '#1e3a5f' },
        margin: { l: 50, r: 20, t: 10, b: 40 }, height: 250,
    }, { displayModeBar: false, responsive: true });
}

async function loadStats() {
    try {
        var url = '/api/stats';
        if (currentScanId) url += '?scan_id=' + encodeURIComponent(currentScanId);
        var resp = await fetch(url);
        var data = await resp.json();
        document.getElementById('stat-total').textContent = 'Total Hits: ' + data.total_hits;
        document.getElementById('stat-on').textContent = 'ON: ' + data.on_hits;
        document.getElementById('stat-off').textContent = 'OFF: ' + data.off_hits;
        document.getElementById('stat-top').textContent = 'Top SNR: ' + data.top_snr;
    } catch(e) {}
}

// ─── ON/OFF Rejection ───────────────────────────────────────────────
async function runRejection() {
    var btn = document.getElementById('btn-run-rejection');
    var summaryDiv = document.getElementById('rejection-summary');
    btn.disabled = true; btn.textContent = 'Running...';
    summaryDiv.innerHTML = '<p style="color:#8ab4f8;">Running ON/OFF rejection...</p>';
    var params = {
        tolerance_mhz: parseFloat(document.getElementById('reject-freq-tol').value),
        drift_tolerance: parseFloat(document.getElementById('reject-drift-tol').value),
        source: currentScanId || 'validation_50mhz',
    };
    try {
        var resp = await fetch('/api/reject', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(params),
        });
        var data = await resp.json();
        if (data.error) { summaryDiv.innerHTML = '<p style="color:#ef5350;">Error: ' + data.error + '</p>'; return; }
        var s = data.summary;
        rejectionCandidates = data.candidates || [];
        summaryDiv.innerHTML = '<div class="rejection-stats">' +
            '<span class="rstat"><span class="rstat-label">Total ON</span><span class="rstat-val">' + s.total_on.toLocaleString() + '</span></span>' +
            '<span class="rstat"><span class="rstat-label">Total OFF</span><span class="rstat-val">' + s.total_off.toLocaleString() + '</span></span>' +
            '<span class="rstat"><span class="rstat-label">Rejected (RFI)</span><span class="rstat-val" style="color:#ef5350;">' + s.rejected_rfi.toLocaleString() + ' (' + s.rejection_rate + '%)</span></span>' +
            '<span class="rstat"><span class="rstat-label">Candidates</span><span class="rstat-val" style="color:' + (s.candidates > 0 ? '#66bb6a' : '#546e7a') + ';font-weight:bold;font-size:1.3em;">' + s.candidates.toLocaleString() + '</span></span></div>';
        if (s.candidates > 0) document.getElementById('results-filter').value = 'candidates';
        renderHitTable(); renderHitChart();
        // Refresh scan list to pick up updated meta
        loadScansList();
    } catch(e) {
        summaryDiv.innerHTML = '<p style="color:#ef5350;">Error: ' + e.message + '</p>';
    } finally { btn.disabled = false; btn.textContent = 'Run ON/OFF Rejection'; }
}

// ─── File Download ───────────────────────────────────────────────────
async function downloadFile(url, filename) {
    try {
        var resp = await fetch('/api/download', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({url: url, filename: filename}),
        });
        var data = await resp.json();
        if (data.error) { alert(data.error); }
        else if (data.status === 'exists') { alert(filename + ' already exists'); loadLocalData(); }
        else { showDownloadPanel(); }
    } catch(e) { alert('Download error: ' + e.message); }
}

function showDownloadPanel() {
    var panel = document.getElementById('download-panel');
    if (panel) panel.style.display = 'block';
}

var lastDownloadCount = -1;
var lastDownloadActive = false;
var auditRunning = false;   // keeps Downloads panel open while an audit runs

async function pollDownloadStatus() {
    try {
        var resp = await fetch('/api/download/status');
        var data = await resp.json();
        var panel = document.getElementById('download-panel');
        if (!panel) return;
        
        // Fix 9: Only reload local data if something actually changed
        var currentCount = data.queue.length;
        var wasActive = lastDownloadActive;
        var nowActive = data.active;
        var countChanged = currentCount !== lastDownloadCount;
        var transitionedToInactive = wasActive && !nowActive;
        
        if (data.queue.length === 0) { panel.style.display = 'none'; 
            if (countChanged && lastDownloadCount > 0) {
                lastDownloadCount = currentCount;
                lastDownloadActive = nowActive;
                loadLocalData();
            }
            lastDownloadCount = currentCount;
            lastDownloadActive = nowActive;
            return;
        }
        panel.style.display = 'block';
        var activeDl = data.queue.filter(function(q) { return q.status === 'downloading' || q.status === 'queued'; });
        var completedDl = data.queue.filter(function(q) { return q.status === 'complete'; });
        var html = '<div class="panel-header" style="font-size:0.85em;">\u2b07 Downloads <button class="btn-small" style="float:right;" onclick="clearDownloads()">Clear</button></div><div class="download-list">';
        for (var i = 0; i < data.queue.length; i++) {
            var q = data.queue[i];
            var sc = q.status === 'downloading' ? '#4fc3f7' : q.status === 'complete' ? '#66bb6a' : q.status === 'error' ? '#ef5350' : '#546e7a';
            var sz = q.size_total > 0 ? (q.size_total / 1e9).toFixed(1) + ' GB' : '?';
            var done = (q.size_done / 1e9).toFixed(1) + ' GB';
            html += '<div class="download-item"><div class="dl-item-header"><span class="dl-item-name">' + q.filename + '</span><span class="dl-item-status" style="color:' + sc + ';">' + q.status + '</span></div>';
            if (q.status === 'downloading') {
                html += '<div class="dl-progress-bar"><div class="dl-progress-fill" style="width:' + q.progress + '%"></div></div>';
                html += '<div class="dl-item-stats">' + done + ' / ' + sz + ' (' + q.progress + '%)';
                if (q.speed_mbs > 0) {
                    html += ' | ' + q.speed_mbs + ' MB/s';
                    if (q.eta_s > 0) { html += ' | ETA ' + Math.floor(q.eta_s/60) + 'm ' + (q.eta_s%60) + 's'; }
                }
                html += ' <button class="btn-small" onclick="cancelDownload(\'' + q.filename + '\')">Cancel</button></div>';
            } else if (q.status === 'complete') {
                html += '<div class="dl-item-stats" style="color:#66bb6a;">' + sz + ' downloaded</div>';
            } else if (q.status === 'error') {
                html += '<div class="dl-item-stats" style="color:#ef5350;">' + (q.error || 'Error') + '</div>';
            }
            html += '</div>';
        }
        panel.innerHTML = html + '</div>';
        // Fix 9: Only reload local data when a download just completed or count changed
        if (transitionedToInactive || (countChanged && !nowActive && currentCount < lastDownloadCount)) {
            loadLocalData();
        }
        lastDownloadCount = currentCount;
        lastDownloadActive = nowActive;
    } catch(e) {}
}

async function cancelDownload(filename) {
    try { await fetch('/api/download/cancel', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({filename: filename}) }); } catch(e) {}
}

async function clearDownloads() {
    try {
        await fetch('/api/download/clear', { method: 'POST' });
        pollDownloadStatus();
    } catch(e) {}
}

// ������ Epoch Audit ����������������������������������
async function startEpochAudit() {
    var inp = document.getElementById('audit-epoch-input');
    var epoch = inp ? inp.value.trim() : '';
    if (!/^\d{5}$/.test(epoch)) { alert('Enter a 5-digit epoch number, e.g. 57910'); return; }
    try {
        var resp = await fetch('/api/audit/run', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ epoch: epoch }),
        });
        var data = await resp.json();
        if (data.error) { alert(data.error); return; }
        auditPollStatus();
    } catch(e) { alert('Audit start failed: ' + e.message); }
}

var _auditLastHtml = '';
function auditRenderStatus() {
    var el = document.getElementById('audit-status-line');
    if (!el) return;
    el.innerHTML = _auditLastHtml;
}

async function auditPollStatus() {
    try {
        var resp = await fetch('/api/audit/status');
        var d = await resp.json();
        auditRunning = !!d.active;
        var h = '';
        if (d.active) {
            var pct = d.total > 0 ? Math.round(100 * d.progress / d.total) : 0;
            h = '<span style="color:#4fc3f7;">' + escapeHtml(String(d.epoch)) + ': ' +
                escapeHtml(d.stage || 'starting') + ' (' + pct + '%)</span>';
        } else if (d.error) {
            h = '<span style="color:#ef5350;">error: ' + escapeHtml(d.error) + '</span>';
        } else if (d.result) {
            var zones = d.result.zones_written || [];
            if (zones.length > 0) {
                var rng = zones.map(function(z) { return z.f_start.toFixed(1) + '-' + z.f_stop.toFixed(1); }).join(', ');
                h = '<span style="color:#ffb74d;">ZONED ' + escapeHtml(rng) + ' MHz (' +
                    (d.result.flagged_windows || []).length + ' windows flagged)</span>';
            } else {
                h = '<span style="color:#66bb6a;">\u2713 CLEAN: no RFI zones found</span>';
            }
        }
        if (h !== _auditLastHtml) { _auditLastHtml = h; auditRenderStatus(); }
    } catch(e) {}
}

async function deleteFile(path, name) {
    if (!confirm('Delete "' + name + '"?\nFile will be moved to trash.')) return;
    try {
        var resp = await fetch('/api/delete', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path: path}) });
        var data = await resp.json();
        if (data.error) { alert(data.error); }
        else { selectedFiles.delete(path); loadLocalData(); refreshSkyMapMarkers(); }
    } catch(e) { alert('Error: ' + e.message); }
}

function autoFillBandRange() {
    // Try to read the band range from the first selected file's header
    var bandFound = false;
    for (var path in fileHeaderCache) {
        var h = fileHeaderCache[path];
        if (h && h.header) {
            var fch1 = h.header.fch1;
            var nchans = h.header.nchans;
            var foff = h.header.foff;
            if (typeof fch1 === 'number' && typeof nchans === 'number' && typeof foff === 'number') {
                var fEnd = fch1 + nchans * foff;
                var fMin = Math.min(fch1, fEnd);
                var fMax = Math.max(fch1, fEnd);
                document.getElementById('ctrl-f-start').value = fMin.toFixed(1);
                document.getElementById('ctrl-f-stop').value = fMax.toFixed(1);
                bandFound = true;
                return;
            }
        }
    }
    // Fallback: if no headers loaded, clear the fields for auto-detect
    document.getElementById('ctrl-f-start').value = '';
    document.getElementById('ctrl-f-stop').value = '';
}

function escapeHtml(text) { var d = document.createElement('div'); d.textContent = text; return d.innerHTML; }

// ─── Waterfall Modal ───────────────────────────────────────────────

function resolveHitFile(h) {
    // Try source_file first, then derive from _source
    if (h.source_file) {
        // source_file should be like 'Parkes_57791_72989_PROXCEN_S_fine.h5'
        // Prepend 'fine/' if it's a bare filename
        if (h.source_file.indexOf('/') === -1) {
            return 'fine/' + h.source_file;
        }
        return h.source_file;
    }
    // Derive from _source (JSON filename like 'Parkes_57791_72989_PROXCEN_S_fine_hits.json')
    if (h._source) {
        var base = h._source.replace('_partial_hits.json', '.h5').replace('_hits.json', '.h5');
        if (base.indexOf('/') === -1) {
            return 'fine/' + base;
        }
        return base;
    }
    // Try h.file
    if (h.file) {
        var base2 = h.file.replace(/_partial\.(json|txt)/, '.$1').replace(/\.(json|txt)$/, '.h5');
        if (base2.indexOf('/') === -1) return 'fine/' + base2;
        return base2;
    }
    return null;
}

function showWaterfall(rowEl) {
    var hitJson = rowEl.getAttribute('data-hit');
    if (!hitJson) return;
    var h;
    try { h = JSON.parse(decodeURIComponent(hitJson)); }
    catch(e) { console.error('Failed to parse hit data', e); return; }

    var modal = document.getElementById('waterfall-modal');
    var title = document.getElementById('waterfall-title');
    var metaDiv = document.getElementById('waterfall-meta');
    var bodyDiv = document.getElementById('waterfall-body');

    var freq = h.freq || 0;
    var snr = h.snr || 0;
    var drift = h.drift_rate || 0;
    var isOn = h.on_off === 'ON' || (h._source || '').indexOf('_S_') !== -1 || (h.source_file || '').indexOf('_S_') !== -1 || (h.file || '').indexOf('_S_') !== -1;
    var sourceFile = h._source || h.source_file || h.file || '?';

    // Set title
    title.textContent = 'Waterfall — ' + freq.toFixed(6) + ' MHz';

    // Show metadata
    var metaHtml = '';
    metaHtml += '<div class="wm-item"><span class="wm-label">Freq:</span><span class="wm-val">' + freq.toFixed(6) + ' MHz</span></div>';
    metaHtml += '<div class="wm-item"><span class="wm-label">SNR:</span><span class="wm-val" style="color:#66bb6a;">' + snr.toFixed(2) + '</span></div>';
    metaHtml += '<div class="wm-item"><span class="wm-label">Drift:</span><span class="wm-val">' + drift.toFixed(4) + ' Hz/s</span></div>';
    metaHtml += '<div class="wm-item"><span class="wm-label">Channel:</span><span class="wm-val">' + (h.channel || '-') + '</span></div>';
    metaHtml += '<div class="wm-item"><span class="wm-label">Cadence:</span><span class="wm-val"><span class="on-badge ' + (isOn?'on':'off') + '">' + (isOn?'ON':'OFF') + '</span></span></div>';
    if (h.status) {
        var statColor = h.status === 'CANDIDATE' ? '#66bb6a' : '#ef5350';
        metaHtml += '<div class="wm-item"><span class="wm-label">Status:</span><span class="wm-val" style="color:' + statColor + ';">' + h.status + '</span></div>';
    }
    metaHtml += '<div class="wm-item" style="flex-basis:100%;"><span class="wm-label">File:</span><span class="wm-val" style="font-size:0.9em;">' + sourceFile + '</span></div>';
    metaDiv.innerHTML = metaHtml;

    // Show loading spinner
    bodyDiv.innerHTML = '<div class="waterfall-loading"><div class="spinner"></div><div>Loading waterfall data...</div><div style="font-size:0.8em;color:#546e7a;margin-top:4px;">Reading from 12 GB HDF5 file</div></div>';

    // Show modal
    modal.style.display = 'flex';

    // Resolve the HDF5 file path
    var filePath = resolveHitFile(h);
    if (!filePath) {
        bodyDiv.innerHTML = '<div class="waterfall-error">Could not determine HDF5 source file for this hit.</div>';
        return;
    }

    // Fetch waterfall data
    var url = '/api/waterfall?file=' + encodeURIComponent(filePath) +
              '&freq_mhz=' + freq +
              '&width_chans=200&max_tints=20';

    fetch(url).then(function(resp) { return resp.json(); }).then(function(data) {
        if (data.error) {
            bodyDiv.innerHTML = '<div class="waterfall-error">Error loading waterfall: ' + escapeHtml(data.error) + '</div>';
            return;
        }
        renderWaterfallPlot(data, freq, drift);
    }).catch(function(err) {
        bodyDiv.innerHTML = '<div class="waterfall-error">Fetch error: ' + escapeHtml(err.message) + '</div>';
    });
}

function renderWaterfallPlot(data, centerFreq, driftRate) {
    var bodyDiv = document.getElementById('waterfall-body');
    bodyDiv.innerHTML = '<div id="waterfall-plot" style="width:100%;height:400px;"></div>';
    var plotDiv = document.getElementById('waterfall-plot');

    var z = data.data;        // 2D array: time x freq (now in dB above median)
    var freqs = data.freqs;   // MHz
    var times = data.times;   // seconds

    // Data is already dB above median from backend, use fixed range
    var zmin = -2;  // 2 dB below median
    var zmax = 10;  // 10 dB above median (bright signals)

    var traces = [{
        type: 'heatmap',
        z: z,
        x: freqs,
        y: times,
        zmin: zmin,
        zmax: zmax,
        colorscale: 'Viridis',
        reversescale: true,
        hovertemplate: '%{x:.6f} MHz<br>t=%{y:.1f}s<br>Power=%{z:.2f}<extra></extra>',
        name: 'Power'
    }];

    // Drift rate overlay: at time t, freq shifts by driftRate * t / 1e6 MHz
    if (driftRate && Math.abs(driftRate) > 0.001) {
        var driftFreqs = times.map(function(t) {
            return centerFreq + driftRate * t / 1e6;
        });
        traces.push({
            type: 'scatter',
            mode: 'lines',
            x: driftFreqs,
            y: times,
            line: { color: '#ff4444', width: 2, dash: 'dashdot' },
            name: 'Drift track',
            hovertemplate: '%{x:.6f} MHz<br>t=%{y:.1f}s<extra>Drift track</extra>'
        });
    }

    // Center frequency marker (vertical line)
    traces.push({
        type: 'scatter',
        mode: 'lines',
        x: [centerFreq, centerFreq],
        y: [times[0], times[times.length - 1]],
        line: { color: '#ffff00', width: 1, dash: 'dot' },
        name: 'Hit freq',
        hoverinfo: 'skip',
        showlegend: false
    });

    // Determine bandwidth and pick appropriate units
    var bw = Math.abs(freqs[freqs.length - 1] - freqs[0]);
    var usekHz = bw < 1.0;  // Under 1 MHz, use kHz

    var xaxisConfig;
    if (usekHz) {
        // Convert freqs to kHz for display
        var freqs_kHz = freqs.map(function(f) { return (f - freqs[0]) * 1000; });
        // Override traces x data to kHz relative offset
        // Actually, easier: just change the tickformat and title
        xaxisConfig = {
            title: 'Frequency (kHz, offset from ' + freqs[0].toFixed(6) + ' MHz)',
            gridcolor: '#1e3a5f',
            tickformat: '.1f',
        };
    } else {
        xaxisConfig = {
            title: 'Frequency (MHz)',
            gridcolor: '#1e3a5f',
            tickformat: '.4f',
        };
    }

    var layout = {
        paper_bgcolor: 'transparent',
        plot_bgcolor: '#0a0a1a',
        font: { color: '#c8c8e0', size: 11 },
        xaxis: xaxisConfig,
        yaxis: {
            title: 'Time (s)',
            gridcolor: '#1e3a5f',
            autorange: 'reversed',  // time goes top to bottom
        },
        margin: { l: 60, r: 20, t: 30, b: 50 },
        height: 400,
        legend: {
            orientation: 'h',
            y: 1.08,
            font: { size: 10 }
        },
        annotations: [{
            x: centerFreq,
            y: times[0],
            xref: 'x',
            yref: 'y',
            text: '★ ' + centerFreq.toFixed(6) + ' MHz',
            showarrow: true,
            arrowhead: 2,
            arrowsize: 1,
            arrowwidth: 1,
            arrowcolor: '#ffff00',
            font: { color: '#ffff00', size: 10 },
            ax: 0,
            ay: -20
        }]
    };

    Plotly.newPlot(plotDiv, traces, layout, {
        displayModeBar: true,
        responsive: true,
        modeBarButtonsToRemove: ['lasso2d', 'autoScale2d']
    });
}

function closeWaterfallModal() {
    var modal = document.getElementById('waterfall-modal');
    modal.style.display = 'none';
    // Purge plot to free memory
    var plotDiv = document.getElementById('waterfall-plot');
    if (plotDiv) Plotly.purge(plotDiv);
}

// ─── Barycentric Correction ─────────────────────────────────────────

var baryTargets = {};
var baryCorrectedPage = 1;
var baryCorrectedPerPage = 200;

async function loadBarycentricTargets() {
    try {
        var resp = await fetch('/api/barycentric/targets');
        var data = await resp.json();
        var select = document.getElementById('bary-target-select');
        var html = '';
        baryTargets = {};
        for (var i = 0; i < data.targets.length; i++) {
            var t = data.targets[i];
            baryTargets[t.name] = {ra: t.ra_hours, dec: t.dec_deg};
            html += '<option value="' + t.name + '">' + t.name + '</option>';
        }
        if (!data.targets.length) {
            html = '<option value="">No targets in registry</option>';
        }
        select.innerHTML = html;
        // Registry-only (Custom retired): auto-select the first target
        // and display its registry coordinates
        if (data.targets.length > 0) {
            select.value = data.targets[0].name;
            document.getElementById('bary-ra').value = data.targets[0].ra_hours;
            document.getElementById('bary-dec').value = data.targets[0].dec_deg;
        }
        // Store which scans have barycentric correction completed
        window.correctedScanIds = data.corrected_scans || [];
        // Store detailed status (complete vs partial)
        window.correctedScanStatus = data.corrected_scans_status || [];
        // Refresh the cross-epoch checkboxes now that we know which scans are corrected
        updateBaryScanCheckboxes();
    } catch(e) {
        console.error('Error loading bary targets:', e);
    }
}

function onBaryTargetSelected() {
    var select = document.getElementById('bary-target-select');
    var target = select.value;
    if (target && baryTargets[target]) {
        document.getElementById('bary-ra').value = baryTargets[target].ra;
        document.getElementById('bary-dec').value = baryTargets[target].dec;
    }
    // Re-filter the Cross-Epoch scan list for the selected target
    updateBaryScanCheckboxes();
}

function updateBaryScanCheckboxes() {
    var container = document.getElementById('bary-scan-checkboxes');
    if (!container) return;
    // Filter by the Barycentric Target dropdown above this list
    var sel = document.getElementById('bary-target-select');
    var activeTarget = (sel && sel.value) ? sel.value.toUpperCase() : '';
    if (scansList.length === 0) {
        container.innerHTML = '<span style="color:#546e7a;font-size:0.85em;">No scans available.</span>';
        return;
    }
    // Only show scans that have barycentric correction completed
    // correctedScanIds is populated by loadBarycentricTargets()
    if (!window.correctedScanIds || window.correctedScanIds.length === 0) {
        container.innerHTML = '<span style="color:#546e7a;font-size:0.85em;">No barycentrically corrected scans yet. Run correction first.</span>';
        return;
    }
    // Build a lookup for status info
    var statusMap = {};
    if (window.correctedScanStatus) {
        for (var si = 0; si < window.correctedScanStatus.length; si++) {
            statusMap[window.correctedScanStatus[si].scan_id] = window.correctedScanStatus[si];
        }
    }
    var html = '';
    for (var i = 0; i < scansList.length; i++) {
        var s = scansList[i];
        var sid = s.scan_id;
        // Skip scans for other targets (target from row, else scan_id prefix)
        var scanTarget = (s.target || sid.split('_')[0] || '').toUpperCase();
        if (activeTarget && scanTarget !== activeTarget) continue;
        // Skip scans that haven't been corrected
        if (window.correctedScanIds.indexOf(sid) === -1) continue;
        var label = scanLabel(s);
        if (label.length > 40) label = label.substring(0, 37) + '...';
        var status = statusMap[sid];
        var partialBadge = '';
        if (status && !status.complete) {
            partialBadge = ' <span class="bary-partial-badge" title="Partial correction: ' +
                (status.bary_hits || 0) + ' / ' + (status.raw_hits || 0) + ' hits">partial</span>';
        }
        html += '<span class="bary-scan-checkbox" data-scan-id="' + sid + '">';
        html += '<input type="checkbox" value="' + sid + '"> ';
        html += '<span class="bary-scan-label">' + label + partialBadge + '</span>';
        html += '<button class="bary-delete-btn" title="Delete barycentric correction for this scan" onclick="deleteBarycentric(\'' + sid + '\')">✕</button>';
        html += '</span>';
    }
    if (!html) {
        container.innerHTML = '<span style="color:#546e7a;font-size:0.85em;">No corrected scans for ' + escapeHtml(activeTarget || 'any target') + ' yet. Run correction first.</span>';
        return;
    }
    container.innerHTML = html;
    // Auto-check the currently selected scan
    if (currentScanId) {
        var cb = container.querySelector('input[value="' + currentScanId + '"]');
        if (cb) {
            cb.checked = true;
            cb.closest('.bary-scan-checkbox').classList.add('checked');
        }
    }
    // Add change listeners
    var checkboxes = container.querySelectorAll('input[type="checkbox"]');
    for (var j = 0; j < checkboxes.length; j++) {
        checkboxes[j].onchange = function() {
            if (this.checked) {
                this.closest('.bary-scan-checkbox').classList.add('checked');
            } else {
                this.closest('.bary-scan-checkbox').classList.remove('checked');
            }
        };
    }
}

async function deleteBarycentric(scanId) {
    if (!scanId) return;
    var shortId = scanId.length > 40 ? scanId.substring(0, 37) + '...' : scanId;
    if (!confirm('Delete barycentric correction for ' + shortId + '?\n\nThis removes all corrected data. You can re-run correction later.')) {
        return;
    }
    try {
        var resp = await fetch('/api/barycentric/delete/' + scanId, {
            method: 'DELETE',
        });
        var data = await resp.json();
        if (data.error) {
            alert('Error: ' + data.error);
            return;
        }
        // Refresh the scan list
        await loadBarycentricTargets();
        // Show brief success message
        var statusDiv = document.getElementById('bary-status');
        if (statusDiv) {
            statusDiv.innerHTML = '<span style="color:#66bb6a;">✓ Deleted barycentric correction for ' + shortId + '</span>';
            setTimeout(function() {
                if (statusDiv.innerHTML.indexOf('Deleted barycentric') !== -1) {
                    statusDiv.innerHTML = '';
                }
            }, 4000);
        }
    } catch(e) {
        alert('Error deleting barycentric correction: ' + e.message);
    }
}

async function runBarycentric() {
    var btn = document.getElementById('btn-bary-correct');
    var statusDiv = document.getElementById('bary-status');
    btn.disabled = true;
    btn.textContent = 'Correcting...';
    statusDiv.innerHTML = '<span style="color:#8ab4f8;">Computing barycentric correction...</span>';

    var params = {
        scan_id: currentScanId,
        target: document.getElementById('bary-target-select').value || null,
        telescope: document.getElementById('bary-telescope').value,
        // Coordinates intentionally NOT sent: the backend resolves them
        // from the scan's target via the registry (single source of truth).
    };

    try {
        var resp = await fetch('/api/barycentric/correct', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(params),
        });
        var data = await resp.json();
        if (data.error) {
            statusDiv.innerHTML = '<span style="color:#ef5350;">Error: ' + escapeHtml(data.error) + '</span>';
            return;
        }
        statusDiv.innerHTML = '<span style="color:#66bb6a;">✓ Corrected ' + data.files_corrected +
            ' files, ' + data.total_hits.toLocaleString() + ' hits. Velocity corrections:</span>';
        for (var fname in data.corrections) {
            var v = data.corrections[fname];
            var shift_mhz = (v / 299792458 * 3000).toFixed(4); // approx shift at 3 GHz
            statusDiv.innerHTML += '<br><span style="color:#546e7a;font-size:0.85em;">' +
                fname.substring(0, 40) + ': v=' + v.toFixed(1) + ' m/s (' + (v/1000).toFixed(2) +
                ' km/s)</span>';
        }
        // Load results
        document.getElementById('bary-results-container').style.display = 'block';
        switchBaryTab('corrected');
        baryCorrectedPage = 1;
        loadBaryCorrectedResults(currentScanId);
    } catch(e) {
        statusDiv.innerHTML = '<span style="color:#ef5350;">Error: ' + escapeHtml(e.message) + '</span>';
    } finally {
        btn.disabled = false;
        btn.textContent = 'Run Correction';
    }
}

async function loadBaryCorrectedResults(scanId, page) {
    if (!page) page = baryCorrectedPage || 1;
    var snrMin = 0;
    try {
        // Use fast SQLite endpoint
        var limit = baryCorrectedPerPage || 500;
        var offset = (page - 1) * limit;
        var resp = await fetch('/api/db/scans/' + encodeURIComponent(scanId) +
            '/corrected?min_snr=' + snrMin + '&limit=' + limit + '&offset=' + offset);
        var data = await resp.json();
        if (data.error) {
            // Fallback to legacy JSON endpoint
            return loadBaryCorrectedResultsLegacy(scanId, page);
        }

        // Render summary
        var summaryHtml = '';
        summaryHtml += '<div class="bs-item"><span class="bs-label">Target:</span><span class="bs-val">' + (data.target || '?') + '</span></div>';
        summaryHtml += '<div class="bs-item"><span class="bs-label">Total hits:</span><span class="bs-val">' + (data.total_hits || 0).toLocaleString() + '</span></div>';
        summaryHtml += '<div class="bs-item"><span class="bs-label">Bary corrected:</span><span class="bs-val">' + (data.bary_corrected ? '✓' : '✗') + '</span></div>';
        summaryHtml += '<div class="bs-item"><span class="bs-label">Showing:</span><span class="bs-val">' + (data.total_filtered || 0).toLocaleString() + '</span></div>';
        document.getElementById('bary-corrected-summary').innerHTML = summaryHtml;

        // Render table
        var hits = data.hits || [];
        if (hits.length === 0) {
            document.getElementById('bary-corrected-tbody').innerHTML =
                '<tr><td colspan="8" class="empty">No hits.</td></tr>';
            document.getElementById('bary-corrected-pagination').style.display = 'none';
            return;
        }

        var startIdx = offset;
        var html = '';
        for (var i = 0; i < hits.length; i++) {
            var h = hits[i];
            var rowNum = startIdx + i + 1;
            var isOn = (h.source_file || '').indexOf('_S_') !== -1 || h.on_off === 'ON';
            var obsFreq = h.freq || 0;
            var baryFreq = h.barycentric_freq || 0;
            var deltaHz = (baryFreq - obsFreq) * 1e6; // MHz to Hz
            html += '<tr>' +
                '<td>' + rowNum + '</td>' +
                '<td style="color:#4fc3f7;">' + baryFreq.toFixed(8) + '</td>' +
                '<td>' + obsFreq.toFixed(6) + '</td>' +
                '<td style="color:' + (deltaHz > 0 ? '#66bb6a' : '#ef5350') + ';font-size:0.85em;">' +
                    (deltaHz > 0 ? '+' : '') + deltaHz.toFixed(1) + '</td>' +
                '<td>' + (h.drift_rate || 0).toFixed(4) + '</td>' +
                '<td style="color:#66bb6a;font-weight:600;">' + (h.snr || 0).toFixed(2) + '</td>' +
                '<td><span class="on-badge ' + (isOn ? 'on' : 'off') + '">' + (isOn ? 'ON' : 'OFF') + '</span></td>' +
                '<td style="font-size:0.8em;color:#546e7a;">' + (h.source_file || h.file || '').substring(0, 30) + '</td>' +
                '</tr>';
        }
        document.getElementById('bary-corrected-tbody').innerHTML = html;

        // Pagination
        var total = data.total_filtered || 0;
        var totalPages = Math.max(1, Math.ceil(total / limit));
        var currentPage = page;
        var pagDiv = document.getElementById('bary-corrected-pagination');
        if (totalPages > 1) {
            pagDiv.style.display = 'flex';
            var phtml = '';
            phtml += '<button onclick="baryCorrectedPage=1;loadBaryCorrectedResults(\'' + scanId + '\')"' + (currentPage === 1 ? ' disabled' : '') + '>« First</button>';
            phtml += '<button onclick="baryCorrectedPage=Math.max(1,' + (currentPage - 1) + ');loadBaryCorrectedResults(\'' + scanId + '\')"' + (currentPage === 1 ? ' disabled' : '') + '>‹ Prev</button>';
            phtml += '<span class="bl-page-info">Page ' + currentPage + ' of ' + totalPages +
                ' (' + (startIdx + 1) + '-' + Math.min(startIdx + limit, total) +
                ' of ' + total.toLocaleString() + ')</span>';
            phtml += '<button onclick="baryCorrectedPage=Math.min(' + totalPages + ',' + (currentPage + 1) + ');loadBaryCorrectedResults(\'' + scanId + '\')"' + (currentPage >= totalPages ? ' disabled' : '') + '>Next ›</button>';
            phtml += '<button onclick="baryCorrectedPage=' + totalPages + ';loadBaryCorrectedResults(\'' + scanId + '\')"' + (currentPage >= totalPages ? ' disabled' : '') + '>Last »</button>';
            pagDiv.innerHTML = phtml;
        } else {
            pagDiv.style.display = 'none';
        }
    } catch(e) {
        console.error('Error loading bary results:', e);
    }
}

async function runCrossEpoch() {
    var btn = document.getElementById('btn-cross-epoch');
    btn.disabled = true;
    btn.textContent = 'Searching...';

    // Get selected scans
    var container = document.getElementById('bary-scan-checkboxes');
    var checked = container.querySelectorAll('input[type="checkbox"]:checked');
    var scanIds = [];
    for (var i = 0; i < checked.length; i++) {
        scanIds.push(checked[i].value);
    }

    if (scanIds.length < 2) {
        alert('Select at least 2 scans for cross-epoch comparison.');
        btn.disabled = false;
        btn.textContent = 'Run Cross-Epoch Search';
        return;
    }

    var params = {
        scan_ids: scanIds,
        freq_tolerance_hz: parseFloat(document.getElementById('bary-tolerance-hz').value) || 10,
        min_epochs: parseInt(document.getElementById('bary-min-epochs').value) || 2,
        min_snr: parseFloat(document.getElementById('bary-min-snr').value) || 0,
        force_rerun: true,
    };

    // Show loading indicator
    var resultsContainer = document.getElementById('bary-results-container');
    if (resultsContainer) {
        resultsContainer.style.display = 'block';
        var tbody = document.getElementById('crossepoch-tbody');
        if (tbody) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:20px;color:#ffeb3b;">'
                + '<div style="display:inline-block;">'
                + '<div style="display:inline-block;width:24px;height:24px;border:3px solid #1e3a5f;border-top-color:#ffeb3b;border-radius:50%;animation:xf-spin 0.8s linear infinite;vertical-align:middle;margin-right:8px;"></div>'
                + 'Searching across ' + scanIds.length + ' scans... this may take 30-60 seconds'
                + '</div></td></tr>';
        }
    }

    try {
        // Use fast SQLite DB endpoint with extended timeout
        var controller = new AbortController();
        var timeoutId = setTimeout(function() { controller.abort(); }, 120000);

        var resp = await fetch('/api/db/cross-epoch', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(params),
            signal: controller.signal,
        });
        clearTimeout(timeoutId);

        var data = await resp.json();
        if (data.error) {
            // Fallback to legacy JSON endpoint
            var resp2 = await fetch('/api/barycentric/cross-epoch', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(params),
            });
            data = await resp2.json();
            if (data.error) { alert(data.error); return; }
        }

        document.getElementById('bary-results-container').style.display = 'block';
        switchBaryTab('cross');
        renderCrossEpochResults(data);
        // Refresh history dropdown with the new cached run
        loadCrossEpochHistory();
    } catch(e) {
        if (e.name === 'AbortError') {
            alert('Cross-epoch search timed out. Try increasing min_snr or reducing tolerance to speed up the query.');
        } else {
            alert('Error: ' + e.message);
        }
    } finally {
        btn.disabled = false;
        btn.textContent = 'Run Cross-Epoch Search';
    }
}

function renderCrossEpochResults(data) {
    var summary = data.summary || {};
    var candidates = data.candidates || [];

    // Render summary
    var summaryHtml = '';
    summaryHtml += '<div class="bs-item"><span class="bs-label">Scans:</span><span class="bs-val">' + summary.total_scans + '</span></div>';
    summaryHtml += '<div class="bs-item"><span class="bs-label">ON freqs checked:</span><span class="bs-val">' + (summary.total_on_frequencies || 0).toLocaleString() + '</span></div>';
        if (summary.freqs_meeting_min_epochs !== undefined) {
            summaryHtml += '<div class="bs-item"><span class="bs-label">Freqs in &ge;' + (summary.min_epochs || 2) + ' epochs:</span><span class="bs-val">' + (summary.freqs_meeting_min_epochs || 0).toLocaleString() + '</span></div>';
            summaryHtml += '<div class="bs-item"><span class="bs-label">Passing OFF veto:</span><span class="bs-val">' + (summary.total_candidates || 0).toLocaleString() + '</span></div>';
        }
    summaryHtml += '<div class="bs-item"><span class="bs-label">Candidates:</span><span class="bs-val ' + (candidates.length > 0 ? 'highlight' : '') + '">' + candidates.length + '</span></div>';
    summaryHtml += '<div class="bs-item"><span class="bs-label">Tolerance:</span><span class="bs-val">' + (summary.freq_tolerance_hz || 10) + ' Hz</span></div>';
    summaryHtml += '<div class="bs-item"><span class="bs-label">Min epochs:</span><span class="bs-val">' + (summary.min_epochs || 2) + '</span></div>';
    if (summary.min_snr && summary.min_snr > 0) {
        summaryHtml += '<div class="bs-item"><span class="bs-label">Min SNR:</span><span class="bs-val">' + summary.min_snr + '</span></div>';
    }
    document.getElementById('bary-cross-summary').innerHTML = summaryHtml;

    // Render candidate table
    var tbody = document.getElementById('bary-cross-tbody');
    if (candidates.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty">No cross-epoch candidates found. ' +
            'This is expected for RFI-dominated data. Try different tolerance or min_epochs settings.</td></tr>';
        return;
    }

    var html = '';
    for (var i = 0; i < candidates.length; i++) {
        var c = candidates[i];
        var obsFreqs = (c.observed_freqs_mhz || []).map(function(f) { return f.toFixed(4); }).join(', ');
        var drifts = (c.drift_rates || []).map(function(d) { return d.toFixed(4); }).join(', ');
        html += '<tr class="bary-candidate-row">' +
            '<td>' + (i + 1) + '</td>' +
            '<td style="color:#4fc3f7;font-weight:600;">' + c.barycentric_freq_mhz.toFixed(8) + '</td>' +
            '<td style="color:#66bb6a;font-weight:bold;">' + c.epoch_count + '</td>' +
            '<td style="color:#66bb6a;">' + (c.max_snr || 0).toFixed(2) + '</td>' +
            '<td>' + (c.mean_drift_rate || 0).toFixed(6) + '</td>' +
            '<td>' + (c.log_false_alarm_prob || 0).toFixed(2) + '</td>' +
            '<td style="font-size:0.8em;color:#546e7a;">' + obsFreqs + '</td>' +
            '</tr>';
    }
    tbody.innerHTML = html;
}

function switchBaryTab(tab) {
    var tabs = document.querySelectorAll('.bary-tab');
    for (var i = 0; i < tabs.length; i++) tabs[i].classList.remove('active');
    document.getElementById('bary-tab-' + tab).classList.add('active');
    document.getElementById('bary-corrected-panel').style.display = (tab === 'corrected') ? 'block' : 'none';
    document.getElementById('bary-cross-panel').style.display = (tab === 'cross') ? 'block' : 'none';
}

// ── Cross-Epoch History & Persistence ───────────────────────────────

async function loadCrossEpochHistory() {
    var select = document.getElementById('bary-cross-history');
    if (!select) return;
    
    try {
        // Use fast SQLite DB endpoint
        var resp = await fetch('/api/db/cross-epoch/history');
        var data = await resp.json();
        var runs = data.runs || [];
        
        // Also try legacy cache files
        if (!runs || runs.length === 0) {
            try {
                var resp2 = await fetch('/api/barycentric/cross-epoch/history');
                var legacyData = await resp2.json();
                runs = legacyData.runs || [];
            } catch(e2) {}
        }
        
        if (!runs || runs.length === 0) {
            select.innerHTML = '<option value="">No previous runs</option>';
            return;
        }
        
        var html = '<option value="">-- Select previous run --</option>';
        for (var i = 0; i < runs.length; i++) {
            var r = runs[i];
            // Recover epochs compared in this run: embedded in new scan ids,
            // absent for runs saved before 2026-08-15
            var eps = [];
            try {
                var sids = r.scan_ids || [];
                if (typeof sids === 'string') { sids = JSON.parse(sids || '[]'); }
                for (var k = 0; k < sids.length; k++) {
                    var em = /_(\d{5})_\d{4}-\d{2}-\d{2}/.exec(String(sids[k]));
                    if (em && eps.indexOf(em[1]) === -1) eps.push(em[1]);
                }
            } catch(e) {}
            var ts = r.created_at || r.timestamp || '';
            var label = (eps.length ? 'Ep ' + eps.join(',') + ' | ' : '') + ts.substring(0, 19).replace('T', ' ');
            label += ' | SNR ' + (r.min_snr || 0);
            label += ' | tol ' + (r.tolerance_hz || 10);
            label += ' | ' + (r.candidate_count || 0) + ' cand';
            var val = r.id || r.filename;
            html += '<option value="' + val + '">' + escapeHtml(label) + '</option>';
        }
        select.innerHTML = html;
        
        // Auto-load most recent run
        loadCrossEpochRun(runs[0].id || runs[0].filename);
    } catch(e) {
        console.error('Error loading cross-epoch history:', e);
        select.innerHTML = '<option value="">Error loading history</option>';
    }
}

async function loadCrossEpochRun(val) {
    if (!val) return;
    try {
        var resp;
        // Check if val is numeric (DB id) or string (legacy filename)
        if (/^\d+$/.test(val)) {
            resp = await fetch('/api/db/cross-epoch/' + val);
        } else {
            resp = await fetch('/api/barycentric/cross-epoch/load?file=' + encodeURIComponent(val));
        }
        var data = await resp.json();
        if (data.error) { alert(data.error); return; }
        document.getElementById('bary-results-container').style.display = 'block';
        switchBaryTab('cross');
        renderCrossEpochResults(data);
    } catch(e) {
        alert('Error loading cached run: ' + e.message);
    }
}

async function deleteCrossEpochRun() {
    var select = document.getElementById('bary-cross-history');
    if (!select || !select.value) {
        alert('Select a run to delete first.');
        return;
    }
    var val = select.value;
    if (/^\d+$/.test(val)) {
        // DB-cached run
        if (!confirm('Delete cross-epoch run #' + val + '?')) return;
        try {
            var resp = await fetch('/api/db/cross-epoch/' + val, { method: 'DELETE' });
            var data = await resp.json();
            if (data.error) { alert(data.error); return; }
            loadCrossEpochHistory();
        } catch(e) {
            alert('Delete failed: ' + e.message);
        }
    } else {
        // Legacy file-cached run
        if (!confirm('Delete cached run?')) return;
        try {
            var resp = await fetch('/api/barycentric/cross-epoch/delete?file=' + encodeURIComponent(val), { method: 'DELETE' });
            var data = await resp.json();
            if (data.error) { alert(data.error); return; }
            loadCrossEpochHistory();
        } catch(e) {
            alert('Delete failed: ' + e.message);
        }
    }
}

// ── Barycentric Corrected Hits: Load Cached ────────────────────────

async function loadCachedCorrected() {
    if (!currentScanId) {
        alert('Select a scan first.');
        return;
    }
    var statusDiv = document.getElementById('bary-status');
    statusDiv.innerHTML = '<span style="color:#8ab4f8;">Loading cached correction...</span>';
    
    try {
        // Use SQLite DB endpoint
        var resp = await fetch('/api/db/scans/' + encodeURIComponent(currentScanId) + '/corrected?limit=500&offset=0');
        if (resp.status === 404) {
            statusDiv.innerHTML = '<span style="color:#ef5350;">No cached correction found for this scan. Run correction first.</span>';
            return;
        }
        var data = await resp.json();
        if (data.error) {
            statusDiv.innerHTML = '<span style="color:#ef5350;">' + escapeHtml(data.error) + '</span>';
            return;
        }
        statusDiv.innerHTML = '<span style="color:#66bb6a;">\u2713 Loaded cached correction: ' +
            (data.total_hits || 0).toLocaleString() + ' hits.</span>';
        document.getElementById('bary-results-container').style.display = 'block';
        switchBaryTab('corrected');
        baryCorrectedPage = 1;
        loadBaryCorrectedResults(currentScanId);
    } catch(e) {
        statusDiv.innerHTML = '<span style="color:#ef5350;">Error: ' + escapeHtml(e.message) + '</span>';
    }
}

// ── Legacy barycentric results fallback ─────────────────────────────

async function loadBaryCorrectedResultsLegacy(scanId, page) {
    if (!page) page = baryCorrectedPage || 1;
    try {
        var resp = await fetch('/api/barycentric/results/' + encodeURIComponent(scanId) +
            '?page=' + page + '&per_page=' + (baryCorrectedPerPage || 500) + '&snr_min=0');
        var data = await resp.json();
        if (data.error) {
            document.getElementById('bary-corrected-tbody').innerHTML =
                '<tr><td colspan="8" class="empty">' + escapeHtml(data.error) + '</td></tr>';
            return;
        }
        var summaryHtml = '';
        summaryHtml += '<div class="bs-item"><span class="bs-label">Target:</span><span class="bs-val">' + (data.target || '?') + '</span></div>';
        summaryHtml += '<div class="bs-item"><span class="bs-label">Total hits:</span><span class="bs-val">' + (data.total_hits || 0).toLocaleString() + '</span></div>';
        summaryHtml += '<div class="bs-item"><span class="bs-label">Showing:</span><span class="bs-val">' + (data.total_filtered || 0).toLocaleString() + '</span></div>';
        document.getElementById('bary-corrected-summary').innerHTML = summaryHtml;
        var hits = data.hits || [];
        var startIdx = (data.page - 1) * data.per_page;
        var html = '';
        for (var i = 0; i < hits.length; i++) {
            var h = hits[i];
            var rowNum = startIdx + i + 1;
            var isOn = (h.source_file || '').indexOf('_S_') !== -1 || h.on_off === 'ON';
            var obsFreq = h.freq || 0;
            var baryFreq = h.barycentric_freq || 0;
            var deltaHz = (baryFreq - obsFreq) * 1e6;
            html += '<tr><td>' + rowNum + '</td>' +
                '<td style="color:#4fc3f7;">' + baryFreq.toFixed(8) + '</td>' +
                '<td>' + obsFreq.toFixed(6) + '</td>' +
                '<td style="color:' + (deltaHz > 0 ? '#66bb6a' : '#ef5350') + ';font-size:0.85em;">' +
                    (deltaHz > 0 ? '+' : '') + deltaHz.toFixed(1) + '</td>' +
                '<td>' + (h.drift_rate || 0).toFixed(4) + '</td>' +
                '<td style="color:#66bb6a;font-weight:600;">' + (h.snr || 0).toFixed(2) + '</td>' +
                '<td><span class="on-badge ' + (isOn ? 'on' : 'off') + '">' + (isOn ? 'ON' : 'OFF') + '</span></td>' +
                '<td style="font-size:0.8em;color:#546e7a;">' + (h.source_file || h.file || '').substring(0, 30) + '</td></tr>';
        }
        document.getElementById('bary-corrected-tbody').innerHTML = html;
    } catch(e) {
        console.error('Error loading legacy bary results:', e);
    }
}

// ── Hits table pagination (DB-backed) ───────────────────────────────

function renderHitsPagination() {
    // If the existing hit table has a pagination div, update it
    // For now, just note the total in the results filter area
    var filterDiv = document.getElementById('results-filter');
    if (filterDiv && allHitsTotal > allHits.length) {
        // Could add pagination controls here in the future
    }
}

// --- Epoch Archive (3B): move staged h5 files to D: archive -------------

var _archivePollTimer = null;

function archiveScanRender(d) {
    var btn = document.getElementById('btn-archive-scan');
    if (!btn) return;
    var lbl = 'Archive Epoch to D:';
    if (d.active) {
        lbl = 'Archiving ' + d.files_done + '/' + d.files_total + '...';
    } else if (d.stage === 'done' && d.last) {
        lbl = 'Archived OK';
    } else if (d.stage === 'error') {
        lbl = 'Archive FAILED';
    }
    btn.textContent = lbl;
    btn.disabled = !!d.active;
    btn.title = d.stage === 'error' ? ('Archive error: ' + (d.error || 'unknown')) :
        "Move this scan's h5 data files from G: SSD staging to the D: archive. Verifies sizes before freeing SSD space.";
}

async function archiveScanPoll() {
    try {
        var resp = await fetch('/api/archive/status');
        var d = await resp.json();
        archiveScanRender(d);
        if (_archivePollTimer && !d.active) {
            clearInterval(_archivePollTimer);
            _archivePollTimer = null;
        }
    } catch (e) { /* transient */ }
}

async function archiveCurrentScan() {
    var sel = document.getElementById('scan-selector');
    var scanId = sel ? sel.value : '';
    if (!scanId) { alert('Select a scan first.'); return; }
    if (!confirm('Archive this epoch\'s h5 files to D:\\seti_data\\TARGET\\fine?\n\nFiles are copied and size-verified; the G: staging copies are deleted only after the archive copies are confirmed.')) return;
    try {
        var resp = await fetch('/api/archive/epoch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ scan_id: scanId }),
        });
        var data = await resp.json();
        if (data.error) { alert(data.error); return; }
        if (_archivePollTimer) clearInterval(_archivePollTimer);
        _archivePollTimer = setInterval(archiveScanPoll, 2000);
        archiveScanPoll();
    } catch (e) { alert('Archive start failed: ' + e.message); }
}
