# 영구 증분 렌더 캐시 엔진 (Phase 1) — 설계 문서

- 날짜: 2026-06-23
- 상태: 설계 승인됨 (구현 plan 작성 대기)
- 범위: Battery Lab 그래프 뷰어의 렌더 성능 문제 해결 (Phase 1)
- 관련 로드맵: Phase 0(메타데이터 스키마) · Phase 2(업로드 UI) · Phase 3(일지 자동기입) · Phase 4(구조화 클러스터링)는 **별도 spec**으로 다룬다.

---

## 1. 배경 / 문제

그래프 뷰어를 열 때마다 로딩이 오래 걸린다. 원인은 매 요청마다 처음부터 전부 다시 계산하기 때문이다. `/api/eis/viewer/overlay`, `/api/capacity/viewer/overlay` 요청 흐름을 추적한 결과 3대 병목을 확인했다.

1. **워커별 메모리 캐시라 거의 안 먹힘.** 현재 캐시는 `ui.py`의 `@lru_cache`(파일 파싱)뿐인데, 이는 gunicorn **워커 프로세스 메모리 안**에 있다. 워커가 여러 개라 A워커가 데운 캐시를 다음 요청은 B워커(콜드)가 받고, **재배포·재시작 시 전부 소실**된다.
2. **매 요청마다 전체 재분류.** `build_eis_match_report` / `build_capacity_match_report`가 매번 EIS 312개·capacity 248개 **전부**를 다시 분류하고, 조건 워크북(xlsx)도 다시 읽는다.
3. **매 요청마다 전체 SVG 재생성.** `eis_overlay_html` / `capacity_overlay_html`가 클러스터 전체 곡선을 매번 통째로 다시 그린다. 특히 `C999` / `P999`("all datasets")는 전체 파일을 파싱+렌더해 거대한 SVG 하나를 만들어 최악이다.

분류(classification)는 파일명·조건 기반이라 비교적 가볍지만, **파싱 + SVG 렌더**가 통증의 본체다.

### 확정된 전제
- 배포 환경에서 `battery_visual_outputs/`(= `BATTERY_OUTPUT_ROOT`, `config.py`)는 **영구 디스크**로 유지된다. (이미 match override JSON이 여기 저장되어 유지되고 있음.)

---

## 2. 목표 / 비목표

### 목표
- 클러스터 그래프를 **최초 1회만 렌더**하고, 이후 같은 클러스터를 열면 저장된 결과를 **즉시(거의 0ms)** 반환한다.
- 새 데이터 파일이 추가되면 **그 파일이 속한 클러스터만** 재계산하고, 나머지는 캐시를 그대로 서빙한다.
- 캐시는 **영구 디스크에 저장**되어 재배포·재시작 후에도 유지되고, **모든 gunicorn 워커가 공유**한다.
- 정확성: 파일/조건이 바뀌면 **반드시** 새로 계산된 결과가 나온다 (stale 금지).

### 비목표 (Phase 1에서 하지 않음)
- 업로드 UI / 파일명 표준화 / 실험정보 입력 (Phase 2)
- 메인 실험일지 엑셀 자동 기입 (Phase 3)
- 클러스터링 로직 변경 (Phase 4). 기존 `build_*_match_report`의 cluster_id 체계를 **그대로** 사용한다.
- 렌더 결과물(SVG/HTML)의 시각적 변경. 캐시는 기존 출력과 **바이트 동일**해야 한다.

---

## 3. 설계 개요

캐시 키 자체를 **"내용 식별자"**로 만든다. 새 파일이 들어오거나 파일이 바뀌면 키가 달라져 **자동으로 miss → 재계산**된다. 따라서 능동적 무효화(invalidation) 로직이 없어도 정확성이 보장되고, 디스크 용량 관리를 위한 **청소(GC)만** 별도로 돌린다.

캐시는 3층으로 구성하고 모두 `battery_visual_outputs/.render_cache/` 아래에 둔다.

