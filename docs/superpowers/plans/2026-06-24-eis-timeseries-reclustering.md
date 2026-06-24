# EIS 시계열 재클러스터링 + 일지 매핑 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** EIS `_hr` 시계열 파일을 "셀 1개 = 0hr→24hr 한 시계열 = 일지 행 1개"로 정확히 재클러스터링하고, 각 클러스터를 in-scope 일지 행에 1:1 매핑해 검증뷰/체크리스트에 노출한다.

**Architecture:** 새 모듈 `battery_lab/eis_timeseries.py`가 (1)정규화 키 1차 그룹 → (2)0hr/24hr 끝점규칙 병합 → (3)신뢰도 분류 + 일지 행 점수가중 투표 → (4)1:1 충돌 검출을 수행한다. `eis_matching.build_eis_match_report`는 기존 `build_time_series_groups` 대신 이 모듈을 지연 import로 호출(순환 import 회피). `matching_service.verification_payload`의 `deferred_rows`가 클러스터 dict로 대체되어 기존 뷰/체크리스트가 소비한다.

**Tech Stack:** Python 3, dataclasses, pytest. 기존 `eis_matching` 헬퍼(`compact_text`, `time_sort_key`, `folder_date`, `compact_date`) 재사용.

---

## File Structure

- Create: `battery_lab/eis_timeseries.py` — 재클러스터링 + 일지매핑 (이 작업의 모든 알고리즘).
- Create: `tests/test_eis_timeseries.py` — 단위/시나리오 테스트.
- Modify: `battery_lab/eis_matching.py` — `EISTimeSeriesGroup` 제거, `build_eis_match_report`가 새 모듈 호출, 리포트 필드 타입 변경.
- Modify: `battery_lab/matching_service.py` — `verification_payload`의 `deferred_rows`를 클러스터 dict로, summary 키 추가.
- Modify: `battery_lab/verification_view.py` — 시계열 클러스터 섹션 렌더.
- Modify: `battery_lab/checklist_view.py` — 클러스터 단위 후보 드롭다운.
- Modify: `tests/test_matching_verification.py` — deferred_rows 단언을 클러스터 형태로 갱신.

테스트 명령 접두사: `.venv/bin/python -m pytest`. 작업 디렉터리: `battery-lab-automation/`. 환경변수 `PYTHONPATH=$PWD`는 스크립트 실행시에만 필요(테스트는 pytest가 처리).

---

## Task 1: 새 모듈 골격 — dataclass + 끝점 헬퍼 + 1차 그룹

**Files:**
- Create: `battery_lab/eis_timeseries.py`
- Test: `tests/test_eis_timeseries.py`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_eis_timeseries.py`

```python
from battery_lab.eis_matching import EISConditionMatch
from battery_lab import eis_timeseries as ts


def _m(rel, group_key, tp, *, key="", sample="", date="", delta=None, score=70):
    """Build a minimal time-series EISConditionMatch for tests."""
    return EISConditionMatch(
        source_path=rel, relative_path=rel, is_time_series=True,
        file_group_key=group_key, time_point=tp, status="review", score=score, margin=0,
        condition_key=key, condition_sample=sample, condition_date=date, date_delta_days=delta,
    )


def test_hr_num_and_fmt():
    assert ts.hr_num("24hr") == 24
    assert ts.hr_num("0hr") == 0
    assert ts.hr_num("") is None
    assert ts._fmt_hrs({0, 1, 2, 3}) == "[0,1,2,3]"


def test_base_signature_strips_trailing_replicate_only():
    assert ts._base_signature("260610pure4t1") == "260610pure4t"
    assert ts._base_signature("260610pure4t") == "260610pure4t"
    assert ts._base_signature("260521dl2t2t2") == "260521dl2t2t"
    assert ts._base_signature("260521dl2t2t") == "260521dl2t2t"


def test_stage1_collapses_spacing_only_split():
    # "dl 2t2t" and "dl2t2t" differ only by a space -> same compact signature.
    ms = [
        _m("260521/dl 2t2t_0hr_01.SEO", "260521 dl 2t2t", "0hr"),
        _m("260521/dl2t2t_24hr_01.SEO", "260521 dl2t2t", "24hr"),
    ]
    groups = ts._stage1_groups(ms)
    assert len(groups) == 1
    (sig, members), = groups.items()
    assert len(members) == 2
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_eis_timeseries.py -v`
Expected: FAIL — `ModuleNotFoundError: battery_lab.eis_timeseries` / attribute errors.

- [ ] **Step 3: 모듈 작성** — `battery_lab/eis_timeseries.py`

```python
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from .eis_matching import (
    EISConditionMatch,
    compact_date,
    compact_text,
    folder_date,
    time_sort_key,
)

REPLICATE_VOTE_RATIO = 0.6  # second-place journal row this close -> competing/ambiguous


@dataclass(frozen=True)
class EISTimeSeriesCluster:
    cluster_id: str
    folder_date: str
    cluster_signature: str
    member_paths: str
    time_points: str
    has_zero: bool
    has_24: bool
    file_count: int
    merge_provenance: str
    condition_key: str
    condition_sample: str
    condition_date: str
    date_delta_days: int | None
    match_status: str
    candidate_options: str
    reason: str


def hr_num(time_point: str) -> int | None:
    match = re.search(r"\d+", time_point or "")
    return int(match.group(0)) if match else None


def _fmt_hrs(hours: set[int | None]) -> str:
    nums = sorted(n for n in hours if n is not None)
    return "[" + ",".join(str(n) for n in nums) + "]"


