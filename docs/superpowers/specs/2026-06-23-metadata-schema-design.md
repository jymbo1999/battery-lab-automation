# 메타데이터 스키마 + 표준 파일명 + 확인 항목 모델 (Phase 0) — 설계 문서

- 날짜: 2026-06-23
- 상태: ⛔ **SUPERSEDED** (2026-06-24) → `2026-06-24-matching-verification-design.md`. 방향 전환: 사용자가 파일 리네임/표준화/업로드/자동기입에 거부감 → 워크플로우 불변, 매칭 정확도+검증만 개선. 이 문서의 vocabulary/필드 파싱 *개념*은 매칭 내부 정규화 보조로만 일부 계승됨.
- 범위: Battery Lab 실험기록 표준화의 토대 — 정규 스키마, 통제어휘, 표준 파일명 빌더, 연구주도자 확인 항목 데이터 모델
- 선행: Phase 1(렌더 캐시) 완료. 본 Phase는 Phase 2(업로드 UI)·Phase 3(일지 자동기입)·Phase 4(구조화 매칭 + 통합 확인 큐)의 공통 토대.

---

## 1. 배경

실험 데이터 파일명이 사람이 즉흥적으로 지은 형태라(EIS `1.5 act 4T_1_01.SEO`, capacity `419_pure GF_9532_7T_VC add_0.1C_…wrd`) 일지와의 매칭이 퍼지·위치 기반이라 자주 어긋난다. 메인 실험일지(`Project_Abstract/Cell condition Calculation.xlsx`의 **`JYJ` 시트**, 631행)는 이미 구조화된 컬럼을 갖지만, 범주형 값에 표기흔들림(`superP`/`super P`, binder 13종 중 다수가 같은 것의 다른 표기, foil `12 파이`/`12파이`)이 있어 자동 분류·매칭이 흔들린다.

본 Phase는 "새 스키마 발명"이 아니라 **기존 일지 컬럼을 정규 스키마 + 통제어휘(표준값+alias) + 표준 파일명 문법으로 정리**하고, 그 과정에서 사람(연구주도자)의 판단이 필요한 지점을 **확인 항목(confirmation item)** 으로 모델링한다.

### 확정된 결정 (브레인스토밍)
- 메인 일지 = `Cell condition Calculation.xlsx`의 **`JYJ` 시트** (Phase 3 자동 행추가 대상).
- 통제어휘는 **정규화**(표준값 + alias 매핑). 드롭다운엔 표준값, 과거표기는 alias로 흡수.
- messy `Sample` 필드는 **구조화 분해**(sample_base / composition_code / pressing_code / protocol_code / replicate).

---

## 2. 목표 / 비목표

### 목표
- JYJ 일지 컬럼을 **정규 스키마**로 정의하고 각 필드를 `categorical | continuous | calculated | identifier | free`로 태깅.
- 범주형 필드의 **통제어휘**(표준값 + alias + 파일명 code)를 JYJ에서 채굴·정규화해 `vocabulary.json`으로 산출.
- **표준 파일명 빌더/파서** 제공.
- 사람 판단이 필요한 지점을 **확인 항목 데이터 모델**로 정의하고, Phase 0가 소유하는 두 생성기(**미지값 감지**, **Sample 분해 제안**)를 제공.

### 비목표 (다음 Phase)
- 업로드 UI·입력폼 (Phase 2)
- 일지 엑셀 자동 행추가 (Phase 3)
- 구조화 매칭 업그레이드 + 통합 확인 큐 UI + standardize-on-confirm 리네임 (Phase 4)
- 본 Phase는 **데이터/로직 토대만**: `naming.py` + `vocabulary.json` + `confirmations.py`(모델 + Phase 0 소유 생성기) + 재생성 스크립트 + 테스트. **UI 없음.**

---

## 3. 정규 스키마

각 필드는 `FieldSpec`: `key, label_ko, type, journal_column(JYJ 헤더), filename(코드 역할/순서), vocab(범주형 어휘 키)`.
Phase 2 입력폼은 `type`으로 렌더: `categorical`→드롭다운(vocab), `continuous`→숫자입력, `calculated`→자동계산(기존 `metrics.py`/`conditions.py` 로직 재사용), `identifier`→생성/지정, `free`→텍스트.

