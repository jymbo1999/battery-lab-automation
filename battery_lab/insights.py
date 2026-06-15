from __future__ import annotations

from collections import defaultdict
from typing import Any

from .models import MetricRecord


def record_insights(record: MetricRecord) -> list[str]:
    metrics = record.metrics
    notes: list[str] = []
    if record.analysis_type == "capacity":
        ice = as_float(metrics.get("ice_percent"))
        retention = as_float(metrics.get("retention@100"))
        ce_mean = as_float(metrics.get("ce_mean"))
        if ice is not None and ice < 90:
            notes.append(f"ICE가 {ice:.1f}%로 낮습니다. 초기 비가역 용량 또는 formation 조건을 확인하세요.")
        if ce_mean is not None and ce_mean < 99:
            notes.append(f"평균 CE가 99% 미만입니다 ({ce_mean:.2f}%).")
        if retention is not None and retention < 80:
            notes.append(f"Retention@100이 80% 미만입니다 ({retention:.1f}%).")
    elif record.analysis_type == "eis":
        quality = as_float(metrics.get("semicircle_quality"))
        if quality is not None and quality < 0.75:
            notes.append("Rct rough fitting 품질이 낮습니다. 자동 Rct는 screening 값으로만 보세요.")
    elif record.analysis_type == "sheet_resistance":
        cv = as_float(metrics.get("cv_percent"))
        if cv is not None and cv > 10:
            notes.append(f"면저항 CV가 높습니다 ({cv:.1f}%). 코팅 불균일 또는 측정 편차를 확인하세요.")
    if record.warning:
        notes.append(record.warning)
    return notes or ["규칙 기반 경고 없음."]


def daily_summary(records: list[MetricRecord]) -> list[str]:
    by_analysis: dict[str, int] = defaultdict(int)
    for record in records:
        by_analysis[record.analysis_type] += 1
    notes = [f"총 {len(records)}개 파일, {len(set(r.cell_id for r in records))}개 셀을 처리했습니다."]
    if by_analysis:
        notes.append(", ".join(f"{name}: {count}" for name, count in sorted(by_analysis.items())))
    best_ice = best_metric(records, "capacity", "ice_percent")
    if best_ice:
        notes.append(f"가장 높은 ICE: {best_ice.cell_id} ({best_ice.metrics['ice_percent']}%).")
    low_rct = best_metric(records, "eis", "rct_auto", reverse=False)
    if low_rct:
        notes.append(f"가장 낮은 rough Rct: {low_rct.cell_id} ({low_rct.metrics['rct_auto']} ohm).")
    warning_count = sum(1 for record in records if record.warning)
    if warning_count:
        notes.append(f"파서 경고가 있는 파일 {warning_count}개는 확인이 필요합니다.")
    return notes


def best_metric(records: list[MetricRecord], analysis_type: str, key: str, reverse: bool = True) -> MetricRecord | None:
    candidates = [record for record in records if record.analysis_type == analysis_type and as_float(record.metrics.get(key)) is not None]
    if not candidates:
        return None
    return sorted(candidates, key=lambda record: as_float(record.metrics.get(key)) or 0.0, reverse=reverse)[0]


def as_float(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
