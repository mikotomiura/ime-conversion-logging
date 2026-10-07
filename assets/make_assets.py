"""Generates the figures of README.md as SVG: the logo, the diagram of the validation and the diagram of what the
logger writes, each in a light and a dark version, and the animated illustration of what the logger writes while a
phrase is typed and converted. Run from the root of the repository:

    python3 assets/make_assets.py --out assets

With --frame T the illustration is written as a still at T seconds (the opacity of every element is fixed), for
checking a moment of the animation. The rows of the illustration follow the format obs/1 (kit/formats/FORMATS.md);
their times are made up."""
import argparse
import html
import os
import re
import sys

SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"
JP = ("'Hiragino Sans', 'Hiragino Kaku Gothic ProN', 'Yu Gothic', 'Noto Sans CJK JP', 'Noto Sans JP', Meiryo, "
      "sans-serif")
THEMES = {
    "light": dict(text="#1f2328", muted="#59636e", accent="#0969da", cap="#ddf4ff", face="#ffffff",
                  caret="#bf8700", panel="#f6f8fa", border="#d0d7de", ok="#1a7f37", okbg="#dafbe1",
                  no="#cf222e", nobg="#ffebe9"),
    "dark": dict(text="#f0f6fc", muted="#9198a1", accent="#4493f8", cap="#0c2d6b", face="#0d1117",
                 caret="#d29922", panel="#161b22", border="#30363d", ok="#3fb950", okbg="#033a16",
                 no="#f85149", nobg="#3c1618"),
}


def logo(theme):
    c = THEMES[theme]
    cell = ('  <rect x="%d" y="58" width="24" height="28" rx="3" fill="{accent}" fill-opacity="0.12" '
            'stroke="{accent}" stroke-width="3"/>')
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 180" width="720" height="180" role="img" '
        'aria-labelledby="t d">',
        '  <title id="t">ime-conversion-logging</title>',
        '  <desc id="d">A key cap holding three empty character cells over the dotted underline of an input-method '
        'composition, and the name of the repository: the logger records the shape and timing of a conversion, not '
        'its text.</desc>',
        '  <rect x="20" y="20" width="140" height="140" rx="30" fill="{cap}" stroke="{accent}" stroke-width="4"/>',
        '  <rect x="34" y="30" width="112" height="106" rx="20" fill="{face}" stroke="{accent}" stroke-width="2" '
        'stroke-opacity="0.45"/>',
        cell % 46, cell % 76, cell % 106,
    ]
    lines += ['  <circle cx="%d" cy="103" r="2.6" fill="{accent}"/>' % x for x in range(49, 131, 9)]
    lines += [
        '  <line x1="139" y1="52" x2="139" y2="94" stroke="{caret}" stroke-width="4" stroke-linecap="round"/>',
        '  <text x="190" y="90" font-family="%s" font-size="38" font-weight="600" fill="{text}">'
        'ime-conversion-logging</text>' % MONO,
        '  <text x="191" y="128" font-family="%s" font-size="19" fill="{muted}">'
        'Logging Japanese IME conversion without recording text</text>' % SANS,
        '</svg>',
    ]
    return "\n".join(lines).format(**c) + "\n"


def _frame(c, w, h, title, desc, body):
    head = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img" '
        'aria-labelledby="t d">' % (w, h, w, h),
        '  <title id="t">%s</title>' % html.escape(title, quote=False),
        '  <desc id="d">%s</desc>' % html.escape(desc, quote=False),
        '  <rect x="0.5" y="0.5" width="%d" height="%d" rx="12" fill="%s" stroke="%s"/>'
        % (w - 1, h - 1, c["face"], c["border"]),
    ]
    return "\n".join(head + body + ["</svg>"]) + "\n"


def _text(x, y, s, size, fill, weight=None, family=SANS, anchor=None):
    return ('  <text x="%s" y="%s" font-family="%s" font-size="%s" fill="%s"%s%s>%s</text>'
            % (x, y, family, size, fill, ' font-weight="%s"' % weight if weight else "",
               ' text-anchor="%s"' % anchor if anchor else "", html.escape(s, quote=False)))


