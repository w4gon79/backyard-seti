/* skymap.js - Lightweight sky map renderer (no d3-celestial dependency)
   Draws stars, constellation lines, and target markers on a canvas.
   Uses the Yale Bright Star Catalog subset (mag < 5) for rendering.
   Aitoff projection.
*/

(function() {
    var SkyMap = {};
    var canvas, ctx;
    var targets = [];
    var width, height, cx, cy, R;

    // Bright stars (mag < 3.5) - subset of Yale BSC
    // [name, RA_hours, Dec_deg, magnitude]
    var brightStars = [
        ['Sirius', 6.7525, -16.7161, -1.46],
        ['Canopus', 6.3992, -52.6956, -0.73],
        ['Arcturus', 14.2610, 19.1825, -0.05],
        ['Vega', 18.6156, 38.7837, 0.03],
        ['Capella', 5.2782, 45.9978, 0.08],
        ['Rigel', 5.2423, -8.2017, 0.13],
        ['Procyon', 7.6550, 5.2250, 0.34],
        ['Betelgeuse', 5.9195, 7.4071, 0.42],
        ['Altair', 19.8463, 8.8683, 0.77],
        ['Aldebaran', 4.5987, 16.5093, 0.85],
        ['Antares', 16.4901, -26.4320, 0.96],
        ['Spica', 13.4199, -11.1614, 0.98],
        ['Pollux', 7.7553, 28.0262, 1.14],
        ['Fomalhaut', 22.9608, -29.6222, 1.16],
        ['Deneb', 20.6905, 45.2803, 1.25],
        ['Regulus', 10.1395, 11.9672, 1.35],
        ['Castor', 7.5766, 31.8883, 1.58],
        ['Bellatrix', 5.4188, 6.3497, 1.64],
        ['Alnilam', 5.6036, -1.2019, 1.69],
        ['Alnitak', 5.6793, -1.9426, 1.74],
        ['Mintaka', 5.5334, -0.2991, 2.23],
        ['Saiph', 5.7959, -9.6696, 2.07],
        ['Polaris', 2.5302, 89.2641, 1.97],
        ['Dubhe', 11.0621, 61.7508, 1.79],
        ['Merak', 11.0307, 56.3824, 2.37],
        ['Phecda', 11.8972, 53.6948, 2.44],
        ['Megrez', 12.2570, 57.0326, 3.31],
        ['Alioth', 12.9005, 55.9598, 1.76],
        ['Mizar', 13.3987, 54.9254, 2.23],
        ['Alkaid', 13.7923, 49.3133, 1.85],
        ['Mimosa', 12.7953, -59.6886, 1.25],
        ['Acrux', 12.4433, -63.0991, 0.77],
        ['Gacrux', 12.5194, -57.1131, 1.59],
        ['Hadar', 14.0637, -60.3730, 0.61],
        ['Rigil Kentaurus', 14.6599, -60.8354, -0.01],
        ['Proxima Centauri', 14.4953, -61.3206, 11.13],
        ['Achernar', 1.6286, -57.2368, 0.46],
        ['Hamel', 2.1196, 23.4624, 2.00],
        ['Diphda', 0.7265, -17.9866, 2.04],
        ['Schedar', 0.6751, 56.5373, 2.24],
        ['Caph', 0.1530, 59.1498, 2.27],
        ['Algol', 3.1361, 40.9556, 2.12],
        ['Mirfak', 3.4054, 49.8612, 1.79],
        ['Hamal', 2.1196, 23.4624, 2.00],
        ['Denebola', 11.8177, 14.5722, 2.13],
        ['Algenib', 0.2206, 15.1836, 2.83],
        ['Scheat', 0.1451, 28.0828, 2.42],
        ['Markab', 23.0793, 15.2052, 2.49],
        ['Mira', 34.8312, -2.9774, 3.0],
    ];

    // Constellation lines (pairs of star names)
    var constellations = [
        // Orion
        ['Betelgeuse', 'Bellatrix'],
        ['Betelgeuse', 'Alnitak'],
        ['Bellatrix', 'Mintaka'],
        ['Alnitak', 'Alnilam'],
        ['Alnilam', 'Mintaka'],
        ['Alnitak', 'Saiph'],
        ['Rigel', 'Saiph'],
        ['Rigel', 'Mintaka'],
        // Ursa Major (Big Dipper)
        ['Dubhe', 'Merak'],
        ['Dubhe', 'Megrez'],
        ['Merak', 'Phecda'],
        ['Phecda', 'Megrez'],
        ['Megrez', 'Alioth'],
        ['Alioth', 'Mizar'],
        ['Mizar', 'Alkaid'],
        // Cassiopeia (W shape)
        ['Caph', 'Schedar'],
        ['Schedar', 'Gamma Cas'],
        ['Gamma Cas', 'Ruchbah'],
        ['Ruchbah', 'Segin'],
        // Lyra
        ['Vega', 'Sheliak'],
        ['Sheliak', 'Sulafat'],
        // Cygnus (Northern Cross)
        ['Deneb', 'Sadr'],
        ['Sadr', 'Albireo'],
        ['Gienah Cyg', 'Sadr'],
        ['Delta Cyg', 'Sadr'],
        // Crux (Southern Cross)
        ['Acrux', 'Gacrux'],
        ['Acrux', 'Mimosa'],
        ['Gacrux', 'Delta Cru'],
        ['Mimosa', 'Delta Cru'],
        // Centaurus
        ['Rigil Kentaurus', 'Hadar'],
        ['Rigil Kentaurus', 'Proxima Centauri'],
        // Leo
        ['Regulus', 'Denebola'],
        ['Regulus', 'Algieba'],
        ['Algieba', 'Denebola'],
        // Scorpius
        ['Antares', 'Shaula'],
        // Bo\u00f6tes
        ['Arcturus', 'Izar'],
        // Pegasus square
        ['Markab', 'Scheat'],
        ['Markab', 'Algenib'],
        ['Scheat', 'Caph'],
    ];

    function aitoff(raHours, decDeg) {
        // Convert RA from hours to radians, centered at 0 (shift by -12h so RA=12h is center)
        var lon = (raHours - 12) * 15 * Math.PI / 180;  // longitude in radians, range [-pi, pi]
        var lat = decDeg * Math.PI / 180;                // latitude in radians

        // Aitoff projection: an equal-area modified azimuthal projection
        // Reference: Snyder, USGS Professional Paper 1395
        var clon = Math.cos(lon / 2);
        var slon = Math.sin(lon / 2);
        var clat = Math.cos(lat);
        var slat = Math.sin(lat);

        // Compute the auxiliary angle theta
        var cosC = clat * clon;
        var sinThetaSq = 1 - cosC * cosC;
        if (sinThetaSq < 0.0001) {
            // At the center point
            return [cx, cy];
        }
        var theta = Math.asin(cosC);  // theta in [0, pi/2] for visible hemisphere
        // Actually use the standard Aitoff formulas directly:
        // gamma = acos(cos(lat) * cos(lon/2))
        var gamma = Math.acos(Math.max(-1, Math.min(1, cosC)));
        if (Math.abs(Math.sin(gamma)) < 0.0001) {
            return [cx, cy];
        }

        var x = R * 2 * Math.SQRT2 * clat * slon / Math.sin(gamma);
        var y = R * Math.SQRT2 * slat / Math.sin(gamma);

        return [cx + x, cy - y];
    }

    function starByName(name) {
        for (var i = 0; i < brightStars.length; i++) {
            if (brightStars[i][0] === name) return brightStars[i];
        }
        return null;
    }

    SkyMap.init = function(containerId) {
        var container = document.getElementById(containerId);
        if (!container) return;

        canvas = document.createElement('canvas');
        container.innerHTML = '';
        container.appendChild(canvas);
        ctx = canvas.getContext('2d');

        function resize() {
            var r = container.getBoundingClientRect();
            canvas.width = r.width * (window.devicePixelRatio || 1);
            canvas.height = r.height * (window.devicePixelRatio || 1);
            canvas.style.width = r.width + 'px';
            canvas.style.height = r.height + 'px';
            ctx.scale(window.devicePixelRatio || 1, window.devicePixelRatio || 1);
            width = r.width;
            height = r.height;
            cx = width / 2;
            cy = height / 2;
            R = Math.min(width, height) * 0.42;
            draw();
        }
        resize();
        window.addEventListener('resize', resize);
    };

    function draw() {
        if (!ctx) return;
        ctx.clearRect(0, 0, width, height);

        // Background
        ctx.fillStyle = '#0a0a1a';
        ctx.fillRect(0, 0, width, height);

        // Milky Way band (simplified - just a translucent band)
        ctx.save();
        ctx.globalAlpha = 0.08;
        ctx.fillStyle = '#4466aa';
        ctx.beginPath();
        for (var ra = 0; ra <= 24; ra += 0.5) {
            var dec = 30 * Math.sin(ra * 15 * Math.PI / 180) - 20;
            var xy = aitoff(ra, dec);
            if (ra === 0) ctx.moveTo(xy[0], xy[1]);
            else ctx.lineTo(xy[0], xy[1]);
        }
        for (var ra2 = 23.5; ra2 >= 0; ra2 -= 0.5) {
            var dec2 = 30 * Math.sin(ra2 * 15 * Math.PI / 180) - 40;
            var xy2 = aitoff(ra2, dec2);
            ctx.lineTo(xy2[0], xy2[1]);
        }
        ctx.closePath();
        ctx.fill();
        ctx.restore();

        // Constellation lines
        ctx.strokeStyle = '#1e3a5f';
        ctx.lineWidth = 1;
        for (var i = 0; i < constellations.length; i++) {
            var s1 = starByName(constellations[i][0]);
            var s2 = starByName(constellations[i][1]);
            if (!s1 || !s2) continue;
            var p1 = aitoff(s1[1], s1[2]);
            var p2 = aitoff(s2[1], s2[2]);
            if (!p1 || !p2) continue;
            ctx.beginPath();
            ctx.moveTo(p1[0], p1[1]);
            ctx.lineTo(p2[0], p2[1]);
            ctx.stroke();
        }

        // Stars
        for (var j = 0; j < brightStars.length; j++) {
            var star = brightStars[j];
            var pos = aitoff(star[1], star[2]);
            if (!pos) continue;
            var mag = star[3];
            var radius = Math.max(0.8, 3.5 - mag * 0.7);
            var alpha = mag < 1.5 ? 1.0 : Math.max(0.4, 1.0 - (mag - 1.5) * 0.2);

            // Star color by magnitude (brighter = warmer)
            var color = mag < 1.0 ? '#ffffdd' : mag < 2.0 ? '#ffffff' : '#ddddff';

            ctx.fillStyle = color;
            ctx.globalAlpha = alpha;
            ctx.beginPath();
            ctx.arc(pos[0], pos[1], radius, 0, 2 * Math.PI);
            ctx.fill();

            // Glow for bright stars
            if (mag < 1.5) {
                ctx.globalAlpha = 0.3;
                ctx.beginPath();
                ctx.arc(pos[0], pos[1], radius * 2.5, 0, 2 * Math.PI);
                ctx.fill();
            }

            // Label for very bright stars
            if (mag < 1.0) {
                ctx.globalAlpha = 0.6;
                ctx.fillStyle = '#aabbdd';
                ctx.font = '9px sans-serif';
                ctx.fillText(star[0], pos[0] + radius + 3, pos[1] + 3);
            }
        }
        ctx.globalAlpha = 1.0;

        // Target markers
        for (var k = 0; k < targets.length; k++) {
            drawTarget(targets[k]);
        }
    }

    function drawTarget(t) {
        var pos = aitoff(t.ra, t.dec);
        if (!pos) return;
        var x = pos[0], y = pos[1];

        // Outer ring
        ctx.strokeStyle = '#ff4444';
        ctx.lineWidth = 1;
        ctx.globalAlpha = 0.3;
        ctx.beginPath();
        ctx.arc(x, y, 18, 0, 2 * Math.PI);
        ctx.stroke();

        // Crosshair ring
        ctx.globalAlpha = 0.7;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(x, y, 12, 0, 2 * Math.PI);
        ctx.stroke();

        // Solid dot
        ctx.globalAlpha = 1.0;
        ctx.fillStyle = '#ff4444';
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, 2 * Math.PI);
        ctx.fill();
        ctx.stroke();

        // Crosshair lines
        ctx.strokeStyle = '#ff6666';
        ctx.lineWidth = 1.5;
        ctx.globalAlpha = 0.7;
        ctx.beginPath();
        ctx.moveTo(x - 22, y); ctx.lineTo(x - 14, y);
        ctx.moveTo(x + 14, y); ctx.lineTo(x + 22, y);
        ctx.moveTo(x, y - 22); ctx.lineTo(x, y - 14);
        ctx.moveTo(x, y + 14); ctx.lineTo(x, y + 22);
        ctx.stroke();

        // Label
        ctx.globalAlpha = 1.0;
        ctx.fillStyle = '#ff8888';
        ctx.font = 'bold 11px sans-serif';
        ctx.fillText(t.name, x + 26, y + 4);
    }

    SkyMap.addTarget = function(name, raHours, decDeg) {
        targets = targets.filter(function(t) { return t.name !== name; });
        targets.push({ name: name, ra: raHours, dec: decDeg });
        draw();
    };

    SkyMap.clearTargets = function() {
        targets = [];
        draw();
    };

    window.SkyMap = SkyMap;
})();