def _base_signature(signature: str) -> str:
    # Strip a trailing replicate index (cell number) so cell-1/cell-2 fragments
    # of the same material share a base for endpoint-rule merging. Thickness
    # tokens end in a letter ("...3t") so they survive.
    return re.sub(r"\d{1,2}$", "", signature)


def _stage1_groups(matches: list[EISConditionMatch]) -> dict[str, list[EISConditionMatch]]:
    # Collapse splits caused only by spacing/punctuation: compact_text removes
    # spaces/symbols, so "260521 dl 2t2t" and "260521 dl2t2t" key together while
    # thickness/replicate digits keep genuinely different cells apart.
    groups: dict[str, list[EISConditionMatch]] = defaultdict(list)
    for match in matches:
        groups[compact_text(match.file_group_key)].append(match)
    return groups
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_eis_timeseries.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: 커밋**

```bash
git add battery_lab/eis_timeseries.py tests/test_eis_timeseries.py
git commit -m "feat(eis): time-series module skeleton (dataclass + endpoint helpers + stage1 group)"
```

---

## Task 2: Stage 2 — 끝점규칙 병합

**Files:**
- Modify: `battery_lab/eis_timeseries.py`
- Test: `tests/test_eis_timeseries.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
def _frags(*pairs):
    """pairs: (compact_sig, [hr ints]) -> list[(sig, [match])] for _merge_fragments."""
    out = []
    for sig, hrs in pairs:
        out.append((sig, [_m(f"{sig}_{h}hr.SEO", sig, f"{h}hr") for h in hrs]))
    return out


def _hrs_of(members):
    return sorted(ts.hr_num(m.time_point) for m in members)


def test_merge_left_and_right_fragment():
    # dl 2t2t: [0,1,2,3] + [4,5,8,24] -> one complete cell.
    res = ts._merge_fragments(_frags(("260521dl2t2t", [0, 1, 2, 3]),
                                     ("260521dl2t2t", [4, 5, 8, 24])))
    assert len(res) == 1
    assert _hrs_of(res[0]["members"]) == [0, 1, 2, 3, 4, 5, 8, 24]
    assert res[0]["provenance"]  # records what was merged


def test_keep_two_real_cells_with_two_zeros():
    # Both fragments start at 0hr -> two separate cells, never merged.
    res = ts._merge_fragments(_frags(("260603pure2t1", [0, 1, 2, 9]),
                                     ("260603pure2t2", [0, 1, 24])))
    assert len(res) == 2


def test_no_merge_on_overlapping_hours():
    # Disjoint requirement fails (both contain 3hr) -> stay separate, flagged later.
    res = ts._merge_fragments(_frags(("x", [0, 3]), ("x2", [3, 24])))
    assert len(res) == 2


def test_complete_group_passes_through_untouched():
    res = ts._merge_fragments(_frags(("c", [0, 6, 24])))
    assert len(res) == 1 and res[0]["provenance"] == ""
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_eis_timeseries.py -k merge -v`
Expected: FAIL — `_merge_fragments` 미정의.

- [ ] **Step 3: 구현 추가** (`eis_timeseries.py`, `_stage1_groups` 아래)

```python
def _hours(group: list[EISConditionMatch]) -> set[int | None]:
    return {hr_num(m.time_point) for m in group if m.time_point}


def _merge_fragments(sig_groups: list[tuple[str, list[EISConditionMatch]]]) -> list[dict[str, Any]]:
    """Endpoint-rule merge within one base signature.

    A 0-side fragment (has 0hr, no 24hr) merges with a 24-side fragment (has
    24hr, no 0hr) when their hour sets are disjoint. Complete groups (0 and 24)
    and groups with neither endpoint are passed through unchanged.
    """
    complete: list[list[EISConditionMatch]] = []
    left: list[tuple[str, list[EISConditionMatch], set[int | None]]] = []
    right: list[tuple[str, list[EISConditionMatch], set[int | None]]] = []
    neither: list[list[EISConditionMatch]] = []
    for sig, group in sig_groups:
        hours = _hours(group)
        has0, has24 = 0 in hours, 24 in hours
        if has0 and has24:
            complete.append(group)
        elif has0:
            left.append((sig, group, hours))
        elif has24:
            right.append((sig, group, hours))
        else:
            neither.append(group)

    results: list[dict[str, Any]] = [{"members": list(g), "provenance": ""} for g in complete]

    right_sorted = sorted(right, key=lambda x: x[0])
    used = set()
    for lsig, lgroup, lh in sorted(left, key=lambda x: x[0]):
        paired = None
        for j, (rsig, rgroup, rh) in enumerate(right_sorted):
            if j in used or (lh & rh):  # already taken, or overlapping hours
                continue
            paired = (j, rsig, rgroup, rh)
            break
        if paired is None:
            results.append({"members": list(lgroup), "provenance": ""})
            continue
        j, rsig, rgroup, rh = paired
        used.add(j)
        prov = f"{lsig}{_fmt_hrs(lh)}+{rsig}{_fmt_hrs(rh)}"
        results.append({"members": list(lgroup) + list(rgroup), "provenance": prov})

    for j, (rsig, rgroup, rh) in enumerate(right_sorted):
        if j not in used:
            results.append({"members": list(rgroup), "provenance": ""})
    for group in neither:
        results.append({"members": list(group), "provenance": ""})
    return results
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_eis_timeseries.py -k merge -v`
Expected: PASS (4 tests).

- [ ] **Step 5: 커밋**

```bash
git add battery_lab/eis_timeseries.py tests/test_eis_timeseries.py
git commit -m "feat(eis): endpoint-rule fragment merge (stage 2)"
```

---

## Task 3: Stage 3 — 신뢰도 분류 + 일지 행 점수가중 투표

