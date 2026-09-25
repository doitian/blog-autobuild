import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("blog_converter_under_test", REPO / "x.py")
converter = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = converter
spec.loader.exec_module(converter)


class FrontmatterOptionsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.src = self.root / "vault/output"
        self.destination = self.root / "site/content"
        self.note = self.src / "§ Blog/Posts/2609 - Example/§ Example.md"
        self.note.parent.mkdir(parents=True)
        for name, value in {
            "SRC_DIR": self.src,
            "CONTENT_DIR": self.destination,
            "ARTICLES_INDEX": {},
            "OBSIDIAN_INDEX": {},
            "BACKLINKS_COLLECTION": {},
            "ARTICLE_DATES": {},
        }.items():
            replace = patch.object(converter, name, value)
            replace.start()
            self.addCleanup(replace.stop)
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("CI", None)

    def convert(self, properties=None, body="Text.\n"):
        frontmatter = "" if properties is None else "---\n" + yaml.safe_dump(
            properties, allow_unicode=True, sort_keys=False
        ) + "---\n"
        self.note.write_text(frontmatter + "# Example\n\n" + body, encoding="utf-8", newline="\n")
        before = self.note.read_bytes(), self.note.stat().st_mtime_ns
        result = converter.convert_md(self.note)
        self.assertEqual(before, (self.note.read_bytes(), self.note.stat().st_mtime_ns))
        self.assertFalse(self.destination.exists())
        return result

    def target(self):
        name = "dock/目标 Note"
        path = self.src.parent / (name + ".md")
        path.parent.mkdir(parents=True)
        path.write_text("# 目标 Note\n", encoding="utf-8", newline="\n")
        return name

    def test_existing_vectors(self):
        converter.ARTICLES_INDEX["§ Lua C Api Userdata - Chinese"] = self.src / (
            "§ Blog/Posts/2001 - Lua C Api Userdata/§ Lua C Api Userdata - Chinese.md"
        )
        vectors = sorted((REPO / "test-vectors").glob("*.in.md"))
        self.assertEqual(len(vectors), 11)
        for source in vectors:
            with self.subTest(vector=source.name):
                expected = source.with_name(source.name.replace(".in.md", ".out.md"))
                self.assertEqual(expected.read_text(encoding="utf-8"), converter.convert_md(source))
        self.assertFalse(self.destination.exists())

    def test_manual_links_accept_both_spellings(self):
        target = self.target()
        for key in ("obsidianFiles", "obsidian-files"):
            with self.subTest(key=key):
                converter.OBSIDIAN_INDEX.clear()
                rendered = self.convert({key: [target]}, "[[目标 Note]]\n")
                properties = yaml.safe_load(rendered.split("---\n", 2)[1])
                self.assertEqual(properties[key], [target])
                self.assertEqual(converter.OBSIDIAN_INDEX, {"目标 Note": target})
                self.assertIn("https://kb.iany.me/dock/%E7%9B%AE%E6%A0%87+Note", rendered)
                other = "obsidianFiles" if key == "obsidian-files" else "obsidian-files"
                self.assertNotIn(other, properties)

    def test_equal_aliases_are_accepted_without_reordering(self):
        target = self.target()
        rendered = self.convert({"obsidian-files": [target], "obsidianFiles": [target]}, "[[目标 Note]]\n")
        self.assertEqual(converter.OBSIDIAN_INDEX, {"目标 Note": target})
        self.assertIn("https://kb.iany.me/dock/", rendered)
        rendered = self.convert({"allow-full-domain-link": True, "allowFullDomainLink": True},
                                "https://blog.iany.me/post/example/\n")
        self.assertIn("https://blog.iany.me/post/example/", rendered)

    def test_conflicting_aliases_fail_before_indexing(self):
        target = self.target()
        for properties in (
            {"obsidian-files": [target], "obsidianFiles": ["dock/Other"]},
            {"obsidian-files": [target], "allow-full-domain-link": False, "allowFullDomainLink": True},
        ):
            with self.subTest(properties=properties), contextlib.redirect_stdout(io.StringIO()) as output:
                converter.OBSIDIAN_INDEX.clear()
                with self.assertRaises(SystemExit) as failure:
                    self.convert(properties)
                self.assertEqual(failure.exception.code, 1)
                self.assertIn("Conflicting frontmatter options", output.getvalue())
                self.assertEqual(converter.OBSIDIAN_INDEX, {})
                self.assertFalse(self.destination.exists())

    def test_domain_link_flag_keeps_boolean_behavior(self):
        body = "https://blog.iany.me/post/example/\n"
        for key in ("allowFullDomainLink", "allow-full-domain-link"):
            for value in (True, False):
                with self.subTest(key=key, value=value):
                    if value:
                        self.assertIn(body, self.convert({key: value}, body))
                    else:
                        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                            self.convert({key: value}, body)
        for properties in (None, {}):
            with self.subTest(default=properties), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.convert(properties, body)
        self.assertIn("uploads", self.convert(None, "https://blog.iany.me/uploads/example.png\n"))

    def test_missing_manual_link_still_fails_locally(self):
        for key in ("obsidianFiles", "obsidian-files"):
            with self.subTest(key=key), contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit):
                    self.convert({key: ["dock/Missing"]}, "[[Missing]]\n")
                self.assertIn("Manual link to file not found", output.getvalue())
        with patch.dict(os.environ, {"CI": "true"}):
            rendered = self.convert({"obsidian-files": ["dock/Missing"]}, "[[Missing]]\n")
        self.assertIn("https://kb.iany.me/dock/Missing", rendered)

    def test_unrelated_properties_and_public_tags_preserved(self):
        properties = {
            "tags": ["programming", "python"],
            "workflow-tags": ["private-workflow"],
            "status": "now",
            "zettel": "permanent",
            "kind": ["paralet", "app"],
            "full-title": "Unicode 标题",
            "description": "Prose remains unchanged.",
        }
        result = self.convert(properties)
        actual = yaml.safe_load(result.split("---\n", 2)[1])
        self.assertEqual(actual, {**properties, "title": "Example"})
        self.assertEqual(actual["tags"], ["programming", "python"])

    def test_isolated_cli_uses_dash_options(self):
        target = self.target()
        self.note.write_text(
            "---\nobsidian-files:\n  - \"" + target + "\"\nallow-full-domain-link: true\n"
            "tags: [python]\n---\n# Example\n\n[[目标 Note]]\n\nhttps://blog.iany.me/post/example/\n",
            encoding="utf-8", newline="\n",
        )
        before = self.note.read_bytes(), self.note.stat().st_mtime_ns
        environment = {k: v for k, v in os.environ.items() if not k.startswith("WATCHEXEC_")}
        environment.update(KB_SRC_DIR=str(self.src), KB_CONTENT_DIR=str(self.destination), PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(REPO / "x.py"), "run"],
                                cwd=self.root, env=environment, capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        generated = self.destination / "post/example/index.md"
        self.assertIn("https://kb.iany.me/dock/", generated.read_text(encoding="utf-8"))
        self.assertIn("https://blog.iany.me/post/example/", generated.read_text(encoding="utf-8"))
        self.assertEqual(before, (self.note.read_bytes(), self.note.stat().st_mtime_ns))
        self.assertTrue((self.destination.parent / "data/backlinks.json").exists())


if __name__ == "__main__":
    unittest.main()
