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
    document.getElementById('btn-stop-scan').onclick = stopScan;
    document.getElementById('btn-refresh').onclick = () => { loadResults(); loadStats(); loadScansList(); };
    document.getElementById('btn-select-all').onclick = selectAllFiles;
    document.getElementById('btn-select-none').onclick = selectNoneFiles;
    document.getElementById('btn-full-band').onclick = () => {
        document.getElementById('ctrl-f-start').value = '2744';
        document.getElementById('ctrl-f-stop').value = '3324';
    };
    document.getElementById('target-input').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') searchBL();
    });
    document.getElementById('results-filter').onchange = renderHitTable;
    document.getElementById('results-search').oninput = renderHitTable;
    document.getElementById('btn-run-rejection').onclick = runRejection;
    document.getElementById('scan-selector').onchange = onScanSelected;

    logInterval = setInterval(pollScanStatus, 3000);
    setInterval(pollDownloadStatus, 2000);
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
        '<p style="color:#546e7a;">Select a file below to mark its target on the sky map.</p>';
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
        info.innerHTML = '<p style="color:#546e7a;">Select a file below to mark its target on the sky map.</p>';
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
                    html += '<div class="data-file-row' + (isSelected ? ' selected' : '') + '" data-path="' + f.path + '">';
                    html += '<span class="file-name-click" onclick="toggleFileSelection(\'' + f.path + '\')">' + f.name + '</span>';
                    html += '<span><span class="file-type ' + labelClass.replace('-label','') + '">' + (isOn ? 'ON' : 'OFF') + '</span> ' + sizeStr + ' <button class="btn-del" onclick="event.stopPropagation();deleteFile(\'' + f.path + '\',\'' + f.name + '\')" title="Delete">\u2715</button></span>';
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
        } else if (data.scan_id) {
            currentScanId = data.scan_id;
        }
    } catch(e) {
        alert('Error: ' + e.message);
        document.getElementById('btn-start-scan').disabled = false;
        document.getElementById('btn-stop-scan').disabled = true;
    }
}

async function stopScan() {
    try { await fetch('/api/scan/stop', { method: 'POST' }); } catch(e) {}
    document.getElementById('btn-start-scan').disabled = false;
    document.getElementById('btn-stop-scan').disabled = true;
}

