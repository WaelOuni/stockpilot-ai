"""Candidate-only bounded inference relay; no model management endpoints or prompt logs."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ALLOWED = {'/api/generate', '/api/embed', '/api/embeddings', '/api/tags', '/api/show', '/api/version', '/api/ps'}


class Relay(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, body):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def forward(self):
        if self.path == '/health':
            self.respond(200, b'{"status":"candidate-relay"}')
            return
        if self.path not in ALLOWED:
            self.respond(403, b'{"error":"endpoint not allowed"}')
            return
        length = int(self.headers.get('Content-Length', '0'))
        if length > 1024 * 1024:
            self.respond(413, b'{}')
            return
        body = self.rfile.read(length) if length else None
        payload = json.loads(body) if body else {}
        mode = Path('/candidate/control/mode').read_text().strip()
        started = time.perf_counter()
        status = 200
        if mode == 'offline' and self.path in {'/api/generate', '/api/embed'}:
            status, result = 503, b'{"error":"candidate-only simulated outage"}'
        elif mode == 'invalid' and self.path == '/api/generate':
            result = b'{"response":"UNVERIFIED INVENTED 999W"}'
        else:
            try:
                request = Request(os.environ['OLLAMA_UPSTREAM'] + self.path, data=body,
                                  headers={'Content-Type': 'application/json'}, method=self.command)
                with urlopen(request, timeout=120) as response:
                    result = response.read()
                    status = response.status
            except HTTPError as error:
                status, result = error.code, b'{"error":"upstream refusal"}'
            except (URLError, TimeoutError):
                status, result = 503, b'{"error":"upstream unavailable"}'
        print(json.dumps({'event': 'candidate_inference', 'path': self.path,
              'model': payload.get('model'), 'mode': mode, 'status': status,
              'duration_ms': round((time.perf_counter() - started) * 1000, 3)}), flush=True)
        self.respond(status, result)

    do_GET = forward
    do_POST = forward


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8080), Relay).serve_forever()
