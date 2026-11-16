# -*- coding: utf-8 -*-
"""MP4 box 解析器。

只用标准库。支持：

* 递归展开容器型 box（moov/trak/mdia/minf/stbl/...）；
* 32 位、64 位（size == 1）以及 size == 0（延伸到父级末尾）的长度；
* full box 的 version 0 / version 1 字段偏移；
* 轨道列表（handler、timescale、duration）；
* stsz / stco / co64 / stsc / stts / stss 组成的 sample 表和关键帧；
* 按 sample 表把字节抽成独立文件。
"""

import os
import struct

# 可以继续向下解析子 box 的容器。stsd 里的 sample entry 不是普通 box，
# dref / meta 之类带额外前缀的也不在此列，避免误解析。
CONTAINERS = {"moov", "trak", "mdia", "minf", "stbl", "dinf", "edts", "udta"}

MAX_DEPTH = 64


class Box(object):
    """一个 MP4 box。

    start/size 是整份文件里的位置；body_start/body_size 指向 box 负载。
    64 位长度时 header 为 16 字节，其余为 8 字节。
    """

    def __init__(self, tag, start, size, body_start, body_size):
        self.tag = tag
        self.start = start
        self.size = size
        self.body_start = body_start
        self.body_size = body_size
        self.children = []

    @property
    def header_size(self):
        return self.body_start - self.start

    def __repr__(self):
        return "Box(%s, %d, body %d)" % (self.tag, self.size, self.body_size)


def _decode_tag(raw):
    return raw.decode("ascii", "replace")


def parse_boxes(data, start, end, depth=0):
    """解析 [start, end) 范围内的 box，容器型 box 递归展开到 children。"""
    boxes = []
    pos = start
    end = min(end, len(data))
    while pos + 8 <= end:
        size = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = _decode_tag(data[pos + 4:pos + 8])
        header = 8
        if size == 1:
            # 64 位长度：紧接着的 8 字节是真实长度。
            if pos + 16 > end:
                break
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            header = 16
        elif size == 0:
            # 长度为 0：这个 box 一直延伸到父级末尾。
            size = end - pos
        if size < header:
            # 长度不合法，继续解析只会死循环，直接停下。
            break
        if pos + size > end:
            size = end - pos
        body_start = pos + header
        body_size = size - header
        box = Box(tag, pos, size, body_start, body_size)
        if tag in CONTAINERS and depth < MAX_DEPTH and body_size > 0:
            box.children = parse_boxes(
                data, body_start, body_start + body_size, depth + 1
            )
        boxes.append(box)
        pos += size
    return boxes


def read_file(path):
    with open(path, "rb") as f:
        return f.read()


def find(boxes, tag):
    for b in boxes:
        if b.tag == tag:
            return b
    return None


def find_all(boxes, tag):
    """在一组 box 里找出所有同名 box。"""
    return [b for b in boxes if b.tag == tag]


def find_deep(boxes, tag):
    """深度优先查找第一个同名 box（含自身层级）。"""
    for b in boxes:
        if b.tag == tag:
            return b
        hit = find_deep(b.children, tag)
        if hit is not None:
            return hit
    return None


def _children(data, box):
    """取得 box 的子 box；已解析过就直接用，否则现场解析。"""
    if box.children:
        return box.children
    if box.tag not in CONTAINERS or box.body_size <= 0:
        return []
    box.children = parse_boxes(
        data, box.body_start, box.body_start + box.body_size
    )
    return box.children


def _box_tree_lines(boxes, indent):
    lines = []
    for b in boxes:
        lines.append(
            "%s%s start=%d size=%d body_start=%d body_size=%d"
            % ("  " * indent, b.tag, b.start, b.size, b.body_start, b.body_size)
        )
        lines.extend(_box_tree_lines(b.children, indent + 1))
    return lines


