/* ═══════════════════════════════════════════════════════════════════
   SETI MISSION CONTROL - JAVASCRIPT
   All data is REAL from the pipeline. No simulations.
   ═══════════════════════════════════════════════════════════════════ */

// ─── State ────────────────────────────────────────────────────────────
let mcState = {
    active: false,
    scanId: null,
    target: '---',
    status: 'IDLE',
    subBandsDone: 0,
    subBandsTotal: 0,
    currentSubBand: 0,
    currentFreq: 0,
    currentFreqStart: 0,
    currentFreqStop: 0,
    freqStart: 2744,
    freqEnd: 3324,
    totalHits: 0,
    onHits: 0,
    offHits: 0,
    recentHits: [],
    scanStartTime: null,
    subBandTimestamps: [],
    speed: 0,
    spectra: [],
    spectraFreqs: [],
    spectraMeta: null,
    polling: false,
    // Fix 2/7: file tracking
    currentFile: '',
    currentFileIndex: 0,
    fileTotal: 0,
    fileHits: 0,
};

const POLL_INTERVAL = 2000;
const SPECTRUM_POLL = 3000;

// ─── Init ─────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initStarfield();
    initFreqMap();
    initSpectrum();
    pollMissionData();
    setInterval(pollMissionData, POLL_INTERVAL);
    setInterval(updateElapsed, 1000);
    setInterval(pollSpectrum, SPECTRUM_POLL);
});

// ─── Starfield Background ─────────────────────────────────────────────
function initStarfield() {
    const canvas = document.getElementById('starfield');
    const ctx = canvas.getContext('2d');
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;

    const stars = [];
    const numStars = 200;
    for (let i = 0; i < numStars; i++) {
        stars.push({
            x: Math.random() * canvas.width,
            y: Math.random() * canvas.height,
            r: Math.random() * 1.2 + 0.3,
            opacity: Math.random() * 0.5 + 0.2,
            twinkleSpeed: Math.random() * 0.02 + 0.005,
            phase: Math.random() * Math.PI * 2,
        });
    }

    function drawStars() {
        ctx.fillStyle = '#000510';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        for (const s of stars) {
            s.phase += s.twinkleSpeed;
            const alpha = s.opacity * (0.5 + 0.5 * Math.sin(s.phase));
            ctx.fillStyle = `rgba(200,220,255,${alpha})`;
            ctx.beginPath();
            ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
            ctx.fill();
        }
        requestAnimationFrame(drawStars);
    }
    drawStars();

    window.addEventListener('resize', () => {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
    });
}

// ─── Frequency Coverage Map ───────────────────────────────────────────
let freqMapCanvas, freqMapCtx;

function initFreqMap() {
    freqMapCanvas = document.getElementById('freq-map-canvas');
    freqMapCtx = freqMapCanvas.getContext('2d');
    resizeFreqMap();
    window.addEventListener('resize', resizeFreqMap);
    requestAnimationFrame(animateFreqMap);
}

function resizeFreqMap() {
    const rect = freqMapCanvas.parentElement.getBoundingClientRect();
    freqMapCanvas.width = rect.width;
    freqMapCanvas.height = rect.height;
}

function animateFreqMap() {
    drawFreqMap();
    requestAnimationFrame(animateFreqMap);
}

