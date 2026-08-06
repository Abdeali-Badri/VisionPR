import json
import unittest
from unittest.mock import patch

from src.crewai_agents import (
    CrewAIAdapterError,
    CrewAIArchitectAgent,
    CrewAICoderAgent,
    CrewAIReviewerAgent,
    SafeReadFileTool,
    SafeWriteFileTool,
    ValidatedBuildPlanTool,
)
from src.schemas import AgenticInput, ArchitectPlan, CoderResult


def agentic_input():
    return AgenticInput.from_dict(
        {
            "run_id": "x",
            "issue_summary": "Fix save",
            "meeting_issue_context": {},
            "repository_context": {"relevant_files": [{"path": "profile.py"}]},
            "build_commands": ["python -m unittest discover"],
        }
    )


class CrewAIAdapterTests(unittest.TestCase):
    def setUp(self):
        self.llm = object()
        self.agent_patch = patch("src.crewai_agents.Agent", side_effect=lambda **kwargs: {"agent_kwargs": kwargs})
        self.task_patch = patch("src.crewai_agents.Task", side_effect=self.make_task)
        self.crew_patch = patch("src.crewai_agents.Crew", side_effect=self.make_crew)
        self.next_result = None
        self.agent_mock = self.agent_patch.start()
        self.task_mock = self.task_patch.start()
        self.crew_mock = self.crew_patch.start()

    def tearDown(self):
        self.crew_patch.stop()
        self.task_patch.stop()
        self.agent_patch.stop()

    def make_task(self, **kwargs):
        class TaskObject:
            output = None

        task = TaskObject()
        task.kwargs = kwargs
        return task

    def make_crew(self, **kwargs):
        test_case = self

        class CrewObject:
            def kickoff(self):
                return test_case.next_result

        crew = CrewObject()
        crew.kwargs = kwargs
        return crew

    def fake_kickoff(self, payload):
        class Result:
            raw = json.dumps(payload)
            pydantic = None

            def __str__(self):
                return self.raw

        return Result()

    def test_architect_creates_structured_plan(self):
        self.next_result = self.fake_kickoff(
            {
                "suspected_cause": "handler",
                "target_files": ["profile.py"],
                "files_to_avoid": [],
                "required_changes": ["change"],
                "implementation_steps": ["edit"],
                "test_plan": ["python -m unittest discover"],
                "risk_notes": [],
            }
        )
        plan = CrewAIArchitectAgent(self.llm).create_plan(agentic_input())
        self.assertEqual(["profile.py"], plan.target_files)

    def test_architect_rejects_invalid_output(self):
        self.next_result = self.fake_kickoff({"target_files": ["profile.py"]})
        with self.assertRaises(CrewAIAdapterError):
            CrewAIArchitectAgent(self.llm).create_plan(agentic_input())

    def test_coder_receives_safe_tools_only_and_validates_output(self):
        self.next_result = self.fake_kickoff(
            {
                "modified_files": ["profile.py"],
                "change_summary": "changed",
                "patch_notes": [],
                "assumptions": [],
                "build_attempted": True,
                "build_result": {"status": "success"},
            }
        )
        coder = CrewAICoderAgent(self.llm, repo_path=".")
        self.assertTrue(all(isinstance(tool, (SafeReadFileTool, SafeWriteFileTool, ValidatedBuildPlanTool)) for tool in coder._tools()))
        result = coder.implement_plan(agentic_input(), ArchitectPlan("cause", ["profile.py"], [], [], [], []))
        self.assertEqual(["profile.py"], result.modified_files)

    def test_reviewer_returns_structured_result(self):
        self.next_result = self.fake_kickoff(
            {
                "approved": True,
                "verdict": "APPROVED",
                "issues_found": [],
                "plan_followed": True,
                "unrelated_changes_detected": False,
                "syntax_or_logic_risks": [],
                "required_revisions": [],
                "next_action": "send_to_pr_publisher",
            }
        )
        review = CrewAIReviewerAgent(self.llm).review_patch(
            agentic_input(),
            ArchitectPlan("cause", ["profile.py"], [], [], [], []),
            CoderResult(["profile.py"], "changed", build_attempted=True, build_result={"status": "success"}),
        )
        self.assertTrue(review.approved)

    def test_model_used_and_delegation_bounded(self):
        agent = CrewAIArchitectAgent(self.llm)._agent()
        kwargs = agent["agent_kwargs"]
        self.assertIs(self.llm, kwargs["llm"])
        self.assertFalse(kwargs["allow_delegation"])
        self.assertLessEqual(kwargs["max_iter"], 3)
        self.assertFalse(kwargs["allow_code_execution"])

    def test_llm_is_required(self):
        with self.assertRaises(CrewAIAdapterError):
            CrewAIArchitectAgent(None)


if __name__ == "__main__":
    unittest.main()
