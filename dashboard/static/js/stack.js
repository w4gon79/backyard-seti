/* Incoherent Stack - Phase 2C Frontend Logic */
/* Vanilla JS, no frameworks */

(function() {
'use strict';

// ─── State ────────────────────────────────────────────────────────────
var epochsData = [];
var currentJobId = null;
var pollTimer = null;
var sortCol = 'snr';
var sortDir = 'desc';
var currentResults = null;

// ─── Init ─────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function() {
    loadEpochs();
    loadHistory().then(function() {
        // After history loads, check for any running job and auto-connect
        autoConnectToRunningJob();
    });

    // Event handlers
    document.getElementById('stack-run-btn').onclick = runStack;
    document.getElementById('stack-refresh-history').onclick = loadHistory;
    document.getElementById('waterfall-close').onclick = closeWaterfallModal;
    document.getElementById('waterfall-modal').addEventListener('click', function(e) {
        if (e.target === this) closeWaterfallModal();
    });
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') closeWaterfallModal();
    });

    // Preset buttons
    var presets = document.querySelectorAll('.btn-preset');
    for (var i = 0; i < presets.length; i++) {
        presets[i].onclick = function() {
            var w = this.getAttribute('data-width');
            var c = this.getAttribute('data-center');
            document.getElementById('stack-width').value = w;
            document.getElementById('stack-freq-center').value = c;
            // Highlight active
            var all = document.querySelectorAll('.btn-preset');
            for (var j = 0; j < all.length; j++) all[j].classList.remove('active');
            this.classList.add('active');
        };
    }

    // Epoch select all/none
    document.getElementById('stack-epoch-all').onclick = function() {
        var cbs = document.querySelectorAll('.epoch-item input[type="checkbox"]');
        for (var i = 0; i < cbs.length; i++) cbs[i].checked = true;
        updateEpochCount();
    };
    document.getElementById('stack-epoch-none').onclick = function() {
        var cbs = document.querySelectorAll('.epoch-item input[type="checkbox"]');
        for (var i = 0; i < cbs.length; i++) cbs[i].checked = false;
        updateEpochCount();
    };

    // Peak table sort handlers
    var ths = document.querySelectorAll('#stack-peaks-table th.sortable');
    for (var i = 0; i < ths.length; i++) {
        ths[i].onclick = function() {
            var col = this.getAttribute('data-sort');
            if (sortCol === col) {
                sortDir = sortDir === 'asc' ? 'desc' : 'asc';
            } else {
                sortCol = col;
                sortDir = 'desc';
            }
            renderPeaksTable();
        };
    }
});

// ─── Epochs ───────────────────────────────────────────────────────────
async function loadEpochs() {
    try {
        var resp = await fetch('/api/stack/epochs');
        var data = await resp.json();
        if (data.error) throw new Error(data.error);

        epochsData = data.epochs || [];
        renderEpochs();
    } catch(err) {
        document.getElementById('stack-epochs-list').innerHTML =
            '<div style="padding:12px;color:#ef5350;font-size:0.85em;">Error: ' + escapeHtml(err.message) + '</div>';
    }
}

function renderEpochs() {
    var container = document.getElementById('stack-epochs-list');
    if (epochsData.length === 0) {
        container.innerHTML = '<div style="padding:12px;color:#546e7a;font-size:0.85em;">No epochs available</div>';
        return;
    }
    var html = '';
    for (var i = 0; i < epochsData.length; i++) {
        var ep = epochsData[i];
        html += '<label class="epoch-item">' +
            '<input type="checkbox" checked data-epoch="' + escapeHtml(ep.label) + '">' +
            '<span class="epoch-label">' + escapeHtml(ep.label) + '</span>' +
            '<span class="epoch-meta">' + ep.n_pairs + ' pairs · MJD ' + ep.mjd_int + '</span>' +
            '</label>';
    }
    container.innerHTML = html;

    // Attach change listeners
    var cbs = container.querySelectorAll('input[type="checkbox"]');
    for (var i = 0; i < cbs.length; i++) {
        cbs[i].onchange = updateEpochCount;
    }
    updateEpochCount();
}

function updateEpochCount() {
    var cbs = document.querySelectorAll('.epoch-item input[type="checkbox"]');
    var checked = 0;
    for (var i = 0; i < cbs.length; i++) if (cbs[i].checked) checked++;
    document.getElementById('stack-epoch-count').textContent = checked + ' selected';

    // Update SNR badge
    if (checked >= 2) {
        var gain = Math.sqrt(checked).toFixed(2);
        var badge = document.getElementById('stack-snr-badge');
        badge.style.display = '';
        badge.innerHTML = 'SNR Gain: <strong>√' + checked + ' = ' + gain + '×</strong>';
    } else {
        document.getElementById('stack-snr-badge').style.display = 'none';
    }
}

function getSelectedEpochs() {
    var cbs = document.querySelectorAll('.epoch-item input[type="checkbox"]');
    var epochs = [];
    for (var i = 0; i < cbs.length; i++) {
        if (cbs[i].checked) epochs.push(cbs[i].getAttribute('data-epoch'));
    }
    return epochs;
}

