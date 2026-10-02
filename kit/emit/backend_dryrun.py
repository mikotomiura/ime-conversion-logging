"""Backend that sends nothing: it accepts every step and returns immediately.

It exists so that the emitter core, the timing, and the oracle format can be exercised without
touching the operating system. The optional ``fail_after=N`` option makes the N-th call to
``send`` raise, which the core's self-test uses to confirm that ``close`` runs after a failure.
"""
import string

SUPPORTED_KEYS = frozenset(
    list(string.ascii_lowercase)
    + list(string.digits)
    + ["f%d" % n for n in range(13, 20)]
    + ["space", "enter", "esc", "backspace", "tab", "left", "right", "up", "down",
       "minus", "comma", "period", "ime_on", "ime_off", "shift",
       "focus_away", "focus_back"]
)


class Backend(object):
    def __init__(self, opts):
        unknown = sorted(set(opts) - {"fail_after"})
        if unknown:
            raise ValueError("unknown backend option(s) for dryrun: %s" % ", ".join(unknown))
        self._fail_after = int(opts["fail_after"]) if "fail_after" in opts else None
        self.sends = 0
        self.opened = 0
        self.closed = 0

    def open(self):
        self.opened += 1

    def send(self, key, down):
        self.sends += 1
        if self._fail_after is not None and self.sends >= self._fail_after:
            raise RuntimeError("injected failure at send %d" % self.sends)

    def close(self):
        self.closed += 1