**Files:**
- Modify: `battery_lab/eis_timeseries.py`
- Test: `tests/test_eis_timeseries.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
def test_cluster_dict_verified_complete_single_row():
    members = [
        _m("260610/pure 4t_2_0hr.SEO", "260610 pure 4t 2", "0hr", key="k1", sample="pure 4T", date="260610", delta=0, score=80),
        _m("260610/pure 4t_2_24hr.SEO", "260610 pure 4t 2", "24hr", key="k1", sample="pure 4T", date="260610", delta=0, score=80),
    ]
    conds = {"k1": {"_source_row_number": 510, "sample": "pure 4T", "date": "260610"}}
    c = ts._cluster_dict(members, "", conds)
    assert c["has_zero"] and c["has_24"]
    assert c["match_status"] == "verified"
    assert c["condition_key"] == "k1" and c["date_delta_days"] == 0
    assert c["time_points"] == "0hr;24hr"


def test_cluster_dict_ambiguous_when_endpoint_missing():
    members = [_m("260603/pure 5t_1_0hr.SEO", "260603 pure 5t 1", "0hr", key="k1", score=70),
               _m("260603/pure 5t_1_9hr.SEO", "260603 pure 5t 1", "9hr", key="k1", score=70)]
    conds = {"k1": {"_source_row_number": 300, "sample": "pure 5T", "date": "260603"}}
    c = ts._cluster_dict(members, "", conds)
    assert c["match_status"] == "ambiguous"
    assert "끝점" in c["reason"]


def test_cluster_dict_ambiguous_when_rows_compete():
    members = [_m("a/x_0hr.SEO", "g", "0hr", key="k1", score=70),
               _m("a/x_24hr.SEO", "g", "24hr", key="k2", score=68)]
    conds = {"k1": {"_source_row_number": 1}, "k2": {"_source_row_number": 2}}
    c = ts._cluster_dict(members, "", conds)
    assert c["match_status"] == "ambiguous"
    import json
    opts = json.loads(c["candidate_options"])
    assert {o["condition_key"] for o in opts} == {"k1", "k2"}
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_eis_timeseries.py -k cluster_dict -v`
Expected: FAIL — `_cluster_dict` 미정의.

- [ ] **Step 3: 구현 추가** (`eis_timeseries.py`)

```python
def _candidate_options(ranked: list[tuple[str, int]], meta: dict[str, EISConditionMatch],
                       conditions: dict[str, dict[str, Any]], *, max_rows: int = 8) -> str:
    options = []
    for key, weight in ranked[:max_rows]:
        cond = conditions.get(key, {})
        match = meta[key]
        options.append({
            "condition_key": key,
            "journal_row": cond.get("_source_row_number") or "",
            "sample": str(cond.get("sample") or match.condition_sample or key),
            "date": compact_date(cond.get("date")) or match.condition_date or "",
            "date_delta_days": match.date_delta_days,
            "score": int(weight),
        })
    return json.dumps(options, ensure_ascii=False)


def _cluster_dict(members: list[EISConditionMatch], provenance: str,
                  conditions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    paths = [m.relative_path for m in members]
    fdate = folder_date(members[0].relative_path)
    time_points = sorted({m.time_point for m in members if m.time_point}, key=time_sort_key)
    hours = {hr_num(t) for t in time_points}
    has_zero, has_24 = 0 in hours, 24 in hours

    votes: dict[str, int] = defaultdict(int)
    meta: dict[str, EISConditionMatch] = {}
    for m in members:
        if m.condition_key:
            votes[m.condition_key] += max(int(m.score), 1)
            meta.setdefault(m.condition_key, m)
    ranked = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))
    best_key = ranked[0][0] if ranked else ""
    best = meta.get(best_key)
    competing = len(ranked) > 1 and ranked[1][1] >= ranked[0][1] * REPLICATE_VOTE_RATIO

    if not (has_zero and has_24):
        status = "ambiguous"
        reason = "0hr/24hr 끝점이 불완전합니다(병합 후에도 한쪽 결손)."
    elif not best_key:
        status = "ambiguous"
        reason = "일지 행 후보를 찾지 못했습니다."
    elif competing:
        status = "ambiguous"
        reason = "멤버 파일들이 서로 다른 일지 행을 가리킵니다."
    else:
        row = conditions.get(best_key, {}).get("_source_row_number") or "?"
        status = "verified"
        reason = f"0hr→24hr 완비 + 단일 일지 행 {row} (파일 {len(members)}개)."
        if provenance:
            reason += f" 병합: {provenance}."

    return {
        "folder_date": fdate,
        "cluster_signature": compact_text(members[0].file_group_key),
        "member_paths": ";".join(sorted(paths)),
        "time_points": ";".join(time_points),
        "has_zero": has_zero,
        "has_24": has_24,
        "file_count": len(members),
        "merge_provenance": provenance,
        "condition_key": best_key,
        "condition_sample": str(best.condition_sample if best else ""),
        "condition_date": str(best.condition_date if best else ""),
        "date_delta_days": best.date_delta_days if best else None,
        "match_status": status,
        "candidate_options": _candidate_options(ranked, meta, conditions),
        "reason": reason,
    }
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_eis_timeseries.py -k cluster_dict -v`
Expected: PASS (3 tests).

- [ ] **Step 5: 커밋**

```bash
git add battery_lab/eis_timeseries.py tests/test_eis_timeseries.py
git commit -m "feat(eis): cluster classification + score-weighted journal vote (stage 3)"
```

---

## Task 4: 조립 — `build_time_series_clusters` + 1:1 충돌 검출

