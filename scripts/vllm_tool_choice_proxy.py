#!/usr/bin/env python3
"""
Proxy: forces tool_choice="required" on FIRST call per task.
Counter resets via GET /reset. Harness calls /reset between tasks.
"""
import json, sys, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request

UPSTREAM = "http://127.0.0.1:8010"
call_count = 0
lock = threading.Lock()

class ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global call_count
        if self.path == "/reset":
            with lock:
                call_count = 0
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
            print("[proxy] COUNTER RESET", flush=True)
            return
        self._proxy("GET")

    def do_POST(self):
        self._proxy("POST")

    def _proxy(self, method):
        global call_count
        cl = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(cl) if cl > 0 else b''

        if self.path == "/v1/chat/completions" and body:
            try:
                data = json.loads(body)
                with lock:
                    call_count += 1
                    cn = call_count
                
                if cn == 1:
                    data["tool_choice"] = "required"
                    print(f"[proxy] CALL#{cn} FORCED", flush=True)
                else:
                    print(f"[proxy] CALL#{cn} normal", flush=True)
                
                body = json.dumps(data).encode('utf-8')
            except:
                pass

        url = UPSTREAM + self.path
        req = urllib.request.Request(url, data=body, method=method)
        for k, v in self.headers.items():
            if k.lower() not in ('host', 'content-length'):
                req.add_header(k, v)

        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() != 'transfer-encoding':
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp.read())
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8011
    server = HTTPServer(("127.0.0.1", port), ProxyHandler)
    print(f"Proxy on 127.0.0.1:{port} → {UPSTREAM}", flush=True)
    print("GET /reset between tasks, tool_choice=required on CALL#1", flush=True)
    server.serve_forever()
