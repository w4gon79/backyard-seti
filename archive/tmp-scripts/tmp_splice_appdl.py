"""Splice app.py: replace api_download's inline queueing with _enqueue_download helper."""
import io

APP = r'G:\seti\dashboard\app.py'

with io.open(APP, 'r', encoding='utf-8') as f:
    src = f.read()

START = "    # Check if already downloading"
END = "    return jsonify({'status': 'queued', 'filename': filename})"

i0 = src.index(START)
i1 = src.index(END) + len(END)
old_block = src[i0:i1]

NEW = '''    res = _enqueue_download(url, filename, target_dir)
    code = 409 if res.get('status') == 'already-downloading' else 200
    return jsonify(res), code


def _enqueue_download(url, filename, target_dir, expected_size=None):
    """Queue one file into the serialized download pipeline.

    Shared by /api/download (single file from the search UI) and
    /api/gbt/download (session bulk). expected_size lets partial files
    left by a killed download be detected (wrong size) and replaced
    instead of blocking re-downloads forever.
    """
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, filename)

    for item in download_state['queue']:
        if item['filename'] == filename and item['status'] in ('downloading', 'queued'):
            return {'error': 'Already downloading',
                    'status': 'already-downloading', 'filename': filename}
    if os.path.isfile(target_path):
        size = os.path.getsize(target_path)
        if expected_size and size != expected_size:
            try:
                os.remove(target_path)  # partial: killed mid-download earlier
            except OSError:
                pass
        else:
            return {'status': 'exists', 'filename': filename, 'size_bytes': size,
                    'path': os.path.relpath(target_path, SETI_ROOT)}

    item = {
        'url': url,
        'filename': filename,
        'target_path': target_path,
        'target_dir': target_dir,
        'status': 'queued',
        'progress': 0.0,
        'speed_mbs': 0.0,
        'eta_s': 0,
        'size_total': expected_size or 0,
        'size_done': 0,
        'error': None,
    }
    download_state['queue'].append(item)

    def do_download(dl_item):
        import urllib.request
        import time as _time

        # Wait if another download is active (serialize downloads)
        while download_state['active'] is not None and download_state['active'] is not dl_item:
            _time.sleep(1)
            if dl_item not in download_state['queue']:
                return  # Cancelled

        download_state['active'] = dl_item
        dl_item['status'] = 'downloading'

        try:
            req = urllib.request.Request(dl_item['url'],
                                         headers={'User-Agent': 'BackyardSETI/1.0'})
            with urllib.request.urlopen(req, timeout=60) as resp:
                if int(resp.headers.get('Content-Length', 0) or 0):
                    dl_item['size_total'] = int(resp.headers['Content-Length'])

                with open(dl_item['target_path'], 'wb') as f:
                    done = 0
                    chunk_size = 1024 * 1024  # 1 MB chunks
                    last_time = _time.time()
                    last_done = 0

                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        dl_item['size_done'] = done

                        if dl_item['size_total'] > 0:
                            dl_item['progress'] = round(done / dl_item['size_total'] * 100, 2)

                        # Calculate speed every 2 seconds
                        now = _time.time()
                        if now - last_time >= 2:
                            elapsed = now - last_time
                            bytes_diff = done - last_done
                            dl_item['speed_mbs'] = round(bytes_diff / elapsed / 1e6, 2)
                            if dl_item['speed_mbs'] > 0:
                                remaining = dl_item['size_total'] - done
                                dl_item['eta_s'] = int(remaining / (bytes_diff / elapsed))
                            last_time = now
                            last_done = done

            dl_item['status'] = 'complete'
            dl_item['progress'] = 100.0

        except Exception as e:
            dl_item['status'] = 'error'
            dl_item['error'] = str(e)
            # Clean up partial file
            if os.path.isfile(dl_item['target_path']):
                try:
                    os.remove(dl_item['target_path'])
                except:
                    pass
        finally:
            if download_state['active'] is dl_item:
                download_state['active'] = None

    thread = threading.Thread(target=do_download, args=(item,), daemon=True)
    thread.start()
    return {'status': 'queued', 'filename': filename}'''

src = src[:i0] + NEW + src[i1:]

with io.open(APP, 'w', encoding='utf-8', newline='') as f:
    f.write(src)
print('spliced ok; old block was', len(old_block), 'chars')