**Files:**
- Modify: `battery_lab/eis_timeseries.py`
- Test: `tests/test_eis_timeseries.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
def test_build_clusters_end_to_end_merges_and_ids():
    ms = [
        _m("260521/dl 2t2t_0hr.SEO", "260521 dl 2t2t", "0hr", key="k1", score=70),
        _m("260521/dl2t2t_24hr.SEO", "260521 dl2t2t", "24hr", key="k1", score=70),
    ]
    conds = {"k1": {"_source_row_number": 11, "sample": "dl 2t2t", "date": "260521"}}
    clusters = ts.build_time_series_clusters(ms, conds)
    assert len(clusters) == 1
    c = clusters[0]
    assert c.cluster_id == "TS001"
    assert c.time_points == "0hr;24hr" and c.has_zero and c.has_24
    assert c.match_status == "verified"


def test_build_clusters_flags_journal_row_conflict():
    # Two distinct complete cells both map to the same journal row -> both conflict.
    ms = [
        _m("a/c1_0hr.SEO", "260521 a 1", "0hr", key="k1", score=70),
        _m("a/c1_24hr.SEO", "260521 a 1", "24hr", key="k1", score=70),
        _m("a/c2_0hr.SEO", "260521 a 2", "0hr", key="k1", score=70),
        _m("a/c2_24hr.SEO", "260521 a 2", "24hr", key="k1", score=70),
    ]
    conds = {"k1": {"_source_row_number": 5, "sample": "a", "date": "260521"}}
    clusters = ts.build_time_series_clusters(ms, conds)
    assert len(clusters) == 2
    assert all(c.match_status == "conflict" for c in clusters)
    assert all("충돌" in c.reason for c in clusters)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_eis_timeseries.py -k build_clusters -v`
Expected: FAIL — `build_time_series_clusters` 미정의.

- [ ] **Step 3: 구현 추가** (`eis_timeseries.py`)

```python
def build_time_series_clusters(
    matches: list[EISConditionMatch],
    conditions: dict[str, dict[str, Any]],
) -> list[EISTimeSeriesCluster]:
    """Re-cluster EIS _hr files into one-cell (0hr->24hr) groups and map each to
    a journal row. Stage 1 collapses spacing splits; stage 2 merges 0-side and
    24-side fragments; stage 3 classifies and votes the journal row; finally a
    journal row claimed by >1 cluster marks those clusters as conflicts."""
    ts_matches = [m for m in matches if m.is_time_series]
    stage1 = _stage1_groups(ts_matches)

    by_base: dict[str, list[tuple[str, list[EISConditionMatch]]]] = defaultdict(list)
    for sig, group in stage1.items():
        by_base[_base_signature(sig)].append((sig, group))

    cluster_dicts: list[dict[str, Any]] = []
    for sig_groups in by_base.values():
        for item in _merge_fragments(sig_groups):
            cluster_dicts.append(_cluster_dict(item["members"], item["provenance"], conditions))

    cluster_dicts.sort(key=lambda c: (c["folder_date"], c["cluster_signature"], c["member_paths"]))

    row_counts = Counter(c["condition_key"] for c in cluster_dicts if c["condition_key"])
    clusters: list[EISTimeSeriesCluster] = []
    for idx, c in enumerate(cluster_dicts, start=1):
        status, reason = c["match_status"], c["reason"]
        if c["condition_key"] and row_counts[c["condition_key"]] > 1:
            status = "conflict"
            reason = f"같은 일지 행을 {row_counts[c['condition_key']]}개 클러스터가 차지(충돌). " + reason
        clusters.append(EISTimeSeriesCluster(
            cluster_id=f"TS{idx:03d}",
            folder_date=c["folder_date"],
            cluster_signature=c["cluster_signature"],
            member_paths=c["member_paths"],
            time_points=c["time_points"],
            has_zero=c["has_zero"],
            has_24=c["has_24"],
            file_count=c["file_count"],
            merge_provenance=c["merge_provenance"],
            condition_key=c["condition_key"],
            condition_sample=c["condition_sample"],
            condition_date=c["condition_date"],
            date_delta_days=c["date_delta_days"],
            match_status=status,
            candidate_options=c["candidate_options"],
            reason=reason,
        ))
    return clusters
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_eis_timeseries.py -v`
Expected: PASS (전체 모듈 테스트).

- [ ] **Step 5: 커밋**

```bash
git add battery_lab/eis_timeseries.py tests/test_eis_timeseries.py
git commit -m "feat(eis): assemble clusters with 1:1 journal-row conflict detection"
```

---

## Task 5: `eis_matching` 통합 — 리포트가 클러스터를 생성

**Files:**
- Modify: `battery_lab/eis_matching.py` (dataclass `EISTimeSeriesGroup` 삭제: 60–69행; `EISMatchReport.time_series_groups` 타입: 110행; `build_eis_match_report` 162행; `build_time_series_groups` 함수 409–433행 삭제)
- Test: `tests/test_eis_timeseries.py`

- [ ] **Step 1: 실패 테스트 추가** (`tests/test_eis_timeseries.py`)

```python
def test_report_uses_clusters(tmp_path):
    from pathlib import Path
    from battery_lab import eis_matching

    root = tmp_path
    (root / "260521").mkdir()
    for name in ("dl 2t2t_0hr_01.SEO", "dl2t2t_24hr_01.SEO"):
        (root / "260521" / name).write_text("x", encoding="utf-8")
    conditions = {"k1": {"_source_row_number": 11, "sample": "dl 2t2t",
                         "date": "260521", "cell_id": "dl 2t2t"}}
    paths = list((root / "260521").glob("*.SEO"))
    report = eis_matching.build_eis_match_report(paths, conditions, root)
    assert all(isinstance(g, ts.EISTimeSeriesCluster) for g in report.time_series_groups)
    assert len(report.time_series_groups) == 1
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_eis_timeseries.py -k report_uses_clusters -v`
Expected: FAIL — `report.time_series_groups`가 여전히 `EISTimeSeriesGroup`.