def _card(c, x, y, w, h, head, lines, bar=None):
    """A rounded box with a coloured bar on top, a heading and lines of text."""
    out = ['  <rect x="%d" y="%d" width="%d" height="%d" rx="10" fill="%s" stroke="%s"/>'
           % (x, y, w, h, c["panel"], c["border"]),
           '  <rect x="%d" y="%d" width="%d" height="4" rx="2" fill="%s"/>' % (x + 14, y + 10, 36, bar or c["accent"]),
           _text(x + 14, y + 36, head, 14, c["text"], 600)]
    out += [_text(x + 14, y + 60 + 18 * i, ln, 12.5, c["muted"]) for i, ln in enumerate(lines)]
    return out


def _arrow(c, pts, dashed=False):
    """A line through the points, with a head at the last one; the last segment points to the right."""
    x2, y2 = pts[-1]
    line = list(pts[:-1]) + [(x2 - 8, y2)]
    return ['  <polyline points="%s" fill="none" stroke="%s" stroke-width="2"%s/>'
            % (" ".join("%d,%d" % p for p in line), c["muted"], ' stroke-dasharray="4 4"' if dashed else ""),
            '  <polygon points="%d,%d %d,%d %d,%d" fill="%s"/>' % (x2, y2, x2 - 9, y2 - 5, x2 - 9, y2 + 5, c["muted"])]


def validation(theme):
    c = THEMES[theme]
    body = [_text(24, 40, "How the logger was validated", 16, c["text"], 600),
            _text(24, 62, "Synthetic input in a pre-registered study. The logger and a reference input field record "
                  "the same runs.", 12.5, c["muted"])]
    body += _card(c, 24, 140, 150, 116, "Plan", ["Generated from the", "phrase corpus and", "the settings with a seed"])
    body += _card(c, 204, 140, 158, 116, "Executor",
                  ["Sends every key press", "and release, and records", "what it sent"])
    body += _card(c, 394, 80, 170, 100, "Input method", ["macOS Japanese input", "or Microsoft IME"])
    body += _card(c, 596, 80, 170, 100, "Reference field", ["Records the calls it", "receives. No text."])
    body += _card(c, 394, 206, 372, 110, "Logger", ["Key categories, times and one row per conversion session.",
                                                    "macOS: sessions inferred from the key presses.",
                                                    "Windows: while a window of the input method is visible."])
    body += _card(c, 798, 124, 138, 150, "Matching", ["By wall-clock time,", "by registered rules:",
                                                      "detection, duration,", "arrow-key presses,", "abandonment"],
                  bar=c["ok"])
    body += _arrow(c, [(174, 198), (204, 198)])
    body += _arrow(c, [(362, 180), (378, 180), (378, 130), (394, 130)])
    body += _arrow(c, [(362, 216), (378, 216), (378, 261), (394, 261)])
    body += _arrow(c, [(564, 130), (596, 130)])
    body += _arrow(c, [(766, 130), (782, 130), (782, 170), (798, 170)])
    body += _arrow(c, [(766, 261), (782, 261), (782, 228), (798, 228)])
    body.append('  <line x1="479" y1="180" x2="479" y2="206" stroke="%s" stroke-width="2" stroke-dasharray="4 4"/>'
                % c["muted"])
    body.append(_text(487, 198, "its windows (Windows)", 11, c["muted"]))
    body += _arrow(c, [(283, 256), (283, 340), (790, 340), (790, 256), (798, 256)], dashed=True)
    body.append(_text(300, 334, "the executor's record: the trials and the presses sent", 11, c["muted"]))
    return _frame(c, 960, 360, "How the logger was validated",
                  "A plan generated from the phrase corpus and the settings drives an executor that sends every key "
                  "press and release and records what it sent. The presses reach the input method and a reference "
                  "input field, which records the calls it receives without text, and the logger, which writes key "
                  "categories, times and a row per conversion session that it detects (on macOS inferred from the key "
                  "presses, on Windows while a window of the input method is visible). The sessions of the reference "
                  "and the rows of the logger are matched by wall-clock time under the registered rules, with the "
                  "trials and the presses sent taken from the executor's record: detection, duration, arrow-key "
                  "presses and abandonment.", body)


CATEGORIES = ["alphabetic", "numeric", "punctuation", "sentence_punctuation", "tab", "backspace", "delete", "space",
              "enter", "navigation", "modifier", "function", "escape", "other"]


