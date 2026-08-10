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
let scanStateActive = false; // Tracks whether a scan is currently running

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
    document.getElementById('btn-search-local').onclick = loadLocalData;
    document.getElementById('btn-start-scan').onclick = startScan;
    document.getElementById('btn-resume-scan').onclick = resumeScan;
    document.getElementById('btn-stop-scan').onclick = stopScan;
    document.getElementById('btn-refresh').onclick = () => { loadResults(); loadStats(); loadScansList(); };
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

    logInterval = setInterval(pollScanStatus, 3000);
    pollScanStatus();  // Fire immediately so button states sync on page load
    setInterval(pollDownloadStatus, 10000);

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

                var pt = null;
                try { pt = Celestial.mapProjection([t.ra * 15, t.dec]); } catch(e) { continue; }
                if (!pt) continue;

                var visible = false;
                try { visible = Celestial.clip([t.ra * 15, t.dec]); } catch(e) { visible = true; }
                if (!visible) continue;

                // Crosshair ring
                ctx.strokeStyle = "#ff6666";
                ctx.lineWidth = 1.5;
                ctx.globalAlpha = 0.7;
                ctx.beginPath();
                ctx.arc(pt[0], pt[1], 12, 0, 2 * Math.PI);
                ctx.stroke();

                // Solid dot
                ctx.globalAlpha = 1.0;
                ctx.fillStyle = "#ff4444";
                ctx.strokeStyle = "#ffffff";
                ctx.lineWidth = 1.5;
                ctx.beginPath();
                ctx.arc(pt[0], pt[1], 4, 0, 2 * Math.PI);
                ctx.fill();
                ctx.stroke();

                // Crosshair lines
                ctx.strokeStyle = "#ff6666";
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
                ctx.fillStyle = "#ff8888";
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
        html += '<p style="margin:2px 0;"><span style="color:#ff6666;">\u25cf</span> <strong>' + keys[i] + '</strong> &nbsp;RA: ' + formatRA(t.ra) + ', Dec: ' + formatDec(t.dec) + '</p>';
    }
    info.innerHTML = html;
}

// Also support BL API search click plotting a marker
function plotTargetOnMap(name, ra, dec) {
    addTargetMarker(name, ra, dec);
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
                    plotTargetOnMap(target.toUpperCase(), raHours, decDeg);
                }
            }
            renderBLResults();
        } else {
            resultsDiv.innerHTML = '<p style="color:#546e7a;">No observations found.</p>';
        }
    } catch(e) {
        resultsDiv.innerHTML = '<p style="color:#ef5350;">Error: ' + e.message + '</p>';
    }
}

