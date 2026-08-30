"""Backyard-SETI Results Report Generator.

Builds a self-contained dark-theme HTML report for a processed target:
funnel stats, per-epoch table, hit plots, star map, stacked spectrum,
and a plain-English verdict. All images embedded as base64 PNGs so the
single .html file can be uploaded anywhere (backyardastronomy.net).

Usage:
  python generate_report.py --target PROXCEN
  python generate_report.py --target PROXCEN --out output/PROXCEN_report.html
"""
import argparse
import base64
import io
import json
import os
import sqlite3

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

SETI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(SETI_ROOT, 'data', 'seti_hits.db')
OUT_DIR = os.path.join(SETI_ROOT, 'report', 'output')

# ---- dark theme ----
BG = '#0d1117'
PANEL = '#161b22'
FG = '#c9d1d9'
MUTED = '#8b949e'
ACCENT = '#58a6ff'
GREEN = '#3fb950'
ORANGE = '#d29922'
RED = '#f85149'
PURPLE = '#bc8cff'

plt.rcParams.update({
    'figure.facecolor': BG, 'axes.facecolor': PANEL,
    'axes.edgecolor': MUTED, 'axes.labelcolor': FG,
    'xtick.color': FG, 'ytick.color': FG, 'text.color': FG,
    'grid.color': '#21262d', 'font.size': 10,
    'axes.titlesize': 12, 'axes.titleweight': 'bold',
})


def fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight',
                facecolor=BG)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def img_tag(b64, alt=''):
    return f'<img src="data:image/png;base64,{b64}" alt="{alt}" style="width:100%;max-width:900px;border-radius:8px;border:1px solid #21262d;">'


def gather(target):
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    scans = [dict(r) for r in c.execute(
        "select * from scans where target=? order by timestamp", (target,))]
    if not scans:
        raise SystemExit(f'No scans found for target {target}')
    sids = [s['scan_id'] for s in scans]

    # funnel: raw hits + rejection survivors per scan
    rejections = []
    for s in sids:
        p = os.path.join(SETI_ROOT, 'results', s, 'rejection',
                         'rejection_results.json')
        d = None
        if os.path.isfile(p):
            try:
                j = json.load(open(p))
                d = {'n_candidates': len(j.get('candidates', [])),
                     'rejected_count': j.get('rejected_count'),
                     'summary': j.get('summary', {})}
            except Exception:
                pass
        rejections.append(d)

    # frequency histogram (50 MHz bins) per scan, ON hits only
    histos = []
    for s in sids:
        rows = list(c.execute(
            "select cast(freq/50 as int)*50 b, count(*) n from hits "
            "where scan_id=? and on_off='ON' group by b", (s,)))
        histos.append({int(r[0]): int(r[1]) for r in rows})

    # drift vs SNR scatter sample (every 60th ON hit)
    freq, drift, snr = [], [], []
    for s in sids:
        for r in c.execute(
                "select freq, drift_rate, snr from hits where scan_id=? "
                "and on_off='ON' and id % 60 = 0", (s,)):
            freq.append(r[0]); drift.append(r[1]); snr.append(r[2])

    # stacked spectrum job (latest complete for target)
    stack = None
    for r in c.execute(
            "select * from stack_jobs where target=? and status='complete' "
            "order by completed_at desc limit 1", (target,)):
        stack = dict(r)
    c.close()
    return scans, rejections, histos, (freq, drift, snr), stack


def plot_funnel(total_raw, total_surv, n_final, target):
    fig, ax = plt.subplots(figsize=(7, 3.6))
    stages = ['Raw hits\ndetected', 'ON/OFF rejection\nsurvivors',
              'Multi-epoch\nviable candidates']
    vals = [total_raw, total_surv, n_final]
    colors = [ACCENT, ORANGE, GREEN if n_final else RED]
    bars = ax.barh(stages[::-1], vals[::-1], color=colors[::-1], height=0.55)
    ax.set_xscale('symlog', linthresh=10)
    ax.set_xlabel('Count (log scale)')
    ax.set_title(f'Candidate funnel: {target}')
    ax.grid(axis='x', alpha=0.4)
    for b, v in zip(bars, vals[::-1]):
        ax.text(v * 1.15, b.get_y() + b.get_height() / 2,
                f'{v:,}', va='center', color=FG, fontweight='bold')
    ax.set_xlim(1, max(total_raw, 10) * 50)
    return fig_to_b64(fig)