function drawFreqMap() {
    const ctx = freqMapCtx;
    if (!ctx) return;
    const w = freqMapCanvas.width;
    const h = freqMapCanvas.height;
    if (w === 0 || h === 0) return;

    ctx.clearRect(0, 0, w, h);

    const fStart = mcState.freqStart;
    const fEnd = mcState.freqEnd;
    const fRange = fEnd - fStart;
    if (fRange <= 0) return;

    const padX = 40;
    const padY = 30;
    const barY = padY + 10;
    const barH = h - barY - 20;
    const barW = w - padX * 2;

    // Background
    ctx.fillStyle = '#020812';
    ctx.fillRect(padX, barY, barW, barH);

    const total = mcState.subBandsTotal || 1;
    const done = mcState.subBandsDone;
    const current = mcState.currentSubBand;
    const subBandWidth = barW / total;

    // Draw each sub-band segment
    for (let i = 0; i < total; i++) {
        const x = padX + i * subBandWidth;
        const segW = Math.max(subBandWidth - 1, 0.5);

        if (i < done) {
            ctx.fillStyle = 'rgba(0,255,0,0.35)';
            ctx.fillRect(x, barY, segW, barH);
            ctx.fillStyle = 'rgba(0,255,0,0.6)';
            ctx.fillRect(x, barY, segW, 2);
        } else if (i === current && mcState.active) {
            const pulse = 0.3 + 0.3 * Math.sin(Date.now() * 0.005);
            ctx.fillStyle = `rgba(0,255,255,${pulse})`;
            ctx.fillRect(x, barY, segW, barH);
            ctx.strokeStyle = `rgba(0,255,255,${0.5 + pulse * 0.5})`;
            ctx.lineWidth = 1;
            ctx.strokeRect(x + 0.5, barY + 0.5, segW - 1, barH - 1);
        } else {
            ctx.fillStyle = 'rgba(10,30,50,0.4)';
            ctx.fillRect(x, barY, segW, barH);
        }
    }

    // Frequency tick marks every 50 MHz
    ctx.strokeStyle = '#1a3050';
    ctx.fillStyle = '#446677';
    ctx.font = '10px Consolas, monospace';
    ctx.textAlign = 'center';
    const tickInterval = 50;
    const startTick = Math.ceil(fStart / tickInterval) * tickInterval;
    for (let f = startTick; f <= fEnd; f += tickInterval) {
        const x = padX + ((f - fStart) / fRange) * barW;
        ctx.beginPath();
        ctx.moveTo(x, barY);
        ctx.lineTo(x, barY + barH + 5);
        ctx.stroke();
        ctx.fillText(f.toFixed(0), x, barY + barH + 18);
    }

    // Border
    ctx.strokeStyle = '#0a2a4e';
    ctx.lineWidth = 1;
    ctx.strokeRect(padX, barY, barW, barH);

    // Labels
    ctx.fillStyle = '#6699aa';
    ctx.font = '10px Consolas, monospace';
    ctx.textAlign = 'left';
    ctx.fillText('FREQUENCY COVERAGE (' + fStart.toFixed(0) + '\u2013' + fEnd.toFixed(0) + ' MHz)', padX, 14);

    if (total > 1) {
        ctx.textAlign = 'right';
        ctx.fillStyle = '#00ffff';
        ctx.fillText(done + '/' + total + ' (' + ((done / total) * 100).toFixed(1) + '%)', padX + barW, 14);
    }

    // Current frequency marker
    if (mcState.currentFreq > 0 && mcState.active) {
        const fx = padX + ((mcState.currentFreq - fStart) / fRange) * barW;
        if (fx >= padX && fx <= padX + barW) {
            ctx.strokeStyle = '#ffaa00';
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.moveTo(fx, barY - 3);
            ctx.lineTo(fx, barY + barH + 3);
            ctx.stroke();
            ctx.fillStyle = '#ffaa00';
            ctx.beginPath();
            ctx.moveTo(fx - 5, barY - 3);
            ctx.lineTo(fx + 5, barY - 3);
            ctx.lineTo(fx, barY + 3);
            ctx.closePath();
            ctx.fill();
        }
    }
}

// ─── Live Spectrum (canvas waterfall with REAL data) ───────────────
let spectrumCanvas, spectrumCtx;
let spectrumScrollRows = [];  // accumulated rows: {data, subband, fStart, fStop}
const MAX_SCROLL_ROWS = 1000;  // Enough for all 794 sub-bands per file
let spectrumCurrentFreqs = null;
let spectrumBandStart = 2744;  // full scan band
let spectrumBandEnd = 3324;

function initSpectrum() {
    spectrumCanvas = document.getElementById('spectrum-canvas');
    if (!spectrumCanvas) return;
    spectrumCtx = spectrumCanvas.getContext('2d');
    resizeSpectrumCanvas();
    window.addEventListener('resize', resizeSpectrumCanvas);
}

function resizeSpectrumCanvas() {
    if (!spectrumCanvas) return;
    const parent = spectrumCanvas.parentElement;
    const rect = parent.getBoundingClientRect();
    spectrumCanvas.width = rect.width;
    spectrumCanvas.height = rect.height;
    drawSpectrum();
}

