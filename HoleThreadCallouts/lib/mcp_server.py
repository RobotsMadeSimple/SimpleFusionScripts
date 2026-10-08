"""Agent access: a small MCP server inside Fusion, so an AI agent can list a part's threaded holes,
set up views and export the callout image (lib/mcp_tools.py). Same design as BuildBook's.

Off until switched on in the panel (kept per user in mcp.json, in
%APPDATA%\\RobotsMadeSimple\\HoleThreadCallouts). It listens on this computer only (127.0.0.1) at
/mcp/<token>, speaking MCP's "streamable HTTP" transport with plain JSON answers: initialize,
tools/list, tools/call, ping. Fusion's API only works on its main thread, so each tool call is
queued, a custom event wakes the add-in on the main thread, and the request thread waits for the
answer. A tool can answer later (Deferred): the export waits for the panel to stitch the image.

Only the standard library: no packages to install.
"""

import json
import os
import queue
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import log

EVENT = "holeThreadCalloutsMcpCall"
FILE = "mcp.json"
PORT = 8766                     # (BuildBook's is 8765)
PROTOCOL = "2025-06-18"
CALL_TIMEOUT = 180.0            # seconds a tool may take (an export renders every view)


# ---------------------------------------------------------------- settings (per user)

def data_dir(*parts):
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "RobotsMadeSimple", "HoleThreadCallouts", *parts)


def settings():
    """{"enabled", "port", "token"}: off, port 8766, a fresh token by default."""
    try:
        with open(data_dir(FILE), encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    out = {"enabled": bool(data.get("enabled")), "port": int(data.get("port") or PORT),
           "token": data.get("token") or ""}
    if not out["token"]:
        out["token"] = secrets.token_urlsafe(18)
        save(out)
    return out


def save(values):
    os.makedirs(data_dir(), exist_ok=True)
    with open(data_dir(FILE), "w", encoding="utf-8") as f:
        json.dump(values, f)


def url(values):
    return "http://127.0.0.1:{}/mcp/{}".format(values["port"], values["token"])


# ---------------------------------------------------------------- answering later

class Deferred:
    """Returned by a tool that answers later (from another main-thread event): the request
    thread keeps waiting until resolve() or fail()."""

    def __init__(self, on_done):
        self.on_done = on_done          # (png bytes, path) -> MCP result
        self.job = None

    def _finish(self, result):
        if self.job is not None:
            self.job["result"] = result
            self.job["done"].set()

    def resolve(self, png, path):
        try:
            self._finish(self.on_done(png, path))
        except Exception as err:
            self._finish(_error("{}: {}".format(type(err).__name__, err)))

    def fail(self, text):
        self._finish(_error(text))


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
            return _error("Hole & Thread Callouts didn't answer in time (Fusion busy, or a dialog open?)")
        return job["result"]

    def run_pending(self, runner):
        """(main thread, from the custom event) Run every queued call with runner(name, args)."""
        while True:
            try:
                job = self.jobs.get_nowait()
            except queue.Empty:
                return
            try:
                result = runner(job["name"], job["args"])
                if isinstance(result, Deferred):
                    result.job = job            # answered later, by result.resolve / fail
                    continue
                job["result"] = result
            except Exception as err:
                log.error("mcp tool " + str(job["name"]))
                job["result"] = _error("{}: {}".format(type(err).__name__, err))
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
                           "serverInfo": {"name": "holethreadcallouts", "version": "1.0"},
                           "instructions": ("Hole & Thread Callouts: images showing which holes of a printed part "
                                            "to tap. Start with list_parts; get_holes lists a part's threaded "
                                            "holes; auto_views sets up views that show them all; export_image "
                                            "renders the callout image. Parts are occurrence paths from "
                                            "list_parts.")})
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