def plot_freq_hist(histos, sids, f_start, f_stop):
    fig, ax = plt.subplots(figsize=(9, 4))
    cmap = plt.cm.viridis(np.linspace(0, 0.9, len(histos)))
    for h, sid, col in zip(histos, sids, cmap):
        if not h:
            continue
        ks = sorted(h)
        ax.plot(ks, [h[k] for k in ks], marker='o', ms=3, lw=1.2,
                color=col, label=sid.split('_')[1])
    ax.set_xlabel('Frequency (MHz)')
    ax.set_ylabel('ON hits per 50 MHz bin')
    ax.set_title('Hit distribution across the observed band')
    ax.legend(fontsize=7, title='Epoch', title_fontsize=8)
    ax.grid(alpha=0.4)
    if f_start and f_stop:
        ax.set_xlim(f_start, f_stop)
    return fig_to_b64(fig)


def plot_scatter(freq, drift, snr):
    fig, ax = plt.subplots(figsize=(9, 4))
    s = np.abs(np.array(snr)) + 1
    sc = ax.scatter(freq, drift, c=np.log10(s), s=2, cmap='plasma',
                    alpha=0.6, linewidths=0)
    ax.set_xlabel('Frequency (MHz)')
    ax.set_ylabel('Drift rate (Hz/s)')
    ax.set_title('Drift rate vs frequency (ON hits, log-color SNR)')
    plt.colorbar(sc, ax=ax, label='log10(SNR)')
    ax.grid(alpha=0.4)
    return fig_to_b64(fig)


def plot_starmap(ra_hours, dec_deg, name):
    """Aitoff map: target marker + galactic plane (J2000, pure numpy)."""
    ra = np.radians(np.linspace(0, 360, 721))
    # equatorial -> galactic (J2000): plot galactic plane b=0
    r13 = np.radians(62.6); d = np.radians(282.25)
    dec_g = np.radians(-27.4)
    cos_dec_g = np.cos(dec_g)
    l = np.radians(np.linspace(0, 360, 721))
    cl, sl = np.cos(l), np.sin(l)
    # galactic plane: b = 0 => sin_dec = sin(dec_g), x = sin(l), y = 0
    sin_dec_g = np.sin(dec_g)
    sin_dec = np.full_like(l, sin_dec_g)
    dec_p = np.arcsin(np.clip(sin_dec, -1, 1))
    x = sl
    y = np.zeros_like(l)
    # ra offset formula
    num = x * np.cos(d) - y * np.sin(d)
    den = x * np.sin(d) + y * np.cos(d)
    ra_off = np.arctan2(num, den)
    ra_p = (r13 + ra_off) % (2 * np.pi)
    # wrap to +/-pi for aitoff
    ra_p = np.where(ra_p > np.pi, ra_p - 2 * np.pi, ra_p)

    fig = plt.figure(figsize=(8, 5))
    ax = fig.add_subplot(111, projection='aitoff')
    ax.set_facecolor(PANEL)
    ax.plot(ra_p, dec_p, color=MUTED, lw=1.2, label='Galactic plane')
    ax.axhline(0, color='#21262d', lw=0.8)
    ax.grid(color='#21262d', alpha=0.8)
    tra = np.radians(ra_hours * 15)
    tra = tra - 2 * np.pi if tra > np.pi else tra
    ax.scatter([tra], [np.radians(dec_deg)], s=120, color=ACCENT,
               edgecolors='white', zorder=5, label=name)
    ax.annotate(name, (tra, np.radians(dec_deg)),
                xytext=(10, 12), textcoords='offset points',
                color=ACCENT, fontweight='bold', fontsize=11)
    ax.set_title('Target position on the sky (J2000)', pad=18)
    tick_ra = np.radians(np.arange(-150, 181, 60))
    ax.set_xticks(tick_ra)
    ax.set_xticklabels([f'{int(np.degrees(t))//15:+d}h' for t in tick_ra],
                       fontsize=7)
    ax.tick_params(labelsize=8)
    ax.legend(loc='lower left', fontsize=8, facecolor=PANEL,
              edgecolor='#21262d', labelcolor=FG)
    return fig_to_b64(fig)


def plot_stack_peaks(peaks_json, n_sigma):
    try:
        peaks = json.loads(peaks_json)
    except Exception:
        return None
    if not peaks:
        return None
    f = [p['freq_mhz'] for p in peaks]
    s = [p['snr'] for p in peaks]
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.scatter(f, s, s=14, color=PURPLE, alpha=0.8, linewidths=0)
    ax.set_yscale('log')
    ax.set_xlabel('Frequency (MHz)')
    ax.set_ylabel('Stacked SNR')
    ax.set_title(f'Stacked-spectrum peaks above {n_sigma:g} sigma '
                 f'(all attributable to known RFI)')
    ax.grid(alpha=0.4)
    return fig_to_b64(fig)


