# EIS 시계열 재클러스터링 + 일지 매핑 설계

- 작성: 2026-06-24
- 리포: `battery-lab-automation/` · 브랜치 `phase1-render-cache`
- 선행 문서: `2026-06-24-matching-verification-design.md`, 인계장 `docs/superpowers/HANDOFF.md` §0

## 1. 문제 (실데이터로 확증됨)

EIS `_hr` 시계열 264파일이 현재 43그룹으로 묶이는데, **16그룹은 0hr 끝점이 없고 21그룹은 24hr 끝점이 없다.** 끝점이 없는 그룹들은 서로 짝지으면 0→24 완전체가 된다. 즉 원래 한 셀(0hr→24hr 한 시계열)이 잘못 분할됐다.

진단 스크립트: `scripts/diagnose_eis_timeseries.py` (read-only, 현재 그룹핑 재현).

원인 두 가지:
1. **정규화 불일치** — 파일명 띄어쓰기 차이(`"pc 73 3t"` vs `"pc73 3t"`, `"dl 2t2t"` vs `"dl2t2t"`)가 같은 셀을 별개 그룹 키로 쪼갬.
2. **복제 인덱스 잡음** — 셀 번호(`_1`/`_2`/`_4`)가 채널 접미사처럼 들쭉날쭉 붙어 키가 더 갈라짐. 예: `pc73 3t 4 = [0hr]` + `pc73 3t 2 = [1hr..24hr]`이 사실 같은 셀.

증거 (현재 그룹 → 합쳐야 할 짝):

| 잘못 쪼개진 두 그룹 | time points | 합치면 |
|---|---|---|
| `dl 2t2t` / `dl2t2t` | `[0,1,2,3]` + `[4,5,8,9,21,22,24]` | `[0…24]` 완전체 |
| `dl 3t3t` / `dl3t3t` | `[0,1,2,3]` + `[4,5,8,9,21,22,24]` | `[0…24]` 완전체 |
| `pc 73 3t` / `pc73 3t` | `[0,1]` + `[2…24]` | `[0…24]` 완전체 |
| `pc 73 4t` / `pc73 4t` | `[0]` + `[1…24]` | `[0…24]` 완전체 |

반례(합치면 안 되는 진짜 별개 셀): `260603 1.5act 2t 1 = [0…9]`, `1.5act 2t 2 = [0…24]` — **둘 다 0hr 시작** → 별개 셀 2개. 0hr 두 개를 한 클러스터에 못 넣는다.

## 2. 핵심 원리: 0hr→24hr 규칙을 병합/분리 오라클로

모든 클러스터는 항상 0hr 시작·24hr 끝을 가진다(사용자 도메인 지식). 그 사이 hr 개수는 셀마다 조금 다를 수 있다. 따라서:
- 한 그룹이 **0hr 있고 24hr 없음**(좌측 조각) + 다른 그룹이 **24hr 있고 0hr 없음**(우측 조각) + hr 구간이 안 겹침 → 같은 셀이 쪼개진 것 → **병합**.
- 두 그룹이 **둘 다 0hr**(시작 2개) 또는 **둘 다 24hr** → 별개 셀 → **병합 금지**.

## 3. 정책 결정 (확정)

- **자동병합 + 애매한 건 체크리스트**: 0→24 규칙으로 고신뢰 병합은 자동. hr 겹침·고아 조각·끝점 결손 잔류 등 모호한 케이스만 기존 담당자 체크리스트로.
- **범위**: 재클러스터링 + 클러스터↔일지 행 1:1 매핑 + 검증뷰 노출을 한 스펙으로.

## 4. 아키텍처 / 모듈 경계

`eis_matching.py`(781줄)는 그대로 두고, 재클러스터링+일지매핑을 **새 모듈 `battery_lab/eis_timeseries.py`**로 분리한다.

- `eis_matching.build_eis_match_report`가 기존 `build_time_series_groups(matches)` 대신 새 모듈의 진입함수를 호출.
- 입력: 이미 계산된 개별 `EISConditionMatch` 리스트(시계열만) + `EISFileInventory`(folder_date·cell_key·time_point) + `conditions`.
- 출력: 풍부해진 `EISTimeSeriesCluster` 리스트.
- **개별 파일 매칭/점수 로직(`match_inventory_item`, `score_condition_candidate` 등)은 건드리지 않는다** — 회귀 위험 최소화.

## 5. 재클러스터링 알고리즘 (3단계)

입력 단위: 각 시계열 파일 = `(folder_date, cell_key, time_point, 개별 condition_match)`.

**1단계 — 정규화 키로 1차 그룹.**
- `cluster_signature = folder_date + compact(cell_key)`. `compact_text`(공백·기호 제거)로 `"pc 73 3t"`·`"pc73 3t"` → `pc733t`. 두께(`3t`/`4t`)·복제숫자는 글자로 남아 다른 셀은 안 섞임.
- 순수 띄어쓰기 분할은 여기서 제거됨.

