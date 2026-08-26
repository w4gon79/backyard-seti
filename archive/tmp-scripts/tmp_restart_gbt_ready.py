"""Idle watcher: when the NGC1426 scan completes AND the download queue
drains, restart the dashboard on the GBT-parity code, verify grammar
handling live, and relaunch the fine sweep. Does NOT start any GBT scan
(Joel drives that from the dashboard)."""
import json
import subprocess
import sys
import time
import urllib.request

BASE = 'http://localhost:8070'


def get(path, timeout=25):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.load(r)


def post(path, obj, timeout=60):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def busy():
    s = get('/api/scan/status')
    if s.get('active'):
        return True, f"scan file {s.get('current_file_index')}/{s.get('file_total')}"
    d = get('/api/download/status')
    act = [i for i in d.get('queue', [])
           if i.get('status') in ('downloading', 'queued')]
    if act:
        cur = act[0]
        return True, (f"dl {cur.get('filename', '?')[-40:]} "
                      f"{cur.get('progress', 0):.0f}%")
    return False, ''


t0 = time.time()
while True:
    try:
        is_busy, why = busy()
    except Exception as e:
        print('status error:', e, flush=True)
        sys.exit(1)
    if not is_busy:
        break
    if time.time() - t0 > 14 * 3600:
        print('timeout waiting for idle', flush=True)
        sys.exit(2)
    print(f"[{time.strftime('%H:%M:%S')}] {why}", flush=True)
    time.sleep(60)

print('idle: restarting dashboard', flush=True)
out = subprocess.run(
    ['powershell', '-NoProfile', '-Command',
     '(Get-NetTCPConnection -LocalPort 8070 -State Listen | '
     'Select-Object -First 1).OwningProcess'],
    capture_output=True, text=True).stdout.strip()
old = int(out) if out.isdigit() else None
if old:
    subprocess.run(['powershell', '-NoProfile', '-Command',
                    f'Stop-Process -Id {old} -Force'], capture_output=True)
time.sleep(3)
p = subprocess.Popen([sys.executable, 'app.py'], cwd=r'G:\seti\dashboard',
                     creationflags=0x00000008 | 0x00000200)
print('new pid:', p.pid, flush=True)

up = False
for _ in range(40):
    time.sleep(2)
    try:
        get('/api/blcatalog/sweep/status')
        up = True
        break
    except Exception:
        pass
print('dashboard up:', up, flush=True)
if not up:
    sys.exit(3)

# Verify GBT grammar live: local data must show the GJ447 group + companions
try:
    t = get('/api/targets', timeout=60)
    groups = {k: len(v.get('fine', [])) for k, v in t.items()
              if k in ('GJ447', 'HIP56677', 'HIP56682', 'HIP56691')}
    print('local GBT groups:', groups, flush=True)
except Exception as e:
    print('targets check failed:', e, flush=True)

try:
    r = post('/api/blcatalog/sweep', {'action': 'start', 'mode': 'fine'})
    print('fine sweep relaunched:', r.get('started'), r.get('queued'), flush=True)
except Exception as e:
    print('sweep relaunch failed:', e, flush=True)

print('DONE', flush=True)
