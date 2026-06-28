# Import Wizard 3-Step Redesign

Date: 2026-06-28
Status: Approved (brainstorming) → implementing autonomously per user request.

## Goal

Rebuild the "새 실험 등록" popup into a guided 3-step flow that replaces the current
drag-and-drop zone board + stacked forms. The popup is already a centered modal
(done in a prior change). This redesign restructures the **inside** of the modal.

User decisions (confirmed):
- **1 등록 = 1 실험일지 행** (current backend model kept; commit appends exactly one row).
- **"xlsx에 저장" = full pipeline** (journal row + file save to EIS/capacity folders +
  match overrides + graph/match refresh). No change to commit semantics.
- **Step 2 = preview 박스 가로 나열 + 실험정보 폼 1개 통합** (no per-box forms;
  one metadata set, since it is one row).

## Step flow

### Step 1 — 업로드 & 분류
- Big horizontal `+ 파일 업로드` dropzone (click → multi-file picker; drag-drop supported).
- Uploaded files list as rows. Each row: filename + auto-detected reason (small grey),
  a **5-type `<select>` toggle** on the right (EIS 비교클러스터 / EIS 시계열 /
  Capacity 1 / Capacity 2 / Capacity 3), and a trailing **× delete** (no confirm).
- Toggle default = backend auto-classification (already implemented in `infer_assignment`:
  filename `hr` → time-series, capacity protocol → 1/2/3, else EIS → comparison).
- Small text button `+ 파일 추가` below the list (appends to the same draft).
- `실험정보 입력하기 →` advances to Step 2 (disabled when 0 files).
- The old `exclude` option is replaced by the × delete button.

### Step 2 — 미리보기 & 실험정보
- Preview boxes laid out **horizontally** (grid). Box = preview plot + key metrics + type label.
- **EIS 시계열 files collapse into a single box** (multiple timepoints, one entry).
- A single consolidated **실험정보** form below (existing required fields +
  past-value autocomplete via the metadata-options endpoint).
- `최종 확인 →` saves metadata (PATCH) and, if valid, advances to Step 3.

### Step 3 — 확인 & 저장
- Shows the **single journal row preview** (metadata field values), the file → assignment
  list, and existing-cluster match status (reuse `cluster-preview` endpoint).
- `실험일지 xlsx에 저장하기` → existing `commit` pipeline. On success, reload the page so
  the journal iframe + counts refresh.

## Backend changes (small, additive)

1. `experiment_import.infer_assignment`: `assignment_options` becomes the **5 types** for every
   file (drop `exclude` from the picker); unknown analysis_type defaults `suggested` to
   `eis_comparison` (per "어디에도 해당 안 하면 EIS 비교클러스터"). `exclude` stays a valid
   internal value (commit still skips it) but is no longer offered in the UI.
2. `experiment_import.append_import_draft_files(output_root, draft_id, uploads, write_raw_wrd)`:
   parse + append new files into an existing draft (reuses `build_draft_file`).
3. `experiment_import.remove_import_draft_file(output_root, draft_id, file_id)`: remove one file
   from the manifest and best-effort unlink its raw/processed/preview/plot artifacts.
4. Routes: `POST /api/import/drafts/<draft_id>/files` (append) and
   `DELETE /api/import/drafts/<draft_id>/files/<file_id>` (delete).

No change to `commit_import_draft`, cluster preview, metadata validation, or file routing.

## Cross-type toggle (decision)

The 5-type toggle is shown on every row ("편견없이"). Analysis group (EIS vs Capacity) is
detected from file **content** (`parse_file`), so cross-group mis-detection is rare and only
realistic for ambiguous `.csv/.xlsx`. Same-group switches (Cap 1↔2↔3, EIS 비교↔시계열) are
pure relabels and safe. A cross-group choice is accepted (options widened) and routed by the
chosen assignment at commit; the backend does **not** re-parse into the other group in this pass.
This is a documented limitation/risk, not a guaranteed-correct path.

## Frontend changes (battery_lab/templates/battery_lab/app.html)

- CSS: replace wizard-inner rules (`.battery-import-form` … `.battery-cluster-table`) with new
  `biw-*` step styles. Keep modal styles (`.battery-import-wizard.open`, `.battery-wizard-close`)
  and shared `.battery-match-*` / `.battery-pill` (used by other panels).
- HTML: replace the wizard inner block with the 3 `data-step` panels.
- JS: replace the wizard IIFE with a stepper controller (upload/append/delete, assignments,
  preview grouping, metadata, confirm, commit). Other IIFEs (viewers, match review) untouched.

## Verification

- `python3 -m pytest tests/test_experiment_import.py` (upload/assignments/metadata/commit +
  new append/delete tests) green.
- `python3 -m py_compile` / AST for `experiment_import.py`, `routes.py`.
- Jinja template renders via existing Flask test client path (test_import_draft_api…), plus a
  manual render-smoke if feasible.
- Manual: open wizard, upload, retype, delete, add more, fill info, confirm, save.

## Risks

- Large `app.html` rewrite (CSS+HTML+JS in one file) — risk of breaking sibling JS/markup.
  Mitigation: replace only the bounded wizard regions; keep shared classes.
- Cross-group toggle inconsistency (above) — documented; researcher mostly uses same-group.
- Append/delete endpoints add backend surface — covered by new tests.
- Page reload after commit assumes single-row commit success; partial-failure messaging kept.
