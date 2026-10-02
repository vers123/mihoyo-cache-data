#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_cache.py - 从 miHoYo/HYP (米哈游启动器) Chromium 缓存目录中提取并分类资源文件。

工作原理：
    扫描 Cache_Data 目录下的所有文件，读取每个文件的 magic bytes（文件头签名），
    识别其真实类型（PNG/JPEG/WEBP/WebM/JSON/证书/缓存元数据等），
    然后按类型复制到 Cache_Sorted/ 下对应的子目录，并补上正确的扩展名。

路径检测优先级：
    1. 命令行参数 --input 指定的路径
    2. 项目根目录下的 Cache_Data/
    3. %APPDATA%\\miHoYo\\HYP\\1_1\\fedata\\Cache\\Cache_Data
    4. 交互式提示用户输入

用法：
    python scripts/extract_cache.py
    python scripts/extract_cache.py --input "C:\\path\\to\\Cache_Data"
    python scripts/extract_cache.py --output "D:\\output\\Cache_Sorted"
    python scripts/extract_cache.py --input "..." --output "..." --force
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# 类型定义与签名表
# ---------------------------------------------------------------------------

# 每个条目: (类型名, 子目录, 扩展名, 检测函数)
# 检测函数接收文件前 N 字节 (bytes)，返回是否匹配
SIGNATURES = [
    # 图像
    ("png", "images/png", ".png", lambda b: b.startswith(b"\x89PNG\r\n\x1a\n")),
    ("jpeg", "images/jpg", ".jpg", lambda b: b.startswith(b"\xff\xd8\xff")),
    ("webp", "images/webp", ".webp",
        lambda b: b.startswith(b"RIFF") and len(b) >= 12 and b[8:12] == b"WEBP"),
    # 视频 (WebM / Matroska EBML)
    ("webm", "video", ".webm", lambda b: b.startswith(b"\x1a\x45\xdf\xa3")),
    # 证书 (ASN.1 DER: SEQUENCE 标签 0x30 + 长度)
    ("certificate", "certificate", ".cer",
        lambda b: len(b) >= 2 and b[0] == 0x30 and b[1] in (0x81, 0x82, 0x83, 0x84)),
]

# Chromium 缓存元数据文件名
CACHE_META_NAMES = {"index"} | {f"data_{i}" for i in range(256)}

# 未知文件使用的扩展名
UNKNOWN_EXT = ".bin"


# ---------------------------------------------------------------------------
# 核心逻辑
# ---------------------------------------------------------------------------

def detect_type(data: bytes, filename: str, file_size: int = 0) -> Tuple[str, str, str]:
    """根据文件头、文件名和文件大小识别类型。

    返回: (类型名, 相对子目录, 扩展名)
    """
    # 1. 优先匹配 magic bytes 签名
    for type_name, subdir, ext, checker in SIGNATURES:
        try:
            if checker(data):
                return type_name, subdir, ext
        except Exception:
            continue

    # 2. Chromium 缓存元数据 (index / data_0..data_n)
    if filename in CACHE_META_NAMES:
        return "cache_meta", "cache_meta", ""

    # 3. JSON 文本（严格校验，避免把以 '{' 开头的二进制分片误判为 JSON）
    text = data.lstrip()
    if text[:1] in (b"{", b"["):
        try:
            json.loads(data.decode("utf-8", errors="strict"))
            return "json", "json", ".json"
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass

    # 4. 视频分片：Chromium Simple Cache 中超过 1MB 的资源会被切成 1MB 的块，
    #    续分片不含 EBML 头，但文件大小恒为 1048576 字节。
    if file_size == 1048576:
        return "webm", "video", ".webm"

    # 5. 无法识别
    return "unknown", "unknown", UNKNOWN_EXT


def read_head(path: Path, size: int = 64) -> bytes:
    """读取文件前 size 字节用于签名检测。"""
    with path.open("rb") as f:
        return f.read(size)


