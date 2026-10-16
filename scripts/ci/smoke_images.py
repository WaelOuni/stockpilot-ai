"""Read-only HTTP smoke checks, solely on the dedicated V10 loopback ports."""
import time
import json
import urllib.error
import urllib.request
for path, expected_status in (
    ('http://127.0.0.1:58000/health', 200),
    ('http://127.0.0.1:58080/healthz', 200),
    ('http://127.0.0.1:58080/products/example', 200),
    ('http://127.0.0.1:58080/api/health', 200),
    ('http://127.0.0.1:58080/api/products', 503),
):
    for attempt in range(30):
        try:
            try:
                response = urllib.request.urlopen(path, timeout=3)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                assert response.status == expected_status
                body = response.read()
                if '/products/example' in path:
                    assert b'<html' in body
                if path.endswith('/api/products'):
                    # A production image without security configuration must
                    # close business APIs while keeping probes operational.
                    payload = json.loads(body)
                    assert set(payload) == {'detail'}
                    assert isinstance(payload['detail'], str) and payload['detail']
            print('PASS', path)
            break
        except Exception:
            if attempt == 29:
                raise
            time.sleep(1)
