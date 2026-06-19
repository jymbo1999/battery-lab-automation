# Battery Lab Flask Migration Handoff

## 현재 상황

- 목표: 기존 `streamlit run app.py`로 쓰던 Battery Lab 인터페이스와 기능을 iChart Render Flask 앱의 `/battery` 아래에 kiwoom-sector-board처럼 Flask-native 모듈로 이전한다.
- 접근: Streamlit 서버를 억지로 Flask 안에 넣지 않고, Streamlit에서 검증된 UX와 로직을 Flask `Blueprint + templates + API + services + DB/jobs` 구조로 옮긴다.
- 이유: 앞으로 GPT API, DB 저장, 파일 위치 이동, background job, 수동 매칭, 그래프 재생성 같은 기능이 계속 추가될 예정이므로 Flask-native 구조가 유지보수에 유리하다.

## 관련 repo

- Battery package repo: `/Users/haesungjun/VSCODE Library/BBATTAERRI/battery-lab-automation`
- Main Flask/iChart repo: `/Users/haesungjun/VSCODE Library/flask-star-admin-master`
- Kiwoom reference repo: `/Users/haesungjun/VSCODE Library/kiwoom-sector-board`

## 최신 배포 커밋

- Battery repo latest pushed commit:
  - `e9ec836844e4124082c209e65a2c0dfd5dab0c4e`
  - message: `Make Battery Lab route show app UI`
- Main Flask repo latest pushed commit:
  - `0556798b12fb18d9b241d6d3ce254770fc70e241`
  - message: `Pin Battery Lab app UI revision`
- Main Flask `requirements.txt` pin:
  - `git+https://github.com/jymbo1999/battery-lab-automation.git@e9ec836844e4124082c209e65a2c0dfd5dab0c4e#egg=battery-lab-automation`

## 이미 완료된 것

- Battery package가 Render에 설치되도록 `pyproject.toml` 정리:
  - `battery_lab`
  - `wonatech_parsers`
  - root `app.py`
  - templates
- 누락됐던 핵심 모듈을 Git/package에 포함:
  - `battery_lab/ui.py`
  - `battery_lab/excel_dashboard.py`
  - `battery_lab/eis_matching.py`
  - `battery_lab/capacity_matching.py`
  - `battery_lab/wonatech_service.py`
  - `wonatech_parsers/`
- `/battery`가 단순 데이터 연결 상태 페이지가 아니라 Flask 앱 화면을 렌더하도록 변경:
  - sidebar
  - 실험 일지
  - dashboard
  - files
  - EIS
  - Capacity
  - Voltage Profiles
- 진단 route 유지:
  - `/battery/status`
  - `/battery/files`
  - `/battery/health`
- artifact serving 추가:
  - `/battery/output/<path>`
  - `/battery/artifact/<analysis>/<path>`
- Render persistent disk 경로 사용하도록 일부 경로 수정:
  - `BATTERY_DATA_ROOT`
  - `BATTERY_EIS_ROOT`
  - `BATTERY_CAPACITY_ROOT`
  - `BATTERY_OUTPUT_ROOT`
  - `BATTERY_CONDITION_WORKBOOK`

## 중요한 주의점

- 3GB 원본 데이터와 생성 산출물은 Git에 넣지 않는다.
- 원본/산출물은 Render Persistent Disk에 둔다.
- DB에는 파일 내용이 아니라 경로, 해시, 메타데이터, 매칭 상태, job 상태를 저장한다.
- `.eisfit.json`, `eis_match_overrides.json`, `capacity_match_overrides.json` 같은 sidecar/override 파일은 persistent disk에 유지한다.
- 앞으로는 JSON sidecar를 유지하되 DB source of truth로 점진 이전하는 것이 좋다.

## Render 데이터 경로 목표

```text
/var/data/battery/
├── EIS/
├── capacity/
├── battery_visual_outputs/
│   ├── eis/
│   ├── capacity/
│   ├── dashboard.html
│   ├── report.html
│   ├── eis_match_overrides.json
│   └── capacity_match_overrides.json
└── Project_Abstract/
    └── Cell condition Calculation.xlsx
```

