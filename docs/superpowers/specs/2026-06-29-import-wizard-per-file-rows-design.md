# 설계: '새 실험 등록' 위저드 — 파일 1개 = 실험일지 1행

- **날짜:** 2026-06-29 (검증·갱신 2026-06-30)
- **상태:** 설계 확정 대기 (구현 계획 작성 전)
- **대상 모듈:** 실험일지 페이지 '새 실험 등록' 위저드
- **주요 파일:** `battery_lab/experiment_import.py`, `battery_lab/routes.py`, `battery_lab/templates/battery_lab/app.html`, `battery_lab/excel_dashboard.py`(수식), `battery_lab/conditions.py`(컬럼 정규화), `battery_lab/capacity_matching.py`(프로토콜 라벨)

## 1. 목표

여러 개의 섞인 원본 데이터 파일(`.seo .sde .wrd .csv .xlsx` 등)을 업로드하면 1단계에서 유형을 자동 분류하는 현행 동작은 유지하되, **각 파일이 실험일지의 독립된 새 행 1개에 대응**되도록 한다(시계열은 예외 — §5). 즉 현행 **N개 파일 → 1행**을 **N개 row-unit → N행**으로 바꾼다.

입력 효율을 위해 대부분 필드는 기본값을 미리 채워두고(토글로 숨김), 자주 바뀌는 4개 필드만 비워서 직접 입력받는다.

## 2. 현행 동작 (변경 전)

3단계 위저드 (`app.html:565`):
1. **업로드 & 분류** — 다중 업로드 → 파서/지표/미리보기 + 타입 자동분류 (`infer_assignment`)
2. **미리보기 & 실험정보** — 단일 공통 실험정보 폼 1개
3. **확인 & 저장** — `commit_import_draft` → `append_journal_row`로 **실험일지 행 1개**만 추가, 모든 파일이 그 행에 귀속

한계: `commit_import_draft`(`experiment_import.py:395`)가 단일 `journal_row`를 만들고 파일 저장/매칭 override가 모두 그 1행을 참조.

## 3. 확정된 결정사항

| # | 주제 | 결정 |
|---|---|---|
| ① | 다중 파일 폼 레이아웃 | **파일별 독립 아코디언** — 한 번에 하나 펼침. 펼친 항목에 가변 4칸 + '고정값 12칸' 토글 내장 |
| ② | Binder 기본값 | **프리셋 2개 드롭다운 + 직접입력**, 기본 `2wt%cmc` (값: `2wt%cmc`, `2wt%cmc/40wt%SBR`) |
| ③ | 수식 컬럼 처리 | **앱이 읽는 컬럼(`areal_mass_density`, `electrode_density`)만 Python 계산 리터럴 기록**, 표시용 파생은 엑셀 수식 유지 |
| ④ | Date 기본값 | 업로드 시 브라우저 `File.lastModified`를 파일별로 받아 `YYMMDD`로 변환 |
| ⑤ | **행의 단위** | **엄격 파일 단위** — comparison EIS·capacity는 각 파일 = 각 행. **시계열만 `cell_id`로 묶어 1행**. 같은 셀 다중 프로토콜 → 여러 행(조건 중복 감수) |
| ⑥ | **Capacity 저장 명명** | `assignment` 코드명 대신 **사람용 프로토콜 라벨**(`capacity_1→0.1C`, `2→0.5C`, `3→rate per`)을 폴더·파일명에 사용 (데이터브라우저 역추적) |

## 4. 실험일지 컬럼 매핑 (워크북 `Cell condition Calculation.xlsx` 시트 `JYJ`, 33컬럼)

### 4.1 가변 4칸 — 항상 표시, 빈칸(사용자 직접 입력)
| 헤더 | col | 기본값 |
|---|---|---|
| Date | 4 | 파일 `lastModified` → `YYMMDD` |
| Sample | 5 | (빈칸) |
| foil+electrode (g) | 16 | (빈칸) |
| 전극(foil+electrode) 두께(mm) | 21 | (빈칸) |

### 4.2 고정 12칸 — '고정값 보기' 토글, 기본값 채움(수정 가능)
| 헤더 | col | 기본값 |
|---|---|---|
| 참고 | 1 | `12 파이_Cu foil` |
| 전해질 | 2 | `1.0M LiPF6 EC/DEC 1:1` |
| 종류 | 3 | `LIB` |
| Conductive agent | 6 | `-` |
| Binder | 7 | `2wt%cmc` (드롭다운, 결정②) |
| Voltage range | 12 | `0.01~2V` |
| foil (g) | 17 | `0.009928` |
| ratio | 18 | `0.96` |
| Current density (mA/g) | 19 | `37.2` |
| 호일 두께(mm) | 22 | `0.00958` |
| Electrolyte (ul) | 29 | `80` |
| Drying Condition | 30 | `60도 12시간` |