- [ ] **Step 3: `eis_matching.py` 수정**

3a. `EISTimeSeriesGroup` dataclass(60–69행) 삭제.

3b. 상단 import 영역(16행 `from .file_io import ...` 아래)에 지연 import 주석 추가 — 실제 import는 함수 내부에서:
```python
# NOTE: eis_timeseries imports from this module, so import it lazily inside
# build_eis_match_report to avoid a circular import at module load.
```

3c. `EISMatchReport.time_series_groups` 타입(110행)을 변경:
```python
    time_series_groups: list["EISTimeSeriesCluster"]
```
그리고 파일 상단 import 블록에 `from typing import TYPE_CHECKING`이 없으면 추가하고:
```python
if TYPE_CHECKING:
    from .eis_timeseries import EISTimeSeriesCluster
```

3d. `build_eis_match_report`의 162행 `time_groups = build_time_series_groups(matches)` 를 교체:
```python
    from .eis_timeseries import build_time_series_clusters
    time_groups = build_time_series_clusters(matches, conditions)
```

3e. `build_time_series_groups` 함수 전체(409–433행) 삭제.

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `.venv/bin/python -m pytest tests/test_eis_timeseries.py -k report_uses_clusters -v`
Expected: PASS.
Run: `.venv/bin/python -m pytest -q`
Expected: 기존 통과 유지(아래 Task 6에서 갱신할 deferred 단언 제외 — 이 시점에 실패하면 Task 6에서 처리). EIS 실데이터 의존 테스트는 skip 가능.

- [ ] **Step 5: 커밋**

```bash
git add battery_lab/eis_matching.py tests/test_eis_timeseries.py
git commit -m "refactor(eis): report builds time-series clusters via eis_timeseries"
```

---

## Task 6: `verification_payload` 통합 — deferred_rows = 클러스터

**Files:**
- Modify: `battery_lab/matching_service.py` (`verification_payload` 469–562행)
- Modify: `tests/test_matching_verification.py` (190–201행 `test_verification_payload_defers_eis_time_series` 갱신)

- [ ] **Step 1: 기존 테스트를 클러스터 형태로 갱신** — `tests/test_matching_verification.py`의 `test_verification_payload_defers_eis_time_series`를 교체:

```python
def test_verification_payload_clusters_eis_time_series():
    if not config.BATTERY_EIS_ROOT.exists() or not config.BATTERY_CONDITION_WORKBOOK.exists():
        pytest.skip("real EIS data / workbook not present")
    p = matching_service.verification_payload(
        "eis", config.BATTERY_EIS_ROOT, config.BATTERY_CONDITION_WORKBOOK,
        config.BATTERY_MATCH_EIS_JSON, condition_sheet="JYJ",
    )
    clusters = p["deferred_rows"]
    assert p["summary"]["time_series_clusters"] == len(clusters)
    # Re-clustering must reduce the 43-group / many-missing-endpoint baseline.
    assert len(clusters) < 43
    missing_endpoint = [c for c in clusters if not (c["has_zero"] and c["has_24"])]
    assert len(missing_endpoint) < 37   # was 16 missing-0 + 21 missing-24 across 43 groups
    assert all("member_paths" in c and "match_status" in c for c in clusters)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_matching_verification.py -k clusters_eis_time_series -v`
Expected: FAIL (실데이터 있으면) — `time_series_clusters` 키 없음 / deferred_rows가 파일별 dict. 실데이터 없으면 skip이므로, 이 경우 Step 4의 합성 테스트로 검증.

- [ ] **Step 3: `verification_payload` 수정**

3a. 루프(504–518행)에서 시계열 분기 제거 — `if vrow.get("is_time_series"): deferred_rows.append(vrow); continue` 블록(510–512행)을 삭제하고, 시계열 파일은 메인 rows에서도 빠지도록 변경:
```python
    for m in matches:
        if m["is_time_series"]:
            continue  # handled as clusters below
        vrow = _verification_row(kind, m, in_scope_conditions)
        override = overrides.get(vrow["relative_path"]) or {}
        vrow["override_source"] = str(override.get("selection_source") or ("manual" if override else ""))
        if vrow["status"] == "unmatched" or not vrow["condition_key"]:
            unmatched_files.append(vrow["relative_path"])
            continue
        rows.append(vrow)
        if vrow["journal_row"] not in ("", None):
            used.setdefault(vrow["journal_row"], []).append(vrow["relative_path"])
```
(`matches`는 `[asdict(row) ...]`이므로 `m["is_time_series"]`로 접근.)

3b. `deferred_rows` 생성을 리포트 클러스터에서 가져오도록 교체 — 501행 `deferred_rows: list[...] = []` 뒤, 루프 끝난 직후에:
```python
    deferred_rows = [asdict(cluster) for cluster in (report.time_series_groups if report else [])]
```

3c. `deferred_rows.sort(...)`(537행)을 클러스터 키 기준으로 교체:
```python
    cluster_order = {"conflict": 0, "ambiguous": 1, "verified": 2}
    deferred_rows.sort(key=lambda c: (cluster_order.get(c["match_status"], 9), c["folder_date"], c["cluster_signature"]))
```

3d. summary(546–555행) `"deferred_time_series": len(deferred_rows),` 를 교체:
```python
            "time_series_clusters": len(deferred_rows),
            "time_series_verified": sum(1 for c in deferred_rows if c["match_status"] == "verified"),
            "time_series_needs_review": sum(1 for c in deferred_rows if c["match_status"] in ("ambiguous", "conflict")),
```