def box_tree(boxes, indent=0):
    """把 box 列表渲染成缩进文本，一行一个 box。"""
    return "\n".join(_box_tree_lines(boxes, indent))


# ---------------------------------------------------------------------------
# full box / 时长
# ---------------------------------------------------------------------------

def _full_box(data, box):
    """返回 (version, flags, payload)。

    payload 已经跳过 version(1) + flags(3)。
    """
    raw = data[box.body_start:box.body_start + box.body_size]
    if len(raw) < 4:
        return 0, 0, b""
    return raw[0], struct.unpack(">I", b"\x00" + raw[1:4])[0], raw[4:]


def read_mvhd(data, moov):
    """读出 mvhd 的 (timescale, duration)。版本 0 / 1 偏移不同。"""
    mvhd = find(_children(data, moov), "mvhd")
    if mvhd is None:
        return 0, 0
    version, _flags, payload = _full_box(data, mvhd)
    if version == 1:
        # creation(8) modification(8) timescale(4) duration(8)
        if len(payload) < 28:
            return 0, 0
        timescale = struct.unpack(">I", payload[16:20])[0]
        duration = struct.unpack(">Q", payload[20:28])[0]
    else:
        # creation(4) modification(4) timescale(4) duration(4)
        if len(payload) < 16:
            return 0, 0
        timescale = struct.unpack(">I", payload[8:12])[0]
        duration = struct.unpack(">I", payload[12:16])[0]
    return timescale, duration


def read_mdhd(data, mdia):
    """读出 mdhd 的 (timescale, duration)，处理版本 0 / 1。"""
    mdhd = find(_children(data, mdia), "mdhd")
    if mdhd is None:
        return 0, 0
    version, _flags, payload = _full_box(data, mdhd)
    if version == 1:
        if len(payload) < 28:
            return 0, 0
        timescale = struct.unpack(">I", payload[16:20])[0]
        duration = struct.unpack(">Q", payload[20:28])[0]
    else:
        if len(payload) < 16:
            return 0, 0
        timescale = struct.unpack(">I", payload[8:12])[0]
        duration = struct.unpack(">I", payload[12:16])[0]
    return timescale, duration


def read_tkhd(data, trak):
    """读出 tkhd 的 (track_id, duration)，处理版本 0 / 1。"""
    tkhd = find(_children(data, trak), "tkhd")
    if tkhd is None:
        return 0, 0
    version, _flags, payload = _full_box(data, tkhd)
    if version == 1:
        # creation(8) modification(8) track_ID(4) reserved(4) duration(8)
        if len(payload) < 32:
            return 0, 0
        track_id = struct.unpack(">I", payload[16:20])[0]
        duration = struct.unpack(">Q", payload[24:32])[0]
    else:
        # creation(4) modification(4) track_ID(4) reserved(4) duration(4)
        if len(payload) < 20:
            return 0, 0
        track_id = struct.unpack(">I", payload[8:12])[0]
        duration = struct.unpack(">I", payload[16:20])[0]
    return track_id, duration


def _to_ms(duration, timescale):
    if not timescale:
        return 0
    return int(round(duration * 1000.0 / timescale))


def duration_ms(data, moov):
    """mvhd 电影时长换算成毫秒。"""
    if moov is None:
        return 0
    timescale, duration = read_mvhd(data, moov)
    return _to_ms(duration, timescale)


def track_count(data, moov):
    """真实 trak box 的数量，不靠字节串计数。"""
    if moov is None:
        return 0
    return len(find_all(_children(data, moov), "trak"))


# ---------------------------------------------------------------------------
# sample 表
# ---------------------------------------------------------------------------

