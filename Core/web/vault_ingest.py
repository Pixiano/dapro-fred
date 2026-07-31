# Core/web/vault_ingest.py
#
# Localhost page for dumping a large blob of unstructured context and
# getting back vault-ready markdown, converted by Qwen3-14B running
# locally.
#
# Why a separate process rather than a route inside FRED: the converter
# is an 8.38GB model and FRED's resident set (gemma4 4.97 + Whisper ~1.3
# + embedder 1.12 + desktop ~2) already sits near 9.4GB of a 16.3GB
# card. Both cannot be loaded at once — that combination is exactly the
# ~14GB+ pressure that produced bare 0xc0000005 access violations before
# (see the DEFAULT_TIER comment in config/settings.py). So this loads
# the model only for the seconds a conversion takes, unloads it
# immediately after, and refuses to start a job at all when the card
# doesn't have room. Occasional batch use, not a resident service.
#
# Bound to 127.0.0.1 on purpose, never 0.0.0.0. The vault's own
# AGENT-BOOTSTRAP.md forbids personal/ and people/ content leaving this
# machine, and a dump pasted here can easily contain exactly that. A
# local model plus a loopback-only bind keeps that rule intact; binding
# to all interfaces would break it the moment anything else is on the
# network.
#
#   Core/venv/Scripts/python.exe Core/web/vault_ingest.py
#   -> http://127.0.0.1:8765

import gc
import html
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent.parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

import bottle
from bottle import Bottle, request, response

from config.settings import VAULT_DIR, MODEL_TIERS

HOST = "127.0.0.1"
PORT = 8765

# Qwen3-14B-Q4_K_M, already on disk as FRED's unused "deep" tier. Read
# from MODEL_TIERS rather than hardcoded so there is one source of truth
# for where models live.
CONVERTER_MODEL = MODEL_TIERS["deep"]

# Weights are 8.38GB; the rest is KV cache at n_ctx plus llama.cpp's
# compute buffers. Measured headroom requirement is conservative on
# purpose — llama.cpp does not raise a clean OOM when it runs out, it
# faults the whole process, so guessing low here costs a crash rather
# than an error message.
MIN_FREE_VRAM_MIB = 10_000
CONVERTER_CTX = 16384
CONVERTER_MAX_TOKENS = 8192

# Folders a converted note can land in, mapped to the frontmatter `type`
# each one uses — read off the real files in the vault rather than
# invented (people/ uses "reference", research/ uses "research", etc).
VAULT_SECTIONS = {
    "projects": "project",
    "knowledge": "knowledge",
    "research": "research",
    "people": "reference",
    "personal": "personal",
    "reference": "reference",
    "jobs": "job",
    "daily": "daily",
    "": "note",  # vault root
}

app = Bottle()

# One slot, reused across requests within a single conversion and
# released at the end of it. Never held between jobs — see the module
# docstring on why this cannot stay resident.
_model = None


# =========================================================
# VRAM
# =========================================================

def free_vram_mib():
    """Free VRAM in MiB, or None if nvidia-smi isn't answering (in which
    case the caller proceeds — refusing to run because a *diagnostic*
    failed would be worse than attempting the load)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return None


# =========================================================
# MODEL — loaded per job, released immediately after
# =========================================================

def load_model():
    global _model
    if _model is not None:
        return _model

    from utils.gpu_bootstrap import ensure_cuda_dlls
    ensure_cuda_dlls()
    from llama_cpp import Llama

    # chat_format left to the GGUF's own template. Qwen3 ships a working
    # chatml-style one that understands its <think> blocks; forcing
    # llama-cpp-python's "chatml-function-calling" default would replace
    # it and discard that, the same trap documented for gemma4 in
    # config/settings.py.
    _model = Llama(
        model_path=str(CONVERTER_MODEL),
        n_ctx=CONVERTER_CTX,
        n_gpu_layers=-1,
        verbose=False,
    )
    return _model


def unload_model():
    """Release the 8.38GB immediately. Mirrors LLMClient.unload() — it is
    close() that actually frees, not dropping the reference."""
    global _model
    if _model is None:
        return
    try:
        close = getattr(_model, "close", None)
        if close:
            close()
    except Exception as e:
        print(f"[vault_ingest] close() failed: {e}")
    _model = None
    gc.collect()
    print("[vault_ingest] converter unloaded")


# =========================================================
# CONVERSION
# =========================================================

_SYSTEM = """You convert raw, unstructured notes into a single markdown file for a personal knowledge vault. You output the file and nothing else.

The vault's format is strict. Follow it exactly:

1. YAML frontmatter first, between --- lines. Keys, in order:
   type: one of project, knowledge, research, reference, personal, job, daily, note
   status: active, archived, or paused
   updated: {today}
   source: stated (the user said it) or derived (you concluded it from the material)
   Add `sensitive: true` as the third key ONLY if the content covers health, identity, a named private individual, or anything similarly personal.

