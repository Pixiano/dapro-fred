"""audio_device: switch the Windows default audio device (and FRED's own mic) from words like "switch to my new earbuds". Add-on skill (skills/extra).

Sockets
  in : run(action="switch", device="", kind="auto")
       action list   -> connected outputs and inputs (the current one marked) and paired-but-not-connected devices
       action switch -> resolves `device` to ONE connected device and sets it as the Windows default (console, multimedia and communications roles); a headset-type device that has both a
                        speaker and a microphone switches both. `kind` input/output restricts it; auto decides from the words (mic words -> input, speaker words or a headset -> output).
       action remember_alias and the `as_word` argument (only ever sent by the yes/no follow-ups, never by the model) -> saves "<word> = <device>" to the personal model
  out: one spoken sentence. A switch needs a CONNECTED device: a paired Bluetooth device that is not connected is named and left alone (nothing is connected for you).
       Two close candidates -> "Which one: A or B?" and the next request with the name finishes it (no hidden state). Nothing plausible -> says so and lists what is there.
Matching (pure, `resolve`): score = remembered alias (+3) + each informative word shared with the device name (+2) + the meaning of the words by embedding (up to +2) + category agreement
  (headphones-words vs a headset-like name, speaker-words vs a speaker-like name, monitor-words vs a display-like name, +1.5) - category contradiction (-1.5) + generic wording (+1.5 for every candidate, elimination does the rest) - the device already in use (-1) + "new" for a device never seen before (+0.5). The elimination
  falls out of the category terms: "my earbuds" with one connected headset-like device and only speakers and a monitor otherwise leaves exactly one. Accepted when the best score >= MIN
  and it leads the next by MARGIN; otherwise it asks.
Memory: aliases live in the personal model as "audio device alias: <words> = <group>" (visible, correctable, forgettable like any item). After a switch that was NOT picked by an explicit
  device name or an existing alias, the skill asks "Should I remember that <words> means <device>?" through the engine's yes/no slot (registry.confirmer); yes runs remember_alias.
FRED side: if ctx has `mic`, its device is pointed at the new input and reopened; if ctx has `bus`, audio.device_changed {output, input} is emitted (the voice output in P4 follows it).
bind(ctx) optional keys: recall (aliases, memory), embed (callable texts -> vectors), mic, bus, registry. Backend: Windows Core Audio through pycaw/comtypes; IPolicyConfig sets the default
(the interface Windows' own sound settings use; undocumented but stable since Vista). Tests replace `BACKEND` with a fake and never touch the real devices.
"""
import json
import re
import warnings
from dataclasses import dataclass

import config
from tools.registry import ToolError

MIN, MARGIN = 1.5, 1.0
_ctx = {"c": {}}

STOP = set("switch change set use make go put play move send route over on onto to the my our new old a an please audio sound output input default device back fred it that for and with of "
           "from now again instead sir hey other another different second alternate alternative".split())
PERSONAL_WORDS = set("headphones headphone headset earbuds earbud earphones earphone buds airpods earpods pods neckband".split())
SPEAKER_WORDS = set("speakers speaker soundbar".split())
DISPLAY_WORDS = set("monitor monitors tv screen display".split())
MIC_WORDS = set("mic microphone microphones input recording".split())
PERSONAL_NAME = re.compile(r"headphone|headset|buds|airpods|earphone|earbud|hands-free|neckband|pods", re.I)
SPEAKER_NAME = re.compile(r"^speakers?\b|soundbar", re.I)
DISPLAY_NAME = re.compile(r"nvidia|amd high|digital output|display|hdmi|monitor|\blcd\b|\btv\b", re.I)
GENERIC = set("audio usb high definition device realtek nvidia amd r hands free stereo digital speakers speaker headphones headset microphone mic output input".split())


def bind(ctx):
    _ctx["c"] = ctx


@dataclass
class Dev:
    id: str
    name: str
    flow: str                      # "out" | "in"
    state: str                     # "active" (connected) | "unplugged" (paired, not connected)
    default: bool = False


def group_key(name: str) -> str:
    m = re.search(r"\((.*)\)\s*$", name)
    return re.sub(r"\s*hands-free\s*$", "", (m.group(1) if m else name), flags=re.I).strip().lower()


def words(text: str) -> list:
    return re.findall(r"[a-z0-9]+", text.lower())


def informative(text: str) -> set:
    return {w for w in words(text) if w not in STOP and w not in PERSONAL_WORDS and w not in SPEAKER_WORDS and w not in DISPLAY_WORDS and w not in MIC_WORDS and w not in GENERIC and len(w) > 1}


def category(name: str) -> str:
    if DISPLAY_NAME.search(name):
        return "display"
    if SPEAKER_NAME.search(name):
        return "speaker"
    return "personal" if PERSONAL_NAME.search(name) else "other"