// ─── Run Stack ────────────────────────────────────────────────────────
async function runStack() {
    var target = document.getElementById('stack-target').value.trim();
    var freqCenter = parseFloat(document.getElementById('stack-freq-center').value);
    var width = parseFloat(document.getElementById('stack-width').value);
    var nSigma = parseFloat(document.getElementById('stack-n-sigma').value);
    var epochs = getSelectedEpochs();

    if (!target) { alert('Please enter a target'); return; }
    if (epochs.length < 2) { alert('Need at least 2 epochs for stacking'); return; }
    if (isNaN(freqCenter) || isNaN(width)) { alert('Invalid frequency/width'); return; }

    var btn = document.getElementById('stack-run-btn');
    btn.disabled = true;
    btn.textContent = '⏳ Running...';

    // Show running view
    showView('running');
    document.getElementById('stack-running-status').textContent = 'Submitting job...';
    document.getElementById('stack-running-fill').style.width = '0%';
    document.getElementById('stack-running-epoch').textContent = '';

    try {
        var resp = await fetch('/api/stack/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                target: target,
                freq_center: freqCenter,
                width: width,
                epochs: epochs,
                n_sigma: nSigma,
                telescope: 'parkes'
            })
        });
        var data = await resp.json();
        if (data.error) throw new Error(data.error);

        currentJobId = data.job_id;
        document.getElementById('stack-running-status').textContent = 'Job ' + currentJobId + ' started...';
        startPolling();
    } catch(err) {
        showError(err.message);
        btn.disabled = false;
        btn.textContent = '▶ Run Stack';
    }
}

// ─── Polling ──────────────────────────────────────────────────────────
function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollStatus(); // Fire immediately
    pollTimer = setInterval(pollStatus, 3000);
}

function stopPolling() {
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
}

async function pollStatus() {
    if (!currentJobId) return;
    try {
        var resp = await fetch('/api/stack/status/' + currentJobId);
        var data = await resp.json();
        if (data.error) throw new Error(data.error);

        var pct = data.progress || 0;
        document.getElementById('stack-running-fill').style.width = pct + '%';
        document.getElementById('stack-running-status').textContent = data.progress_msg || data.status || 'Running...';

        if (data.epochs && data.n_epochs) {
            document.getElementById('stack-running-epoch').textContent =
                data.epochs.length + ' epochs';
        }

        if (data.status === 'complete') {
            stopPolling();
            await loadResults(currentJobId);
        } else if (data.status === 'error') {
            stopPolling();
            showError(data.progress_msg || data.error || 'Unknown error');
            var btn = document.getElementById('stack-run-btn');
            btn.disabled = false;
            btn.textContent = '▶ Run Stack';
        }
    } catch(err) {
        console.error('Poll error:', err);
    }
}

// ─── Results ──────────────────────────────────────────────────────────
async function loadResults(jobId) {
    try {
        var resp = await fetch('/api/stack/results/' + jobId);
        var data = await resp.json();
        if (data.error) throw new Error(data.error);

        currentResults = data;
        currentJobId = jobId;
        showView('complete');
        renderSummary(data);
        renderEpochTable(data);
        renderPeaksTable();
        loadPlot(jobId);

        // Async load classification + cross-ref (post-processing features)
        loadClassifications(jobId);
        loadCrossRef(jobId);

        // Re-enable run button
        var btn = document.getElementById('stack-run-btn');
        btn.disabled = false;
        btn.textContent = '▶ Run Stack';

        // Refresh history
        loadHistory();
    } catch(err) {
        showError(err.message);
    }
}

function renderSummary(data) {
    var nEpochs = data.n_epochs || (data.used_epochs || []).length;
    var snrImprove = data.snr_improvement || (nEpochs > 0 ? Math.sqrt(nEpochs).toFixed(2) : '--');
    var stackSigma = data.stack_sigma;
    var nPeaks = (data.peaks || []).length;

    var html = '';
    html += '<div class="summary-stat"><div class="stat-label">Epochs Stacked</div><div class="stat-value blue">' + nEpochs + '</div></div>';
    html += '<div class="summary-stat"><div class="stat-label">SNR Improvement</div><div class="stat-value green">×' + snrImprove + '</div></div>';
    html += '<div class="summary-stat"><div class="stat-label">Stack σ (noise)</div><div class="stat-value yellow">' + (stackSigma ? stackSigma.toFixed(4) : '--') + '</div></div>';
    html += '<div class="summary-stat"><div class="stat-label">Peaks Found</div><div class="stat-value ' + (nPeaks > 0 ? 'green' : 'red') + '">' + nPeaks + '</div></div>';
    document.getElementById('stack-summary').innerHTML = html;

    // Update header badge
    var badge = document.getElementById('stack-snr-badge');
    badge.style.display = '';
    badge.innerHTML = 'SNR Gain: <strong>√' + nEpochs + ' = ×' + snrImprove + '</strong>';

    // Result badge in panel header
    var rb = document.getElementById('stack-result-badge');
    rb.style.display = '';
    rb.textContent = nPeaks + ' peaks';
    rb.style.background = nPeaks > 0 ? '#1b5e20' : '#37474f';
    rb.style.color = nPeaks > 0 ? '#a5d6a7' : '#b0bec5';
}

function renderEpochTable(data) {
    var epochInfo = data.epoch_info || [];
    if (epochInfo.length === 0) {
        document.getElementById('stack-epoch-tbody').innerHTML =
            '<tr><td colspan="3" style="color:#546e7a;text-align:center;padding:12px;">No per-epoch data</td></tr>';
        return;
    }
    var stackSigma = data.stack_sigma || 0;
    var html = '';
    for (var i = 0; i < epochInfo.length; i++) {
        var ep = epochInfo[i];
        var sigma = ep.sigma || ep.noise_sigma || 0;
        var ratio = stackSigma > 0 ? (sigma / stackSigma).toFixed(3) : '--';
        var ratioColor = ratio !== '--' && Math.abs(parseFloat(ratio) - 1.0) < 0.3 ? '#66bb6a' : '#ffeb3b';
        html += '<tr>' +
            '<td style="color:#4fc3f7;font-family:Consolas,monospace;">' + escapeHtml(ep.label || ep.epoch || '?') + '</td>' +
            '<td>' + sigma.toFixed(4) + '</td>' +
            '<td style="color:' + ratioColor + ';">' + ratio + '</td>' +
            '</tr>';
    }
    document.getElementById('stack-epoch-tbody').innerHTML = html;
}

