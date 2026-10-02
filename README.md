# mihoyo-cache-data

从米哈游启动器（miHoYo Launcher / HYP）的 Chromium 缓存目录中提取并分类的资源数据集。

## 数据来源

- 原始缓存路径：`%APPDATA%\miHoYo\HYP\1_1\fedata\Cache\Cache_Data`
- 启动器基于 Chromium Embedded Framework，其 Simple Cache 机制将网络资源以无扩展名文件（`f_xxxxxx`、`data_0`~`data_3`、`index`）形式存储在 `Cache_Data/` 中。
- 本仓库通过 magic bytes 识别每个缓存文件的真实类型，并复制到 `Cache_Sorted/` 下对应的子目录。

> 原始 `Cache_Data/` 不纳入版本控制（见 `.gitignore`），仓库只保留分类后的结果。

## 目录结构

```
mihoyo-cache-data/
├── Cache_Sorted/              # 分类后的资源
│   ├── cache_meta/            # Chromium 缓存元数据 (data_0~3, index)
│   ├── certificate/           # ASN.1 DER 证书
│   ├── images/
│   │   ├── jpg/               # JPEG 图片
│   │   ├── png/               # PNG 图片
│   │   └── webp/              # WebP 图片
│   ├── unknown/               # 无法识别的文件
│   └── video/                 # WebM 视频（含 EBML 头及 1MB 分片）
├── scripts/
│   ├── extract_cache.py       # 提取与分类脚本（CLI）
│   └── gui.py                 # 图形界面（PySide6）
├── requirements.txt
├── .github/workflows/release.yml
├── .gitignore
├── LICENSE
└── README.md
```

## 分类统计

| 类型        | 数量 |
| ----------- | ---- |
| PNG         | 86   |
| JPEG        | 75   |
| WebP        | 55   |
| WebM 视频   | 141  |
| 证书        | 1    |
| 缓存元数据  | 5    |
| 未知        | 11   |
| **合计**    | 374  |

## 使用方法

### 环境准备

GUI 模式依赖 `tqdm`（进度条）和 `PySide6`（界面框架），建议使用虚拟环境：

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

> CLI 模式（`extract_cache.py`）仅依赖标准库，可不安装依赖直接运行；
> 若未安装 `tqdm`，会自动退化为普通逐行输出。

### 图形界面（推荐）

```bash
python scripts/gui.py
```

功能：
- 输入/输出路径选择（浏览按钮 + 文件夹拖拽）
- 实时进度条与日志输出
- 分类统计表格
- 一键打开输出目录
- 主题切换（Fusion / 系统原生 / 亮色 / 暗色）
- 多语言（中文 / English）
- 覆盖已存在文件、静默模式选项
- 后台线程执行，不阻塞 UI

### 命令行模式

确保本地存在 miHoYo/HYP 的 `Cache_Data` 目录，然后运行：

```bash
python scripts/extract_cache.py
```

脚本会按以下优先级查找输入目录：

1. 命令行参数 `--input` 指定的路径
2. 项目根目录下的 `Cache_Data/`
3. `%APPDATA%\miHoYo\HYP\1_1\fedata\Cache\Cache_Data`
4. 若以上均不存在，交互式提示用户输入

### 命令行参数

```bash
python scripts/extract_cache.py --input "C:\path\to\Cache_Data" --output "D:\output\Cache_Sorted" --force
```

| 参数           | 说明                                   |
| -------------- | -------------------------------------- |
| `-i, --input`  | 指定 Cache_Data 目录路径               |
| `-o, --output` | 指定分类结果输出目录（默认 Cache_Sorted） |
| `-f, --force`  | 覆盖已存在的目标文件                   |
| `-q, --quiet`  | 仅打印汇总，不逐文件输出               |

## 识别规则

脚本通过读取文件头 magic bytes 判定类型：

| 类型        | 特征                                   |
| ----------- | -------------------------------------- |
| PNG         | `89 50 4E 47 0D 0A 1A 0A`              |
| JPEG        | `FF D8 FF`                             |
| WebP        | `RIFF....WEBP`                         |
| WebM        | EBML 头 `1A 45 DF A3`                  |
| 证书 (DER)  | ASN.1 SEQUENCE `30 8x`                 |
| 缓存元数据  | 文件名 `index` / `data_0`..`data_n`    |
| JSON        | 可被 `json.loads` 解析的文本           |
| 视频分片    | 文件大小恰好为 1048576 字节（1MB）     |

> 视频文件超过 1MB 时会被 Chromium 切成 1MB 的块，续分片不含 EBML 头，
> 因此通过文件大小（1048576 字节）识别为视频分片。

## License

MIT
