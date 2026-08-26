"""Idle watcher: wait for NGC1426 downloads to finish, then restart the
dashboard (new GBT endpoints + exact-match search), resume the catalog
sweep, and verify the new endpoints live."""
import json
import subprocess
import sys
import time
import urllib.request

BASE = 'http://localhost:8070'


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.load(r)


def post(path, obj):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def wait_idle(max_s=3 * 3600):
    t0 = time.time()
    while time.time() - t0 < max_s:
        try:
            s = get('/api/download/status')
            q = s.get('queue', [])
            active = [i for i in q if i.get('status') in ('downloading', 'queued')]
            if not active:
                return True
            cur = q[0]
            print(f"[{time.strftime('%H:%M:%S')}] {len(active)} active: "
                  f"{cur.get('filename', '?')} {cur.get('status')} "
                  f"{cur.get('progress', 0):.0f}%", flush=True)
        except Exception as e:
            print('status error:', e, flush=True)
            return False
        time.sleep(30)
    return False


ok = wait_idle()
print('downloads idle:', ok, flush=True)
if not ok:
    sys.exit(1)

out = subprocess.run(
    ['powershell', '-NoProfile', '-Command',
     '(Get-NetTCPConnection -LocalPort 8070 -State Listen | '
     'Select-Object -First 1).OwningProcess'],
    capture_output=True, text=True).stdout.strip()
old_pid = int(out) if out.isdigit() else None
print('restarting dashboard, old pid:', old_pid, flush=True)
if old_pid:
    subprocess.run(['powershell', '-NoProfile', '-Command',
                    f'Stop-Process -Id {old_pid} -Force'], capture_output=True)
time.sleep(3)

p = subprocess.Popen(
    [sys.executable, 'app.py'], cwd=r'G:\seti\dashboard',
    creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
print('new dashboard pid:', p.pid, flush=True)

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
    sys.exit(2)

try:
    r = post('/api/blcatalog/sweep', {'action': 'start', 'mode': 'resume'})
    print('sweep resumed:', r, flush=True)
except Exception as e:
    print('sweep resume failed:', e, flush=True)

try:
    g = get('/api/gbt/sessions?target=GJ447&band=L')
    ns = len(g.get('sessions', []))
    print(f'gbt sessions endpoint OK: GJ447 has {ns} sessions', flush=True)
except Exception as e:
    print('gbt sessions endpoint FAILED:', e, flush=True)

try:
    s = get('/api/blsearch?target=HIP2')
    files = s.get('data', [])
    print(f'blsearch exact-match OK: HIP2 -> {len(files)} files '
          f'(raw {s.get("raw_count")})', flush=True)
except Exception as e:
    print('blsearch check FAILED:', e, flush=True)

print('DONE', flush=True)
