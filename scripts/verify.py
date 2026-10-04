#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文件校验模块：SHA256 哈希、复制完整性、内容结构校验。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Tuple


def sha256_file(path: Path, chunk_size: int = 65536) -> str:
    """计算文件的 SHA256 哈希。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def verify_copy(src: Path, dst: Path) -> Tuple[bool, str]:
    """校验复制完整性：文件大小 + SHA256 哈希一致。

    返回: (是否通过, 失败原因)
    """
    src_size = src.stat().st_size
    dst_size = dst.stat().st_size
    if src_size != dst_size:
        return False, f"size mismatch: src={src_size} dst={dst_size}"
    if sha256_file(src) != sha256_file(dst):
        return False, "sha256 mismatch"
    return True, ""


def verify_content(path: Path, type_name: str, file_size: int = 0) -> Tuple[bool, str]:
    """校验文件内容结构是否符合其类型。

    参数:
        path:       文件路径
        type_name:  判定的类型名
        file_size:  文件大小（用于跳过 1MB 分片的 EBML 校验）

    返回: (是否通过, 失败原因)
    """
    try:
        size = file_size or path.stat().st_size
        if size == 0:
            return False, "empty file"

        with path.open("rb") as f:
            head = f.read(64)

        if type_name == "png":
            if not head.startswith(b"\x89PNG\r\n\x1a\n"):
                return False, "bad png signature"
            if len(head) < 24:
                return False, "truncated png (no IHDR)"
            if head[12:16] != b"IHDR":
                return False, "missing IHDR chunk"

        elif type_name == "jpeg":
            if not head.startswith(b"\xff\xd8\xff"):
                return False, "bad jpeg signature"
            with path.open("rb") as f:
                f.seek(-2, 2)
                tail = f.read(2)
            if tail != b"\xff\xd9":
                return False, "missing jpeg EOI marker"

        elif type_name == "webp":
            if not (head.startswith(b"RIFF") and len(head) >= 12 and head[8:12] == b"WEBP"):
                return False, "bad webp signature"

        elif type_name == "webm":
            # 1MB 视频分片不含 EBML 头，跳过签名校验
            if size != 1048576 and not head.startswith(b"\x1a\x45\xdf\xa3"):
                return False, "bad ebml signature"

        elif type_name == "gif":
            if not (head.startswith(b"GIF87a") or head.startswith(b"GIF89a")):
                return False, "bad gif signature"

        elif type_name == "bmp":
            if not head.startswith(b"BM"):
                return False, "bad bmp signature"

        elif type_name == "mp4":
            if len(head) < 12 or head[4:8] != b"ftyp":
                return False, "missing ftyp box"

        elif type_name == "certificate":
            if not (len(head) >= 2 and head[0] == 0x30
                    and head[1] in (0x81, 0x82, 0x83, 0x84)):
                return False, "bad der certificate structure"

        elif type_name == "zip":
            if not head.startswith(b"PK\x03\x04"):
                return False, "bad zip signature"

        elif type_name == "pdf":
            if not head.startswith(b"%PDF"):
                return False, "bad pdf signature"

        elif type_name in ("json", "text"):
            # 文本类：校验不含 null 字节即可（编码可能是 GBK/UTF-8 等）
            with path.open("rb") as f:
                if b"\x00" in f.read(65536):
                    return False, "contains null bytes"

        # cache_meta / unknown 等不做结构校验
        return True, ""

    except OSError as e:
        return False, f"read error: {e}"


def move_to_invalid(dest: Path, output_root: Path, reason: str) -> Path:
    """将校验失败的文件移动到 output_root/invalid/ 目录。

    返回移动后的目标路径。
    """
    invalid_dir = output_root / "invalid"
    invalid_dir.mkdir(parents=True, exist_ok=True)
    target = invalid_dir / f"{dest.name}"
    # 避免重名
    counter = 1
    base = target.stem
    suffix = target.suffix
    while target.exists():
        target = invalid_dir / f"{base}_{counter}{suffix}"
        counter += 1
    dest.rename(target)
    return target
