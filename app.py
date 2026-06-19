from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from battery_lab.cli import collect_paths
from battery_lab.conditions import read_conditions
from battery_lab.excel_dashboard import DEFAULT_CONDITION_WORKBOOK, DEFAULT_CONDITION_SHEET, ensure_excel_dashboard_server
from battery_lab.file_io import parse_file
from battery_lab.journal import write_journal
from battery_lab.metrics import compute_metrics
from battery_lab.report import write_outputs
from battery_lab.ui import inject_app_chrome, render_analysis_panel, render_finder_page, render_sidebar
from battery_lab.wonatech_service import convert_wonatech_inputs, conversions_to_rows


def main() -> None:
    try:
        import streamlit as st  # type: ignore
        import streamlit.components.v1 as components  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit("Streamlit is not installed. Run `python3 -m pip install -r requirements.txt`, or use the CLI.") from exc

    st.set_page_config(page_title="배터리 실험 자동 정리", layout="wide")
    inject_app_chrome(st)
    page = render_sidebar(st)

    if page == "journal":
        render_journal_page(st, components)
    elif page == "files":
        render_finder_page(st, components)
    elif page in {"eis", "voltage_profile", "capacity"}:
        render_analysis_panel(st, components, page)
    else:
        render_journal_page(st, components)


def render_journal_page(st: object, components: object) -> None:
    st.title("배터리 실험 자동 정리")
    st.caption("실험 일지를 기준으로 Capacity, Voltage profile, EIS 데이터를 정리합니다.")
    st.subheader("실험 일지")
    st.caption(f"{DEFAULT_CONDITION_WORKBOOK.name} / {DEFAULT_CONDITION_SHEET} 시트를 엑셀 화면처럼 편집합니다. 셀을 수정하고 포커스를 벗어나면 원본 XLSX에 저장됩니다.")
    if DEFAULT_CONDITION_WORKBOOK.exists():
        try:
            server = ensure_excel_dashboard_server(DEFAULT_CONDITION_WORKBOOK, DEFAULT_CONDITION_SHEET)
            st.iframe(server.url, height=820)
        except Exception as exc:
            st.error(f"셀 조건표를 불러오지 못했습니다: {exc}")
    else:
        st.warning(f"실험 일지 파일이 없습니다: {DEFAULT_CONDITION_WORKBOOK}")

    with st.expander("파일 업로드 처리", expanded=False):
        uploaded = st.file_uploader(
            "실험 데이터 업로드 (CSV, TSV, TXT, SEO, SDE, WRD, XLSX)",
            type=["csv", "tsv", "txt", "seo", "sde", "wrd", "xlsx", "xls"],
            accept_multiple_files=True,
        )
        condition_file = st.file_uploader("셀 조건표 업로드 (선택)", type=["csv", "xlsx", "xls"])
        output_dir = Path(st.text_input("출력 폴더", "battery_visual_outputs"))
        write_wrd_raw = st.checkbox("WRD raw time-series CSV 생성", value=False, help="수십~수백 MB가 될 수 있어 기본값은 꺼져 있습니다.")
        make_journal = st.checkbox("날짜별 실험 일지 생성", value=True)
        journal_dir = Path(st.text_input("일지 폴더", "lab_journal", disabled=not make_journal))
        if st.button("파일 처리", type="primary", disabled=not uploaded):
            with TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                for file in uploaded:
                    (tmp_path / file.name).write_bytes(file.getbuffer())
                conditions = {}
                if condition_file is not None:
                    condition_path = tmp_path / condition_file.name
                    condition_path.write_bytes(condition_file.getbuffer())
                    conditions = read_conditions(condition_path)
                paths = collect_paths(tmp_path)
                if condition_file is not None:
                    paths = [path for path in paths if path.name != condition_file.name]
                paths, conversions, conversion_errors = convert_wonatech_inputs(
                    paths,
                    output_dir / "processed",
                    write_raw_wrd=write_wrd_raw,
                )
                datasets = []
                records = []
                parse_errors = []
                for path in paths:
                    try:
                        dataset = parse_file(path)
                        datasets.append(dataset)
                        records.append(compute_metrics(dataset))
                    except Exception as exc:
                        parse_errors.append(f"{path.name}: {exc}")
                write_outputs(datasets, records, output_dir, conditions)
                journal_days = write_journal(datasets, records, journal_dir, conditions) if make_journal else []
            st.success(f"{len(records)}개 파일 처리 완료. 리포트: {output_dir / 'report.html'}")
            st.info(f"대시보드: {output_dir / 'dashboard.html'}")
            if conversions:
                st.dataframe(conversions_to_rows(conversions))
            for error in conversion_errors + parse_errors:
                st.error(error)
            if make_journal:
                st.info(f"날짜별 실험 일지: {journal_dir / 'index.html'} ({len(journal_days)}일)")
            st.dataframe([{"cell_id": r.cell_id, "analysis_type": r.analysis_type, **r.metrics, "warning": r.warning} for r in records])
            dashboard_path = output_dir / "dashboard.html"
            if dashboard_path.exists():
                with st.expander("대시보드 미리보기", expanded=True):
                    components.html(dashboard_path.read_text(encoding="utf-8"), height=900, scrolling=True)


if __name__ == "__main__":
    main()