| 층 | 키 | 내용 | hit 시 스킵하는 작업 |
|---|---|---|---|
| **분류 report** | `(kind, 정렬된 relpath들, context_hash)` | 클러스터 ↔ 멤버 매핑 (match report 직렬화) | 재분류 + 조건 워크북 재읽기 |
| **파일 파싱** | `(relpath, mtime, size)` | 파싱된 Dataset (정규화 데이터) | 원본 파일 파싱 |
| **클러스터 렌더** | `(kind, mode, cluster_id, membersig, context_hash, flags)` | 최종 HTML payload | 파싱 + SVG 렌더 |

---

## 4. 상세 설계

### 4.1 저장소 레이아웃

```
battery_visual_outputs/.render_cache/
  v1/                                  # CACHE_VERSION
    reports/{report_key}.json
    parsed/{parsed_key}.json
    clusters/{kind}/{mode}/{safe_cluster_id}/{membersig}__{flags}.json
```

- `v1`은 `CACHE_VERSION`. 렌더/파싱 로직이 바뀌면 버전을 올린다. 이전 `v*` 디렉터리는 통째로 삭제 가능.
- `{safe_cluster_id}`: cluster_id를 파일시스템 안전 문자열로 정규화한 값.

### 4.2 키 생성

모든 키는 입력을 정규화(`json.dumps(..., sort_keys=True, ensure_ascii=False)`)한 뒤 `sha1` 해시.

> **버전 격리는 §4.1의 `v{N}/` 디렉터리로만 제공한다.** 모든 캐시 파일이 `v{N}/` 아래에 있으므로 `CACHE_VERSION`을 키 입력에 넣지 않는다(중복 방지). 렌더/파싱 로직이 바뀌면 버전을 올려 디렉터리째 갈아끼운다.

- **파일 식별자** `file_identity(path, root) = (relpath_str, mtime_ns, size)`
  - `os.stat`만 사용 → 2.7GB 데이터를 해싱하지 않는다. 기존 `ui.py:parse_file_cached_by_mtime(path, mtime_ns, size)`의 키와 동일한 철학.
  - 트레이드오프: 내용은 같은데 mtime만 바뀌면 재파싱(드물고 허용). 파일 경로가 바뀌면 miss(허용).
- **`parsed_key`** = `sha1(file_identity)`
- **`context_hash`** = `sha1([stat_tuple(condition_workbook), stat_tuple(override_json)])`
  - `stat_tuple(p)` = `(mtime_ns, size)` 또는 파일 없으면 `None`.
  - 조건 워크북이나 override가 바뀌면 라벨·색·분류가 달라지므로 report/cluster 캐시가 자동 갱신된다.
- **`report_key`** = `sha1([kind, sorted(relpaths), context_hash])`
  - 분류는 파일 **집합(relpath) + 조건 + override**의 순수 함수이므로 mtime은 키에 넣지 않는다.
- **`membersig`** = `sha1(sorted([file_identity(p) for p in members]))`
  - 순서 무관. 멤버 중 하나라도 mtime/size가 바뀌면 membersig가 바뀐다.
- **`cluster_key`** = `sha1([kind, mode, cluster_id, membersig, context_hash, flags])`
  - `flags`: 렌더에 영향 주는 추가 파라미터. EIS는 `{"show_fit": bool}`. Capacity는 `{}`.

### 4.3 읽기 경로 (overlay)

```
1. report = get_or_build_report(kind, ...)        # report 캐시 (4.5)
2. (mode, cluster_id, members) = resolve_target(report, mode, key)
3. membersig = compute_membersig(members)
4. ckey = make_cluster_key(kind, mode, cluster_id, membersig, context_hash, flags)
5. cached = read_json(clusters_path(ckey))
6. if cached is not None: return cached            # HIT → 즉시 반환
7. payload = <기존 overlay 계산>                    # MISS
       - 멤버 파일별로 parsed 캐시 조회/저장 (4.4)
       - 변경되지 않은 파일은 parsed 캐시 재사용, 새 파일만 파싱
       - 기존 eis_overlay_html / capacity_overlay_html 로 렌더
8. atomic_write_json(clusters_path(ckey), payload)
9. gc_cluster_dir(kind, mode, cluster_id, keep=membersig)   # 4.7
10. return payload
```