## Render 환경변수 목표

```text
ENABLE_BATTERY_MODULE=1
DISABLE_STARTUP_SCHEMA=1
BATTERY_DATA_ROOT=/var/data/battery
BATTERY_EIS_ROOT=/var/data/battery/EIS
BATTERY_CAPACITY_ROOT=/var/data/battery/capacity
BATTERY_OUTPUT_ROOT=/var/data/battery/battery_visual_outputs
BATTERY_CONDITION_WORKBOOK=/var/data/battery/Project_Abstract/Cell condition Calculation.xlsx
```

## 새 대화창에서 먼저 확인할 Render Shell 명령

```bash
python - <<'PY'
from pathlib import Path
import importlib.metadata as md
import battery_lab

root = Path(battery_lab.__file__).resolve().parent
print("version:", md.version("battery-lab-automation"))
print("root:", root)
print("app template:", (root / "templates" / "battery_lab" / "app.html").exists())
PY
```

정상 기대값:

```text
version: 0.1.3
app template: True
```

```bash
ENABLE_BATTERY_MODULE=1 DISABLE_STARTUP_SCHEMA=1 python - <<'PY'
from run import app

with app.test_client() as c:
    for path in ["/battery/", "/battery/?tab=eis", "/battery/?tab=capacity", "/battery/?tab=dashboard", "/battery/status"]:
        r = c.get(path)
        text = r.data.decode("utf-8", errors="replace")
        print(path, r.status_code, {
            "app": "EIS source files" in text,
            "eis": "EIS 그래프 보기" in text,
            "capacity": "Capacity 그래프 보기" in text,
            "status": "Render Persistent Disk" in text,
        })
PY
```

## 최종 목표 아키텍처

```text
iChart Flask app
└── /battery
    ├── Flask Blueprint
    ├── templates
    ├── API routes
    ├── services
    ├── DB models
    ├── background jobs
    └── persistent disk files
```

## Migration Roadmap

### Phase 1. 현 기능 목록과 화면 맵 고정

- Streamlit `app.py`와 `battery_lab/ui.py` 기준으로 기능 목록을 만든다.
- 각 기능을 Flask route/API/template 단위로 매핑한다.
- 우선순위:
  1. EIS 그래프 보기
  2. Capacity 그래프 보기
  3. 대시보드/리포트 보기
  4. EIS 수동 매칭
  5. Capacity 수동 매칭
  6. 그래프 재생성
  7. EIS fitting batch
  8. 실험일지 엑셀 편집
  9. GPT 분석

### Phase 2. 경로 계층 완전 정리

- `battery_lab/ui.py`, `excel_dashboard.py`, matching modules, report modules 전체에서 로컬 하드코딩 경로 제거.
- 모든 경로는 `battery_lab/config.py`에서 가져오도록 통일.
- Render와 로컬에서 같은 코드가 동작해야 한다.

### Phase 3. Flask UI 완성

- `/battery`를 앱 홈으로 유지한다.
- 페이지 후보:
  - `/battery`
  - `/battery/files`
  - `/battery/eis`
  - `/battery/capacity`
  - `/battery/journal`
  - `/battery/jobs`
  - `/battery/settings`
- 현재는 query tab 기반이므로, 이후 route 분리로 정리한다.

### Phase 4. 수동 매칭 UI 이전

- Streamlit `st.data_editor` 기반 수동 매칭을 Flask table + JS로 이전.
- API 후보:
  - `GET /battery/api/eis/matches`
  - `POST /battery/api/eis/matches`
  - `GET /battery/api/capacity/matches`
  - `POST /battery/api/capacity/matches`
- 저장:
  - 단기: 기존 JSON 유지
  - 중기: DB 저장 + JSON import/export

### Phase 5. Job 시스템

- 긴 작업은 request thread에서 직접 실행하지 않는다.
- 대상:
  - 파일 재스캔
  - EIS graph build
  - Capacity graph build
  - EIS fitting batch
  - GPT 분석
- DB 테이블 후보:
  - `battery_jobs`
  - `battery_job_logs`
