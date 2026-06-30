# 설계: '새 실험 등록' 위저드 — 파일 1개 = 실험일지 1행

- **날짜:** 2026-06-29
- **상태:** 설계 확정 대기 (구현 계획 작성 전)
- **대상 모듈:** 실험일지 페이지 '새 실험 등록' 위저드
- **주요 파일:** `battery_lab/experiment_import.py`, `battery_lab/routes.py`, `battery_lab/templates/battery_lab/app.html`, `battery_lab/excel_dashboard.py`(수식), `battery_lab/conditions.py`(컬럼 정규화)

## 1. 목표

여러 개의 섞인 원본 데이터 파일(`.seo .sde .wrd .csv .xlsx` 등)을 업로드하면 1단계에서 유형을 자동 분류하는 현행 동작은 유지하되, **각 파일이 실험일지의 독립된 새 행 1개에 대응**되도록 한다. 즉 현행 **N개 파일 → 1행** 구조를 **N개 파일 → N행**으로 바꾼다.

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
| ② | Binder 기본값 | **프리셋 2개 드롭다운 + 직접입력**, 기본 선택 `2wt%cmc` (값: `2wt%cmc`, `2wt%cmc/40wt%SBR`) |
| ③ | 수식 컬럼 처리 | **앱이 읽는 컬럼만 계산값(숫자) 기록**, 표시용 파생 수식은 엑셀 수식 유지 |
| ④ | Date 기본값 | 업로드 시 브라우저 `File.lastModified`를 파일별로 받아 `YYMMDD`로 변환 |

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
- **Areal mass density (col20)** `= Active material × 1000/(π·0.6²)` — **앱이 읽음 → Python 계산 리터럴 기록(결정③)**
- **합제밀도/electrode_density (col26)** `= electrode(g)/(volume/1000)` — CONDITION_FIELDS 포함 → 리터럴 기록(결정③)
- Active material(col9), Current(A)(col8), electrode(g)(col24), volume(col25), 전극 두께(col23) — 표시용 → 엑셀 수식 유지

도출식(원본 입력으로부터):
```
active_material_g  = (foil_electrode_g − foil_g) × ratio
areal_mass_density = active_material_g × 1000 / (π × 0.6²)
electrode_g        = foil_electrode_g − foil_g
전극두께_mm        = foil_electrode_mm − 호일두께_mm
volume_mm3         = 113.1 × 전극두께_mm
electrode_density  = electrode_g / (volume_mm3 / 1000)
```

### 4.4 폼에 없음 + 빈칸: CV, Cut capacity, Cell 자리, Theoretical capacity, C-Rate, 압연 전 두께, Drops, OCV, MEMO, Additionnal MEMO

### 4.5 현행 폼에서 삭제
- 입력칸: `sample group`, `material family`, `treatment`, `note` (`app.html:604-607`)
- 코멘트: "과거 조건값을 빠르게 선택할 수 있습니다." (`app.html:611`)

## 5. 데이터 모델 변경

현행 `DraftImportManifest`는 단일 `metadata`/`metadata_status`/`metadata_errors`(`experiment_import.py:98`). →

- **`DraftImportFile`에 per-file 필드 추가:** `metadata: dict`, `metadata_status: str`, `metadata_errors: list`.
- manifest 레벨 단일 metadata는 폐기(또는 호환용 유지 후 제거).
- `manifest_from_payload`/`asdict` 직렬화에 새 필드 반영.

## 6. 백엔드 변경

### 6.1 journal writer — 정확한 헤더 매핑 (예상문제 B 해결)
`append_journal_row`(`experiment_import.py:571`)의 `condition_column(헤더)` 키 매핑은 `호일 두께/전극 두께/압연 전 두께`가 모두 키 `'mm'`로 충돌 → **정확한 헤더 문자열 → 컬럼 인덱스** 매핑으로 교체. 폼 필드 키도 정확한 헤더(또는 안정적 col-index) 사용.

### 6.2 파생값 계산 + 리터럴 기록 (결정③)
행 기록 절차: ① 가변+고정 입력값을 해당 컬럼에 기록 → ② `apply_row_formulas` 호출(표시용 수식 채움) → ③ **`areal_mass_density`(col20), `electrode_density`(col26)를 Python 계산 리터럴로 덮어씀**(엑셀 열기 전에도 앱이 즉시 숫자로 읽도록).

