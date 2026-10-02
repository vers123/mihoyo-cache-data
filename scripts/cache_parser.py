"""Chromium Blockfile Disk Cache 解析器。

解析流程：
1. 解析 index 文件 → entry 地址列表
2. 读取每个 entry 的块 → 解析 data_size/data_addr
3. 从 data_addr[2] 读取 HTTP headers
4. 从 data_addr[3] 获取 body 文件 (f_*)
"""

from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BLOCKFILE_HEADER_SIZE = 8192


@dataclass
class CacheEntry:
    url: str
    content_type: str = ""
    content_length: int = 0
    last_modified: str = ""
    etag: str = ""
    body_file: str = ""
    headers: Dict[str, str] = field(default_factory=dict)


def _read_index(cache_dir: Path) -> List[int]:
    """解析 index 文件，返回 entry 地址列表。"""
    idx_path = cache_dir / "index"
    if not idx_path.exists():
        return []

    with open(idx_path, "rb") as f:
        idx = f.read()

    # IndexHeader: 256 bytes, then IndexTable at offset 256
    # IndexTable 每条 4 字节，前 112 字节是 flags/lru 等
    table_offset = 256 + 112
    addrs = []
    for i in range((len(idx) - table_offset) // 4):
        addr = struct.unpack_from("<I", idx, table_offset + i * 4)[0]
        if addr == 0:
            continue
        if not (addr >> 31) & 1:
            continue
        ftype = (addr >> 28) & 7
        if ftype in (2, 3, 4):  # entry blocks
            addrs.append(addr)
    return addrs


def _get_block_size(cache_dir: Path, data_file: int) -> int:
    """获取 data_# 文件的块大小。"""
    p = cache_dir / f"data_{data_file}"
    if not p.exists():
        return 0
    with open(p, "rb") as f:
        return struct.unpack("<I", f.read(16)[12:16])[0]


def _read_block_chain(cache_dir: Path, addr: int) -> bytes:
    """读取块链（如果有 next 指针则拼接多个块）。"""
    ftype = (addr >> 28) & 7
    dfile = (addr >> 16) & 0xFF
    block = addr & 0xFFFF
    bsize = _get_block_size(cache_dir, dfile)
    if bsize == 0:
        return b""

    p = cache_dir / f"data_{dfile}"
    with open(p, "rb") as f:
        data = f.read()

    result = b""
    visited = set()
    cur_block = block
    while cur_block not in visited:
        visited.add(cur_block)
        bstart = BLOCKFILE_HEADER_SIZE + cur_block * bsize
        if bstart + bsize > len(data):
            break
        bdata = data[bstart:bstart + bsize]
        result += bdata
        next_ptr = struct.unpack_from("<I", bdata, 0)[0]
        if next_ptr == 0:
            break
        ntype = (next_ptr >> 28) & 7
        if ntype != ftype:
            break
        cur_block = next_ptr & 0xFFFF

    return result


def _parse_http_headers(data: bytes) -> Dict[str, str]:
    """从 \x00 分隔的块中解析 HTTP headers。"""
    headers: Dict[str, str] = {}
    # 找 HTTP/ 起始位置
    http_pos = data.find(b"HTTP/")
    if http_pos < 0:
        return headers

    block = data[http_pos:http_pos + 8192]
    parts = block.split(b"\x00")
    for part in parts[1:]:
        if not part:
            break
        if b":" in part:
            k, v = part.split(b":", 1)
            headers[k.strip().lower().decode("ascii", errors="replace")] = \
                v.strip().decode("ascii", errors="replace")
        else:
            break
    return headers


def _find_url_in_block(block: bytes) -> str:
    """在块中查找 URL（key 字段）。"""
    for prefix in (b"1/0/https://", b"1/1/https://", b"0/0/https://", b"https://", b"http://"):
        pos = block.find(prefix)
        if pos >= 0:
            end = block.find(b"\x00", pos)
            if end < 0:
                end = min(pos + 500, len(block))
            url = block[pos:end].decode("utf-8", errors="replace")
            url = re.sub(r"^\d+/\d+/", "", url)
            return url
    return ""


def _addr_to_filename(addr: int) -> str:
    """将 cache 地址转换为 f_* 文件名。"""
    file_num = addr & 0x00FFFFFF
    return f"f_{file_num:06x}"


def parse_cache(cache_dir: str | Path) -> List[CacheEntry]:
    """解析缓存目录，返回所有缓存条目。"""
    cache_dir = Path(cache_dir)
    entry_addrs = _read_index(cache_dir)

    entries: List[CacheEntry] = []
    for addr in entry_addrs:
        block = _read_block_chain(cache_dir, addr)
        if len(block) < 64:
            continue

        # EntryStore 从块偏移 0 开始
        data_sizes = struct.unpack_from("<iiii", block, 32)
        data_addrs = struct.unpack_from("<IIII", block, 48)

        # 找 URL
        url = _find_url_in_block(block)
        if not url:
            continue

        # 读取 HTTP headers (stream 2)
        headers: Dict[str, str] = {}
        header_addr = data_addrs[2]
        if header_addr and (header_addr >> 31) & 1:
            htype = (header_addr >> 28) & 7
            if htype in (1, 2, 3, 4):
                header_data = _read_block_chain(cache_dir, header_addr)
                headers = _parse_http_headers(header_data)

        # 获取 body 文件 (stream 3)
        body_file = ""
        body_addr = data_addrs[3]
        if body_addr and (body_addr >> 31) & 1:
            btype = (body_addr >> 28) & 7
            if btype == 0:  # external file
                body_file = _addr_to_filename(body_addr)
                # 检查文件是否存在
                if not (cache_dir / body_file).exists():
                    body_file = ""

        try:
            cl = int(headers.get("content-length", "0"))
        except ValueError:
            cl = data_sizes[3] if data_sizes[3] > 0 else 0

        entry = CacheEntry(
            url=url,
            content_type=headers.get("content-type", ""),
            content_length=cl,
            last_modified=headers.get("last-modified", ""),
            etag=headers.get("etag", ""),
            body_file=body_file,
            headers=headers,
        )
        entries.append(entry)

    return entries


def url_to_filename(url: str) -> str:
    url = url.split("?")[0]
    # 去掉视频分片后缀 :hash:N
    url = re.sub(r":[0-9a-f]+:[0-9a-f]+$", "", url, flags=re.IGNORECASE)
    name = url.rsplit("/", 1)[-1]
    name = re.sub(r":\w+$", "", name)
    return name


def classify_by_url(url: str, content_type: str, file_size: int = 0) -> str:
    url_lower = url.lower()
    ct_lower = content_type.lower()
    # 去掉视频分片后缀再判断扩展名
    url_no_seg = re.sub(r":[0-9a-f]+:[0-9a-f]+$", "", url_lower, flags=re.IGNORECASE)
    if "video" in ct_lower or url_no_seg.endswith((".webm", ".mp4")):
        return "video"
    if "image/png" in ct_lower or url_no_seg.endswith(".png"):
        return "image/png"
    if "image/jpeg" in ct_lower or url_no_seg.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if "image/webp" in ct_lower or url_no_seg.endswith(".webp"):
        return "image/webp"
    if "gif" in ct_lower or url_no_seg.endswith(".gif"):
        return "image/gif"
    if "json" in ct_lower or url_no_seg.endswith(".json"):
        return "json"
    return "unknown"


def is_video_segment(url: str) -> bool:
    return bool(re.search(r":[0-9a-f]+:[0-9a-f]+$", url, re.IGNORECASE))


def get_video_base_url(url: str) -> str:
    return re.sub(r":[0-9a-f]+:[0-9a-f]+$", "", url, flags=re.IGNORECASE)


def get_seg_index(url: str) -> int:
    m = re.search(r":([0-9a-f]+):([0-9a-f]+)$", url, re.IGNORECASE)
    return int(m.group(2), 16) if m else 0
