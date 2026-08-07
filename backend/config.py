from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    database_path: Path = Path(os.getenv("VISIONPR_WEB_DB", PROJECT_ROOT / "data" / "visionpr_web.db"))
    upload_dir: Path = Path(os.getenv("VISIONPR_UPLOAD_DIR", PROJECT_ROOT / "data" / "web_uploads"))
    frontend_url: str = os.getenv("VISIONPR_FRONTEND_URL", "http://127.0.0.1:5173")
    backend_url: str = os.getenv("VISIONPR_BACKEND_URL", "http://127.0.0.1:8000")
    github_client_id: str = os.getenv("GITHUB_CLIENT_ID", "").strip()
    github_client_secret: str = os.getenv("GITHUB_CLIENT_SECRET", "").strip()
    session_secret: str = os.getenv("VISIONPR_SESSION_SECRET", "visionpr-local-development-secret")
    cookie_secure: bool = os.getenv("VISIONPR_COOKIE_SECURE", "0") == "1"
    demo_mode: bool = os.getenv("VISIONPR_DEMO_MODE", "1") == "1"

    @property
    def oauth_configured(self) -> bool:
        return bool(self.github_client_id and self.github_client_secret)


settings = Settings()