function renderPeaksTable() {
    if (!currentResults || !currentResults.peaks) return;
    var peaks = currentResults.peaks.slice(0); // copy for sorting

    // Sort
    peaks.sort(function(a, b) {
        var va, vb;
        switch(sortCol) {
            case 'freq': va = a.freq_mhz || a.freq || 0; vb = b.freq_mhz || b.freq || 0; break;
            case 'snr': va = a.snr || 0; vb = b.snr || 0; break;
            case 'width': va = a.width_chans || a.width || 0; vb = b.width_chans || b.width || 0; break;
            default: va = 0; vb = 0; break;
        }
        if (sortDir === 'asc') return va - vb;
        return vb - va;
    });

    // Update sort indicators
    var ths = document.querySelectorAll('#stack-peaks-table th.sortable');
    for (var i = 0; i < ths.length; i++) {
        ths[i].classList.remove('sorted-asc', 'sorted-desc');
        if (ths[i].getAttribute('data-sort') === sortCol) {
            ths[i].classList.add(sortDir === 'asc' ? 'sorted-asc' : 'sorted-desc');
        }
    }

    var display = peaks.slice(0, 50);
    document.getElementById('stack-peak-count').textContent = display.length + ' shown';

    if (display.length === 0) {
        document.getElementById('stack-peaks-tbody').innerHTML =
            '<tr><td colspan="7" style="color:#546e7a;text-align:center;padding:20px;">No peaks above threshold</td></tr>';
        return;
    }

    // Derive the first ON file for waterfall
    var waterfallFile = deriveWaterfallFile();

    // Classification data (loaded async after render)
    var classData = currentResults._classifications || {};
    var classMap = {};
    if (classData.classifications) {
        for (var i = 0; i < classData.classifications.length; i++) {
            var c = classData.classifications[i];
            classMap[c.freq_mhz.toFixed(6)] = c;
        }
    }

    // Cross-ref data
    var xrefData = currentResults._crossref || {};
    var xrefMap = {};
    if (xrefData.matches) {
        for (var i = 0; i < xrefData.matches.length; i++) {
            var m = xrefData.matches[i];
            if (m.matched) xrefMap[m.freq_mhz.toFixed(6)] = m;
        }
    }

    var html = '';
    for (var i = 0; i < display.length; i++) {
        var p = display[i];
        var freq = p.freq_mhz || p.freq || 0;
        var snr = p.snr || 0;
        var width = p.width_chans || p.width || 0;
        var peakJson = encodeURIComponent(JSON.stringify({ freq: freq, file: waterfallFile }));

        // Classification badge
        var freqKey = freq.toFixed(6);
        var cls = classMap[freqKey];
        var classBadge = '<span class="peak-class pending" data-freq="' + freqKey + '">···</span>';
        if (cls) {
            var clsIcon = cls.class === 'candidate' ? '●' : (cls.class === 'possible' ? '●' : '●');
            classBadge = '<span class="peak-class ' + cls.class + '" title="' + escapeHtml(cls.reasons.join('; ')) + '" data-freq="' + freqKey + '">' + clsIcon + '</span>';
        }

        // Cross-epoch badge
        var xref = xrefMap[freqKey];
        var xrefBadge = '<span class="xref-badge none">—</span>';
        if (xref) {
            var nCands = xref.candidates.length;
            var topCand = xref.candidates[0];
            var title = topCand.epoch_count + ' epochs, SNR ' + (topCand.max_snr || 0).toFixed(1) + ', drift ' + (topCand.mean_drift || 0).toFixed(2) + ' Hz/s';
            xrefBadge = '<span class="xref-badge match" title="' + escapeHtml(title) + '" data-candidates=\'' + encodeURIComponent(JSON.stringify(xref.candidates)) + '\' onclick="showXrefDetail(this)">🔗 ' + nCands + '</span>';
        }

        html += '<tr data-peak="' + peakJson + '">' +
            '<td style="color:#546e7a;">' + (i + 1) + '</td>' +
            '<td class="peak-freq">' + freq.toFixed(6) + '</td>' +
            '<td class="peak-snr">' + snr.toFixed(2) + '</td>' +
            '<td class="peak-width">' + width + '</td>' +
            '<td class="peak-class-cell">' + classBadge + '</td>' +
            '<td class="xref-cell">' + xrefBadge + '</td>' +
            '<td><button class="btn-waterfall" onclick="event.stopPropagation(); showStackWaterfall(this.closest(\'tr\'));">🔍 View</button></td>' +
            '</tr>';
    }
    document.getElementById('stack-peaks-tbody').innerHTML = html;

    // Update classification summary if available
    if (classData.summary) {
        var s = classData.summary;
        var sumEl = document.getElementById('stack-class-summary');
        if (sumEl) {
            sumEl.innerHTML = '<span class="class-sum candidate">' + s.candidate + ' candidates</span>' +
                '<span class="class-sum possible">' + s.possible + ' possible</span>' +
                '<span class="class-sum rfi">' + s.rfi + ' RFI</span>';
            sumEl.style.display = '';
        }
    }
}

