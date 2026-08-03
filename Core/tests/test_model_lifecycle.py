# Confirms preload()'s one asymmetric rule: Whisper/Kokoro always
# reload on hotkey-down, but the LLM only reloads locally when offline
# — see model_lifecycle.py's docstring for why (the cloud cascade in
# llm_client.py answers first whenever there's a connection).

import time

import utils.model_lifecycle as model_lifecycle
from utils.model_lifecycle import ModelLifecycle


class _FakeModel:
    def __init__(self):
        self.loaded = False
        self.ensure_loaded_calls = 0

    def is_loaded(self):
        return self.loaded

    def ensure_loaded(self):
        self.ensure_loaded_calls += 1
        self.loaded = True

    def unload(self):
        was_loaded = self.loaded
        self.loaded = False
        return was_loaded


def _run_preload_and_wait(lifecycle):
    lifecycle.preload()
    # preload() spawns a daemon thread and returns immediately by design
    # (see its docstring); the fakes' ensure_loaded() is instant, so a
    # short fixed wait is enough for it to have run.
    time.sleep(0.3)


def test_preload_skips_local_llm_when_online(monkeypatch):
    monkeypatch.setattr(model_lifecycle, "_is_online", lambda: True)
    llm, stt, tts = _FakeModel(), _FakeModel(), _FakeModel()
    lifecycle = ModelLifecycle(llm=llm, stt=stt, tts=tts)

    _run_preload_and_wait(lifecycle)

    assert llm.ensure_loaded_calls == 0
    assert stt.ensure_loaded_calls == 1
    assert tts.ensure_loaded_calls == 1


def test_preload_loads_local_llm_when_offline(monkeypatch):
    monkeypatch.setattr(model_lifecycle, "_is_online", lambda: False)
    llm, stt, tts = _FakeModel(), _FakeModel(), _FakeModel()
    lifecycle = ModelLifecycle(llm=llm, stt=stt, tts=tts)

    _run_preload_and_wait(lifecycle)

    assert llm.ensure_loaded_calls == 1
    assert stt.ensure_loaded_calls == 1
    assert tts.ensure_loaded_calls == 1