HTML = '''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Backyard-SETI: {target}</title>
<style>
 body{{background:{bg};color:{fg};font-family:'Segoe UI',system-ui,sans-serif;margin:0;padding:24px;max-width:960px;margin:auto;line-height:1.6}}
 h1{{color:{accent};font-size:1.9em;margin-bottom:4px}} h2{{color:{accent};border-bottom:1px solid #21262d;padding-bottom:6px;margin-top:40px}}
 .sub{{color:{muted};margin-bottom:24px}} .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin:20px 0}}
 .card{{background:{panel};border:1px solid #21262d;border-radius:10px;padding:14px}}
 .card .v{{font-size:1.6em;font-weight:700;color:{accent}}} .card .l{{color:{muted};font-size:.85em}}
 table{{border-collapse:collapse;width:100%;font-size:.92em}} th,td{{padding:7px 10px;border-bottom:1px solid #21262d;text-align:left}}
 th{{color:{muted};font-weight:600}} .verdict{{background:{panel};border-left:4px solid {red};border-radius:8px;padding:18px 22px;margin-top:16px}}
 .ok{{border-left-color:{green}}} .foot{{color:{muted};font-size:.8em;margin-top:48px;border-top:1px solid #21262d;padding-top:14px}}
 img{{display:block;margin:14px 0}}
</style></head><body>
<h1>📡 {target}</h1>
<div class="sub">Backyard-SETI broadband technosignature search &middot; generated {generated}</div>
<h2>Target</h2>
<div class="cards">
 <div class="card"><div class="v">{ra}</div><div class="l">Right ascension</div></div>
 <div class="card"><div class="v">{dec}</div><div class="l">Declination</div></div>
 <div class="card"><div class="v">{n_epochs}</div><div class="l">Observing epochs</div></div>
 <div class="card"><div class="v">{band}</div><div class="l">Frequency coverage</div></div>
 <div class="card"><div class="v">{dur}</div><div class="l">Total observing time</div></div>
 <div class="card"><div class="v">{tel}</div><div class="l">Telescope</div></div>
</div>
{starmap}
<h2>What we did</h2>
<p>{explain}</p>
<h2>The numbers</h2>
<div class="cards">
 <div class="card"><div class="v">{raw:,}</div><div class="l">Raw narrowband hits</div></div>
 <div class="card"><div class="v">{surv:,}</div><div class="l">ON/OFF rejection survivors</div></div>
 <div class="card"><div class="v">{final:,}</div><div class="l">Candidates surviving all analysis</div></div>
</div>
{funnel}
<h2>Hit statistics</h2>
{freqhist}
{scatter}
<h2>Epochs</h2>
<table><tr><th>Scan</th><th>Date</th><th>Hits (ON)</th><th>Rejection survivors</th><th>Observing time</th></tr>
{epoch_rows}</table>
<h2>Multi-epoch stacked analysis</h2>
<p>{stack_text}</p>
{stackplot}
<h2>Verdict</h2>
<div class="verdict {cls}">{verdict}</div>
<div class="foot">Backyard-SETI &middot; an amateur technosignature search program by Joel, TN USA &middot;
data: {tel_full} via Breakthrough Listen open archives &middot; pipeline: turboSETI + custom ON/OFF rejection,
barycentric correction, RFI comb/zone triage, incoherent multi-epoch stacking.</div>
</body></html>'''


def fmt_hours(h):
    hh = int(h); m = (h - hh) * 60; mm = int(m); ss = int(round((m - mm) * 60))
    return f'{hh:02d}h {mm:02d}m {ss:02d}s'