// Viridis-like color map: 0=dark purple, 0.25=blue, 0.5=teal, 0.75=green-yellow, 1=yellow
function powerToColor(norm) {
    norm = Math.max(0, Math.min(1, norm));
    if (norm < 0.1) {
        const t = norm / 0.1;
        return [Math.round(t * 68), Math.round(t * 1), Math.round(t * 84)];
    } else if (norm < 0.25) {
        const t = (norm - 0.1) / 0.15;
        return [Math.round(68 + t * 5), Math.round(1 + t * 70), Math.round(84 + t * 110)];
    } else if (norm < 0.45) {
        const t = (norm - 0.25) / 0.20;
        return [Math.round(73 - t * 30), Math.round(71 + t * 100), Math.round(194 - t * 10)];
    } else if (norm < 0.65) {
        const t = (norm - 0.45) / 0.20;
        return [Math.round(43 + t * 60), Math.round(171 + t * 70), Math.round(184 - t * 110)];
    } else if (norm < 0.85) {
        const t = (norm - 0.65) / 0.20;
        return [Math.round(103 + t * 150), Math.round(241 - t * 20), Math.round(74 - t * 40)];
    } else {
        const t = (norm - 0.85) / 0.15;
        return [253, Math.round(221 + t * 30), Math.round(34 + t * 20)];
    }
}

function drawSpectrum() {
    const ctx = spectrumCtx;
    if (!ctx || !spectrumCanvas) return;
    const w = spectrumCanvas.width;
    const h = spectrumCanvas.height;
    if (w === 0 || h === 0) return;

    ctx.fillStyle = '#020812';
    ctx.fillRect(0, 0, w, h);

    if (!spectrumScrollRows || spectrumScrollRows.length === 0) return;

    const rows = spectrumScrollRows;
    const nRows = rows.length;
    const padL = 55, padR = 10, padT = 8, padB = 30;
    const plotW = w - padL - padR;
    const plotH = h - padT - padB;

    // Full band range
    const bandStart = spectrumBandStart;
    const bandEnd = spectrumBandEnd;
    const bandRange = bandEnd - bandStart;

    // Each subband row covers its own freq slice mapped onto the full band x-axis
    // We render at full plotW resolution for smooth horizontal interpolation
    const nXPixels = Math.floor(plotW);

    for (let row = 0; row < nRows; row++) {
        const rowData = rows[row];
        const data = rowData.data;
        const nCols = data.length;

        const y = padT + row * (plotH / nRows);
        const rowH = Math.ceil(plotH / nRows) + 1;

        // Stretch each subband's data across the FULL band width
        // so every row fills 100% of the x-axis. RFI at a given frequency
        // will show as vertical streaks across rows.
        for (let px = 0; px < plotW; px++) {
            const frac = px / plotW;
            const dataIdx = Math.min(nCols - 1, Math.floor(frac * nCols));
            const val = data[dataIdx];
            const norm = (val - 82) / 25;
            const [r, g, b] = powerToColor(norm);
            ctx.fillStyle = `rgb(${r},${g},${b})`;
            ctx.fillRect(padL + px, y, 1, rowH);
        }
    }

    // Axis labels
    ctx.fillStyle = '#446677';
    ctx.font = '9px Consolas, monospace';

    // Y-axis (subband number, newest at top)
    ctx.textAlign = 'right';
    for (let i = 0; i <= 4; i++) {
        const rowIdx = Math.round((nRows - 1) * i / 4);
        const sbNum = rows[rowIdx] ? rows[rowIdx].subband : 0;
        const y = padT + (plotH * i / 4);
        ctx.fillText('sub ' + sbNum, padL - 4, y + 3);
        ctx.strokeStyle = '#0a1a2e';
        ctx.beginPath();
        ctx.moveTo(padL - 2, y);
        ctx.lineTo(padL, y);
        ctx.stroke();
    }

    // X-axis (full band frequency)
    ctx.textAlign = 'center';
    const tickInterval = bandRange > 400 ? 100 : 50;
    const startTick = Math.ceil(bandStart / tickInterval) * tickInterval;
    for (let f = startTick; f <= bandEnd; f += tickInterval) {
        const x = padL + ((f - bandStart) / bandRange) * plotW;
        if (x < padL || x > padL + plotW) continue;
        ctx.fillText(f.toFixed(0), x, padT + plotH + 14);
        ctx.strokeStyle = '#0a1a2e';
        ctx.beginPath();
        ctx.moveTo(x, padT + plotH);
        ctx.lineTo(x, padT + plotH + 3);
        ctx.stroke();
    }
    ctx.fillStyle = '#6699aa';
    ctx.fillText('Frequency (MHz)', padL + plotW / 2, padT + plotH + 26);

    // Border
    ctx.strokeStyle = '#0a2a4e';
    ctx.lineWidth = 1;
    ctx.strokeRect(padL, padT, plotW, plotH);
}

