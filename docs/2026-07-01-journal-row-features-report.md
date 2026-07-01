# 실험일지 행 기능 + Capacity 뷰어 작업 보고서 (2026-07-01 새벽)

밤사이 요청 5건을 구현했습니다. **174개 테스트 전부 통과**(신규 5개 포함), 실제 Flask 앱을
브라우저로 띄워 4개 UI 동작을 눈으로 확인했습니다. 파괴적(데이터 덮어쓰기) 부분만
컨펌 대기로 남겨뒀습니다(§5, §6).

---

## 1. 행번호 칸 클릭 → 행 전체 선택 ✅ 구현·검증 완료

- 실험일지(iframe)의 맨 왼쪽 행번호 `th`를 클릭하면 그 행 전체가 선택모드로 들어갑니다.
- 한 번 더 클릭하면(= "선택상태로 한번더클릭") 미리보기 팝업이 열립니다(§5).
- 헤더(1행)는 선택 대상에서 제외했습니다.
- 검증: 행 360 클릭 시 셀 33개가 선택 하이라이트, 행번호 칸이 파란 테두리(active)로 표시됨.
- 파일: `battery_lab/excel_dashboard.py` (CSS + `handleRowHeadClick`/`selectWholeRow`).

## 2. Source 그래프 개별 보기 정렬 뒤집기(최근이 위로) ✅ 구현·검증 완료

- EIS `source`, Capacity `Summary source overlay`/`WRD raw source preview` 드롭다운을
  **역순(최신 파일이 맨 위)** 으로 바꿨습니다.
- 기존 정렬은 파일경로 오름차순(=오래된 것 위). 이걸 그대로 뒤집어 최신이 위로 오게 했습니다.
- 검증: Capacity raw_source 드롭다운 첫 항목이 `260603`(6/3), 마지막이 `260319`(3/19)로 확인.
- 클러스터(1·2·3 protocol) 모드는 의미상 순서가 있어 **건드리지 않았습니다**. 개별 source 보기만
  반전. 혹시 클러스터 목록도 최신순을 원하시면 말씀 주세요.
- 파일: `battery_lab/templates/battery_lab/app.html` (`optionsForMode` + `newestFirst`).

## 3. "Summary source overlay" vs "WRD/raw source preview" 차이 (질문 답변)

**한 줄 요약:** 둘은 *보는 데이터 자체*와 *그리는 그래프*가 다릅니다.

| | Summary source overlay (`source`) | WRD/raw source preview (`raw_source`) |
|---|---|---|
| 읽는 파일 | 이름에 `capacity` 들어간 **요약 CSV**(`*_Capacity.csv` 등) | 원본 **`.wrd`**(및 raw csv) |
| 데이터 단위 | 사이클별 집계(Cycle, Q_charge/discharge, CE…) | 측정 레코드 시계열(전압/전류 vs 시간) |
| 그래프 | **사이클 vs 용량(mAh/g)** 곡선, 여러 셀 겹쳐 그림 | 한 파일의 **전압 프로파일(시간축)** |
| 백엔드 | `capacity_overlay_payload(mode="source")` → `capacity_dataset_svg` | `capacity_source_payload` → `wrd_voltage_profile_svg` |

즉 같은 셀이라도 한쪽은 "충방전 거듭할수록 용량이 어떻게 변하나"(수명곡선),
다른 쪽은 "한 사이클 안에서 전압이 어떻게 움직였나"(원시 파형)를 봅니다. 그래서 플롯이 다릅니다.

**wrd→csv 변환이 모든 wrd 입력에 대해 자동 생성되나?** → **아니요.**
- summary overlay는 디스크에 **이미 있는** `*capacity*.csv`를 읽을 뿐, wrd를 즉석에서 변환하지 않습니다.
- 변환은 `convert_wrd_file`(`wonatech_service.py`)이 수행하며, 이건 **"그래프 다시 만들기
  (build_capacity_graphs) 작업"이나 "새 실험 등록 위저드"를 돌릴 때만** 실행됩니다. wrd를 그냥
  폴더에 떨궈둔다고 자동 변환되지 않습니다.
- 변환 시 `{stem}_capacity_summary.csv`(요약)는 항상 생성, `{stem}_raw_timeseries.csv`(원시)는
  "WRD raw time-series CSV 저장" 체크 시에만 생성.

**wrd → csv 변환 방식 (`build_capacity_summary`)**
1. `parse_wrd_file`로 .wrd 바이너리 레코드를 파싱.
2. `cycle_index`로 묶어 사이클마다 충전/방전 Q의 최댓값(Ah)에 ×1000 → **mAh**.
3. CE(쿨롱효율) 계산.
4. 실험정보의 areal density로부터 `mass_g`가 주어지면 mAh를 나눠 **mAh/g(비용량)** 컬럼도 추가.

**플롯이 달라 보이는 핵심 이유(mAh vs mAh/g):** 과거 매칭하던 공식 `_Capacity.csv`는 이미
**비용량(mAh/g)** 이고, wrd에서 막 뽑은 요약은 **절대 mAh**. 둘은 `mass_g` 상수배만큼 차이납니다
(셀 471에서 비율 0.0098629 g로 검증됨, CE는 비율이라 동일). `mass_g`는 wrd가 아니라 **실험일지의
areal density**에서 옵니다 — 이래서 실험정보 입력이 그래프 스케일에 영향을 줍니다.

> 참고: 설정 탭의 "Capacity CSV / WRD Audit"는 wrd 재생성 요약과 공식 CSV가 일치하는지
> 비교만 하는(삭제 없는) 점검 도구입니다.

