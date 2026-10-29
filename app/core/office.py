"""Word, PowerPoint and Excel files, made on this PC (2026-10-24, the owner: "making the docs too like words and ppt and other stuff";
docs/adr/0005). A model writes the content as Markdown (executor._office); this module builds the document through Office's own
automation (COM, pywin32) in a window the owner sees, block by block, then saves it and counts what it holds.

- `outline(text)`: the Markdown as blocks: title, headings, paragraphs, bullets (with their level), numbered items, tables, notes.
- `slides(blocks)`: those blocks as a deck: the title slide, then a slide per heading with its points, or its table, and notes.
- `target(folder, title, ext)`: a new file's path, never one that exists ("BharatGen (2).pptx").
- `with_pictures(blocks, pics)`: photos spread through a document, each with its caption (2026-10-26: "a doc … with images").
- `build(kind, blocks, path, visible=True, cursor=None)`: the file made in Word, PowerPoint or Excel and saved -> {"count", "unit"}: a
  document's pages (Word's own count), a deck's slides, a sheet's rows.
- `available(kind)`: whether this PC's Office can make it, and if not, why.
Self-check: python -m core.office (the parsing) · python -m core.office --live (each app hidden; files in data/tmp, deleted after)
"""
import os
import re
import sys
import time
from pathlib import Path

KINDS = {"word": (".docx", "Word", "Word.Application"), "pdf": (".pdf", "Word", "Word.Application"),
         "powerpoint": (".pptx", "PowerPoint", "PowerPoint.Application"), "excel": (".xlsx", "Excel", "Excel.Application")}
PACE_S = 0.12        # between a document's blocks while the owner watches: it is written in front of them, not dropped in at once
SLIDE_PACE_S = 0.35  # between slides
TABLE_ROWS = 10      # a slide's table rows below its header; more go on the next slide
NAME_MAX = 80
WORD_STYLES = {"title": -63, "h1": -2, "h2": -3, "h3": -4, "para": -1, "number": -50, "caption": -35}  # wdStyleTitle, Heading 1-3, Normal,
#                                                                                                     List Number, Caption
PICTURE_W, PICTURE_H = 4.5 * 72, 3.4 * 72  # a photo's largest size on a page, in points: most of a page's width, a third of its height
BULLETS = (-49, -55, -56, -57)  # wdStyleListBullet, List Bullet 2, 3, 4
WORD_TABLE_STYLE = "Grid Table 4 - Accent 1"
SAVE_AS = {".docx": 16, ".pdf": 17, ".pptx": 24, ".xlsx": 51}  # wdFormatDocumentDefault, wdFormatPDF, ppSaveAsOpenXMLPresentation, xlOpenXMLWorkbook
THEMES = ("Integral.thmx",)  # PowerPoint's own theme for a new deck, the first one found in Office's theme folders


class OfficeError(Exception):
    pass


def inline(s):
    """A line's Markdown marks taken off, except **bold** (Word keeps it): a link as its words, code, italics, gpt-oss's 【n】 as [n]."""
    s = re.sub(r"【(\d+)[^】]*】", r"[\1]", s)
    s = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"(?<![\w*])\*(?!\*)([^*\n]+?)(?<!\*)\*(?![\w*])", r"\1", s)
    s = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"\1", s)
    return re.sub(r"\s*<br\s*/?>\s*", " ", s).strip()


def plain(s):
    return (s or "").replace("**", "")


_TABLE_RULE = re.compile(r"^\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?$")