def group_category(devs: list, flow: str) -> str:
    """personal if any endpoint of the group reads like a headset (the hands-free twin gives it away), speaker if the connected endpoint of this direction reads like a speaker or display."""
    if any(PERSONAL_NAME.search(d.name) for d in devs):
        return "personal"
    act = [d for d in devs if d.flow == flow and d.state == "active"]
    return "display" if any(DISPLAY_NAME.search(d.name) for d in act) else ("speaker" if any(SPEAKER_NAME.search(d.name) for d in act) else "other")


def said_category(said: str) -> str:
    w = set(words(said))
    return "personal" if w & PERSONAL_WORDS else ("display" if w & DISPLAY_WORDS else ("speaker" if w & SPEAKER_WORDS else ""))      # "the monitor's speakers" means the monitor


# ---- Windows backend ---------------------------------------------------------------------------------------------------------------------
class Windows:
    def devices(self) -> list:
        import comtypes
        from pycaw.pycaw import AudioUtilities
        comtypes.CoInitialize()
        defaults = {self._default_id(f) for f in (0, 1)}
        out = []
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")                                      # pycaw warns about unreadable properties of ghost endpoints
            for d in AudioUtilities.GetAllDevices():
                st = str(d.state).rsplit(".", 1)[-1].lower()
                if st not in ("active", "unplugged") or not d.FriendlyName:
                    continue                                                      # NotPresent / Disabled endpoints are ghosts of old hardware
                out.append(Dev(d.id, d.FriendlyName, "out" if d.id.startswith("{0.0.0") else "in", st, d.id in defaults))
        return out

    @staticmethod
    def _default_id(flow: int) -> str:
        from pycaw.pycaw import AudioUtilities
        try:
            return AudioUtilities.GetDeviceEnumerator().GetDefaultAudioEndpoint(flow, 0).GetId()
        except Exception:
            return ""                                                             # no default device of that kind

    def set_default(self, dev_id: str) -> None:
        import comtypes
        from ctypes import HRESULT
        from ctypes.wintypes import DWORD, LPCWSTR
        from comtypes import COMMETHOD, GUID, IUnknown
        comtypes.CoInitialize()

        def stub(name):
            return COMMETHOD([], HRESULT, name)                                   # vtable slots we never call; only their number matters

        class IPolicyConfig(IUnknown):
            _iid_ = GUID("{F8679F50-850A-41CF-9C72-430F290290C8}")
            _methods_ = tuple(stub(n) for n in ("GetMixFormat", "GetDeviceFormat", "ResetDeviceFormat", "SetDeviceFormat", "GetProcessingPeriod", "SetProcessingPeriod", "GetShareMode",
                                                "SetShareMode", "GetPropertyValue", "SetPropertyValue")) + (
                COMMETHOD([], HRESULT, "SetDefaultEndpoint", (["in"], LPCWSTR, "pszDeviceId"), (["in"], DWORD, "role")), stub("SetEndpointVisibility"))

        cfg = comtypes.CoCreateInstance(GUID("{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}"), interface=IPolicyConfig, clsctx=comtypes.CLSCTX_ALL)
        for role in (0, 1, 2):                                                    # console, multimedia, communications
            cfg.SetDefaultEndpoint(dev_id, role)


BACKEND = None                                                                    # tests put a fake with devices() / set_default(id) here


def _backend():
    return BACKEND or Windows()


# ---- matching (pure) ---------------------------------------------------------------------------------------------------------------------
def kind_of(said: str, kind: str, group_has_both: bool) -> str:
    """'output', 'input' or 'both' for the chosen group."""
    if kind in ("output", "input"):
        return kind
    w = set(words(said))
    if w & MIC_WORDS:
        return "input"
    return "both" if group_has_both and (w & PERSONAL_WORDS or not said_category(said)) else "output"


def groups(devs: list) -> dict:
    g = {}
    for d in devs:
        g.setdefault(group_key(d.name), []).append(d)
    return g


