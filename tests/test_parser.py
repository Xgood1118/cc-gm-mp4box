# -*- coding: utf-8 -*-
"""解析器测试。

用 clip.mp4 验证真实文件，另外在内存里拼出合成 MP4，覆盖 64 位长度、
version 0 / 1、多轨道、stsc 分 chunk 等情况。
"""

import os
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mp4box.core import (  # noqa: E402
    Box,
    box_tree,
    duration_ms,
    extract_samples,
    find,
    find_all,
    find_deep,
    parse_boxes,
    parse_track,
    parse_tracks,
    read_file,
    read_mvhd,
    track_count,
)

CLIP = os.path.join(ROOT, "data", "clip.mp4")


# ---------------------------------------------------------------------------
# 合成 MP4 的小工具
# ---------------------------------------------------------------------------

def box(tag, body, force64=False):
    if force64:
        return struct.pack(">I", 1) + tag + struct.pack(">Q", 16 + len(body)) + body
    return struct.pack(">I", 8 + len(body)) + tag + body


def full(tag, version, flags, body):
    return box(tag, struct.pack(">B", version) + struct.pack(">I", flags)[1:] + body)


def mvhd_box(version, timescale, duration):
    if version == 1:
        body = (struct.pack(">QQ", 0, 0) + struct.pack(">I", timescale) +
                struct.pack(">Q", duration))
    else:
        body = (struct.pack(">II", 0, 0) + struct.pack(">I", timescale) +
                struct.pack(">I", duration))
    return full(b"mvhd", version, 0, body)


def mdhd_box(version, timescale, duration):
    if version == 1:
        body = (struct.pack(">QQ", 0, 0) + struct.pack(">I", timescale) +
                struct.pack(">Q", duration))
    else:
        body = (struct.pack(">II", 0, 0) + struct.pack(">I", timescale) +
                struct.pack(">I", duration))
    return full(b"mdhd", version, 0, body)


def tkhd_box(version, track_id, duration):
    if version == 1:
        body = (struct.pack(">QQ", 0, 0) + struct.pack(">II", track_id, 0) +
                struct.pack(">Q", duration))
    else:
        body = (struct.pack(">II", 0, 0) + struct.pack(">II", track_id, 0) +
                struct.pack(">I", duration))
    return full(b"tkhd", version, 3, body)


def hdlr_box(handler):
    return full(
        b"hdlr", 0, 0,
        struct.pack(">I", 0) + handler + b"\x00" * 12 + handler + b"Handler\x00",
    )


def stbl_box(sizes, chunk_offsets, stsc_entries, keyframes,
             constant_size=0, use_stss=True):
    entries = b"".join(struct.pack(">III", *e) for e in stsc_entries)
    stsc = full(b"stsc", 0, 0, struct.pack(">I", len(stsc_entries)) + entries)
    stsz = full(
        b"stsz", 0, 0,
        struct.pack(">II", constant_size, len(sizes)) +
        (b"" if constant_size else b"".join(struct.pack(">I", s) for s in sizes)),
    )
    stco = full(
        b"stco", 0, 0,
        struct.pack(">I", len(chunk_offsets)) +
        b"".join(struct.pack(">I", o) for o in chunk_offsets),
    )
    parts = [
        full(b"stsd", 0, 0, struct.pack(">I", 1) + box(b"avc1", b"\x00" * 78)),
    ]
    if sizes:
        parts.append(full(b"stts", 0, 0,
                          struct.pack(">I", 1) + struct.pack(">II", len(sizes), 1000)))
    if use_stss:
        parts.append(full(b"stss", 0, 0,
                          struct.pack(">I", len(keyframes)) +
                          b"".join(struct.pack(">I", k) for k in keyframes)))
    parts.extend([stsc, stsz, stco])
    return box(b"stbl", b"".join(parts))


def trak_box(handler, timescale, duration, sizes, chunk_offsets,
             stsc_entries, keyframes, track_id, version=0,
             mdhd_version=0, tkhd_version=0, constant_size=0):
    mdia = box(
        b"mdia",
        mdhd_box(mdhd_version, timescale, duration) +
        hdlr_box(handler) +
        box(b"minf", stbl_box(sizes, chunk_offsets, stsc_entries, keyframes,
                              constant_size=constant_size)),
    )
    return box(b"trak", tkhd_box(tkhd_version, track_id, duration) + mdia)


