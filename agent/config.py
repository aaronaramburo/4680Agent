"""Central settings. Everything tunable lives here."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = PROJECT_ROOT / "data" / "sessions"

MODEL = os.getenv("AGENT_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("AGENT_EFFORT", "low")
MAX_TOKENS = 16000

# Agent loop limits
MAX_STEPS = 5                 # plan -> act -> observe rounds per request
MAX_ACTIONS_PER_STEP = 6
MAX_REPAIR_ATTEMPTS = 2       # re-asks when the model's output fails validation
MAX_LLM_CALLS_PER_SESSION = 40  # protects the API key on a public deployment
MAX_LLM_CALLS_PER_DAY = int(os.getenv("AGENT_DAILY_CALL_LIMIT", "400"))  # across all sessions

# LLM retry policy
LLM_MAX_RETRIES = 4
LLM_BASE_DELAY = 1.0
LLM_MAX_DELAY = 30.0
LLM_TIMEOUT = 120.0

# Sandbox limits
ALLOWED_EXTENSIONS = {".txt", ".md", ".csv", ".json"}
MAX_FILE_BYTES = 50_000
MAX_FILES = 40
MAX_PATH_DEPTH = 3
MAX_READ_CHARS = 20_000

HTTP_TIMEOUT = 10
SESSION_TTL_SECONDS = 24 * 3600
