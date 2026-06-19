import csv
import math
import struct

from battery_lab.file_io import parse_file
from battery_lab.metrics import compute_metrics
from battery_lab.wonatech_service import convert_wonatech_file
from wonatech_parsers.eis import parse_eis_bytes
from wonatech_parsers.wrd import build_capacity_summary, parse_wrd_bytes


def test_parse_synthetic_eis_block_uses_verified_offsets():
    stride = 112
    start = 137
    freqs = [100000 * (10 ** (-i / 10)) for i in range(30)]
    buf = bytearray(b"HEADER" + b"\x00" * (start - 6))
    for i, freq in enumerate(freqs):
        off = start + i * stride
        if len(buf) < off + stride:
            buf.extend(b"\x00" * (off + stride - len(buf)))
        zre = 1.0 + i * 0.1
        zim = -(0.5 + i * 0.05)
        struct.pack_into("<f", buf, off + 0, freq)
        struct.pack_into("<f", buf, off + 28, zre)
        struct.pack_into("<f", buf, off + 32, zim)
        struct.pack_into("<f", buf, off + 92, 9999999.0)

    result = parse_eis_bytes(bytes(buf))

    assert len(result.records) == 30
    assert result.start_offset == start
    assert result.validation["ok"] is True
    assert math.isclose(result.records[0].frequency_hz, freqs[0], rel_tol=1e-6)
    assert math.isclose(result.records[0].zreal_ohm, 1.0, rel_tol=1e-6)
    assert result.layout["frequency_hz"]["offset"] == 0
    assert result.layout["zreal_ohm"]["offset"] == 28
    assert result.layout["zimag_ohm"]["offset"] == 32


def test_wonatech_eis_service_writes_csv_for_existing_analysis(tmp_path):
    source = tmp_path / "sample.SEO"
    processed = tmp_path / "processed"
    stride = 112
    start = 137
    freqs = [100000 * (10 ** (-i / 10)) for i in range(30)]
    buf = bytearray(b"HEADER" + b"\x00" * (start - 6))
    for i, freq in enumerate(freqs):
        off = start + i * stride
        if len(buf) < off + stride:
            buf.extend(b"\x00" * (off + stride - len(buf)))
        struct.pack_into("<f", buf, off + 0, freq)
        struct.pack_into("<f", buf, off + 28, 1.0 + i * 0.1)
        struct.pack_into("<f", buf, off + 32, -(0.5 + i * 0.05))
    source.write_bytes(bytes(buf))

    conversion = convert_wonatech_file(source, processed)
    dataset = parse_file(conversion.primary_csv_path)
    record = compute_metrics(dataset)

    assert conversion.primary_csv_path.name == "sample_eis.csv"
    assert conversion.meta_path.exists()
    assert dataset.meta.analysis_type == "eis"
    assert record.metrics["valid_points"] == 30
    assert record.warning == ""


def _pack_wrd_record(
    *,
    test_time_s=0.0,
    cycle_index=0,
    voltage=3.7,
    current=0.1,
    charge_q_ah=0.0,
    discharge_q_ah=0.0,
):
    b = bytearray()
    b += struct.pack("<q", 638000000000000000)
    b += struct.pack("<i", 1)
    b += struct.pack("<q", int(test_time_s * 1e7))
    b += struct.pack("<q", int(test_time_s * 1e7))
    b += struct.pack("<q", int(test_time_s * 1e7))
    b += struct.pack("<i", 1)
    b += struct.pack("<i", 1)
    b += struct.pack("<i", cycle_index)
    b += bytes([1, 1, 1])
    b += struct.pack("<i", 0)
    irange = b"101mA"
    b += bytes([len(irange)])
    b += irange
    vals = [
        voltage,
        current,
        charge_q_ah,
        discharge_q_ah,
        charge_q_ah * voltage,
        discharge_q_ah * voltage,
        0.0,
        25.0,
        voltage,
    ]
    b += struct.pack("<9d", *vals)
    return bytes(b)


def test_parse_synthetic_wrd_records_and_summary():
    header = b"metadata DATE TIME VOLTAGE CURRENT CHARGE Q DISCHARGE Q" + b"\x00" * 100
    records = b"".join(
        [
            _pack_wrd_record(test_time_s=0, cycle_index=0, charge_q_ah=0.001, discharge_q_ah=0.0),
            _pack_wrd_record(test_time_s=10, cycle_index=0, charge_q_ah=0.002, discharge_q_ah=0.0015),
            _pack_wrd_record(test_time_s=20, cycle_index=0, charge_q_ah=0.002, discharge_q_ah=0.0018),
            _pack_wrd_record(test_time_s=30, cycle_index=1, charge_q_ah=0.001, discharge_q_ah=0.0),
            _pack_wrd_record(test_time_s=40, cycle_index=1, charge_q_ah=0.0025, discharge_q_ah=0.0020),
            _pack_wrd_record(test_time_s=50, cycle_index=1, charge_q_ah=0.003, discharge_q_ah=0.0024),
        ]
    )
    parsed, validation = parse_wrd_bytes(header + records)
    summary = build_capacity_summary(parsed)

    assert validation["ok"] is True
    assert len(parsed) == 6
    assert len(summary) == 2
    assert summary[0]["Cycle"] == 1
    assert abs(summary[0]["Q_Charge_mAh"] - 2.0) < 1e-9
    assert abs(summary[0]["Q_Discharge_mAh"] - 1.8) < 1e-9


def test_wonatech_wrd_service_writes_capacity_summary_and_optional_raw(tmp_path):
    source = tmp_path / "sample.wrd"
    processed = tmp_path / "processed"
    header = b"metadata DATE TIME VOLTAGE CURRENT CHARGE Q DISCHARGE Q" + b"\x00" * 100
    source.write_bytes(
        header
        + b"".join(
            [
                _pack_wrd_record(test_time_s=0, cycle_index=0, charge_q_ah=0.001, discharge_q_ah=0.0),
                _pack_wrd_record(test_time_s=10, cycle_index=0, charge_q_ah=0.002, discharge_q_ah=0.0018),
                _pack_wrd_record(test_time_s=20, cycle_index=1, charge_q_ah=0.001, discharge_q_ah=0.0),
                _pack_wrd_record(test_time_s=30, cycle_index=1, charge_q_ah=0.003, discharge_q_ah=0.0024),
                _pack_wrd_record(test_time_s=40, cycle_index=2, charge_q_ah=0.001, discharge_q_ah=0.0),
                _pack_wrd_record(test_time_s=50, cycle_index=2, charge_q_ah=0.0031, discharge_q_ah=0.0025),
            ]
        )
    )

    conversion = convert_wonatech_file(source, processed, write_raw_wrd=True)
    dataset = parse_file(conversion.primary_csv_path)
    record = compute_metrics(dataset)

    assert conversion.primary_csv_path.name == "sample_capacity_summary.csv"
    assert conversion.raw_csv_path and conversion.raw_csv_path.exists()
    assert conversion.meta_path.exists()
    assert dataset.meta.analysis_type == "capacity"
    assert record.metrics["valid_points"] == 3
    with conversion.primary_csv_path.open(newline="", encoding="utf-8") as handle:
        first = next(csv.DictReader(handle))
    assert float(first["Q_Charge_mAh"]) == 2.0
    assert float(first["Q_Discharge_mAh"]) == 1.8
