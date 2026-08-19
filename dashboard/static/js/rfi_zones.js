// ─── RFI Zones manager (Stack page) ────────────────────────────────

async function loadRfiZones() {
    var list = document.getElementById('rfi-zones-list');
    if (!list) return;
    try {
        var r = await fetch('/api/rfi/zones');
        var d = await r.json();
        renderRfiZones(d.zones || {});
    } catch (e) {
        list.innerHTML = '<div class="rfi-zone-empty">Failed to load zones</div>';
    }
}

function renderRfiZones(zones) {
    var list = document.getElementById('rfi-zones-list');
    var countEl = document.getElementById('rfi-zone-count');
    var keys = Object.keys(zones).sort();
    var total = 0;
    keys.forEach(function(k) { total += zones[k].length; });
    if (countEl) countEl.textContent = total ? (' ' + total + ' active') : '';
    if (!keys.length) {
        list.innerHTML = '<div class="rfi-zone-empty">No zones defined</div>';
        return;
    }
    var html = '';
    keys.forEach(function(scope) {
        var isBand = scope.indexOf('/') !== -1;
        zones[scope].forEach(function(z) {
            total++;
            html += '<div class="rfi-zone-row" title="' +
                (z.reason || '').replace(/"/g, '&quot;') + '">' +
                '<span class="rfi-zone-scope ' + (isBand ? 'rfi-scope-band' : 'rfi-scope-epoch') + '">' +
                    scope + '</span>' +
                '<span class="rfi-zone-freq">' +
                    z.f_start.toFixed(3) + '–' + z.f_stop.toFixed(3) + ' MHz</span>' +
                '<button class="rfi-zone-del" title="delete zone"' +
                    ' data-scope="' + scope + '" data-fs="' + z.f_start + '" data-fe="' + z.f_stop + '">✕</button>' +
                '</div>';
        });
    });
    list.innerHTML = html;
    var dels = list.querySelectorAll('.rfi-zone-del');
    for (var i = 0; i < dels.length; i++) {
        dels[i].onclick = function() {
            deleteRfiZone(this.getAttribute('data-scope'),
                          parseFloat(this.getAttribute('data-fs')),
                          parseFloat(this.getAttribute('data-fe')));
        };
    }
}

async function deleteRfiZone(scope, fStart, fStop) {
    if (!confirm('Delete zone ' + fStart + '-' + fStop + ' MHz from ' + scope + '?')) return;
    try {
        var r = await fetch('/api/rfi/zones?scope=' + encodeURIComponent(scope) +
                            '&f_start=' + fStart + '&f_stop=' + fStop,
                            { method: 'DELETE' });
        var d = await r.json();
        if (d.error) { alert('Delete failed: ' + d.error); return; }
        loadRfiZones();
    } catch (e) {
        alert('Delete failed: ' + e.message);
    }
}

// ─── Zone-from-plot dialog ──────────────────────────────────────
// Called by stack.js plotly_selected: box-drag a frequency range on the
// stack spectrum, get a prefilled add-zone dialog.
function openZoneFromPlot(fStart, fStop) {
    closeZoneDialog();
    var ov = document.createElement('div');
    ov.id = 'rfi-zone-dialog-overlay';
    ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.55);' +
        'z-index:10000;display:flex;align-items:center;justify-content:center;';
    var dlg = document.createElement('div');
    dlg.className = 'rfi-zone-dialog';
    var epochOpts = '';
    var epCbs = document.querySelectorAll('#stack-epochs-list input[type="checkbox"][data-epoch]');
    for (var i = 0; i < epCbs.length; i++) {
        var lbl = epCbs[i].getAttribute('data-epoch') || '';
        if (/^\d{5}$/.test(lbl)) {
            epochOpts += '<option value="' + lbl + '">' + lbl + ' (this epoch only)</option>';
        }
    }
    dlg.innerHTML =
        '<div class="rfi-zone-dialog-title">🚫 Zone this range?</div>' +
        '<div class="rfi-zone-dialog-freq">' + fStart.toFixed(4) + ' – ' + fStop.toFixed(4) + ' MHz (' +
            (fStop - fStart).toFixed(3) + ' MHz wide)</div>' +
        '<label class="rfi-zone-dialog-label">Scope</label>' +
        '<select id="rfi-zd-scope" class="rfi-zone-dialog-input">' +
            '<option value="gbt/L" selected>gbt/L (all GBT L-band epochs)</option>' +
            '<option value="parkes/L">parkes/L (all Parkes L-band)</option>' +
            epochOpts +
        '</select>' +
        '<label class="rfi-zone-dialog-label">Reason</label>' +
        '<input id="rfi-zd-reason" class="rfi-zone-dialog-input" type="text" ' +
            'placeholder="what lives here? (required)">' +
        '<div class="rfi-zone-dialog-btns">' +
            '<button id="rfi-zd-cancel" class="btn-small">Cancel</button>' +
            '<button id="rfi-zd-ok" class="btn-small" style="background:#43a047;color:#fff;">+ Zone It</button>' +
        '</div>';
    ov.appendChild(dlg);
    document.body.appendChild(ov);
    ov.addEventListener('click', function(e) { if (e.target === ov) closeZoneDialog(); });
    document.getElementById('rfi-zd-cancel').onclick = closeZoneDialog;
    document.getElementById('rfi-zd-reason').focus();
    document.getElementById('rfi-zd-ok').onclick = async function() {
        var scope = document.getElementById('rfi-zd-scope').value;
        var reason = document.getElementById('rfi-zd-reason').value.trim();
        if (!reason) {
            document.getElementById('rfi-zd-reason').style.borderColor = '#ef5350';
            document.getElementById('rfi-zd-reason').focus();
            return;
        }
        try {
            var r = await fetch('/api/rfi/zones', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ scope: scope, f_start: fStart,
                                       f_stop: fStop, reason: reason })
            });
            var d = await r.json();
            if (d.error) { alert('Add failed: ' + d.error); return; }
            closeZoneDialog();
            loadRfiZones();
        } catch (e) { alert('Add failed: ' + e.message); }
    };
}

function closeZoneDialog() {
    var ov = document.getElementById('rfi-zone-dialog-overlay');
    if (ov) ov.parentNode.removeChild(ov);
}

document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeZoneDialog();
});

async function addRfiZoneFromForm() {
    var scope = (document.getElementById('rfi-zone-scope').value || '').trim();
    var fs = parseFloat(document.getElementById('rfi-zone-fstart').value);
    var fe = parseFloat(document.getElementById('rfi-zone-fstop').value);
    var reason = (document.getElementById('rfi-zone-reason').value || '').trim();
    if (!scope) { alert('Scope required: gbt/L or an epoch MJD like 57532'); return; }
    if (isNaN(fs) || isNaN(fe)) { alert('f_start and f_stop (MHz) required'); return; }
    if (!reason) { alert('Reason required (what lives here?)'); return; }
    try {
        var r = await fetch('/api/rfi/zones', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ scope: scope, f_start: fs, f_stop: fe, reason: reason })
        });
        var d = await r.json();
        if (d.error) { alert('Add failed: ' + d.error); return; }
        document.getElementById('rfi-zone-fstart').value = '';
        document.getElementById('rfi-zone-fstop').value = '';
        document.getElementById('rfi-zone-reason').value = '';
        loadRfiZones();
    } catch (e) {
        alert('Add failed: ' + e.message);
    }
}