async function pollScanStatus() {
    try {
        var resp = await fetch('/api/scan/status');
        var data = await resp.json();
        var statusDiv = document.getElementById('scan-status');
        var progBar = document.getElementById('progress-bar-container');
        if (data.active) {
            statusDiv.innerHTML = '<p class="status-running">Scan running...</p>';
            progBar.style.display = 'block';
            var logDiv = document.getElementById('scan-log');
            var lines = data.log_tail || [];
            logDiv.innerHTML = lines.slice(-30).map(function(l) { return '<div>' + escapeHtml(l) + '</div>'; }).join('');
            logDiv.scrollTop = logDiv.scrollHeight;
            var lastLine = (data.progress && data.progress.last_line) || '';
            var match = lastLine.match(/\[(\d+)\/(\d+)\]/);
            if (match) {
                var pct = (parseInt(match[1]) / parseInt(match[2])) * 100;
                document.getElementById('progress-fill').style.width = pct + '%';
                document.getElementById('progress-text').textContent = match[1] + '/' + match[2] + ' sub-bands (' + pct.toFixed(1) + '%)';
            }
        } else {
            if (statusDiv.querySelector('.status-running')) {
                statusDiv.innerHTML = '<p class="status-idle">Scan complete.</p>';
                document.getElementById('btn-start-scan').disabled = false;
                document.getElementById('btn-stop-scan').disabled = true;
                // Reload scan list to pick up the new/updated scan
                loadScansList();
                loadResults(); loadStats();
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
        return;
    }
    loadScanResults(currentScanId);
    loadScanStats(currentScanId);
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

function renderHitTable() {
    var tbody = document.getElementById('hit-table-body');
    var filter = document.getElementById('results-filter').value;
    var search = document.getElementById('results-search').value.toLowerCase();
    var hits = allHits.slice();
    if (filter === 'on') hits = hits.filter(function(h) { return h.on_off === 'ON' || (h._source || '').indexOf('_S_') !== -1; });
    else if (filter === 'off') hits = hits.filter(function(h) { return h.on_off === 'OFF' || (h._source || '').indexOf('_R_') !== -1; });
    else if (filter === 'candidates') hits = rejectionCandidates.slice();
    if (search) hits = hits.filter(function(h) { return String(h.freq || '').indexOf(search) !== -1; });
    hits.sort(function(a, b) { return (b.snr || 0) - (a.snr || 0); });
    if (hits.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="empty">No hits found.</td></tr>';
        return;
    }
    var html = '';
    for (var i = 0; i < Math.min(hits.length, 500); i++) {
        var h = hits[i];
        var isOn = h.on_off === 'ON' || (h._source || '').indexOf('_S_') !== -1;
        var statusHtml = '';
        if (h.status === 'CANDIDATE') statusHtml = '<span style="color:#66bb6a;font-weight:bold;">CANDIDATE</span>';
        else if (h.status === 'RFI') statusHtml = '<span style="color:#ef5350;">RFI</span>';
        html += '<tr><td>' + (i+1) + '</td><td style="color:#66bb6a;font-weight:600;">' + (h.snr||0).toFixed(2) + '</td><td style="color:#4fc3f7;">' + (h.freq||0).toFixed(6) + '</td><td>' + (h.drift_rate||0).toFixed(4) + '</td><td>' + (h.channel||'-') + '</td><td><span class="on-badge ' + (isOn?'on':'off') + '">' + (isOn?'ON':'OFF') + '</span></td><td>' + statusHtml + '</td><td style="font-size:0.8em;color:#546e7a;">' + (h._source||h.file||'').substring(0,30) + '</td></tr>';
    }
    tbody.innerHTML = html;
}

function renderHitChart() {
    var chartDiv = document.getElementById('hit-chart');
    if (allHits.length === 0) {
        chartDiv.innerHTML = '<p style="text-align:center;color:#546e7a;padding:40px;">No hits to plot yet.</p>';
        return;
    }
    var onHits = allHits.filter(function(h) { return h.on_off === 'ON' || (h._source || '').indexOf('_S_') !== -1; });
    var offHits = allHits.filter(function(h) { return h.on_off === 'OFF' || (h._source || '').indexOf('_R_') !== -1; });
    var traces = [{
        x: onHits.map(function(h) { return h.freq; }), y: onHits.map(function(h) { return h.snr; }),
        mode: 'markers', type: 'scatter', name: 'ON',
        marker: { color: '#66bb6a', size: 8, opacity: 0.7 },
    }];
    if (offHits.length > 0) {
        traces.push({
            x: offHits.map(function(h) { return h.freq; }), y: offHits.map(function(h) { return h.snr; }),
            mode: 'markers', type: 'scatter', name: 'OFF',
            marker: { color: '#ef5350', size: 8, opacity: 0.7 },
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

async function pollDownloadStatus() {
    try {
        var resp = await fetch('/api/download/status');
        var data = await resp.json();
        var panel = document.getElementById('download-panel');
        if (!panel) return;
        if (data.queue.length === 0) { panel.style.display = 'none'; return; }
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
        var newComp = completedDl.filter(function(q) { return !q._refreshed; });
        if (newComp.length > 0 && activeDl.length === 0) {
            for (var j = 0; j < data.queue.length; j++) { if (data.queue[j].status === 'complete') data.queue[j]._refreshed = true; }
            loadLocalData();
        }
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

function escapeHtml(text) { var d = document.createElement('div'); d.textContent = text; return d.innerHTML; }
