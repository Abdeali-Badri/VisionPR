import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend.app import app, cookie_samesite
from backend.database import db
from backend.services import create_session


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="visionpr-web-")
        self.original_path = db.path
        db.path = Path(self.temp.name) / "visionpr.db"
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        db.path = self.original_path
        self.temp.cleanup()

    def test_health_auth_and_seeded_dashboard(self):
        health = self.client.get("/api/health")
        self.assertEqual(200, health.status_code)
        self.assertEqual("ok", health.json()["status"])

        auth = self.client.get("/api/auth/me").json()
        self.assertEqual("demo", auth["mode"])
        self.assertEqual("Demo Workspace", auth["user"]["display_name"])

        dashboard = self.client.get("/api/dashboard")
        self.assertEqual(200, dashboard.status_code)
        self.assertGreaterEqual(dashboard.json()["stats"]["projects"], 1)
        self.assertTrue(dashboard.json()["recent_reviews"])

    def test_logout_invalidates_server_session_and_clears_cookie(self):
        user = db.fetch_one("SELECT * FROM users WHERE login='alex-visionpr'")
        session_id = create_session(int(user["id"]))
        self.client.cookies.set("visionpr_session", session_id, domain="testserver.local", path="/")
        self.assertTrue(self.client.get("/api/auth/me").json()["authenticated"])

        response = self.client.post("/api/auth/logout")

        self.assertEqual(204, response.status_code)
        self.assertIsNone(db.fetch_one("SELECT * FROM sessions WHERE id=?", (session_id,)))
        self.assertNotIn("visionpr_session", self.client.cookies)
        self.assertFalse(self.client.get("/api/auth/me").json()["authenticated"])

    def test_github_login_always_requests_account_picker(self):
        with patch("backend.app.settings") as configured:
            configured.oauth_configured = True
            configured.github_client_id = "client-id"
            configured.backend_url = "http://testserver"
            configured.cookie_secure = False
            response = self.client.get("/api/auth/github/start", follow_redirects=False)

        self.assertEqual(307, response.status_code)
        query = parse_qs(urlparse(response.headers["location"]).query)
        self.assertEqual(["select_account"], query["prompt"])
        self.assertIn("visionpr_oauth_state", response.headers["set-cookie"])

    def test_secure_cross_origin_deployment_uses_none_samesite(self):
        with patch("backend.app.settings") as configured:
            configured.cookie_secure = True
            self.assertEqual("none", cookie_samesite())

    def test_creates_and_reads_repository_review(self):
        response = self.client.post(
            "/api/reviews",
            json={
                "title": "Improve recommendation results",
                "repository_url": "https://github.com/example/movie-app",
                "source_type": "youtube",
                "source_value": "https://youtu.be/example",
                "build_commands": ["python -m pytest"],
                "constraints": ["Keep the API compatible"],
            },
        )
        self.assertEqual(201, response.status_code)
        review = response.json()
        self.assertEqual("example/movie-app", review["repository"])
        self.assertEqual(["python -m pytest"], review["options"]["build_commands"])

        fetched = self.client.get(f"/api/reviews/{review['id']}")
        self.assertEqual(200, fetched.status_code)
        event = fetched.json()["events"][0]
        self.assertEqual({}, event["metadata"])
        self.assertNotIn("metadata_json", event)

    def test_accept_requires_a_separate_merge_action(self):
        seeded = self.client.get("/api/reviews").json()[0]
        db.execute("UPDATE reviews SET status='AWAITING_HUMAN_REVIEW' WHERE id=?", (seeded["id"],))
        response = self.client.post(
            f"/api/reviews/{seeded['id']}/accept",
            json={"confirmation": "ACCEPT"},
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual({"status": "ACCEPTED", "merge_available": True}, response.json())
        self.assertEqual("ACCEPTED", self.client.get(f"/api/reviews/{seeded['id']}").json()["status"])

    def test_accept_rejects_reviews_with_pending_change_requests(self):
        seeded = self.client.get("/api/reviews").json()[0]
        db.execute("UPDATE reviews SET status='CHANGES_REQUESTED' WHERE id=?", (seeded["id"],))

        response = self.client.post(
            f"/api/reviews/{seeded['id']}/accept",
            json={"confirmation": "ACCEPT"},
        )

        self.assertEqual(409, response.status_code)
        self.assertEqual("CHANGES_REQUESTED", self.client.get(f"/api/reviews/{seeded['id']}").json()["status"])

    def test_feedback_posts_comment_and_starts_correction_worker(self):
        seeded = self.client.get("/api/reviews").json()[0]
        pull = Mock()
        pull.create_issue_comment.return_value = SimpleNamespace(html_url="https://github.test/comment/1")
        repository = Mock()
        repository.get_pull.return_value = pull
        github = Mock()
        github.get_repo.return_value = repository

        with patch("backend.app.github_client_for_user", return_value=github), patch("backend.app.start_worker", return_value=4321) as worker:
            response = self.client.post(
                f"/api/reviews/{seeded['id']}/feedback",
                json={"body": "Keep the explanation but move the dependency into requirements.txt."},
            )

        self.assertEqual(200, response.status_code)
        self.assertEqual(4321, response.json()["worker_pid"])
        worker.assert_called_once_with(seeded["id"], mode="feedback")
        pull.create_issue_comment.assert_called_once()


if __name__ == "__main__":
    unittest.main()