- **단일 source 모드**(`eis_source_payload`, `capacity_source_payload`)도 멤버 집합 = 파일 1개로 보고 같은 메커니즘으로 캐시한다.

### 4.4 파일 파싱 캐시

- `parse_file(path)` 결과(Dataset)를 `parsed/{parsed_key}.json`에 직렬화한다.
- 직렬화 경계: **Dataset**을 기본으로 한다(컨텍스트 독립적 = 최대 재사용). 구현 시 `dataclasses.asdict`로 직렬화하고 역직렬화 함수를 만든다.
  - 구현 메모: Dataset에 JSON 비호환 필드가 있으면, JSON 가능한 **series 표현**(load_*_overlay_series의 출력)으로 경계를 낮추고 그 경우 키에 color_mode/conditions 식별자를 추가한다. 기본은 Dataset 경계.
- 기존 인메모리 `@lru_cache`는 유지(프로세스 내 재사용). 디스크 캐시는 그 위에 둬서 **콜드 워커·재배포 후에도** 재사용되게 한다.

### 4.5 분류 report 캐시

- `build_eis_viewer_report` / `build_capacity_viewer_report`를 래핑한다.
- `report_key`로 조회 → hit이면 직렬화된 report를 역직렬화해 반환(조건 워크북 재읽기 + 재매칭 스킵).
- miss면 기존 빌더 실행 → 결과 직렬화 저장.
- report dataclass(`EISMatchReport` 등)는 `asdict`로 직렬화하고 역직렬화 헬퍼를 만든다. (overlay 경로에서 report의 어떤 필드가 실제로 쓰이는지 확인해 필요한 부분만 직렬화해도 됨.)

### 4.6 증분 동작 (새 파일 N개 추가)

- 새 파일이 폴더에 들어오면 파일 집합(relpaths)이 바뀜 → `report_key`가 바뀜 → report 재계산(가벼움: 파일명·조건 기반).
- 새 파일이 속한 클러스터는 멤버가 늘어 `membersig`가 바뀜 → 그 `cluster_key`만 miss → **그 클러스터만 재렌더**.
  - 재렌더 시 기존 멤버 파일들은 `parsed` 캐시 hit → **새 파일 N개만 파싱**. 정확히 "추가분만 계산".
- 다른 클러스터는 membersig 불변 → 캐시 그대로 즉시 서빙.

### 4.7 동시성 · 원자성 · GC

- **쓰기**: `tmp = path + ".tmp.{pid}.{uuid}"` 에 쓰고 `os.replace(tmp, path)` (POSIX atomic rename). 락 불필요.
- **읽기**: 파일을 읽어 JSON 파싱. `FileNotFoundError` / `JSONDecodeError` → miss로 처리(재계산). 손상 내성.
- **중복 계산**: 두 워커가 동시에 같은 키를 miss하면 둘 다 계산·기록한다(결과 동일). 1회 중복 작업은 허용. (선택적 per-key 락 파일은 도입하지 않음 — 단순성 우선.)
- **GC**: 클러스터를 새로 쓸 때 같은 `clusters/{kind}/{mode}/{safe_cluster_id}/` 디렉터리에서 현재 `membersig`가 아닌 파일을 삭제한다. `parsed/`는 용량이 커지면 오래된(=현재 manifest에 없는) 엔트리를 주기적으로 정리(저우선 후속).
- **버전**: `CACHE_VERSION` 상향 시 새 `v{N}` 디렉터리를 쓰고, 기동 시 이전 `v*`를 비동기 삭제(선택).

### 4.8 예열 훅 (Phase 2 연결점)

