"""Self-contained, fillable matching-checklist HTML for offline review.

The research lead opens this single file in a browser (no server), picks the correct
journal row for each ambiguous file, and exports the answers as JSON (copy to clipboard
or download). The answers round-trip back via `matching_service.apply_checklist_answers`
into `overrides.json`. Answers auto-save to localStorage so progress is never lost.
"""
from __future__ import annotations

from html import escape
from typing import Any

_DECISION_STATUSES = {"ambiguous", "review"}
# Clusters add a `conflict` status (two clusters claiming one journal row) that
# per-file rows never have; it always needs the research lead to resolve it.
_CLUSTER_DECISION_STATUSES = {"ambiguous", "review", "conflict"}

import json as _json


def _candidate_options(row: dict[str, Any]) -> str:
    opts = ['<option value="">— 선택 —</option>']
    for idx, c in enumerate(row.get("candidate_options") or []):
        ck = escape(str(c.get("condition_key", "")))
        delta = c.get("date_delta_days")
        delta_txt = f"±{delta}일" if delta is not None else "?"
        label = (
            f'행 {escape(str(c.get("journal_row", "")))} · {escape(str(c.get("sample", "")))} · '
            f'{escape(str(c.get("date", "")))} ({delta_txt}, {escape(str(c.get("score", "")))}점)'
        )
        rec = " ⟵ 코드 추천" if idx == 0 else ""
        opts.append(f'<option value="{ck}">{label}{rec}</option>')
    opts.append('<option value="__delete__">❌ 삭제 대상</option>')
    opts.append('<option value="__skip__">⏭ 모르겠음 / 보류</option>')
    return "".join(opts)


def _decision_card(row: dict[str, Any]) -> str:
    rel = escape(str(row.get("relative_path", "")))
    return (
        f'<div class="card">'
        f'<div class="chead"><span class="fname">{escape(str(row.get("source_name", "")))}</span>'
        f'<span class="fdate">파일날짜 {escape(str(row.get("file_date", "")))}</span>'
        f'<span class="badge warn">{escape(str(row.get("status", "")))}</span></div>'
        f'<div class="why">{escape(str(row.get("reason", "")))}</div>'
        f'<div class="pick"><label>올바른 실험일지 행:</label>'
        f'<select class="ans" data-file="{rel}">{_candidate_options(row)}</select>'
        f'<input class="memo" data-file="{rel}" placeholder="메모 (선택)"></div>'
        f"</div>"
    )