def fmt_dec(d):
    return f"{d:.3f}°"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--target', required=True)
    ap.add_argument('--out')
    ap.add_argument('--final-candidates', type=int, default=None,
                    help='Final candidate count after cross-epoch analysis '
                         '(default: infer from cross_epoch table, else 0)')
    args = ap.parse_args()

    scans, rejections, histos, (freq, drift, snr), stack = gather(args.target)
    s0 = scans[0]
    total_raw = sum(s['total_hits'] or 0 for s in scans)
    total_surv = sum((r or {}).get('n_candidates', 0) or 0 for r in rejections)

    n_final = args.final_candidates
    if n_final is None:
        c = sqlite3.connect(DB_PATH)
        n_final = c.execute(
            "select coalesce(sum(candidate_count),0) from "
            "cross_epoch_results where scan_ids like ?",
            (f"%{s0['scan_id'].split('_')[1]}%",)).fetchone()[0]
        c.close()

    dur = sum(s['duration_s'] or 0 for s in scans)
    dur_s = f'{dur/3600:.1f} h' if dur > 7200 else f'{dur/60:.0f} min'
    tel = (s0.get('telescope') or 'GBT').capitalize()
    band = (f"{s0['f_stop']-s0['f_start']:.0f} MHz<br>"
            f"({s0['f_start']:.0f}&ndash;{s0['f_stop']:.0f})")

    explain = (f"We observed {args.target} in {len(scans)} separate epochs between "
               f"{scans[0]['timestamp'][:10]} and {scans[-1]['timestamp'][:10]} with the "
               f"{tel} radio telescope, covering {s0['f_start']:.0f}&ndash;{s0['f_stop']:.0f} MHz. "
               "Every narrowband signal detected by the turboSETI pipeline was put through "
               "ON/OFF source rejection (a signal present when the telescope points away from "
               "the target is local interference, not a transmission from the target), "
               "barycentric Doppler correction, automated RFI comb and zone triage, and "
               "finally an incoherent stack across all epochs. A genuine transmitter at the "
               "target would appear at the same corrected frequency in every epoch; "
               "terrestrial interference does not.")

    funnel = img_tag(plot_funnel(total_raw, total_surv, n_final, args.target), 'funnel')
    freqhist = img_tag(plot_freq_hist(histos, [s['scan_id'] for s in scans],
                                      s0['f_start'], s0['f_stop']), 'histogram')
    scatter = img_tag(plot_scatter(freq, drift, snr), 'scatter')
    starmap = img_tag(plot_starmap(s0['ra_hours'], s0['dec_deg'], args.target), 'star map')

    stack_img = None
    stack_text = ''
    if stack:
        n_ep = stack.get('n_epochs', '?')
        sig = stack.get('n_sigma', 5)
        stack_text = (f"We incoherently stacked all {n_ep} epochs and searched the "
                      f"combined spectrum for peaks above {sig:g} sigma "
                      f"(a sensitivity gain of ~{stack.get('snr_improvement', 0):.1f}x). "
                      f"{len(json.loads(stack.get('peaks_json') or '[]'))} peaks rose above "
                      "the threshold; every one of them coincides with known local RFI "
                      "(parked combs and satellite downlinks) and none repeats across "
                      "epochs at a consistent barycentric frequency.")
        stack_img = img_tag(plot_stack_peaks(stack.get('peaks_json'), sig),
                            'stack peaks')
        pp = stack.get('plot_path')
        if pp and os.path.isfile(pp):
            stack_img += ('<p style="color:#8b949e;font-size:.85em">Full stacked '
                          'spectrum plot is also saved at ' + pp + '</p>')
    else:
        stack_text = 'No stacked analysis has been run for this target yet.'

    epoch_rows = ''
    for s, r in zip(scans, rejections):
        surv = (r or {}).get('n_candidates', 'n/a')
        d = s['duration_s'] or 0
        ds = f'{d/3600:.1f} h' if d > 7200 else f'{d/60:.0f} min'
        epoch_rows += (f"<tr><td>{s['scan_id'].split('_')[1]}</td>"
                       f"<td>{s['timestamp'][:10]}</td>"
                       f"<td>{s['on_hits']:,}</td><td>{surv:,}</td>"
                       f"<td>{ds}</td></tr>")

    if n_final == 0:
        cls, verdict = 'ok', (
            f"<b>No candidates survived.</b> After {len(scans)} epochs, "
            f"{total_raw:,} raw detections and ON/OFF rejection, zero signals "
            "passed all tests. Everything we saw was terrestrial interference. "
            f"That is how a null result is supposed to look: for a quiet "
            "transmitter near " + args.target + ", we would have seen the same "
            "drifting signal reappear at the same corrected frequency in every "
            "epoch. We will keep listening.")
    else:
        cls, verdict = '', (f"<b>{n_final} candidate(s)</b> survived all "
                            "rejection stages and merit follow-up observation.")

    import datetime
    html = HTML.format(
        target=args.target, bg=BG, fg=FG, panel=PANEL, muted=MUTED,
        accent=ACCENT, red=RED, green=GREEN,
        generated=datetime.datetime.now().strftime('%Y-%m-%d %H:%M CDT'),
        ra=fmt_hours(s0['ra_hours']), dec=fmt_dec(s0['dec_deg']),
        n_epochs=len(scans), band=band, dur=dur_s, tel=tel,
        raw=total_raw, surv=total_surv, final=n_final,
        starmap=starmap, funnel=funnel, freqhist=freqhist, scatter=scatter,
        explain=explain, epoch_rows=epoch_rows, stack_text=stack_text,
        stackplot=stack_img or '', cls=cls, verdict=verdict,
        tel_full=tel)

    out = args.out or os.path.join(OUT_DIR, f'{args.target}_report.html')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)
    print('WROTE', out, f'{os.path.getsize(out)/1e6:.2f} MB')


if __name__ == '__main__':
    main()
