"""WonATech/ZIVE binary parsers for .SEO/.SDE EIS and .wrd cycling data."""

from .eis import (
    EISParseResult,
    EISRecord,
    parse_eis_bytes,
    parse_eis_file,
    write_eis_csv,
)
from .wrd import (
    ELECTRODE_AREA_CM2,
    WrdRecord,
    parse_wrd_bytes,
    parse_wrd_file,
    build_capacity_summary,
    mass_g_from_areal_density,
    write_wrd_raw_csv,
    write_capacity_summary_csv,
)

__all__ = [
    "EISParseResult",
    "EISRecord",
    "parse_eis_bytes",
    "parse_eis_file",
    "write_eis_csv",
    "ELECTRODE_AREA_CM2",
    "WrdRecord",
    "parse_wrd_bytes",
    "parse_wrd_file",
    "build_capacity_summary",
    "mass_g_from_areal_density",
    "write_wrd_raw_csv",
    "write_capacity_summary_csv",
]
