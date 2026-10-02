#!/usr/bin/env python3
# kit: selftest
"""Character n-gram language model over the kana alphabet (Python standard library only).

  charlm.py --selftest     closed-form values, normalisation, ordering properties, sampling (fast)

  CharLM(n, smoothing).fit(texts)   estimate the model from a list of kana strings
  lm.logp(context, ch)              natural-log probability of `ch` after `context`
  lm.logprobs(context)              the whole conditional distribution {symbol: log-probability} (cached)
  lm.text_logp(text)                log-probability of a whole string, including the end symbol
  lm.sample_prior(length, seed)     a string of exactly `length` characters drawn from the model
  lm.sample_length(seed)            a length drawn from the empirical distribution of the training strings

Alphabet. The closed vocabulary is the kana table of the stimulus generator (`romaji.ALPHABET`) plus one
end-of-text symbol. A start symbol pads the left context. Both are control characters that cannot occur in a
text. A training string containing any other character is rejected, because the base distribution of the
smoothing methods is uniform over the closed vocabulary.

Smoothing.
  "wb"   interpolated Witten-Bell: the order-k estimate is (c(h, x) + T(h) p_(k-1)(x)) / (c(h) + T(h)), where
         c(h) is the count of the context h and T(h) the number of distinct symbols seen after h. A context
         never seen in training backs off to the lower order unchanged. The order-0 base is uniform.
  "add"  additive (Lidstone) smoothing on the full-order context only: (c(h, x) + alpha) / (c(h) + alpha |V|).
         An unseen context gives the uniform distribution.

Randomness uses only the `random()` method of a private `random.Random(seed)` instance; the global random
state is neither read nor written.

References (author, year, title only):
  - Witten and Bell (1991) The zero-frequency problem: estimating the probabilities of novel events in
    adaptive text compression
  - Lidstone (1920) Note on the general case of the Bayes-Laplace formula for inductive or a posteriori
    probabilities
"""
import argparse
import bisect
import math
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_GEN = os.path.join(os.path.dirname(_HERE), "gen")
if _GEN not in sys.path:
    sys.path.insert(0, _GEN)
import romaji  # noqa: E402

BOS = chr(2)   # start-of-text padding symbol (never predicted)
EOS = chr(3)   # end-of-text symbol (predicted)
SMOOTHINGS = ("wb", "add")
VOCAB = tuple(romaji.ALPHABET) + (EOS,)
_VOCAB_SET = frozenset(VOCAB)
_SAMPLE_CHARS = tuple(c for c in VOCAB if c != EOS)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


class CharLM(object):
    """Character n-gram model. `n` is the order (n = 1 is the unigram model)."""

    def __init__(self, n=3, smoothing="wb", alpha=0.1):
        if not _is_int(n) or n < 1:
            raise ValueError("n must be an integer >= 1")
        if smoothing not in SMOOTHINGS:
            raise ValueError("smoothing must be one of %s" % ", ".join(SMOOTHINGS))
        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not math.isfinite(alpha) or alpha <= 0:
            raise ValueError("alpha must be a positive finite number")
        self.n = n
        self.smoothing = smoothing
        self.alpha = float(alpha)
        self._reset()

    def _reset(self):
        # counts[k][context of k characters] = {symbol: count}
        self._counts = [dict() for _ in range(self.n)]
        self._cache = {}
        self.lengths = []
        self.n_texts = 0

    def fit(self, texts):
        """Estimate from an iterable of kana strings (previous training is discarded). Returns self."""
        if isinstance(texts, str):
            raise ValueError("texts must be an iterable of strings, not a single string")
        self._reset()
        pad = BOS * (self.n - 1)
        for text in texts:
            if not isinstance(text, str):
                raise ValueError("every training text must be a string")
            for ch in text:
                if ch not in _VOCAB_SET or ch == EOS:
                    raise ValueError("training text contains a character outside the kana alphabet")
            padded = pad + text + EOS
            for i in range(self.n - 1, len(padded)):
                ch = padded[i]
                for k in range(self.n):
                    bucket = self._counts[k].setdefault(padded[i - k:i], {})
                    bucket[ch] = bucket.get(ch, 0) + 1
            self.lengths.append(len(text))
            self.n_texts += 1
        self.lengths.sort()
        return self

    def _tail(self, context):
        m = self.n - 1
        if m == 0:
            return ""
        if len(context) >= m:
            return context[-m:]
        return BOS * (m - len(context)) + context

    def _compute(self, tail):
        v = len(VOCAB)
        if self.smoothing == "add":
            bucket = self._counts[self.n - 1].get(tail, {})
            den = sum(bucket.values()) + self.alpha * v
            return dict((ch, math.log((bucket.get(ch, 0) + self.alpha) / den)) for ch in VOCAB)
        probs = dict((ch, 1.0 / v) for ch in VOCAB)
        for k in range(self.n):
            bucket = self._counts[k].get(tail[len(tail) - k:] if k else "")
            if not bucket:
                continue
            total = sum(bucket.values())
            types = len(bucket)
            probs = dict((ch, (bucket.get(ch, 0) + types * probs[ch]) / (total + types)) for ch in VOCAB)
        return dict((ch, math.log(p)) for ch, p in probs.items())

    def logprobs(self, context):
        """{symbol: natural-log probability} for every symbol of the vocabulary (the end symbol included).
        Only the last n - 1 characters of `context` matter. The returned dict is shared: do not modify it."""
        tail = self._tail(context)
        dist = self._cache.get(tail)
        if dist is None:
            dist = self._compute(tail)
            self._cache[tail] = dist
        return dist

    def logp(self, context, ch):
        """Natural-log probability of `ch` (a kana, or the end symbol `charlm.EOS`) after `context`."""
        if ch not in _VOCAB_SET:
            raise ValueError("symbol outside the vocabulary")
        return self.logprobs(context)[ch]

    def text_logp(self, text):
        """Log-probability of the whole string followed by the end symbol."""
        total = 0.0
        for i, ch in enumerate(text):
            total += self.logp(text[:i], ch)
        return total + self.logp(text, EOS)

    def sample_prior(self, length, seed):
        """A string of exactly `length` characters drawn from the model (the end symbol is excluded and the
        remaining probabilities are renormalised at each step)."""
        if not _is_int(length) or length < 0:
            raise ValueError("length must be an integer >= 0")
        if not _is_int(seed):
            raise ValueError("seed must be an integer")
        rng = random.Random(seed)
        out = []
        for _ in range(length):
            dist = self.logprobs("".join(out))
            cum = []
            acc = 0.0
            for ch in _SAMPLE_CHARS:
                acc += math.exp(dist[ch])
                cum.append(acc)
            idx = bisect.bisect_right(cum, rng.random() * acc)
            out.append(_SAMPLE_CHARS[min(idx, len(_SAMPLE_CHARS) - 1)])
        return "".join(out)

    def sample_length(self, seed):
        """A length drawn from the empirical distribution of the training strings' lengths."""
        if not _is_int(seed):
            raise ValueError("seed must be an integer")
        if not self.lengths:
            raise ValueError("the model has no training strings")
        rng = random.Random(seed)
        return self.lengths[min(len(self.lengths) - 1, int(rng.random() * len(self.lengths)))]