def _confirmed_list(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    items = "".join(
        f'<li><b>{escape(str(r.get("journal_row", "")))}행</b> ← '
        f'{escape(str(r.get("source_name", "")))} <span class="sub">({escape(str(r.get("sample", "")))})</span></li>'
        for r in rows
    )
    return (
        f'<details class="fold"><summary>이미 확정 — {len(rows)}개 (검토용)</summary>'
        f'<ul class="list">{items}</ul></details>'
    )


def _cluster_candidate_options(cluster: dict[str, Any]) -> str:
    """Build <option> elements from a cluster's candidate_options (JSON string or list)."""
    raw = cluster.get("candidate_options") or "[]"
    if isinstance(raw, str):
        try:
            candidates = _json.loads(raw)
        except Exception:
            candidates = []
    else:
        candidates = list(raw)
    opts = ['<option value="">— 선택 —</option>']
    for idx, c in enumerate(candidates):
        ck = escape(str(c.get("condition_key", "")))
        delta = c.get("date_delta_days")
        delta_txt = f"±{delta}일" if delta is not None else "?"
        label = (
            f'행 {escape(str(c.get("journal_row", "")))} · {escape(str(c.get("sample", "")))} · '
            f'{escape(str(c.get("date", "")))} ({delta_txt}, {escape(str(c.get("score", "")))}점)'
        )
        rec = " ⟵ 코드 추천" if idx == 0 else ""
        opts.append(f'<option value="{ck}">{label}{rec}</option>')
    opts.append('<option value="__delete__">❌ 삭제 대상</option>')
    opts.append('<option value="__skip__">⏭ 모르겠음 / 보류</option>')
    return "".join(opts)


def _cluster_decision_card(cluster: dict[str, Any]) -> str:
    cid = escape(str(cluster.get("cluster_id", "")))
    members = escape(str(cluster.get("member_paths", "")))
    has_zero = cluster.get("has_zero", False)
    has_24 = cluster.get("has_24", False)
    endpoint_txt = ("✅" if has_zero else "✗") + "0hr " + ("✅" if has_24 else "✗") + "24hr"
    provenance = str(cluster.get("merge_provenance") or "")
    provenance_html = f'<span class="sub">{escape(provenance)}</span>' if provenance else ""
    return (
        f'<div class="card">'
        f'<div class="chead">'
        f'<span class="fname">{cid}</span>'
        f'<span class="fdate">폴더 {escape(str(cluster.get("folder_date", "")))}</span>'
        f'<span class="fdate">시점: {escape(str(cluster.get("time_points", "")))} · 파일 {escape(str(cluster.get("file_count", "")))}개</span>'
        f'<span class="fdate">{endpoint_txt}</span>'
        f'{provenance_html}'
        f'<span class="badge warn">{escape(str(cluster.get("match_status", "")))}</span>'
        f'</div>'
        f'<div class="why">{escape(str(cluster.get("reason", "")))}</div>'
        f'<div class="pick"><label>올바른 실험일지 행:</label>'
        f'<select class="ans" data-cluster="{cid}" data-members="{members}">'
        f'{_cluster_candidate_options(cluster)}</select>'
        f'</div>'
        f'</div>'
    )


def _cluster_confirmed_list(clusters: list[dict[str, Any]]) -> str:
    if not clusters:
        return ""
    items = "".join(
        f'<li><b>{escape(str(c.get("cluster_id", "")))} </b>'
        f'{escape(str(c.get("condition_sample", "")))} <span class="sub">({escape(str(c.get("condition_date", "")))})</span></li>'
        for c in clusters
    )
    return (
        f'<details class="fold"><summary>시계열 확정 클러스터 — {len(clusters)}개 (검토용)</summary>'
        f'<ul class="list">{items}</ul></details>'
    )


def _orphans_note(payloads: dict[str, dict[str, Any]]) -> str:
    chunks = []
    for kind, payload in payloads.items():
        orphans = payload.get("orphans") or []
        if not orphans:
            continue
        items = "".join(
            f'<li>{escape(str(o.get("journal_row", "")))}행 · {escape(str(o.get("sample", "")))}</li>' for o in orphans[:200]
        )
        chunks.append(
            f'<details class="fold"><summary>{escape(kind.upper())} 고아 행 — 파일 없는 활성 행 {len(orphans)}개</summary>'
            f'<ul class="list">{items}</ul></details>'
        )
    return "".join(chunks)


def render_checklist_html(payloads: dict[str, dict[str, Any]]) -> str:
    decision_rows: list[dict[str, Any]] = []
    confirmed_rows: list[dict[str, Any]] = []
    decision_clusters: list[dict[str, Any]] = []
    confirmed_clusters: list[dict[str, Any]] = []
    for kind in ("eis", "capacity"):
        payload = payloads.get(kind) or {}
        for row in payload.get("rows", []):
            if str(row.get("status")) in _DECISION_STATUSES:
                decision_rows.append(row)
            elif str(row.get("status")) in ("verified", "manual"):
                confirmed_rows.append(row)
        for cluster in payload.get("deferred_rows", []):
            if str(cluster.get("match_status")) in _CLUSTER_DECISION_STATUSES:
                decision_clusters.append(cluster)
            elif str(cluster.get("match_status")) in ("verified", "manual"):
                confirmed_clusters.append(cluster)

    total_decisions = len(decision_rows) + len(decision_clusters)
    cards = "".join(_decision_card(r) for r in decision_rows)
    cluster_cards = "".join(_cluster_decision_card(c) for c in decision_clusters)
    all_cards = cards + cluster_cards or '<div class="empty">결정이 필요한 항목이 없습니다 🎉</div>'
    body = (
        f'<section class="block"><h2>결정 필요 — {total_decisions}개</h2>'
        f'<p class="hint">아래 각 파일이 어느 실험일지 행의 셀인지 골라주세요. "코드 추천"은 참고용이며, '
        f'행날짜·점수를 보고 다른 후보로 바꾸거나 삭제/보류를 선택할 수 있습니다.</p>'
        f"{all_cards}</section>"
        f'<section class="block">{_confirmed_list(confirmed_rows)}{_cluster_confirmed_list(confirmed_clusters)}{_orphans_note(payloads)}</section>'
    )
    return _SHELL.replace("__BODY__", body).replace("__TOTAL__", str(total_decisions))


_SHELL = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>매칭 확인 체크리스트</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; background: #f5f6f8; color: #1f2733; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 14px; padding-bottom: 60px; }
  .bar { position: sticky; top: 0; z-index: 10; background: #fff; border-bottom: 1px solid #e2e6ec; padding: 10px 16px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .bar h1 { margin: 0; font-size: 16px; }
  .bar .prog { font-weight: 700; color: #2566c4; }
  .bar button, .bar label.file { border: 1px solid #c3ccd8; background: #fff; border-radius: 7px; padding: 6px 12px; font-size: 13px; cursor: pointer; }
  .bar button.primary { background: #2566c4; color: #fff; border-color: #2566c4; }
  .spacer { flex: 1; }
  section.block { margin: 14px 16px; }
  section.block h2 { font-size: 15px; margin: 6px 0; }
  .hint { color: #6b7686; font-size: 12.5px; margin: 2px 0 12px; }
  .card { background: #fff; border: 1px solid #e2e6ec; border-left: 4px solid #f0a92b; border-radius: 9px; padding: 11px 13px; margin-bottom: 10px; }
  .chead { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .fname { font-family: ui-monospace, Menlo, monospace; font-weight: 600; }
  .fdate { color: #8b95a4; font-size: 12px; }
  .why { color: #56607a; font-size: 12.5px; margin: 5px 0 9px; }
  .pick { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .pick label { color: #44506a; font-size: 12.5px; }
  select.ans { padding: 6px 8px; border: 1px solid #c3ccd8; border-radius: 7px; min-width: 320px; max-width: 100%; font-size: 13px; }
  input.memo { padding: 6px 8px; border: 1px solid #d7dde6; border-radius: 7px; min-width: 160px; }
  .badge { padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; background: #fdf0d8; color: #b9760a; }
  details.fold { background: #fff; border: 1px solid #e7eaef; border-radius: 8px; padding: 8px 12px; margin-top: 10px; }
  details.fold summary { cursor: pointer; font-weight: 600; color: #44506a; }
  .list { margin: 8px 0 0; padding-left: 18px; columns: 2; }
  .list .sub { color: #8b95a4; }
  .empty { color: #1f8a4c; padding: 16px; font-weight: 600; }
  #blob { display: none; width: calc(100% - 32px); margin: 0 16px; height: 120px; font-family: ui-monospace, monospace; font-size: 11px; }
</style>
</head>
<body>
<div class="bar">
  <h1>매칭 확인 체크리스트</h1>
  <span class="prog" id="prog">0 / __TOTAL__ 결정됨</span>
  <span class="spacer"></span>
  <button class="primary" id="copyBtn">결과 복사 (카톡 회신용)</button>
  <button id="dlBtn">JSON 내려받기</button>
  <label class="file">불러오기<input type="file" id="loadInput" accept="application/json" style="display:none"></label>
</div>
<textarea id="blob" readonly></textarea>
__BODY__
<script>
(function(){
  const KEY='battery_matching_checklist_v1';
  const q=(sel,root)=> (root||document).querySelector(sel);
  function esc(f){ return (window.CSS&&CSS.escape)?CSS.escape(f):f.replace(/"/g,'\\\\"'); }
  function answers(){
    const out={};
    document.querySelectorAll('select.ans').forEach(s=>{
      const v=s.value;
      if(s.dataset.cluster){
        const id=s.dataset.cluster;
        const m=q('input.memo[data-cluster="'+esc(id)+'"]'); const memo=m?m.value:'';
        if(v||memo){
          const members=(s.dataset.members||'').split(';').filter(Boolean);
          out[id]={choice:v, memo:memo, members:members};
        }
      } else {
        const f=s.dataset.file;
        const m=q('input.memo[data-file="'+esc(f)+'"]'); const memo=m?m.value:'';
        if(v||memo) out[f]={choice:v, memo:memo};
      }
    });
    return {version:1, kind:'matching_checklist', answers:out};
  }
  function progress(){
    const sels=[...document.querySelectorAll('select.ans')];
    const done=sels.filter(s=>s.value).length;
    const p=q('#prog'); if(p) p.textContent=done+' / '+sels.length+' 결정됨';
  }
  function save(){ try{ localStorage.setItem(KEY, JSON.stringify(answers())); }catch(e){} progress(); }
  function restore(){
    let d={}; try{ d=JSON.parse(localStorage.getItem(KEY)||'{}'); }catch(e){}
    const a=(d&&d.answers)||{};
    Object.keys(a).forEach(k=>{
      const s=q('select.ans[data-file="'+esc(k)+'"]')||q('select.ans[data-cluster="'+esc(k)+'"]');
      if(s) s.value=a[k].choice||'';
      const m=q('input.memo[data-file="'+esc(k)+'"]')||q('input.memo[data-cluster="'+esc(k)+'"]');
      if(m) m.value=a[k].memo||'';
    });
    progress();
  }
  document.addEventListener('change', e=>{ if(e.target.matches('select.ans,input.memo')) save(); });
  document.addEventListener('input', e=>{ if(e.target.matches('input.memo')) save(); });
  q('#copyBtn').addEventListener('click', async ()=>{
    const txt=JSON.stringify(answers());
    try{ await navigator.clipboard.writeText(txt); alert('결과를 복사했습니다. 카톡 대화창에 붙여넣어 회신해주세요.'); }
    catch(e){ const ta=q('#blob'); ta.style.display='block'; ta.value=txt; ta.focus(); ta.select(); }
  });
  q('#dlBtn').addEventListener('click', ()=>{
    const blob=new Blob([JSON.stringify(answers(),null,2)],{type:'application/json'});
    const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='checklist-answers.json'; a.click();
  });
  q('#loadInput').addEventListener('change', ev=>{
    const file=ev.target.files[0]; if(!file) return;
    const r=new FileReader(); r.onload=()=>{ try{ localStorage.setItem(KEY, r.result); restore(); alert('불러왔습니다.'); }catch(e){ alert('불러오기 실패'); } };
    r.readAsText(file);
  });
  restore();
})();
</script>
</body>
</html>"""
