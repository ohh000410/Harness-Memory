"""curate.py on synthetic roots without the LLM (code paths only). Run: python -X utf8 -m unittest discover tests"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fixtures  # noqa: E402

CURATE = Path(__file__).resolve().parents[1] / "skills/curate-memory/scripts/curate.py"


def run(root, cfg):
    env = {**os.environ, "MEMORY_CURATION_CONFIG": str(cfg)}
    r = subprocess.run([sys.executable, "-X", "utf8", str(CURATE), "--path", str(root), "--no-llm"],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    report = next(root.parent.glob("curate-report-*/REPORT.md")).read_text(encoding="utf-8")
    return r.returncode, report


class CurateNoLLM(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        fixtures.build(t / "gen")
        cfg = t / "config.json"   # never the real memory: every root is under the temp folder
        cfg.write_text(json.dumps({"roots": {"main": str(t / "none"), "agent": str(t / "none")}, "logs": str(t / "logs")}),
                       encoding="utf-8")
        cls.r, cls.code, cls.report = {}, {}, {}
        for name in ("alpha", "beta", "gamma"):
            (t / name).mkdir()
            (t / "gen" / name).rename(t / name / name)   # one parent per root: separate report folders
            cls.r[name] = t / name / name
            cls.code[name], cls.report[name] = run(cls.r[name], cfg)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def text(self, root, rel):
        return (self.r[root] / rel).read_text(encoding="utf-8")

    def test_only_routing_is_left_without_llm(self):
        for name in ("alpha", "beta"):
            self.assertEqual(self.code[name], 3)   # unrouted notes remain: routing needs the LLM
            verify = [l for l in self.report[name].splitlines() if "**verify**" in l]
            self.assertTrue(verify and all("unrouted" in l for l in verify), verify)
        self.assertEqual(self.code["gamma"], 0)

    def test_loose_note_moved(self):
        self.assertTrue((self.r["alpha"] / "distilled/scratch-idea.md").exists())
        self.assertFalse((self.r["alpha"] / "scratch-idea.md").exists())

    def test_dead_index_lines_removed(self):
        self.assertNotIn("reference_missing.md", self.text("alpha", "indexes/tools.md"))
        self.assertNotIn("indexes/gone.md", self.text("alpha", "MEMORY.md"))
        self.assertNotIn("reference_dead.md", self.text("beta", "MEMORY.md"))
        self.assertIn("https://example.com", self.text("beta", "MEMORY.md"))   # external links stay

    def test_frontmatter_filled(self):
        self.assertRegex(self.text("alpha", "distilled/Note_With_Caps.md"), r"(?m)^name: note-with-caps$")
        self.assertRegex(self.text("alpha", "distilled/reference_noname.md"), r"(?m)^name: reference-noname$")

    def test_raw_only_links_unwrapped(self):
        t = self.text("alpha", "distilled/reference_procedure.md")
        self.assertNotIn("[[old-procedure]]", t)
        self.assertIn("[[tool-02]]", t)          # live link kept
        self.assertIn("[[nowhere-note]]", t)     # not-yet-existing name kept

    def test_struck_text_superseded(self):
        t = self.text("alpha", "distilled/reference_ports.md")
        self.assertNotIn("~~", t)
        self.assertIn("superseded:", t)
        self.assertNotIn("~~", self.text("beta", "distilled/reference_struck.md"))

    def test_code_is_not_struck_text(self):
        t = self.text("beta", "distilled/reference_code.md")
        for kept in ("echo ~~not struck~~", "x = '~~kept~~'", "Inline `~~kept~~` too."):
            self.assertIn(kept, t)
        self.assertNotIn("superseded: reference_code.md", self.report["beta"])
        self.assertIn("struck 1,", self.report["beta"])   # only reference_struck.md

    def test_archive_only_stale_project_notes(self):
        a = self.r["alpha"]
        self.assertTrue((a / "archive/project_old-purchase.md").exists())
        self.assertNotIn("project_old-purchase", self.text("alpha", "indexes/projects.md"))
        self.assertTrue((a / "distilled/project_hot-item.md").exists())    # under ## Hot
        self.assertTrue((a / "distilled/feedback_style.md").exists())      # not a project note
        self.assertTrue((self.r["beta"] / "archive/project_stale.md").exists())

    def test_big_note_split_at_headings(self):
        d = self.r["alpha"] / "distilled"
        parts = [d / "reference_big.md", *sorted(d.glob("reference_big-*.md"))]
        self.assertGreaterEqual(len(parts), 2)
        self.assertTrue(all(p.stat().st_size <= 8192 for p in parts))
        self.assertIn("[[reference-big]] (refines)", parts[1].read_text(encoding="utf-8"))

    def test_log_rotated_and_encoding_kept(self):
        self.assertLess(len(self.text("beta", "log.md").splitlines()), 100)
        self.assertTrue(list((self.r["beta"] / "raw/legacy").rglob("*log*")))
        raw = (self.r["beta"] / "distilled/reference_crlf.md").read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw)

    def test_llm_work_reported_not_guessed(self):
        self.assertIn("not done", self.report["gamma"])
        self.assertTrue((self.r["gamma"] / "indexes/everything.md").exists())   # split needs the LLM
        self.assertFalse(list((self.r["gamma"] / "distilled").glob("reference_wall-*.md")))

    def test_backups_written(self):
        self.assertTrue(list((self.r["alpha"] / "raw/legacy").iterdir()))


class ConfigSafety(unittest.TestCase):
    def test_roots_are_not_merged_with_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "c.json"
            cfg.write_text(json.dumps({"roots": {"main": tmp}, "archive_days": {"main": 30}}), encoding="utf-8")
            sys.path.insert(0, str(CURATE.parent))
            import curate
            c = curate.Config(str(cfg))
            self.assertEqual(list(c.groups), ["main"])                    # no default agent folder sneaks in
            self.assertEqual((c.days("main"), c.days("agent")), (30, 180))


class Checkpoint(unittest.TestCase):
    def test_failed_root_does_not_block_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            fixtures.build(t / "gen")
            group = t / "group"
            group.mkdir()
            for name in ("alpha", "gamma"):   # alpha fails verification without the LLM, gamma passes
                (t / "gen" / name).rename(group / name)
            cfg = t / "config.json"
            cfg.write_text(json.dumps({"roots": {"main": str(group), "agent": str(t / "no-agents")}, "logs": str(t / "logs")}),
                           encoding="utf-8")
            env = {**os.environ, "MEMORY_CURATION_CONFIG": str(cfg)}
            r = subprocess.run([sys.executable, "-X", "utf8", str(CURATE), "--no-llm", "--full"], capture_output=True, text=True,
                               encoding="utf-8", env=env)
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
            roots = json.loads((t / "logs/checkpoint.json").read_text(encoding="utf-8"))["roots"]
            self.assertIn(str(group / "gamma"), roots)
            self.assertNotIn(str(group / "alpha"), roots)

    def setup_two_roots(self, t):
        group = t / "group"
        for name in ("one", "two"):
            (group / name / "distilled").mkdir(parents=True)
            (group / name / "distilled/reference_a.md").write_text(
                "---\nname: reference-a\ndescription: \"A\"\ntype: reference\n---\n\nFact.\n", encoding="utf-8")
            (group / name / "MEMORY.md").write_text("# M\n\n## Leaves\n- [a](distilled/reference_a.md) — a\n\n## Recent / pending curate\n",
                                                   encoding="utf-8")
        cfg = t / "config.json"
        cfg.write_text(json.dumps({"roots": {"main": str(group)}, "logs": str(t / "logs")}), encoding="utf-8")
        return group, {**os.environ, "MEMORY_CURATION_CONFIG": str(cfg)}

    def curate(self, env, *args):
        return subprocess.run([sys.executable, "-X", "utf8", str(CURATE), "--no-llm", *args], capture_output=True, text=True,
                              encoding="utf-8", env=env)

    def test_only_advances_just_that_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            group, env = self.setup_two_roots(t)
            self.assertEqual(self.curate(env).returncode, 0)   # nightly run: baseline for both roots
            for name in ("one", "two"):
                (group / name / "distilled/reference_a.md").write_text(
                    "---\nname: reference-a\ndescription: \"A\"\ntype: reference\n---\n\nFact, edited.\n", encoding="utf-8")
            before = json.loads((t / "logs/checkpoint.json").read_text(encoding="utf-8"))["roots"]
            r = self.curate(env, "--only", "one")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            after = json.loads((t / "logs/checkpoint.json").read_text(encoding="utf-8"))["roots"]
            self.assertNotEqual(after[str(group / "one")], before[str(group / "one")])   # handled now, not again tonight
            self.assertEqual(after[str(group / "two")], before[str(group / "two")])       # still pending for the nightly run

    def test_unknown_only_name_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            _, env = self.setup_two_roots(t)
            r = self.curate(env, "--only", "on")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("ERROR no such root on", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