def resolve(said: str, devs: list, kind: str = "auto", aliases: dict | None = None, seen: set | None = None, sims: dict | None = None):
    """-> ("switch", key, score, how) | ("ask", [keys]) | ("maybe", key: the one connected device whose name says nothing) | ("none", [paired-not-connected group keys]).  Only CONNECTED groups with the wanted direction compete."""
    want_in = kind == "input" or (kind == "auto" and bool(set(words(said)) & MIC_WORDS))
    flow = "in" if want_in else "out"
    allg = groups(devs)
    active = {k: v for k, v in allg.items() if any(d.state == "active" and d.flow == flow for d in v)}
    cat, inf, sw = said_category(said), informative(said), set(words(said))
    aliases, seen, sims = aliases or {}, seen or set(), sims or {}
    scores, how, maybes = {}, {}, []
    for k, v in active.items():
        names = " ".join(d.name for d in v)
        s = 0.0
        if aliases and any(aw and aw <= sw for aw in aliases if aliases[aw] == k):
            s += 3.0
            how[k] = "alias"
        hit = inf & informative(names)
        if hit:
            s += 2.0 * len(hit)
            how.setdefault(k, "name")
        vcat = group_category(v, flow)
        if cat:
            s += 1.5 if vcat == cat else (-1.5 if vcat in ("speaker", "personal", "display") else 0.0)
        if not inf and k not in how:
            s += 1.5                                                                 # generic words ("my headphones", "the mic"): every connected candidate starts level; category, elimination of the current one and memory decide
        s += 2.0 * sims.get(k, 0.0)
        if "new" in sw and k not in seen:
            s += 0.5
        if cat and vcat == "other" and k not in how:
            maybes.append(k)                                                         # a name that says nothing: never switched to on category wording alone, asked about instead
            s = min(s, MIN - 0.5)
        if any(d.default and d.flow == flow for d in v) and k not in how:
            s -= 1.0                                                                # asked to SWITCH: the one already in use only wins when nothing else fits (then it says "already using")
        scores[k] = s
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    if ranked and ranked[0][1] >= MIN and (len(ranked) == 1 or ranked[0][1] - ranked[1][1] >= MARGIN):
        return ("switch", ranked[0][0], ranked[0][1], how.get(ranked[0][0], "category"))
    close = [k for k, s in ranked if s >= MIN and ranked and ranked[0][1] - s < MARGIN]
    if len(close) > 1:
        return ("ask", close)
    others = [k for k in maybes if not any(d.default and d.flow == flow for d in active[k])]
    if len(others) == 1:
        return ("maybe", others[0])
    paired = sorted(k for k, v in allg.items() if k not in active and any(d.state == "unplugged" and d.flow == flow for d in v)
                    and (not cat or group_category(v, flow) == cat or group_category(v, flow) == "other"))
    return ("none", paired)


# ---- memory, history, effects ------------------------------------------------------------------------------------------------------------
def _aliases() -> dict:
    """{frozenset(words): group key} from the personal model."""
    rc = _ctx["c"].get("recall")
    out = {}
    try:
        for r in rc.model.q("SELECT text FROM model_items WHERE status='active' AND text LIKE 'audio device alias:%'"):
            m = re.fullmatch(r"audio device alias:\s*(.+?)\s*=\s*(.+)", r["text"])
            if m:
                out[frozenset(informative(m.group(1)) | (set(words(m.group(1))) & (PERSONAL_WORDS | SPEAKER_WORDS | MIC_WORDS)))] = m.group(2).strip().lower()
    except Exception:
        pass
    return out


def _seen_path():
    return config.data_dir() / "audio_seen.json"


