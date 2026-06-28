import struct

from battery_lab.capacity_csv_audit import audit_capacity_csv_wrd_pairs


def _pack_wrd_record(
    *,
    test_time_s=0.0,
    cycle_index=0,
    voltage=3.7,
    current=0.1,
    charge_q_ah=0.0,
    discharge_q_ah=0.0,
):
    data = bytearray()
    data += struct.pack("<q", 638000000000000000)
    data += struct.pack("<i", 1)
    data += struct.pack("<q", int(test_time_s * 1e7))
    data += struct.pack("<q", int(test_time_s * 1e7))
    data += struct.pack("<q", int(test_time_s * 1e7))
    data += struct.pack("<i", 1)
    data += struct.pack("<i", 1)
    data += struct.pack("<i", cycle_index)
    data += bytes([1, 1, 1])
    data += struct.pack("<i", 0)
    current_range = b"101mA"
    data += bytes([len(current_range)])
    data += current_range
    values = [
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
    data += struct.pack("<9d", *values)
    return bytes(data)


def _write_wrd(path):
    header = b"metadata DATE TIME VOLTAGE CURRENT CHARGE Q DISCHARGE Q" + b"\x00" * 100
    path.write_bytes(
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


def test_capacity_csv_wrd_audit_marks_matching_csv_as_archive_candidate(tmp_path):
    capacity = tmp_path / "capacity"
    output = tmp_path / "battery_visual_outputs"
    folder = capacity / "260701"
    folder.mkdir(parents=True)
    _write_wrd(folder / "sample.wrd")
    (folder / "sample_Capacity.csv").write_text(
        "Cycle,Q_Charge_mAh,Q_Discharge_mAh,CE_export_Qch_over_Qdis_percent\n"
        "1,2.0,1.8,111.11111111111111\n"
        "2,3.0,2.4,125.0\n"
        "3,3.1,2.5,124.0\n",
        encoding="utf-8",
    )

    payload = audit_capacity_csv_wrd_pairs(capacity, output)

    assert payload["counts"]["archive_candidate"] == 1
    assert payload["rows"][0]["status"] == "archive_candidate"
    assert payload["rows"][0]["common_cycles"] == 3
    assert (output / "audits").exists()


def test_capacity_csv_wrd_audit_keeps_mismatched_csv(tmp_path):
    capacity = tmp_path / "capacity"
    output = tmp_path / "battery_visual_outputs"
    folder = capacity / "260701"
    folder.mkdir(parents=True)
    _write_wrd(folder / "sample.wrd")
    (folder / "sample_Capacity.csv").write_text(
        "Cycle,Q_Charge_mAh,Q_Discharge_mAh\n"
        "1,2.0,1.0\n"
        "2,3.0,1.1\n"
        "3,3.1,1.2\n",
        encoding="utf-8",
    )

    payload = audit_capacity_csv_wrd_pairs(capacity, output)

    assert payload["counts"]["keep"] == 1
    assert payload["rows"][0]["status"] == "keep"