### 4.3 수식 자동계산 (폼에 없음) — `excel_dashboard.py:31` FORMULA_TEMPLATES
- **Areal mass density (col20)** `= Active material × 1000/(π·0.6²)` — **앱이 읽음 → Python 계산 리터럴(결정③)**
- **합제밀도/electrode_density (col26)** — CONDITION_FIELDS 포함 → 리터럴(결정③)
- Active material(col9), Current(A)(col8), electrode(g)(col24), volume(col25), 전극 두께(col23) — 표시용 → 엑셀 수식 유지

도출식: `active_material_g=(foil_electrode_g−foil_g)×ratio` → `areal_mass_density=active_material_g×1000/(π·0.6²)`; `electrode_g=foil_electrode_g−foil_g`, `전극두께=foil_electrode_mm−호일두께`, `volume=113.1×전극두께`, `electrode_density=electrode_g/(volume/1000)`

### 4.4 폼에 없음 + 빈칸: CV, Cut capacity, Cell 자리, Theoretical capacity, C-Rate, 압연 전 두께, Drops, OCV, MEMO, Additionnal MEMO

### 4.5 현행 폼에서 삭제
- 입력칸: `sample group`, `material family`, `treatment`, `note` (`app.html:604-607`)
- 코멘트: "과거 조건값을 빠르게 선택할 수 있습니다." (`app.html:611`)

## 5. 행의 단위(row-unit) 규칙 + 1:1 검증 결과

### 5.1 검증 결과 (2026-06-30, 실데이터 매칭 리포트 분석)
- **Capacity**: 117파일/82행 — `1파일:54행`, 2:22, 3:5, 4:1. 다중파일 28행을 직접 확인하니 선두번호(364/402/403/417)·날짜가 제각각 → **과거 퍼지 매처 아티팩트**(같은 셀이 아님). 새 플로우는 명시적 행 생성이라 무관.
- **EIS**: 한 행에 8~18 파일 = 시계열(`_hr` N개 → 1행), 예상대로.
- **결론**: comparison EIS·단일 capacity는 **1:1 성립**. 시계열은 `cell_id`로 묶어 1행.

### 5.2 row-unit 정의 (결정⑤)
- assignment ∈ {`eis_comparison`, `capacity_1/2/3`} → **각 파일 = 1 row-unit**.
- assignment = `eis_time_series` → **`cell_id`로 그룹핑, 그룹당 1 row-unit**(N개 `_hr` 파일).
- 각 row-unit = 실험일지 1행. unit별 metadata 1세트.

### 5.3 업로드창 안내문구 추가 (1번 요청)
1단계 업로드 영역에: **"EIS 시계열 데이터의 경우 파일명 내에 `_hr` 표기를 반드시 포함해 주십시오"**. (시계열 자동분류·그룹핑이 `_hr` 토큰 의존 — `infer_assignment`)

## 6. 백엔드 변경

### 6.1 데이터 모델 — row-unit + per-unit metadata
- manifest의 단일 `metadata`/`metadata_status` 폐기.
- **row-unit 구조 도입**: `unit_id`(싱글=`file_id`, 시계열=`cell_id` 기반 그룹키), `file_ids: list`, `metadata: dict`, `metadata_status`, `metadata_errors`.
- `manifest_from_payload`/`asdict` 직렬화 반영.

### 6.2 journal writer — 정확한 헤더 매핑 (예상문제 B)
`append_journal_row`의 `condition_column(헤더)` 키 매핑은 `호일/전극/압연 전 두께`가 모두 키 `'mm'`로 충돌 → **정확한 헤더 문자열 → 컬럼 인덱스** 매핑으로 교체. 폼 필드 키도 정확한 헤더(또는 안정적 col-index) 사용.

### 6.3 파생값 리터럴 기록 (결정③)
행 기록: ① 입력값 기록 → ② `apply_row_formulas`(표시용 수식) → ③ `areal_mass_density`(col20)·`electrode_density`(col26)를 Python 계산 리터럴로 **덮어씀**(엑셀 열기 전에도 앱이 숫자로 읽도록).

### 6.4 검증 (`validate_metadata`, 예상문제 G)
- 기존 `areal_mass_density` 필수 제거.
- 필수: `date`, `sample`, `foil_electrode_g`, `foil_electrode_mm`.
- 관계: `foil_electrode_g > foil_g`, `0 < ratio ≤ 1`, 숫자 필드 numeric.
- `clean_metadata` allowed 집합을 신규 16필드로 교체.

### 6.5 commit 루프화 + 원자성 (예상문제 E)
- 커밋 전 **모든 unit metadata 일괄 검증**(하나라도 invalid면 중단).
- **워크북 1회 open → unit별 N행 append → 1회 save**.
- unit별 `save_draft_files_to_final_locations`/`write_commit_match_overrides`를 해당 행 번호로(시계열 unit은 N파일 전부 같은 행).
- rebuild(`persist_commit_outputs`)·`queue_import_rebuild_jobs`는 **마지막 1회만**.

