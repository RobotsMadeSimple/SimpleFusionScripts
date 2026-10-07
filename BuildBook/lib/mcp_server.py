"""Agent access: a small MCP server inside Fusion, so an AI agent can read the build manual,
capture pictures and (if allowed) edit it.

Off until switched on in Settings (kept per user in the data folder, mcp.json). It listens on
this computer only (127.0.0.1) at /mcp/<token>, speaking MCP's "streamable HTTP" transport with
plain JSON answers: initialize, tools/list, tools/call, ping. Fusion's API only works on its main
thread, so each tool call is queued, a custom event wakes the add-in on the main thread, and the
request thread waits for the answer.

Only the standard library: no packages to install.
"""

import json
import os
import queue
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import log, paths

EVENT = "buildBookMcpCall"
FILE = "mcp.json"
PROTOCOL = "2025-06-18"
CALL_TIMEOUT = 180.0            # seconds a tool may take (a capture renders; a PDF takes longer)


# ---------------------------------------------------------------- settings (per user)

def settings():
    """{"enabled", "edit", "port", "token"}: off, read-only, port 8765, a fresh token by default."""
    try:
        with open(paths.data_dir(FILE), encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    out = {"enabled": bool(data.get("enabled")), "edit": bool(data.get("edit")),
           "port": int(data.get("port") or 8765), "token": data.get("token") or ""}
    if not out["token"]:
        out["token"] = secrets.token_urlsafe(18)
        save(out)
    return out


def save(values):
    os.makedirs(paths.data_dir(), exist_ok=True)
    with open(paths.data_dir(FILE), "w", encoding="utf-8") as f:
        json.dump(values, f)


def url(values):
    return "http://127.0.0.1:{}/mcp/{}".format(values["port"], values["token"])


# ---------------------------------------------------------------- the server

class Server:
    """Runs the HTTP side on a thread; tool calls run on the main thread (run_pending)."""

    def __init__(self, fire_event):
        self.fire_event = fire_event        # wakes the main thread (custom event)
        self.jobs = queue.Queue()
        self.httpd = None
        self.thread = None
        self.error = ""
        self.config = None

    @property
    def running(self):
        return self.httpd is not None

    def start(self, config):
        self.stop()
        self.config = config
        try:
            self.httpd = ThreadingHTTPServer(("127.0.0.1", int(config["port"])), _handler_for(self))
            self.httpd.daemon_threads = True
        except OSError as err:
            self.httpd = None
            self.error = "Port {} is in use or blocked ({})".format(config["port"], err)
            log.info("mcp: " + self.error)
            return False
        self.error = ""
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        log.info("mcp: listening on 127.0.0.1:{}".format(config["port"]))
        return True

    def stop(self):
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
            self.httpd = None
            log.info("mcp: stopped")

    # -------------------------------------------------------- main-thread hand-off

    def call(self, name, args):
        """(request thread) Run tool `name` on the main thread and wait for its result."""
        job = {"name": name, "args": args or {}, "done": threading.Event(), "result": None}
        self.jobs.put(job)
        self.fire_event()
        if not job["done"].wait(CALL_TIMEOUT):
            return _error("BuildBook didn't answer in time (Fusion busy, or a dialog open?)")
        return job["result"]

    def run_pending(self, runner):
        """(main thread, from the custom event) Run every queued call with runner(name, args)."""
        while True:
            try:
                job = self.jobs.get_nowait()
            except queue.Empty:
                return
            try:
                job["result"] = runner(job["name"], job["args"])
            except Exception as err:
                log.error("mcp tool " + job["name"])
                job["result"] = _error("{}: {}".format(type(err).__name__, err))
            finally:
                job["done"].set()


def _error(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _handler_for(server):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass                            # (quiet: the add-in log has what matters)

        def _authorised(self):
            return self.path.split("?")[0].rstrip("/") == "/mcp/" + server.config["token"]

        def _send(self, code, body=None):
            data = json.dumps(body).encode("utf-8") if body is not None else b""
            self.send_response(code)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if data:
                self.wfile.write(data)

        def do_GET(self):
            self._send(405 if self._authorised() else 404)

        def do_DELETE(self):
            self._send(200 if self._authorised() else 404)

        def do_POST(self):
            if not self._authorised():
                self._send(404)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                message = json.loads(self.rfile.read(length) or b"null")
            except Exception:
                self._send(400, {"jsonrpc": "2.0", "id": None,
                                 "error": {"code": -32700, "message": "Parse error"}})
                return
            if isinstance(message, list):
                answers = [a for a in (self._answer(m) for m in message) if a is not None]
                self._send(200, answers) if answers else self._send(202)
                return
            answer = self._answer(message)
            self._send(200, answer) if answer is not None else self._send(202)

        def _answer(self, m):
            if not isinstance(m, dict) or "method" not in m:
                return None                 # (a response / notification from the client)
            mid, method, params = m.get("id"), m["method"], m.get("params") or {}
            if mid is None:
                return None                 # notifications (e.g. notifications/initialized)

            def ok(result):
                return {"jsonrpc": "2.0", "id": mid, "result": result}
            if method == "initialize":
                return ok({"protocolVersion": params.get("protocolVersion") or PROTOCOL,
                           "capabilities": {"tools": {"listChanged": False}},
                           "serverInfo": {"name": "buildbook", "version": "1.0"},
                           "instructions": ("BuildBook: step-by-step build manuals in Autodesk Fusion. "
                                            "Start with list_steps; capture_step returns a picture. "
                                            "Distances are in the design's length units unless a unit "
                                            "is given (e.g. '25 mm').")})
            if method == "ping":
                return ok({})
            if method == "tools/list":
                tools = server.call("__list__", {})         # (on the main thread, like every call)
                return ok({"tools": tools if isinstance(tools, list) else []})
            if method == "tools/call":
                t0 = time.perf_counter()
                result = server.call(params.get("name"), params.get("arguments") or {})
                log.info("mcp: {} in {:.0f} ms".format(params.get("name"), (time.perf_counter() - t0) * 1000))
                return ok(result)
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Unknown method " + method}}
    return Handler