def synthetic_file(version=0, use_64bit_moov=False):
    """三条样本的视频轨 + 两条样本的音频轨，moov 在文件尾部。

    视频 sample 大小 [10, 20, 30]，落在 mdat 内的 0、10、30；
    音频 sample 大小 [5, 5]，落在 mdat 内的 40、45。
    """
    ftyp = box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2avc1mp41")
    mdat_payload = (b"A" * 10 + b"B" * 20 + b"C" * 30 +
                    b"D" * 5 + b"E" * 5)
    mdat = box(b"mdat", mdat_payload)
    data_start = len(ftyp) + 8

    video = trak_box(
        b"vide", 90000, 4500,
        sizes=[10, 20, 30],
        chunk_offsets=[data_start, data_start + 10, data_start + 30],
        stsc_entries=[(1, 1, 1)],
        keyframes=[1, 3],
        track_id=1,
        version=version,
        mdhd_version=version,
        tkhd_version=version,
    )
    audio = trak_box(
        b"soun", 48000, 96000,
        sizes=[5, 5],
        chunk_offsets=[data_start + 40],
        stsc_entries=[(1, 2, 1)],
        keyframes=[],
        track_id=2,
        version=version,
        mdhd_version=version,
        tkhd_version=version,
        constant_size=5,
    )
    moov = box(
        b"moov",
        mvhd_box(version, 600, 300) + video + audio,
        force64=use_64bit_moov,
    )
    return ftyp + mdat + moov


# ---------------------------------------------------------------------------
# box 层
# ---------------------------------------------------------------------------

def test_box_repr():
    b = Box("ftyp", 0, 24, 8, 16)
    assert "ftyp" in repr(b)


def test_parse_top_level_clip():
    data = read_file(CLIP)
    boxes = parse_boxes(data, 0, len(data))
    tags = [b.tag for b in boxes]
    assert tags == ["ftyp", "mdat", "moov"]
    assert boxes[-1].tag == "moov"
    assert boxes[-1].start == 1960
    assert boxes[1].body_start == 40


def test_recursive_containers_clip():
    data = read_file(CLIP)
    boxes = parse_boxes(data, 0, len(data))
    moov = find(boxes, "moov")
    assert moov is not None
    assert [b.tag for b in moov.children] == ["mvhd", "trak"]

    trak = find(moov.children, "trak")
    assert find(trak.children, "tkhd") is not None
    mdia = find(trak.children, "mdia")
    assert mdia is not None
    assert find(mdia.children, "mdhd") is not None
    assert find(mdia.children, "hdlr") is not None

    stbl = find_deep(moov.children, "stbl")
    assert stbl is not None
    tags = [b.tag for b in stbl.children]
    for tag in ("stsd", "stts", "stss", "stsz", "stco"):
        assert tag in tags


def test_box_tree_indentation():
    data = read_file(CLIP)
    tree = box_tree(parse_boxes(data, 0, len(data)))
    lines = tree.splitlines()
    assert lines[0].startswith("ftyp")
    moov_line = next(line for line in lines if "moov start" in line)
    trak_line = next(line for line in lines if "trak start" in line)
    mdia_line = next(line for line in lines if "mdia start" in line)
    stbl_line = next(line for line in lines if "stbl start" in line)
    assert moov_line.startswith("moov ")
    assert trak_line.startswith("  trak ")
    assert mdia_line.startswith("    mdia ")
    assert stbl_line.startswith("        stbl ")
    # 每个 box 都只剩一行，没有被按字符拆开
    assert len(lines) == 19


def test_size_zero_extends_to_end():
    # 前缀 8 字节：size(4) + tag(4)
    head = struct.pack(">I", 16) + b"ftyp" + b"isom" + struct.pack(">I", 512)
    inner = struct.pack(">I", 0) + b"free" + b"payload-until-end"
    data = head + inner
    boxes = parse_boxes(data, 0, len(data))
    assert boxes[0].tag == "ftyp"
    assert boxes[0].size == 16
    assert boxes[1].tag == "free"
    assert boxes[1].size == len(data) - 16
    assert boxes[1].body_size == len(data) - 16 - 8


