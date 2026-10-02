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
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Callable, Optional, Tuple

try:
    from tqdm import tqdm
    _HAS_TQDM = True
except ImportError:
    _HAS_TQDM = False


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
    ("gif", "images/gif", ".gif",
        lambda b: b.startswith(b"GIF87a") or b.startswith(b"GIF89a")),
    ("bmp", "images/bmp", ".bmp", lambda b: b.startswith(b"BM")),
    ("ico", "images/ico", ".ico",
        lambda b: len(b) >= 4 and b[:2] == b"\x00\x00" and b[2:4] == b"\x01\x00"),
    ("tiff", "images/tiff", ".tiff",
        lambda b: b.startswith(b"II\x2a\x00") or b.startswith(b"MM\x00\x2a")),
    # 视频 (WebM / Matroska EBML)
    ("webm", "video", ".webm", lambda b: b.startswith(b"\x1a\x45\xdf\xa3")),
    ("mp4", "video", ".mp4",
        lambda b: len(b) >= 12 and b[4:8] == b"ftyp"),
    ("ogg", "audio", ".ogg", lambda b: b.startswith(b"OggS")),
    ("wav", "audio", ".wav",
        lambda b: b.startswith(b"RIFF") and len(b) >= 12 and b[8:12] == b"WAVE"),
    ("mp3", "audio", ".mp3",
        lambda b: b.startswith(b"ID3") or (len(b) >= 2 and b[0] == 0xFF and (b[1] & 0xE0) == 0xE0)),
    # 文档 / 压缩
    ("zip", "archive", ".zip", lambda b: b.startswith(b"PK\x03\x04")),
    ("pdf", "document", ".pdf", lambda b: b.startswith(b"%PDF")),
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
    #    （必须在文本检测之前，避免二进制分片被误判为文本）
    if file_size == 1048576:
        return "webm", "video", ".webm"

    # 5. 文本文件：不含 null 字节且可打印字符占比高
    if file_size > 0 and _is_text(data, file_size):
        return "text", "text", ".txt"

    # 6. 无法识别
    return "unknown", "unknown", UNKNOWN_EXT


def _is_text(data: bytes, file_size: int, sample_size: int = 8192) -> bool:
    """判断文件是否为文本。

    条件：
        - 采样范围内不含 null 字节 (0x00)
        - 可打印字符（ASCII 可打印 + 空白 + UTF-8 多字节）占比 > 90%
    """
    sample = data[:sample_size] if len(data) >= sample_size else data
    if not sample:
        return False
    # 文本文件通常不含 null 字节
    if b"\x00" in sample:
        return False
    printable = 0
    for byte in sample:
        if 0x20 <= byte <= 0x7E or byte in (0x09, 0x0A, 0x0D):
            printable += 1
        elif byte >= 0x80:
            printable += 1
    ratio = printable / len(sample)
    return ratio > 0.90


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


# ---------------------------------------------------------------------------
# 文件校验
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 文件遍历
# ---------------------------------------------------------------------------

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
# 核心提取流程
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[int, int, str, str], None]


def _move_to_invalid(dest: Path, output_root: Path, reason: str) -> Path:
    """将校验失败的文件移动到 output_root/invalid/ 目录。

    返回移动后的目标路径。
    """
    invalid_dir = output_root / "invalid"
    invalid_dir.mkdir(parents=True, exist_ok=True)
    # 保留原文件名，附加原因后缀（取简短标识）
    short_reason = reason.split(":")[0].replace(" ", "_")[:30]
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