function updateSpectrum(spectra, freqs, subbandIdx) {
    if (!spectra || spectra.length === 0) return;
    const placeholder = document.getElementById('spectrum-placeholder');
    if (placeholder) placeholder.style.display = 'none';
    if (!spectrumCanvas) initSpectrum();

    // Compute mean spectrum across all time rows
    const nTimes = spectra.length;
    const nFreqs = spectra[0].length;
    const meanRow = new Array(nFreqs);
    for (let c = 0; c < nFreqs; c++) {
        let sum = 0;
        for (let t = 0; t < nTimes; t++) {
            sum += spectra[t][c];
        }
        meanRow[c] = sum / nTimes;
    }

    // Track frequency range for this subband
    const fStart = freqs[0];
    const fStop = freqs[freqs.length - 1];

    spectrumCurrentFreqs = freqs;

    // Add new row at top, cap at MAX_SCROLL_ROWS
    spectrumScrollRows.unshift({ data: meanRow, subband: subbandIdx || 0, fStart: fStart, fStop: fStop });
    if (spectrumScrollRows.length > MAX_SCROLL_ROWS) {
        spectrumScrollRows.pop();
    }

    drawSpectrum();
}

// ─── Spectrum Polling (REAL data from API) ───────────────────────────
async function pollSpectrum() {
    if (!mcState.active) return;
    try {
        const resp = await fetch('/api/scan/spectrum');
        const data = await resp.json();
        if (data.spectra && data.spectra.length > 0) {
            mcState.spectra = data.spectra;
            mcState.spectraFreqs = data.freqs || [];
            mcState.spectraMeta = { n_times: data.n_times, n_freqs: data.n_freqs };
            updateSpectrum(data.spectra, data.freqs, data.subband_index);
        }
    } catch(e) {
        // Spectrum not available yet, will retry
    }
}

// ─── Hit Ticker ──────────────────────────────────────────────────────
let seenHits = new Set();

function addTickerEntry(hit) {
    const ticker = document.getElementById('hit-ticker');
    if (!ticker) return;
    const now = new Date();
    const ts = String(now.getHours()).padStart(2, '0') + ':' +
               String(now.getMinutes()).padStart(2, '0') + ':' +
               String(now.getSeconds()).padStart(2, '0');

    const snr = hit.snr || 0;
    const drift = hit.drift_rate || 0;
    const cc = hit.coarse_chan !== null && hit.coarse_chan !== undefined ? hit.coarse_chan : '';
    const highSnr = snr > 25;
    const star = highSnr ? '\u2605' : '  ';

    const line = document.createElement('div');
    line.className = 'ticker-line ' + (highSnr ? 'on' : 'off');
    if (highSnr) line.classList.add('flash');
    line.innerHTML =
        '<span class="timestamp">[' + ts + ']</span> ' +
        '<span class="star">' + star + '</span> ' +
        'SNR: <span class="snr">' + snr.toFixed(1) + '</span> | ' +
        'drift: <span class="drift">' + (drift >= 0 ? '+' : '') + drift.toFixed(3) + ' Hz/s</span>' +
        (cc !== '' ? ' | cc ' + cc : '');

    if (ticker.firstChild) {
        ticker.insertBefore(line, ticker.firstChild);
    } else {
        ticker.appendChild(line);
    }

    while (ticker.children.length > 200) {
        ticker.removeChild(ticker.lastChild);
    }
}

function clearTicker() {
    const ticker = document.getElementById('hit-ticker');
    if (ticker) ticker.innerHTML = '<div class="ticker-empty">Awaiting detections...</div>';
}