function deriveWaterfallFile() {
    if (!currentResults) return null;
    // epoch_info is an array of {label, median, sigma}
    var epochs = currentResults.used_epochs || [];
    if (epochs.length === 0 && currentResults.epoch_info) {
        epochs = currentResults.epoch_info.map(function(e) { return e.label; });
    }
    if (epochs.length === 0) return null;
    var firstEpoch = epochs[0];
    // Parse epoch label like "MJD_57791" or "57791"
    var mjdMatch = firstEpoch.match(/(\d+)/);
    var mjd = mjdMatch ? mjdMatch[1] : '57791';
    // Construct first ON file path
    // Pattern: fine/Parkes_<mjd>_<seq>_PROXCEN_S_fine.h5
    // Seq numbers for each epoch's first ON file
    var epochSeqs = {
        '57791': '72989',
        '57846': '49534',
        '57930': '41709',
        '58020': '21048'
    };
    var seq = epochSeqs[firstEpoch] || '72989';
    return 'fine/Parkes_' + mjd + '_' + seq + '_PROXCEN_S_fine.h5';
}

function loadPlot(jobId) {
    // Try interactive Plotly chart first (needs grid_freqs + stack_power)
    if (currentResults && currentResults.grid_freqs && currentResults.stack_power) {
        renderInteractiveSpectrum(currentResults);
        return;
    }
    // Try loading spectrum data from API (for jobs restored from DB)
    if (currentResults && currentResults.has_spectrum) {
        fetch('/api/stack/spectrum/' + jobId).then(function(resp) {
            return resp.json();
        }).then(function(data) {
            if (data.error) {
                fallbackToPNG(jobId);
                return;
            }
            currentResults.grid_freqs = data.grid_freqs;
            currentResults.stack_power = data.stack_power;
            renderInteractiveSpectrum(currentResults);
        }).catch(function() {
            fallbackToPNG(jobId);
        });
        return;
    }
    fallbackToPNG(jobId);
}

function fallbackToPNG(jobId) {
    var img = document.getElementById('stack-plot-img');
    img.src = '/api/stack/plot/' + jobId + '?t=' + Date.now();
    img.style.display = 'none';
    img.onload = function() { img.style.display = 'block'; };
    img.onerror = function() { img.style.display = 'none'; };
}

function renderInteractiveSpectrum(data) {
    var freqs = data.grid_freqs;
    var power = data.stack_power;
    var peaks = data.peaks || [];
    var median = data.stack_median || 0;
    var sigma = data.stack_sigma || 1;

    var plotDiv = document.getElementById('stack-plot-div');
    if (!plotDiv) return;

    // Hide the PNG fallback image when Plotly is active
    var imgEl = document.getElementById('stack-plot-img');
    if (imgEl) imgEl.style.display = 'none';

    // Downsample if too many points (Plotly struggles with >100k points)
    var maxPoints = 50000;
    var step = Math.ceil(freqs.length / maxPoints);
    var fPlot = [], pPlot = [];
    for (var i = 0; i < freqs.length; i += step) {
        fPlot.push(freqs[i]);
        pPlot.push(power[i]);
    }

    var traces = [{
        x: fPlot,
        y: pPlot,
        type: 'scatter',
        mode: 'lines',
        line: { color: '#4fc3f7', width: 0.5 },
        name: 'Stacked spectrum',
        hovertemplate: '%{x:.6f} MHz<br>Power: %{y:.2e}<extra></extra>'
    }];

    // Add peak markers
    if (peaks.length > 0) {
        var peakX = [], peakY = [];
        for (var i = 0; i < peaks.length && i < 50; i++) {
            var pf = peaks[i].freq_mhz || peaks[i].freq || 0;
            // Find nearest power value
            var idx = Math.round((pf - freqs[0]) / (freqs[freqs.length - 1] - freqs[0]) * (freqs.length - 1));
            if (idx >= 0 && idx < power.length) {
                peakX.push(pf);
                peakY.push(power[idx]);
            }
        }
        if (peakX.length > 0) {
            traces.push({
                x: peakX,
                y: peakY,
                type: 'scatter',
                mode: 'markers',
                marker: { color: '#ffeb3b', size: 8, symbol: 'triangle-up' },
                name: 'Peaks (>' + (data.n_sigma || 5) + '\u03c3)',
                hovertemplate: '%{x:.6f} MHz<br>Peak<extra></extra>'
            });
        }
    }

    // Noise threshold line
    if (sigma > 0) {
        var threshY = median + (data.n_sigma || 5) * sigma;
        traces.push({
            x: [fPlot[0], fPlot[fPlot.length - 1]],
            y: [threshY, threshY],
            type: 'scatter',
            mode: 'lines',
            line: { color: '#ef5350', width: 1, dash: 'dash' },
            name: (data.n_sigma || 5) + '\u03c3 threshold',
            hoverinfo: 'skip'
        });
    }

    var layout = {
        paper_bgcolor: 'transparent',
        plot_bgcolor: '#0d1b2a',
        font: { color: '#c8c8e0', size: 11 },
        xaxis: { title: 'Barycentric Frequency (MHz)', gridcolor: '#1e3a5f', tickformat: '.4f' },
        yaxis: { title: 'Power (dB above median)', gridcolor: '#1e3a5f' },
        margin: { l: 60, r: 20, t: 30, b: 50 },
        showlegend: true,
        legend: { x: 0.02, y: 0.98, bgcolor: 'rgba(13,27,42,0.8)' },
        hovermode: 'closest'
    };

    Plotly.newPlot(plotDiv, traces, layout, {
        displayModeBar: true,
        responsive: true,
        modeBarButtonsToRemove: ['lasso2d', 'autoScale2d']
    });
}

