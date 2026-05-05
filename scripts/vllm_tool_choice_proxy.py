#!/usr/bin/env python3
"""
Proxy: force tool_choice + fix finish_reason for 100% spawn with answers.
- FORCE_TURNS=2: model must call tool on first 2 turns (read → task)
- FINISH FIX: if vLLM returns finish_reason="stop" but has tool_calls,
  rewrite to "tool_calls" so OpenCode continues the conversation
- Turn 3+: free, model can answer with text
"""
import json, sys, threading, os
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request

UPSTREAM = "http://127.0.0.1:8010"
call_count = 0
lock = threading.Lock()
FORCE_TURNS = 2
FORCE_TASK = {"type": "function", "function": {"name": "task"}}
LOG_FILE = "/tmp/proxy_v25.log"

def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(msg + "\n")

class ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global call_count
        if self.path == "/reset":
            with lock:
                call_count = 0
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
            return
        elif self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
            return
        self._proxy("GET")

    def do_POST(self):
        self._proxy("POST")

    def _fix_finish_reason(self, response_body):
        """If vLLM says 'stop' but there are tool_calls, change to 'tool_calls'."""
        try:
            data = json.loads(response_body)
            for choice in data.get("choices", []):
                msg = choice.get("message", {})
                tc = msg.get("tool_calls")
                fr = choice.get("finish_reason")
                if tc and fr == "stop":
                    choice["finish_reason"] = "tool_calls"
                    log(f"FIXED finish_reason: stop → tool_calls (tool={tc[0]['function']['name']})")
                    return json.dumps(data).encode('utf-8')
                elif tc:
                    log(f"finish_reason already OK: {fr} (tool={tc[0]['function']['name']})")
                else:
                    log(f"finish_reason={fr}, no tool_calls")
        except Exception as e:
            log(f"ERROR in _fix_finish_reason: {e}")
        return response_body

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
                
                # Turn 1: no force → model reads prompt naturally
                # Turn 2: force task() → model MUST spawn
                # Turn 3: force tool_choice="none" → model MUST answer (no more tools)
                # Turn 4+: free (shouldn't reach here)
                data["stream"] = False  # force non-streaming
                if cn == 2:
                    data["tool_choice"] = FORCE_TASK
                    log(f"turn #2 → FORCED task()")
                elif cn == 3:
                    data["tool_choice"] = "none"
                    log(f"turn #3 → FORCED none (answer now!)")
                elif cn >= 4:
                    if "tool_choice" in data:
                        del data["tool_choice"]
                    log(f"turn #{cn} → FREE (shouldn't reach)")
                else:
                    log(f"turn #1 → pass-through")
                
                body = json.dumps(data).encode('utf-8')
            except Exception as e:
                print(f"[proxy] ERROR parsing request: {e}", flush=True)

        url = UPSTREAM + self.path
        req = urllib.request.Request(url, data=body, method=method)
        for k, v in self.headers.items():
            if k.lower() not in ('host', 'content-length'):
                req.add_header(k, v)

        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                resp_body = resp.read()
                # Fix finish_reason before forwarding to OpenCode
                if self.path == "/v1/chat/completions":
                    resp_body = self._fix_finish_reason(resp_body)
                
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() != 'transfer-encoding':
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp_body)
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
    print(f"FORCE_TURNS={FORCE_TURNS}, finish_reason fix ENABLED", flush=True)
    server.serve_forever()
