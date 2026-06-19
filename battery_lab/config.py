from pathlib import Path
import os


def env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


BATTERY_DATA_ROOT = Path(os.getenv("BATTERY_DATA_ROOT", "/var/data/battery"))
BATTERY_EIS_ROOT = Path(os.getenv("BATTERY_EIS_ROOT", str(BATTERY_DATA_ROOT / "EIS")))
BATTERY_CAPACITY_ROOT = Path(os.getenv("BATTERY_CAPACITY_ROOT", str(BATTERY_DATA_ROOT / "capacity")))
BATTERY_OUTPUT_ROOT = Path(os.getenv("BATTERY_OUTPUT_ROOT", str(BATTERY_DATA_ROOT / "battery_visual_outputs")))
BATTERY_JOURNAL_ROOT = Path(os.getenv("BATTERY_JOURNAL_ROOT", str(BATTERY_OUTPUT_ROOT / "lab_journal")))
BATTERY_CONDITION_WORKBOOK = Path(
    os.getenv(
        "BATTERY_CONDITION_WORKBOOK",
        str(BATTERY_DATA_ROOT / "Project_Abstract" / "Cell condition Calculation.xlsx"),
    )
)
BATTERY_MATCH_EIS_JSON = Path(
    os.getenv(
        "BATTERY_MATCH_EIS_JSON",
        str(BATTERY_OUTPUT_ROOT / "eis_match_overrides.json"),
    )
)
BATTERY_MATCH_CAPACITY_JSON = Path(
    os.getenv(
        "BATTERY_MATCH_CAPACITY_JSON",
        str(BATTERY_OUTPUT_ROOT / "capacity_match_overrides.json"),
    )
)
BATTERY_STREAMLIT_URL = os.getenv("BATTERY_STREAMLIT_URL", "").strip().rstrip("/")
BATTERY_AI_MODEL = os.getenv("BATTERY_AI_MODEL", "gpt-5.5").strip() or "gpt-5.5"
BATTERY_AI_ENABLE_API = env_truthy("BATTERY_AI_ENABLE_API")
BATTERY_AI_TIMEOUT_SECONDS = env_int("BATTERY_AI_TIMEOUT_SECONDS", 20, minimum=1, maximum=120)
BATTERY_AI_MAX_RETRIES = env_int("BATTERY_AI_MAX_RETRIES", 1, minimum=0, maximum=5)
BATTERY_AI_MAX_INPUT_CHARS = env_int("BATTERY_AI_MAX_INPUT_CHARS", 12000, minimum=1000, maximum=100000)