def outline(md):
    """A model's Markdown -> [(kind, text or rows, level)]: kind is title (#), h1 (##), h2, h3, para, bullet (level 0-3), number, table
    (rows of cells), notes ("Notes: …"). Its thinking, code fences and rules are dropped."""
    blocks, para, rows = [], [], []

    def flush():
        if para:
            blocks.append(("para", inline(" ".join(para)), 0))
            para.clear()
        if rows:
            blocks.append(("table", [r[:] for r in rows], 0))
            rows.clear()
    for raw in re.sub(r"<think>.*?</think>", "", md or "", flags=re.S).splitlines():
        s = raw.strip()
        if s.startswith("|") and s.count("|") >= 2:
            if not rows:
                flush()
            if not _TABLE_RULE.match(s):
                rows.append([inline(c) for c in s.strip("|").split("|")])
            continue
        if rows or not s or s.startswith("```") or re.fullmatch(r"[-*_]{3,}", s):
            flush()
            if not s or s.startswith("```") or re.fullmatch(r"[-*_]{3,}", s):
                continue
        m = re.match(r"^(#{1,4})\s+(.+)$", s)
        if m:
            flush()
            blocks.append((("title", "h1", "h2", "h3")[len(m[1]) - 1], inline(m[2].strip("# ")), 0))
            continue
        m = re.match(r"^(?:[-*•+]\s+)?[*_>\s]*(?:speaker\s+)?notes?\s*[*_]*\s*:\s*[*_]*\s*(.+)$", s, re.I)  # (written as a point, or bold)
        if m:
            flush()
            blocks.append(("notes", inline(m[1]), 0))
            continue
        m = re.match(r"^(\s*)[-*•+–]\s+(.+)$", raw)  # (gpt-oss lists with en dashes too: "– Exact launch date …")
        if m:
            flush()
            blocks.append(("bullet", inline(m[2]), min(len(m[1].expandtabs(4)) // 2, 3)))
            continue
        m = re.match(r"^\s*\d{1,3}[.)]\s+(.+)$", raw)
        if m:
            flush()
            blocks.append(("number", inline(m[1]), 0))
            continue
        para.append(s)
    flush()
    return blocks


def with_pictures(blocks, pics):
    """The document's blocks with `pics` ([{"path", "caption"}]) spread through it: each after the first paragraph of a section, the
    sections spaced evenly from the first (two to a section when there are more pictures than sections)."""
    heads = [i for i, b in enumerate(blocks) if b[0] == "h1"] or [0]
    at = {}
    for j, p in enumerate(pics):
        h = heads[j * len(heads) // len(pics)]
        k = next((i for i in range(h, len(blocks)) if blocks[i][0] == "para"), h)
        at.setdefault(k, []).append(("picture", p, 0))
    return [x for i, b in enumerate(blocks) for x in [b, *at.get(i, [])]]


def title_of(blocks, default="Document"):
    return next((plain(t) for k, t, _ in blocks if k == "title"), None) or next((plain(t) for k, t, _ in blocks if k == "h1"), None) or default


def slides(blocks):
    """Blocks as a deck: [{"title", "subtitle", "points": [(text, level)], "table": rows or None, "notes"}]. The title (#) and the words
    right after it make the first slide; each heading starts one; a table gets a slide of its own (TABLE_ROWS rows a slide, its header
    on each), titled like the slide it was on."""
    deck = []

    def new(title):
        deck.append({"title": title, "subtitle": "", "points": [], "table": None, "notes": ""})
    for kind, text, level in blocks:
        if kind in ("title", "h1", "h2", "h3"):
            new(text)
            continue
        if not deck:
            new("")
        cur = deck[-1]
        if kind == "notes":
            cur["notes"] = f"{cur['notes']} {text}".strip()
        elif kind == "table":
            head, body = text[0], text[1:] or [[""] * len(text[0])]
            for i in range(0, len(body), TABLE_ROWS):
                if cur["points"] or cur["table"] or len(deck) == 1:
                    new(cur["title"])
                    cur = deck[-1]
                cur["table"] = [head] + body[i:i + TABLE_ROWS]
        elif len(deck) == 1 and kind == "para" and not cur["points"]:
            cur["subtitle"] = f"{cur['subtitle']} {text}".strip()
        else:
            cur["points"].append((text, level if kind == "bullet" else 0))
    return deck


def target(folder, title, ext):
    """A new file in `folder` named after the title (what Windows refuses in a name taken out; the models' narrow no-break spaces and
    non-breaking hyphens made plain, so the name can be typed and searched), never one that exists."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", plain(title)).replace("‑", "-").replace("‐", "-")
    name = re.sub(r"\s+", " ", name).strip(" .")[:NAME_MAX].strip(" .") or "Document"
    if re.fullmatch(r"(?i)con|prn|aux|nul|com\d|lpt\d", name):
        name += " document"
    p, n = Path(folder) / f"{name}{ext}", 2
    while p.exists():
        p, n = Path(folder) / f"{name} ({n}){ext}", n + 1
    return p


def available(kind):
    """(True, "") when this PC's Office can make `kind`, else (False, why)."""
    app = KINDS[kind][1]
    if sys.platform != "win32":
        return False, "Making Word, PowerPoint and Excel files needs Microsoft Office on Windows, and this computer isn't Windows."
    try:
        import win32com.client  # noqa: F401
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, KINDS[kind][2] + "\\CLSID"))
    except ImportError:
        return False, f"I can't drive {app} here: pywin32 isn't installed for Monday's Python."
    except OSError:
        return False, f"Microsoft {app} isn't installed on this computer, so I can't make the file."
    return True, ""


def theme_file():
    pf = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)") if os.environ.get(k)]
    dirs = [Path(p) / "Microsoft Office" / sub for p in pf for sub in ("root/Document Themes 16", "Document Themes 16", "Document Themes 15")]
    return next((d / t for t in THEMES for d in dirs if (d / t).exists()), None)


def build(kind, blocks, path, visible=True, cursor=None, front=None):
    """The document made in Office and saved at `path` -> {"count", "unit"}. visible: in a window the owner sees, filled block by block
    and left open for them (front(hwnd): brings that window forward, which Windows won't let Office do itself; cursor(x, y): where
    Monday's cursor goes meanwhile); else hidden, and closed when saved. Raises OfficeError."""
    import pythoncom
    import pywintypes
    pythoncom.CoInitialize()
    try:
        return {"word": _word, "pdf": _word, "powerpoint": _powerpoint, "excel": _excel}[kind](
            blocks, Path(path), visible, cursor, front if visible else None)
    except pywintypes.com_error as e:
        info = e.excepinfo[2] if e.excepinfo and e.excepinfo[2] else e.strerror
        raise OfficeError(f"{KINDS[kind][1]} refused: {info}") from e
    finally:
        pythoncom.CoUninitialize()


def _window(cls, name, wait=3.0):
    """The visible Office window (class `cls`) whose title starts with the document's name ("Presentation1 - PowerPoint"), polled while
    Office draws it; None if it doesn't come. (Office's own HWND properties don't read through pywin32's late binding: PowerPoint's is
    "Member not found", 2026-10-24.)"""
    import ctypes
    from ctypes import wintypes
    u, end = ctypes.windll.user32, time.monotonic() + wait
    while True:
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def each(h, _):
            k, t = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(256)
            u.GetClassNameW(h, k, 64)
            u.GetWindowTextW(h, t, 256)
            if k.value == cls and t.value.startswith(name) and u.IsWindowVisible(h):
                found.append(h)
            return True
        u.EnumWindows(each, 0)
        if found or time.monotonic() > end:
            return found[0] if found else None
        time.sleep(0.1)


def _front(front, cls, name):
    """Brings the document's window forward (Windows won't let Office take the front itself)."""
    try:
        hwnd = _window(cls, name)
        if hwnd:
            front(hwnd)
    except Exception:  # (the document is still made when its window can't come forward)
        pass


def _type(sel, text):
    parts = re.split(r"\*\*(.+?)\*\*", text)
    if len(parts) == 1:
        sel.TypeText(text)
        return
    for i, part in enumerate(parts):
        if part:
            sel.Font.Bold = bool(i % 2)
            sel.TypeText(part)
    sel.Font.Bold = False


def _word(blocks, path, visible, cursor, front=None):
    """Word: each block typed at the caret in its style (Title, Heading 1-3, List Bullet, List Number), tables filled in place, photos
    centred with their caption under them. Counted in pages, as Word lays them out."""
    from win32com.client import DispatchEx
    app = DispatchEx("Word.Application")
    app.Visible = visible
    d = app.Documents.Add()
    if front:
        _front(front, "OpusApp", d.Name)
    sel = app.Selection
    for kind, text, level in blocks:
        if kind == "notes":  # (a slide's notes; in a document, a note is its words)
            kind, text = "para", f"Note: {text}"
        if kind == "table":
            cols = max(len(r) for r in text)
            t = d.Tables.Add(sel.Range, len(text), cols)
            for i, row in enumerate(text, 1):
                for j in range(1, cols + 1):
                    t.Cell(i, j).Range.Text = plain(row[j - 1]) if j <= len(row) else ""
            try:
                t.Style = WORD_TABLE_STYLE
            except Exception:  # (an Office without the Grid Table styles: plain borders)
                t.Borders.Enable = True
            sel.EndKey(6)  # wdStory: on past the table
        elif kind == "picture":
            sel.Style = d.Styles(WORD_STYLES["para"])  # (not the list or heading the last block left)
            sel.ParagraphFormat.Alignment = 1  # wdAlignParagraphCenter
            pic = sel.InlineShapes.AddPicture(str(text["path"]), False, True)  # (not linked: saved in the document)
            pic.LockAspectRatio = -1  # msoTrue: a width or height set keeps its shape
            if pic.Width > PICTURE_W:
                pic.Width = PICTURE_W
            if pic.Height > PICTURE_H:
                pic.Height = PICTURE_H
            sel.TypeParagraph()
            sel.Style = d.Styles(WORD_STYLES["caption"])
            sel.ParagraphFormat.Alignment = 1
            _type(sel, text["caption"])
            sel.TypeParagraph()
            sel.ParagraphFormat.Alignment = 0
        else:
            sel.Style = d.Styles(BULLETS[level] if kind == "bullet" else WORD_STYLES[kind])
            _type(sel, text)
            sel.TypeParagraph()
        if visible:
            time.sleep(PACE_S)
    pages, tables, pictures = d.ComputeStatistics(2), d.Tables.Count, d.InlineShapes.Count  # wdStatisticPages
    d.SaveAs2(str(path), SAVE_AS[path.suffix])
    if path.suffix == ".pdf":
        d.Saved = True  # (the PDF is the file; the open document asks nothing when it is closed)
    if not visible:
        d.Close(0)
        if app.Documents.Count == 0:
            app.Quit()
    return {"count": pages, "unit": "page" if pages == 1 else "pages", "tables": tables, "pictures": pictures}


def _powerpoint(blocks, path, visible, cursor, front=None):
    """PowerPoint: the deck in Office's own theme, a slide at a time (shown as it is made), notes under each, tables on their own."""
    from win32com.client import DispatchEx
    deck = slides(blocks)
    if not deck:
        raise OfficeError("There was nothing to put on a slide.")
    app = DispatchEx("PowerPoint.Application")
    others = app.Presentations.Count  # the owner's own decks, when PowerPoint was open: never closed
    pr = app.Presentations.Add(WithWindow=visible)
    if front:
        _front(front, "PPTFrameClass", pr.Name)
    theme = theme_file()
    if theme:
        pr.ApplyTheme(str(theme))
    w, h = pr.PageSetup.SlideWidth, pr.PageSetup.SlideHeight
    for i, sl in enumerate(deck, 1):
        layout = 1 if i == 1 else 11 if sl["table"] else 2  # ppLayoutTitle, ppLayoutTitleOnly, ppLayoutText
        s = pr.Slides.Add(i, layout)
        s.Shapes(1).TextFrame.TextRange.Text = plain(sl["title"]) or " "
        if layout == 1 and s.Shapes.Count > 1:
            if sl["subtitle"] or sl["points"]:
                s.Shapes(2).TextFrame.TextRange.Text = plain(sl["subtitle"] or " · ".join(t for t, _ in sl["points"]))
            else:
                s.Shapes(2).Delete()
        elif layout == 2:
            body = s.Shapes(2).TextFrame.TextRange
            body.Text = "\r".join(plain(t) for t, _ in sl["points"]) or " "
            for j, (_, lvl) in enumerate(sl["points"], 1):
                if lvl:
                    body.Paragraphs(j).IndentLevel = min(lvl + 1, 5)
        else:
            rows, cols = sl["table"], max(len(r) for r in sl["table"])
            tb = s.Shapes.AddTable(len(rows), cols, w * 0.06, h * 0.26, w * 0.88, min(h * 0.66, h * 0.065 * len(rows))).Table
            for r, row in enumerate(rows, 1):
                for c in range(1, cols + 1):
                    tr = tb.Cell(r, c).Shape.TextFrame.TextRange
                    tr.Text = plain(row[c - 1]) if c <= len(row) else ""
                    tr.Font.Size = 14 if len(rows) <= 7 else 12
        if sl["notes"]:
            s.NotesPage.Shapes.Placeholders(2).TextFrame.TextRange.Text = plain(sl["notes"])
        if visible:
            win = pr.Windows(1)
            win.View.GotoSlide(i)
            if cursor:
                t = s.Shapes(1)
                cursor(win.PointsToScreenPixelsX(t.Left + t.Width / 2), win.PointsToScreenPixelsY(t.Top + t.Height / 2))
            time.sleep(SLIDE_PACE_S)
    pr.SaveAs(str(path), SAVE_AS[".pptx"])
    count = pr.Slides.Count
    if not visible:
        pr.Close()
        if others == 0:
            app.Quit()
    return {"count": count, "unit": "slides"}


def _cell(v):
    """A cell's value: a number as a number; text that Excel would run as a formula (= + - @) kept as text."""
    t = plain(v).strip()
    if re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", t):
        return float(t.replace(",", ""))
    return "'" + t if t[:1] in ("=", "+", "-", "@") else t


def _excel(blocks, path, visible, cursor, front=None):
    """Excel: each table on a sheet of its own, named after the heading above it, as an Excel table with its columns fitted."""
    from win32com.client import DispatchEx
    tables, head = [], None
    for kind, text, _ in blocks:
        if kind in ("title", "h1", "h2", "h3"):
            head = plain(text)
        elif kind == "table":
            tables.append((head, text))
    if not tables:
        raise OfficeError("There was no table to put in a spreadsheet.")
    app = DispatchEx("Excel.Application")
    others = app.Workbooks.Count
    app.Visible = visible
    if visible:
        app.UserControl = True  # it stays open for the owner when Monday lets go
    wb = app.Workbooks.Add()
    if front:
        _front(front, "XLMAIN", wb.Name)
    for i, (name, rows) in enumerate(tables):
        ws = wb.Worksheets(1) if i == 0 else wb.Worksheets.Add(After=wb.Worksheets(wb.Worksheets.Count))
        ws.Name = (re.sub(r"[\[\]:*?/\\]", "", name or "")[:31].strip() or f"Sheet{i + 1}") if name and name not in [x[0] for x in tables[:i]] else f"Sheet{i + 1}"
        cols = max(len(r) for r in rows)
        for r, row in enumerate(rows, 1):
            for c in range(1, cols + 1):
                ws.Cells(r, c).Value = _cell(row[c - 1]) if c <= len(row) else ""
            if visible:
                if cursor:
                    cell = ws.Cells(r, 1)
                    cursor(app.ActiveWindow.PointsToScreenPixelsX(cell.Left + cell.Width / 2),
                           app.ActiveWindow.PointsToScreenPixelsY(cell.Top + cell.Height / 2))
                time.sleep(PACE_S / 2)
        ws.ListObjects.Add(1, ws.Range(ws.Cells(1, 1), ws.Cells(len(rows), cols)), None, 1).TableStyle = "TableStyleMedium2"  # xlSrcRange, xlYes
        ws.Columns.AutoFit()
    wb.Worksheets(1).Activate()
    wb.SaveAs(str(path), SAVE_AS[".xlsx"])
    count = sum(len(r) - 1 for _, r in tables)
    if not visible:
        wb.Close(False)
        if others == 0:
            app.Quit()
    return {"count": count, "unit": "rows"}


if __name__ == "__main__":
    md = ("<think>plan</think>\n# BharatGen: India's AI\nA government programme, explained.\n\n## What it is\n- Funded by **DST** since 2024 【2】\n"
          "  - 22 languages [1]\n- See [the site](https://bharatgen.com)\n\nNotes: say who funds it.\n\n## Money\n| Pillar | Outlay |\n|---|---:|\n"
          "| Compute | 4,563 |\n| Skills | 883 |\n\n### Steps\n1. Apply\n2) Wait\n---\n```\ncode\n```\nA closing *thought* on `it`.")
    b = outline(md)
    assert b == [("title", "BharatGen: India's AI", 0), ("para", "A government programme, explained.", 0), ("h1", "What it is", 0),
                 ("bullet", "Funded by **DST** since 2024 [2]", 0), ("bullet", "22 languages [1]", 1), ("bullet", "See the site", 0),
                 ("notes", "say who funds it.", 0), ("h1", "Money", 0), ("table", [["Pillar", "Outlay"], ["Compute", "4,563"], ["Skills", "883"]], 0),
                 ("h2", "Steps", 0), ("number", "Apply", 0), ("number", "Wait", 0), ("para", "code", 0), ("para", "A closing thought on it.", 0)], b
    assert outline("## S\n- a point\n- Notes: say it\n**Notes:** and this\n*Speaker notes*: more") == [
        ("h1", "S", 0), ("bullet", "a point", 0), ("notes", "say it", 0), ("notes", "and this", 0), ("notes", "more", 0)], \
        "a slide's notes, however the model wrote them, are notes, not points"
    assert title_of(b) == "BharatGen: India's AI" and title_of([("h1", "X", 0)]) == "X" and title_of([]) == "Document"
    assert outline("Not confirmed:\n– Exact launch date\n– The crew's roles\nA 2027 – 2028 window.") == [("para", "Not confirmed:", 0),
        ("bullet", "Exact launch date", 0), ("bullet", "The crew's roles", 0), ("para", "A 2027 – 2028 window.", 0)], "en-dash lists, as gpt-oss writes them"
    d = slides(b)
    assert [x["title"] for x in d] == ["BharatGen: India's AI", "What it is", "Money", "Steps"], [x["title"] for x in d]
    assert d[0]["subtitle"] == "A government programme, explained." and d[1]["points"][1] == ("22 languages [1]", 1) and d[1]["notes"] == "say who funds it."
    assert d[2]["table"] == [["Pillar", "Outlay"], ["Compute", "4,563"], ["Skills", "883"]] and not d[2]["points"]
    assert d[3]["points"] == [("Apply", 0), ("Wait", 0), ("code", 0), ("A closing thought on it.", 0)]
    big = slides([("h1", "T", 0), ("bullet", "a", 0), ("table", [["h"]] + [[str(i)] for i in range(23)], 0)])
    assert [(x["title"], len(x["table"] or [])) for x in big] == [("T", 0), ("T", 11), ("T", 11), ("T", 4)], "a point, then the table over three slides"
    assert _cell("4,563") == 4563.0 and _cell("12.5") == 12.5 and _cell("=cmd|' /c calc'!A1") == "'=cmd|' /c calc'!A1" and _cell("₹4,563 cr") == "₹4,563 cr"
    doc = [("title", "T", 0), ("para", "p0", 0), ("h1", "A", 0), ("para", "a", 0), ("para", "a2", 0), ("h1", "B", 0), ("bullet", "b", 0),
           ("h1", "C", 0), ("para", "c", 0), ("h1", "D", 0), ("para", "d", 0)]
    _kinds = lambda bs: [(k, t["caption"] if k == "picture" else t) for k, t, _ in bs]  # noqa: E731
    assert _kinds(with_pictures(doc, [{"caption": "1"}, {"caption": "2"}]))[3:5] == [("para", "a"), ("picture", "1")] and \
        _kinds(with_pictures(doc, [{"caption": "1"}, {"caption": "2"}]))[9:11] == [("para", "c"), ("picture", "2")], \
        "after the first paragraph of every other section"
    assert _kinds(with_pictures(doc, [{"caption": str(i)} for i in range(4)]))[6:10] == \
        [("h1", "B"), ("bullet", "b"), ("h1", "C"), ("para", "c")] and len(with_pictures(doc, [{"caption": "x"}] * 4)) == len(doc) + 4, \
        "a section with no paragraph: its picture after the next one's"
    assert _kinds(with_pictures([("para", "x", 0)], [{"caption": "1"}])) == [("para", "x"), ("picture", "1")] and with_pictures(doc, []) == doc
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        assert target(tmp, 'A/B: "C"?', ".docx").name == "AB C.docx" and target(tmp, "con", ".pptx").name == "con document.pptx"
        assert target(tmp, "Argos Watch – Micro‑Brand Overview", ".pptx").name == "Argos Watch – Micro-Brand Overview.pptx", \
            "a name that can be typed: gpt-oss writes narrow no-break spaces and non-breaking hyphens"
        (Path(tmp) / "Deck.pptx").write_bytes(b"x")
        assert target(tmp, "Deck", ".pptx").name == "Deck (2).pptx" and target(tmp, "  ", ".xlsx").name == "Document.xlsx"
    assert available("word")[0] or "isn't" in available("word")[1] or "pywin32" in available("word")[1]
    if "--live" in sys.argv:  # each app hidden, files in data/tmp, deleted after: what it built is counted and on disk
        out = Path(__file__).resolve().parent.parent / "data" / "tmp" / "office-live"
        out.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        photo = out / "photo.jpg"
        Image.new("RGB", (1200, 900), (90, 120, 160)).save(photo)  # (a photo larger than a page allows: Word's copy is made smaller)
        for kind in ("word", "pdf", "powerpoint", "excel"):
            if not available(kind)[0]:
                print(kind, "skipped:", available(kind)[1])
                continue
            p = target(out, f"Monday live {kind}", KINDS[kind][0])
            t0 = time.perf_counter()
            r = build(kind, with_pictures(b, [{"path": photo, "caption": "A photo. Photo: Monday, CC0."}]) if kind == "word" else b, p, visible=False)
            print(f"{kind}: {r['count']} {r['unit']}, {p.stat().st_size} bytes in {time.perf_counter() - t0:.1f}s"
                  + (f", {r['pictures']} picture" if kind == "word" else ""))
            assert r["count"] > 0 and p.stat().st_size > 1000 and (kind != "word" or r["pictures"] == 1 and r["unit"] in ("page", "pages"))
            p.unlink()
        photo.unlink()
        out.rmdir()
    print("office self-check: OK")
