#!/usr/bin/env python3
"""
Proxy: globally forces tool_choice="required". 
Main agent → forced to call tool, prompt says "task" so should call task().
Subagent → forced to call tool, normally calls read/bash — fine.
"""
import json, sys
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request

UPSTREAM = "http://127.0.0.1:8010"

class ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._proxy("GET")
    def do_POST(self):
        self._proxy("POST")

    def _proxy(self, method):
        cl = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(cl) if cl > 0 else b''

        if self.path == "/v1/chat/completions" and body:
            try:
                data = json.loads(body)
                data["tool_choice"] = "required"
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
    print("tool_choice=required GLOBAL", flush=True)
    server.serve_forever()