# ---------------------------------------------------------------- selftest

def selftest():
    fails = []
    count = [0]

    def check(name, cond, detail=""):
        count[0] += 1
        if not cond:
            fails.append("%s %s" % (name, detail))

    def near(name, got, want, tol=1e-9):
        check(name, got is not None and abs(got - want) <= tol, "got %r want %r" % (got, want))

    def raises(name, fn):
        count[0] += 1
        try:
            fn()
        except ValueError:
            return
        except Exception as e:  # a different exception is not a refusal
            fails.append("%s different exception: %r" % (name, e))
            return
        fails.append("%s no exception" % name)

    v = len(VOCAB)
    p = lambda lm, ctx, ch: math.exp(lm.logp(ctx, ch))

    # closed forms, Witten-Bell. Unigram on `ああ`: counts `あ`:2, EOS:1 (N = 3, T = 2, uniform base 1/V).
    lm = CharLM(1, "wb").fit(["ああ"])
    near("WB unigram, seen symbol", p(lm, "", "あ"), (2 + 2.0 / v) / 5.0)
    near("WB unigram, end symbol", p(lm, "", EOS), (1 + 2.0 / v) / 5.0)
    near("WB unigram, unseen symbol", p(lm, "", "い"), (2.0 / v) / 5.0)
    # Bigram on `あい`: unigram counts `あ`, `い`, EOS (N = 3, T = 3); context `あ` -> {`い`: 1}.
    lm = CharLM(2, "wb").fit(["あい"])
    p0 = (1 + 3.0 / v) / 6.0
    near("WB bigram, seen continuation", p(lm, "あ", "い"), (1 + 1 * p0) / 2.0)
    near("WB bigram, unseen context backs off unchanged", p(lm, "う", "い"), p0)
    near("WB bigram, start context", p(lm, "", "あ"), (1 + 1 * ((1 + 3.0 / v) / 6.0)) / 2.0)
    # closed forms, additive
    lm = CharLM(1, "add", alpha=0.5).fit(["ああ"])
    near("add unigram", p(lm, "", "あ"), (2 + 0.5) / (3 + 0.5 * v))
    lm = CharLM(2, "add", alpha=0.5).fit(["ああ"])
    near("add bigram, seen context", p(lm, "あ", "あ"), (1 + 0.5) / (2 + 0.5 * v))
    near("add bigram, unseen context is uniform", p(lm, "い", "う"), 1.0 / v)
    check("an untrained model is uniform", abs(p(CharLM(3, "wb"), "あい", "う") - 1.0 / v) < 1e-12)

    # every conditional distribution sums to one (seen, unseen and short contexts; both smoothings)
    corpus = ["きょうはいいてんきです", "あしたはがっこうです", "らーめんをたべる", "ふぁいるをひらく"]
    for smoothing in SMOOTHINGS:
        for n in (1, 2, 3, 4):
            lm = CharLM(n, smoothing).fit(corpus)
            worst = 0.0
            for ctx in ("", "き", "きょ", "きょう", "ん", "ゔゔゔ", "てんき", "っ"):
                worst = max(worst, abs(sum(math.exp(x) for x in lm.logprobs(ctx).values()) - 1.0))
            check("distribution sums to one (%s, n=%d)" % (smoothing, n), worst < 1e-9, "off by %g" % worst)

    # the model learns: a higher order explains held-out text of the same kind better, and word order matters
    train = ["きょうはいいてんきです", "きょうはがっこうです", "あしたはいいてんきです", "あしたはがっこうです"] * 3
    held = "きょうはあしたです"
    uni, tri = CharLM(1, "wb").fit(train), CharLM(3, "wb").fit(train)
    check("trigram beats unigram on held-out text of the same kind", tri.text_logp(held) > uni.text_logp(held),
          "%r %r" % (tri.text_logp(held), uni.text_logp(held)))
    check("trigram prefers the real order to a scrambled order",
          tri.text_logp("きょうはいいてんきです") > tri.text_logp("すでんてきいいはうょき"))
    check("unigram cannot tell the order", abs(uni.text_logp("きょうは") - uni.text_logp("はうょき")) < 1e-9)

    # sampling
    lm = CharLM(1, "wb").fit(["あ" * 80 + "い" * 20])
    a = lm.sample_prior(40, 7)
    check("sample has the requested length", len(a) == 40)
    check("sample uses the vocabulary only", all(c in _VOCAB_SET and c not in (BOS, EOS) for c in a))
    check("same seed gives the same sample", a == lm.sample_prior(40, 7))
    check("a different seed gives a different sample", a != lm.sample_prior(40, 8))
    check("length 0 gives the empty string", lm.sample_prior(0, 1) == "")
    g0 = random.getstate()
    lm.sample_prior(30, 3)
    lm.sample_length(3)
    check("sampling leaves the global random state alone", random.getstate() == g0)
    big = lm.sample_prior(4000, 11)
    p_a = (80 + 3.0 / v) / 104.0
    p_end = (1 + 3.0 / v) / 104.0
    want = p_a / (1.0 - p_end)
    got = big.count("あ") / 4000.0
    check("sample frequency matches the model (renormalised without the end symbol)", abs(got - want) < 0.03,
          "got %.4f want %.4f" % (got, want))
    # a trigram model reproduces its training text when trained on a single string
    one = CharLM(3, "wb").fit(["がっこうにいく"] * 5)
    check("a trigram sample of a single training string is that string", one.sample_prior(7, 5) == "がっこうにいく")
    fit_lens = CharLM(1, "wb").fit(["あ", "ああ", "あああ", "あああ"])
    check("training lengths are recorded sorted", fit_lens.lengths == [1, 2, 3, 3])
    draws = set(fit_lens.sample_length(s) for s in range(60))
    check("sampled lengths come from the training lengths", draws <= {1, 2, 3} and len(draws) >= 2, repr(draws))
    check("same seed gives the same length", fit_lens.sample_length(4) == fit_lens.sample_length(4))

    # refusals
    raises("n = 0", lambda: CharLM(0))
    raises("n is not an integer", lambda: CharLM(2.5))
    raises("unknown smoothing", lambda: CharLM(2, "kn"))
    raises("alpha = 0", lambda: CharLM(2, "add", alpha=0))
    raises("alpha is nan", lambda: CharLM(2, "add", alpha=float("nan")))
    raises("a Latin letter in a training text", lambda: CharLM(2).fit(["aい"]))
    raises("a kanji in a training text", lambda: CharLM(2).fit(["漢"]))
    raises("a control symbol in a training text", lambda: CharLM(2).fit([BOS]))
    raises("a single string instead of a list", lambda: CharLM(2).fit("あい"))
    raises("a non-string text", lambda: CharLM(2).fit([1]))
    raises("logp of a Latin letter", lambda: CharLM(2).logp("", "a"))
    raises("logp of the start symbol", lambda: CharLM(2).logp("", BOS))
    raises("negative sample length", lambda: CharLM(2).sample_prior(-1, 1))
    raises("seed is not an integer", lambda: CharLM(2).sample_prior(3, "x"))
    raises("sample_length without training strings", lambda: CharLM(2).sample_length(1))
    check("fit returns the model and replaces earlier training", CharLM(2).fit(["あ"]).fit(["い"]).n_texts == 1)

    if fails:
        for f in fails:
            print("NG: " + f)
        print("selftest failed: %d of %d" % (len(fails), count[0]))
        return 1
    print("selftest OK (%d checks)" % count[0])
    return 0


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Character n-gram language model (library; only --selftest runs standalone)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    ap.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