def formats_categories():
    """The key categories that kit/formats/FORMATS.md lists for the key row of obs/1."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit", "formats", "FORMATS.md")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"`category` is one of(.*?)\n- ", text, re.S)
    return re.findall(r"`([a-z_]+)`", m.group(1)) if m else []


def records(theme):
    c = THEMES[theme]
    body = [_text(24, 40, "What the logger writes, and what it never writes", 16, c["text"], 600)]
    body.append('  <rect x="24" y="60" width="580" height="222" rx="10" fill="%s" stroke="%s"/>'
                % (c["panel"], c["border"]))
    body.append('  <rect x="38" y="70" width="36" height="4" rx="2" fill="%s"/>' % c["ok"])
    body.append(_text(38, 96, "Written to the log (format obs/1)", 14, c["ok"], 600))
    body.append(_text(38, 122, "Every press and release of an observed key", 13, c["text"], 600))
    body.append(_text(330, 122, "one of 14 categories, the time", 12.5, c["muted"]))
    x, y = 38, 136
    for cat in CATEGORIES:
        w = int(len(cat) * 7.2 + 14)
        if x + w > 590:
            x, y = 38, y + 28
        body.append('  <rect x="%d" y="%d" width="%d" height="22" rx="11" fill="%s" stroke="%s"/>'
                    % (x, y, w, c["okbg"], c["ok"]))
        body.append(_text(x + w / 2, y + 15, cat, 11.5, c["text"], family=MONO, anchor="middle"))
        x += w + 6
    body.append(_text(38, 222, "Each detected session, when it ends", 13, c["text"], 600))
    body.append(_text(290, 222, "(a session cut off, as at 20 s, writes no row)", 12.5, c["muted"]))
    body.append(_text(38, 242, "its duration (dwell_ms), the arrow-key presses during it (nav)", 12.5, c["muted"]))
    body.append(_text(38, 260, "and whether it was abandoned (abandon)", 12.5, c["muted"]))
    body.append('  <rect x="624" y="60" width="312" height="222" rx="10" fill="%s" stroke="%s"/>'
                % (c["panel"], c["border"]))
    body.append('  <rect x="638" y="70" width="36" height="4" rx="2" fill="%s"/>' % c["no"])
    body.append(_text(638, 96, "Never written", 14, c["no"], 600))
    for i, item in enumerate(["Key names and key codes", "Characters and readings", "Candidate strings",
                              "Window names"]):
        yy = 130 + 34 * i
        body.append('  <rect x="638" y="%d" width="296" height="26" rx="6" fill="%s"/>' % (yy - 18, c["nobg"]))
        body.append('  <line x1="648" y1="%d" x2="660" y2="%d" stroke="%s" stroke-width="2"/>'
                    % (yy - 5, yy - 5, c["no"]))
        body.append(_text(670, yy, item, 13, c["text"]))
    body.append(_text(24, 310, "Some categories, such as backspace or enter, stand for a single key, and the key rows "
                      "are not anonymous by construction:", 12.5, c["muted"]))
    body.append(_text(24, 328, "the article measures how far they reveal the typed phrase.", 12.5, c["muted"]))
    return _frame(c, 960, 346, "What the logger writes, and what it never writes",
                  "Written to the log in the format obs/1: for every press and release of an observed key, one of "
                  "fourteen categories, the time and whether it was a press; when a conversion session that the logger "
                  "detects ends, its duration, the arrow-key presses during it and whether it was abandoned (a session "
                  "cut off, for example at 20 s, writes no row). Never written: key names and key codes, "
                  "characters and readings, candidate strings and window names. Some categories stand for a single "
                  "key, and the key rows are not anonymous by construction; the article measures how far they "
                  "reveal the typed phrase.", body)


CYCLE = 12.0
FADE_AT, GONE_AT = 10.5, 11.0
KEYS = [  # label, seconds, category, offset in ms in the log
    ("n", 1.0, "alphabetic", 0), ("i", 1.3, "alphabetic", 300), ("h", 1.6, "alphabetic", 600),
    ("o", 1.9, "alphabetic", 900), ("n", 2.2, "alphabetic", 1200), ("g", 2.5, "alphabetic", 1500),
    ("o", 2.8, "alphabetic", 1800), ("Space", 3.8, "space", 2800), ("Enter", 5.0, "enter", 4000),
]
T0 = 1791230400000
SHADOW_AT, SHADOW_LAG = 5.1, 16
STATES = [  # text shown in the field, from, to, kind
    ("n", 1.0, 1.3, "comp"), ("に", 1.3, 1.6, "comp"), ("にh", 1.6, 1.9, "comp"), ("にほ", 1.9, 2.2, "comp"),
    ("にほn", 2.2, 2.5, "comp"), ("にほんg", 2.5, 2.8, "comp"), ("にほんご", 2.8, 3.8, "comp"),
    ("日本語", 3.8, 5.0, "conv"), ("日本語", 5.0, GONE_AT, "done"),
]
MUTED_TEXT = '<text x="%d" y="%d" font-family="%s" font-size="12.5" fill="#9198a1">%s</text>'
CARET = '<line x1="%d" y1="128" x2="%d" y2="164" stroke="#d29922" stroke-width="2"%s/>'
LOG_ROW = '<text x="386" y="%d" font-family="%s" font-size="11" xml:space="preserve"%s>%s</text>'


def pct(s):
    return "%.3f%%" % (s / CYCLE * 100)


def width(text):
    return sum(19 if ord(ch) < 128 else 34 for ch in text)


def demo(frame=None):
    css, body = [], []
    n = [0]

    def window(a, b, fade=False):
        """Opacity 1 in [a, b); with fade, it falls to 0 between b and GONE_AT."""
        n[0] += 1
        name = "w%d" % n[0]
        if frame is not None:
            if a <= frame < b:
                op = 1
            elif fade and b <= frame < GONE_AT:
                op = 1 - (frame - b) / (GONE_AT - b)
            else:
                op = 0
            return ' opacity="%.2f"' % op
        eps = 0.001
        end = pct(GONE_AT) if fade else pct(b + eps)
        css.append("@keyframes %s{0%%,%s{opacity:0}%s,%s{opacity:1}%s,100%%{opacity:0}}"
                   % (name, pct(a), pct(a + eps), pct(b), end))
        css.append(".%s{opacity:0;animation:%s %.1fs linear infinite}" % (name, name, CYCLE))
        return ' class="%s"' % name

    def flash(a):
        n[0] += 1
        name = "k%d" % n[0]
        if frame is not None:
            return ' fill="%s"' % ("#1f6feb" if a <= frame < a + 0.25 else "#21262d")
        css.append("@keyframes %s{0%%,%s{fill:#21262d}%s,%s{fill:#1f6feb}%s,100%%{fill:#21262d}}"
                   % (name, pct(a), pct(a + 0.001), pct(a + 0.25), pct(a + 0.251)))
        css.append(".%s{fill:#21262d;animation:%s %.1fs linear infinite}" % (name, name, CYCLE))
        return ' class="%s"' % name

    # left panel: the input field
    body.append('<text x="24" y="44" font-family="%s" font-size="16" font-weight="600" fill="#f0f6fc">'
                'What is typed, and what the logger writes</text>' % SANS)
    body.append('<rect x="24" y="68" width="330" height="292" rx="8" fill="#161b22" stroke="#30363d"/>')
    body.append('<text x="40" y="100" font-family="%s" font-size="13" fill="#9198a1">Input field</text>' % SANS)
    body.append('<rect x="40" y="114" width="298" height="60" rx="6" fill="#0d1117" stroke="#30363d"/>')
    x0, base = 52, 157
    body.append(CARET % (x0, x0, window(0, 1.0)))
    body.append(CARET % (x0, x0, window(GONE_AT, CYCLE)))
    for text, a, b, kind in STATES:
        w = width(text)
        g = ['<g%s>' % window(a, b if kind != "done" else FADE_AT, fade=(kind == "done"))]
        if kind == "conv":
            g.append('<rect x="%d" y="126" width="%d" height="40" rx="3" fill="#1f6feb" fill-opacity="0.35"/>'
                     % (x0 - 2, w + 4))
        g.append('<text x="%d" y="%d" font-family="%s" font-size="34" fill="#f0f6fc">%s</text>'
                 % (x0, base, JP if any(ord(ch) > 127 for ch in text) else MONO, text))
        if kind == "comp":
            g.append('<line x1="%d" y1="167" x2="%d" y2="167" stroke="#f0f6fc" stroke-width="2" '
                     'stroke-dasharray="2 4"/>' % (x0, x0 + w))
        elif kind == "conv":
            g.append('<line x1="%d" y1="168" x2="%d" y2="168" stroke="#f0f6fc" stroke-width="3"/>' % (x0, x0 + w))
        g.append(CARET % (x0 + w + 3, x0 + w + 3, ""))
        g.append('</g>')
        body.append("".join(g))
    # key caps
    x = 40
    for label, a, cat, off in KEYS:
        w = 22 if len(label) == 1 else (54 if label == "Space" else 50)
        body.append('<rect x="%d" y="200" width="%d" height="30" rx="5" stroke="#30363d"%s/>' % (x, w, flash(a)))
        body.append('<text x="%.1f" y="220" text-anchor="middle" font-family="%s" font-size="13" fill="#f0f6fc">'
                    '%s</text>' % (x + w / 2, MONO if len(label) == 1 else SANS, label))
        x += w + 4
    for i, line in enumerate(["Romaji typed with the input method on.", "Space converts; Enter commits."]):
        body.append(MUTED_TEXT % (40, 262 + 20 * i, SANS, line))
    body.append(MUTED_TEXT % (40, 330, SANS, "The text stays on the screen."))

    # right panel: the logger's rows
    body.append('<rect x="370" y="68" width="566" height="292" rx="8" fill="#010409" stroke="#30363d"/>')
    body.append('<text x="386" y="100" font-family="%s" font-size="13" fill="#9198a1">'
                'What the logger writes (obs/1)</text>' % SANS)
    K, S, N, B = "#9198a1", "#79c0ff", "#d2a8ff", "#ffa657"

    def row(parts):
        return "".join('<tspan fill="%s">%s</tspan>' % (col, txt.replace('"', "&quot;")) for col, txt in parts)

    y = 126
    for label, a, cat, off in KEYS:
        parts = [(K, '{"type":'), (S, '"key"'), (K, ',"t":'), (N, str(T0 + off)), (K, ',"category":'),
                 (B, '"%s"' % cat), (K, ',"press":'), (N, "true"), (K, "}")]
        body.append(LOG_ROW % (y, MONO, window(a, FADE_AT, fade=True), row(parts)))
        y += 23
    parts = [(K, '{"type":'), (S, '"ime_shadow"'), (K, ',"t":'), (N, str(T0 + 4000 + SHADOW_LAG)),
             (K, ',"dwell_ms":'), (B, "4000"), (K, ',"nav":'), (B, "0"), (K, ',"abandon":'), (B, "false"),
             (K, "}")]
    body.append(LOG_ROW % (y, MONO, window(SHADOW_AT, FADE_AT, fade=True), row(parts)))

    foot = ('An illustration with made-up times. Release rows ("press":false) are written too and are not shown. '
            'No character, reading or candidate is recorded.')
    body.append(MUTED_TEXT % (24, 392, SANS, foot.replace('"', "&quot;")))
    style = "<style>%s</style>" % "".join(css) if css else ""
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 410" width="960" height="410" role="img" '
            'aria-labelledby="t d">\n'
            '<title id="t">How the logger records a conversion</title>\n'
            '<desc id="d">Left: the romaji n, i, h, o, n, g, o typed with the input method on become the reading '
            'nihongo, Space converts it and Enter commits it. Right: the rows the logger writes at the same times, '
            'a key category and a time for every key press and, when the conversion ends, its duration, the number '
            'of arrow-key presses during it and whether it was abandoned; no character is written.</desc>\n'
            + style + "\n"
            '<rect x="0.5" y="0.5" width="959" height="409" rx="12" fill="#0d1117" stroke="#30363d"/>\n'
            + "\n".join(body) + "\n</svg>\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--frame", type=float)
    a = ap.parse_args()
    found = formats_categories()  # compared in order, so that the figure keeps the order of the specification
    if not found:
        sys.exit("make_assets: the list of key categories was not found in kit/formats/FORMATS.md; nothing was written")
    if found != CATEGORIES:
        sys.exit("make_assets: the key categories differ from those of kit/formats/FORMATS.md; nothing was written")
    os.makedirs(a.out, exist_ok=True)

    def put(name, text):
        with open(os.path.join(a.out, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    if a.frame is None:
        for theme in ("light", "dark"):
            put("logo-%s.svg" % theme, logo(theme))
            put("validation-%s.svg" % theme, validation(theme))
            put("records-%s.svg" % theme, records(theme))
        put("demo.svg", demo())
    else:
        put("demo-frame-%04.1f.svg" % a.frame, demo(a.frame))