- [ ] **Step 4: 합성 데이터 단언 추가** (실데이터 없는 CI 대비) — `tests/test_matching_verification.py`에 추가:

```python
def test_verification_payload_time_series_clusters_synthetic(tmp_path, monkeypatch):
    import openpyxl
    wb = openpyxl.Workbook(); wsx = wb.active
    wsx.append(["sample", "참고", "전해질", "종류", "Binder", "Voltage range", "date"])
    wsx.append(["dl 2t2t", "12파이_Cu foil", "1.0M LiPF6 EC/DEC 1:1", "LIB", "2wt% cmc", "0.01~2V", "260521"])
    wb_path = tmp_path / "cond.xlsx"; wb.save(wb_path)
    eis_root = tmp_path / "EIS" / "260521"; eis_root.mkdir(parents=True)
    for name in ("dl 2t2t_0hr_01.SEO", "dl2t2t_24hr_01.SEO"):
        (eis_root / name).write_text("x", encoding="utf-8")
    ov = tmp_path / "ov.json"
    p = matching_service.verification_payload(
        "eis", tmp_path / "EIS", wb_path, ov, condition_sheet=wsx.title)
    assert p["summary"]["time_series_clusters"] == len(p["deferred_rows"]) >= 1
    assert all("has_zero" in c for c in p["deferred_rows"])
```

- [ ] **Step 5: 통과 + 회귀 확인**

Run: `.venv/bin/python -m pytest tests/test_matching_verification.py -v`
Expected: PASS (실데이터 의존 테스트는 환경에 따라 skip).
Run: `.venv/bin/python -m pytest -q`
Expected: 전체 통과(이전 97 이상).

- [ ] **Step 6: 커밋**

```bash
git add battery_lab/matching_service.py tests/test_matching_verification.py
git commit -m "feat(eis): verification payload exposes time-series clusters"
```

---

## Task 7: 검증뷰 + 체크리스트 클러스터 렌더

**Files:**
- Modify: `battery_lab/verification_view.py`
- Modify: `battery_lab/checklist_view.py`
- Test: `tests/test_matching_verification.py`

**선행:** 두 파일을 먼저 Read해 기존 `deferred_rows` 렌더 섹션과 행 dict 사용 방식을 확인하고, 동일 패턴(테이블/배지/접이식)을 따른다.

- [ ] **Step 1: 실패 테스트 추가** — `tests/test_matching_verification.py`

```python
def test_render_verification_html_shows_time_series_clusters():
    from battery_lab import verification_view
    payloads = {"eis": {"kind": "eis",
        "summary": {"in_scope_rows": 125, "matched_files": 28, "needs_review": 0,
                    "ambiguous_files": 0, "unmatched_files": 0, "orphan_rows": 0,
                    "duplicate_groups": 0, "time_series_clusters": 2,
                    "time_series_verified": 1, "time_series_needs_review": 1},
        "rows": [], "orphans": [], "invariant": {"ambiguous": [], "duplicates": [], "unmatched_count": 0},
        "deferred_rows": [
            {"cluster_id": "TS001", "folder_date": "260521", "cluster_signature": "260521dl2t2t",
             "member_paths": "260521/dl 2t2t_0hr_01.SEO;260521/dl2t2t_24hr_01.SEO",
             "time_points": "0hr;24hr", "has_zero": True, "has_24": True, "file_count": 2,
             "merge_provenance": "260521dl2t2t[0]+260521dl2t2t[24]", "condition_key": "k1",
             "condition_sample": "dl 2t2t", "condition_date": "260521", "date_delta_days": 0,
             "match_status": "verified", "candidate_options": "[]",
             "reason": "0hr→24hr 완비 + 단일 일지 행 11 (파일 2개)."},
            {"cluster_id": "TS002", "folder_date": "260603", "cluster_signature": "260603pure5t1",
             "member_paths": "260603/pure 5t_1_0hr.SEO", "time_points": "0hr;9hr",
             "has_zero": True, "has_24": False, "file_count": 5, "merge_provenance": "",
             "condition_key": "k2", "condition_sample": "pure 5T", "condition_date": "260603",
             "date_delta_days": 0, "match_status": "ambiguous", "candidate_options": "[]",
             "reason": "0hr/24hr 끝점이 불완전합니다(병합 후에도 한쪽 결손)."},
        ]}}
    html = verification_view.render_verification_html(payloads)
    assert "시계열" in html                          # time-series section header
    assert "TS001" in html and "0hr;24hr" in html
    assert "260521dl2t2t[0]+260521dl2t2t[24]" in html  # merge provenance shown
    assert "끝점이 불완전" in html                    # ambiguous reason shown


def test_render_checklist_html_offers_cluster_candidates():
    from battery_lab import checklist_view
    payloads = {"eis": {"kind": "eis", "summary": {}, "orphans": [], "rows": [], "deferred_rows": [
        {"cluster_id": "TS002", "folder_date": "260603", "cluster_signature": "260603pure5t1",
         "member_paths": "260603/pure 5t_1_0hr.SEO;260603/pure 5t_1_9hr.SEO", "time_points": "0hr;9hr",
         "has_zero": True, "has_24": False, "file_count": 2, "merge_provenance": "",
         "condition_key": "k2", "condition_sample": "pure 5T", "condition_date": "260603",
         "date_delta_days": 0, "match_status": "ambiguous",
         "candidate_options": "[{\"condition_key\": \"k2\", \"journal_row\": 300, \"sample\": \"pure 5T\", \"date\": \"260603\", \"date_delta_days\": 0, \"score\": 140}]",
         "reason": "끝점 불완전"},
    ]}}
    html = checklist_view.render_checklist_html(payloads)
    assert "TS002" in html
    assert 'data-cluster="TS002"' in html      # cluster-scoped answer control
    assert "행 300" in html                     # candidate journal row offered
    assert "__delete__" in html                 # delete option available for clusters too
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_matching_verification.py -k "time_series_clusters or cluster_candidates" -v`
Expected: FAIL — 뷰가 클러스터 섹션을 아직 렌더하지 않음.