// ─── Waterfall Modal ──────────────────────────────────────────────────
window.showStackWaterfall = function(rowEl) {
    var peakJson = rowEl.getAttribute('data-peak');
    if (!peakJson) return;
    var p;
    try { p = JSON.parse(decodeURIComponent(peakJson)); }
    catch(e) { console.error('Failed to parse peak data', e); return; }

    var freq = p.freq || 0;
    var modal = document.getElementById('waterfall-modal');
    var title = document.getElementById('waterfall-title');
    var metaDiv = document.getElementById('waterfall-meta');
    var bodyDiv = document.getElementById('waterfall-body');

    title.textContent = 'Waterfall — ' + freq.toFixed(6) + ' MHz';

    // If we have a stack job context, try the stacked waterfall endpoint.
    // Falls back to single-file waterfall if stacked endpoint fails.
    if (currentJobId) {
        showStackedWaterfallSafe(currentJobId, freq, p.file, modal, title, metaDiv, bodyDiv);
        return;
    }

    // Fallback: single-file waterfall (original behavior)
    var metaHtml = '';
    metaHtml += '<div class="wm-item"><span class="wm-label">Freq:</span><span class="wm-val">' + freq.toFixed(6) + ' MHz</span></div>';
    metaHtml += '<div class="wm-item"><span class="wm-label">Source:</span><span class="wm-val" style="font-size:0.85em;">' + (p.file || 'auto') + '</span></div>';
    metaDiv.innerHTML = metaHtml;

    bodyDiv.innerHTML = '<div class="waterfall-loading"><div class="spinner"></div><div>Loading waterfall data...</div><div style="font-size:0.8em;color:#546e7a;margin-top:4px;">Reading from HDF5 file</div></div>';
    modal.style.display = 'flex';

    if (!p.file) {
        bodyDiv.innerHTML = '<div class="waterfall-error">Could not determine source HDF5 file.</div>';
        return;
    }

    var url = '/api/waterfall?file=' + encodeURIComponent(p.file) +
              '&freq_mhz=' + freq +
              '&width_chans=200&max_tints=20';

    fetch(url).then(function(resp) { return resp.json(); }).then(function(data) {
        if (data.error) {
            bodyDiv.innerHTML = '<div class="waterfall-error">Error: ' + escapeHtml(data.error) + '</div>';
            return;
        }
        renderWaterfallPlot(data, freq);
    }).catch(function(err) {
        bodyDiv.innerHTML = '<div class="waterfall-error">Fetch error: ' + escapeHtml(err.message) + '</div>';
    });
};

// ─── Stacked Waterfall (single epoch vs stacked comparison) ──────────
// Tries the stacked endpoint first, falls back to single-file waterfall
// if it fails (OOM/timeout on large HDF5 files).
function showStackedWaterfallSafe(jobId, freq, fileFallback, modal, title, metaDiv, bodyDiv) {
    title.textContent = 'Waterfall — ' + freq.toFixed(6) + ' MHz';
    metaDiv.innerHTML =
        '<div class="wm-item"><span class="wm-label">Freq:</span><span class="wm-val">' + freq.toFixed(6) + ' MHz</span></div>';
    bodyDiv.innerHTML =
        '<div class="waterfall-loading"><div class="spinner"></div>' +
        '<div>Loading waterfall data...</div></div>';
    modal.style.display = 'flex';

    // Try stacked endpoint with a short timeout
    var controller = new AbortController();
    var timeoutId = setTimeout(function() { controller.abort(); }, 30000);

    fetch('/api/stack/peaks/' + jobId + '/stacked_waterfall?freq_mhz=' + freq +
          '&width_chans=100&max_tints=10', { signal: controller.signal })
        .then(function(resp) {
            clearTimeout(timeoutId);
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            return resp.json();
        })
        .then(function(data) {
            if (data.error) throw new Error(data.error);
            renderStackedWaterfallPlot(data);
        })
        .catch(function(err) {
            clearTimeout(timeoutId);
            console.log('Stacked waterfall failed, falling back to single:', err.message);
            // Fall back to original single-file waterfall
            if (fileFallback) {
                var url = '/api/waterfall?file=' + encodeURIComponent(fileFallback) +
                          '&freq_mhz=' + freq + '&width_chans=200&max_tints=20';
                fetch(url).then(function(r) { return r.json(); }).then(function(d) {
                    if (d.error) {
                        bodyDiv.innerHTML = '<div class="waterfall-error">Error: ' + escapeHtml(d.error) + '</div>';
                        return;
                    }
                    renderWaterfallPlot(d, freq);
                }).catch(function(e) {
                    bodyDiv.innerHTML = '<div class="waterfall-error">Fetch error: ' + escapeHtml(e.message) + '</div>';
                });
            } else {
                bodyDiv.innerHTML = '<div class="waterfall-error">Waterfall unavailable for this peak.</div>';
            }
        });
}