function getFilteredBL() {
    return blResults.filter(function(obs) {
        var url = obs.url || '';
        var res = '';
        if (url.indexOf('_fine.') !== -1) res = 'fine';
        else if (url.indexOf('_mid.') !== -1) res = 'mid';
        else if (url.indexOf('_time.') !== -1) res = 'time';
        else if (url.indexOf('_coarse.') !== -1) res = 'coarse';

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

            html += '<div class="target-header"><strong style="color:#4fc3f7;">' + target + '</strong><span style="color:#546e7a;"> ' + totalCount + ' files</span></div>';

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
    var params = {
        target: 'PROXCEN', resolution: 'fine',
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
        var resp = await fetch('/api/scans');
        scansList = await resp.json();
        renderScanSelector();
        // Auto-select the most recent scan if none selected
        if (!currentScanId && scansList.length > 0) {
            currentScanId = scansList[0].scan_id;
            renderScanSelector();
            loadScanResults(currentScanId);
            loadScanStats(currentScanId);
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
    if (scansList.length === 0) {
        sel.innerHTML = '<option value="">No scans yet</option>';
        document.getElementById('scan-meta-display').innerHTML = '';
        return;
    }
    var html = '';
    for (var i = 0; i < scansList.length; i++) {
        var s = scansList[i];
        var label = s.scan_id;
        // Shorten label if too long
        if (label.length > 50) label = label.substring(0, 50);
        var selAttr = (currentScanId === s.scan_id) ? ' selected' : '';
        html += '<option value="' + s.scan_id + '"' + selAttr + '>' + label + '</option>';
    }
    sel.innerHTML = html;
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
        var resp = await fetch('/api/scans/' + encodeURIComponent(scanId) + '/results');
        var data = await resp.json();
        if (data.error) {
            console.error('Scan results error:', data.error);
            return;
        }
        // Render scan meta display
        renderScanMetaDisplay(data.meta || {}, data.results);
        
        // Process results into allHits
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
        
        // Load rejection results if present for this scan
        if (data.rejection && data.rejection.candidates) {
            rejectionCandidates = data.rejection.candidates;
            renderRejectionSummary(data.rejection.summary, data.rejection.parameters);
        } else {
            rejectionCandidates = [];
            document.getElementById('rejection-summary').innerHTML =
                '<p style="color:#546e7a;">No rejection run for this scan yet.</p>';
        }
        
        renderHitTable();
        renderHitChart();
    } catch(e) {
        console.error('Error loading scan results:', e);
    }
}

async function loadScanStats(scanId) {
    try {
        var resp = await fetch('/api/stats?scan_id=' + encodeURIComponent(scanId));
        var data = await resp.json();
        document.getElementById('stat-total').textContent = 'Total Hits: ' + data.total_hits;
        document.getElementById('stat-on').textContent = 'ON: ' + data.on_hits;
        document.getElementById('stat-off').textContent = 'OFF: ' + data.off_hits;
        document.getElementById('stat-top').textContent = 'Top SNR: ' + data.top_snr;
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
        var html = '<div class="panel-header" style="font-size:0.85em;">\u2b07 Downloads</div><div class="download-list">';
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
        var base = h._source.replace('_hits.json', '.h5');
        if (base.indexOf('/') === -1) {
            return 'fine/' + base;
        }
        return base;
    }
    // Try h.file
    if (h.file) {
        var base2 = h.file.replace(/\.(json|txt)$/, '.h5');
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
        var html = '<option value="">Custom...</option>';
        baryTargets = {};
        for (var i = 0; i < data.targets.length; i++) {
            var t = data.targets[i];
            baryTargets[t.name] = {ra: t.ra_hours, dec: t.dec_deg};
            html += '<option value="' + t.name + '">' + t.name + '</option>';
        }
        select.innerHTML = html;
        // Store which scans have barycentric correction completed
        window.correctedScanIds = data.corrected_scans || [];
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
}

function updateBaryScanCheckboxes() {
    var container = document.getElementById('bary-scan-checkboxes');
    if (!container) return;
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
    var html = '';
    for (var i = 0; i < scansList.length; i++) {
        var s = scansList[i];
        var sid = s.scan_id;
        // Skip scans that haven't been corrected
        if (window.correctedScanIds.indexOf(sid) === -1) continue;
        var label = sid;
        if (label.length > 40) label = label.substring(0, 37) + '...';
        html += '<label class="bary-scan-checkbox" data-scan-id="' + sid + '">';
        html += '<input type="checkbox" value="' + sid + '"> ';
        html += label + '</label>';
    }
    if (!html) {
        container.innerHTML = '<span style="color:#546e7a;font-size:0.85em;">No barycentrically corrected scans yet. Run correction first.</span>';
        return;
    }
    container.innerHTML = html;
    // Auto-check the currently selected scan
    if (currentScanId) {
        var cb = container.querySelector('input[value="' + currentScanId + '"]');
        if (cb) {
            cb.checked = true;
            cb.parentElement.classList.add('checked');
        }
    }
    // Add change listeners
    var checkboxes = container.querySelectorAll('input[type="checkbox"]');
    for (var j = 0; j < checkboxes.length; j++) {
        checkboxes[j].onchange = function() {
            if (this.checked) {
                this.parentElement.classList.add('checked');
            } else {
                this.parentElement.classList.remove('checked');
            }
        };
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
        ra_hours: parseFloat(document.getElementById('bary-ra').value) || null,
        dec_deg: parseFloat(document.getElementById('bary-dec').value) || null,
        telescope: document.getElementById('bary-telescope').value,
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
        var resp = await fetch('/api/barycentric/results/' + encodeURIComponent(scanId) +
            '?page=' + page + '&per_page=' + baryCorrectedPerPage + '&snr_min=' + snrMin);
        var data = await resp.json();
        if (data.error) {
            document.getElementById('bary-corrected-tbody').innerHTML =
                '<tr><td colspan="8" class="empty">' + escapeHtml(data.error) + '</td></tr>';
            return;
        }

        // Render summary
        var summaryHtml = '';
        summaryHtml += '<div class="bs-item"><span class="bs-label">Target:</span><span class="bs-val">' + (data.target || '?') + '</span></div>';
        summaryHtml += '<div class="bs-item"><span class="bs-label">Total hits:</span><span class="bs-val">' + data.total_hits.toLocaleString() + '</span></div>';
        summaryHtml += '<div class="bs-item"><span class="bs-label">Files:</span><span class="bs-val">' + data.files_corrected + '</span></div>';
        summaryHtml += '<div class="bs-item"><span class="bs-label">Showing:</span><span class="bs-val">' + data.total_filtered.toLocaleString() + '</span></div>';
        document.getElementById('bary-corrected-summary').innerHTML = summaryHtml;

        // Render table
        var hits = data.hits || [];
        if (hits.length === 0) {
            document.getElementById('bary-corrected-tbody').innerHTML =
                '<tr><td colspan="8" class="empty">No hits.</td></tr>';
            document.getElementById('bary-corrected-pagination').style.display = 'none';
            return;
        }

        var startIdx = (data.page - 1) * data.per_page;
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
        var pagDiv = document.getElementById('bary-corrected-pagination');
        if (data.total_pages > 1) {
            pagDiv.style.display = 'flex';
            var phtml = '';
            phtml += '<button onclick="baryCorrectedPage=1;loadBaryCorrectedResults(\'' + scanId + '\')"' + (data.page === 1 ? ' disabled' : '') + '>« First</button>';
            phtml += '<button onclick="baryCorrectedPage=Math.max(1,' + (data.page - 1) + ');loadBaryCorrectedResults(\'' + scanId + '\')"' + (data.page === 1 ? ' disabled' : '') + '>‹ Prev</button>';
            phtml += '<span class="bl-page-info">Page ' + data.page + ' of ' + data.total_pages +
                ' (' + (startIdx + 1) + '-' + Math.min(startIdx + data.per_page, data.total_filtered) +
                ' of ' + data.total_filtered.toLocaleString() + ')</span>';
            phtml += '<button onclick="baryCorrectedPage=Math.min(' + data.total_pages + ',' + (data.page + 1) + ');loadBaryCorrectedResults(\'' + scanId + '\')"' + (data.page >= data.total_pages ? ' disabled' : '') + '>Next ›</button>';
            phtml += '<button onclick="baryCorrectedPage=' + data.total_pages + ';loadBaryCorrectedResults(\'' + scanId + '\')"' + (data.page >= data.total_pages ? ' disabled' : '') + '>Last »</button>';
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
    };

    try {
        var resp = await fetch('/api/barycentric/cross-epoch', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(params),
        });
        var data = await resp.json();
        if (data.error) {
            alert(data.error);
            return;
        }

        document.getElementById('bary-results-container').style.display = 'block';
        switchBaryTab('cross');
        renderCrossEpochResults(data);
    } catch(e) {
        alert('Error: ' + e.message);
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