def _parse_stsz(data, stbl):
    """返回 (sample_count, sizes)。sample_size 非 0 时所有 sample 等大。"""
    stsz = find(_children(data, stbl), "stsz")
    if stsz is None:
        return 0, []
    _version, _flags, payload = _full_box(data, stsz)
    if len(payload) < 8:
        return 0, []
    sample_size, sample_count = struct.unpack(">II", payload[0:8])
    if sample_size:
        return sample_count, [sample_size] * sample_count
    count = min(sample_count, max(0, (len(payload) - 8) // 4))
    sizes = list(struct.unpack(">%dI" % count, payload[8:8 + 4 * count]))
    # 表里可能少于声明的数量，用 0 补齐，保持索引稳定。
    if len(sizes) < sample_count:
        sizes.extend([0] * (sample_count - len(sizes)))
    return sample_count, sizes


def _parse_stco(data, stbl):
    """读出 chunk 偏移，stco（32 位）与 co64（64 位）都支持。"""
    children = _children(data, stbl)
    stco = find(children, "stco")
    fmt = ">I"
    width = 4
    if stco is None:
        stco = find(children, "co64")
        fmt = ">Q"
        width = 8
    if stco is None:
        return []
    _version, _flags, payload = _full_box(data, stco)
    if len(payload) < 4:
        return []
    count = struct.unpack(">I", payload[0:4])[0]
    count = min(count, max(0, (len(payload) - 4) // width))
    return list(struct.unpack(">%d%s" % (count, fmt[1]), payload[4:4 + width * count]))


def _parse_stsc(data, stbl):
    """返回 stsc 条目 [(first_chunk, samples_per_chunk, desc_index), ...]。"""
    stsc = find(_children(data, stbl), "stsc")
    if stsc is None:
        return []
    _version, _flags, payload = _full_box(data, stsc)
    if len(payload) < 4:
        return []
    count = struct.unpack(">I", payload[0:4])[0]
    count = min(count, max(0, (len(payload) - 4) // 12))
    entries = []
    for i in range(count):
        off = 4 + i * 12
        entries.append(struct.unpack(">III", payload[off:off + 12]))
    return entries


def _sample_offsets(sizes, chunk_offsets, stsc_entries):
    """按 stsc 把 sample 分配到 chunk，算出每个 sample 的绝对文件偏移。"""
    if not sizes or not chunk_offsets:
        return []

    if stsc_entries:
        per_chunk = []
        for i, (first_chunk, samples_per_chunk, _desc) in enumerate(stsc_entries):
            last_chunk = (
                stsc_entries[i + 1][0] - 1
                if i + 1 < len(stsc_entries)
                else len(chunk_offsets)
            )
            first_chunk = max(1, first_chunk)
            for _chunk in range(first_chunk, min(last_chunk, len(chunk_offsets)) + 1):
                per_chunk.append(samples_per_chunk)
        # 声明的 chunk 数可能多于 stsc 覆盖的，沿用最后一个条目的值。
        if per_chunk:
            per_chunk.extend([per_chunk[-1]] * (len(chunk_offsets) - len(per_chunk)))

        offsets = []
        sample_index = 0
        for chunk_index, chunk_offset in enumerate(chunk_offsets):
            if sample_index >= len(sizes):
                break
            samples_here = per_chunk[chunk_index] if chunk_index < len(per_chunk) else 0
            cursor = chunk_offset
            for _ in range(samples_here):
                if sample_index >= len(sizes):
                    break
                offsets.append(cursor)
                cursor += sizes[sample_index]
                sample_index += 1
        if len(offsets) == len(sizes):
            return offsets

    # 没有 stsc 时的合理回退：
    # 1) chunk 数与 sample 数相同 —— 每个 chunk 一个 sample；
    # 2) 只有一个 chunk —— 所有 sample 连续排布；
    # 3) 其他情况 —— 从首个 chunk 开始按顺序连续排布。
    if len(chunk_offsets) == len(sizes):
        return list(chunk_offsets)
    offsets = []
    cursor = chunk_offsets[0]
    for size in sizes:
        offsets.append(cursor)
        cursor += size
    return offsets


def _parse_stts(data, stbl, sample_count):
    """展开 stts，返回每个 sample 的时长（media timescale）。"""
    stts = find(_children(data, stbl), "stts")
    durations = []
    if stts is not None:
        _version, _flags, payload = _full_box(data, stts)
        if len(payload) >= 4:
            entry_count = struct.unpack(">I", payload[0:4])[0]
            entry_count = min(entry_count, max(0, (len(payload) - 4) // 8))
            for i in range(entry_count):
                off = 4 + i * 8
                count, delta = struct.unpack(">II", payload[off:off + 8])
                remaining = sample_count - len(durations)
                durations.extend([delta] * min(count, max(0, remaining)))
        if len(durations) < sample_count:
            pad = durations[-1] if durations else 0
            durations.extend([pad] * (sample_count - len(durations)))
    return durations


def _parse_stss(data, stbl, sample_count):
    """读出关键帧的 1-based sample 序号。

    没有 stss 时按规范所有 sample 都是同步帧；stss 存在但为空表示没有关键帧。
    """
    stss = find(_children(data, stbl), "stss")
    if stss is None:
        return list(range(1, sample_count + 1))
    _version, _flags, payload = _full_box(data, stss)
    if len(payload) < 4:
        return []
    count = struct.unpack(">I", payload[0:4])[0]
    count = min(count, max(0, (len(payload) - 4) // 4))
    return list(struct.unpack(">%dI" % count, payload[4:4 + 4 * count]))


def _mdat_boxes(data):
    return [b for b in parse_boxes(data, 0, len(data)) if b.tag == "mdat"]


def _mdat_offset(sample_offset, mdats):
    """sample 在 mdat 负载内的相对偏移；不在任何 mdat 里返回 None。"""
    for mdat in mdats:
        if mdat.body_start <= sample_offset < mdat.body_start + mdat.body_size:
            return sample_offset - mdat.body_start
    return None


def parse_track(data, trak, mdats=None):
    """解析一条 trak，返回轨道、sample 表和关键帧信息。"""
    if mdats is None:
        mdats = _mdat_boxes(data)

    children = _children(data, trak)
    track_id, tkhd_duration = read_tkhd(data, trak)
    mdia = find(children, "mdia")
    if mdia is None:
        return {
            "track_id": track_id,
            "handler": "",
            "timescale": 0,
            "duration": 0,
            "duration_ms": 0,
            "sample_count": 0,
            "samples": [],
            "keyframes": [],
        }

    mdia_children = _children(data, mdia)
    timescale, duration = read_mdhd(data, mdia)

    handler = ""
    hdlr = find(mdia_children, "hdlr")
    if hdlr is not None:
        _version, _flags, payload = _full_box(data, hdlr)
        if len(payload) >= 8:
            handler = _decode_tag(payload[4:8])

    stbl = find_deep(mdia_children, "stbl")
    sample_count, sizes = (0, [])
    chunk_offsets, stsc_entries, sample_durations, keyframe_numbers = [], [], [], []
    if stbl is not None:
        sample_count, sizes = _parse_stsz(data, stbl)
        chunk_offsets = _parse_stco(data, stbl)
        stsc_entries = _parse_stsc(data, stbl)
        sample_durations = _parse_stts(data, stbl, sample_count)
        keyframe_numbers = _parse_stss(data, stbl, sample_count)

    offsets = _sample_offsets(sizes, chunk_offsets, stsc_entries)
    if not sample_durations:
        sample_durations = [0] * sample_count

    keyframe_set = set(keyframe_numbers)
    samples = []
    dts = 0
    for i, (offset, size) in enumerate(zip(offsets, sizes)):
        sample_number = i + 1
        samples.append({
            "index": sample_number,
            "offset": offset,
            "size": size,
            "mdat_offset": _mdat_offset(offset, mdats),
            "keyframe": sample_number in keyframe_set,
            "dts": dts,
            "duration": sample_durations[i],
        })
        dts += sample_durations[i]

    keyframes = [
        {
            "sample": s["index"],
            "offset": s["offset"],
            "size": s["size"],
            "mdat_offset": s["mdat_offset"],
        }
        for s in samples
        if s["keyframe"]
    ]

    if not timescale:
        # mdhd 缺失时尽量从 tkhd 时长兜底，但仍无法换算媒体时间。
        duration = tkhd_duration

    return {
        "track_id": track_id,
        "handler": handler,
        "timescale": timescale,
        "duration": duration,
        "duration_ms": _to_ms(duration, timescale),
        "sample_count": sample_count,
        "samples": samples,
        "keyframes": keyframes,
    }


def parse_tracks(data, moov):
    if moov is None:
        return []
    mdats = _mdat_boxes(data)
    return [
        parse_track(data, trak, mdats)
        for trak in find_all(_children(data, moov), "trak")
    ]


# ---------------------------------------------------------------------------
# 抽帧
# ---------------------------------------------------------------------------

def extract_samples(data, samples, out_dir, prefix="sample"):
    """按 sample 表把每个 sample 的字节写成独立文件，返回写出的文件信息。"""
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for sample in samples:
        start = sample["offset"]
        size = sample["size"]
        end = start + size
        if start < 0 or end > len(data):
            raise ValueError(
                "sample %d 越界: offset=%d size=%d file_size=%d"
                % (sample["index"], start, size, len(data))
            )
        path = os.path.join(out_dir, "%s_%05d.bin" % (prefix, sample["index"]))
        with open(path, "wb") as f:
            f.write(data[start:end])
        written.append({
            "index": sample["index"],
            "path": path,
            "size": os.path.getsize(path),
        })
    return written


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_report(data, path=""):
    """把整份文件解析成可 JSON 序列化的报告。"""
    boxes = parse_boxes(data, 0, len(data))
    moov = find(boxes, "moov")
    mdats = [b for b in boxes if b.tag == "mdat"]
    tracks = parse_tracks(data, moov) if moov is not None else []

    samples = []
    keyframes = []
    for track in tracks:
        samples.extend(track["samples"])
        keyframes.extend(track["keyframes"])
    keyframe_numbers = [k["sample"] for k in keyframes]

    report = {
        "file": path,
        "file_size": len(data),
        "top_level": [
            {
                "tag": b.tag,
                "start": b.start,
                "size": b.size,
                "body_start": b.body_start,
            }
            for b in boxes
        ],
        "mdat_box_offset": mdats[0].start if mdats else None,
        "mdat_data_offset": mdats[0].body_start if mdats else None,
        "mdat_size": mdats[0].body_size if mdats else 0,
        "moov_at_end": bool(boxes and boxes[-1].tag == "moov"),
        "tracks": len(tracks),
        "duration_ms": duration_ms(data, moov),
        "sample_count": sum(t["sample_count"] for t in tracks),
        "keyframes": keyframe_numbers,
        "box_tree": box_tree(boxes),
        "track_details": tracks,
    }
    return report


def main(argv=None):
    import argparse
    import json

    ap = argparse.ArgumentParser(prog="mp4box")
    ap.add_argument("mp4")
    ap.add_argument("--out", default="report.json")
    ap.add_argument("--extract", default=None, help="把每个 sample 抽到该目录")
    args = ap.parse_args(argv)

    data = read_file(args.mp4)
    report = build_report(data, args.mp4)

    if args.extract:
        all_samples = []
        for track in report["track_details"]:
            all_samples.extend(track["samples"])
        extract_samples(data, all_samples, args.extract)

    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    summary = {
        "tracks": report["tracks"],
        "duration_ms": report["duration_ms"],
        "sample_count": report["sample_count"],
        "keyframes": report["keyframes"],
        "mdat_box_offset": report["mdat_box_offset"],
        "mdat_data_offset": report["mdat_data_offset"],
        "moov_at_end": report["moov_at_end"],
        "out": args.out,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0