- API 후보:
  - `POST /battery/api/jobs/rescan`
  - `POST /battery/api/jobs/build-eis`
  - `POST /battery/api/jobs/build-capacity`
  - `POST /battery/api/jobs/eis-fitting`
  - `GET /battery/api/jobs/<id>`

### Phase 6. DB 모델

초기 테이블 후보:

```text
battery_files
battery_artifacts
battery_file_metadata
battery_match_overrides
battery_jobs
battery_job_logs
battery_experiments
battery_ai_runs
battery_ai_annotations
```

원칙:

- 파일 blob은 DB에 넣지 않는다.
- DB에는 path, relative_path, hash, size, mtime, type, parsed metadata, match status만 둔다.
- 파일 이동/이름 변경 기능을 만들 때 DB와 filesystem을 함께 transaction처럼 다룬다.

### Phase 7. GPT API

GPT 기능은 UI에서 바로 호출하지 말고 service/job으로 둔다.

기능 후보:

- EIS 이상 패턴 설명
- Rs/Rct 변화 요약
- Capacity degradation 요약
- 실험 조건별 비교 리포트
- 파일명-실험일지 매칭 추천
- 논문식 해석 초안 생성

저장 후보:

```text
battery_ai_runs
battery_ai_annotations
```

## 다음 구현 우선순위

1. `/battery/eis`, `/battery/capacity` route 분리 및 UI polish
2. artifact list 검색/필터/정렬 추가
3. manual match UI 이전
4. graph rebuild job화
5. DB file index 도입
6. experiment journal DB import
7. GPT 분석 job 추가

## 새 대화창 시작 프롬프트

아래 순서대로 입력하면 된다.

### Prompt 1

```text
cd "/Users/haesungjun/VSCODE Library/BBATTAERRI/battery-lab-automation"

BATTERY_FLASK_MIGRATION_HANDOFF.md를 먼저 읽고, 현재 Battery Lab Flask migration 상황을 요약해줘.
수정하지 말고, repo 상태와 최신 커밋/파일 구조만 확인해서 다음 구현 우선순위를 제안해줘.
```

### Prompt 2

```text
이제 Phase 2부터 시작하자.
목표는 Battery Lab 코드 전체에서 로컬 하드코딩 경로를 제거하고, 모든 데이터/출력/조건표 경로를 battery_lab/config.py 기준으로 통일하는 것이다.
수정 전에는 rg로 경로 사용처를 조사하고, 데이터 파일은 절대 stage하지 마.
수정 후에는 py_compile, Flask test_client smoke, 관련 pytest를 돌려줘.
```

### Prompt 3

```text
다음은 Phase 3이다.
현재 /battery query-tab UI를 kiwoom-sector-board처럼 명확한 Flask route 구조로 정리해줘.
후보 route는 /battery, /battery/eis, /battery/capacity, /battery/journal, /battery/files, /battery/jobs, /battery/settings.
기존 /battery/status, /battery/health는 유지.
첫 목표는 EIS/Capacity 그래프 보기 UX를 Streamlit 원본에 가깝게 만드는 것이다.
```

### Prompt 4

```text
다음은 수동 매칭 UI 이전이다.
Streamlit의 EIS/Capacity manual match data_editor 흐름을 읽고,
Flask template + JS + API로 옮기는 구현 계획을 먼저 작성한 뒤 진행해줘.
저장은 기존 JSON override와 호환되게 하고, 이후 DB 이전 가능하도록 service layer를 분리해줘.
```

### Prompt 5

```text
다음은 job 시스템 설계와 1차 구현이다.
그래프 재생성, EIS fitting batch, 파일 재스캔을 request thread에서 직접 돌리지 않고 battery_jobs로 관리하도록 설계해줘.
우선 SQLite/Postgres 호환 SQLAlchemy 모델과 status API, jobs 화면부터 구현해줘.
```

### Prompt 6

```text
다음은 GPT API 연동 준비다.
Battery Lab 분석 결과를 GPT로 요약/해석하는 기능을 붙일 수 있게 service interface, prompt template, DB 저장 구조를 설계해줘.
아직 실제 API 호출은 최소 smoke까지만 하고, 키/비용/timeout/retry 정책을 안전하게 설계해줘.
```

