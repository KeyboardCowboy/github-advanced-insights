#!/usr/bin/env python3
"""Render a deliberately small Markdown subset to safe HTML.

Used for a report's info panel, which is authored in the report form and shown
on the report page. Rendering happens here, at normalize time, rather than in
the template, for two reasons: the view model then carries HTML that is already
safe, and the template stays a dumb renderer.

Safety is by construction rather than by sanitizing afterwards. Every character
of input is HTML-escaped first, then a fixed set of patterns re-introduces the
only tags this function can ever emit:

    paragraphs, <ul>/<li>, <strong>, <em>, <code>, <a>

Because escaping happens before any tag is inserted, authored text cannot
introduce markup of its own. There is no allowlist to keep in sync and no way
for a pasted `<script>` to survive, which matters because these pages get
published and shared.

Links are restricted to http and https so a `javascript:` href cannot be
smuggled through the link syntax.
"""
import re

# Inline patterns, applied to already-escaped text. Order matters: code spans
# are taken first so their contents are not further interpreted.
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")

_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}


def escape(text):
    return "".join(_ESCAPES.get(char, char) for char in str(text))


def _inline(text):
    """Apply inline formatting to a line of already-escaped text."""
    text = _CODE.sub(r"<code>\1</code>", text)
    text = _LINK.sub(r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    return text


def render(markdown):
    """Markdown subset -> HTML string. Empty input renders as an empty string."""
    if not markdown or not str(markdown).strip():
        return ""

    html_blocks = []
    bullets = []

    def flush_bullets():
        if bullets:
            html_blocks.append("<ul>" + "".join(f"<li>{b}</li>" for b in bullets) + "</ul>")
            bullets.clear()

    for raw_line in escape(markdown).splitlines():
        line = raw_line.strip()
        if not line:
            flush_bullets()
            continue
        if line.startswith(("- ", "* ")):
            bullets.append(_inline(line[2:].strip()))
            continue
        # A non-bullet line ends any run of bullets before starting a paragraph.
        flush_bullets()
        html_blocks.append(f"<p>{_inline(line)}</p>")

    flush_bullets()
    return "".join(html_blocks)
