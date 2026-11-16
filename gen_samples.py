# -*- coding: utf-8 -*-
"""生成一个最小可解析的 MP4：ftyp + mdat + moov（moov 在尾部）。"""
import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = 30
SAMPLE_SIZE = 64
TIMESCALE = 1000
DURATION = 3000


def box(tag, body):
    return struct.pack(">I", 8 + len(body)) + tag + body


def fullbox(tag, version, flags, body):
    return box(tag, struct.pack(">B", version) + struct.pack(">I", flags)[1:] + body)


def build():
    ftyp = box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2avc1mp41")

    mdat_data = b"".join(bytes([(i * 7) % 256]) * SAMPLE_SIZE for i in range(SAMPLES))
    mdat = box(b"mdat", mdat_data)

    stsd_entry = (b"\x00" * 6 + struct.pack(">H", 1) +
                  b"\x00" * 16 + struct.pack(">HH", 32, 32) +
                  struct.pack(">II", 72, 72) + struct.pack(">I", 0) +
                  struct.pack(">H", 1) + b"\x00" * 32 + struct.pack(">H", 0x18) +
                  struct.pack(">H", 0xFFFF))
    stsd = fullbox(b"stsd", 0, 0, struct.pack(">I", 1) + box(b"avc1", stsd_entry))
    stts = fullbox(b"stts", 0, 0, struct.pack(">I", 1) + struct.pack(">II", SAMPLES, 3000))
    stss = fullbox(b"stss", 0, 0, struct.pack(">I", 3) + struct.pack(">III", 1, 15, 29))
    stsz = fullbox(b"stsz", 0, 0, struct.pack(">II", SAMPLE_SIZE, SAMPLES))
    stco_placeholder = fullbox(b"stco", 0, 0, struct.pack(">I", SAMPLES) +
                               b"\x00" * (4 * SAMPLES))
    stbl = box(b"stbl", stsd + stts + stss + stsz + stco_placeholder)

    vmhd = fullbox(b"vmhd", 0, 1, struct.pack(">HHHH", 0, 0, 0, 0))
    dinf = box(b"dinf", box(b"dref", struct.pack(">I", 1) + fullbox(b"url ", 0, 1, b"")))
    minf = box(b"minf", vmhd + dinf + stbl)

    hdlr = fullbox(b"hdlr", 0, 0, struct.pack(">I", 0) + b"vide" + b"\x00" * 12 + b"VideoHandler\x00")
    mdhd = fullbox(b"mdhd", 0, 0,
                   struct.pack(">IIII", 0, 0, 90000, 270000) + struct.pack(">HH", 0x55C4, 0))
    mdia = box(b"mdia", mdhd + hdlr + minf)

    tkhd = fullbox(b"tkhd", 0, 3,
                   struct.pack(">IIIII", 0, 0, 1, 0, DURATION) +
                   struct.pack(">II", 0, 0) + struct.pack(">HH", 0, 0) +
                   struct.pack(">HH", 0x0100, 0) +
                   struct.pack(">IIIIIIIIII", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000, 0) +
                   struct.pack(">II", 0, 0))
    trak = box(b"trak", tkhd + mdia)

    mvhd = fullbox(b"mvhd", 0, 0,
                   struct.pack(">IIII", 0, 0, TIMESCALE, DURATION) +
                   struct.pack(">IHH", 0x00010000, 0x0100, 0) +
                   b"\x00" * 10 +
                   struct.pack(">IIIIIIIIII", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000, 0) +
                   b"\x00" * 24 + struct.pack(">I", 2))
    moov_wo_stco = box(b"moov", mvhd + trak)

    # 回填 stco：mdat 数据起始 = ftyp + mdat header(8) + mdat_data 长度 + stco 之前的结构
    # 先算最终布局长度
    stco_len = 8 + 4 + 4 + 4 * SAMPLES
    part_before = len(ftyp) + 8 + len(mdat_data) + len(moov_wo_stco) + len(stsd) + len(stts) + len(stss) + len(stsz)
    # 更直接：mdat 在 moov 之前，moov 内 stco 之前的长度已知
    mdat_data_start = len(ftyp) + 8
    offsets = [mdat_data_start + i * SAMPLE_SIZE for i in range(SAMPLES)]
    stco = fullbox(b"stco", 0, 0, struct.pack(">I", SAMPLES) +
                   b"".join(struct.pack(">I", o) for o in offsets))
    stbl2 = box(b"stbl", stsd + stts + stss + stsz + stco)
    minf2 = box(b"minf", vmhd + dinf + stbl2)
    mdia2 = box(b"mdia", mdhd + hdlr + minf2)
    trak2 = box(b"trak", tkhd + mdia2)
    moov2 = box(b"moov", mvhd + trak2)

    return ftyp + mdat + moov2


if __name__ == "__main__":
    os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
    data = build()
    path = os.path.join(HERE, "data", "clip.mp4")
    with open(path, "wb") as f:
        f.write(data)
    print("已生成", path, len(data), "字节")
