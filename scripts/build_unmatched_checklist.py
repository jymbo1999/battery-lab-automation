"""Generate an operator HTML checklist for the EIS non-time-series files that
stayed `unmatched` (no auto-confirmed journal row). The operator picks the right
journal row, types a row number directly, or marks "no such row exists / delete".

Read-only against the data; only writes the HTML file.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from battery_lab.config import BATTERY_EIS_ROOT, BATTERY_CONDITION_WORKBOOK, BATTERY_MATCH_EIS_JSON
from battery_lab.conditions import read_conditions
from battery_lab.eis_matching import EIS_SUFFIXES, match_eis_files_to_conditions, folder_date
from battery_lab.matching_service import collect_source_files, load_match_overrides

OUT = Path("/Users/haesungjun/VSCODE Library/BBATTAERRI/battery_visual_outputs/matching_checklist.html")


def journal_hint(rel: str, conds: dict) -> str:
    """A short, human note about what the journal does/doesn't contain for this file."""
    low = rel.lower()
    if "pc91" in low.replace(" ", ""):
        return ("실험일지(JYJ)에 <b>pc91은 6T(432·433행)만</b> 있고 "
                "<b>4T·5T 행이 없습니다.</b> 4T·5T 셀을 실제로 만들었는지, "
                "만들었다면 일지 어느 행인지 알려주세요. 없으면 '해당 행 없음'.")
    if "no3" in low.replace(" ", "") or "no 3" in low:
        return ("일지의 no3 GF C 행은 <b>342~353(날짜 260209·260225)</b>뿐이고 "
                "이 파일은 <b>260424 'after 1 cycle'</b>입니다. 같은 셀의 사이클 후 측정인지, "
                "어느 행 셀인지 확인해 주세요.")
    if "pure gf" in low:
        return "pure GF 계열 — 코드가 <b>pure GF_9532_7T_no add</b> 행을 후보로 제시했습니다. 맞는지 확인해 주세요."
    return "자동 매칭이 확정되지 못했습니다. 어느 일지 행의 셀인지 확인해 주세요."


def section_for(rel: str) -> str:
    low = rel.lower().replace(" ", "")
    if "pc91" in low:
        return "A. pc91 4T·5T — 일지 행 존재 여부 확인 필요"
    if "no3" in low or "no 3" in rel.lower():
        return "B. no3 GF C (사이클 후) — 셀/행 확인 필요"
    return "C. pure GF 계열 — 후보 확인만 하면 됨"


def main() -> None:
    root = BATTERY_EIS_ROOT.resolve()
    paths = collect_source_files(root, EIS_SUFFIXES)
    conds = read_conditions(BATTERY_CONDITION_WORKBOOK, sheet_name="JYJ")
    ovr = load_match_overrides(BATTERY_MATCH_EIS_JSON)
    _, matches = match_eis_files_to_conditions(paths, conds, root, ovr)
    unm = [m for m in matches if m.status == "unmatched" and not m.is_time_series]

    items = []
    for m in sorted(unm, key=lambda x: x.relative_path):
        opts = json.loads(m.candidate_options or "[]")
        items.append({
            "file": m.relative_path,
            "date": folder_date(m.relative_path),
            "section": section_for(m.relative_path),
            "hint": journal_hint(m.relative_path, conds),
            "candidates": [
                {"row": o.get("journal_row"), "sample": o.get("sample"), "score": o.get("score")}
                for o in opts
            ],
        })

    # group by section
    sections: dict[str, list] = {}
    for it in items:
        sections.setdefault(it["section"], []).append(it)

    cards_html = []
    idx = 0
    for sec_name in sorted(sections):
        group = sections[sec_name]
        cards_html.append(f'<section class="block"><h2>{html.escape(sec_name)}</h2>')
        for it in group:
            idx += 1
            cand_opts = "".join(
                f'<option value="{c["row"]}">{c["row"]}행 · {html.escape(str(c["sample"] or ""))} (점수 {c["score"]})</option>'
                for c in it["candidates"] if c["row"]
            )
            cand_block = ""
            if it["candidates"]:
                lis = "".join(
                    f'<li>{c["row"]}행 · {html.escape(str(c["sample"] or ""))} · 점수 {c["score"]}</li>'
                    for c in it["candidates"] if c["row"]
                )
                cand_block = f'<div class="candidate-note">코드 추천 후보:<ul class="candidate-list">{lis}</ul></div>'
            cards_html.append(f'''
<div class="card" data-file="{html.escape(it["file"])}">
  <div class="chead">
    <span class="fname">{html.escape(it["file"])}</span>
    <span class="fdate">폴더날짜 {html.escape(it["date"] or "-")}</span>
  </div>
  <div class="why">{it["hint"]}</div>
  {cand_block}
  <div class="pick">
    <label>결정:</label>
    <select class="ans">
      <option value="">— 선택 —</option>
      {cand_opts}
      <option value="__direct__">다른 일지 행번호 직접 입력 ▼</option>
      <option value="__none__">해당 행 없음 (일지에 미입력)</option>
      <option value="__delete__">이 파일 삭제/제외</option>
      <option value="__hold__">보류 (나중에)</option>
    </select>
    <input class="memo" placeholder="행번호 또는 메모" />
  </div>
</div>''')
        cards_html.append("</section>")

    body = "\n".join(cards_html) if items else '<div class="empty">확인이 필요한 unmatched 항목이 없습니다 🎉</div>'

    page = TEMPLATE.replace("{{TOTAL}}", str(len(items))).replace("{{CARDS}}", body)
    OUT.write_text(page, encoding="utf-8")
    print(f"wrote {OUT}  ({len(items)} items, {len(sections)} sections)")


TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EIS 미매칭 파일 확인 체크리스트</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; background: #f5f6f8; color: #1f2733; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 14px; padding-bottom: 60px; }
  .bar { position: sticky; top: 0; z-index: 10; background: #fff; border-bottom: 1px solid #e2e6ec; padding: 10px 16px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .bar h1 { margin: 0; font-size: 16px; }
  .bar .prog { font-weight: 700; color: #2566c4; }
  .bar button, .bar label.file { border: 1px solid #c3ccd8; background: #fff; border-radius: 7px; padding: 6px 12px; font-size: 13px; cursor: pointer; }
  .bar button.primary { background: #2566c4; color: #fff; border-color: #2566c4; }
  .spacer { flex: 1; }
  .intro { margin: 14px 16px; color: #56607a; font-size: 13px; line-height: 1.5; }
  section.block { margin: 14px 16px; }
  section.block h2 { font-size: 15px; margin: 6px 0; }
  .card { background: #fff; border: 1px solid #e2e6ec; border-left: 4px solid #f0a92b; border-radius: 9px; padding: 11px 13px; margin-bottom: 10px; }
  .card.done { border-left-color: #1f8a4c; }
  .chead { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .fname { font-family: ui-monospace, Menlo, monospace; font-weight: 600; }
  .fdate { color: #8b95a4; font-size: 12px; }
  .why { color: #56607a; font-size: 12.5px; margin: 6px 0 9px; line-height: 1.45; }
  .pick { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .pick label { color: #44506a; font-size: 12.5px; }
  select.ans { padding: 6px 8px; border: 1px solid #c3ccd8; border-radius: 7px; min-width: 320px; max-width: 100%; font-size: 13px; }
  input.memo { padding: 6px 8px; border: 1px solid #d7dde6; border-radius: 7px; min-width: 180px; }
  .candidate-note { margin: 4px 0 9px; color: #36465f; font-size: 12.5px; }
  .candidate-list { margin: 5px 0 0; padding-left: 20px; }
  .candidate-list li { margin: 2px 0; font-size: 12.5px; }
  .empty { color: #1f8a4c; padding: 16px; font-weight: 600; }
  #blob { display: none; width: calc(100% - 32px); margin: 0 16px; height: 140px; font-family: ui-monospace, monospace; font-size: 11px; }
</style>
</head>
<body>
<div class="bar">
  <h1>EIS 미매칭 파일 확인</h1>
  <span class="prog" id="prog">0 / {{TOTAL}} 결정됨</span>
  <span class="spacer"></span>
  <button class="primary" id="copyBtn">결과 복사 (카톡 회신용)</button>
  <button id="dlBtn">JSON 내려받기</button>
</div>
<textarea id="blob" readonly></textarea>
<p class="intro">자동 매칭이 안 된 EIS 파일 {{TOTAL}}개입니다. 각 파일이 <b>실험일지(JYJ 시트)의 어느 행</b> 셀인지 골라주세요.
일지에 해당 셀이 <b>아예 없으면 "해당 행 없음"</b>을 선택하면 됩니다 — 억지로 맞출 필요 없습니다.
다 고른 뒤 <b>"결과 복사"</b>를 눌러 카톡으로 회신해 주세요.</p>
{{CARDS}}
<script>
function collect() {
  const out = [];
  document.querySelectorAll('.card').forEach(card => {
    const sel = card.querySelector('.ans');
    const memo = card.querySelector('.memo').value.trim();
    if (!sel.value && !memo) return;
    out.push({ file: card.dataset.file, decision: sel.value, memo: memo });
    card.classList.toggle('done', !!sel.value);
  });
  return out;
}
function refresh() {
  const done = collect().length;
  document.getElementById('prog').textContent = done + ' / {{TOTAL}} 결정됨';
}
document.addEventListener('input', refresh);
document.getElementById('copyBtn').addEventListener('click', () => {
  const data = collect();
  const text = '[EIS 미매칭 확인 회신]\\n' + data.map(d =>
    `- ${d.file} => ${d.decision || ''}${d.memo ? ' ('+d.memo+')' : ''}`).join('\\n');
  const ta = document.getElementById('blob');
  ta.style.display = 'block'; ta.value = text; ta.select();
  navigator.clipboard && navigator.clipboard.writeText(text);
  refresh();
});
document.getElementById('dlBtn').addEventListener('click', () => {
  const blob = new Blob([JSON.stringify(collect(), null, 2)], {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'unmatched_decisions.json'; a.click();
});
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