| key | type | JYJ 컬럼 | 파일명 코드 | vocab |
|---|---|---|---|---|
| `cell_id` | identifier | (생성) | ✅ 1 (선두) | — |
| `system` | categorical | 종류 | ✅ 2 | system |
| `sample_base` | categorical(open) | Sample(분해) | ✅ 3 | sample_base |
| `composition_code` | categorical(open) | Sample(분해) | ✅ 4 | composition_code |
| `pressing_code` | categorical | Sample(분해) | ✅ 5 | pressing_code |
| `electrolyte` | categorical | 전해질 | ✅ 6 | electrolyte |
| `binder` | categorical | Binder | ✅ 7 | binder |
| `protocol_code` | categorical | Sample/C-Rate | ✅ 8 | protocol_code |
| `replicate` | identifier | Sample(분해)/Cell 자리 | ✅ 9 | — |
| `conductive` | categorical | Conductive agent | ✗ | conductive |
| `foil` | categorical | 참고 | ✗ | foil |
| `voltage_range` | categorical | Voltage range | ✗ | voltage_range |
| `ratio` | categorical | ratio | ✗ | ratio |
| `date` | identifier | Date | (cell_id에 포함) | — |
| `cell_slot` | identifier | Cell 자리 | ✗ | — |
| `active_material_g`·`current_A`·`cv_uA`·`cut_capacity_Ah`·`electrolyte_ul`·`drops`·`ocv`·두께·`volume` | continuous | 해당 컬럼 | ✗ | — |
| `theoretical_capacity`·`current_density`·`areal_mass_density`·`합제밀도` | calculated | 해당 컬럼 | ✗ | — |
| `drying_condition`·`memo`·`additional_memo` | free | 해당 컬럼 | ✗ | — |

`open` = 드롭다운이지만 "신규 추가" 허용(새 sample/조성이 계속 생김 → 확인 항목으로 승격).

---

## 4. 표준 파일명 문법

```
{cell_id}__{system}__{sample_base}__{composition}__{pressing}__{electrolyte}__{binder}__{protocol}__{replicate}{.원본확장자}
```
- 구분자 `__`. 각 칸 = 해당 필드의 **code**(공백·슬래시·콜론 제거, filesystem-safe). 빈 필드는 `NA`.
- `cell_id = {prefix}-{YYMMDD}-{seq}` (예 `JYJ-260422-419`). `prefix`는 일지 시트명(JYJ), `seq`는 capacity 파일명 선두번호 또는 일지 행번호를 seed로 사용.
- 원본 확장자 보존(.seo/.sde/.wrd/.csv/.xlsx).
- 예: capacity `419_pure GF_9532_7T_VC add_0.1C_…wrd` →
  `JYJ-260422-419__LIB__pureGF__9532__7T__LiPF6-ECDEC11__CMCSBR__0p1C-VCadd__R01.wrd`

---

## 5. `vocabulary.json`

JYJ 시트에서 채굴 후 정규화. 범주형 필드별로 `표준값 → {code, aliases[]}`:

```json
{
  "binder": {
    "2wt% CMC / 40wt% SBR": { "code": "CMCSBR", "aliases": ["2wt%cmc/40wt%SBR", "2wt%cmc, 40wt% SBR"] },
    "5wt% PVdF":            { "code": "PVdF",   "aliases": ["5wt% pvdf/nmp", "(새로 만든) 5wt% PVdF"] }
  },
  "system":      { "LIB": {"code":"LIB","aliases":["LiB","lib"]}, "AZIB": {"code":"AZIB","aliases":[]}, "ZIB": {"code":"ZIB","aliases":[]} },
  "electrolyte": { "1.0M LiPF6 EC/DEC 1:1": {"code":"LiPF6-ECDEC11","aliases":[]}, "2M ZnSO4 + 0.1M MnSO4": {"code":"ZnSO4-Mn0p1","aliases":["(new) 2M ZnSO4 + 0.1M MnSO4"]} }
}
```
- 드롭다운엔 표준값(키), 파일명엔 `code`, 입력/매칭 시 과거표기는 `aliases`로 흡수.
- 어휘는 **데이터 파일**이라 코드 수정 없이 갱신. (어휘가 매칭/렌더에 영향 주기 시작하면 Phase 4에서 `render_cache.context_hash`가 `vocabulary.json`도 추적하도록 추가 — Phase 4 spec에서 다룸.)