def test_64bit_size_top_level_and_nested():
    # 顶层 64 位长度的 free box
    free = box(b"free", b"X" * 20, force64=True)
    assert struct.unpack(">I", free[0:4])[0] == 1
    assert struct.unpack(">Q", free[8:16])[0] == 16 + 20

    # moov 自身也是 64 位长度，里面仍要有可遍历的 trak
    data = synthetic_file(use_64bit_moov=True)
    boxes = parse_boxes(data, 0, len(data))
    moov = boxes[-1]
    assert moov.tag == "moov"
    assert moov.header_size == 16
    assert moov.body_start == moov.start + 16
    assert moov.size == len(data) - moov.start
    assert [b.tag for b in moov.children] == ["mvhd", "trak", "trak"]

    both = free + data
    parsed = parse_boxes(both, 0, len(both))
    assert parsed[0].tag == "free"
    assert parsed[0].body_size == 20
    assert parsed[1].start == len(free)
    assert parsed[1].tag == "ftyp"


def test_find_helpers():
    data = synthetic_file()
    boxes = parse_boxes(data, 0, len(data))
    moov = find(boxes, "moov")
    assert find(moov.children, "mvhd") is not None
    assert len(find_all(moov.children, "trak")) == 2
    assert find_deep(moov.children, "stsz") is not None
    assert find_deep(moov.children, "mdat") is None


# ---------------------------------------------------------------------------
# 版本与时长
# ---------------------------------------------------------------------------

def test_mvhd_version_0_offsets():
    data = synthetic_file(version=0)
    moov = parse_boxes(data, 0, len(data))[-1]
    assert read_mvhd(data, moov) == (600, 300)
    assert duration_ms(data, moov) == 500


def test_mvhd_version_1_offsets():
    data = synthetic_file(version=1)
    moov = parse_boxes(data, 0, len(data))[-1]
    assert read_mvhd(data, moov) == (600, 300)
    assert duration_ms(data, moov) == 500


def test_duration_ms_rounding_clip():
    data = read_file(CLIP)
    moov = find(parse_boxes(data, 0, len(data)), "moov")
    assert duration_ms(data, moov) == 3000


def test_version_1_track_fields():
    data = synthetic_file(version=1)
    boxes = parse_boxes(data, 0, len(data))
    tracks = parse_tracks(data, find(boxes, "moov"))
    assert tracks[0]["track_id"] == 1
    assert tracks[0]["timescale"] == 90000
    assert tracks[0]["duration"] == 4500
    assert tracks[0]["duration_ms"] == 50
    assert tracks[1]["track_id"] == 2
    assert tracks[1]["timescale"] == 48000
    assert tracks[1]["duration_ms"] == 2000


# ---------------------------------------------------------------------------
# 轨道
# ---------------------------------------------------------------------------

def test_track_count_counts_real_traks():
    data = read_file(CLIP)
    boxes = parse_boxes(data, 0, len(data))
    moov = find(boxes, "moov")
    assert track_count(data, moov) == 1

    # 把 trak 字样塞进普通 box 的负载里，不能影响计数
    ftyp = box(b"ftyp", b"trak" * 8)
    no_trak = ftyp + box(b"moov", mvhd_box(0, 1000, 1000))
    data2 = parse_boxes(no_trak, 0, len(no_trak))
    assert track_count(no_trak, find(data2, "moov")) == 0

    data3 = synthetic_file()
    boxes3 = parse_boxes(data3, 0, len(data3))
    assert track_count(data3, find(boxes3, "moov")) == 2


def test_track_handler_timescale_duration_clip():
    data = read_file(CLIP)
    moov = find(parse_boxes(data, 0, len(data)), "moov")
    trak = find(moov.children, "trak")
    track = parse_track(data, trak)
    assert track["handler"] == "vide"
    assert track["timescale"] == 90000
    assert track["duration"] == 270000
    assert abs(track["duration_ms"] - 3000) <= 1


# ---------------------------------------------------------------------------
# sample 表
# ---------------------------------------------------------------------------

def test_sample_offsets_sizes_clip():
    data = read_file(CLIP)
    moov = find(parse_boxes(data, 0, len(data)), "moov")
    track = parse_track(data, find(moov.children, "trak"))
    assert track["sample_count"] == 30
    assert len(track["samples"]) == 30

    sizes = [s["size"] for s in track["samples"]]
    assert sizes == [64] * 30
    offsets = [s["offset"] for s in track["samples"]]
    assert offsets[0] == 40
    assert offsets[1] == 104
    assert offsets[-1] == 40 + 64 * 29
    # 所有 sample 都要落在 mdat 负载内
    for s in track["samples"]:
        assert s["mdat_offset"] is not None
        assert 0 <= s["mdat_offset"] <= 1920 - 64


