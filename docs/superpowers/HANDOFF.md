# 인계장 (HANDOFF) — Battery Lab 매칭 검증

- 작성: 2026-06-24
- 리포: `battery-lab-automation/` · 브랜치 **`phase1-render-cache`** (main 미머지, 위 모든 작업이 여기 누적)
- 테스트: `.venv/bin/python -m pytest -q` → **97 passed**
- 다음 세션은 이 문서 + 메모리 `graph-viewer-perf-roadmap.md`를 읽고 바로 픽업.

---

## 0. 다음 세션에서 할 일 (NEXT TASK) — EIS 시계열 클러스터링 개선

사용자가 준 단서를 **그대로** 옮긴다(다음 세션은 "우선 계획부터" 짜고, 추측 말고 실데이터로 검증하며 진행):

> 시계열 데이터 클러스터링이 지금 애매함. 중요한 단서: **모든 클러스터는 항상 0hr, 24hr 끝점 데이터가 존재**한다. 그러나 그 사이 hr 값들의 개수는 약간 차이가 있을 수 있다. 그래서 지금 잘못된 클러스터가 `[1hr 2hr 3hr]` `[0hr 22hr 24hr]` 이렇게 쪼개져 있다면 사실 **별개 클러스터가 아닐 수 있다**(원래 한 클러스터를 잘못 분할). 이 규칙(클러스터는 0hr→24hr 한 묶음)을 적용해 클러스터를 다시 정확히 유추하면 더 많이 정상화될 것. 직접 확인-검증하며 단계적으로 알고리즘을 짜서 제시할 것.
> 그리고 **같은 실험에서 나온 여러 시각(hr)의 파일들은 실험일지상 행 하나에 대응**시키면 된다(시계열 그룹 1개 ↔ 일지 행 1개 ↔ 셀 1개).

**시작 지점(직전 세션이 분류기 장애로 중단된 곳):**
- 현재 시계열 그룹핑: `battery_lab/eis_matching.py`의 `build_time_series_groups`, `eis_group_key`, `guess_time_point`, `has_hr_token`. 인벤토리에서 `is_time_series`/`time_point` 부여(`inventory_for_path`).
- `EISTimeSeriesGroup` dataclass: `group_id, group_key, condition_sample, file_count, time_points(;구분 문자열), source_paths`.
- 검증 페이로드에서 시계열은 `deferred_rows`로 빠져 있음(264개). `is_time_series=True`.
- **다음 세션 1단계(권장):** 실데이터로 현재 시계열 그룹들의 `time_points`를 뽑아 (a) 0hr/24hr 끝점 유무, (b) 같은 셀(sample)이 여러 그룹으로 쪼개졌는지, (c) `[1,2,3]`+`[0,22,24]`처럼 합쳐야 할 후보가 있는지 표로 확인 → 그 위에서 재클러스터링 알고리즘 제안. (그 다음 시계열 그룹 ↔ 일지 행 1:1 매핑까지.)

---

## 1. 지금까지의 큰 그림

Battery Lab = Flask 앱(그래프 뷰어 + 실험일지/매칭). 두 갈래 작업:

**A) Phase 1 — 영구 렌더 캐시 (완료, 검증됨).** 그래프 매번 느림 → 콜드 6.78s→캐시 0.38s(~18×). `battery_lab/render_cache.py`, `battery_visual_outputs/.render_cache/`(Render 영구 디스크). 스펙/플랜 `docs/superpowers/`.

**B) 매칭 검증 (현재 작업).** 방향: **파일 리네임/표준화/업로드/자동기입 전부 안 함.** 파일은 폴더에 원래 이름 그대로, 일지도 그대로. **단 하나의 목표 = 파일↔일지 행 1:1 정확 매칭 + 근거를 보여 안심 검증.** 스펙: `docs/superpowers/specs/2026-06-24-matching-verification-design.md` (이전 표준화 스펙은 supersede).

---

## 2. B에서 완료한 것 (커밋 `4a7e1eb`..`9423753`)

1. **`battery_lab/scope.py`** — in-scope 필터. `excel_dashboard.FILTER_RULES`(일지 "무시행" 토글의 5조건: 참고=12파이_Cu foil·전해질=1.0M LiPF6 EC/DEC 1:1·종류=LIB·Binder∈{2wt%cmc, 2wt%cmc/40wt%SBR}·Voltage=0.01~2V) 재사용. `in_scope(condition)`, `filter_in_scope`. → **in-scope 125행.**
2. **`conditions.read_conditions` 2건 수정 (핵심):**
   - 같은 Sample 반복행이 dedup으로 합쳐지던 것 → **행별로 유지**(충돌 시 키에 ` #row{N}` 접미사).
   - `_source_row_number`가 빈 행 때문에 ~42 어긋나던 것 → **진짜 Excel 행번호**로(`file_io.rows_to_records`/`read_xlsx_optional`에 `with_row_number`). **파일 앞번호 = Excel 행 확인(90%+)** → capacity `row_exact(+120)`가 정확히 작동 → **capacity 117개 전부 verified, ambiguous 0** (전엔 다수가 ~42행 어긋난 false verified).
