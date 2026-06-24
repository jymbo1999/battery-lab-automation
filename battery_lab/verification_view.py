"""Server-rendered matching-verification page.

Pure HTML rendering over `matching_service.verification_payload` output: a per-file
evidence table (verified rows included, with the *why*), summary badges, orphan rows,
and duplicate groups — so the research lead can review every file with confidence.
No file/journal mutation; read-only.
"""
from __future__ import annotations

from html import escape
from typing import Any

_STATUS_LABEL = {
    "verified": ("확정", "ok"),
    "manual": ("수동확정", "info"),
    "review": ("검토", "warn"),
    "ambiguous": ("모호", "warn"),
    "blocked": ("충돌", "bad"),
    "unmatched": ("미매칭", "muted"),
}
_RISKY = {"review", "ambiguous", "blocked", "unmatched"}


def _badge(status: str) -> str:
    label, cls = _STATUS_LABEL.get(status, (status or "?", "muted"))
    return f'<span class="badge {cls}">{escape(label)}</span>'


def _summary_bar(s: dict[str, Any]) -> str:
    cells = [
        ("in-scope 행", s.get("in_scope_rows", 0)),
        ("매칭(비교용)", s.get("matched_files", 0)),
        ("확인필요", s.get("needs_review", 0)),
        ("모호", s.get("ambiguous_files", 0)),
        ("미매칭", s.get("unmatched_files", 0)),
        ("고아행", s.get("orphan_rows", 0)),
        ("중복그룹", s.get("duplicate_groups", 0)),
        ("시계열 클러스터", s.get("time_series_clusters", 0)),
    ]
    inner = "".join(f'<div class="stat"><b>{escape(str(v))}</b><span>{escape(label)}</span></div>' for label, v in cells)
    return f'<div class="stats">{inner}</div>'


def _rows_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="empty">표시할 파일이 없습니다.</div>'
    body = []
    for r in rows:
        status = str(r.get("status", ""))
        risky = status in _RISKY
        exact = r.get("row_exact")
        exact_txt = "✅" if exact is True else ("—" if exact is None else "✗")
        delta = r.get("date_delta_days")
        delta_cls = "bad" if (isinstance(delta, int) and abs(delta) > 7) else ""
        body.append(
            f'<tr class="{"risky" if risky else ""}">'
            f'<td class="file">{escape(str(r.get("source_name") or r.get("relative_path", "")))}</td>'
            f'<td class="c sub">{escape(str(r.get("file_date", "")))}</td>'
            f'<td class="c">{escape(str(r.get("journal_row", "")))}<div class="sub">{escape(str(r.get("sample", "")))}</div></td>'
            f'<td class="c sub">{escape(str(r.get("date", "")))}</td>'
            f'<td class="c {delta_cls}">{escape(str(delta) if delta is not None else "")}</td>'
            f'<td>{_badge(status)}</td>'
            f'<td class="reason">{escape(str(r.get("reason", "")))}</td>'
            f'<td class="c">{exact_txt}</td>'
            f'<td class="sub">{escape(str(r.get("overlap_tokens", "")))}</td>'
            f'<td class="sub bad">{escape(str(r.get("conflict_tokens", "")))}</td>'
            f'<td class="c">{escape(str(r.get("score", "")))}</td>'
            "</tr>"
        )
    head = (
        "<tr><th>파일</th><th>파일<br>날짜</th><th>매칭 행</th><th>행<br>날짜</th><th>날짜<br>차</th>"
        "<th>상태</th><th>근거 (왜 이 행인가)</th><th>행번호<br>일치</th><th>겹친 단서</th><th>충돌</th><th>점수</th></tr>"
    )
    return f'<table class="grid"><thead>{head}</thead><tbody>{"".join(body)}</tbody></table>'


def _orphans(orphans: list[dict[str, Any]]) -> str:
    if not orphans:
        return ""
    items = "".join(
        f'<li><b>{escape(str(o.get("journal_row", "")))}행</b> · {escape(str(o.get("sample", "")))} '
        f'<span class="sub">{escape(str(o.get("date", "")))}</span></li>'
        for o in orphans
    )
    return f'<div class="section"><h3>🟠 고아 행 — 파일이 없는 활성 행 ({len(orphans)})</h3><ul class="list">{items}</ul></div>'