def classify_and_copy(
    src: Path,
    output_root: Path,
    force: bool = False,
) -> Tuple[str, str]:
    """分类单个文件并复制到目标目录。

    返回: (类型名, 目标相对路径)
    """
    head = read_head(src)
    type_name, subdir, ext = detect_type(head, src.name, file_size=src.stat().st_size)

    dest_dir = output_root / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 目标文件名：保留原名 + 扩展名（cache_meta 不加扩展名）
    dest_name = src.name + ext if ext else src.name
    dest = dest_dir / dest_name

    if dest.exists() and not force:
        # 已存在且非强制，跳过
        return type_name, str(dest.relative_to(output_root))

    shutil.copy2(src, dest)
    return type_name, str(dest.relative_to(output_root))


def iter_cache_files(input_dir: Path):
    """遍历缓存目录中的普通文件（排除子目录）。"""
    for entry in input_dir.iterdir():
        if entry.is_file():
            yield entry


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------

def project_root() -> Path:
    """返回项目根目录（scripts/ 的上一级）。"""
    return Path(__file__).resolve().parent.parent


def default_cache_dir() -> Optional[Path]:
    """返回默认的 miHoYo HYP 缓存目录（如不存在则返回 None）。"""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    candidate = Path(appdata) / "miHoYo" / "HYP" / "1_1" / "fedata" / "Cache" / "Cache_Data"
    return candidate if candidate.is_dir() else None


def resolve_input_dir(cli_input: Optional[str]) -> Path:
    """按优先级解析输入目录。"""
    # 1. 命令行参数
    if cli_input:
        p = Path(cli_input).expanduser().resolve()
        if p.is_dir():
            return p
        print(f"[错误] 指定的输入路径不存在或不是目录: {p}", file=sys.stderr)
        sys.exit(1)

    # 2. 项目根 Cache_Data
    local = project_root() / "Cache_Data"
    if local.is_dir():
        return local

    # 3. 系统默认缓存路径
    sys_default = default_cache_dir()
    if sys_default:
        return sys_default

    # 4. 交互式提示
    print("未自动找到缓存目录，请输入 miHoYo/HYP Cache_Data 的完整路径：")
    try:
        user = input("> ").strip().strip('"').strip("'")
    except (EOFError, KeyboardInterrupt):
        print("\n[错误] 未提供输入路径，已取消。", file=sys.stderr)
        sys.exit(1)
    p = Path(user).expanduser().resolve()
    if not p.is_dir():
        print(f"[错误] 路径无效: {p}", file=sys.stderr)
        sys.exit(1)
    return p


def resolve_output_dir(cli_output: Optional[str]) -> Path:
    """解析输出目录，默认为项目根 Cache_Sorted。"""
    if cli_output:
        return Path(cli_output).expanduser().resolve()
    return project_root() / "Cache_Sorted"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="从 miHoYo HYP 缓存目录提取并分类资源文件。"
    )
    parser.add_argument(
        "--input", "-i",
        help="Cache_Data 目录路径（默认自动检测）。",
    )
    parser.add_argument(
        "--output", "-o",
        help="分类结果输出目录（默认: 项目根/Cache_Sorted）。",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="覆盖已存在的目标文件。",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="减少输出，只打印汇总。",
    )
    args = parser.parse_args()

    input_dir = resolve_input_dir(args.input)
    output_dir = resolve_output_dir(args.output)

    print(f"输入目录: {input_dir}")
    print(f"输出目录: {output_dir}")
    print("-" * 60)

    files = sorted(iter_cache_files(input_dir))
    total = len(files)
    if total == 0:
        print("[警告] 输入目录中没有文件。")
        return 0

    counts: dict[str, int] = {}
    for i, src in enumerate(files, 1):
        type_name, rel = classify_and_copy(src, output_dir, force=args.force)
        counts[type_name] = counts.get(type_name, 0) + 1
        if not args.quiet:
            print(f"[{i}/{total}] {type_name:>12}  {src.name}  ->  {rel}")

    print("-" * 60)
    print("分类汇总:")
    for name in sorted(counts):
        print(f"  {name:>12}: {counts[name]}")
    print(f"  {'合计':>12}: {total}")
    print("完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
