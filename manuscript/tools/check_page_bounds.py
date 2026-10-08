#!/usr/bin/env python3
"""Count the pages of a built PDF on which text or a rule lies outside the page, where a reader cannot see it, or
covers the page number.

LaTeX does not always warn when a float is taller than the page: a sideways table set on a page of floats can run off
both edges of the paper and still give no overfull box, and a long table can run into the foot of the page over the
page number. This script reads the PDF that the build wrote and reports every page whose text (taken without clipping
to the page) or drawn rules extend beyond the page by more than a tolerance, and every page whose number is covered by
other text or crossed by a rule. It needs PyMuPDF; when PyMuPDF is not installed, or a PDF cannot be read, it says
that the pages were not checked and exits with 3, so that a missing tool is not read as a clean result.

usage: check_page_bounds.py <pdf> [<pdf> ...]
       check_page_bounds.py --selftest
"""
import sys

TOLERANCE = 1.0  # points; a glyph or a rule may touch the edge of the page


def outside(page, fitz):
    """The parts of a page that lie outside it: (kind, rectangle, text) for spans of text and for drawn paths. Text
    and paths come in the coordinates of the unrotated page with the origin at the corner of the crop box, so they
    are compared with a rectangle of the crop box's size at the origin, not with page.rect, which a landscape page
    (pdflscape) turns, nor with page.cropbox, which keeps the box's offset."""
    box = page.cropbox
    area = fitz.Rect(0, 0, box.width, box.height) + (-TOLERANCE, -TOLERANCE, TOLERANCE, TOLERANCE)
    found = []
    for block in page.get_text("dict", clip=fitz.INFINITE_RECT())["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                rect = fitz.Rect(span["bbox"])
                if span["text"].strip() and not area.contains(rect):
                    found.append(("text", rect, span["text"]))
    for path in page.get_drawings():
        rect = path["rect"]
        if not area.contains(rect):
            found.append(("rule", rect, ""))
    return found


def _segments(path, fitz):
    """The straight pieces of a drawn path: lines as they are, rectangles and quads by their edges, a curve by the
    chord and the two control legs."""
    for item in path["items"]:
        kind = item[0]
        if kind == "l":
            yield item[1], item[2]
        elif kind == "c":
            yield item[1], item[2]
            yield item[3], item[4]
            yield item[1], item[4]
        elif kind in ("re", "qu"):
            q = fitz.Rect(item[1]).quad if kind == "re" else item[1]
            corners = [q.ul, q.ur, q.lr, q.ll]
            for k in range(4):
                yield corners[k], corners[(k + 1) % 4]


def _crosses(a, b, rect):
    """Whether the segment from a to b passes through rect (sampled at a tenth of a point)."""
    steps = max(1, int(max(abs(b.x - a.x), abs(b.y - a.y)) * 10))
    return any(rect.contains((a.x + (b.x - a.x) * k / steps, a.y + (b.y - a.y) * k / steps)) for k in range(steps + 1))


def over_number(page, number, fitz):
    """Text or rules that cover the page number: (kind, rectangle, text), or None when no span holds the number. The
    page number is a span whose text is the number alone; another span covers it when it overlaps at least a quarter
    of the span's area, a filled path when its fill does, and a stroked path when one of its lines passes through it.
    A long table that runs into the foot of the page prints over the number while staying on the paper."""
    spans = [(fitz.Rect(s["bbox"]), s["text"])
             for b in page.get_text("dict", clip=fitz.INFINITE_RECT())["blocks"] for l in b.get("lines", [])
             for s in l["spans"] if s["text"].strip()]
    paths = page.get_drawings()
    numbers = [i for i, (rect, text) in enumerate(spans) if text.strip() == str(number) and not rect.is_empty]
    if not numbers:
        return None
    found = []
    for i in numbers:
        rect = spans[i][0]
        least = 0.25 * rect.get_area()
        for j, (other, t) in enumerate(spans):
            if j != i and (other & rect).get_area() >= least:
                found.append(("text", other, t))
        for path in paths:
            if path.get("fill") is not None and (fitz.Rect(path["rect"]) & rect).get_area() >= least:
                found.append(("fill", path["rect"], ""))
            elif path.get("color") is not None and any(
                    _crosses(a, b, rect + (-0.5, -0.5, 0.5, 0.5)) for a, b in _segments(path, fitz)):
                found.append(("rule", path["rect"], ""))
    return found


def check(path, fitz):
    """Print one line per page with content outside it or over its number; return the numbers of such pages and of
    pages whose number (taken to be the page's position in the file) was not found, which are not checked for it."""
    bad = covered = unnumbered = 0
    with fitz.open(path) as doc:
        for number, page in enumerate(doc, 1):
            cover = over_number(page, number, fitz)
            if cover is None:
                unnumbered += 1
            for label, parts in (("outside the page", outside(page, fitz)), ("over the page number", cover or [])):
                if not parts:
                    continue
                if label == "outside the page":
                    bad += 1
                else:
                    covered += 1
                kind, rect, text = parts[0]
                # ascii() keeps a dash or a ligature of the text from stopping the print on a narrow console encoding.
                print("%s: page %d: %d parts %s, the first a %s at %s %s" % (
                    path, number, len(parts), label, kind, [round(v) for v in rect], ascii(text[:40])))
    return bad, covered, unnumbered


def selftest(fitz):
    """A page with text inside it passes; text or a rule past an edge is found; a page with none is clean; a tall
    page turned to landscape with its content inside passes, and with content past its long edge is found; text
    wholly outside the page is found."""
    doc = fitz.open()
    inside = doc.new_page(width=200, height=200)
    inside.insert_text((20, 40), "inside")
    off_text = doc.new_page(width=200, height=200)
    off_text.insert_text((20, 40), "starts inside")
    off_text.insert_text((180, 80), "runs off the right edge")
    off_rule = doc.new_page(width=200, height=200)
    off_rule.draw_line((20, 100), (260, 100))
    doc.new_page(width=200, height=200)
    turned = doc.new_page(width=200, height=400)
    turned.insert_text((20, 380), "near the foot of the tall page")
    turned.set_rotation(90)
    turned_off = doc.new_page(width=200, height=400)
    turned_off.draw_line((20, 300), (20, 420))
    turned_off.set_rotation(90)
    # A rule inside a turned page, beyond the width the turned page would have: it passes.
    turned_rule = doc.new_page(width=200, height=400)
    turned_rule.draw_line((20, 300), (20, 390))
    turned_rule.set_rotation(90)
    # Text wholly past the edge, as a caption set beside a rotated page is: it is found only without clipping.
    off_whole = doc.new_page(width=200, height=200)
    off_whole.insert_text((-120, 60), "wholly outside")
    # A crop box whose corner is not at the origin: content inside the box passes, content past its far edge is found.
    cropped = doc.new_page(width=300, height=300)
    cropped.insert_text((70, 240), "inside the box")
    cropped.draw_line((60, 230), (240, 230))
    cropped.set_cropbox(fitz.Rect(50, 50, 250, 250))
    cropped_off = doc.new_page(width=300, height=300)
    cropped_off.insert_text((70, 100), "inside the box")
    cropped_off.draw_line((60, 120), (280, 120))
    cropped_off.set_cropbox(fitz.Rect(50, 50, 250, 250))
    results = [len(outside(p, fitz)) for p in doc]
    ok = (results[0] == 0 and results[1] >= 1 and results[2] >= 1 and results[3] == 0 and results[4] == 0
          and results[5] >= 1 and results[6] == 0 and results[7] >= 1 and results[8] == 0 and results[9] >= 1)
    # The page number: alone it passes; text printed over it or a rule across it is found; the same digit inside a
    # line of text, next to other words, is not taken for a cover.
    numbered = fitz.open()
    for _ in range(8):
        numbered.new_page(width=300, height=400)
    numbered[0].insert_text((150, 380), "1")
    numbered[0].insert_text((40, 100), "a line of text above")
    numbered[1].insert_text((150, 380), "2")
    numbered[1].insert_text((140, 381), "text printed over it")
    numbered[2].insert_text((150, 380), "3")
    numbered[2].draw_line((100, 377), (200, 377))
    numbered[3].insert_text((40, 100), "in 4 of the cases")  # the digit is a word of a line, not a span of its own
    numbered[4].insert_text((150, 380), "5")
    numbered[4].insert_text((152, 300), "text well above the number")
    # A frame drawn around the number touches none of it; a filled box over it covers it.
    numbered[5].insert_text((150, 380), "6")
    numbered[5].draw_rect(fitz.Rect(140, 366, 166, 388))
    numbered[6].insert_text((150, 380), "7")
    numbered[6].draw_rect(fitz.Rect(145, 368, 162, 386), color=None, fill=(0, 0, 0))
    # Another span with the very rectangle of the number (a digit of the same width, in another colour so that it is
    # a span of its own) covers it.
    numbered[7].insert_text((150, 380), "8")
    numbered[7].insert_text((150, 380), "9", color=(1, 0, 0))
    covers = [over_number(p, n, fitz) for n, p in enumerate(numbered, 1)]
    counts = [None if c is None else len(c) for c in covers]
    ok = (ok and counts[0] == 0 and counts[1] >= 1 and counts[2] >= 1 and counts[3] is None and counts[4] == 0
          and counts[5] == 0 and counts[6] >= 1 and counts[7] >= 1)
    covers = counts
    print("selftest %s (%s; %s)" % ("OK" if ok else "FAILED", results, covers))
    return 0 if ok else 1


def main(argv):
    try:
        import fitz
    except ImportError:
        print("check_page_bounds.py: pages not checked: PyMuPDF is not installed")
        return 3
    if argv == ["--selftest"]:
        return selftest(fitz)
    if not argv:
        print(__doc__.strip().splitlines()[-2])
        return 2
    total, unchecked = 0, False
    for path in argv:
        # Every PDF gets one summary line, also when it cannot be read.
        try:
            n, covered, unnumbered = check(path, fitz)
        except Exception as e:  # a missing or unreadable PDF, or an older PyMuPDF
            print("%s: pages not checked: %s" % (path, type(e).__name__))
            unchecked = True
            continue
        print("%s: %d pages with content outside the page, %d pages with content over the page number, %d pages "
              "whose number was not found (not checked for it)" % (path, n, covered, unnumbered))
        total += n + covered
    return 3 if unchecked else (1 if total else 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