- [ ] **Step 3: `verification_view.py` 수정** — 기존 `deferred_rows` 접이식 섹션을 클러스터 표로 교체. 컬럼: cluster_id · folder_date · time_points(끝점 강조) · file_count · 매칭행(condition_sample/날짜) · 상태배지 · 병합근거(merge_provenance) · reason. summary 줄에 `time_series_clusters`/`time_series_verified`/`time_series_needs_review` 노출. (기존 `render_verification_html`의 capacity rows 테이블 헬퍼를 재사용하되 컬럼만 클러스터용으로.)

- [ ] **Step 4: `checklist_view.py` 수정** — 시계열 클러스터를 행 단위 대신 클러스터 단위 항목으로 렌더. 각 클러스터에 `<select class="ans" data-cluster="TS00N">` 드롭다운(후보 일지행 `행 {journal_row}` + `__delete__` + `__skip__`), 멤버 파일 목록·time_points·끝점 상태·merge_provenance를 보조 정보로 표시. `verified` 클러스터는 접어서 스팟체크용으로. localStorage 키는 기존(`battery_matching_checklist_v1`) 유지하되 클러스터는 `data-cluster`로 식별.

- [ ] **Step 5: 통과 + 회귀 확인**

Run: `.venv/bin/python -m pytest tests/test_matching_verification.py -v`
Expected: PASS.
Run: `.venv/bin/python -m pytest -q`
Expected: 전체 통과.

- [ ] **Step 6: 커밋**

```bash
git add battery_lab/verification_view.py battery_lab/checklist_view.py tests/test_matching_verification.py
git commit -m "feat(eis): render time-series clusters in verification + checklist views"
```

---

## Task 8: 클러스터 단위 체크리스트 회신 적용

**Files:**
- Modify: `battery_lab/matching_service.py` (`apply_checklist_answers` 565–이하)
- Test: `tests/test_matching_verification.py`

**배경:** override는 파일(relative_path)별로 키잉된다. 담당자가 클러스터 1개에 대해 일지 행을 확정하면, 그 클러스터의 **모든 멤버 파일**에 동일 override를 써야 한다. 회신 blob에 클러스터→멤버경로 매핑이 필요하므로, 체크리스트가 내보내는 answer에 `members`(또는 cluster→paths) 정보를 포함시키고 apply가 이를 펼친다.

- [ ] **Step 1: 실패 테스트 추가** — `tests/test_matching_verification.py`

```python
def test_apply_checklist_answers_cluster_fans_out_to_members(tmp_path):
    import json as _json
    csv = tmp_path / "cond.csv"
    csv.write_text(
        "sample,참고,전해질,종류,Binder,Voltage range\n"
        "pure 5T,12파이_Cu foil,1.0M LiPF6 EC/DEC 1:1,LIB,2wt% cmc,0.01~2V\n",
        encoding="utf-8")
    ov = tmp_path / "ov.json"
    answers = {"version": 1, "answers": {
        "TS002": {"choice": "pure 5T", "memo": "셀1",
                  "members": ["260603/pure 5t_1_0hr.SEO", "260603/pure 5t_1_9hr.SEO"]},
    }}
    res = matching_service.apply_checklist_answers(answers, csv, ov)
    assert res["applied"] == 2          # both member files written
    saved = _json.loads(ov.read_text(encoding="utf-8"))
    assert saved["260603/pure 5t_1_0hr.SEO"]["condition_key"] == "pure 5T"
    assert saved["260603/pure 5t_1_9hr.SEO"]["condition_key"] == "pure 5T"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_matching_verification.py -k cluster_fans_out -v`
Expected: FAIL — `members` 펼침 미구현(클러스터 키 "TS002"가 파일로 처리됨).

- [ ] **Step 3: `apply_checklist_answers` 수정** — 루프에서 `members`가 있으면 각 멤버 경로로 펼쳐 override를 쓰고, 없으면 기존 단일 파일 동작 유지:

```python
    for file_key, ans in (data or {}).items():
        ans = ans or {}
        choice = str(ans.get("choice") or "").strip()
        memo = str(ans.get("memo") or "")
        members = ans.get("members") or [file_key]   # cluster fans out to member files
        if not choice or choice == "__skip__":
            skipped += 1
            continue
        # ... resolve condition_key / __delete__ exactly as today, but iterate `members`:
        for path in members:
            # (apply the same per-file write the current code does for `file_key`)
            ...
```
(기존 `applied/deleted/unknown` 카운팅은 멤버별로 누적. 단일 파일은 `members == [file_key]`라 동작 불변.)

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `.venv/bin/python -m pytest tests/test_matching_verification.py -k checklist -v`
Expected: PASS (기존 `test_apply_checklist_answers_roundtrip` 포함 — 단일 파일 동작 불변).
Run: `.venv/bin/python -m pytest -q`
Expected: 전체 통과.

- [ ] **Step 5: 커밋**

```bash
git add battery_lab/matching_service.py tests/test_matching_verification.py
git commit -m "feat(eis): checklist answers fan out from cluster to member files"
```

---

## Task 9: 실데이터 회귀 스냅샷 + 진단 재실행

