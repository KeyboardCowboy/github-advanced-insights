"""Browser tests.

These need Playwright, which the tool itself does not. Everything here skips
cleanly when it is missing, so `python3 -m unittest discover` still works on a
plain checkout with nothing installed -- the property that makes the rest of the
suite worth running.

    pip install -r requirements-dev.txt && playwright install chromium
"""
