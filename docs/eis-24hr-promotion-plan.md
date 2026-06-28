# EIS 비교 클러스터 — 시계열 24hr 엔드포인트 정식 편입 계획

## 1. 목표

시계열(time-series) 클러스터의 **24hr 끝점(안정화 후 데이터)**을 비교 클러스터의
**정식 비교 멤버**로 승격한다. 현재는 `_attach_time_series`가 optional 오버레이로만
붙이는데, 24hr 데이터는 안정화 상태라 비-시계열 셀과 동등하게 비교 가능하므로
클러스터를 *정의*하는 풀에 합류시킨다.

## 2. 근거 / 구현 결과 (실데이터, override 191 적용)

| 항목 | 현재 | 승격 후 |
|---|---|---|
| 비교 조건 수 | 20 (전부 비-시계열) | **41** (비-시계열 20 + 24hr 21) |
| 백본 버킷 | 1 | **2** |
| 클러스터 크기 | `[20]` | **`[32, 9]`** |
| 새로 등장하는 백본 | — | `1.0M LiPF6 EC/DEC 1:1 / 2wt%CMC / 0.01~2V / 0.96` |

- 담당자 답변 반영 후 TS 클러스터 **29개**(manual 18, verified 11)
- TS 클러스터 중 **22개가 `has_24` + verified/manual** → 승격 후보
- 그중 **21개가 신규 조건**(기존 비-시계열 20개와 중복 없음)으로 정식 승격
- `2wt%CMC/0.96` 백본은 비-시계열 비교 파일이 **0개** → 24hr 승격으로만 비교군이 생김

## 3. 설계 결정

| # | 결정 | 내용 |
|---|---|---|
| D1 | **승격 게이트** | TS 클러스터가 `has_24=True` **AND** `match_status ∈ {verified, manual}` **AND** 유효 `condition_key` 일 때만 승격. `ambiguous`/`conflict`(35개 중 13개)는 제외 — 클러스터 오염 방지 |
| D2 | **끝점 파일 선택** | 기존 `_pick_endpoint_member(member_paths, match_by_path, 24)` 재사용 (24hr에 가장 가까운 멤버) |
| D3 | **중복 우선순위** | 같은 `condition_key`에 비-시계열 비교 셀이 이미 있으면 그것을 우선, 24hr은 **비-시계열 셀이 없는 조건만** 채움 (한 조건 = 한 셀 유지) |
| D4 | **출처 태깅** | `_ComparisonCell`에 `origin` 필드 추가 (`"steady"` / `"ts_24hr"`). 클러스터 CSV·뷰어 범례에서 24hr 멤버를 시각적으로 구분 |
| D5 | **optional 재정의** | 24hr이 정식 멤버가 되므로, optional 오버레이는 **승격 못 한 TS(ambiguous/conflict)** 또는 sub-24hr 시점만 담도록 축소 (토글 유지) |
| D6 | **백본/로딩** | 백본 버킷팅·`backbone_components`는 그대로. 로딩 임계값은 이미 제거됨([이전 작업](../battery_lab/eis_matching.py)) — 24hr 셀도 같은 백본이면 한 그룹 |

## 4. 구현 단계 (계획표)

