# 실험일지 후속 작업 보고서 (2026-07-01, 2차)

요청 6건 전부 구현 + 실제 앱 브라우저 검증 완료. **테스트 180개 통과**(신규: 교체 플로우 6개).
파괴적인 "파일 교체"도 임시 복사본에서 안전하게 테스트했고, 실제 데이터는 건드리지 않았습니다.

---

## 1. 고아 행("데이터 파일 없음") — 개수 + 짙은 회색 표시 ✅

- **개수: 339개** (연결된 데이터 파일이 없는 실험일지 행). 데이터가 있는 행은 116개, 합 455개.
  - 493, 494 포함 확인. 예시 고아행: 369, 428, 486, 491~495(대부분 `_CV`/`low temp` 프로토콜), 508, 511~512 등.
  - 왜 이렇게 많나: 일지에는 계획만 적힌 행이나, 아직 매칭/등록 안 된 행, capacity 클러스터(1·2·3)에 안 들어가는 프로토콜(CV, low temp 등) 행이 많습니다. capacity 클러스터는 0.1C/0.5C/rate per 3종만 잡으므로 그 외 프로토콜은 "데이터 없음"으로 분류됩니다. (원하시면 분류 기준을 넓힐 수 있음 — §6 참고)
- **표시:** 고아 행의 행번호 칸을 **짙은 회색(#9ca3af)** 배경으로 칠하고, hover 툴팁은 **"데이터 파일 없음"**.
- 백엔드: `/api/journal/row-types` 응답에 `orphan_rows: [..]` 추가(중복 제거). 프론트가 행번호 칸에 `row-no-data` 클래스 부여.
- 검증: 실 데이터에서 화면상 486/491/492/493/494/508 등의 행번호 칸이 회색으로 렌더됨을 스크린샷으로 확인.

## 2. (Task 2 클러스터 목록) — 그대로 둠 ✅
지금 최신순(위)이 맞다고 하셔서 변경 없음.

## 3. 팝업 미리보기 그래프 잘림 → 버블에 맞게 ✅

- 미리보기 SVG가 박스 폭에 맞춰 비율 유지하며 축소되도록 변경(`source_preview_html`의 `.graph svg { width:100%; height:auto }`, viewBox 기반이라 비율 보존). 팝업 미리보기 박스도 더 크게(폭 360, 높이 420).
- 검증: 행 360 팝업에서 용량곡선(y축 333→183, x축, 축 라벨)이 잘림 없이 전부 보임. 이 수정은 capacity WRD/raw source 라이브 미리보기에도 동일 적용됨(공유 렌더러).

## 4. 데이터 파일 교체 — 맨 위로 + 누르면 즉시 완전 재계산 + 부분 교체 ✅

말씀하신 취지("새 행 등록과 똑같이 부수 산출값·클러스터 배정을 완전 재계산")대로, **등록 파이프라인을 그대로 재사용**해 행에 고정(pin)했습니다.

- **위치:** 팝업 **맨 위**에 "데이터 파일 교체" 섹션(주황 박스). "저장 후 클러스터 재계산" 별도 버튼은 **삭제** — 교체 버튼 하나로 교체와 재계산이 동시에 일어납니다.
- **부분 교체 지원:** 한 행에 연결된 파일이 여러 개(시계열 등)면 드롭다운에서 **교체할 파일 1개를 선택**해 그것만 교체.
- **동작(누르면):**
  1. 확인창 → 기존 파일을 **`.bak` 백업**(`battery_visual_outputs/row_replace_backups/<row>_<stamp>/`로 이동, 데이터 루트 밖이라 재스캔 안 됨).
  2. 새 파일 배치(등록과 동일한 이름규칙 `{행}_{sample}_{프로토콜}…`). WRD면 journal 행의 areal density로 `mass_g`까지 넣어 **summary CSV(mAh/g)** 재생성(+옵션 시 raw_timeseries).
  3. **지표 재계산**(Rs/Rct/ICE 등 `compute_metrics`) → `summary_metrics.csv` upsert, dataset plot 재생성.
  4. **매칭 override 재지정**(옛 파일 제거, 새 파일 → 같은 행) → **매칭/클러스터 리포트 재작성**(`write_*_match_outputs`) → 잘못된 클러스터에서 올바른 클러스터로 자동 재배치.
  5. 캐시 무효화 후 팝업/툴팁 자동 새로고침.
  - **실험정보 입력칸은 건드리지 않습니다**(요청대로 데이터와 별개).
- 백엔드: `POST /api/journal/row-replace-file`(multipart: row, kind, target, file, write_raw) → `experiment_import.replace_journal_row_file`.
- 검증(임시 복사본): 합성 WRD 등록→행2 매칭→교체 호출 시 ①새 파일 디스크 존재 ②옛 파일 백업+삭제 ③override가 새 파일로 재지정(행2 유지) ④매칭 리포트 재생성 ⑤실험정보 셀(Sample="cell A") 불변 — 모두 통과. 잘못된 target은 400.
- ⚠️ 주의: capacity 행 교체 시, 그 셀의 폴더(`..._cyc/<date>/`)에 있던 **기존 `.wrd`도 함께 백업·교체**합니다(폴더가 셀 단위라 안전). EIS는 매칭 파일 1개만 교체.

## 5. §3 — CSV 사이클 용량곡선 & summary vs raw_timeseries (설명 추가)

맞습니다. **WRD는 사이클축 곡선을 보려면 CSV로 변환이 필요**하고, capacity live viewer의 "Summary source overlay"가 읽는 파일은 **이름에 `capacity`가 들어간 요약 CSV**입니다 — 공식 `*_Capacity.csv`이거나, 우리가 자동 생성한 **`{stem}_capacity_summary.csv`** 둘 중 디스크에 있는 것. 둘 다 **사이클별 한 줄**(Cycle, 충·방전 용량, CE…)이라 cycle이 축이 됩니다.

- `{stem}_capacity_summary.csv` (**항상 생성**) = 사이클축 요약. 용량곡선·KPI·클러스터에 쓰는 그 파일.
- `{stem}_raw_timeseries.csv` (**체크박스 켤 때만**) = 원시 측정 레코드(시간·전압·전류·누적용량). cycle 축이 아니라 시간축 전압 프로파일/디버깅용, 용량이 큼.
- **'새실험등록' 위저드의 "WRD raw time-series CSV도 생성" 체크박스 옆에 위 설명을 추가**했습니다.

## 6. 확인/선택 사항 (있으면 알려주세요, 없으면 현 상태 유지)

1. **고아행 정의 범위**: 지금은 "capacity 1/2/3 클러스터 또는 EIS에 매칭된 파일이 하나도 없는 행"을 고아로 봅니다. 그래서 CV·low-temp 등 클러스터에 안 잡히는 프로토콜 행도 "데이터 없음"으로 회색이 됩니다. 이게 의도와 다르면(예: 파일은 있는데 클러스터만 없는 행은 제외) 기준을 조정하겠습니다.
2. **교체 후 정적 그래프 산출물(build_*_graphs 잡)**: 현재는 등록과 동일하게 per-file plot + 매칭 리포트까지 재생성합니다(라이브 뷰어는 항상 원본을 즉석 렌더하므로 바로 반영됨). 전체 셀 정적 SVG 일괄 재빌드는 무거운 백그라운드 잡이라 자동 실행하지 않았습니다 — 교체할 때마다 그 잡까지 자동으로 돌리길 원하면 큐에 넣도록 추가하겠습니다.

---

### 변경 파일
- `battery_lab/viewer_service.py` — orphan_rows 산출; preview SVG 반응형; row-detail에 kind 추가
- `battery_lab/excel_dashboard.py` — 고아행 회색 스타일/툴팁; 팝업 미리보기 크기; 교체 섹션 상단 이동 + 부분교체 UI; recluster 버튼 제거
- `battery_lab/experiment_import.py` — `replace_journal_row_file`(백업+재배치+전체 재계산)
- `battery_lab/routes.py` — `POST /api/journal/row-replace-file`; row-detail에 replace_url; 캐시 무효화
- `battery_lab/templates/battery_lab/app.html` — WRD 체크박스 설명 추가
- `tests/test_journal_row_replace.py` — 신규 교체 플로우 테스트 6개

### 테스트 / 검증
- `pytest`: **180 passed, 1 skipped**.
- 라이브 브라우저: 고아행 회색(#9ca3af, 툴팁 "데이터 파일 없음"), 팝업 그래프 잘림 없음, 교체 섹션 최상단+부분교체 셀렉터+파일입력, recluster 버튼 없음 — 모두 확인.
