# MP4 box 解析

一个只用 Python 标准库的 MP4 解析器：递归展开 box 树、处理 32/64 位长度与 full box 版本差异，
读出轨道、sample 表和关键帧，并能按 sample 表把字节抽成独立文件。

## 能力

- **层级**：`parse_boxes` 递归展开容器 box（`moov`/`trak`/`mdia`/`minf`/`stbl`/`dinf`/`edts`/`udta`），
  每个 box 子节点挂在 `Box.children`；`box_tree()` 输出缩进文本。
- **长度**：32 位长度、64 位长度（`size == 1`，后续 8 字节）、`size == 0`（延伸到父级末尾）都支持。
- **版本**：`mvhd`/`mdhd`/`tkhd` 的 version 0 与 version 1 分别读取字段偏移。
- **轨道**：`track_count` 数真实 `trak` box；`parse_tracks` 给出 handler、timescale、duration、duration_ms。
- **sample 表**：`stsz`/`stco`/`co64`/`stsc`/`stts`/`stss`，得到每个 sample 的文件偏移、大小、
  mdat 内偏移、DTS、时长与关键帧标记。
- **抽帧**：`extract_samples` 按 sample 表把每个 sample 写成独立文件。

## 运行

```
python gen_samples.py
python -m mp4box.cli data/clip.mp4 --out report.json
python -m mp4box.cli data/clip.mp4 --out report.json --extract frames/
python -m pytest tests -q
```

`report.json` 里除了摘要，还有完整 `box_tree`（缩进 box 树）与 `track_details`
（每条轨道的 handler / timescale / duration_ms / samples / keyframes）。

## data/clip.mp4 的解析结果

```
python -m mp4box.cli data/clip.mp4 --out report.json
```

| 项目 | 值 |
| --- | --- |
| 轨道数 | 1（handler `vide`） |
| 影片时长 | 3000 ms（mvhd timescale 1000，duration 3000） |
| 轨道时间刻度 / 时长 | 90000 / 270000（= 3000 ms） |
| sample 总数 | 30 |
| 每个 sample 偏移 / 大小 | 40, 104, 168, … / 64 字节（30 个等大） |
| 关键帧序号 | 1, 15, 29（1-based） |
| 关键帧在 mdat 内的偏移 | 0, 896, 1792 |
| 关键帧文件偏移 | 40, 936, 1832 |
| mdat 起始偏移（box / 负载） | 32 / 40 |
| moov 位置 | 文件尾部（start 1960） |

`clip.mp4` 的结构：ftyp、mdat（30 个 64 字节 sample）、尾部 moov。moov 内 mvhd 之后是一条
视频 trak，stbl 带 stsd、stts、stss（关键帧 1/15/29）、stsz、stco。

抽帧验证：`--extract` 写出 30 个文件，每个 64 字节，与 `stsz` 记录一致。
