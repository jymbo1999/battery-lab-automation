# 인계장 (HANDOFF) — Battery Lab 매칭 검증

- 작성: 2026-06-24
- 리포: `battery-lab-automation/` · 브랜치 **`phase1-render-cache`** (main 미머지, 위 모든 작업이 여기 누적)
- 테스트: `.venv/bin/python -m pytest -q` → **97 passed**
- 다음 세션은 이 문서 + 메모리 `graph-viewer-perf-roadmap.md`를 읽고 바로 픽업.

---

## 0. 다음 세션에서 할 일 (NEXT TASKS)

매칭은 안정화됨(capacity 117 verified · EIS 비교 28 확정 · EIS 시계열 35 TS클러스터). 시계열 재클러스터링(`eis_timeseries.py`, 끝점규칙+행투표+conflict)·비교클러스터 정리는 **완료**. 남은 것:

**(A) 비교 클러스터 "시계열 포함" 토글 UI** — 데이터는 준비 끝. 비교 클러스터는 이제 **non-time-series 셀로만 정의**(깨끗)되고, 조건이 맞는 시계열 셀의 24hr 대표가 `EISComparisonCluster.optional_source_paths`로 붙어 있음(실데이터: C001 +2, C002 +6). 남은 일 = 뷰어 토글:
  - `viewer_service.eis_viewer_options` comparison_options에 optional 개수/경로 노출.
  - `viewer_service.eis_overlay_payload`(mode="comparison")에 `include_time_series` 파라미터 추가 → True면 `source_paths`+`optional_source_paths` 합쳐 오버레이.
  - `app.html` EIS live viewer에 체크박스.

**(B) Thickness(두께) 비교 모드** — 사용자 요청: 같은 sample_base를 두께(2T/3T/5T/7T…)별로 비교. 현재 클러스터 축은 (electrolyte/binder/voltage/ratio)+loading이라 두께가 갈림. 새 비교 축: (backbone + sample_base)로 묶고 멤버=다른 두께. sample에서 `\d+T` 파싱 필요.

**(대기) 시계열 conflict 19개** — `battery_visual_outputs/matching_checklist.html`(재생성됨; 19 conflict + 5 ambiguous 클러스터 카드 포함)을 담당자에게 전송 → 회신 JSON을 `matching_service.apply_checklist_answers`로 반영(클러스터 답변은 member 파일로 fan-out). 담당자 응답 대기중.

**용어 메모:** "loading" == areal mass density (mg/cm²) — 동의어.

**이번 세션 버그픽스:** `verification_payload`가 capacity에서 `is_time_series`/`time_series_groups` 접근으로 crash하던 것 수정(`.get`/`getattr`). 이 때문에 체크리스트 생성이 막혀 있었음 → 이제 정상. 회귀테스트 추가.

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
