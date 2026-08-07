#!/usr/bin/env python3
"""Patch dashboard.js to add BL API pagination and filtering."""
import re

with open('dashboard/static/js/dashboard.js', encoding='utf-8') as f:
    src = f.read()

# Find the BL search section by function boundaries
# Replace from "async function searchBL" to "function onBLRowClick" inclusive
old_start = src.find('async function searchBL()')
old_end = src.find('function parseRA(')

if old_start == -1 or old_end == -1:
    print('ERROR: Could not find function boundaries')
    exit(1)

old_section = src[old_start:old_end]
print(f'Replacing {len(old_section)} chars of BL search code')

new_section = """var blResults = [];
var blPage = 0;
var blPageSize = 50;
var blFilterRes = 'all';
var blFilterType = 'all';

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

            var first = data.data[0];
            var raVal = first.ra || first.ra_hours;
            var decVal = first.decl || first.dec;
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
        var res = (obs.resolution || '').toLowerCase();
        var fileType = obs.file_type || '';
        var targetName = obs.target || obs.source_name || '';
        var isOn = fileType.indexOf('S') !== -1 || targetName.indexOf('_S_') !== -1;
        var isOff = fileType.indexOf('R') !== -1 || targetName.indexOf('_R_') !== -1;
        if (blFilterRes !== 'all' && res.indexOf(blFilterRes) === -1) return false;
        if (blFilterType === 'on' && !isOn) return false;
        if (blFilterType === 'off' && !isOff) return false;
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

    var html = '';
    html += '<div class="bl-summary">';
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
    html += '</div>';

    html += '<table class="bl-table"><thead><tr>';
    html += '<th>MJD</th><th>Target</th><th>RA</th><th>Dec</th><th>Type</th><th>Res</th><th>Size</th><th>DL</th>';
    html += '</tr></thead><tbody>';

    for (var i = 0; i < pageData.length; i++) {
        var obs = pageData[i];
        var rv = obs.ra || obs.ra_hours || '?';
        var dv = obs.decl || obs.dec || '?';
        var rH = typeof rv === 'number' ? rv / 15.0 : (parseRA(rv) || 0);
        var dD = typeof dv === 'number' ? dv : (parseDec(dv) || 0);
        var sn = (obs.target || obs.source_name || 'target').replace(/'/g, '');
        var sz = obs.size || obs.file_size;

        html += '<tr onclick="onBLRowClick(\\'' + sn + '\\',' + rH + ',' + dD + ')">';
        html += '<td>' + (obs.mjd || obs.tstart || '?') + '</td>';
        html += '<td>' + (obs.target || obs.source_name || '?') + '</td>';
        html += '<td>' + (typeof rv === 'number' ? rv.toFixed(3) : rv) + '</td>';
        html += '<td>' + (typeof dv === 'number' ? dv.toFixed(3) : dv) + '</td>';
        html += '<td>' + (obs.file_type || '') + '</td>';
        html += '<td>' + (obs.resolution || '?') + '</td>';
        html += '<td>' + (sz ? (sz / 1e9).toFixed(1) + ' GB' : '?') + '</td>';
        var u = obs.url || '';
        if (u) {
            html += '<td><a href="' + u + '" target="_blank" class="dl-link" onclick="event.stopPropagation()" title="Download">\\u2b07</a></td>';
        } else {
            html += '<td>\\u2014</td>';
        }
        html += '</tr>';
    }
    html += '</tbody></table>';

    if (totalPages > 1) {
        html += '<div class="bl-pagination">';
        html += '<button onclick="blPage=0;renderBLResults()"' + (blPage === 0 ? ' disabled' : '') + '>\\u00ab First</button>';
        html += '<button onclick="blPage=Math.max(0,blPage-1);renderBLResults()"' + (blPage === 0 ? ' disabled' : '') + '>\\u2039 Prev</button>';
        html += '<span class="bl-page-info">Page ' + (blPage + 1) + ' of ' + totalPages + ' (' + (startIdx + 1) + '-' + endIdx + ' of ' + filtered.length + ')</span>';
        html += '<button onclick="blPage=Math.min(' + (totalPages - 1) + ',blPage+1);renderBLResults()"' + (blPage >= totalPages - 1 ? ' disabled' : '') + '>Next \\u203a</button>';
        html += '<button onclick="blPage=' + (totalPages - 1) + ';renderBLResults()"' + (blPage >= totalPages - 1 ? ' disabled' : '') + '>Last \\u00bb</button>';
        html += '</div>';
    }

    resultsDiv.innerHTML = html;
}

function onBLRowClick(name, ra, dec) {
    if (ra && dec) {
        plotTargetOnMap(name, ra, dec);
    }
}

"""

src = src[:old_start] + new_section + src[old_end:]

with open('dashboard/static/js/dashboard.js', 'w', encoding='utf-8') as f:
    f.write(src)

print('Done. BL search section replaced with paginated version.')
