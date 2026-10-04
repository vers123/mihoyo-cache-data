#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WebM 视频分片处理模块：分片合并、完整性检测、不完整视频修复。

修复策略：
    从某个完整视频借用 WebM 头部（Info + Tracks），
    在不完整视频的分片中找到第一个有效 VP9 关键帧，
    从该 Cluster 开始拼接，输出到 video_repaired/。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from cache_parser import (
    CacheEntry,
    get_seg_index,
    get_video_base_url,
    is_video_segment,
    url_to_filename,
)


# ---------------------------------------------------------------------------
# ffprobe 可播放性检测
# ---------------------------------------------------------------------------

def ffprobe_playable(path: Path) -> bool:
    """用 ffprobe 检测视频文件是否可播放。ffprobe 不可用时返回 True（跳过校验）。"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return True


# ---------------------------------------------------------------------------
# 分片完整性检测
# ---------------------------------------------------------------------------

def check_segments_complete(segs: List[CacheEntry]) -> Tuple[bool, str]:
    """检查视频分片是否完整（包含 index 0 且序号连续）。

    返回: (是否完整, 不完整原因)
    """
    indices = sorted(get_seg_index(s.url) for s in segs)
    if not indices:
        return False, "no segments"
    if indices[0] != 0:
        return False, f"missing first segment (starts at index {indices[0]})"
    expected = set(range(indices[0], indices[-1] + 1))
    actual = set(indices)
    missing = expected - actual
    if missing:
        return False, f"missing segments: {sorted(missing)}"
    return True, ""


# ---------------------------------------------------------------------------
# EBML / WebM 结构解析
# ---------------------------------------------------------------------------

def _parse_ebml_vint(data: bytes, pos: int) -> Tuple[int, int]:
    """解析 EBML VINT，返回 (值, 字节数)。"""
    b = data[pos]
    for i in range(8):
        if b & (0x80 >> i):
            length = i + 1
            mask = (0xFF >> (i + 1)) & 0xFF
            val = b & mask
            for j in range(1, length):
                val = (val << 8) | data[pos + j]
            return val, length
    return 0, 1


def _parse_ebml_elem_id(data: bytes, pos: int) -> Tuple[bytes, int]:
    """解析 EBML 元素 ID，返回 (ID, 字节数)。"""
    b = data[pos]
    for i in range(4):
        if b & (0x80 >> i):
            return data[pos:pos + i + 1], i + 1
    return data[pos:pos + 1], 1


def extract_webm_header(first_seg_data: bytes) -> bytes:
    """从完整视频的首个分片中提取最小 WebM 头部。

    返回: EBML + Segment(unknown size) + Info + Tracks，不含 SeekHead 和 Cluster。
    """
    ebml_id = b"\x1a\x45\xdf\xa3"
    seg_id = b"\x18\x53\x80\x67"
    info_id = b"\x15\x49\xa9\x66"
    tracks_id = b"\x16\x54\xae\x6b"
    cluster_id = b"\x1f\x43\xb6\x75"

    ebml_pos = first_seg_data.find(ebml_id)
    ebml_size, ebml_slen = _parse_ebml_vint(first_seg_data, ebml_pos + 4)
    ebml_header = first_seg_data[:ebml_pos + 4 + ebml_slen + ebml_size]

    seg_pos = first_seg_data.find(seg_id)
    _, seg_slen = _parse_ebml_vint(first_seg_data, seg_pos + 4)
    seg_content_start = seg_pos + 4 + seg_slen

    info_element = None
    tracks_element = None
    pos = seg_content_start
    while pos < len(first_seg_data):
        elem_id, id_len = _parse_ebml_elem_id(first_seg_data, pos)
        if elem_id == cluster_id:
            break
        elem_size, size_len = _parse_ebml_vint(first_seg_data, pos + id_len)
        elem_end = pos + id_len + size_len + elem_size
        if elem_id == info_id:
            info_element = first_seg_data[pos:elem_end]
        elif elem_id == tracks_id:
            tracks_element = first_seg_data[pos:elem_end]
        pos = elem_end

    if not info_element or not tracks_element:
        return b""

    # Segment size 设为 unknown，让播放器读到文件末尾
    segment_header = seg_id + b"\x01\xff\xff\xff\xff\xff\xff\xff"
    return ebml_header + segment_header + info_element + tracks_element


def find_valid_vp9_keyframe_cluster(data: bytes) -> Optional[int]:
    """在数据中查找第一个包含有效 VP9 关键帧的 Cluster 偏移量。

    VP9 帧标记为前 2 bit = 0b10。返回 Cluster 起始偏移，无则返回 None。
    """
    cluster_id = b"\x1f\x43\xb6\x75"
    pos = 0
    while True:
        cpos = data.find(cluster_id, pos)
        if cpos < 0:
            return None
        size, size_len = _parse_ebml_vint(data, cpos + 4)
        content_start = cpos + 4 + size_len
        content_end = min(content_start + size, len(data))

        cpos2 = content_start
        while cpos2 < content_end:
            child_id, child_id_len = _parse_ebml_elem_id(data, cpos2)
            child_size, child_size_len = _parse_ebml_vint(data, cpos2 + child_id_len)
            child_content = cpos2 + child_id_len + child_size_len

            if child_id == b"\xa3":  # SimpleBlock
                tn, tn_len = _parse_ebml_vint(data, child_content)
                flags_pos = child_content + tn_len + 2
                if flags_pos < len(data):
                    flags = data[flags_pos]
                    if flags & 0x80:  # 关键帧
                        frame_pos = flags_pos + 1
                        if frame_pos < len(data) and (data[frame_pos] >> 6) == 0b10:
                            return cpos
            cpos2 = child_content + child_size
        pos = cpos + 1


# ---------------------------------------------------------------------------
# 分组辅助
# ---------------------------------------------------------------------------

def group_video_segments(entries: List[CacheEntry]) -> Dict[str, List[CacheEntry]]:
    """按 base URL 对视频分片分组。"""
    groups: Dict[str, List[CacheEntry]] = {}
    for e in entries:
        if is_video_segment(e.url) and e.body_file:
            base = get_video_base_url(e.url)
            groups.setdefault(base, []).append(e)
    for segs in groups.values():
        segs.sort(key=lambda s: get_seg_index(s.url))
    return groups


# ---------------------------------------------------------------------------
# 不完整视频修复
# ---------------------------------------------------------------------------

def repair_incomplete_videos(
    entries: List[CacheEntry],
    input_dir: Path,
    output_root: Path,
    force: bool = False,
) -> List[Tuple[str, str]]:
    """尝试修复不完整的视频分片。

    策略：从某个完整视频借用 WebM 头部（Info + Tracks），
    然后在不完整视频的分片中找到第一个有效 VP9 关键帧，
    从该 Cluster 开始拼接，输出到 video_repaired/。

    返回: [(type_name, dest_rel_path), ...]
    """
    results: List[Tuple[str, str]] = []
    repaired_dir = output_root / "video_repaired"
    repaired_dir.mkdir(parents=True, exist_ok=True)

    groups = group_video_segments(entries)
    if not groups:
        return results

    # 找一个完整视频提取头部
    webm_header = b""
    for base, segs in groups.items():
        indices = [get_seg_index(s.url) for s in segs]
        if indices and indices[0] == 0 and segs[0].body_file:
            with open(input_dir / segs[0].body_file, "rb") as f:
                webm_header = extract_webm_header(f.read())
            if webm_header:
                break

    if not webm_header:
        return results

    for base, segs in groups.items():
        segs_with_body = [s for s in segs if s.body_file]
        if not segs_with_body:
            continue

        # 只处理不完整的视频
        complete, _ = check_segments_complete(segs_with_body)
        if complete:
            continue

        # 拼接所有分片数据
        all_data = b""
        for s in segs_with_body:
            src = input_dir / s.body_file
            if src.exists():
                with open(src, "rb") as f:
                    all_data += f.read()

        # 查找有效 VP9 关键帧的 Cluster
        start = find_valid_vp9_keyframe_cluster(all_data)
        if start is None:
            continue  # 无可修复的关键帧

        out_name = url_to_filename(base)
        if not out_name.endswith(".webm"):
            out_name += ".webm"
        dest = repaired_dir / out_name

        if dest.exists() and not force:
            results.append(("video_repaired", str(dest.relative_to(output_root))))
            continue

        repaired = webm_header + all_data[start:]
        with open(dest, "wb") as f:
            f.write(repaired)

        # ffprobe 校验
        if not ffprobe_playable(dest):
            dest.unlink(missing_ok=True)
            continue

        results.append(("video_repaired", str(dest.relative_to(output_root))))

    return results