---

## 6. `battery_lab/naming.py` API

- `FIELDS: list[FieldSpec]` — §3 스키마(타입·컬럼·코드역할·vocab키).
- `FILENAME_FIELDS: list[str]` — §4 순서.
- `load_vocabulary() -> dict` / `save_vocabulary(vocab)`.
- `canonicalize(field: str, raw_value: str) -> str | None` — alias·경량정규화(소문자·공백·구두점 무시) → 표준값. 못 찾으면 `None`(= 미지값).
- `field_code(field: str, canonical_value: str) -> str` — 표준값 → 파일명 code.
- `build_standard_filename(fields: dict, *, ext: str) -> str` — §4 문법.
- `parse_standard_filename(name: str) -> dict | None` — **표준명** 역파싱(레거시 messy 이름 아님).

---

## 7. 확인 항목(confirmation item) 데이터 모델 + 미지값 감지

연구주도자(battery 연구 주도자)에게 받을 모든 수동 판단을 **하나의 모델**로 통일한다. Phase 4의 통합 확인 큐 UI가 이 모델을 렌더하고, Phase 2(업로드)·기존 매칭(matching_service)도 같은 모델로 항목을 흘려보낸다.

### 7.1 `ConfirmationItem` (in `battery_lab/confirmations.py`)
```python
@dataclass(frozen=True)
class ConfirmationItem:
    item_id: str        # sha1(type + ":" + subject + ":" + field)
    type: str           # "vocabulary" | "sample_decomposition" | "match" | "filename" | "journal_gap"
    kind: str           # "eis" | "capacity" | "global"
    priority: int       # 낮을수록 우선: vocabulary=0, sample_decomposition=1, match=2, filename=3, journal_gap=4
    subject: str        # 파일 relpath / 원시 범주값 / 일지 행 식별자
    field: str          # 관련 스키마 필드 키("binder"…) 또는 ""
    question: str       # 한국어 질문
    proposed: Any       # 미리채움(best guess)
    options: list[dict] # 택1 후보 [{value, label, hint}]
    reason: str         # 왜 확인 필요한지(설명·신뢰도 근거)
    confidence: float   # 0.0~1.0
    persist_to: str     # "vocabulary" | "overrides" | "decomposition" | "rename_log"
```

### 7.2 항목 타입 (생성 위치)
| type | 트리거 | 묻는 것 | persist_to | 생성 위치 |
|---|---|---|---|---|
| `vocabulary` | 범주값이 `canonicalize`에서 `None` | "신규 표준값인가, 기존값의 alias인가?" | vocabulary | **Phase 0** (`detect_unknown_values`) |
| `sample_decomposition` | Sample 분해 신뢰도 < 임계 | 제안 분해 확인·수정 | decomposition | **Phase 0** (`propose_sample_decomposition`) |
| `match` | risky 상태(unmatched/ambiguous/blocked/review) | 후보 택1 / 행번호 직접 / 삭제 | overrides | 기존 `matching_service` (재사용) |
| `filename` | 표준명 생성 후 | 제안 파일명 승인/수정 → 리네임 | rename_log | Phase 4 |
| `journal_gap` | 파일 cell# 일지에 없음 / 일지 행에 파일 없음 | 일지 행 생성? / 측정 누락? | overrides | Phase 4 (+ Phase 3 연계) |

본 Phase는 모델 + `vocabulary`·`sample_decomposition` **두 생성기**만 구현한다. 나머지 타입은 모델에 자리만 두고 후속 Phase가 채운다.

