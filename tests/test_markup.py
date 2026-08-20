"""markup.render turns authored notes into HTML.

Report notes are written by whoever configures the tool, and this output goes
into a page unescaped, so the security property matters: escape everything
first, then reintroduce only the tags this renderer itself produces. A test that
merely checked bold and links would pass just as happily on a renderer that
passed raw HTML straight through.
"""
import unittest

import tests.helpers  # noqa: F401  -- points the tool at the fixtures; must precede the import below
from markup import render


class Formatting(unittest.TestCase):

    def test_bold_and_italic(self):
        self.assertIn("<strong>loud</strong>", render("**loud**"))
        self.assertIn("<em>soft</em>", render("*soft*"))

    def test_code_spans(self):
        self.assertIn("<code>status:Todo</code>", render("`status:Todo`"))

    def test_links(self):
        html = render("[the board](https://github.com/orgs/acme/projects/1)")
        self.assertIn('href="https://github.com/orgs/acme/projects/1"', html)
        self.assertIn(">the board</a>", html)


class Safety(unittest.TestCase):

    def test_script_tags_render_as_visible_text(self):
        html = render("<script>alert(1)</script>")
        self.assertNotIn("<script", html)
        self.assertIn("&lt;script&gt;", html)

    def test_javascript_hrefs_do_not_become_links(self):
        # The link pattern only accepts http(s), so this stays literal text.
        html = render("[click me](javascript:alert(1))")
        self.assertNotIn("<a ", html)
        self.assertNotIn("javascript:alert(1)\"", html)

    def test_raw_html_attributes_are_escaped(self):
        html = render('<img src=x onerror="alert(1)">')
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)

    def test_ampersands_are_escaped_once(self):
        # Escaping twice would show "&amp;amp;" to the reader.
        self.assertIn("Ready &amp; waiting", render("Ready & waiting"))
        self.assertNotIn("&amp;amp;", render("Ready & waiting"))

    def test_markup_inside_a_code_span_is_not_interpreted(self):
        html = render("`<b>not bold</b>`")
        self.assertNotIn("<b>", html)


class Structure(unittest.TestCase):

    def test_blank_lines_separate_paragraphs(self):
        html = render("First point.\n\nSecond point.")
        self.assertEqual(html.count("<p>"), 2)

    def test_empty_input_produces_empty_output(self):
        self.assertEqual(render("").strip(), "")


if __name__ == "__main__":
    unittest.main()
