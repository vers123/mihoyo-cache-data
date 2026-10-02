# mihoyo-cache-data

从米哈游启动器（miHoYo Launcher / HYP）的 Chromium 缓存目录中提取并分类资源的工具集。

## 数据来源

- 原始缓存路径：`%APPDATA%\miHoYo\HYP\1_1\fedata\Cache\Cache_Data`
- 启动器基于 Chromium Embedded Framework，其 Blockfile Disk Cache 机制将网络资源以无扩展名文件（`f_xxxxxx`、`data_0`~`data_3`、`index`）形式存储在 `Cache_Data/` 中。
- 本项目通过解析 Chromium 缓存元数据（`index` + `data_#` 中的 EntryStore），建立 URL → `f_*` 文件的映射，结合 HTTP 响应头（content-type）和 URL 扩展名进行智能分类，并将视频分片自动合并。

> 原始 `Cache_Data/` 与提取结果 `Cache_Sorted/` 均不纳入版本控制（见 `.gitignore`），仓库只保留脚本与文档。

## 目录结构

```
mihoyo-cache-data/
├── scripts/
│   ├── cache_parser.py        # Chromium 缓存解析模块（index + entry + URL 映射）
│   ├── extract_cache.py       # 提取与分类脚本（CLI）
│   └── gui.py                 # 图形界面（PySide6）
├── requirements.txt
├── .github/workflows/release.yml
├── .gitignore
├── LICENSE
└── README.md
```

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
- 使用缓存元数据（URL 命名 + 智能分类）开关
- 视频分片自动合并开关
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

| 参数               | 说明                                   |
| ------------------ | -------------------------------------- |
| `-i, --input`      | 指定 Cache_Data 目录路径               |
| `-o, --output`     | 指定分类结果输出目录（默认 Cache_Sorted） |
| `-f, --force`      | 覆盖已存在的目标文件                   |
| `-q, --quiet`      | 仅打印汇总，不逐文件输出               |
| `--no-cache-meta`  | 禁用缓存元数据解析，改用 magic bytes 分类 |
| `--no-merge-video` | 禁用视频分片自动合并                   |

## 识别规则

脚本通过解析 Chromium 缓存元数据（URL + HTTP content-type）进行分类，magic bytes 作为兜底：

| 类型        | 特征                                   |
| ----------- | -------------------------------------- |
| PNG         | URL 扩展名 `.png` 或 content-type `image/png` |
| JPEG        | URL 扩展名 `.jpg`/`.jpeg` 或 content-type `image/jpeg` |
| WebP        | URL 扩展名 `.webp` 或 content-type `image/webp` |
| WebM        | URL 扩展名 `.webm` 或 content-type `video/webm` |
| 证书 (DER)  | URL 扩展名 `.cer`/`.crt` 或 magic bytes `30 8x` |
| 缓存元数据  | 文件名 `index` / `data_0`..`data_n`    |
| JSON        | content-type `application/json` 或可被 `json.loads` 解析的文本 |
| 视频分片    | URL 后缀 `:hash:N`（N 为十六进制序号） |

> 启用缓存元数据解析后（默认开启），输出文件名将来自原始 URL 的哈希值而非 `f_xxxxxx`，
> 分类依据为 URL 扩展名与 HTTP content-type，比单纯 magic bytes 更准确。
> 视频分片（range request）会按序号自动拼接成完整 WebM 文件。

## License

MIT
