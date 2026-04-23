"""Pre-seed the ADK session database schema before agents start.

The Google ADK DatabaseSessionService calls Base.metadata.create_all() in its
__init__. When all 9 agents start concurrently on a fresh DB they race to
CREATE TYPE / CREATE TABLE simultaneously and conflict. Running this once
before honcho starts ensures the schema exists so each agent's create_all
is a silent no-op.

Usage: python -m scripts.init_adk_db
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

adk_url = os.environ.get("ADK_DATABASE_URL")
if not adk_url:
    print("ADK_DATABASE_URL not set — skipping ADK schema init")
    sys.exit(0)

print(f"==> Initialising ADK session schema on {adk_url.split('@')[-1]} ...")

from google.adk.sessions.database_session_service import DatabaseSessionService  # noqa: E402

DatabaseSessionService(db_url=adk_url)
print("==> ADK schema ready.")