// ─── Stats Panel ─────────────────────────────────────────────────────
function updateStats() {
    const s = mcState;

    document.getElementById('stat-subbands').textContent = s.subBandsDone + ' / ' + (s.subBandsTotal || '?');
    document.getElementById('stat-subbands').className = 'stat-value ' + (s.subBandsTotal > 0 ? 'cyan' : 'dim');

    const pct = s.subBandsTotal > 0 ? (s.subBandsDone / s.subBandsTotal * 100) : 0;
    document.getElementById('stat-progress').textContent = pct.toFixed(1) + '%';
    document.getElementById('stat-progress').className = 'stat-value ' + (pct > 0 ? 'cyan' : 'dim');

    document.getElementById('stat-speed').textContent = s.speed > 0 ? s.speed.toFixed(1) + '/min' : '---';
    document.getElementById('stat-speed').className = 'stat-value ' + (s.speed > 0 ? 'amber' : 'dim');

    document.getElementById('stat-hits').textContent = s.totalHits.toLocaleString();
    document.getElementById('stat-hits').className = 'stat-value ' + (s.totalHits > 0 ? 'green' : 'dim');

    document.getElementById('stat-on').textContent = s.onHits.toLocaleString();
    document.getElementById('stat-on').className = 'stat-value ' + (s.onHits > 0 ? 'green' : 'dim');
    document.getElementById('stat-off').textContent = s.offHits.toLocaleString();
    document.getElementById('stat-off').className = 'stat-value ' + (s.offHits > 0 ? 'red' : 'dim');

    // Fix 2/7: Show current file info
    var fileEl = document.getElementById('stat-file');
    if (fileEl) {
        if (s.currentFile) {
            var fileIdxStr = s.fileTotal > 0 ? 'File ' + s.currentFileIndex + '/' + s.fileTotal : 'File ' + s.currentFileIndex;
            fileEl.textContent = fileIdxStr + ': ' + s.currentFile;
            fileEl.className = 'stat-value cyan';
        } else {
            fileEl.textContent = '---';
            fileEl.className = 'stat-value dim';
        }
    }
    // Fix 8: Show per-file hits
    var fileHitsEl = document.getElementById('stat-file-hits');
    if (fileHitsEl) {
        fileHitsEl.textContent = s.fileHits > 0 ? s.fileHits.toLocaleString() : '0';
        fileHitsEl.className = 'stat-value ' + (s.fileHits > 0 ? 'amber' : 'dim');
    }

    document.getElementById('stat-freq').textContent = s.currentFreq > 0 ? s.currentFreq.toFixed(3) + ' MHz' : '---';
    document.getElementById('stat-freq').className = 'stat-value ' + (s.currentFreq > 0 ? 'amber' : 'dim');

    if (s.speed > 0 && s.subBandsTotal > 0 && s.subBandsDone < s.subBandsTotal) {
        const remaining = s.subBandsTotal - s.subBandsDone;
        const etaMin = remaining / s.speed;
        const h = Math.floor(etaMin / 60);
        const m = Math.round(etaMin % 60);
        document.getElementById('stat-eta').textContent = h > 0 ? h + 'h ' + m + 'm' : m + 'm';
        document.getElementById('stat-eta').className = 'stat-value blue';
    } else {
        document.getElementById('stat-eta').textContent = '---';
        document.getElementById('stat-eta').className = 'stat-value dim';
    }

    updateElapsed();

    // Header status
    document.getElementById('mc-target').textContent = s.target;
    const statusEl = document.getElementById('mc-status');
    const dotEl = document.getElementById('mc-status-dot');
    if (s.active) {
        statusEl.textContent = 'SCANNING';
        statusEl.className = 'value green';
        dotEl.className = 'status-dot scanning';
    } else if (s.status === 'complete') {
        statusEl.textContent = 'COMPLETE';
        statusEl.className = 'value cyan';
        dotEl.className = 'status-dot complete';
    } else {
        statusEl.textContent = 'IDLE';
        statusEl.className = 'value dim';
        dotEl.className = 'status-dot idle';
    }

    // Progress bar
    const fillEl = document.getElementById('mc-progress-fill');
    const textEl = document.getElementById('mc-progress-text');
    if (s.subBandsTotal > 0) {
        fillEl.style.width = pct + '%';
        textEl.textContent = s.subBandsDone + '/' + s.subBandsTotal + ' sub-bands (' + pct.toFixed(1) + '%)';
    } else {
        fillEl.style.width = '0%';
        textEl.textContent = 'Awaiting scan...';
    }
}

