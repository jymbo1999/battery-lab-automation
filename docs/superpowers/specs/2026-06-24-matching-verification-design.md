# 스코프 매칭 검증 (Matching Verification) — 설계 문서

- 날짜: 2026-06-24
- 상태: 설계 승인됨 (구현 plan 작성 대기). 사용자 부재(야간) 중 자율 진행 승인.
- 대체: `docs/superpowers/specs/2026-06-23-metadata-schema-design.md`(표준화·리네임 중심)를 **supersede**. 방향 전환 사유: 사용자가 파일 리네임/표준화/업로드/자동기입에 거부감 → 워크플로우 불변, **매칭 정확도 + 검증 신뢰**만 개선.

---

## 1. 배경

파일명이 사람이 즉흥적으로 지은 형태라 일지(`Cell condition Calculation.xlsx`의 `JYJ` 시트)와의 매칭이 퍼지·위치 기반이라 자주 어긋난다. 사용자(연구주도자)는:
- 파일을 **리네임하지 않고** 폴더 tree에 기존 형식 그대로 둔다.
- 일지도 **지금 양식 그대로** 다음 행에 직접 기입한다.
- 규칙기반 자동수정이 예외를 놓칠까봐 두렵다 → **모든 파일에 대해 "왜 이 행에 매칭됐는지" 근거를 보고 안심**하고 싶다.

따라서 본 작업의 목표는 단 하나: **파일 ↔ 일지 행을 1:1로 정확히 매칭하고, 그 근거를 verified 포함 전부 표로 보여줘 사용자가 이중 검증**하게 한다. (물리적으로 한 행=한 셀은 EIS·capacity 등 여러 파일을 가지므로 "1:1"은 *각 파일이 정확히 한 행에 모호함 없이 귀속*을 뜻한다. 행:파일 = 1:N 정상.)

---

## 2. 목표 / 비목표

### 목표
- **스코프 한정:** in-scope 활성 행(아래 §3 필터, 현재 125행)과 그 파일들만 매칭 대상으로 삼는다.
- **매칭 개선:** 스코핑 + 값 정규화로 표기흔들림發 false `blocked`/`review`를 제거해 1:1 정확도를 올린다.
- **검증 UI:** in-scope **모든 파일**에 매칭 근거표(verified 포함)를 제공.
- **1:1 불변식 표면화:** unmatched(0행)·ambiguous(≥2후보)·고아 활성행(파일 0개)·중복(같은 analysis_type 2파일)을 명시.
- **수동 확정 보존:** 기존 `overrides.json` + `review_EIS_capacity` 탭을 이 검증뷰로 진화(폐기 아님).

### 비목표 (명시적 제외)
- 파일 **리네임/표준 파일명** 생성·적용
- 업로드 UI, 일지 자동 행추가
- 메타데이터 스키마/통제어휘 표준화(vocabulary.json 등)를 *산출물*로 만들기 — 정규화는 매칭 내부 보조로만 쓰고 사용자에게 노출/강제하지 않음
- 일지/폴더 구조 변경

---

## 3. 스코프 정의 (기존 코드 재사용)

`excel_dashboard.py`에 **이미 구현된** 필터를 단일 진실원으로 재사용한다:
- `FILTER_RULES` — `참고=12파이_Cu foil · 전해질=1.0M LiPF6 EC/DEC 1:1 · 종류=LIB · Binder∈{2wt%cmc, 2wt%cmc/40wt%SBR} · Voltage range=0.01~2V`
- `normalize_filter_value(v)` = 소문자 + 전체 공백제거 (표기흔들림 흡수: `2wt% cmc`→`2wt%cmc`).
- `row_matches_filter(ws, row, header_map)` → 5규칙 모두 통과해야 in-scope. 일지 뷰의 `무시행 표시안함/회색` 토글(`filterMode`)이 이미 이 필터로 동작.

**변경점:** 현재 매칭은 일지 **전체 행**을 후보로 쓴다(out-of-scope 행이 노이즈로 끼어 false 매칭 유발). 본 작업은 **매칭 후보를 in-scope 행으로 한정**한다.

- 구현: `FILTER_RULES`/필터 로직을 워크시트 의존에서 떼어 **conditions(dict) 위에서 동작하는 공유 함수**로 추출 → `scope.in_scope(condition) -> bool`. 매칭 리포트가 conditions를 받기 전에 in-scope만 남긴다.
- ✅ 확인됨(`conditions.py:condition_column`): `read_conditions`가 5필드를 conditions dict에 노출함 — `참고`→`reference`, `전해질`→`electrolyte`, `종류`→`cell_type`, `voltage`→`voltage_range`, `binder`→`binder`. 따라서 `in_scope(condition)`은 이 키들로 동작하고, 허용값 집합은 `FILTER_RULES`(소문자+공백제거 정규화)를 **단일 진실원**으로 재사용한다(헤더→conditions키 매핑 테이블).

---

## 4. 매칭 개선

스코핑이 1차 개선이다 — 백본(foil/전해질/종류/voltage)이 고정되므로 in-scope 안에선 변별 축이 **sample·composition·pressing(T)·protocol·binder(2종)·replicate + (capacity)셀번호**로 줄어 매칭이 훨씬 쉬워진다.

2차 개선:
- **토큰 정규화:** 매칭 토큰 비교에도 `normalize_filter_value`식(소문자+공백제거) 정규화를 적용. 특히 binder 2종을 canonical로 묶어 `material_conflicts`의 오발동을 제거.
- **capacity 셀번호 링크 유지:** `row_prefix(stem)`(파일명 앞 번호) == 일지 행 식별자 매칭은 강한 신호이므로 유지하되, in-scope 한정으로 충돌 가능성을 줄인다.
- 리네임은 하지 않는다. 정규화는 *비교용 내부 표현*일 뿐 파일·일지를 건드리지 않는다.