### 6.6 Capacity 저장 명명 수정 (결정⑥, 2번 요청)
`final_directory_for_item`/`final_filename_for_item`에서 `item.assignment`(`capacity_1`) 대신 **프로토콜 라벨**(`0.1C`/`0.5C`/`rate per`) 사용 → `capacity_protocol_from_filename`이 재인식, 데이터브라우저 폴더/그룹이 기존과 동일 형태. 선두 `{journal_row}_`는 유지(`row_prefix()` 역링크).

### 6.7 routes
- metadata PATCH를 unit_id 단위로.
- 정규화 파일명 미리보기 엔드포인트(다음 행번호 예측 → §7.4).
- commit 응답: 행 번호 배열 + unit별 저장 파일(raw/normalized) 결과.

## 7. 프론트엔드 변경 (`app.html`)

### 7.1 2단계 — 파일별 아코디언 (결정①)
row-unit별 아코디언 항목. 각 항목 = 미리보기 + 가변 4칸(항상) + '고정값 보기' 토글(12칸 기본값). Binder는 `<select>` 2프리셋+직접입력(결정②). Date는 unit 대표 파일 `lastModified` 기본값(결정④).

### 7.2 삭제
4개 입력칸 + "과거 조건값…" 코멘트(§4.5).

### 7.3 3단계 — 클러스터 배정 표시 (4번 요청)
unit별로 배정 클러스터 표시(기존 `build_import_draft_cluster_preview`/`cluster_preview_row`의 `matched_existing_cluster`/`new_independent_cluster` 활용). 현행 step3 chip 로직을 unit 단위로 확장.

### 7.4 3단계 — 파일명 정규화 "raw → normalized" 표기 (3번 요청)
- 2단계 폼: 업로드된 **raw 파일명** 표기.
- 3단계 최종확인: 각 파일을 **`raw명 → 정규화명`** 형태로 표시하고 그 형태로 저장.
- 정규화명은 `journal_row`를 포함하므로, 커밋 전엔 **다음 행번호 예측**(서버 dry-run, unit 순서대로 `max_row+1, +2, …`)으로 표시 → 커밋 결과에서 실제 저장 경로로 확정.

## 8. 예상 문제 & 대응

| # | 문제 | 대응 |
|---|---|---|
| A | **수식 read-back=None** — `read_xlsx_optional`이 `data_only=True`(`file_io.py:204`), openpyxl은 수식 미계산 → 새 행 areal None → 매칭·정규화 깨짐 | §6.3 리터럴 기록 |
| B | **키 충돌** `'mm'` 3컬럼 | §6.2 정확한 헤더 매핑 |
| C | per-unit metadata 모델 | §6.1 |
| D | Date=파일생성일, 서버 stat 부적합 | §7.1 브라우저 `lastModified` |
| E | N행 부분 실패 | §6.5 일괄검증+1회 save |
| F | 시계열 그룹핑(`cell_id`) | §5.2 / `_hr` 안내 §5.3 |
| G | 검증 규칙 | §6.4 |
| H | 테스트 — `test_experiment_import.py:127` 단일 metadata 가정 | per-unit + 신규 스키마로 재작성 |
| I | **Capacity 명명** `capacity_1`→브라우저 인식 실패 | §6.6 프로토콜 라벨 |
| J | **정규화명에 행번호 필요**(커밋 전 미지) | §7.4 다음 행번호 예측 → 결과서 확정 |

## 9. 구현 순서

1. 백엔드 모델: row-unit + per-unit metadata, 직렬화
2. 검증/도출: `validate_metadata` 신규 스키마 + 파생값 계산 헬퍼
3. journal writer: 정확한 헤더 매핑 + areal/density 리터럴
4. 저장 명명: capacity 프로토콜 라벨 매핑(§6.6)
5. commit 루프화: 1회 open → unit별 N행 + 파일 저장 + override, rebuild 1회
6. routes: unit별 metadata PATCH, 정규화명 예측, commit 응답
7. 프론트: 아코디언 + 고정값 토글 + Binder 드롭다운 + Date 자동 + `_hr` 안내 + 삭제 + 3단계(클러스터·raw→normalized)
8. 테스트 재작성, `pytest` 통과

## 10. 검증 (구현 후, 4번 요청)
- 단위/API 테스트: per-unit metadata, N행 생성, areal 리터럴 숫자 확인.
- **저장 후 그래프 생성 확인**: 커밋 → `build_eis_graphs`/`build_capacity_graphs` 잡 완료 후 `battery_visual_outputs/eis`·`/capacity`에 새 행 artifact(SVG/PNG) 생성 + **데이터 분석 탭에 실제 그래프가 추가되는지** 확인(자동 테스트로 artifact 존재 assert + 수동 UI 확인).
- 데이터브라우저 역추적: 새 capacity 파일이 `0.1C/0.5C/rate per` 프로토콜로, 선두 행번호로 올바르게 그룹/인식되는지 확인.

## 11. 보류/저순위
- Date 텍스트(`260627`) vs 숫자 저장 — 현행(텍스트) 유지.
- 고정 기본값 단일 출처(서버 상수 vs 프론트) — 구현 계획에서 확정.