3. **`matching_service.verification_payload`** — in-scope 한정 매칭 → 파일별 근거 rows(verified 포함) + 고아행 + 1:1 불변식 + (EIS) 시계열은 `deferred_rows`로 분리. `_verification_row`에 `is_time_series`, `file_date` 포함.
4. **`battery_lab/verification_view.py` + `GET /battery/verification`** — 서버렌더 근거표. 컬럼: 파일·파일날짜·매칭행·행날짜·날짜차·상태·근거·행번호일치·겹친단서·충돌·점수. "확인 필요만 보기" 토글. 시계열은 접이식.
5. **체크리스트 라운드트립 (오프라인/카톡):** `battery_lab/checklist_view.render_checklist_html`(자기완결 HTML, 후보 드롭다운+메모+localStorage+결과복사/JSON), `matching_service.apply_checklist_answers`(회신 JSON → overrides.json 병합, 기존 수동확정 스키마 재사용). 라우트 `GET /battery/checklist`, `POST /battery/api/checklist/apply?kind=eis`.
6. **담당자 답변 28개 반영 완료 (이번 세션):** `apply_checklist_answers`로 EIS override에 적용. applied 28 / unknown 0. **EIS 비교용 ambiguous 26→0** (28개 수동확정). 저장: `battery_visual_outputs/eis_match_overrides.json`(gitignore, 영구).

---

## 3. 현재 매칭 상태 (실데이터)

- **Capacity**: 117개 전부 `verified` (0 ambiguous, 0 unmatched). row_exact 앵커 강함.
- **EIS 비교용**: 28개 = 전부 담당자 수동확정(`manual`). ambiguous 0. (중복그룹 8 = 1st/2nd 재측정이 같은 행 → 정상.)
- **EIS 시계열(_hr)**: 264개 = `deferred_rows`로 보류 → **다음 세션 과제(§0).**

---

## 4. 실행/확인 방법

```bash
cd "battery-lab-automation"
.venv/bin/python -m pytest -q                  # 97 passed
.venv/bin/flask --app wsgi run --port 8000     # 로컬 앱 (gunicorn 미설치)
#  http://127.0.0.1:8000/battery/verification   근거표
#  http://127.0.0.1:8000/battery/checklist       담당자용 체크리스트
```
검증 페이지/체크리스트 정적 미리보기는 `battery_visual_outputs/verification_preview.html`, `matching_checklist.html`로도 생성됨(`render_*_html(payloads)`).

핵심 파일: `scope.py` · `conditions.py`(read_conditions) · `file_io.py`(rows_to_records/read_xlsx_optional) · `matching_service.py`(verification_payload, apply_checklist_answers, _verification_row) · `verification_view.py` · `checklist_view.py` · `routes.py`(/verification, /checklist, /api/checklist/apply, /api/<kind>/verification) · 테스트 `tests/test_matching_verification.py`(17개).

---

## 5. 알아둘 것 / 미해결

- **브랜치 미머지**: `phase1-render-cache`가 main 미머지(Phase 1 캐시 + 매칭검증 전부 누적). 머지 시점은 사용자와 정할 것.
- **needs_review 지표 nuance**: `RISKY_REVIEW_STATUSES`에 `manual` 포함이라, 수동확정한 28개가 `needs_review`로 잡힘(실제론 해결됨). 지표만 손볼지 다음 세션에서 판단(경미).
- **EIS unmatched 20 / 고아행 105**: in-scope인데 비교용 EIS 파일이 없는 행(대부분 시계열만 있거나 측정 없음). §0 시계열 작업 후 다시 볼 것.
- **deferred 매칭 토큰 정규화**: 위험회피로 보류(회귀 픽스처와 함께가 좋음). overnight 리포트 `docs/superpowers/reports/2026-06-24-matching-verification-progress.md` 참고.
- 미추적 `.omc/`, `battery_lab/.omc/`, `*.egg-info/`는 도구 산출물(커밋 안 함).
- 담당자 답변 원본 백업: `/tmp/checklist_answers.json` (영구 아님).
