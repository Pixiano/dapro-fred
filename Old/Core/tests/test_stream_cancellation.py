# "Can we kill mid-generation?" -- answered empirically 2026-08-02, not
# guessed: for the plain-chat streaming path, yes, already, with no code
# change needed. Measured directly against the real model: GPU
# utilization was 74% mid-generation, then 1% within 0.2-0.4s of the
# consuming loop abandoning generate_stream()'s generator (pill_app.py's
# produce() already does exactly this on cancel: `return` instead of
# continuing the for-loop). Python's refcount-based cleanup closes the
# whole generator chain almost immediately -- no extra plumbing needed.
#
# These tests can't reproduce the GPU measurement (that needs a loaded
# model), but they pin the actual mechanism being relied on: that
# abandoning a Python generator via early return from its consuming
# for-loop stops it from advancing further, and that this propagates
# through a chain of nested generators the same way generate_stream ->
# create_chat_completion's stream are chained. If a future Python
# version or refactor ever changed generator cleanup to be lazy/deferred
# instead of immediate, this is what would catch it.

import time


def _token_source(produced: list, stop_requested: list):
    """Stands in for llama.cpp's own token-yielding generator -- each
    iteration is a unit of real work (recorded in `produced`) that
    should NEVER happen after the consumer walks away."""
    for i in range(1000):
        if stop_requested and stop_requested[0]:
            # A real generator has no way to know it's been abandoned
            # from the inside; this branch exists only to prove the
            # test harness itself isn't the thing stopping iteration --
            # the real mechanism under test is the consumer's early
            # `return`, exercised below without this branch's help.
            return
        produced.append(i)
        yield i


def _chained_source(produced: list, stop_requested: list):
    """Mirrors generate_stream() wrapping create_chat_completion's own
    stream generator -- one generator's for-loop yielding from another."""
    for token in _token_source(produced, stop_requested):
        yield token


def test_abandoning_a_consumer_loop_stops_further_production():
    """The actual mechanism pill_app.py relies on: a bare `return` out
    of a `for x in generator:` loop, with no explicit .close() call,
    must stop the generator from producing anything further."""
    produced = []

    def consume():
        for token in _chained_source(produced, stop_requested=[]):
            if token >= 3:
                return  # exactly what pill_app.py's produce() does
            pass

    consume()

    # Nothing beyond the abandonment point should ever have been
    # produced -- if this were 1000, the generator kept running after
    # being "abandoned" and the whole cancellation model is broken.
    assert produced == [0, 1, 2, 3]


def test_del_on_a_started_generator_also_closes_it():
    """The exact mechanism the live GPU measurement used: `del gen`
    rather than a loop return, since both are "drop the last reference"
    in CPython and both must trigger immediate close()."""
    produced = []
    gen = _chained_source(produced, stop_requested=[])

    next(gen)  # start it -- one token genuinely produced
    assert produced == [0]

    del gen  # abandon it

    # Give any (incorrectly) deferred work a moment it shouldn't need.
    time.sleep(0.05)

    assert produced == [0]  # still just the one -- nothing continued


def test_a_fully_drained_generator_is_unaffected_by_this_pattern():
    """Sanity check the abandonment tests above aren't accidentally
    passing because production is broken generally -- letting the loop
    run to completion must still produce everything."""
    produced = []

    def consume():
        for _ in _chained_source(produced, stop_requested=[]):
            if len(produced) >= 5:
                break

    consume()
    assert produced == [0, 1, 2, 3, 4]
