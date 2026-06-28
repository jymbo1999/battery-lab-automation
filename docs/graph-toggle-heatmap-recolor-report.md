# 그래프 토글 Heatmap 재배색 구현 보고서

구현 일자: 2026-06-27

## 요약

EIS / Capacity 그래프 뷰어의 토글 표 UX를 수정했습니다.

- **hide된 그래프**: 행 왼쪽 색상 동그라미(swatch)가 흰색으로 바뀜 (+ 회색 테두리)
- **활성화된 그래프**: 매 토글 액션마다 현재 활성 그래프들만을 대상으로 heatmap 색상을 즉시 재계산·재배치

## 변경 파일

| 파일 | 변경 내용 |
|------|-----------|
| `battery_lab/ui.py` | 5군데 수정 (아래 상세) |
| `tests/test_ui.py` | assert 문자열 1개 업데이트 |

---

## 변경 상세

### 1. SVG 요소에 식별 속성 추가 (`ui.py`)

JS가 fit-shape 등 다른 색상 요소를 건드리지 않도록, 재배색 대상 SVG 요소에 명시적 속성 추가:

- **main path** (`overlay_viewer_svg` 내): `data-series-line` 속성 추가
  - 대상: `<path fill="none" stroke="{color}" .../>` (시리즈 선)
- **markers** (`overlay_marker_svg`): `data-series-marker` 속성 추가
  - 대상: `<circle .../>` (원형 마커), `<rect .../>` (사각형 마커)
  - fit-shape 내부의 circle/path는 이 속성이 없으므로 재배색에서 제외됨

### 2. 테이블 행에 `data-sort-value` 추가 (`ui.py`)

JS heatmap 계산의 기준값을 각 `<tr>`에 embed:

- **`eis_overlay_table()`** (EIS 비교 모드): `areal_mass_density` 값
- **`eis_overlay_table()`** (EIS 시계열 모드): `time_hours` 값
- **`capacity_overlay_table()`**: `areal_mass_density` 값
- 값이 없으면 `data-sort-value` 속성 자체를 생략 → JS에서 `#64748b` (회색) 처리

### 3. Shell div에 `data-color-mode` 추가 (`ui.py`)

`overlay_viewer_html()`의 외부 div에 `data-color-mode="{color_mode}"` 추가.  
JS가 이를 읽어 올바른 heatmap 함수를 선택함.

### 4. JS 토글 로직 전면 교체 (`ui.py` 내 `overlay_viewer_html` f-string)

#### 추가된 함수들

```
rgbToHex(channels)          - [r,g,b] → "#rrggbb" 변환
heatmapColor(ratio)         - comparison/capacity: blue→cyan→green→amber→red
applySeriesColor(id, color) - SVG g[data-series-id] 내 data-series-line/marker/label-* 업데이트
recolorActiveSeries()       - 핵심 함수, 매 토글 후 호출
```

#### `recolorActiveSeries()` 동작

1. 전체 행을 active / inactive로 분류
2. **inactive 행**: swatch → `background: #ffffff; outline: 1px solid #cbd5e1`
3. **active 행 (comparison/capacity 모드)**: sort_value 기준 min~max 정규화 → `heatmapColor(ratio)`
4. **active 행 (time_series 모드)**: sort_value(time_hours) 기준 rank 정렬 → light-red(`#fecaca`) ~ dark-red(`#991b1b`) (Python `red_time_series_color` 로직과 동일)
5. 각 active 행: swatch 색상 업데이트 + 해당 SVG series_id의 모든 line/marker/label 요소 색상 업데이트

#### 수정된 기존 함수들

| 함수 | 변경 |
|------|------|
| `_setRowState(row, active)` | (신규) display 토글 + inactive-row class만 처리, recolor 없음 |
| `setRowActive(row, active)` | `_setRowState` 호출 후 `recolorActiveSeries()` |
| `setAllSeriesActive(active)` | 모든 행 `_setRowState` 일괄처리 후 `recolorActiveSeries()` 1회 (N²→N 개선) |
| `applyRowToggle(row)` | `setRowActive` 대신 `_setRowState` 사용 (drag 중 recolor는 별도로 호출) |

#### 이벤트 핸들러 변경

- `pointerdown`: `applyRowToggle` 후 → `recolorActiveSeries()` 즉시 호출
- `pointermove` (drag): `applyRowToggle` 후 → `recolorActiveSeries()` 호출 (드래그 중 실시간 재배색)
- `root.pointerup`: 변경 없음 (drag 종료 처리)

---

## 성능 분석

매 액션마다 `recolorActiveSeries()` 호출 시 비용:

- DOM 조회: O(n) — n = 시리즈 수 (보통 5~20개)
- 색상 계산: O(n) — 단순 산술
- DOM 업데이트: O(n × k) — k = 시리즈당 SVG 요소 수 (≈ 3~10개)
- **총합: 시리즈 20개 기준 약 ~200 DOM ops → 1ms 미만**, drag 60fps에서도 전혀 문제 없음

---

## 테스트 결과

```
tests/test_ui.py          13 passed ✓  (test_ui.py::assertIn 문자열 1개 업데이트)
전체 테스트 suite         123 passed ✓  (4.17s)
```

`tests/test_ui.py` 변경 내용:
```python
# 기존
self.assertIn("<rect data-zoom-radius", html)
# 변경 후
self.assertIn("<rect data-series-marker data-zoom-radius", html)
```
→ 새 속성 순서를 반영한 업데이트. 기능 의미는 동일 (사각형 마커 존재 확인).

---

## 동작 확인 방법 (기상 후 수동 검증)

1. 서버 실행: `cd battery-lab-automation && .venv/bin/python -m flask run`
2. EIS 비교 그래프 뷰어 열기 → 그래프 선택 → 오버레이 열기
3. **Hide all** 클릭 → 모든 행의 동그라미가 흰색(회색 테두리)으로 바뀌는지 확인
4. 몇 개 행 클릭/드래그로 활성화 → 활성화된 행들만 heatmap 색상으로 재배치되는지 확인
5. **Show all** 클릭 → 전체 행이 heatmap 색상으로 복귀하는지 확인
6. EIS 시계열 모드에서 동일 테스트 → 색상이 light-red ~ dark-red 범위 내에서 재배치되는지 확인
7. Capacity 뷰어에서도 동일 테스트

---

## 엣지 케이스 처리

| 상황 | 처리 |
|------|------|
| 모든 그래프 hide | 전부 흰색 dot, SVG 재배색 없음 |
| 활성 그래프 1개 | comparison: ratio=0.5 → 중간색(초록), time_series: ratio=1.0 → 가장 진한 red |
| areal_density 없는 시리즈 | `data-sort-value` 없음 → `#64748b` 회색으로 표시 |
| 동일한 density 값 여러 개 | 동일 색상 배정 (Python의 `vary_similar_color` 미세조정은 초기 렌더 후 JS에서는 생략) |
