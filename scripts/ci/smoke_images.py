"""Read-only HTTP smoke checks, solely on the dedicated V10 loopback ports."""
import time
import urllib.request
for path in ('http://127.0.0.1:58000/health', 'http://127.0.0.1:58080/healthz',
             'http://127.0.0.1:58080/products/example', 'http://127.0.0.1:58080/api/health',
             'http://127.0.0.1:58080/api/products'):
    for attempt in range(30):
        try:
            with urllib.request.urlopen(path, timeout=3) as response:
                assert response.status == 200
                body = response.read()
                if '/products/example' in path:
                    assert b'<html' in body
                if path.endswith('/api/products'):
                    assert b'"items":[]' in body
            print('PASS', path)
            break
        except Exception:
            if attempt == 29:
                raise
            time.sleep(1)