### 6.3 검증 (`validate_metadata`, 예상문제 G)
- 기존 `areal_mass_density` 필수 규칙 제거.
- 신규 필수: `date`, `sample`, `foil_electrode_g`, `foil_electrode_mm`.
- 관계 검증: `foil_electrode_g > foil_g`, `0 < ratio ≤ 1`, 숫자 필드(foil_g, ratio, current_density, 두께들) numeric.
- `clean_metadata`의 allowed 집합을 신규 16필드로 교체.

### 6.4 commit 루프화 + 원자성 (예상문제 E)
`commit_import_draft`:
- 커밋 전 **모든 파일 metadata 일괄 검증**(하나라도 invalid면 중단, 부분 기록 방지).
- **워크북 1회 open → 파일별 N행 append → 1회 save**(원자성·속도).
- 파일별로 `save_draft_files_to_final_locations`/`write_commit_match_overrides`를 해당 행 번호로 기록.
- 무거운 rebuild(plot/match, `persist_commit_outputs`)와 `queue_import_rebuild_jobs`는 **마지막에 1회만**.

### 6.5 routes (`routes.py`)
- metadata PATCH를 file_id 단위로 (`update_import_draft_metadata_api`, `:266`).
- commit 응답이 N행 결과(행 번호 배열, 파일별 저장 결과) 반환하도록.

## 7. 프론트엔드 변경 (`app.html`)

- **2단계:** 파일별 아코디언(결정①). 각 항목 = 미리보기 + 가변 4칸(항상) + '고정값 보기' 토글(12칸, 기본값 채움).
- **Binder:** `<select>` 2 프리셋 + 직접입력(결정②).
- **Date 자동(결정④):** 업로드 시 각 파일 `File.lastModified` 캡처 → `YYMMDD` 기본값. (서버 `stat()`은 업로드 시각이라 부적합)
- 삭제: 4개 입력칸 + 코멘트(§4.5).
- **3단계:** 추가될 **N개 행** 확인 → 저장 시 N행 생성.
- JS 메타데이터 수집/저장을 파일별로.

## 8. 예상 문제 & 대응

| # | 문제 | 대응 |
|---|---|---|
| A | **수식 read-back = None** — `read_xlsx_optional`이 `data_only=True`(`file_io.py:204`)인데 openpyxl은 수식을 계산·캐싱 안 함 → 새 행 `areal_mass_density`가 엑셀로 열기 전까지 None → 매칭/용량정규화 깨짐 | §6.2 — Python 계산 리터럴 기록(결정③) |
| B | **키 충돌** — `호일/전극/압연 전 두께` 3컬럼이 키 `'mm'`로 충돌 | §6.1 — 정확한 헤더→컬럼 매핑 |
| C | per-file metadata 모델 부재 | §5 — `DraftImportFile`에 per-file 필드 |
| D | Date = "파일 생성일" — 서버 stat 부적합 | §7 — 브라우저 `File.lastModified` |
| E | N행 부분 실패 | §6.4 — 일괄검증 + 1회 save |
| F | 매칭/클러스터 — 행별 cluster 키 | 고정 기본값 동일 시 같은 cluster로 묶임(의도 일치). override 루프화 |
| G | 검증 규칙 | §6.3 |
| H | 테스트 — `tests/test_experiment_import.py:127`가 단일 metadata 가정 | 파일별 metadata + 신규 스키마로 재작성 |

## 9. 구현 순서

1. 백엔드 모델: `DraftImportFile` per-file metadata, manifest 직렬화
2. 검증/도출: `validate_metadata` 신규 스키마 + 파생값 계산 헬퍼
3. journal writer: 정확한 헤더 매핑 + areal/density 리터럴 기록
4. commit 루프화: 1회 open → N행 + 파일 저장 + override, rebuild 1회
5. routes: file_id별 metadata PATCH, commit 응답
6. 프론트(app.html): 아코디언 + 고정값 토글 + Binder 드롭다운 + Date 자동 + 삭제 + N행 확인
7. 테스트 재작성, `pytest` 통과

## 10. 보류/저순위

- Date를 텍스트(`260627`) vs 숫자로 저장 — 현행 동작(텍스트) 유지, 추후 정합.
- 고정 기본값을 서버 상수 vs 프론트 상수 — 구현 계획에서 단일 출처 결정.