function updateElapsed() {
    if (!mcState.scanStartTime || !mcState.active) return;
    const elapsed = (Date.now() - mcState.scanStartTime) / 1000;
    const h = Math.floor(elapsed / 3600);
    const m = Math.floor((elapsed % 3600) / 60);
    const s = Math.floor(elapsed % 60);
    const el = document.getElementById('stat-elapsed');
    if (h > 0) {
        el.textContent = h + 'h ' + m + 'm';
    } else if (m > 0) {
        el.textContent = m + 'm ' + s + 's';
    } else {
        el.textContent = s + 's';
    }
    el.className = 'stat-value ' + (mcState.active ? 'amber' : 'dim');

    const headerTime = document.getElementById('mc-elapsed');
    if (headerTime) {
        if (h > 0) headerTime.textContent = h + 'h ' + m + 'm';
        else if (m > 0) headerTime.textContent = m + 'm ' + s + 's';
        else headerTime.textContent = s + 's';
    }
}

// ─── Log ─────────────────────────────────────────────────────────────
function updateLog(logLines) {
    const logEl = document.getElementById('mc-log');
    if (!logEl) return;
    if (!logLines || logLines.length === 0) {
        logEl.innerHTML = '<div class="mc-log-line info">No scan log output yet.</div>';
        return;
    }
    // Show all lines from backend (now 500), preserve scroll position
    const wasNearBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 50;
    const prevScrollTop = logEl.scrollTop;
    // Rebuild from backend log_tail (now 500 lines)
    logEl.innerHTML = '';
    for (let i = 0; i < logLines.length; i++) {
        let cls = 'mc-log-line info';
        if (logLines[i].indexOf('hit') !== -1 || logLines[i].indexOf('Hit') !== -1) cls = 'mc-log-line hit';
        if (logLines[i].indexOf('ERROR') !== -1 || logLines[i].indexOf('Error') !== -1) cls = 'mc-log-line error';
        const text = logLines[i].length > 120 ? logLines[i].substring(0, 117) + '...' : logLines[i];
        const div = document.createElement('div');
        div.className = cls;
        div.textContent = text;
        logEl.appendChild(div);
    }
    if (wasNearBottom) {
        logEl.scrollTop = logEl.scrollHeight;
    } else {
        logEl.scrollTop = prevScrollTop;
    }
}

function escapeHtml(text) {
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
}

// ─── Speed Calculation ────────────────────────────────────────────────
function calcSpeed() {
    const now = Date.now();
    const cutoff = now - 5 * 60 * 1000;
    mcState.subBandTimestamps = mcState.subBandTimestamps.filter(t => t > cutoff);

    if (mcState.subBandTimestamps.length >= 2) {
        const elapsedMin = (now - mcState.subBandTimestamps[0]) / 60000;
        const count = mcState.subBandTimestamps.length;
        if (elapsedMin > 0) {
            mcState.speed = count / elapsedMin;
        }
    }
}