| Phase | 작업 | 파일 / 함수 | 산출물 |
|---|---|---|---|
| **P0** | 담당자 답변 반영: 같은 수동확정 행의 disjoint 시간 파편을 병합. 겹치는 시간값은 중복/빈 파일 가능성이 있어 병합하지 않음 | `eis_timeseries.py` · `_merge_manual_same_row()` | 473/474/475/476 병합, 506 미병합 |
| **P1** | `_ComparisonCell`에 `origin` 필드 추가, 기존 비-시계열 경로는 `origin="steady"`로 세팅 | `eis_matching.py` · `_ComparisonCell`, `_primary_cells` | 데이터 모델 |
| **P2** | `_time_series_cells`를 D1 게이트(`has_24`+상태)로 필터하고 `origin="ts_24hr"` 부여 | `eis_matching.py` · `_time_series_cells` | 승격 후보 풀 |
| **P3** | 승격 병합 함수 신설: 비-시계열 셀 우선, 24hr은 미커버 조건만 추가(D3) | `eis_matching.py` · `_merge_primary_and_ts()` | 통합 primary 풀 |
| **P4** | `build_comparison_clusters`가 통합 풀을 `usable`로 사용하도록 교체 | `eis_matching.py` · `build_comparison_clusters` | 클러스터 = 41조건 / 2버킷 |
| **P5** | `_attach_time_series`는 승격분을 component 멤버로 보유하므로 중복 optional에서 자연 제외 | `eis_matching.py` · `_attach_time_series` | optional 중복 방지 |
| **P6** | `EISComparisonCluster`에 멤버 출처 노출(`member_origins`, `ts24_source_paths`) | `eis_matching.py` · `EISComparisonCluster` dataclass | CSV/JSON 스키마 |
| **P7** | 뷰어/API가 출처 필드를 받을 수 있게 스키마 노출. 색상/범례 직접 변경은 후속 UI 표시 작업으로 분리 | `viewer_service.py`/`ui.py` 소비 데이터 | 출처 추적 |
| **P8** | 테스트: 수동 파편 병합, 24hr 승격, sub-24hr 제외 케이스 추가 | `tests/test_eis_timeseries.py`, `tests/test_eis_matching.py` | 회귀 방지 |
| **P9** | 진단 스크립트로 검증: `[32, 9]` / 41조건 / 21개 24hr 승격 재현 확인 | `scripts/diagnose_cluster_threshold.py` | 정량 검증 |

## 5. 영향받는 파일

- `battery_lab/eis_matching.py` — 핵심 로직 (P1~P6)
- `battery_lab/viewer_service.py`, `battery_lab/ui.py` — 라벨/색 (P7)
- `tests/test_eis_matching.py` — 회귀 (P8)
- (참고) `scripts/diagnose_cluster_threshold.py` — 검증용

## 6. 검증 기준 (완료 조건)

1. 진단 스크립트 출력이 **2 클러스터 `[32, 9]` / 41 조건 / TS 24hr 승격 21개**
2. 승격된 멤버는 전부 `origin="ts_24hr"`이고 24hr 시점 파일
3. 같은 조건에 비-시계열 셀이 있으면 24hr이 중복 추가되지 않음 (D3)
4. `ambiguous`/`conflict` TS는 정식 멤버에서 제외, optional로만 노출 가능 (D1/D5)
5. 기존 EIS 테스트 + 신규 테스트 전부 통과
6. 뷰어에서 24hr 멤버가 라벨/색으로 구분됨

## 7. 담당자 답변 반영

회신 파일: `/Users/haesungjun/Downloads/eis_24hr_confirmation_answer.json`

| 항목 | 답변 | 반영 |
|---|---|---|
| 행 473 | 같은 셀의 연속 측정, 24hr 끝점 사용 가능. 474와는 서로 다른 셀 | 0/3/4/7hr + 5/6hr + 9/13/15/24hr 병합 |
| 행 474 | 같은 셀의 연속 측정, 24hr 끝점 사용 가능. 473과는 서로 다른 셀 | 0/3/4/7hr + 5/6hr + 9/13/15/24hr 병합 |
| 행 475 | 6hr 파일 포함 병합, 24hr 끝점 사용 가능 | 단독 6hr + 0→24hr 묶음 병합 |
| 행 476 | 6hr 파일 포함 병합, 24hr 끝점 사용 가능 | 단독 6hr + 0→24hr 묶음 병합 |
| 행 506 | 6hr 종료 실험, 24hr 없음 | 24hr 승격 제외. 6hr 중복은 겹치는 시간값이 있어 자동 병합하지 않음 |
| 24hr 없는 9hr 종료 파일 | 24hr 안정화 비교군에서 제외 | `has_24` 게이트로 제외 |
| 0.5C / rate per | 일반 EIS와 구별하지 않고, 24hr이 있으면 비교 가능 | 별도 제외 규칙 추가하지 않음 |
| 0hr 없는 24hr 묶음 | 24hr 끝점 비교는 가능, 변화량 분석은 병합 필요 | 24hr 비교 승격은 허용. 변화량은 별도 병합/연결 필요 |
| `pure GF 964_4T_1` | `pure GF 964_4T` 조건으로 만든 두 셀 중 첫 번째 셀 | 네이밍 해석 확정 |
| 중복/빈 파일 가능성 | 중복이면 하나만 살리고 빈 파일은 삭제 가능 | 시간값이 겹치는 수동 파편은 자동 병합하지 않고 검토 대상으로 유지 |