**2단계 — 끝점규칙 병합.** 같은 `folder_date + base(두께 포함, 복제 인덱스 제외)` 묶음 안에서 후보쌍 검사:
- 병합 조건(전부 충족 → 자동): ① hr 집합 disjoint, ② 한쪽 좌측 조각(0hr 있고 24hr 없음) + 다른쪽 우측 조각(24hr 있고 0hr 없음), ③ 합집합이 0→24 단조 시퀀스로 그럴듯함.
- 병합 금지: 둘 다 0hr, 또는 둘 다 24hr.
- 좌/우 조각이 여러 개면 그리디로 1:1 짝지음(좌측 1개당 우측 1개). 짝이 안 맞으면 잔여는 3단계로.

**3단계 — 잔여 분류.**
- 병합 후 0hr·24hr 둘 다 → `complete`(자동 확정 후보).
- hr 겹치는 병합후보 / 고아 단일 조각 / 병합해도 끝점 결손 → `ambiguous`(체크리스트).

각 클러스터는 멤버 파일·time_points·끝점유무·병합근거(merge_provenance)를 보존한다.

## 6. 클러스터 → 일지 행 1:1 매핑

- 멤버 파일은 이미 개별 `condition_key`(점수·date_delta)를 가진다. 클러스터 단위 **점수 가중 다수결**로 최우선 후보 행 선정 → date_delta·재질 서명으로 검증.
- 멤버들이 서로 다른 행을 강하게 가리키면(경쟁) → `ambiguous`.
- **1:1 불변식**: in-scope 일지 행 1개 ↔ 클러스터 최대 1개. 두 클러스터가 같은 행 → 둘 다 `conflict`(체크리스트). 같은 셀의 재측정은 한 클러스터 안에 들어와야 정상.

## 7. 신뢰도 분류 (클러스터당)

| 등급 | 조건 | 처리 |
|---|---|---|
| `verified` | 0hr+24hr 완비 + 단일 우세 일지행 + 충돌 없음 | 자동 확정 |
| `ambiguous` | 끝점 결손 / hr겹침 병합후보 / 경쟁행 / 고아조각 | 체크리스트 |
| `conflict` | 두 클러스터가 동일 일지행 차지 | 체크리스트 |

체크리스트 회신은 기존 `apply_checklist_answers` + `eis_match_overrides.json` 스키마 재사용(시계열용 분기 추가). 검증뷰에 시계열 클러스터 섹션 추가(멤버 파일·끝점·매칭행·병합근거 표시) — 더 이상 단순 `deferred`가 아님.

## 8. 데이터 모델 변경

`EISTimeSeriesGroup` → `EISTimeSeriesCluster`로 확장/교체:

```
cluster_id, folder_date, cluster_signature, member_paths, time_points,
has_zero, has_24, file_count,
merge_provenance,      # 예: "pc733t4[0]+pc733t2[1..24]"
condition_key, condition_sample, condition_date, date_delta_days,
match_status,          # verified | ambiguous | conflict
candidate_options,     # 체크리스트 드롭다운용 후보 일지행 (기존 JSON 스키마)
reason
```

`EISMatchReport.time_series_groups` 자리에 이 리스트가 들어가고, `matching_service.verification_payload`의 `deferred_rows`를 이 클러스터들로 대체. CSV 출력(`eis_time_series_groups.csv`)도 새 필드 반영.

## 9. 테스트 전략 (TDD)

실데이터 파일명에서 뽑은 시나리오를 합성 픽스처로:

1. 띄어쓰기 분할 병합: `dl 2t2t`+`dl2t2t` → 1클러스터 `[0…24]`, `verified`.
2. 복제숫자 잡음 병합: `pc733t4=[0]`+`pc733t2=[1…24]` → 1클러스터.
3. 별개 셀 보존: 둘 다 0hr(`2t 1`,`2t 2`) → 2클러스터, 병합 안 됨.
4. 끝점 결손 잔류: 병합해도 24hr 없음 → `ambiguous` + 체크리스트 후보.
5. 고아 `[6hr]` 단독 → `ambiguous`.
6. 일지 1:1 충돌: 두 클러스터가 같은 행 → 둘 다 `conflict`.
7. 회귀(전체 264파일 스냅샷): 클러스터 수 감소(43↓) + 0hr·24hr 결손 클러스터 수 큰 폭 감소.

기존 `tests/test_matching_verification.py`(17개) 통과 유지. 전체 `pytest -q` 97 passed 유지/증가.

## 10. 비범위 (YAGNI)

- 파일 리네임/표준화/업로드/자동기입 없음 (B 트랙 대원칙 유지).
- 개별 파일 점수 알고리즘 변경 없음.
- `RISKY_REVIEW_STATUSES`의 `manual` nuance 등 기존 미해결 지표는 별도(인계장 §5).
