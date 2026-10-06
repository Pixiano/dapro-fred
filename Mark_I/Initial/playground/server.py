"""FRED model playground: one OpenAI-compatible endpoint (default http://localhost:8090/v1) in front of llama.cpp's llama-server, for every model FRED uses.

  python server.py            start it, then open http://localhost:8090 (chat, embeddings, load/unload, VRAM, register a model)
  python server.py stop       unload everything (kills the llama-server children)
A request names a model ("model": id from models.json); the server loads it on demand and swaps the chat model when another one is asked for, so the
analysts' usual "one model on the GPU at a time" still holds. One chat slot + one embedding slot, both fully on the GPU (-ngl 99): nothing runs on the CPU.
Any .gguf under models_dir that is not listed in models.json also shows up (id "local/<file stem>"), so a freshly downloaded model needs no config.
API: /v1/models, /v1/chat/completions, /v1/completions, /v1/embeddings (streaming passes straight through) and
     GET /api/status, POST /api/load {model, ctx?, vision?}, POST /api/unload {slot: chat|embed|all}, POST /api/register {id, path, kind, ctx, ...}
`reasoning_effort` in a chat request is passed to the template as chat_template_kwargs (gpt-oss), response_format json_schema is enforced by llama.cpp's grammar.
ponytail: no auth (127.0.0.1 only), one request at a time per slot is not enforced beyond a swap waiting for in-flight requests, no queue.
"""
import atexit
import http.client
import json
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
CFG_FILE = HERE / "models.json"
CFG = json.loads(CFG_FILE.read_text(encoding="utf-8"))
PORT = CFG.get("port", 8090)
INTERNAL = {"chat": 8191, "embed": 8192}
LOAD_TIMEOUT = 600


class Slot:
    def __init__(self, name):
        self.name, self.port, self.proc, self.model, self.ctx, self.vision = name, INTERNAL[name], None, None, None, False
        self.cond, self.active = threading.Condition(), 0

    def alive(self):
        return self.proc is not None and self.proc.poll() is None


SLOTS = {k: Slot(k) for k in INTERNAL}
_swap = threading.Lock()


def registry():
    """Listed models plus any unlisted .gguf under models_dir (not mmproj files)."""
    models, root = {m["id"].lower(): dict(m) for m in CFG["models"]}, Path(CFG["models_dir"])
    listed = {str((root / m["path"]).resolve()).lower() for m in CFG["models"]}
    for g in root.rglob("*.gguf"):
        if "mmproj" in g.name.lower() or str(g.resolve()).lower() in listed:
            continue
        i = "local/" + g.stem.lower()
        models[i] = {"id": i, "path": str(g.relative_to(root)).replace("\\", "/"), "ctx": 8192, "kind": "embed" if "embed" in g.name.lower() else "chat",
                     "args": ["--embedding"] if "embed" in g.name.lower() else [], "note": "found on disk, not in models.json"}
    return models


def find(name):
    reg, n = registry(), (name or "").lower()
    if n in reg:
        return reg[n]
    hits = [m for k, m in reg.items() if n and n in k]
    return hits[0] if len(hits) == 1 else None


def stop(slot):
    with slot.cond:
        while slot.active:
            slot.cond.wait(1)
        if slot.alive():
            slot.proc.kill()
            slot.proc.wait()
        slot.proc, slot.model = None, None


def ensure(m, ctx=None, vision=False):
    """The slot for model m, loaded with the wanted context. Swaps the model in that slot if another is there."""
    slot = SLOTS[m["kind"]]
    ctx = int(ctx or m.get("ctx", 8192))
    with _swap:
        if slot.alive() and slot.model == m["id"] and slot.ctx == ctx and slot.vision == bool(vision):
            return slot
        stop(slot)
        root = Path(CFG["models_dir"])
        cmd = [CFG["llama_server"], "-m", str(root / m["path"]), "-c", str(ctx), "-ngl", "99", "--port", str(slot.port), "--host", "127.0.0.1",
               "--jinja", "-fa", "on", "-np", "1", "--no-webui", *m.get("args", [])]
        if vision and m.get("mmproj"):
            cmd += ["--mmproj", str(root / m["mmproj"])]
        log = open(HERE / f"llama-{slot.name}.log", "w", encoding="utf-8")
        slot.proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
        t = time.time()
        while time.time() - t < LOAD_TIMEOUT:
            if not slot.alive():
                raise RuntimeError(f"llama-server exited while loading {m['id']} (see llama-{slot.name}.log)")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{slot.port}/health", timeout=2) as r:
                    if r.status == 200:
                        slot.model, slot.ctx, slot.vision = m["id"], ctx, bool(vision)
                        return slot
            except Exception:
                time.sleep(1)
        stop(slot)
        raise RuntimeError(f"{m['id']} did not load within {LOAD_TIMEOUT}s")