## 8. 담당자 확인 원문 (기록)

담당자 회신용 단일 HTML은 [`eis-24hr-confirmation-form.html`](eis-24hr-confirmation-form.html)에 작성했다.
그 문서는 내부 시계열 가명을 쓰지 않고, **JYJ 행번호 / 일지 날짜 / 일지 샘플명 / 실제 EIS 상대경로**만으로 확인할 수 있게 구성했다.

### Q1. (최우선) 한 일지 행에 여러 실제 파일 묶음이 잡힌 경우

담당자에게 다음 지시를 보낸다: `Project_Abstract/Cell condition Calculation.xlsx`의 `JYJ` 시트에서
아래 행을 열고, 날짜와 샘플명을 먼저 확인한 뒤, 표시된 실제 EIS 파일들이 같은 셀의 연속 측정인지 판단한다.
같은 셀이면 병합하고 24hr 파일을 안정화 끝점으로 비교군에 넣는다. 다른 셀이면 분리 또는 제외한다.

| JYJ 행 | 일지 날짜 / 샘플명 | 확인할 실제 파일 묶음 | 담당자 판단 |
|---|---|---|---|
| 473 | 260527 / `pure 900 no SBR_4T` | `pure 900 no SBR 4T_0hr_02.SEO`, `pure 900 no SBR 4T_3hr_02.SEO`, `pure 900 no SBR 4T_4hr_02.SEO`, `pure 900 no SBR 4T_7hr_01.SEO` + `pure GF 900 no SBR_5hr_02.SEO`, `pure GF 900 no SBR_6hr_02.SEO` + `pure GF 900 no SBR_4T 9hr_01.SEO`, `pure GF 900 no SBR 4T_13hr_01.SEO`, `pure GF 900 no SBR 4T_15hr_01.SEO`, `pure GF 900 no SBR 4T_24hr_01.SDE` | 세 묶음이 같은 셀의 0→24hr 연속 측정인지 |
| 474 | 260527 / `pure 900 no SBR_4T` | `pure 900 no SBR 4T_2_0hr_03.SEO`, `pure 900 no SBR 4T_2_3hr_03.SEO`, `pure 900 no SBR 4T_2_4hr_03.SEO`, `pure 900 no SBR 4T_2_7hr_02.SEO` + `pure GF 900 no SBR_2_5hr_03.SEO`, `pure GF 900 no SBR_2_6hr_03.SEO` + `pure GF 900 no SBR 4T_2_9hr_02.SEO`, `pure GF 900 no SBR 4T_2_13hr_02.SEO`, `pure GF 900 no SBR 4T_2_15hr_02.SEO`, `pure GF 900 no SBR 4T_2_24hr_02.SDE` | 세 묶음이 같은 셀의 0→24hr 연속 측정인지 |
| 475 | 260527 / `1.5act no SBR_4T` | 단독 `1.5act no SBR_6hr_02.SEO` + `1.5act NO SBR 4T 0hr_04.SEO`, `1.5act no SBR 4T 0hr again_02.SEO`, `1.5act no SBR 4T_3hr_02.SEO`, `1.5act no SBR 4T_4hr_02.SEO`, `1.5act no SBR_4T_7hr_03.SEO`, `1.5act no SBR 4T_9hr_03.SEO`, `1.5act no SBR 4T 13hr_03.SEO`, `1.5act no SBR 4T_15hr_03.SEO`, `1.5act no SBR 4T_24hr_03.SDE` | 단독 6hr 파일을 병합할지, 24hr 묶음만 쓸지 |
| 476 | 260527 / `1.5act no SBR_4T` | 단독 `1.5act no SBR_2_6hr_03.SEO` + `1.5 act no SBR 4T_2 0hr_03.SEO`, `1.5act no SBR 4T_2_3hr_03.SEO`, `1.5 act no SBR 4T_2_4hr_03.SEO`, `1.5act no SBR_4T_2_7hr_04.SEO`, `1.5act no SBR 4T_2_9hr_04.SEO`, `1.5act no SBR 4T_2_13hr_04.SEO`, `1.5act no SBR 4T_2_15hr_04.SEO`, `1.5act no SBR 4T_2_24hr_04.SDE` | 단독 6hr 파일을 병합할지, 24hr 묶음만 쓸지 |
| 506 | 260610 / `pure GF 964_4T_1` | 단독 `pure 4T_1 6hr again_01.SDE` + `pure 4T_1 0hr_01.SDE`, `pure 4T_1 3hr_01.SDE`, `pure 4T_1_6hr_01.SDE`; 현재 24hr 파일 없음 | 6hr 종료 실험인지, 24hr 파일이 다른 폴더/이름에 있는지 |