```python
def register_sources(kind: str, relpaths: list[str]) -> None:
    """업로드 등으로 새 파일이 추가됐을 때 호출. 해당 파일이 속한
    클러스터를 백그라운드로 미리 렌더해 캐시에 채운다 (lazy로도 동작하므로 선택적)."""
```

- Phase 1에서는 **lazy(처음 열 때 계산)**가 기본. `register_sources`는 인터페이스만 만들어 두고, 내부는 기존 `warm_overlay_cache`(ui.py) 백그라운드 스레드 패턴을 재활용한다.
- Phase 1 단독으로도, **폴백 자동감지**가 동작한다: 별도 호출 없이도 폴더 스캔 결과가 키에 반영되므로 새/변경 파일이 자동으로 miss→재계산된다.

---

## 5. 통합 지점 (기존 코드)

- **신규 모듈** `battery_lab/render_cache.py`: 키 생성, read/atomic-write, GC, report/parsed/cluster 캐시 헬퍼, `register_sources`.
- **래핑 대상** (`battery_lab/viewer_service.py`):
  - `eis_overlay_payload`, `capacity_overlay_payload` → 클러스터 렌더 캐시
  - `eis_viewer_options`, `capacity_viewer_options` → report 캐시 (내부 `build_*_viewer_report` 경유)
  - `eis_source_payload`, `capacity_source_payload` → 단일 source 캐시
- **재활용**: `ui.py`의 `parse_file_cached` / `warm_overlay_cache`.
- **설정**: 캐시 루트 = `BATTERY_OUTPUT_ROOT / ".render_cache"`. 환경변수 `BATTERY_RENDER_CACHE_DISABLE`로 끄는 스위치 추가(디버깅/비교용).

라우트(`routes.py`)는 변경 없음 — 뷰어 서비스 함수 시그니처를 유지한 채 내부만 캐시 경유로 바꾼다.

---

## 6. 에러 처리 / 폴백

- 캐시 디스크 쓰기 실패(권한/용량) → 경고 로그 후 **계산 결과를 그대로 반환**(기능 저하 없이 동작). 캐시는 best-effort.
- 캐시 읽기 손상 → miss로 처리(재계산).
- `BATTERY_RENDER_CACHE_DISABLE=1` → 전 캐시 우회, 기존 동작과 동일.

---

## 7. 테스트 계획

- **키 함수 단위**: 동일 입력 → 동일 키; mtime/size 변하면 `parsed_key`·`membersig` 변함; 멤버 순서 바뀌어도 membersig 동일.
- **캐시 hit**: 같은 overlay를 2회 호출 → 실제 렌더 함수(`eis_overlay_html` 등) 호출 **1회**만 (monkeypatch 카운터).
- **증분**: 클러스터 3파일 렌더 → 4번째 파일 추가 → 그 클러스터만 재렌더되고, 기존 3파일은 `parse_file` 재호출 **없음**; 다른 클러스터 캐시 무손상.
- **컨텍스트 변경**: 조건 워크북 stat 변경 → 관련 report/cluster 캐시 miss·재계산.
- **원자성/동시성**: 두 스레드가 동일 키 동시 miss → 둘 다 정상 결과, 최종 파일 유효.
- **버전**: `CACHE_VERSION` 상향 → 전부 재계산, 새 `v{N}`에 기록.
- **출력 동일성**: 캐시 on/off 결과 HTML 바이트 동일(회귀 방지).
- **GC**: 멤버 변경 후 옛 membersig 파일이 삭제되는지.

---

## 8. 열린 질문 / 후속

- (구현 시 결정) `parsed` 직렬화 경계: Dataset vs series. 기본 Dataset, 비호환 시 series로 낮춤(§4.4).
- `parsed/` GC 정책(용량 상한, LRU 삭제)은 저우선 후속으로 분리 가능.
- Phase 2 업로드가 `register_sources`를 호출해 **업로드 직후 예열**하는 흐름은 Phase 2 spec에서 상세화.