// ─── Polling (REAL data from enhanced API) ───────────────────────────
async function pollMissionData() {
    if (mcState.polling) return;
    mcState.polling = true;
    try {
        const statusResp = await fetch('/api/scan/status');
        const statusData = await statusResp.json();

        mcState.active = statusData.active;
        mcState.scanId = statusData.scan_id;

        if (statusData.active && !mcState.scanStartTime) {
            mcState.scanStartTime = Date.now();
        }
        if (!statusData.active && mcState.scanStartTime) {
            mcState.status = 'complete';
        }

        // Use structured fields from enhanced API
        if (statusData.target) mcState.target = statusData.target;
        if (statusData.freq_start) mcState.freqStart = statusData.freq_start;
        if (statusData.freq_end) mcState.freqEnd = statusData.freq_end;
        if (statusData.sub_bands_total) mcState.subBandsTotal = statusData.sub_bands_total;
        if (statusData.sub_bands_done !== undefined) {
            if (statusData.sub_bands_done > mcState.subBandsDone) {
                mcState.subBandsDone = statusData.sub_bands_done;
                mcState.currentSubBand = statusData.sub_bands_done;
                mcState.subBandTimestamps.push(Date.now());
            }
        }
        if (statusData.current_sub_band !== undefined) mcState.currentSubBand = statusData.current_sub_band;
        if (statusData.current_freq) mcState.currentFreq = statusData.current_freq;
        if (statusData.current_freq_start) mcState.currentFreqStart = statusData.current_freq_start;
        if (statusData.current_freq_stop) mcState.currentFreqStop = statusData.current_freq_stop;
        if (statusData.total_hits !== undefined) mcState.totalHits = statusData.total_hits;

        // Fix 2/3/7/8: Pick up new structured fields from enhanced API
        if (statusData.current_file !== undefined) {
            // File transition: clear waterfall so new file builds up fresh
            if (mcState.currentFile && statusData.current_file !== mcState.currentFile) {
                spectrumScrollRows = [];
                seenHits.clear();
                clearTicker();
            }
            mcState.currentFile = statusData.current_file;
        }
        if (statusData.current_file_index !== undefined) mcState.currentFileIndex = statusData.current_file_index;
        if (statusData.file_total !== undefined) mcState.fileTotal = statusData.file_total;
        if (statusData.on_hits !== undefined) mcState.onHits = statusData.on_hits;
        if (statusData.off_hits !== undefined) mcState.offHits = statusData.off_hits;
        if (statusData.file_hits !== undefined) mcState.fileHits = statusData.file_hits;

        // Process recent hits from structured API
        if (statusData.recent_hits) {
            for (const hit of statusData.recent_hits) {
                const hitKey = (hit.snr || 0) + '_' + (hit.drift_rate || 0) + '_' + (hit.coarse_chan || 0) + '_' + (hit.index || 0);
                if (!seenHits.has(hitKey)) {
                    seenHits.add(hitKey);
                    addTickerEntry(hit);
                }
            }
        }

        // Fallback: parse log lines for any data the structured fields missed
        const logLines = statusData.log_tail || [];
        parseLogFallback(logLines);
        updateLog(logLines);

        // Fetch hit counts from stats API (only when scan is NOT active, since
        // Fix 3 now tracks ON/OFF hits in real-time from the pipeline parser)
        if (!mcState.active) {
            let statsUrl = '/api/stats';
            if (mcState.scanId) statsUrl += '?scan_id=' + encodeURIComponent(mcState.scanId);
            try {
                const statsResp = await fetch(statsUrl);
                const statsData = await statsResp.json();
                if (statsData.total_hits) mcState.totalHits = statsData.total_hits;
                if (statsData.on_hits) mcState.onHits = statsData.on_hits;
                if (statsData.off_hits) mcState.offHits = statsData.off_hits;
            } catch(e) { /* ignore */ }
        }

        calcSpeed();
        updateStats();

        // Spectrum canvas inits on first data arrival

    } catch(e) {
        console.error('Mission Control poll error:', e);
    } finally {
        mcState.polling = false;
    }
}

// ─── Log Fallback Parser ─────────────────────────────────────────────
function parseLogFallback(lines) {
    for (const line of lines) {
        // Subband progress: [X/Y] FFFF-FFFF MHz
        const subMatch = line.match(/\[(\d+)\/(\d+)\]\s+([\d.]+)-([\d.]+)\s+MHz/);
        if (subMatch) {
            mcState.subBandsTotal = parseInt(subMatch[1]) > mcState.subBandsTotal ? parseInt(subMatch[1]) : mcState.subBandsTotal;
            // Actually [X/Y] means X out of Y, so total is Y
            mcState.subBandsTotal = parseInt(subMatch[2]);
            const subNum = parseInt(subMatch[1]);
            if (subNum > mcState.subBandsDone) {
                mcState.subBandsDone = subNum;
                mcState.currentSubBand = subNum;
                mcState.subBandTimestamps.push(Date.now());
            }
            mcState.currentFreqStart = parseFloat(subMatch[3]);
            mcState.currentFreqStop = parseFloat(subMatch[4]);
            mcState.currentFreq = (mcState.currentFreqStart + mcState.currentFreqStop) / 2;
        }

        // Top hit from turbo_seti: find_doppler.N INFO     Top hit found! SNR X, Drift Rate Y, index Z
        const topMatch = line.match(/find_doppler\.(\d+)\s+INFO\s+Top hit found!.*SNR\s+([\d.]+).*Drift Rate\s+([-\d.]+).*index\s+(\d+)/);
        if (topMatch) {
            const hit = {
                coarse_chan: parseInt(topMatch[1]),
                snr: parseFloat(topMatch[2]),
                drift_rate: parseFloat(topMatch[3]),
                index: parseInt(topMatch[4]),
            };
            const hitKey = hit.snr + '_' + hit.drift_rate + '_' + hit.coarse_chan + '_' + hit.index;
            if (!seenHits.has(hitKey)) {
                seenHits.add(hitKey);
                addTickerEntry(hit);
            }
        }

        // Target name from various log formats
        const targetMatch = line.match(/(?:target|Target)[:\s]+([A-Z][A-Z0-9_]+)/i);
        if (targetMatch) {
            mcState.target = targetMatch[1];
        }
    }
}