import tempfile
import unittest
from pathlib import Path

from src.codebase_mapper import build_repository_context


class RepositoryContextTests(unittest.TestCase):
    def test_context_is_framework_neutral_and_issue_ranked(self):
        with tempfile.TemporaryDirectory(prefix="visionpr context ") as tmp:
            root = Path(tmp)
            (root / "package.json").write_text('{"scripts":{"test":"vitest"}}', encoding="utf-8")
            (root / "profile.ts").write_text("export function saveProfile() { return false; }\n", encoding="utf-8")
            (root / "unrelated.py").write_text("def calculate_total():\n    return 1\n", encoding="utf-8")
            ignored = root / "node_modules" / "package"
            ignored.mkdir(parents=True)
            (ignored / "index.js").write_text("saveProfile", encoding="utf-8")

            context = build_repository_context(root, "save profile changes", max_relevant_files=3)

        paths = [item["path"] for item in context["relevant_files"]]
        self.assertEqual("profile.ts", paths[0])
        self.assertIn("package.json", paths)
        self.assertNotIn("node_modules/package/index.js", context["repo_tree"])

    def test_python_symbols_and_content_are_available(self):
        with tempfile.TemporaryDirectory(prefix="visionpr python context ") as tmp:
            root = Path(tmp)
            (root / "service.py").write_text("class Service:\n    def run(self):\n        return True\n", encoding="utf-8")
            context = build_repository_context(root, "service run")

        item = context["relevant_files"][0]
        self.assertIn("Service", item["symbols"])
        self.assertIn("return True", item["content_excerpt"])


if __name__ == "__main__":
    unittest.main()