def _duplicates(invariant: dict[str, Any]) -> str:
    dups = (invariant or {}).get("duplicates") or []
    if not dups:
        return ""
    items = "".join(
        f'<li><b>{escape(str(d.get("journal_row", "")))}행</b> ← {escape(", ".join(str(f) for f in d.get("files", [])))}</li>'
        for d in dups
    )
    return f'<div class="section"><h3>🔁 중복 — 한 행에 파일 2개+ ({len(dups)})</h3><ul class="list">{items}</ul></div>'


def _kind_block(kind: str, payload: dict[str, Any]) -> str:
    title = {"eis": "EIS", "capacity": "Capacity"}.get(kind, kind)
    deferred = payload.get("deferred_rows", [])
    deferred_html = (
        f'<details class="deferred"><summary>시계열(_hr) — 매칭 후순위 보류 ({len(deferred)}개)</summary>'
        f'{_rows_table(deferred)}</details>'
        if deferred
        else ""
    )
    return (
        f'<section class="kind"><h2>{escape(title)}</h2>'
        f'{_summary_bar(payload.get("summary", {}))}'
        f'{_rows_table(payload.get("rows", []))}'
        f'{deferred_html}'
        f'{_orphans(payload.get("orphans", []))}'
        f'{_duplicates(payload.get("invariant", {}))}'
        "</section>"
    )


def render_verification_html(payloads: dict[str, dict[str, Any]]) -> str:
    blocks = "".join(_kind_block(kind, payloads[kind]) for kind in ("capacity", "eis") if kind in payloads)
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>매칭 검증</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #f6f7f9; color: #1f2733; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 13px; }}
  .top {{ position: sticky; top: 0; z-index: 5; background: #fff; border-bottom: 1px solid #e2e6ec; padding: 12px 18px; display: flex; align-items: center; gap: 18px; }}
  .top h1 {{ margin: 0; font-size: 17px; }}
  .toggle {{ font-size: 13px; color: #44506a; cursor: pointer; }}
  section.kind {{ background: #fff; margin: 14px 18px; border: 1px solid #e2e6ec; border-radius: 10px; padding: 14px 16px; }}
  section.kind h2 {{ margin: 0 0 10px; font-size: 15px; }}
  .stats {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }}
  .stat {{ background: #f1f4f8; border-radius: 8px; padding: 7px 12px; min-width: 80px; text-align: center; }}
  .stat b {{ display: block; font-size: 17px; }}
  .stat span {{ color: #6b7686; font-size: 11px; }}
  table.grid {{ width: 100%; border-collapse: collapse; }}
  table.grid th, table.grid td {{ border-bottom: 1px solid #eef1f5; padding: 6px 8px; text-align: left; vertical-align: top; }}
  table.grid th {{ position: sticky; top: 53px; background: #f7f9fb; font-size: 11px; color: #5a6678; z-index: 1; }}
  td.file {{ font-family: ui-monospace, Menlo, monospace; font-size: 12px; max-width: 280px; word-break: break-all; }}
  td.reason {{ color: #364152; max-width: 340px; }}
  td.c {{ text-align: center; }}
  .sub {{ color: #8b95a4; font-size: 11px; }}
  .bad {{ color: #c0392b; }}
  tr.risky td {{ background: #fffaf0; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }}
  .badge.ok {{ background: #e3f6e9; color: #1f8a4c; }}
  .badge.info {{ background: #e6effb; color: #2566c4; }}
  .badge.warn {{ background: #fdf0d8; color: #b9760a; }}
  .badge.bad {{ background: #fbe4e1; color: #c0392b; }}
  .badge.muted {{ background: #eceef1; color: #6b7686; }}
  .section {{ margin-top: 14px; }}
  .section h3 {{ font-size: 13px; margin: 0 0 6px; }}
  .list {{ margin: 0; padding-left: 18px; columns: 2; }}
  .list li {{ margin: 2px 0; }}
  .empty {{ color: #8b95a4; padding: 18px; }}
</style>
</head>
<body>
<header class="top">
  <h1>매칭 검증 — 파일 ↔ 실험일지 행</h1>
  <label class="toggle"><input type="checkbox" id="onlyRisky"> 확인 필요만 보기 (확정 숨김)</label>
</header>
{blocks}
<script>
  const cb = document.getElementById('onlyRisky');
  cb.addEventListener('change', () => {{
    document.querySelectorAll('table.grid tbody tr').forEach(tr => {{
      tr.style.display = (cb.checked && !tr.classList.contains('risky')) ? 'none' : '';
    }});
  }});
</script>
</body>
</html>"""
