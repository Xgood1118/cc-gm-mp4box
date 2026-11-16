# MP4 box 解析

骨架能读顶层 box、能找 moov、能读 mvhd 的版本与时长字段，但进不去轨道内部。

## 已知缺口

- `parse_boxes` 不递归，moov 里的 trak、mdia、stbl 全都没展开，`CONTAINERS` 定义了但没用上。
- `track_count` 直接在整份文件里数 `trak` 字节，这个字符串在别的 box 里出现也会被算上。
- `duration_ms` 把 timescale 当毫秒，实际 duration 要除以 timescale 再换算，30 秒的片子会报成 3000。
- 不支持 64 位长度（size 为 1 时长度在接下来的 8 字节里），大文件会解析错位。
- 不支持 size 为 0 表示延伸到文件末尾之外的边界情况。
- 没有读 stco、stsz、stss，抽不出关键帧偏移，也数不出 sample 数。
- 没有按 moov 在尾部这种布局做偏移修正的意识，README 里也没说明。

## 运行

```
python gen_samples.py
python -m mp4box.cli data/clip.mp4 --out report.json
python -m pytest tests -q
```

`clip.mp4` 是最小合法结构：ftyp、mdat（30 个 64 字节 sample）、moov 在文件尾部。
moov 里有 mvhd（timescale 1000，duration 3000）、一个视频 trak、stbl 里带 stsd、stts、stss、
stsz、stco。