**Files:**
- Modify: `scripts/diagnose_eis_timeseries.py` (재클러스터링 결과도 출력하도록 확장)
- Test: `tests/test_eis_timeseries.py`

- [ ] **Step 1: 실데이터 회귀 테스트 추가** — `tests/test_eis_timeseries.py`

```python
def test_real_data_recluster_reduces_fragmentation():
    import pytest
    from battery_lab import config
    from battery_lab.conditions import read_conditions
    from battery_lab.eis_matching import match_eis_files_to_conditions
    from battery_lab.matching_service import collect_source_files, EIS_SUFFIXES
    from battery_lab import scope

    if not config.BATTERY_EIS_ROOT.exists() or not config.BATTERY_CONDITION_WORKBOOK.exists():
        pytest.skip("real EIS data / workbook not present")
    conds = scope.filter_in_scope(read_conditions(config.BATTERY_CONDITION_WORKBOOK, sheet_name="JYJ"))
    paths = collect_source_files(config.BATTERY_EIS_ROOT, EIS_SUFFIXES)
    _, matches = match_eis_files_to_conditions(paths, conds, config.BATTERY_EIS_ROOT)
    clusters = ts.build_time_series_clusters(matches, conds)
    # Baseline before reclustering: 43 groups, 16 missing 0hr + 21 missing 24hr.
    assert len(clusters) < 43
    missing = [c for c in clusters if not (c.has_zero and c.has_24)]
    assert len(missing) < 30
    # The dl/pc73 spacing splits must now be single complete clusters.
    by_sig = {c.cluster_signature: c for c in clusters}
    assert all(by_sig[s].has_zero and by_sig[s].has_24
               for s in by_sig if s.endswith("dl2t2t") or s.endswith("dl3t3t"))
```

- [ ] **Step 2: 실패/통과 확인**

Run: `.venv/bin/python -m pytest tests/test_eis_timeseries.py -k real_data_recluster -v`
Expected: 실데이터 있으면 PASS(알고리즘이 맞다면). 없으면 skip. FAIL 시 → systematic-debugging으로 실제 클러스터 출력 점검(아래 진단 스크립트).

- [ ] **Step 3: 진단 스크립트 확장** — `scripts/diagnose_eis_timeseries.py` 끝에 재클러스터링 결과 표(섹션 (d))를 추가: `build_time_series_clusters` 호출 → cluster_id·time_points·has_zero/has_24·match_status·merge_provenance·condition_key 출력. before/after(43그룹 → N클러스터, 끝점결손 수) 요약.

```python
    # append after existing sections
    from battery_lab.eis_matching import match_eis_files_to_conditions
    from battery_lab.conditions import read_conditions
    from battery_lab.config import BATTERY_CONDITION_WORKBOOK
    from battery_lab import scope, eis_timeseries
    conds = scope.filter_in_scope(read_conditions(BATTERY_CONDITION_WORKBOOK, sheet_name="JYJ")) \
        if BATTERY_CONDITION_WORKBOOK.exists() else {}
    _, matches = match_eis_files_to_conditions(paths, conds, root)
    clusters = eis_timeseries.build_time_series_clusters(matches, conds)
    print(f"\n=== (d) reclustered: {len(clusters)} clusters (was {len(groups)} groups) ===")
    miss = sum(1 for c in clusters if not (c.has_zero and c.has_24))
    print(f"clusters missing an endpoint: {miss}\n")
    for c in clusters:
        flag = "" if (c.has_zero and c.has_24) else "  <-- INCOMPLETE"
        prov = f"  merge={c.merge_provenance}" if c.merge_provenance else ""
        print(f"{c.cluster_id} {c.cluster_signature:30s} {c.match_status:9s} [{c.time_points}]{flag}{prov}")
```

- [ ] **Step 4: 실행 + 눈으로 검증**

Run: `PYTHONPATH=$PWD .venv/bin/python scripts/diagnose_eis_timeseries.py`
Expected: (d) 섹션에 클러스터 수가 43보다 작고, dl/pc73 케이스가 완전체로 합쳐졌으며, 끝점 결손이 크게 감소. 남은 INCOMPLETE/conflict 클러스터를 눈으로 확인해 체크리스트 대상으로 타당한지 점검.

- [ ] **Step 5: 전체 테스트 + 커밋**

Run: `.venv/bin/python -m pytest -q`
Expected: 전체 통과(97 이상).

```bash
git add scripts/diagnose_eis_timeseries.py tests/test_eis_timeseries.py
git commit -m "test(eis): real-data recluster regression + diagnostic before/after"
```

---

## Self-Review 결과 (작성자 체크)

- **Spec 커버리지:** §4 모듈경계→T1/T5, §5 1단계→T1, 2단계→T2, 3단계→T3, §6 일지매핑→T3, 1:1 불변식→T4, §7 신뢰도→T3/T4, §8 데이터모델→T1/T5, 검증뷰/체크리스트→T7/T8, §9 테스트→각 Task + T9. 누락 없음.
- **Placeholder:** T7/T8의 뷰·apply 수정은 "기존 파일 패턴을 따른다"는 지시 + 정확한 실패 테스트(기대 문자열 명시)로 계약을 고정 — 핵심 알고리즘(T1–T4)은 완전 코드. T7/T8 구현 전 해당 파일 Read 선행을 명시.
- **타입 일관성:** `EISTimeSeriesCluster` 필드명이 T1 정의 ↔ T3 `_cluster_dict` dict 키 ↔ T4 생성자 ↔ T6/T7 소비에서 일치. `build_time_series_clusters(matches, conditions)` 시그니처가 T4 정의 ↔ T5 호출 ↔ T9 호출에서 일치.