def extract(
    input_dir: Path,
    output_root: Path,
    force: bool = False,
    progress: Optional[ProgressCallback] = None,
    do_verify: bool = False,
    do_verify_content: bool = False,
    report_path: Optional[Path] = None,
) -> dict[str, int]:
    """扫描 input_dir 并分类复制到 output_root。

    参数:
        input_dir:        缓存目录 (Cache_Data)
        output_root:      分类结果根目录 (Cache_Sorted)
        force:            是否覆盖已存在的目标文件
        progress:         进度回调 callback(current, total, type_name, filename)
        do_verify:        复制后校验完整性（大小 + SHA256）
        do_verify_content: 校验文件内容结构是否符合类型
        report_path:      校验报告输出路径（JSON），为 None 则不生成报告

    返回:
        各类型计数 dict，如 {"png": 86, "jpeg": 75, ...}
    """
    files = sorted(iter_cache_files(input_dir))
    total = len(files)
    counts: dict[str, int] = {}
    report_entries: list[dict] = []
    invalid_count = 0

    for i, src in enumerate(files, 1):
        type_name, rel = classify_and_copy(src, output_root, force=force)
        dest = output_root / rel
        entry = {
            "source": src.name,
            "type": type_name,
            "dest": rel,
            "size": src.stat().st_size,
            "verify_copy": None,
            "verify_content": None,
            "invalid": False,
            "reason": "",
        }

        # 复制完整性校验
        if do_verify:
            ok, reason = verify_copy(src, dest)
            entry["verify_copy"] = ok
            if not ok:
                entry["invalid"] = True
                entry["reason"] = f"copy: {reason}"
                _move_to_invalid(dest, output_root, reason)
                invalid_count += 1

        # 内容有效性校验
        if do_verify_content and not entry["invalid"]:
            ok, reason = verify_content(dest, type_name, file_size=entry["size"])
            entry["verify_content"] = ok
            if not ok:
                entry["invalid"] = True
                entry["reason"] = f"content: {reason}"
                _move_to_invalid(dest, output_root, reason)
                invalid_count += 1

        counts[type_name] = counts.get(type_name, 0) + 1
        report_entries.append(entry)

        if progress is not None:
            display = type_name if not entry["invalid"] else "invalid"
            progress(i, total, display, src.name)

    # 生成校验报告
    if report_path is not None:
        report = {
            "total": total,
            "invalid": invalid_count,
            "by_type": counts,
            "files": report_entries,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    return counts


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="从 miHoYo HYP 缓存目录提取并分类资源文件。"
    )
    parser.add_argument("--input", "-i", help="Cache_Data 目录路径（默认自动检测）。")
    parser.add_argument("--output", "-o", help="分类结果输出目录（默认: 项目根/Cache_Sorted）。")
    parser.add_argument("--force", "-f", action="store_true", help="覆盖已存在的目标文件。")
    parser.add_argument("--quiet", "-q", action="store_true", help="减少输出，只打印汇总。")
    parser.add_argument("--verify", action="store_true",
                        help="复制后校验完整性（大小 + SHA256 哈希）。")
    parser.add_argument("--verify-content", action="store_true",
                        help="校验文件内容结构是否符合判定类型。")
    parser.add_argument("--report", nargs="?", const="report.json", default=None,
                        help="生成校验报告（JSON），默认输出到 report.json。")
    args = parser.parse_args()

    input_dir = resolve_input_dir(args.input)
    output_dir = resolve_output_dir(args.output)

    print(f"输入目录: {input_dir}")
    print(f"输出目录: {output_dir}")
    if args.verify or args.verify_content:
        print(f"校验: 完整性={'开' if args.verify else '关'}  "
              f"内容={'开' if args.verify_content else '关'}")
    print("-" * 60)

    files = list(iter_cache_files(input_dir))
    total = len(files)
    if total == 0:
        print("[警告] 输入目录中没有文件。")
        return 0

    report_path = Path(args.report).resolve() if args.report else None

    # 根据是否安装 tqdm 选择进度展示方式
    if _HAS_TQDM:
        pbar = tqdm(total=total, desc="分类中", unit="file", ncols=80)

        def _progress(current: int, _total: int, type_name: str, filename: str) -> None:
            pbar.update(1)
            pbar.set_postfix_str(f"{type_name}")
            if not args.quiet:
                tqdm.write(f"  {type_name:>12}  {filename}")

        counts = extract(
            input_dir, output_dir, force=args.force, progress=_progress,
            do_verify=args.verify, do_verify_content=args.verify_content,
            report_path=report_path,
        )
        pbar.close()
    else:
        def _progress(current: int, total: int, type_name: str, filename: str) -> None:
            if not args.quiet:
                print(f"[{current}/{total}] {type_name:>12}  {filename}")

        counts = extract(
            input_dir, output_dir, force=args.force, progress=_progress,
            do_verify=args.verify, do_verify_content=args.verify_content,
            report_path=report_path,
        )

    print("-" * 60)
    print("分类汇总:")
    for name in sorted(counts):
        print(f"  {name:>12}: {counts[name]}")
    print(f"  {'合计':>12}: {total}")
    if report_path:
        print(f"校验报告: {report_path}")
    print("完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
