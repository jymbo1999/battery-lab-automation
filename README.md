# 배터리 실험 자동 정리 MVP

`Project_Abstract`에 있는 셀 조건표, Capacity CSV, Voltage profile CSV, EIS XLSX/SDE를 읽어서 그래프와 핵심 지표를 자동으로 정리하는 로컬 도구입니다.

이 버전의 목표는 “AI 예측”이 아니라, 먼저 실험자가 과거 데이터를 빠르게 꺼내 보고 비교할 수 있도록 만드는 것입니다.

## 핵심 기능

- 파일명과 컬럼명 기반 자동 분류: Capacity / Voltage profile / EIS / 면저항
- 셀 조건표의 `Sample`, `Areal mass density`, `합제밀도`, `전해질`, `Binder`, `Voltage range`, `ratio` 매칭
- Capacity 지표: ICE, discharge capacity, retention, CE 평균/표준편차, fade slope
- Voltage profile: 선택 cycle별 capacity-voltage 곡선
- EIS: Z' vs -Z'' Nyquist plot, rough Rs/Rct screening
- HTML 리포트와 `summary_metrics.csv` 자동 생성
- 업로드 날짜/조건표 날짜 기준의 날짜별 실험 일지 자동 생성
- Streamlit 한글 인터페이스 준비

## 바로 실행

```bash
export BATTERY_DATA_ROOT=/var/data/battery
export BATTERY_OUTPUT_ROOT="$BATTERY_DATA_ROOT/battery_visual_outputs"
export BATTERY_JOURNAL_ROOT="$BATTERY_OUTPUT_ROOT/lab_journal"
export BATTERY_CONDITION_WORKBOOK="/var/data/battery/Project_Abstract/Cell condition Calculation.xlsx"
python3 -m battery_lab.cli "$BATTERY_DATA_ROOT" \
  --conditions "$BATTERY_CONDITION_WORKBOOK"
```

결과물:

```text
$BATTERY_OUTPUT_ROOT/
├─ capacity/
├─ voltage_profile/
├─ eis/
├─ sheet_resistance/
├─ summary_metrics.csv
└─ report.html
```

기본 실행 시 날짜별 일지도 함께 생성됩니다.

```text
$BATTERY_JOURNAL_ROOT/
├─ index.html
├─ journal_manifest.csv
└─ 2026-06-15/
   ├─ dashboard.html
   ├─ report.html
   └─ summary_metrics.csv
```

일지 폴더를 바꾸려면 `--journal my_journal`, 일지 생성을 끄려면 `--no-journal`를 사용합니다.

## 참고자료에서 확인한 실제 형식

Capacity CSV:

- `Cycle`
- `Q_Ch/M [mAh/g]`
- `Q_Dis/M [mAh/g]`

Voltage profile CSV:

- 첫 번째 줄: `1st Ch`, `1st Dis`, `10th Ch` 같은 cycle/direction
- 두 번째 줄: `V [V]`, `Q [mAh]`, `Q/M [mAh/g]`
- 그래프는 `Q/M [mAh/g]`를 x축, `V [V]`를 y축으로 사용

EIS XLSX:

- `Z'_raw [Ohm]`을 x축
- `Z"_raw [Ohm]`에 음수 부호를 붙여 y축
- `Rs/Rct_auto`는 screening용 rough 값

조건표 XLSX:

- `Sample`이 셀 이름 역할
- `Areal mass density (mg/cm2)`가 비교 가능성의 핵심
- `합제밀도(g/cm3)`는 그래프 아래 조건표에 표시
- `전해질`, `Binder`, `Voltage range`, `ratio`가 같은 셀끼리만 비교 권장

## Streamlit 앱

의존성을 설치할 수 있는 환경에서는:

```bash
python3 -m pip install -r requirements.txt
streamlit run app.py
```

앱에서는 파일 업로드 후 `대시보드 미리보기`가 바로 열리고, `날짜별 실험 일지 생성`을 켜 둔 상태라면 같은 실행 결과가 `BATTERY_JOURNAL_ROOT` 아래에도 날짜별로 정리됩니다.

## GPT 분석 준비

Flask 통합 화면의 Settings에서 GPT 분석 스모크를 실행할 수 있습니다. 기본값은 dry-run이며, 프롬프트와 산출물 스냅샷만 `battery_ai_runs`에 저장하고 OpenAI API는 호출하지 않습니다.

실제 API smoke는 아래 조건이 모두 맞을 때만 실행됩니다.

```bash
export BATTERY_AI_ENABLE_API=1
export OPENAI_API_KEY=...
export BATTERY_AI_MODEL=gpt-5.5
export BATTERY_AI_TIMEOUT_SECONDS=20
export BATTERY_AI_MAX_RETRIES=1
export BATTERY_AI_MAX_INPUT_CHARS=12000
```

`openai` Python SDK는 선택 의존성입니다. SDK, API key, enable flag 중 하나라도 없으면 API smoke는 `skipped`로 DB에 기록되고 실제 호출은 일어나지 않습니다.

## 주의

- 현재 참고자료는 같은 셀의 완전한 세트가 아니라 샘플 파일이 섞여 있습니다. 따라서 리포트는 “시각화/처리 예시”와 “조건 매칭 상태”를 보여주는 용도로 먼저 봐야 합니다.
- 논문/발표용 Rct는 ZMAN/ZView equivalent circuit fitting 값을 수동 업로드하거나 별도 검증하는 것이 안전합니다.
