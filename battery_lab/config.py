from pathlib import Path
import os


def env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


BATTERY_DATA_ROOT = Path(os.getenv("BATTERY_DATA_ROOT", "/var/data/battery"))
BATTERY_EIS_ROOT = Path(os.getenv("BATTERY_EIS_ROOT", str(BATTERY_DATA_ROOT / "EIS")))
BATTERY_CAPACITY_ROOT = Path(os.getenv("BATTERY_CAPACITY_ROOT", str(BATTERY_DATA_ROOT / "capacity")))
BATTERY_OUTPUT_ROOT = Path(os.getenv("BATTERY_OUTPUT_ROOT", str(BATTERY_DATA_ROOT / "battery_visual_outputs")))
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