이 작업은 기존 `eis_matching.py`/`capacity_matching.py`/`matching_service.py` 리포트를 **재사용**하고, in-scope 한정 + 토큰 정규화만 얹는다. 새 매칭 엔진을 만들지 않는다.

---

## 5. 검증 UI — 모든 파일 근거표

기존 review 탭은 risky 행만 노출했다. 본 작업은 **in-scope 전 파일**을 한 표로 보여주고, 각 행에 매칭 근거를 채운다. 데이터는 매칭 리포트에 이미 있다(asdict 필드 재사용).

| 컬럼 | 출처 | verified 행에서의 의미 |
|---|---|---|
| 파일(relative_path) | report | — |
| 매칭 행(journal_row) + sample/date | report/override | 확정된 일지 행 |
| 상태(status) | report | verified/auto/review/ambiguous/blocked/unmatched/manual |
| 신뢰 근거 | `explain_*_match_status` | "파일명 앞 번호 419 = 일지 행 419 일치" 등 한국어 설명 |
| 행번호일치 | `row_prefix == journal_row` | ✅/✗ (capacity 핵심 근거) |
| 겹친 단서(overlap_tokens) | report | 어떤 재료/조성 토큰이 일치했나 |
| 충돌 단서(conflict_tokens) | report | 충돌 있으면 표시 |
| 날짜차(date_delta_days) | report | 실험일 vs 측정일 차 |
| 점수/margin | report | 자동확정 근거 강도 |
| 대안 후보(candidate_options) | report | 펼치면 다른 후보들 |

- **정렬/필터:** 기본은 risky 먼저(확인 필요), 그러나 `전체 보기` 토글로 verified 포함 전부. 일지 뷰의 `무시행` 토글과 동일 UX 결.
- verified 행도 **펼치면 근거**를 보여 "왜 확신했는지" 이중 확인.

---

## 6. 1:1 불변식

매칭 결과에서 다음을 계산·표면화한다:
- **unmatched:** in-scope 파일인데 귀속 행 없음 → 빨강.
- **ambiguous:** 후보 ≥2, margin 낮음 → 노랑(택1 필요).
- **고아 활성행:** in-scope 일지 행인데 기대 analysis_type 파일이 0 → "측정 누락?" (정보).
- **중복:** 같은 (행, analysis_type)에 파일 ≥2 → 재측정/오류 점검.
- 요약 배지: `in-scope 125행 · 매칭완료 N · 확인필요 M · 고아 K`.

---

## 7. 통합 지점 (기존 코드 재사용)

- **신규 공유 모듈** `battery_lab/scope.py` — `FILTER_RULES`(excel_dashboard에서 이동/공유) + `in_scope(condition)` + `normalize_token(v)`.
- **`matching_service.py`** — `build_match_payload`에서 conditions를 in-scope로 한정; risky-only 대신 **전체 in-scope 행** + 근거 컬럼을 내보내는 `verification_rows(...)` 추가. 기존 stage1/stage2/overrides 로직 유지.
- **`eis_matching.py`/`capacity_matching.py`** — 토큰 비교에 `normalize_token` 적용(최소 변경). 리포트 dataclass 필드 유지.
- **`excel_dashboard.py`** — `FILTER_RULES`를 `scope.py`에서 import하도록 변경(중복 제거).
- **`routes.py` + `templates/battery_lab/app.html`** — review_EIS_capacity 탭에 "전체 근거표" 뷰 추가(기존 stage UI 보존). 라우트는 기존 `match_api`/`match_review_api` 확장.

---

## 8. 데이터 모델

`verification_rows`가 반환하는 한 행:
```python
{
  "relative_path": str, "source_name": str, "analysis_type": str,
  "status": str,               # verified|auto|review|ambiguous|blocked|unmatched|manual
  "in_scope": True,            # 항상 in-scope만
  "journal_row": int | "", "condition_key": str, "sample": str, "date": str,
  "row_exact": bool,           # capacity 행번호 일치
  "overlap_tokens": str, "conflict_tokens": str, "date_delta_days": int | None,
  "score": int, "margin": int,
  "reason": str,               # explain_* 한국어
  "candidate_options": [ {...} ],   # 대안 후보
  "override_source": str,      # 수동 확정 출처(있으면)
}
```

---

## 9. 테스트

- `scope.in_scope`: 5조건 전부 만족 → True; 하나라도 어긋나면 False; 표기변형(`2wt% cmc`) 흡수.
- 매칭 후보가 in-scope로 한정되는지(out-of-scope 일지 행이 후보에서 빠짐).
- `normalize_token`이 binder 2변형을 동일화 → 기존 false `blocked` 케이스가 사라짐(회귀 픽스처).
- `verification_rows`: in-scope 전 파일 반환, verified 행도 근거 컬럼 채움.
- 1:1 불변식: 합성 데이터로 unmatched/ambiguous/고아행/중복 각각 검출.
- 출력 안정성: 동일 입력 동일 결과(렌더 캐시와 정합).

---

## 10. 열린 질문 / 위험 (플랜·구현 중 확정)

- `read_conditions`의 in-scope 필드 노출 여부(§3). 누락 시 정규화 확장 필요.
- capacity `row_prefix`가 일지 *행번호*인지 *셀 일련번호*인지 — in-scope 한정 후 1:1 검증으로 확인.
- review 탭 UI 확장 범위(전체 근거표를 새 서브탭 vs 기존 stage 대체). 기본: **추가**(보존 우선).
- 렌더 캐시 `context_hash`는 이미 override JSON을 추적 → 수동 확정이 그래프 캐시도 갱신(정합 유지).