def gpu():
    try:
        o = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5).stdout
        n, u, t = [x.strip() for x in o.splitlines()[0].split(",")]
        return {"name": n, "used_mb": int(u), "total_mb": int(t)}
    except Exception:
        return None


def status():
    return {"slots": {k: ({"model": s.model, "ctx": s.ctx, "vision": s.vision, "pid": s.proc.pid} if s.alive() else None) for k, s in SLOTS.items()},
            "models": [dict(m, loaded=any(s.alive() and s.model == m["id"] for s in SLOTS.values())) for m in registry().values()], "gpu": gpu()}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"   # streamed bodies simply end when the connection closes

    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b)

    def body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in (("Access-Control-Allow-Origin", "*"), ("Access-Control-Allow-Headers", "*"), ("Access-Control-Allow-Methods", "*")):
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            b = (HERE / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif self.path == "/api/status":
            self.send_json(status())
        elif self.path.rstrip("/") == "/v1/models":
            self.send_json({"object": "list", "data": [{"id": m["id"], "object": "model", "owned_by": "local", "loaded": m["loaded"]} for m in status()["models"]]})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            b = self.body()
            if self.path == "/api/load":
                m = find(b.get("model"))
                if not m:
                    return self.send_json({"error": f"unknown model {b.get('model')!r}"}, 404)
                ensure(m, b.get("ctx"), b.get("vision"))
                return self.send_json(status())
            if self.path == "/api/unload":
                for k in (INTERNAL if b.get("slot") in (None, "all") else [b["slot"]]):
                    stop(SLOTS[k])
                return self.send_json(status())
            if self.path == "/api/register":
                CFG["models"] = [m for m in CFG["models"] if m["id"] != b["id"]] + [{k: v for k, v in b.items() if v not in (None, "")}]
                CFG_FILE.write_text(json.dumps(CFG, indent=2), encoding="utf-8")
                return self.send_json(status())
            if self.path in ("/v1/chat/completions", "/v1/completions", "/v1/embeddings"):
                return self.proxy(b)
            self.send_json({"error": "not found"}, 404)
        except Exception as ex:
            self.send_json({"error": {"message": str(ex)}}, 500)

    def proxy(self, b):
        emb = self.path.endswith("embeddings")
        m = find(b.get("model")) if b.get("model") else None
        if not m:   # no/unknown model: whatever is loaded in that slot
            cur = SLOTS["embed" if emb else "chat"]
            m = find(cur.model) if cur.alive() else None
        if not m:
            return self.send_json({"error": {"message": f"unknown or no model loaded ({b.get('model')!r}); see /v1/models"}}, 404)
        if (m["kind"] == "embed") != emb:
            return self.send_json({"error": {"message": f"{m['id']} is a {m['kind']} model, wrong endpoint"}}, 400)
        slot = ensure(m)
        with slot.cond:
            slot.active += 1
        try:
            b = dict(b, model=m["id"])
            if "reasoning_effort" in b and "chat_template_kwargs" not in b:
                b["chat_template_kwargs"] = {"reasoning_effort": b["reasoning_effort"]}
            c = http.client.HTTPConnection("127.0.0.1", slot.port, timeout=3600)
            c.request("POST", self.path, json.dumps(b), {"Content-Type": "application/json"})
            r = c.getresponse()
            self.send_response(r.status)
            self.send_header("Content-Type", r.getheader("Content-Type", "application/json"))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            while chunk := r.read1(65536):
                self.wfile.write(chunk)
                self.wfile.flush()
            c.close()
        finally:
            with slot.cond:
                slot.active -= 1
                slot.cond.notify_all()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "stop":
        print(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/api/unload", b"{}", {"Content-Type": "application/json"})).status)
        return
    atexit.register(lambda: [stop(s) for s in SLOTS.values()])
    print(f"playground on http://localhost:{PORT}  (OpenAI API at /v1)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
