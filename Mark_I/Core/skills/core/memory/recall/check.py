"""Self-check for the recall skill: overview, recent conversation, forced search, nothing found. No GPU."""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import config
from skills.testkit import memory_ctx, skill


def _selfcheck():
    pick_dentist = lambda m, tools: json.dumps({"relevant": [int(l.split(":")[0]) for l in m[0]["content"].splitlines() if l[:1].isdigit() and "dentist" in l], "retry_queries": [], "ask": ""})
    nothing = json.dumps({"relevant": [], "retry_queries": [], "ask": ""})
    with tempfile.TemporaryDirectory() as t:
        mem, ctx = memory_ctx(t, [pick_dentist, nothing, nothing, nothing, nothing, nothing])
        rc = ctx["recall"]
        reg, _ = skill("recall", ctx)
        assert not reg.spec("recall").destructive and reg.call("recall").data == "Nothing yet."                           # no query: what is known (nothing yet)
        rc.model.add("preference", "likes dark mode")
        assert "likes dark mode" in reg.call("recall").data and "not the screen" in reg.call("recall").data
        assert reg.call("recall", {"days": 2}).data == "We haven't talked in that time."
        mem.archive.write("conversation", {"role": "user", "text": "hello fred"}, ts=datetime.now(config.IST).isoformat())
        assert "You: hello fred" in reg.call("recall", {"days": 2}).data
        mem.store.add_event("note", "the dentist appointment is on Thursday at 4 pm")
        mem.store.add_event("note", "bought tomatoes at the market")
        r = reg.call("recall", {"query": "dentist"}).data                                                                 # "dentist" alone does not look like a past reference: the skill forces the search
        assert "dentist appointment is on Thursday" in r and "tomatoes" not in r, r
        assert "keyword" in reg.call("recall", {"query": "the zebra project"}).data                                        # nothing found: the memory asks for a keyword instead of guessing
        mem.close()
    print("recall check ok")


if __name__ == "__main__":
    _selfcheck()