### 7.3 Phase 0 소유 생성기
- `detect_unknown_values(conditions, extra_values, vocab) -> list[ConfirmationItem]`
  - 각 범주형 필드에 대해 일지(conditions) + 추가 출처(파일명 파싱값)의 원시값을 모아, `canonicalize`가 `None`인 값을 미지값으로 본다.
  - 각 미지값에 **최근접 표준값**(경량 정규화 후 문자열 유사도 최댓값)을 `proposed`로, 상위 후보들을 `options`로 채워 `vocabulary` 항목 생성. 한 번 결정하면 같은 값을 쓰는 모든 파일이 일괄 해소(최고 레버리지 → priority 0).
- `propose_sample_decomposition(sample_raw, vocab) -> tuple[dict, float]`
  - messy Sample을 `sample_base/composition_code/pressing_code/protocol_code/replicate`로 휴리스틱 분해(압연 `\d+T`, 조성 `\d{3,5}`, protocol `0\.?\d+C|rate per|\d+mA`, replicate `_\d+`/`R\d+`, 나머지 base).
  - `(fields, confidence)` 반환. `confidence < 0.6`이면 호출측이 `sample_decomposition` 항목 생성.

### 7.4 통합 큐 정렬
`sorted(items, key=lambda i: (i.priority, -i.confidence, i.subject))` — 어휘 먼저, 그다음 분해, 그다음 파일별. pre-fill이 강해서 대부분 "그대로 승인" 한 클릭이 되도록 한다.

---

## 8. 부속 / 매칭 연계

- `scripts/mine_vocabulary.py` — JYJ 시트에서 범주형 distinct 값(+빈도) 추출 → `vocabulary.json` 초안 갱신(표준값/code/alias는 사람이 확정).
- **매칭 개선(Phase 4 미리보기, 본 Phase는 토대만):** 표준 파일명의 `cell_id`가 일지 링크를 명시하므로 매칭이 퍼지 점수 → 결정적 lookup으로 단순화되고, 어휘 정규화가 `material_conflicts` 오발동(blocked)·부분겹침(review)을 제거한다. 레거시 파일은 `parse`(messy)+canonical 필드 비교로 정밀도를 올린다. 상세 구현은 Phase 4 spec.

---

## 9. 테스트

- `build_standard_filename`이 알려진 필드셋에 대해 기대 `__`-코드열 생성; `parse_standard_filename` 라운드트립.
- `canonicalize`가 alias·표기변형(`super P`/`superP`)을 표준값으로, 미지값은 `None`.
- `vocabulary.json` 로드 시 모든 엔트리에 `code` 존재.
- `detect_unknown_values`: vocab에 없는 binder 값 1개 투입 → `vocabulary` 항목 1개, `proposed`가 최근접 표준값.
- `propose_sample_decomposition("pure SDI 9532_6T_2_0.1C")` → base/composition(9532)/pressing(6T)/protocol(0p1C)/replicate(2) 추출, confidence 반환; 모호 입력은 저신뢰.
- `ConfirmationItem` 정렬: vocabulary가 match보다 앞.

---

## 10. 로드맵 갱신

- **Phase 4 재정의:** 단순 클러스터링 → **"구조화 매칭 업그레이드 + 통합 확인 큐(review_EIS_capacity 탭 진화) + standardize-on-confirm 리네임"**. 캐파시티 행번호 링크 → `cell_id` 링크 교체 포함.
- review_EIS_capacity 탭은 폐기 아님 — 기존 staged 워크플로우·overrides.json 재사용하며 통합 큐로 확장.

---

## 11. 열린 질문 / 후속

- `cell_id`의 `seq` 출처 확정(파일명 선두번호 vs 일지 행번호)은 Phase 2/4에서 데이터 정합성 보고 확정.
- 문자열 유사도 함수(최근접 표준값)는 stdlib `difflib.SequenceMatcher`로 시작, 필요시 토큰 기반으로 교체.
- `vocabulary.json` 위치: `battery_lab/` 패키지 동봉(버전관리) vs `BATTERY_OUTPUT_ROOT`(영구 디스크, 런타임 갱신). 기본 패키지 동봉 + 런타임 override 병합은 Phase 2에서 확정.