## 4. 행번호 hover 툴팁(데이터 유형) ✅ 구현·검증 완료

- 행번호에 마우스를 올리면 `데이터 유형: EIS, EIS time series, capacity 1` 식으로 뜹니다.
- 유형 판별: EIS 매칭(파일명 `_hr` 있으면 time series) + Capacity 매칭(클러스터 protocol →
  capacity 1/2/3)을 기존 매칭 리포트에서 역으로 모았습니다.
- 신규 엔드포인트 `GET /battery/api/journal/row-types` → `{row_types: {"360": ["type_1_..."], ...}}`.
  매칭 리포트는 메모이즈되어 있어 비용이 작고, 실패해도 빈 맵으로 떨어져 일지를 깨지 않습니다.
- 검증: 실제 데이터에서 116개 행에 유형이 매핑됨, 행 360 툴팁이 "데이터 유형: capacity 1"로 표시.
- 파일: `viewer_service.journal_row_data_index/journal_row_types_payload`, `routes.journal_row_types_api`,
  `excel_dashboard.py`(툴팁 + `TYPE_LABELS`).

## 5. 행번호 더블클릭(또는 선택 후 재클릭) → 미리보기/실험정보 팝업 ⚠️ 핵심 구현, 일부 컨펌 대기

**구현·검증 완료(비파괴):**
- 더블클릭/재클릭 시 위저드 "2. 미리보기 & 실험정보" 포맷을 빌려온 팝업이 뜹니다.
- **미리보기**: 그 행에 매칭된 데이터 파일들의 그래프를 iframe로 렌더(EIS/Capacity 각각 기존
  `eis_source_payload`/`capacity_source_payload` 재사용).
- **실험정보**: 그 행의 셀들을 헤더 라벨과 함께 편집 가능한 입력칸으로 표시(수식 셀은 회색 비활성).
- **수정내용 저장하기**: 바뀐 칸만 기존 `journal_cell_api`로 셀 단위 저장 → 시트 리로드(수식 컬럼 갱신).
- 신규 엔드포인트 `GET /battery/api/journal/row-detail?row=N`.
- 검증: 행 360 팝업에서 그래프 1개 + 입력칸 33개(참고/전해질/종류/Date/Sample/Binder…) 정상 렌더,
  Current(A) 같은 수식 칸은 회색 비활성, 저장 버튼 동작 가능 상태 확인(실제 워크북은 건드리지 않음).

**컨펌 대기(파괴적이라 일부러 비활성 버튼으로 둠):** → 아래 §6 질문 참조
- "저장 후 클러스터 재계산" 버튼: 백엔드 `recluster_url` 미연결 → 비활성.
- "데이터 파일만 교체" 버튼: 백엔드 `replace_url` 미연결 → 비활성.

- 파일: `viewer_service.journal_row_detail_payload`, `routes.journal_row_detail_api`+`_journal_row_info_fields`,
  `excel_dashboard.py`(`openRowPopup`/`renderRowPopup`/`saveRowInfo`).

## 6. 깨워서 확인받고 싶은 결정사항

§5의 파괴적 동작 2개는 의도적으로 비워뒀습니다. 결정 주시면 바로 붙이겠습니다.

1. **"클러스터 재계산"의 정확한 의미** — 실험정보 저장 후 (a) EIS+Capacity 매칭/override를 다시
   돌려 이 행에 붙는 파일·클러스터를 갱신만 하면 되나요, 아니면 (b) 그래프 산출물(build_*_graphs)까지
   재생성해야 하나요? "필요하면 재배치"의 재배치 기준(무엇이 바뀌면 다른 클러스터로 옮기나)도 알려주세요.

2. **"데이터 파일만 교체" 덮어쓰기 정책** — 새 파일 업로드 시:
   - 어떤 파일을 덮어쓰나요? (그 행에 매칭된 원본 1개 vs 여러 개 중 선택 / EIS·Capacity 동시 가능?)
   - 원본을 정말 덮어쓰기(되돌리기 불가)할까요, 아니면 백업(.bak) 후 교체할까요? **데이터 손실 위험**이라
     기본은 백업 후 교체를 권합니다.
   - "rs·rct·ICE 재계산"은 새 파일 파싱→지표 재산출인데, 이때 실험일지 행은 그대로 두고
     지표/그래프만 갱신하는 게 맞나요? (요청대로 "실험정보 입력내역과는 별개"로 이해했습니다.)

3. **Task 2 클러스터 목록**도 최신순으로 뒤집을지 (현재는 개별 source 보기만 반전).

---

### 변경 파일 요약
- `battery_lab/excel_dashboard.py` — 행 선택/툴팁/팝업(CSS+JS), `render_page` 시그니처에 URL 2개 추가
- `battery_lab/templates/battery_lab/app.html` — source 드롭다운 최신순 반전
- `battery_lab/viewer_service.py` — `journal_row_data_index`/`_types_payload`/`_detail_payload` 추가
- `battery_lab/routes.py` — `journal_row_types_api`, `journal_row_detail_api`(+info_fields 헬퍼), `journal_excel` URL 전달, `Any` import
- `tests/test_journal_row_detail.py` — 신규 테스트 5개

### 테스트
- 전체 `pytest`: **174 passed, 1 skipped**.
- 라이브 브라우저 검증: 행선택(33셀), 툴팁("capacity 1"), 팝업(그래프1+칸33, 수식칸 비활성),
  드롭다운 최신순(reversed=true) 모두 확인.
