from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from battery_lab.cli import collect_paths
from battery_lab.conditions import read_conditions
from battery_lab.file_io import parse_file
from battery_lab.metrics import compute_metrics
from battery_lab.report import write_outputs


def main() -> None:
    try:
        import streamlit as st  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit("Streamlit is not installed. Run `python3 -m pip install -r requirements.txt`, or use the CLI.") from exc

    st.set_page_config(page_title="배터리 실험 자동 정리", layout="wide")
    st.title("배터리 실험 자동 정리")
    st.caption("Capacity, Voltage profile, EIS, 면저항 파일을 올리면 그래프와 핵심 지표를 자동 생성합니다.")

    uploaded = st.file_uploader(
        "실험 데이터 업로드 (CSV, TSV, TXT, SDE, XLSX)",
        type=["csv", "tsv", "txt", "sde", "xlsx", "xls"],
        accept_multiple_files=True,
    )
    condition_file = st.file_uploader("셀 조건표 업로드 (선택)", type=["csv", "xlsx", "xls"])
    output_dir = Path(st.text_input("출력 폴더", "battery_visual_outputs"))
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
            datasets = [parse_file(path) for path in collect_paths(tmp_path)]
            if condition_file is not None:
                datasets = [dataset for dataset in datasets if dataset.meta.original_filename != condition_file.name]
            records = [compute_metrics(dataset) for dataset in datasets]
            write_outputs(datasets, records, output_dir, conditions)
        st.success(f"{len(records)}개 파일 처리 완료. 리포트: {output_dir / 'report.html'}")
        st.dataframe([{"cell_id": r.cell_id, "analysis_type": r.analysis_type, **r.metrics, "warning": r.warning} for r in records])


if __name__ == "__main__":
    main()