2. Then `# Title` — a real title, not a restatement of the filename.

3. Then a `>` blockquote: one line saying what this is and why it exists.

4. Then any `**Key:** value` lines that apply (Location, Stack, Repo, Relationship, Published, etc). Only ones the material actually supports.

5. Then `---` and `## Section` headings, each separated by `---` on its own line. Use the section set for this destination, given below. Do NOT use project sections on a person or a daily note.

Hard rules, in priority order:

- NEVER invent a fact. If an obvious section has no material behind it, write `[FILL IN: what's missing]` rather than filling it with plausible text. An honest gap is useful; an invented answer poisons everything downstream.
- Mark inferences. Anything you concluded rather than were told gets written as an inference — say "appears to", "seems", or tag it `[inferred]`. Never launder a guess into a stated fact.
- Preserve specifics exactly: numbers, dates, file paths, URLs, names, versions. Do not round, paraphrase, or tidy them.
- Never write a secret into the file. If the material contains a password, API key, token, or recovery code, record only where it lives, never the value itself.
- Keep the user's own wording where it carries meaning. You are reformatting, not rewriting.

Output the raw markdown file only. No preamble, no explanation, no code fences around it."""


# Section sets per destination. Without this the model applied the
# project template ("What it does", "Current state", "Decisions",
# "Constraints", "Known issues") to everything — which produced eleven
# people/ files carrying `Stack:` and `Repo:` headers on entries about
# real human beings, and buried a relationship chronology under a
# software-project schema. Read off the real _TEMPLATE.md files in the
# vault rather than invented.
SECTION_SETS = {
    "projects": "What it does, Current state, Decisions, Constraints, Known issues, Context",
    "jobs": "When to run this, Context (read these first), Steps, Done when",
    "people": "Context, Recurring, Notes — and a `**Relationship:**` / `**Comes up in:**` pair "
              "under the title. NEVER use project sections here, and never emit Stack/Repo/Published "
              "on a person. Only record what makes the assistant useful, not private detail about "
              "them that serves no purpose.",
    "personal": "Context, Current state, Notes",
    "knowledge": "What this is, Key points, Gotchas, Context",
    "research": "What it is, Findings, Relevance, Context",
    "reference": "Context, Details, Notes",
    "daily": "What happened, Decisions, Open threads, Profile updates",
}


def build_prompt(dump: str, section: str, hint: str):
    today = date.today().isoformat()
    wanted_type = VAULT_SECTIONS.get(section, "note")
    sections = SECTION_SETS.get(section, "Context, Details, Notes")

    user = [f"Convert the following into one vault file.\n"]
    user.append(f"It belongs in: {section or 'the vault root'}/")
    user.append(f"Use type: {wanted_type}")
    user.append(f"Use these sections: {sections}")
    if hint.strip():
        user.append(f"\nThe user adds this context about the dump: {hint.strip()}")
    user.append("\n--- RAW DUMP BEGINS ---\n")
    user.append(dump)
    user.append("\n--- RAW DUMP ENDS ---")

    return [
        {"role": "system", "content": _SYSTEM.format(today=today)},
        {"role": "user", "content": "\n".join(user)},
    ]


_FENCE_RE = re.compile(r"^\s*```(?:markdown|md)?\s*\n(.*?)\n\s*```\s*$", re.DOTALL)


def convert(dump: str, section: str, hint: str) -> str:
    from llm.llm_client import LLMClient

    model = load_model()
    result = model.create_chat_completion(
        messages=build_prompt(dump, section, hint),
        temperature=0.2,
        top_p=0.9,
        max_tokens=CONVERTER_MAX_TOKENS,
    )
    raw = result["choices"][0]["message"]["content"] or ""

    # Qwen3 is a reasoning model and emits <think> blocks like FRED's own
    # tiers do. Reuse the exact stripper FRED uses so the two can never
    # drift — including its refusal behaviour: an unterminated block
    # returns "", which means generation was cut off mid-reasoning and
    # there is no answer to show. That is reported as an error below
    # rather than silently rendering a monologue as if it were the file.
    cleaned = LLMClient._strip_thinking(raw)

    if not cleaned.strip():
        raise RuntimeError(
            "The model was cut off mid-reasoning and produced no file "
            f"(max_tokens={CONVERTER_MAX_TOKENS}). Try a smaller dump, "
            "or split it into two."
        )

    fenced = _FENCE_RE.match(cleaned)
    if fenced:
        cleaned = fenced.group(1)

    return cleaned.strip() + "\n"


def suggest_filename(markdown: str, section: str) -> str:
    """Slug from the H1, since that is the one line the model was told to
    make a real title. Falls back to a dated name rather than guessing."""
    match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    if match:
        slug = re.sub(r"[^\w\s-]", "", match.group(1).lower())
        slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    else:
        slug = f"note-{date.today().isoformat()}"
    return f"{section}/{slug}.md" if section else f"{slug}.md"


# =========================================================
# SAVING
# =========================================================

def resolve_target(rel_path: str) -> Path:
    """Resolve a user-supplied path inside the vault, refusing anything
    that escapes it. The page is loopback-only, but a traversal here
    would write outside a directory that has no git history and no undo,
    so it is checked rather than assumed."""
    rel_path = (rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not rel_path.endswith(".md"):
        rel_path += ".md"

    target = (VAULT_DIR / rel_path).resolve()
    if not str(target).startswith(str(VAULT_DIR.resolve())):
        raise ValueError("Refusing to write outside the vault.")
    return target


# =========================================================
# ROUTES
# =========================================================

@app.route("/")
def index():
    return PAGE.replace("__VAULT__", html.escape(str(VAULT_DIR))).replace(
        "__MODEL__", html.escape(CONVERTER_MODEL.name)
    )


@app.route("/convert", method="POST")
def do_convert():
    response.content_type = "application/json"
    data = request.json or {}
    dump = (data.get("dump") or "").strip()
    section = (data.get("section") or "").strip()
    hint = (data.get("hint") or "").strip()

    if not dump:
        return json.dumps({"error": "Nothing to convert — the dump is empty."})

    free = free_vram_mib()
    if free is not None and free < MIN_FREE_VRAM_MIB:
        return json.dumps({"error": (
            f"Not enough free VRAM: {free} MiB free, need ~{MIN_FREE_VRAM_MIB} MiB "
            f"for {CONVERTER_MODEL.name}. FRED is most likely holding gemma4 + "
            "Whisper + the embedder. Quit FRED from its tray icon (or let it "
            "idle-unload) and try again."
        )})

    try:
        markdown = convert(dump, section, hint)
        return json.dumps({
            "markdown": markdown,
            "path": suggest_filename(markdown, section),
        })
    except Exception as e:
        print(f"[vault_ingest] convert failed: {e}")
        return json.dumps({"error": str(e)})
    finally:
        # Released even on failure — a crashed conversion must not leave
        # 8.38GB parked where FRED needs it.
        unload_model()


@app.route("/save", method="POST")
def do_save():
    response.content_type = "application/json"
    data = request.json or {}
    markdown = data.get("markdown") or ""
    rel_path = data.get("path") or ""
    overwrite = bool(data.get("overwrite"))

    if not markdown.strip():
        return json.dumps({"error": "Nothing to save."})

    try:
        target = resolve_target(rel_path)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    if target.exists() and not overwrite:
        return json.dumps({
            "confirm": True,
            "error": f"{target.name} already exists. Overwrite? "
                     "The vault has no git history — this cannot be undone.",
        })

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown, encoding="utf-8")
    except OSError as e:
        return json.dumps({"error": f"Write failed: {e}"})

    print(f"[vault_ingest] wrote {target}")
    return json.dumps({
        "saved": str(target),
        # Surfaced rather than done automatically: the vault's own
        # AGENT-BOOTSTRAP.md requires MAP.md to be updated in the same
        # edit that creates a file, and silently rewriting MAP.md from a
        # web form is a worse failure than a reminder.
        "reminder": "Add this file to MAP.md — the vault's rules require "
                    "the map to be updated in the same edit that creates a file.",
    })


# =========================================================
# PAGE
# =========================================================

PAGE = """
<!doctype html>
<meta charset="utf-8">
<title>Vault Ingest</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; background:#0e1117; color:#e6edf3;
         font:15px/1.55 ui-sans-serif,system-ui,'Segoe UI',sans-serif; }
  .wrap { max-width:1400px; margin:0 auto; padding:24px; }
  h1 { font-size:20px; margin:0 0 4px; font-weight:600; }
  .sub { color:#8b949e; font-size:13px; margin-bottom:20px; }
  .sub code { color:#58a6ff; }
  .cols { display:grid; grid-template-columns:1fr 1fr; gap:20px; }
  @media (max-width:1000px) { .cols { grid-template-columns:1fr; } }
  label { display:block; font-size:12px; text-transform:uppercase;
          letter-spacing:.05em; color:#8b949e; margin:0 0 6px; }
  textarea, input, select {
    width:100%; background:#161b22; color:#e6edf3; border:1px solid #30363d;
    border-radius:8px; padding:10px 12px; font-size:14px; font-family:inherit; }
  textarea { resize:vertical; }
  #dump { height:340px; }
  #out { height:420px; font-family:ui-monospace,'Cascadia Code',Consolas,monospace;
         font-size:13px; }
  .row { display:flex; gap:12px; margin-bottom:14px; }
  .row > div { flex:1; }
  button { background:#238636; color:#fff; border:0; border-radius:8px;
           padding:11px 20px; font-size:14px; font-weight:600; cursor:pointer; }
  button:hover:not(:disabled) { background:#2ea043; }
  button:disabled { opacity:.5; cursor:not-allowed; }
  button.alt { background:#21262d; border:1px solid #30363d; }
  button.alt:hover:not(:disabled) { background:#30363d; }
  .bar { display:flex; gap:10px; align-items:center; margin-top:14px; }
  .msg { margin-top:14px; padding:11px 14px; border-radius:8px; font-size:13px;
         display:none; white-space:pre-wrap; }
  .msg.err { background:#3d1418; border:1px solid #8b2c34; color:#ffb3ba; display:block; }
  .msg.ok  { background:#0f2f1a; border:1px solid #2a6b3f; color:#a5e8bd; display:block; }
  .msg.work{ background:#1c2330; border:1px solid #30475e; color:#9dc7ff; display:block; }
</style>
<div class="wrap">
  <h1>Vault Ingest</h1>
  <div class="sub">
    Dump anything &rarr; <code>__MODEL__</code> reformats it to vault schema &rarr; you review, then save.
    Vault: <code>__VAULT__</code>. Local model, loopback only &mdash; nothing leaves this machine.
  </div>

  <div class="cols">
    <div>
      <div class="row">
        <div>
          <label>Goes in</label>
          <select id="section">
            <option value="projects">projects/</option>
            <option value="knowledge">knowledge/</option>
            <option value="research">research/</option>
            <option value="people">people/</option>
            <option value="personal">personal/</option>
            <option value="reference">reference/</option>
            <option value="jobs">jobs/</option>
            <option value="daily">daily/</option>
            <option value="">(vault root)</option>
          </select>
        </div>
        <div>
          <label>Hint (optional)</label>
          <input id="hint" placeholder="e.g. this is about the scraper rewrite">
        </div>
      </div>
      <label>Raw dump</label>
      <textarea id="dump" placeholder="Paste anything — notes, transcript, half-formed thoughts, a wall of text."></textarea>
      <div class="bar">
        <button id="go">Convert</button>
        <span style="color:#8b949e;font-size:12px">Loads the model, converts, unloads. First run takes a moment.</span>
      </div>
    </div>

    <div>
      <label>Save as (relative to vault)</label>
      <input id="path" placeholder="projects/something.md">
      <div style="height:14px"></div>
      <label>Converted &mdash; editable before saving</label>
      <textarea id="out" placeholder="Output appears here for review."></textarea>
      <div class="bar">
        <button id="save" class="alt" disabled>Save to vault</button>
      </div>
    </div>
  </div>

  <div id="msg" class="msg"></div>
</div>

<script>
const $ = id => document.getElementById(id);
const msg = (text, kind) => { const m = $('msg'); m.className = 'msg ' + kind; m.textContent = text; };

$('go').onclick = async () => {
  const dump = $('dump').value.trim();
  if (!dump) { msg('Paste something first.', 'err'); return; }
  $('go').disabled = true; $('save').disabled = true;
  msg('Loading the model and converting — this holds ~9GB of VRAM until it finishes.', 'work');
  try {
    const r = await fetch('/convert', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ dump, section: $('section').value, hint: $('hint').value })
    });
    const d = await r.json();
    if (d.error) { msg(d.error, 'err'); }
    else {
      $('out').value = d.markdown;
      $('path').value = d.path;
      $('save').disabled = false;
      msg('Converted. Read it before saving — check nothing was invented.', 'ok');
    }
  } catch (e) { msg('Request failed: ' + e, 'err'); }
  $('go').disabled = false;
};

async function save(overwrite) {
  const r = await fetch('/save', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ markdown: $('out').value, path: $('path').value, overwrite })
  });
  const d = await r.json();
  if (d.confirm) { if (confirm(d.error)) save(true); return; }
  if (d.error) { msg(d.error, 'err'); return; }
  msg('Saved to ' + d.saved + '\\n\\n' + d.reminder, 'ok');
}
$('save').onclick = () => save(false);
</script>
"""


if __name__ == "__main__":
    print(f"[vault_ingest] vault    : {VAULT_DIR}")
    print(f"[vault_ingest] converter: {CONVERTER_MODEL}")
    if not CONVERTER_MODEL.exists():
        print(f"[vault_ingest] WARNING: converter model not found at that path")
    print(f"[vault_ingest] serving  : http://{HOST}:{PORT}")
    bottle.run(app, host=HOST, port=PORT, quiet=True)
