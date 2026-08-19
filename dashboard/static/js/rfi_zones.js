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
