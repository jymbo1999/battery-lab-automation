# 매칭 검증 — 야간 자율 진행 보고서

- 날짜: 2026-06-24 (사용자 부재 중 자율 진행)
- 브랜치: `phase1-render-cache` (아직 main 미머지 — 모든 작업이 여기 누적)
- 스펙: `docs/superpowers/specs/2026-06-24-matching-verification-design.md`
- 한 줄 요약: **백엔드(스코프 필터 → 스코프 한정 매칭 → 파일별 근거 + 1:1 불변식 → HTTP 엔드포인트) 완성·테스트. 프런트 근거표 UI는 의도적으로 보류(당신 검토 필요).** 전체 테스트 89개 통과.

---

## 1. 완료한 것 (커밋됨)

| 커밋 | 내용 |
|---|---|
| `0cef67b` | `battery_lab/scope.py` — in-scope 필터. `excel_dashboard.FILTER_RULES`를 **단일 진실원**으로 재사용(소문자+공백제거 정규화). `in_scope(condition)`, `filter_in_scope`, `normalize_token`. |
| `590d0ca` | `matching_service.verification_payload(...)` — in-scope 행에만 매칭, **모든 매칭 파일을 근거 컬럼과 함께** 반환 + 고아행/1:1 불변식. 읽기전용·additive(기존 review 흐름 안 건드림). |
| `1c67bdf` | `GET /api/<kind>/verification` 라우트(Flask 테스트 통과). |

신규 테스트 9개(`tests/test_matching_verification.py`). 기존 동작·테스트 변경 0 → 회귀 위험 최소.

## 2. 실데이터가 말해주는 현재 매칭 품질 (가장 중요)

`verification_payload`를 실데이터에 돌린 결과 (in-scope 117행 기준):

| | EIS | Capacity |
|---|---|---|
| 매칭된 파일 | 274 | 115 |
| **확인필요(risky)** | **154** | 28 |
| **모호(ambiguous)** | **124** | 28 |
| 고아행(파일 0) | 84 | 36 |
| unmatched(스코프밖+실패) | 38 | 2 |
| 중복그룹(같은 행 ≥2파일) | 21 | 28 |

**해석:**
- **Capacity는 양호** — 파일명 앞 행번호(`row_prefix`)→일지 행 앵커가 강해서 모호 28뿐.
- **EIS는 약함** — 행번호 앵커가 없어 퍼지 매칭에 의존 → 274개 중 124개 모호. **여기가 개선의 핵심 타깃.**
- 매칭파일 274 > 117행인 이유: 한 셀(행)이 시계열 EIS 여러 개(0hr/1hr/…)를 가져서 정상.

## 3. ⚠️ 예상 문제 / 특이사항 (검토 필요, 우선순위순)

### (P1) 일지가 Sample 이름으로 dedup됨 → 1:1의 근본 위협
`read_conditions`는 행을 **cell_id**로 키잉하는데, JYJ에 명시적 Cell_ID 컬럼이 없어 **Sample 이름으로 폴백**합니다. 결과: 631행 → 276 고유조건, in-scope 125행 → **117 조건**(같은 Sample의 반복셀이 1개로 합쳐짐).
- 영향: 같은 Sample의 **반복 셀(Cell 자리 다름)**이 하나로 붕괴 → capacity 파일 `419_`가 그 Sample의 *마지막* 행번호와 매칭될 수 있음 → **반복셀 1:1이 깨질 위험.**
- 권장 수정: 조건을 **행번호(`_source_row_number`)로 키잉**(모든 행을 distinct로). 단 이는 `conditions.py` + 다운스트림 매칭의 전제를 바꾸는 **깊은 변경**이라 당신 인지 하에 별도로 진행 권장. (오늘은 위험회피로 보류)

### (P2) EIS 모호도 높음 (124/274)
스코핑으로 cross-system 노이즈는 제거했지만, EIS는 앵커가 없어 여전히 약함. 개선안:
- **시점(time_point) + Sample + (P1 수정 후) 행번호** 결합 매칭.
- binder 2종/표기변형을 매칭 토큰에서 canonical화(아래 P3). 단 EIS in-scope는 binder가 거의 고정(LIB/CMC계)이라 효과는 sample/조성 변별이 더 큼.

### (P3) 매칭 내부 토큰 정규화는 보류함
`eis_matching`/`capacity_matching`의 `material_conflicts`/토큰 비교에 `normalize_token`을 적용하면 표기변형發 false `blocked`/`review`가 줄지만, **핵심 매칭 동작을 바꿔 기존 테스트에 영향**을 줄 수 있어 야간 단독 적용은 보류했습니다. 별도 회귀 픽스처와 함께 진행 권장.

### (P4) unmatched 버킷이 "스코프 밖" + "in-scope 매칭실패"를 섞음
스코프 한정 매칭이라 out-of-scope 파일은 자연히 unmatched로 빠집니다(EIS 38, capacity 2). 현재는 카운트로만 분리하고 `rows`(근거표)에서 제외 — 당신이 굳이 안 봐도 되게. 다만 "정말 누락된 in-scope 파일"과 "스코프 밖 파일"을 자동 구분하진 못함(파일만 보고 스코프 판단 불가). **고아행(84/36)** 이 "측정 누락" 신호를 대신 줍니다.

### (P5) 중복그룹 (EIS 21, capacity 28)
같은 행에 같은 kind 파일 ≥2. 시계열·다른 protocol이면 정상, 실수면 점검 대상. 근거표 UI에서 펼쳐 보게 할 예정.

## 4. 의도적으로 안 한 것
- **프런트 근거표 UI 미구현** — verified 포함 전 파일을 표로 보여주는 그 화면이 *신뢰의 핵심*이라, UX를 당신이 직접 정하는 게 맞다고 판단. 백엔드/엔드포인트는 준비됨(`/api/eis/verification`, `/api/capacity/verification`).
- 파일 리네임/표준화/업로드/일지 자동기입 — 스펙대로 전부 제외.

## 5. 추천 다음 단계 (아침에 결정)
1. **(P1) 조건 키잉을 행번호로** — 1:1 정확도의 토대. 가장 임팩트 큼.
2. **근거표 UI** — `/api/<kind>/verification` JSON을 review_EIS_capacity 탭에 표로. (verified 펼치면 근거, risky 상위 정렬, 고아행 섹션, 요약 배지) — UX 같이 잡기.
3. **(P3) 매칭 토큰 정규화** — 회귀 픽스처와 함께.
4. Phase 1(렌더캐시) + 이 작업 브랜치 정리·머지 시점 결정.

## 6. 검증 근거
- `python -m pytest -q` → **89 passed** (회귀 0).
- `/api/eis/verification` Flask 테스트클라이언트 200 + 구조 확인.
- 실데이터 요약(§2)은 `verification_payload`를 EIS/capacity 실폴더에 직접 실행한 결과.
