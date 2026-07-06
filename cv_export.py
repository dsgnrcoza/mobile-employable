"""
cv_export.py
------------
Turns the CV Workshop editor's real contenteditable HTML into a
sequence of typed blocks (paragraph / heading / list item / rule),
each with its own reportlab-flavoured inline markup (<b>, <i>, <u>,
<font color="...">) already normalized.

Both the PDF and DOCX exporters build from these same blocks, so a
bold word or a bullet list looks the same in either file, and both
actually reflect what the user formatted in the editor rather than
guessing structure back out of plain text (the previous approach:
"a short ALL-CAPS line is probably a heading").
"""

import re
from html.parser import HTMLParser

# Tags whose inline content reportlab's own Paragraph mini-markup
# understands directly, once the aliases below are normalized to them.
_REPORTLAB_INLINE_TAGS = {"b", "i", "u", "font", "br"}
_BOLD_ALIASES = {"strong", "b"}
_ITALIC_ALIASES = {"em", "i"}
_BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "li"}


def _rgb_to_hex(value: str) -> str | None:
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", value or "")
    if not m:
        return None
    r, g, b = (int(m.group(i)) for i in (1, 2, 3))
    return f"#{r:02x}{g:02x}{b:02x}"


def _style_align(style: str) -> str | None:
    m = re.search(r"text-align\s*:\s*(left|center|right|justify)", style or "", re.I)
    return m.group(1).lower() if m else None


def _style_color(style: str) -> str | None:
    m = re.search(r"(?<!background-)color\s*:\s*([^;]+)", style or "", re.I)
    if not m:
        return None
    raw = m.group(1).strip()
    return _rgb_to_hex(raw) or (raw if raw.startswith("#") else None)


class _Block:
    __slots__ = ("kind", "align", "ordered", "runs")

    def __init__(self, kind, align=None, ordered=False):
        self.kind = kind  # "p" | "h1" | "h2" | "h3" | "li" | "hr"
        self.align = align or "left"
        self.ordered = ordered
        self.runs = []  # list of (text, {"bold":bool,"italic":bool,"underline":bool,"color":str|None})


class _CvHtmlParser(HTMLParser):
    """
    Walks contenteditable-generated HTML and emits _Block objects.
    Deliberately forgiving: unrecognized tags (div wrappers browsers
    sometimes insert, spans with no useful style) are dropped but their
    text content is kept, rather than raising on anything unexpected.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[_Block] = []
        self._current: _Block | None = None
        self._fmt_stack = []  # stack of {"bold","italic","underline","color"}
        self._list_stack = []  # stack of bool (ordered?)

    def _fmt(self):
        bold = italic = underline = False
        color = None
        for f in self._fmt_stack:
            bold = bold or f.get("bold", False)
            italic = italic or f.get("italic", False)
            underline = underline or f.get("underline", False)
            color = f.get("color") or color
        return {"bold": bold, "italic": italic, "underline": underline, "color": color}

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        tag = tag.lower()

        if tag == "hr":
            self.blocks.append(_Block("hr"))
            return
        if tag == "br":
            if self._current is not None:
                self._current.runs.append(("\n", self._fmt()))
            return

        if tag in ("ul", "ol"):
            self._list_stack.append(tag == "ol")
            return

        if tag in _BLOCK_TAGS:
            kind = "li" if tag == "li" else (tag if tag in ("h1", "h2", "h3") else "p")
            align = _style_align(attrs_d.get("style", ""))
            ordered = self._list_stack[-1] if (tag == "li" and self._list_stack) else False
            self._current = _Block(kind, align=align, ordered=ordered)
            return

        if tag in _BOLD_ALIASES:
            self._fmt_stack.append({"bold": True})
        elif tag in _ITALIC_ALIASES:
            self._fmt_stack.append({"italic": True})
        elif tag == "u":
            self._fmt_stack.append({"underline": True})
        elif tag in ("font", "span"):
            color = attrs_d.get("color") or _style_color(attrs_d.get("style", ""))
            self._fmt_stack.append({"color": color} if color else {})
        else:
            self._fmt_stack.append({})

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
            return
        if tag in _BLOCK_TAGS:
            if self._current is not None and self._current.runs:
                self.blocks.append(self._current)
            self._current = None
            return
        if tag in _BOLD_ALIASES or tag in _ITALIC_ALIASES or tag in ("u", "font", "span"):
            if self._fmt_stack:
                self._fmt_stack.pop()

    def handle_data(self, data):
        if self._current is None:
            # Text sitting directly inside the body/wrapper, outside any
            # recognized block tag -- treat it as its own paragraph
            # rather than silently dropping it.
            if data.strip():
                self._current = _Block("p")
                self._current.runs.append((data, self._fmt()))
                self.blocks.append(self._current)
                self._current = None
            return
        self._current.runs.append((data, self._fmt()))


def parse_cv_html(html: str) -> list[dict]:
    """
    Returns a list of block dicts:
      {"kind": "p"|"h1"|"h2"|"h3"|"li"|"hr", "align": "left"|"center"|"right"|"justify",
       "ordered": bool, "markup": "<b>...</b> reportlab-flavoured inline HTML",
       "text": "plain text, for DOCX and for blocks with no formatting"}
    """
    parser = _CvHtmlParser()
    try:
        parser.feed(html or "")
    except Exception:
        return []

    results = []
    for block in parser.blocks:
        if block.kind == "hr":
            results.append({"kind": "hr", "align": "left", "ordered": False, "markup": "", "text": ""})
            continue

        markup_parts = []
        text_parts = []
        for text, fmt in block.runs:
            text_parts.append(text)
            escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")
            if fmt.get("bold"):
                escaped = f"<b>{escaped}</b>"
            if fmt.get("italic"):
                escaped = f"<i>{escaped}</i>"
            if fmt.get("underline"):
                escaped = f"<u>{escaped}</u>"
            if fmt.get("color"):
                escaped = f'<font color="{fmt["color"]}">{escaped}</font>'
            markup_parts.append(escaped)

        text = "".join(text_parts).strip()
        if not text:
            continue

        results.append({
            "kind": block.kind,
            "align": block.align or "left",
            "ordered": block.ordered,
            "markup": "".join(markup_parts).strip(),
            "text": text,
            # Raw per-run formatting, for consumers (DOCX) that need
            # bold/italic/underline/color as real run attributes rather
            # than reportlab's embedded markup string.
            "runs": [(t, dict(f)) for t, f in block.runs if t],
        })

    return results
