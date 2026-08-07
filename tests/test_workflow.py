import json
import tempfile
import unittest
from pathlib import Path

from src.workflow import run_intelligence_workflow, run_meeting_workflow, tasks_from_video_intelligence


def intelligence():
    return {
        "transcript": {
            "segments": [
                {"start": 8, "end": 12, "text": "The save button needs to persist."},
                {"start": 50, "end": 55, "text": "Unrelated discussion."},
            ]
        },
        "key_points": [
            {
                "point": "Persist profile edits",
                "original_quote": "The save button needs to persist.",
                "english_quote": "The save button needs to persist.",
                "timestamp": 10,
            }
        ],
        "visual_context": [
            {
                "key_point_index": 1,
                "timestamp": 10,
                "frames": ["frames/save.jpg"],
                "analysis": {"summary": "Profile form with Save button"},
            }
        ],
    }


class WorkflowTests(unittest.TestCase):
    def test_builds_timestamped_repository_task(self):
        tasks = tasks_from_video_intelligence(intelligence())
        self.assertEqual("Persist profile edits", tasks[0]["issue_summary"])
        context = tasks[0]["meeting_issue_context"]
        self.assertEqual(1, len(context["transcript_segments"]))
        self.assertEqual("frames/save.jpg", context["screenshot_context"][0]["path"])

    def test_runs_tasks_and_writes_structured_reports(self):
        calls = []

        def runner(repository_url, **kwargs):
            calls.append((repository_url, kwargs))
            return {
                "status": "PR_OPENED",
                "agent_workflow_result": {
                    "changed_files": ["profile.py"],
                    "coder_result": {"change_summary": "Saved profile edits."},
                },
                "pr_state": {"pr_number": 7, "pr_url": "https://github.test/pr/7"},
            }

        with tempfile.TemporaryDirectory() as directory:
            report = run_intelligence_workflow(
                "owner/project",
                intelligence(),
                run_id="meeting-1",
                report_dir=directory,
                task_runner=runner,
            )
            self.assertEqual("COMPLETED", report["status"])
            self.assertEqual("meeting-1-task-01", calls[0][1]["run_id"])
            self.assertTrue(Path(report["report_paths"]["json"]).is_file())
            self.assertIn("Saved profile edits", Path(report["report_paths"]["markdown"]).read_text(encoding="utf-8"))

    def test_no_actionable_tasks_finishes_without_running_repository_agent(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            report = run_intelligence_workflow(
                "owner/project",
                {"transcript": {"segments": []}, "key_points": [], "visual_context": []},
                run_id="tutorial-video",
                report_dir=directory,
                task_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
            )

            self.assertEqual("NO_ACTIONABLE_TASKS", report["status"])
            self.assertEqual([], calls)
            self.assertTrue(Path(report["report_paths"]["markdown"]).is_file())

    def test_task_exception_is_preserved_in_generated_report(self):
        def failing_runner(*args, **kwargs):
            raise RuntimeError("provider rejected the request")

        with tempfile.TemporaryDirectory() as directory:
            report = run_intelligence_workflow(
                "owner/project",
                intelligence(),
                run_id="failed-agent",
                report_dir=directory,
                task_runner=failing_runner,
            )

            self.assertEqual("INCOMPLETE", report["status"])
            self.assertEqual("TASK_EXECUTION_FAILED", report["tasks"][0]["error"]["code"])
            self.assertIn("provider rejected", Path(report["report_paths"]["markdown"]).read_text(encoding="utf-8"))

    def test_meeting_workflow_accepts_injected_extractor(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "meeting.mp4"
            video.write_bytes(b"video")
            output = Path(directory) / "intelligence.json"
            output.write_text(json.dumps(intelligence()), encoding="utf-8")

            def runner(repository_url, **kwargs):
                return {
                    "status": "APPROVED_FOR_PR",
                    "agent_workflow_result": {"changed_files": ["profile.py"], "coder_result": {}},
                    "pr_state": {},
                }

            progress = []
            report = run_meeting_workflow(
                video,
                "owner/project",
                run_id="meeting-2",
                extractor=lambda path: output,
                report_dir=Path(directory) / "reports",
                publish=False,
                task_runner=runner,
                progress_callback=lambda event_type, message: progress.append((event_type, message)),
            )
            self.assertEqual("COMPLETED", report["status"])
            self.assertEqual(["media_started", "intelligence_started", "repository_started"], [item[0] for item in progress])


if __name__ == "__main__":
    unittest.main()
