"""Paths that need LLM decisions, with the LLM replaced by a stub returning fixed JSON. Run: python -X utf8 -m unittest discover tests"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from argparse import Namespace
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/curate-memory/scripts"
HOOK = Path(__file__).resolve().parents[1] / "hooks/ensure-memory-dir.ps1"
sys.path.insert(0, str(SCRIPTS))
import consolidate  # noqa: E402
import curate  # noqa: E402

ARGS = Namespace(no_llm=False, dry_run=False, llm_dry=False)


def note(name, desc, body, typ="reference"):
    return f"---\nname: {name}\ndescription: \"{desc}\"\ntype: {typ}\n---\n\n{body}\n"


def write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        cfg = self.t / "config.json"   # every group inside the temp folder — never the real memory
        cfg.write_text(json.dumps({"roots": {"main": str(self.t / "none"), "agent": str(self.t / "none")},
                                   "logs": str(self.t / "logs")}), encoding="utf-8")
        curate.configure(str(cfg))
        self._llm = curate.call_llm

    def tearDown(self):
        curate.call_llm = self._llm
        consolidate.call_llm = self._llm
        self.tmp.cleanup()

    def stub(self, reply):
        def fake(root, model, prompt, effort="medium"):
            if isinstance(reply, Exception):
                raise reply
            return reply
        curate.call_llm = fake
        consolidate.call_llm = fake

    def run_curate(self, root_dir):
        r = curate.Root("main", root_dir)
        a = curate.audit(r, curate.live_keys([r]), {})
        before = len(r.leaves()) + len(r.archived())
        curate.run_root(r, a, ARGS, "sonnet")
        return r, curate.verify(r, before, curate.live_keys([r]), False)


class Routing(Base):
    def test_recent_relisting_does_not_duplicate_index_line(self):
        root = self.t / "r"
        for i in range(15):
            write(root / f"distilled/reference_n{i}.md", note(f"reference-n{i}", f"n{i}", f"fact {i}"))
        write(root / "indexes/facts.md", "# Index: facts\n\n" + "\n".join(f"- [n{i}](../distilled/reference_n{i}.md) — n{i}" for i in range(15)) + "\n")
        write(root / "MEMORY.md", "# M\n\n## Topic indexes\n- [facts](indexes/facts.md) — facts\n\n## Recent / pending curate\n- [n3](distilled/reference_n3.md) — updated\n")
        self.stub({"route": [{"leaf": "reference_n3.md", "index": "facts.md", "title": "n3", "hook": "updated"}],
                   "contradictions": [], "hook_updates": [], "l0_hooks": []})
        r, problems = self.run_curate(root)
        self.assertEqual((root / "indexes/facts.md").read_text(encoding="utf-8").count("reference_n3.md"), 1)
        self.assertNotIn("reference_n3.md", (root / "MEMORY.md").read_text(encoding="utf-8").split("## Recent")[1])
        self.assertEqual(problems, [])

    def test_backslash_in_hook_is_kept_as_text(self):
        root = self.t / "r"
        write(root / "distilled/reference_path.md", note("reference-path", "Path", "Install dir."))
        write(root / "MEMORY.md", "# M\n\n## Leaves\n\n## Recent / pending curate\n- [p](distilled/reference_path.md) — p\n")
        hook = r"installed at C:\Users\someone\new\tool"
        self.stub({"route": [{"leaf": "reference_path.md", "index": "LEAVES", "title": "Path", "hook": hook}],
                   "contradictions": [], "hook_updates": [], "l0_hooks": []})
        r, problems = self.run_curate(root)
        self.assertIn(hook, (root / "MEMORY.md").read_text(encoding="utf-8"))
        self.assertEqual(problems, [])

    def test_llm_failure_fails_verification(self):
        root = self.t / "r"
        write(root / "distilled/reference_new.md", note("reference-new", "New", "Body."))
        write(root / "MEMORY.md", "# M\n\n## Leaves\n\n## Recent / pending curate\n- [n](distilled/reference_new.md) — n\n")
        self.stub(RuntimeError("claude exit 1"))
        r, problems = self.run_curate(root)
        self.assertIn("an LLM call failed", problems)


class Audit(Base):
    def test_unrouted_uses_exact_link_targets(self):
        root = self.t / "r"
        write(root / "distilled/project_api.md", note("project-api", "API", "v1"))
        write(root / "distilled/project_api-v2.md", note("project-api-v2", "API v2", "v2"))
        write(root / "MEMORY.md", "# M\n\n## Leaves\n- [v2](distilled/project_api-v2.md) — v2\n")
        r = curate.Root("main", root)
        a = curate.audit(r, curate.live_keys([r]), {})
        self.assertEqual(a["unrouted"], ["project_api.md"])

    def test_struck_text_is_one_line(self):
        root = self.t / "r"
        write(root / "distilled/reference_x.md", note("reference-x", "X", "a ~~b\nc~~ d"))
        write(root / "MEMORY.md", "# M\n\n## Leaves\n- [x](distilled/reference_x.md) — x\n")
        r = curate.Root("main", root)
        self.assertEqual(curate.audit(r, curate.live_keys([r]), {})["struck"], [])

    def test_hangul_counts_toward_the_l0_limit(self):
        self.assertGreater(curate.est_tokens("가" * 1000), 800)
        self.assertLess(curate.est_tokens("a" * 1000), 800)


class Writes(Base):
    def test_split_is_all_or_nothing(self):
        root = self.t / "r"
        body = "\n".join(f"line {i} " + "x" * 90 for i in range(150))   # ~15 KB, no headings to cut at
        write(root / "distilled/reference_big.md", note("reference-big", "Big", body))
        write(root / "MEMORY.md", "# M\n\n## Leaves\n- [big](distilled/reference_big.md) — big\n")
        self.stub({"starts": [3, 6]})   # third part would still be far over 8 KB
        before = (root / "distilled/reference_big.md").read_text(encoding="utf-8")
        r, problems = self.run_curate(root)
        self.assertEqual(sorted(p.name for p in (root / "distilled").iterdir()), ["reference_big.md"])
        self.assertEqual((root / "distilled/reference_big.md").read_text(encoding="utf-8"), before)
        self.assertTrue(any("still over" in i for i in r.issues))

    def test_archived_note_keeps_its_edits_in_one_place(self):
        root = self.t / "r"
        p = root / "distilled/project_old.md"
        write(p, "---\nname: project-old\ntype: project\n---\n\nOld work, no description.\n")
        old = time.time() - 400 * 86400
        os.utime(p, (old, old))
        write(root / "MEMORY.md", "# M\n\n## Leaves\n- [old](distilled/project_old.md) — old\n")
        r, problems = self.run_curate(root)
        self.assertFalse((root / "distilled/project_old.md").exists())
        self.assertIn("description:", (root / "archive/project_old.md").read_text(encoding="utf-8"))


class Consolidate(Base):
    def setUp(self):
        super().setUp()
        self.root = self.t / "r"
        write(self.root / "distilled/project_a.md", note("project-a", "A", "Vendor X phone 010-0000-0001.\nOther.", "project"))
        write(self.root / "distilled/project_b.md", note("project-b", "B", "Vendor X address 1 Main St.", "project"))
        write(self.root / "indexes/work.md", "# Index: work\n\n- [a](../distilled/project_a.md) — a\n- [b](../distilled/project_b.md) — b\n")
        write(self.root / "MEMORY.md", "# M\n\n## Topic indexes\n- [work](indexes/work.md) — work\n\n## Recent / pending curate\n")
        write(self.root / "log.md", "")

    def group(self, file, take, body="Vendor X: phone 010-0000-0001, address 1 Main St."):
        return {"file": file, "type": "reference", "title": "Vendor X", "description": "Vendor X contacts",
                "body": body, "take": take, "conflicts": []}

    def propose(self, groups):
        self.stub({"groups": groups})
        r = curate.Root("main", self.root)
        consolidate.propose(r, "opus", None)
        return json.loads(next((self.root / "raw/consolidate").glob("plan-*.json")).read_text(encoding="utf-8"))

    def test_same_new_name_in_two_groups_is_rejected(self):
        plan = self.propose([self.group("reference_vendor-x.md", [{"leaf": "project_a.md", "lines": [7]}], "Vendor X phone 010-0000-0001."),
                             self.group("reference_vendor-x.md", [{"leaf": "project_b.md", "lines": [7]}], "Vendor X address 1 Main St.")])
        self.assertEqual([g["ok"] for g in plan["groups"]], [True, False])

    def test_apply_never_overwrites_and_backs_up_every_changed_file(self):
        plan = self.propose([self.group("reference_vendor-x.md", [{"leaf": "project_b.md", "lines": [7]},
                                                                   {"leaf": "project_a.md", "lines": [7]}])])
        self.assertTrue(plan["groups"][0]["ok"], plan["groups"][0]["why"])
        pj = next((self.root / "raw/consolidate").glob("plan-*.json"))
        write(self.root / "distilled/reference_vendor-x.md", "someone else's note\n")   # appears after propose
        r = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / "consolidate.py"), "apply", str(pj)], capture_output=True, text=True)
        self.assertIn("STALE", r.stdout + r.stderr)
        self.assertEqual((self.root / "distilled/reference_vendor-x.md").read_text(encoding="utf-8"), "someone else's note\n")
        (self.root / "distilled/reference_vendor-x.md").unlink()
        r = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / "consolidate.py"), "apply", str(pj)], capture_output=True, text=True)
        self.assertIn("APPLIED", r.stdout, r.stderr)
        legacy = next((self.root / "raw/legacy").glob("consolidate-*"))
        self.assertTrue((legacy / "indexes/work.md").exists())   # project_b retired → index edited → backed up


class SecondReview(Base):
    def test_session_write_during_run_is_never_overwritten(self):
        root = self.t / "r"
        write(root / "distilled/reference_new.md", note("reference-new", "New", "Body."))
        write(root / "MEMORY.md", "# M\n\n## Leaves\n\n## Recent / pending curate\n- [n](distilled/reference_new.md) — n\n")
        decision = {"route": [{"leaf": "reference_new.md", "index": "LEAVES", "title": "n", "hook": "n"}],
                    "contradictions": [], "hook_updates": [], "l0_hooks": []}

        def session_saves_meanwhile(r, model, prompt, effort="medium"):
            with open(root / "MEMORY.md", "a", encoding="utf-8") as f:   # a live session saves while the LLM is thinking
                f.write("- [s](distilled/reference_session.md) — saved by a session\n")
            return decision
        curate.call_llm = session_saves_meanwhile
        r, problems = self.run_curate(root)
        self.assertIn("saved by a session", (root / "MEMORY.md").read_text(encoding="utf-8"))
        self.assertTrue(any("changed during the run" in p for p in problems))

    def test_archive_never_overwrites_an_archived_note(self):
        root = self.t / "r"
        p = root / "distilled/project_x.md"
        write(p, note("project-x", "X now", "new text", "project"))
        old = time.time() - 400 * 86400
        os.utime(p, (old, old))
        write(root / "archive/project_x.md", note("project-x", "X before", "archived text", "project"))
        write(root / "MEMORY.md", "# M\n\n## Leaves\n- [x](distilled/project_x.md) — x\n")
        r, problems = self.run_curate(root)
        self.assertIn("archived text", (root / "archive/project_x.md").read_text(encoding="utf-8"))
        self.assertTrue(p.exists())
        self.assertTrue(any("already exists" in i for i in r.issues))

    def test_empty_existing_note_gets_its_fix_written(self):
        root = self.t / "r"
        write(root / "distilled/reference_empty.md", "")
        write(root / "MEMORY.md", "# M\n\n## Leaves\n- [e](distilled/reference_empty.md) — e\n")
        r, problems = self.run_curate(root)
        self.assertIn("name: reference-empty", (root / "distilled/reference_empty.md").read_text(encoding="utf-8"))

    def test_housekeeping_keeps_the_note_age(self):
        root = self.t / "r"
        p = root / "distilled/reference_old.md"
        write(p, "---\nname: reference-old\ntype: reference\n---\n\nOld rule.\n")   # no description → fixed by code
        old = time.time() - 30 * 86400
        os.utime(p, (old, old))
        write(root / "MEMORY.md", "# M\n\n## Leaves\n- [o](distilled/reference_old.md) — o\n")
        self.run_curate(root)
        self.assertIn("description:", p.read_text(encoding="utf-8"))
        self.assertAlmostEqual(p.stat().st_mtime, old, delta=2)

    def test_anchor_in_recent_link(self):
        root = self.t / "r"
        write(root / "distilled/reference_a.md", note("reference-a", "A", "## Sec\n\nBody."))
        write(root / "MEMORY.md", "# M\n\n## Leaves\n\n## Recent / pending curate\n- [a](distilled/reference_a.md#sec) — a\n")
        self.stub({"route": [{"leaf": "reference_a.md", "index": "LEAVES", "title": "A", "hook": "a"}],
                   "contradictions": [], "hook_updates": [], "l0_hooks": []})
        r, problems = self.run_curate(root)
        text = (root / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("(distilled/reference_a.md)", text.split("## Recent")[0])
        self.assertNotIn("reference_a.md", text.split("## Recent")[1])   # the anchored pending line is dropped once routed

    def test_inline_code_survives_struck_fix(self):
        root = self.t / "r"
        p = root / "distilled/reference_c.md"
        write(p, note("reference-c", "C", "Run `a~~b~~c` and ~~old~~ new."))
        write(root / "MEMORY.md", "# M\n\n## Leaves\n- [c](distilled/reference_c.md) — c\n")
        self.run_curate(root)
        t = p.read_text(encoding="utf-8")
        self.assertIn("`a~~b~~c`", t)
        self.assertNotIn("~~old~~", t)

    def test_index_line_not_starting_with_a_link(self):
        root = self.t / "r"
        for i in range(27):
            write(root / f"distilled/reference_i{i}.md", note(f"reference-i{i}", f"i{i}", f"item {i}"))
        write(root / "indexes/all.md", "# Index: all\n\n" + "\n".join(f"- item: [i{i}](../distilled/reference_i{i}.md) — i{i}" for i in range(27)) + "\n")
        write(root / "MEMORY.md", "# M\n\n## Topic indexes\n- [all](indexes/all.md) — all\n\n## Recent / pending curate\n")
        names = [f"reference_i{i}.md" for i in range(27)]
        self.stub({"groups": [{"name": "first", "leaves": names[:14]}, {"name": "second", "leaves": names[14:]}],
                   "route": [], "contradictions": [], "hook_updates": [], "l0_hooks": []})
        r, problems = self.run_curate(root)
        self.assertTrue((root / "indexes/first.md").exists())
        self.assertEqual(problems, [])

    def test_negation_flip_is_rejected(self):
        root = self.t / "c"
        write(root / "distilled/project_ops.md", note("project-ops", "Ops", "Do not disable backups.", "project"))
        write(root / "MEMORY.md", "# M\n\n## Leaves\n- [o](distilled/project_ops.md) — o\n")
        write(root / "log.md", "")
        self.stub({"groups": [{"file": "reference_backups.md", "type": "reference", "title": "Backups", "description": "Backups",
                               "body": "Disable backups.", "take": [{"leaf": "project_ops.md", "lines": [7]}], "conflicts": []}]})
        consolidate.propose(curate.Root("main", root), "opus", None)
        plan = json.loads(next((root / "raw/consolidate").glob("plan-*.json")).read_text(encoding="utf-8"))
        self.assertFalse(plan["groups"][0]["ok"])


@unittest.skipUnless(sys.platform == "win32", "PowerShell hook")
class BuiltInFormat(Base):
    """A memory in Claude Code's built-in format — note files at the top and one '- [title](file.md)' line each, no sections —
    as written by agents that know only that format (for example the Subagents plugins)"""
    def route_all(self, names):
        self.stub({"route": [{"leaf": n, "index": "LEAVES", "title": n[:-3], "hook": n[:-3]} for n in names],
                   "contradictions": [], "hook_updates": [], "l0_hooks": []})

    def test_each_note_listed_once_and_new_lines_stay_pending(self):
        root = self.t / "inventor"
        write(root / "env_paths.md", note("env-paths", "Install path", "C:\\Apps\\Tool"))
        write(root / "fillet_error.md", note("fillet-error", "Fillet error", "[RESOLVED] recompute first."))
        write(root / "MEMORY.md", "- [Install path](env_paths.md) — install path\n- [Fillet error](fillet_error.md) — fix\n")
        self.route_all(["env_paths.md", "fillet_error.md"])
        self.run_curate(root)
        text = (root / "MEMORY.md").read_text(encoding="utf-8")
        for n in ("env_paths.md", "fillet_error.md"):
            self.assertEqual(text.count(f"distilled/{n}"), 1, text)
        self.assertTrue(text.rstrip().endswith("## Recent / pending curate"), text)   # Recent is the last section

        # next day the agent adds a note the same way: file at the top, link line appended at the end
        write(root / "timing.md", note("timing", "Run times", "Export takes 40 s."))
        write(root / "MEMORY.md", text.rstrip("\n") + "\n- [Run times](timing.md) — export timing\n")
        self.route_all(["timing.md"])
        r, problems = self.run_curate(root)
        text = (root / "MEMORY.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("distilled/timing.md"), 1, text)
        self.assertIn("distilled/timing.md", text.split("## Leaves")[1].split("## Recent")[0])
        self.assertEqual(problems, [])

    def test_new_topic_index_goes_above_recent(self):
        text = curate.ensure_section("# M\n\n## Leaves\n- [a](distilled/a.md) — a\n", "Topic indexes")
        self.assertEqual([l for l in text.split("\n") if l.startswith("## ")], ["## Leaves", "## Topic indexes", "## Recent / pending curate"])


class Hook(unittest.TestCase):
    def test_existing_root_is_reconnected_not_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            ws, mem = t / "ws", t / "mem"
            (ws / "proj").mkdir(parents=True)
            write(mem / "ws__proj/MEMORY.md", "# my existing index\n")
            write(mem / "ws__proj/log.md", "- old log\n")
            cfg = t / "c.json"
            cfg.write_text(json.dumps({"workspace": str(ws), "roots": {"main": str(mem)}}), encoding="utf-8")
            r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HOOK)],
                               input=json.dumps({"cwd": str(ws / "proj")}).encode(), capture_output=True,
                               env={**os.environ, "MEMORY_CURATION_CONFIG": str(cfg)})
            self.assertIn("Reconnected", r.stdout.decode("utf-8", "replace"))
            self.assertEqual((mem / "ws__proj/MEMORY.md").read_text(encoding="utf-8"), "# my existing index\n")
            self.assertEqual((mem / "ws__proj/log.md").read_text(encoding="utf-8"), "- old log\n")
            self.assertIn("ws__proj", (ws / "proj/.claude/settings.local.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
