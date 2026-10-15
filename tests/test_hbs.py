import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from mailqueue.cli import main
from mailqueue.config import Config
from mailqueue.message import build_message, html_to_text
from mailqueue.template import (RenderError, TemplateSyntaxError, find_tags, missing_tags, render)


class HandlebarsSyntaxTests(unittest.TestCase):
    def test_escaped_vs_raw_in_html(self):
        vals = {"name": "<b>&", "link": "<a href='x'>go</a>"}
        out = render("<p>{{ Name }} {{{link}}} {{&link}}</p>", vals, html=True)
        self.assertEqual(out, "<p>&lt;b&gt;&amp; <a href='x'>go</a> <a href='x'>go</a></p>")

    def test_plain_text_never_escapes(self):
        self.assertEqual(render("{{name}}", {"name": "<b>&"}, html=False), "<b>&")

    def test_comments_removed_and_not_tags(self):
        text = "a{{! just a note }}b{{!-- may contain }} and {{name}} --}}e"
        self.assertEqual(render(text, {}), "abe")
        self.assertEqual(find_tags(text), [])

    def test_bracket_names_with_spaces(self):
        self.assertEqual(missing_tags(["{{[First Name]}}"], ["first name"]), [])
        self.assertEqual(render("{{[First Name]}}", {"first name": "Al"}), "Al")

    def test_triple_stash_tag_is_found(self):
        self.assertEqual(find_tags("{{{ Link }}}"), [("link", "Link")])

    def test_unsupported_constructs_rejected(self):
        for bad in ["{{#if x}}y{{/if}}", "{{#each rows}}{{/each}}", "{{> header}}", "{{else}}",
                    "{{upper name}}", "{{this.name}}", "{{}}", "{{^x}}", "{{(lookup a b)}}"]:
            with self.assertRaises(TemplateSyntaxError, msg=bad):
                find_tags(bad)

    def test_literal_text_and_single_braces_untouched(self):
        self.assertEqual(render("a { b } c", {}), "a { b } c")

    def test_empty_value_raises(self):
        with self.assertRaises(RenderError):
            render("{{name}}", {"name": ""})


class HandlebarsMessageTests(unittest.TestCase):
    def camp(self, html, body=""):
        return {"subject_tpl": "Hi {{name}}", "body_tpl": body, "html_tpl": html}

    def test_text_alternative_derived_from_html(self):
        msg = build_message(Config(), self.camp("<html><head><style>p{}</style></head>"
                                                "<body><h1>Hello {{name}}</h1><p>See <a href='https://x.y'>site</a></p>"
                                                "<script>1</script></body></html>"),
                            {"name": "Zoë <3"}, "a@x.com")
        text = msg.get_body(("plain",)).get_content()
        html_part = msg.get_body(("html",)).get_content()
        self.assertIn("Hello Zoë <3", text)
        self.assertIn("site (https://x.y)", text)
        self.assertNotIn("script", text.lower())
        self.assertIn("Zoë &lt;3", html_part)

    def test_explicit_text_template_wins(self):
        msg = build_message(Config(), self.camp("<p>{{name}}</p>", body="Plain {{name}}"), {"name": "A&B"}, "a@x.com")
        self.assertEqual(msg.get_body(("plain",)).get_content().strip(), "Plain A&B")

    def test_html_to_text_collapses_blank_lines(self):
        self.assertEqual(html_to_text("<p>a</p><p></p><p></p><p>b</p>"), "a\n\nb\n")


class HandlebarsCliTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.data = str(self.dir / "data")
        (self.dir / "r.csv").write_text("email,name\na@x.com,Al\n", encoding="utf-8")

    def cli(self, template_name, content, *extra):
        (self.dir / template_name).write_text(content, encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--data-dir", self.data, "enqueue", "--campaign", "c", "--csv", str(self.dir / "r.csv"),
                         "--subject", "Hi {{name}}", "--template", str(self.dir / template_name), *extra])
        return code, out.getvalue(), err.getvalue()

    def test_non_hbs_file_refused(self):
        code, _, err = self.cli("body.html", "<p>{{name}}</p>")
        self.assertEqual(code, 2)
        self.assertIn(".hbs", err)

    def test_block_helper_fails_run_before_queue(self):
        code, _, err = self.cli("b.hbs", "{{#if name}}<p>{{name}}</p>{{/if}}")
        self.assertEqual(code, 2)
        self.assertIn("unsupported Handlebars construct", err)
        self.assertFalse((Path(self.data) / "queue.db").exists())

    def test_missing_tag_in_hbs_names_it(self):
        code, _, err = self.cli("b.hbs", "<p>{{name}} {{{roll_no}}}</p>")
        self.assertEqual(code, 2)
        self.assertIn("{{roll_no}}", err)

    def test_text_template_must_be_hbs_and_works(self):
        (self.dir / "t.txt").write_text("Hi {{name}}", encoding="utf-8")
        code, _, err = self.cli("b.hbs", "<p>{{name}}</p>", "--text-template", str(self.dir / "t.txt"))
        self.assertEqual(code, 2)
        (self.dir / "t.hbs").write_text("Hi {{name}}", encoding="utf-8")
        code, out, _ = self.cli("b.hbs", "<p>{{name}}</p>", "--text-template", str(self.dir / "t.hbs"))
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
