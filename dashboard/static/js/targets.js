/* Target Registry UI (Phase 3A)
 * Panel: add targets (SIMBAD-resolved), view BL fine-res availability,
 * delete. Talks to /api/targets* endpoints backed by src/target_registry.py.
 */
'use strict';

function escReg(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;')
        .replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

async function loadRegistry() {
    var listEl = document.getElementById('registry-target-list');
    if (!listEl) return;
    try {
        var resp = await fetch('/api/registry');
        var data = await resp.json();
        if (data.error) {
            listEl.innerHTML = '<p style="color:#ef5350;">' + escReg(data.error) + '</p>';
            return;
        }
        var targets = data.targets || [];
        if (!targets.length) {
            listEl.innerHTML = '<p style="color:#546e7a;">Registry empty.</p>';
            return;
        }
        var html = '<table style="width:100%;border-collapse:collapse;font-size:0.85em;">' +
            '<tr style="color:#90a4ae;text-align:left;">' +
            '<th style="padding:4px 6px;">Target</th>' +
            '<th style="padding:4px 6px;">RA (h)</th>' +
            '<th style="padding:4px 6px;">Dec (&deg;)</th>' +
            '<th style="padding:4px 6px;">Src</th>' +
            '<th style="padding:4px 6px;">BL fine</th>' +
            '<th style="padding:4px 6px;"></th></tr>';
        for (var i = 0; i < targets.length; i++) {
            var t = targets[i];
            var blTxt = 'unknown';
            if (t.bl_fine_files != null) {
                if (t.bl_fine_files > 0) {
                    blTxt = '<span style="color:#66bb6a;">' + t.bl_fine_files +
                            ' fine / ' + (t.bl_fine_epochs || 0) + ' ep</span>';
                } else if ((t.bl_total_files || 0) > 0) {
                    blTxt = '<span style="color:#ffb74d;">' + t.bl_total_files +
                            ' files, no fine-res</span>';
                } else {
                    blTxt = '<span style="color:#90a4ae;">no BL data</span>';
                }
                if (t.bl_query_name && t.bl_query_name !== t.name) {
                    blTxt += ' <span style="color:#546e7a;">[' +
                             escReg(t.bl_query_name) + ']</span>';
                }
            }
            html += '<tr style="border-top:1px solid #2a3b4d;">' +
                '<td style="padding:4px 6px;color:#4fc3f7;">' + escReg(t.name) +
                    (t.display_name && t.display_name !== t.name ?
                        ' <span style="color:#90a4ae;">(' + escReg(t.display_name) + ')</span>' : '') + '</td>' +
                '<td style="padding:4px 6px;">' + (t.ra_hours != null ? Number(t.ra_hours).toFixed(4) : '-') + '</td>' +
                '<td style="padding:4px 6px;">' + (t.dec_deg != null ? Number(t.dec_deg).toFixed(4) : '-') + '</td>' +
                '<td style="padding:4px 6px;color:#90a4ae;">' + escReg(t.coord_source || '-') + '</td>' +
                '<td style="padding:4px 6px;">' + blTxt + '</td>' +
                '<td style="padding:4px 6px;white-space:nowrap;">' +
                    '<button class="btn-small" onclick="registryBLCheck(\'' + escReg(t.name) + '\', this)">Check BL</button> ' +
                    '<button class="btn-small btn-danger-small" onclick="registryDelete(\'' + escReg(t.name) + '\')">Del</button>' +
                '</td></tr>';
        }
        html += '</table>';
        listEl.innerHTML = html;
    } catch (e) {
        listEl.innerHTML = '<p style="color:#ef5350;">' + escReg(e.message) + '</p>';
    }
}

async function registrySimbadPreview() {
    var name = (document.getElementById('registry-name-input') || {}).value || '';
    name = name.trim();
    var prevEl = document.getElementById('registry-simbad-preview');
    if (!name) {
        prevEl.innerHTML = '<span style="color:#ef5350;">Enter a name first.</span>';
        return;
    }
    prevEl.innerHTML = '<span style="color:#8ab4f8;">Searching SIMBAD...</span>';
    try {
        var resp = await fetch('/api/registry/simbad?name=' + encodeURIComponent(name));
        var data = await resp.json();
        if (data.error) {
            prevEl.innerHTML = '<span style="color:#ef5350;">' + escReg(data.error) + '</span>';
            return;
        }
        var results = data.results || [];
        if (!results.length) {
            prevEl.innerHTML = '<span style="color:#ef5350;">No SIMBAD match for "' + escReg(name) + '".</span>';
            return;
        }
        var html = 'SIMBAD top matches: ';
        for (var i = 0; i < results.length; i++) {
            var r = results[i];
            html += '<span style="color:#66bb6a;">' + escReg(r.main_id) + '</span> ' +
                    '(ra ' + Number(r.ra_hours).toFixed(4) + 'h, dec ' +
                    Number(r.dec_deg).toFixed(3) + '&deg;)' +
                    (i < results.length - 1 ? ' | ' : '');
        }
        prevEl.innerHTML = html;
    } catch (e) {
        prevEl.innerHTML = '<span style="color:#ef5350;">' + escReg(e.message) + '</span>';
    }
}

async function registryAdd() {
    var input = document.getElementById('registry-name-input');
    var prevEl = document.getElementById('registry-simbad-preview');
    var name = (input.value || '').trim();
    if (!name) {
        prevEl.innerHTML = '<span style="color:#ef5350;">Enter a target name.</span>';
        return;
    }
    prevEl.innerHTML = '<span style="color:#8ab4f8;">Adding "' + escReg(name) +
        '" (SIMBAD resolve + BL check)...</span>';
    try {
        var resp = await fetch('/api/registry', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name: name, check_bl: true}),
        });
        var data = await resp.json();
        if (data.error) {
            prevEl.innerHTML = '<span style="color:#ef5350;">' + escReg(data.error) + '</span>';
            return;
        }
        var t = data.target || {};
        var bl = t.bl_fine_files != null
            ? 'BL: ' + t.bl_fine_files + ' fine files, ' + (t.bl_fine_epochs || 0) + ' epochs'
            : 'BL: not checked';
        prevEl.innerHTML = '<span style="color:#66bb6a;">Added ' + escReg(t.name) +
            '</span> (' + escReg(t.coord_source) + ' coords, ' + escReg(bl) + ')';
        input.value = '';
        loadRegistry();
        // New target is now in the barycentric dropdown too
        if (typeof loadBarycentricTargets === 'function') loadBarycentricTargets();
    } catch (e) {
        prevEl.innerHTML = '<span style="color:#ef5350;">' + escReg(e.message) + '</span>';
    }
}

async function registryBLCheck(name, btnEl) {
    var doneChk = busyButton(btnEl, 'Checking...');
    try {
        var resp = await fetch('/api/registry/' + encodeURIComponent(name) + '/blcheck', {
            method: 'POST'});
        var data = await resp.json();
        doneChk();
        if (data.error) {
            alert('BL check failed: ' + data.error);
            return;
        }
        loadRegistry();
    } catch (e) {
        doneChk();
        alert('BL check failed: ' + e.message);
    }
}

async function registryDelete(name) {
    if (!confirm('Delete target "' + name + '" from the registry? ' +
                 'Scans and hits are not affected.')) return;
    try {
        var resp = await fetch('/api/registry/' + encodeURIComponent(name), {
            method: 'DELETE'});
        var data = await resp.json();
        if (data.error) {
            alert('Delete failed: ' + data.error);
            return;
        }
        loadRegistry();
    } catch (e) {
        alert('Delete failed: ' + e.message);
    }
}

document.addEventListener('DOMContentLoaded', loadRegistry);