→ 영향: Q1이 “같은 실험”이면 24hr 승격 전에 3개 이상 파편을 한 셀로 잇는 병합 로직을 보강한다.

### Q2. 24hr 없는 0.5C/rate 계열 파일

담당자에게 다음 네 행은 24hr 안정화 비교군에서 제외할지 확인한다.

| JYJ 행 | 일지 날짜 / 샘플명 | 현재 끝점 | 실제 파일명 예시 |
|---|---|---|---|
| 482 | 260602 / `pure 900 3T_1_0.5C` | 9hr | `pure 3T_1_0hr_01.SDE` ... `pure 3T_1_9hr_01.SDE` |
| 484 | 260602 / `pure 900 5T_1_0.5C` | 9hr | `pure 5T_1_0hr_03.SDE` ... `pure 5T_1_9hr_03.SDE` |
| 487 | 260602 / `1.5act 2T_1_0.5C` | 9hr | `1.5act 2T_1_0hr_01.SDE` ... `1.5act 2T_1_9hr_01.SDE` |
| 490 | 260602 / `1.5act 3T_2_rate per` | 9hr | `1.5act 3T_2_0hr_04.SDE` ... `1.5act 3T_2_9hr_04.SDE` |

→ **확인**: 9hr를 안정화 끝점으로 인정할지, 아니면 24hr 비교군에서는 제외할지.
→ **추가 확인**: `rate per` / `0.5C` 계열 전체를 일반 안정화 EIS 비교와 분리해야 하는지.

### Q3. 0hr 없이 24hr만 있는 묶음

행 471(`DL pc 2T2T_2`), 473, 474는 24hr 파일이 있지만 같은 묶음 안에 0hr이 없다.
끝점 비교만 할 때는 24hr 파일 사용이 가능할 수 있으나, 0→24 변화량 분석에는 0hr 파일 연결 여부가 필요하다.
담당자에게 HTML의 Q3 표를 보고 24hr만 비교군에 넣어도 되는지 답변받는다.

### Q4. 수동 확정된 중복 행

행 473, 474, 475, 476, 506은 여러 실제 파일 묶음이 같은 JYJ 행으로 수동 확정되어 있다.
수동 확정 행은 자동 충돌 감지가 우회되므로, 의도된 연속 측정/재측정인지 또는 매칭 오류인지 담당자 답변이 필요하다.

### Q5. 네이밍/소재 해석

`no SBR_4T`, `pure GF 964_4T_1`, `2T2T`, `0.5C`, `rate per` 표기가 일지 의도와 맞는지 확인한다.

---

## 9. 리스크 / 주의

- **R1 잘못된 매칭 전파**: 24hr→일지행 매칭이 틀리면 비교군이 오염됨. → D1 게이트로 verified/manual만 허용해 완화. ambiguous는 의도적으로 제외.
- **R2 중복 일지행**: TS의 `conflict`(같은 행을 여러 클러스터가 차지)는 D1에서 제외되어 안전.
- **R3 스키마 변경 파급**: P6에서 dataclass 필드 추가 시 CSV 헤더·뷰어 파서가 따라가야 함(P7과 함께 처리).
- **R4 pairs 등급**: `comparison_pair`는 여전히 로딩차 ≤1.0로 A/B 등급만 매김(정보용, 비교를 막지 않음). 범위가 넓어지면 등급 미부여 쌍이 늘 수 있음 — 필요 시 별도 정리.
