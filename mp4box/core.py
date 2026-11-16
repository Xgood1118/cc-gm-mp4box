# -*- coding: utf-8 -*-
"""MP4 box 解析骨架。

只解析到第一层 box，不递归进入 moov，也不处理 64 位长度与版本差异。
"""

import struct


class Box(object):
    def __init__(self, tag, start, size, body_start, body_size):
        self.tag = tag
        self.start = start
        self.size = size
        self.body_start = body_start
        self.body_size = body_size
        self.children = []

    def __repr__(self):
        return "Box(%s, %d, body %d)" % (self.tag, self.size, self.body_size)


CONTAINERS = {"moov", "trak", "mdia", "minf", "stbl", "dinf", "edts", "udta"}


def parse_boxes(data, start, end, depth=0):
    """解析一层 box。当前不递归，子 box 只记录不展开。"""
    boxes = []
    pos = start
    while pos + 8 <= end:
        size = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8].decode("ascii", "replace")
        if size == 0:
            size = end - pos
        body_start = pos + 8
        body_size = max(0, size - 8)
        boxes.append(Box(tag, pos, size, body_start, body_size))
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


def track_count(data, moov):
    """轨道数量。当前直接按 body 里的字节猜，没有进 trak。"""
    return data.count(b"trak")


def duration_ms(data, moov):
    """时长。当前把 mvhd 的 timescale 当成毫秒，没有按 timescale 换算。"""
    header = parse_boxes(data, moov.body_start, moov.body_start + moov.body_size)
    mvhd = find(header, b"mvhd")
    if mvhd is None:
        return 0
    body = data[mvhd.body_start:mvhd.body_start + mvhd.body_size]
    version = body[0]
    if version == 0:
        timescale = struct.unpack(">I", body[12:16])[0]
        duration = struct.unpack(">I", body[16:20])[0]
    else:
        timescale = struct.unpack(">I", body[20:24])[0]
        duration = struct.unpack(">Q", body[24:32])[0]
    return duration if timescale == 0 else duration


def main(argv=None):
    import argparse
    import json
    ap = argparse.ArgumentParser(prog="mp4box")
    ap.add_argument("mp4")
    ap.add_argument("--out", default="report.json")
    args = ap.parse_args(argv)
    data = read_file(args.mp4)
    boxes = parse_boxes(data, 0, len(data))
    moov = find(boxes, b"moov")
    report = {
        "top_level": [{"tag": b.tag, "size": b.size} for b in boxes],
        "moov_at_end": bool(boxes and boxes[-1].tag == "moov"),
        "tracks": track_count(data, moov) if moov else 0,
        "duration_ms": duration_ms(data, moov) if moov else 0,
    }
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    return 0