function renderStackedWaterfallPlot(data) {
    var bodyDiv = document.getElementById('waterfall-body');
    var freqs = data.freqs;
    var times = data.times;
    var centerFreq = data.center_freq_mhz;

    var single = data.single_epoch;
    var stacked = data.stacked;
    var allEpochs = data.all_epochs || [];

    // Build mode toggle: "comparison" (side by side) or "all epochs" (grid)
    var html = '';
    html += '<div class="stacked-wf-controls">';
    html += '<button class="wf-mode-btn active" data-mode="compare">Single vs Stacked</button>';
    if (allEpochs.length > 2) {
        html += '<button class="wf-mode-btn" data-mode="all">All Epochs (' + allEpochs.length + ')</button>';
    }
    html += '</div>';
    html += '<div id="wf-compare-view" class="wf-view">';
    html += '<div class="wf-pair-wrap">';
    html += '<div class="wf-pair">';
    html += '<div class="wf-panel-label">' + escapeHtml(single.label) + ' (raw)</div>';
    html += '<div id="wf-single-plot" class="wf-plot"></div>';
    html += '</div>';
    html += '<div class="wf-pair">';
    html += '<div class="wf-panel-label">' + escapeHtml(stacked.label) + ' (SNR boosted)</div>';
    html += '<div id="wf-stacked-plot" class="wf-plot"></div>';
    html += '</div>';
    html += '</div>';
    html += '</div>';
    if (allEpochs.length > 2) {
        html += '<div id="wf-all-view" class="wf-view" style="display:none;">';
        html += '<div class="wf-grid">';
        for (var i = 0; i < allEpochs.length; i++) {
            html += '<div class="wf-grid-item">';
            html += '<div class="wf-panel-label">' + escapeHtml(allEpochs[i].label) + '</div>';
            html += '<div id="wf-epoch-' + i + '" class="wf-plot wf-plot-small"></div>';
            html += '</div>';
        }
        html += '</div>';
        html += '</div>';
    }
    bodyDiv.innerHTML = html;

    // Render side-by-side comparison
    renderWaterfallHeatmap('wf-single-plot', single.data, freqs, times, centerFreq, -2, 10);
    renderWaterfallHeatmap('wf-stacked-plot', stacked.data, freqs, times, centerFreq, -2, 10);

    // Render all-epochs grid if present
    if (allEpochs.length > 2) {
        for (var i = 0; i < allEpochs.length; i++) {
            renderWaterfallHeatmap('wf-epoch-' + i, allEpochs[i].data, freqs, times, centerFreq, -2, 10);
        }
    }

    // Mode toggle handlers
    var btns = document.querySelectorAll('.wf-mode-btn');
    for (var i = 0; i < btns.length; i++) {
        btns[i].onclick = function() {
            for (var j = 0; j < btns.length; j++) btns[j].classList.remove('active');
            this.classList.add('active');
            var mode = this.getAttribute('data-mode');
            var compareView = document.getElementById('wf-compare-view');
            var allView = document.getElementById('wf-all-view');
            if (mode === 'compare') {
                compareView.style.display = '';
                if (allView) allView.style.display = 'none';
            } else {
                compareView.style.display = 'none';
                if (allView) allView.style.display = '';
            }
        };
    }
}

function renderWaterfallHeatmap(divId, zData, freqs, times, centerFreq, zmin, zmax) {
    var plotDiv = document.getElementById(divId);
    if (!plotDiv) return;

    var isSmall = divId.indexOf('epoch-') >= 0;

    var traces = [{
        type: 'heatmap',
        z: zData,
        x: freqs,
        y: times,
        zmin: zmin,
        zmax: zmax,
        colorscale: 'Viridis',
        reversescale: true,
        hoverinfo: isSmall ? 'none' : 'x+y+z',
        hovertemplate: isSmall ? undefined : '%{x:.6f} MHz<br>t=%{y:.1f}s<br>Power=%{z:.2f}<extra></extra>',
        name: 'Power'
    }];

    // Center frequency marker
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

    var layout = {
        paper_bgcolor: 'transparent',
        plot_bgcolor: '#0a0e14',
        font: { color: '#c8c8e0', size: isSmall ? 9 : 11 },
        xaxis: {
            title: isSmall ? '' : 'Frequency (MHz)',
            gridcolor: '#1e3a5f',
            tickformat: '.4f',
            fixedrange: true
        },
        yaxis: {
            title: isSmall ? '' : 'Time (s)',
            gridcolor: '#1e3a5f',
            autorange: 'reversed',
            fixedrange: true
        },
        margin: { l: 50, r: 10, t: 10, b: isSmall ? 30 : 50 },
        height: isSmall ? 200 : 350,
        showlegend: false,
        hovermode: isSmall ? false : 'closest',
        hoverdistance: -1,
        hoverlabel: {
            bgcolor: '#0d1b2a',
            bordercolor: '#4fc3f7',
            font: { color: '#c8c8e0', size: 10 },
            align: 'left',
            namelength: 0
        }
    };

    Plotly.newPlot(plotDiv, traces, layout, {
        displayModeBar: false,
        responsive: true
    });

    // For small grid plots: show hover info in a fixed status bar above the plot
    if (isSmall) {
        var gridItem = plotDiv.closest('.wf-grid-item');
        if (gridItem) {
            var labelEl = gridItem.querySelector('.wf-panel-label');
            plotDiv.on('plotly_hover', function(eventData) {
                if (eventData.points && eventData.points[0]) {
                    var pt = eventData.points[0];
                    var freqVal = freqs[pt.pointIndex[1]] || 0;
                    var timeVal = times[pt.pointIndex[0]] || 0;
                    var powerVal = zData[pt.pointIndex[0]] ? zData[pt.pointIndex[0]][pt.pointIndex[1]] : 0;
                    if (labelEl) {
                        labelEl.dataset.original = labelEl.dataset.original || labelEl.textContent;
                        labelEl.textContent = labelEl.dataset.original +
                            ' | ' + freqVal.toFixed(6) + ' MHz | ' + powerVal.toFixed(2) + ' dB';
                    }
                }
            });
            plotDiv.on('plotly_unhover', function() {
                if (labelEl && labelEl.dataset.original) {
                    labelEl.textContent = labelEl.dataset.original;
                }
            });
        }
    }
}