def test_stsc_chunk_assignment_synthetic():
    data = synthetic_file()
    moov = find(parse_boxes(data, 0, len(data)), "moov")
    tracks = parse_tracks(data, moov)

    video = tracks[0]
    assert [s["size"] for s in video["samples"]] == [10, 20, 30]
    assert [s["mdat_offset"] for s in video["samples"]] == [0, 10, 30]

    audio = tracks[1]
    assert [s["size"] for s in audio["samples"]] == [5, 5]
    # 两个 sample 共用一个 chunk，第二个偏移要按第一个的大小递推
    assert [s["mdat_offset"] for s in audio["samples"]] == [40, 45]
    assert [s["offset"] for s in audio["samples"]] == [80, 85]


def test_keyframes_clip():
    data = read_file(CLIP)
    moov = find(parse_boxes(data, 0, len(data)), "moov")
    track = parse_track(data, find(moov.children, "trak"))
    assert [k["sample"] for k in track["keyframes"]] == [1, 15, 29]
    assert track["keyframes"][0]["offset"] == 40
    assert track["keyframes"][1]["offset"] == 936
    assert track["keyframes"][2]["offset"] == 1832
    # 关键帧在 mdat 负载内的偏移
    assert [k["mdat_offset"] for k in track["keyframes"]] == [0, 896, 1792]
    flagged = [s["index"] for s in track["samples"] if s["keyframe"]]
    assert flagged == [1, 15, 29]


def test_keyframes_synthetic():
    data = synthetic_file()
    moov = find(parse_boxes(data, 0, len(data)), "moov")
    tracks = parse_tracks(data, moov)
    assert [k["sample"] for k in tracks[0]["keyframes"]] == [1, 3]
    assert [k["mdat_offset"] for k in tracks[0]["keyframes"]] == [0, 30]
    # stss 为空表示没有关键帧
    assert tracks[1]["keyframes"] == []


def test_sample_durations_and_dts():
    data = synthetic_file()
    moov = find(parse_boxes(data, 0, len(data)), "moov")
    video = parse_tracks(data, moov)[0]
    assert [s["duration"] for s in video["samples"]] == [1000, 1000, 1000]
    assert [s["dts"] for s in video["samples"]] == [0, 1000, 2000]


# ---------------------------------------------------------------------------
# 抽帧
# ---------------------------------------------------------------------------

def _tmpdir():
    return tempfile.mkdtemp(prefix="mp4box-test-")


def test_extract_samples_clip():
    tmp = _tmpdir()
    data = read_file(CLIP)
    moov = find(parse_boxes(data, 0, len(data)), "moov")
    track = parse_track(data, find(moov.children, "trak"))
    written = extract_samples(data, track["samples"], tmp)

    assert len(written) == track["sample_count"] == 30
    for info, sample in zip(written, track["samples"]):
        assert os.path.getsize(info["path"]) == sample["size"] == 64
        assert info["size"] == sample["size"]

    # 30 个文件拼起来就是 mdat 负载
    mdat = find(parse_boxes(data, 0, len(data)), "mdat")
    joined = b"".join(
        open(info["path"], "rb").read() for info in
        sorted(written, key=lambda w: w["index"])
    )
    assert joined == data[mdat.body_start:mdat.body_start + mdat.body_size]


def test_extract_samples_synthetic():
    tmp = _tmpdir()
    data = synthetic_file()
    moov = find(parse_boxes(data, 0, len(data)), "moov")
    tracks = parse_tracks(data, moov)
    out = os.path.join(tmp, "frames")
    written = extract_samples(data, tracks[0]["samples"], out, prefix="v")

    assert [w["size"] for w in written] == [10, 20, 30]
    contents = [open(w["path"], "rb").read() for w in written]
    assert contents[0] == b"A" * 10
    assert contents[1] == b"B" * 20
    assert contents[2] == b"C" * 30


def test_extract_rejects_out_of_bounds():
    tmp = _tmpdir()
    try:
        extract_samples(b"short", [{"index": 1, "offset": 100, "size": 10}], tmp)
    except ValueError:
        pass
    else:
        raise AssertionError("越界的 sample 应该抛 ValueError")