def _seen_load() -> set:
    try:
        return set(json.loads(_seen_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _seen_save(keys: set):
    try:
        _seen_path().parent.mkdir(parents=True, exist_ok=True)
        _seen_path().write_text(json.dumps(sorted(keys)), encoding="utf-8")
    except OSError:
        pass                                                                      # the history is only a hint for "new"


def _sims(said: str, keys: list) -> dict:
    emb = _ctx["c"].get("embed")
    clean = " ".join(w for w in words(said) if w not in STOP) or said
    if not emb or not clean.strip() or not keys:
        return {}
    try:
        v = emb([clean] + keys)
        import math
        n = lambda a: math.sqrt(sum(x * x for x in a)) or 1.0
        return {k: max(0.0, sum(a * b for a, b in zip(v[0], v[i + 1])) / (n(v[0]) * n(v[i + 1])) - 0.35) for i, k in enumerate(keys)}      # only clearly related names add anything
    except Exception:
        return {}


def _apply_fred(out_name, in_name, in_key):
    c = _ctx["c"]
    mic = c.get("mic")
    if in_name and mic is not None:
        try:
            mic.device = in_key
            mic.pause()
            mic.resume()
        except Exception:
            pass
    if c.get("bus") is not None:
        c["bus"].emit("audio", "device_changed", output=out_name, input=in_name)


# ---- the skill ---------------------------------------------------------------------------------------------------------------------------
def _shown(devs: list) -> str:
    """One name per physical device: the stereo endpoint, not its hands-free twin."""
    return sorted(devs, key=lambda d: "hands-free" in d.name.lower())[0].name


def _listing(devs: list) -> str:
    def part(flow, label):
        names = [d.name + (" (current)" if d.default else "") for d in devs if d.flow == flow and d.state == "active"]
        return f"{label}: " + (", ".join(names) if names else "none connected") + "."
    g = groups([d for d in devs if d.state == "unplugged" and d.flow == "out"])
    paired = sorted(_shown(v) for k, v in g.items() if group_category(v, "out") in ("personal", "other"))
    return " ".join([part("out", "Outputs"), part("in", "Inputs")] + ([f"Paired but not connected: {', '.join(paired)}."] if paired else []))


def run(action: str = "switch", device: str = "", kind: str = "auto", as_word: str = "") -> str:
    be = _backend()
    try:
        devs = be.devices()
    except Exception as ex:
        raise ToolError("unavailable", "I can't reach the audio devices right now.", f"audio devices: {ex!r}")
    if action == "list":
        return _listing(devs)
    if action == "remember_alias":
        return _remember(device)
    if not device.strip():
        raise ToolError("bad_args", "Which device?", "say which device: headphones, speakers, a microphone, or a name")
    seen = _seen_load()
    allg = groups(devs)
    res = resolve(device, devs, kind, _aliases(), seen, _sims(device, sorted(allg)))
    _seen_save(seen | {k for k, v in allg.items() if any(d.state == "active" for d in v)})
    if res[0] == "ask":
        pretty = [next(d.name for d in allg[k] if d.state == "active") for k in res[1]]
        return f"Which one: {' or '.join(pretty)}? Say the name."
    if res[0] == "maybe":
        flow = "in" if kind == "input" or (kind == "auto" and set(words(device)) & MIC_WORDS) else "out"
        name = next(d.name for d in allg[res[1]] if d.state == "active" and d.flow == flow)
        label = " ".join(w for w in words(device) if w not in STOP) or "that"
        conf = getattr(_ctx["c"].get("registry"), "confirmer", None)
        if conf is None:
            return f"Nothing connected is named like {label}. Do you mean {name}? Say its name to use it."
        return conf.ask("audio_device", {"action": "switch", "device": name, "kind": kind, "as_word": label}, f"Nothing connected is named like {label}. Do you mean {name}? Yes or no?")
    if res[0] == "none":
        if res[1]:
            return f"I can't find a connected device for that. Paired but not connected: {', '.join(_shown(allg[k]) for k in res[1])}. Connect one and ask again."
        names = [d.name for d in devs if d.state == "active" and d.flow == ("in" if kind == "input" or set(words(device)) & MIC_WORDS else "out")]
        return f"I can't find a connected device for that. Connected: {', '.join(names) or 'nothing'}."
    key, how = res[1], res[3]
    grp = allg[key]
    outs = [d for d in grp if d.flow == "out" and d.state == "active"]
    ins = [d for d in grp if d.flow == "in" and d.state == "active"]
    outs.sort(key=lambda d: "hands-free" in d.name.lower())                      # stereo output before the hands-free one
    what = kind_of(device, kind, bool(outs and ins))
    chosen_out = outs[0] if outs and what in ("output", "both") else None
    chosen_in = ins[0] if ins and what in ("input", "both") else None
    if (what == "input" and not chosen_in) or (what != "input" and not chosen_out and not chosen_in):
        return f"{allg[key][0].name} has no {'microphone' if what == 'input' else 'speaker'} connected."
    if all(d.default for d in (chosen_out, chosen_in) if d):
        return f"Already using {(chosen_out or chosen_in).name}."
    try:
        for d in (chosen_out, chosen_in):
            if d and not d.default:
                be.set_default(d.id)
    except Exception as ex:
        raise ToolError("unavailable", "Windows wouldn't switch the device.", f"set default endpoint failed: {ex!r}")
    _apply_fred(chosen_out.name if chosen_out else None, chosen_in.name if chosen_in else None, key)
    said = f"Switched to {(chosen_out or chosen_in).name}" + (" for speaker and microphone." if chosen_out and chosen_in else (" for the microphone." if not chosen_out else "."))
    return said + _offer_alias(device, key, how, (chosen_out or chosen_in).name, as_word)


def _offer_alias(device: str, key: str, how: str, shown: str, as_word: str = "") -> str:
    if how in ("alias", "name") and not as_word:
        return ""                                                                  # an explicit name or an existing alias needs no teaching
    rc, conf = _ctx["c"].get("recall"), getattr(_ctx["c"].get("registry"), "confirmer", None)
    label = as_word or " ".join(w for w in words(device) if w not in STOP) or ""
    if rc is None or conf is None or not label:
        return ""
    return " " + conf.ask("audio_device", {"action": "remember_alias", "device": f"{label} = {key}"}, f"Should I remember that {label} means {shown}? Yes or no?")


def _remember(spec: str) -> str:
    rc = _ctx["c"].get("recall")
    m = re.fullmatch(r"(.+?)\s*=\s*(.+)", spec.strip())
    if rc is None or not m:
        raise ToolError("bad_args", "I couldn't save that.", "remember_alias needs memory and 'words = device'")
    rc.model.add("preference", f"audio device alias: {m.group(1).strip()} = {m.group(2).strip()}")
    return f"Okay, I'll remember that {m.group(1).strip()} means that device."
