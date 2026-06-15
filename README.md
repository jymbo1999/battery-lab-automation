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
- Streamlit 한글 인터페이스 준비

## 바로 실행

```bash
cd "/Users/haesungjun/VSCODE Library/BBATTAERRI/battery-lab-automation"
python3 -m battery_lab.cli "../Project_Abstract" \
  --conditions "../Project_Abstract/Cell condition Calculation 일부.xlsx" \
  --output battery_visual_outputs
```

결과물:

```text
battery_visual_outputs/
├─ capacity/
├─ voltage_profile/
├─ eis/
├─ sheet_resistance/
├─ summary_metrics.csv
└─ report.html
```

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

## 주의

- 현재 참고자료는 같은 셀의 완전한 세트가 아니라 샘플 파일이 섞여 있습니다. 따라서 리포트는 “시각화/처리 예시”와 “조건 매칭 상태”를 보여주는 용도로 먼저 봐야 합니다.
- 논문/발표용 Rct는 ZMAN/ZView equivalent circuit fitting 값을 수동 업로드하거나 별도 검증하는 것이 안전합니다.