function renderWaterfallPlot(data, centerFreq) {
    var bodyDiv = document.getElementById('waterfall-body');
    bodyDiv.innerHTML = '<div id="waterfall-plot" style="width:100%;height:400px;"></div>';
    var plotDiv = document.getElementById('waterfall-plot');

    var z = data.data;
    var freqs = data.freqs;
    var times = data.times;

    var traces = [{
        type: 'heatmap',
        z: z,
        x: freqs,
        y: times,
        zmin: -2,
        zmax: 10,
        colorscale: 'Viridis',
        reversescale: true,
        hovertemplate: '%{x:.6f} MHz<br>t=%{y:.1f}s<br>Power=%{z:.2f}<extra></extra>',
        name: 'Power'
    }];

    // Center frequency marker
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

    var layout = {
        paper_bgcolor: 'transparent',
        plot_bgcolor: '#0a0e14',
        font: { color: '#c8c8e0', size: 11 },
        xaxis: { title: 'Frequency (MHz)', gridcolor: '#1e3a5f', tickformat: '.4f' },
        yaxis: { title: 'Time (s)', gridcolor: '#1e3a5f', autorange: 'reversed' },
        margin: { l: 60, r: 20, t: 30, b: 50 },
        height: 400,
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
    var plotDiv = document.getElementById('waterfall-plot');
    if (plotDiv && plotDiv.data) Plotly.purge(plotDiv);
}

// ─── Peak Classification Loader ─────────────────────────────────────
async function loadClassifications(jobId) {
    try {
        var resp = await fetch('/api/stack/peaks/' + jobId + '/classify');
        var data = await resp.json();
        if (data.error) return;
        if (currentResults) {
            currentResults._classifications = data;
            renderPeaksTable();
        }
    } catch(err) {
        console.error('Classification load error:', err);
    }
}

// ─── Cross-Epoch Reference Loader ────────────────────────────────────
async function loadCrossRef(jobId) {
    try {
        var resp = await fetch('/api/stack/peaks/' + jobId + '/crossref');
        var data = await resp.json();
        if (data.error) return;
        if (currentResults) {
            currentResults._crossref = data;
            renderPeaksTable();
        }
    } catch(err) {
        console.error('Cross-ref load error:', err);
    }
}

// ─── Cross-Epoch Detail Modal ────────────────────────────────────────
window.showXrefDetail = function(el) {
    var encoded = el.getAttribute('data-candidates');
    if (!encoded) return;
    var cands;
    try { cands = JSON.parse(decodeURIComponent(encoded)); }
    catch(e) { return; }

    var modal = document.getElementById('waterfall-modal');
    var title = document.getElementById('waterfall-title');
    var metaDiv = document.getElementById('waterfall-meta');
    var bodyDiv = document.getElementById('waterfall-body');

    title.textContent = '🔗 Cross-Epoch Candidates';

    var metaHtml = '<div class="wm-item"><span class="wm-label">Matched peaks:</span><span class="wm-val">' + cands.length + '</span></div>';
    metaDiv.innerHTML = metaHtml;

    var html = '<div style="padding:16px;color:#c8c8e0;">';
    html += '<table style="width:100%;border-collapse:collapse;font-size:0.9em;">';
    html += '<tr style="border-bottom:1px solid #1e3a5f;color:#90a4ae;"><th style="text-align:left;padding:6px;">Freq (MHz)</th><th>Epochs</th><th>Max SNR</th><th>Drift (Hz/s)</th></tr>';
    for (var i = 0; i < cands.length; i++) {
        var c = cands[i];
        html += '<tr style="border-bottom:1px solid #112233;">';
        html += '<td style="padding:6px;color:#4fc3f7;font-family:Consolas,monospace;">' + (c.freq_mhz || 0).toFixed(6) + '</td>';
        html += '<td style="text-align:center;color:#a5d6a7;">' + (c.epoch_count || 0) + '</td>';
        html += '<td style="text-align:center;color:#ffeb3b;">' + (c.max_snr || 0).toFixed(1) + '</td>';
        html += '<td style="text-align:center;color:#90a4ae;">' + (c.mean_drift || 0).toFixed(2) + '</td>';
        html += '</tr>';
    }
    html += '</table></div>';
    bodyDiv.innerHTML = html;
    modal.style.display = 'flex';
};

// ─── Auto-connect to running job on page load ─────────────────────────
function autoConnectToRunningJob() {
    var items = document.querySelectorAll('.history-item');
    for (var i = 0; i < items.length; i++) {
        var status = items[i].getAttribute('data-status');
        if (status === 'running' || status === 'pending') {
            var jobId = items[i].getAttribute('data-job');
            currentJobId = jobId;
            showView('running');
            document.getElementById('stack-running-status').textContent = 'Connected to running job ' + jobId + '...';
            document.getElementById('stack-running-fill').style.width = '0%';
            document.getElementById('stack-running-epoch').textContent = '';
            startPolling();
            break; // Only auto-connect to the first running job
        }
    }
}

// ─── History ──────────────────────────────────────────────────────────
async function loadHistory() {
    try {
        var resp = await fetch('/api/stack/history');
        var data = await resp.json();
        if (data.error) throw new Error(data.error);

        renderHistory(data.jobs || []);
    } catch(err) {
        document.getElementById('stack-history-list').innerHTML =
            '<div style="padding:12px;color:#ef5350;font-size:0.85em;">Error: ' + escapeHtml(err.message) + '</div>';
    }
}

function renderHistory(jobs) {
    var container = document.getElementById('stack-history-list');
    if (jobs.length === 0) {
        container.innerHTML = '<div class="history-placeholder">No previous stacks. Run one!</div>';
        return;
    }

    var html = '';
    for (var i = 0; i < jobs.length; i++) {
        var j = jobs[i];
        var statusIcon = j.status === 'complete' ? '✅' : (j.status === 'error' ? '❌' : (j.status === 'interrupted' ? '⏸️' : '⏳'));
        var statusClass = j.status || 'unknown';
        var time = j.created_at || '';
        // Truncate time to just date + HH:MM
        if (time.length > 16) time = time.substring(0, 16);

        var peakInfo = '';
        if (j.n_peaks !== undefined && j.n_peaks !== null) {
            peakInfo = '<span class="history-peaks">' + j.n_peaks + ' peaks</span>';
        }

        var resumeBtn = '';
        if (j.status === 'error' || j.status === 'interrupted') {
            resumeBtn = '<button class="btn-resume-small" data-resume="' + j.job_id + '">▶ Resume</button>';
        }
        var deleteBtn = '<button class="btn-delete-small" data-delete="' + j.job_id + '">✕</button>';

        html += '<div class="history-item" data-job="' + j.job_id + '" data-status="' + j.status + '">' +
            '<span class="history-status ' + statusClass + '">' + statusIcon + '</span>' +
            '<div class="history-info">' +
                '<span class="history-target">' + escapeHtml(j.target || '?') + '</span>' +
                '<span class="history-freq">' + (j.freq_center || 0).toFixed(1) + ' MHz ±' + (j.width_mhz || 0).toFixed(0) + '</span>' +
                '<span class="history-epochs">' + (j.n_epochs || 0) + ' epochs</span>' +
                peakInfo +
            '</div>' +
            resumeBtn +
            deleteBtn +
            '<span class="history-time">' + time + '</span>' +
            '</div>';
    }
    container.innerHTML = html;

    // Click handlers
    var items = container.querySelectorAll('.history-item');
    for (var i = 0; i < items.length; i++) {
        items[i].onclick = function() {
            var jobId = this.getAttribute('data-job');
            var status = this.getAttribute('data-status');
            if (status === 'complete') {
                loadResults(jobId);
            } else if (status === 'running' || status === 'pending') {
                // Show live progress for in-progress jobs
                currentJobId = jobId;
                showView('running');
                document.getElementById('stack-running-status').textContent = 'Connected to running job ' + jobId + '...';
                document.getElementById('stack-running-fill').style.width = '0%';
                document.getElementById('stack-running-epoch').textContent = '';
                startPolling();
            } else if (status === 'interrupted' || status === 'error') {
                // Show error view for failed jobs
                currentJobId = jobId;
                fetch('/api/stack/status/' + jobId).then(function(r) { return r.json(); }).then(function(data) {
                    showError(data.progress_msg || data.error || 'Job ' + status);
                }).catch(function() {
                    showError('Could not load job details');
                });
            }
        };
    }

    // Resume button handlers
    var resumeBtns = container.querySelectorAll('.btn-resume-small');
    for (var i = 0; i < resumeBtns.length; i++) {
        resumeBtns[i].onclick = function(e) {
            e.stopPropagation();
            var oldJobId = this.getAttribute('data-resume');
            resumeStackJob(oldJobId);
        };
    }

    // Delete button handlers
    var deleteBtns = container.querySelectorAll('.btn-delete-small');
    for (var i = 0; i < deleteBtns.length; i++) {
        deleteBtns[i].onclick = function(e) {
            e.stopPropagation();
            var delJobId = this.getAttribute('data-delete');
            if (confirm('Delete stack job ' + delJobId + '? This removes the DB record and all output files.')) {
                deleteStackJob(delJobId);
            }
        };
    }
}

async function deleteStackJob(jobId) {
    try {
        var resp = await fetch('/api/stack/delete/' + jobId, {
            method: 'DELETE'
        });
        var data = await resp.json();
        if (data.error) throw new Error(data.error);
        loadHistory();
    } catch(err) {
        alert('Delete failed: ' + err.message);
    }
}

async function resumeStackJob(oldJobId) {
    var btn = document.getElementById('stack-run-btn');
    btn.disabled = true;
    btn.textContent = 'Resuming...';
    showView('running');
    document.getElementById('stack-running-status').textContent = 'Resuming job ' + oldJobId + '...';
    document.getElementById('stack-running-fill').style.width = '0%';

    try {
        var resp = await fetch('/api/stack/resume/' + oldJobId, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        var data = await resp.json();
        if (data.error) throw new Error(data.error);

        currentJobId = data.job_id;
        document.getElementById('stack-running-status').textContent = 'Resumed as job ' + currentJobId + '...';
        startPolling();
        loadHistory();
    } catch(err) {
        showError(err.message);
        btn.disabled = false;
        btn.textContent = '▶ Run Stack';
    }
}

// ─── View Management ──────────────────────────────────────────────────
function showView(view) {
    var views = ['idle', 'running', 'complete', 'error'];
    for (var i = 0; i < views.length; i++) {
        var el = document.getElementById('stack-' + views[i]);
        if (el) el.style.display = views[i] === view ? '' : 'none';
    }
}

function showError(msg) {
    showView('error');
    document.getElementById('stack-error-msg').textContent = msg;
}

// ─── Utilities ────────────────────────────────────────────────────────
function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

})();
