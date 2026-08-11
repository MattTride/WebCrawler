# Crawl Studio

一个面向日常网页检查与内容采集的 Python 桌面应用。输入单个 URL，Crawl Studio 会在同一个工作台中呈现页面概览、SEO 情报、正文、链接、图片、视频、原始 HTML 和本地任务历史，并支持媒体下载与结构化报告导出。

[![Tests](https://github.com/MattTride/WebCrawler/actions/workflows/tests.yml/badge.svg)](https://github.com/MattTride/WebCrawler/actions/workflows/tests.yml)
[![Latest release](https://img.shields.io/github/v/release/MattTride/WebCrawler)](https://github.com/MattTride/WebCrawler/releases/latest)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## 下载桌面版

[![Download for macOS](https://img.shields.io/badge/Download-macOS-111111?style=for-the-badge&logo=apple&logoColor=white)](https://github.com/MattTride/WebCrawler/releases/latest/download/WebCrawler-macOS.zip)
[![Download for Windows](https://img.shields.io/badge/Download-Windows-0067b8?style=for-the-badge&logo=windows&logoColor=white)](https://github.com/MattTride/WebCrawler/releases/latest/download/WebCrawler-Windows.zip)

- **macOS**：解压后把 `WebCrawler.app` 拖入“应用程序”，右键应用并选择“打开”。
- **Windows**：解压后运行 `WebCrawler.exe`。若 SmartScreen 提示未知发布者，选择“更多信息”后再决定是否运行。
- **源码运行**：适合开发者，也可在 macOS 双击 `run_crawler.command`。

安装包由 GitHub Actions 在 `v*` 标签发布时分别在 macOS 和 Windows 环境中构建。每个压缩包旁会附带 `.sha256` 校验文件。

## 核心能力

### 集成式采集工作台

- URL 命令栏固定在首屏，支持自动补全 `https://`、回车获取和一键清空。
- 顶部同时展示请求状态、SEO 分数、链接、图片、视频和标题数量。
- 左侧工作区统一导航概览、页面情报、正文、链接、图片、视频、HTML 预览和历史记录。
- 网络抓取、图片预览和资源下载都在后台线程运行，主界面保持响应。
- 可查看服务器原始响应，包括 HTTP 状态行、响应头和服务器返回的正文。

### 页面情报与 SEO

- 估算中英文混合正文的字数与阅读时间。
- 区分内部链接、外部链接、HTTP 与 HTTPS 链接。
- 提取语言、canonical、作者、关键词、发布时间、站点名称、页面类型、robots 和生成器信息。
- 发现页面中的邮箱、电话、表单、密码字段、脚本、样式表和 iframe。
- 对标题长度、description、唯一 H1、canonical、语言、viewport、图片 alt、HTTPS 和 noindex 进行基础评分。
- 在“页面情报”中直接给出通过项与优先优化建议。

### 媒体与资源

- 提取 `img`、懒加载属性、`srcset`、Open Graph 图片和视频封面。
- 提取 `video`、`source`、视频 meta、iframe 播放地址和直接视频文件链接。
- 图片会在右侧媒体检查器中生成缩略图，不必先打开资源地址。
- 图片与视频列表支持实时搜索、多选和批量下载。
- 批量下载自动为同名文件添加编号，并隔离单个失败资源。

### 历史与导出

- 最近 50 次抓取摘要保存在本机 `~/.crawl_studio/history.json`。
- 历史记录支持双击再次抓取、复制网址、删除单条和清空全部。
- 历史只记录任务摘要，不保存网页正文、HTML 或媒体文件。
- 可导出 JSON 原始数据、Markdown 报告、HTML 可视报告和 CSV 资源清单。
- CSV 使用带 BOM 的 UTF-8 编码，可直接在 Excel 中打开中文内容。

## 快速开始

### 1. 获取源码

```bash
git clone https://github.com/MattTride/WebCrawler.git
cd WebCrawler
```

### 2. 创建环境并安装依赖

macOS / Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows PowerShell：

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### 3. 启动

```bash
python crawler_app.py
```

要求 Python 3.10 或更高版本，并且 Python 构建中包含 `tkinter`。Pillow 用于 JPEG、WebP 等图片缩略图；抓取和解析核心只使用 Python 标准库。

## 使用流程

1. 在“目标网址”输入框粘贴 URL。
2. 保持“遵守 robots.txt”勾选，点击“获取”或按 `Command/Ctrl + Enter`。
3. 在“概览”确认状态、最终地址、编码和页面结构。
4. 在“页面情报”查看 SEO 分数、元数据、联系方式、表单和资源诊断。
5. 在正文、链接、图片和视频工作区中搜索或检查结果。
6. 多选图片或视频后点击“批量下载”，选择目标文件夹。
7. 点击“导出报告”，通过文件后缀或文件类型选择 JSON、Markdown、HTML 或 CSV。

常用快捷操作：

| 操作 | macOS | Windows / Linux |
| --- | --- | --- |
| 定位并选中 URL | `Command + L` | `Ctrl + L` |
| 开始获取 | `Command + Enter` | `Ctrl + Enter` |
| 导出报告 | `Command + S` | `Ctrl + S` |

## 导出格式

| 格式 | 用途 | 内容 |
| --- | --- | --- |
| JSON | 程序处理、归档 | 完整 `CrawlResult` 结构 |
| Markdown | 笔记、Issue、知识库 | 摘要、情报、标题、资源与正文 |
| HTML | 浏览器阅读、发送报告 | 响应式指标、SEO 建议、媒体与链接表格 |
| CSV | Excel、资源盘点 | 摘要、链接、图片、视频、表单和页面资源 |

JSON 中除基础字段外，还包含以下分析结果：

```json
{
  "word_count": 680,
  "reading_minutes": 3,
  "page_info": {
    "domain": "example.com",
    "language": "zh-CN",
    "canonical": "https://example.com/page"
  },
  "link_stats": {
    "total": 24,
    "internal": 18,
    "external": 6,
    "https": 24,
    "http": 0
  },
  "contacts": {"emails": [], "phones": []},
  "forms": [],
  "resources": {"scripts": [], "stylesheets": [], "iframes": []},
  "seo_report": {"score": 88, "grade": "优秀", "issues": [], "checks": []}
}
```

## macOS 无法打开时

本项目的公开构建目前未使用 Apple Developer ID 签名，因此 Gatekeeper 可能阻止第一次启动。

1. 确认已经完整解压 ZIP，不要直接在压缩包预览中运行。
2. 右键 `WebCrawler.app`，选择“打开”，再在系统对话框中确认。
3. 仍被拦截时，打开“系统设置 > 隐私与安全性”，在安全提示旁选择“仍要打开”。
4. 也可以从源码运行 `python crawler_app.py`，用来区分 Gatekeeper 与程序本身的问题。

请只运行从本仓库 Release 下载并核对过 SHA-256 的安装包。

## 抓取范围与限制

- 应用一次只抓取用户输入的单个页面，不递归遍历整个网站。
- 默认检查并遵守目标站点的 `robots.txt`；关闭此选项前请确认你有权抓取。
- 抓取的是服务器返回的 HTML，不执行网页 JavaScript。依赖客户端渲染的正文或媒体可能无法出现。
- 受登录、验证码、地区限制、DRM 或临时签名保护的资源可能无法获取或下载。
- 部分媒体地址是播放器页面而非可直接保存的视频文件。
- 请遵守目标网站服务条款、版权要求、隐私规定和当地法律，不要进行高频请求。

## 项目结构

```text
.
├── crawler_app.py             # tkinter 桌面界面与应用工作流
├── crawler_core.py            # 抓取、解析、分析与报告生成
├── workspace_store.py         # 本地历史记录持久化
├── crawler_app.spec           # macOS PyInstaller 应用配置
├── run_crawler.command        # macOS 源码备用启动器
├── requirements.txt           # 运行依赖
├── requirements-dev.txt       # 测试依赖
├── tests/                     # 离线单元测试与 GUI 冒烟测试
├── .github/workflows/         # 持续测试与跨平台发布构建
├── CHANGELOG.md               # 版本变更记录
└── LICENSE                    # MIT License
```

核心层没有 `tkinter` 依赖，可以单独导入并用于脚本：

```python
from crawler_core import fetch_url, result_to_markdown

result = fetch_url("https://example.com")
print(result.title)
print(result_to_markdown(result))
```

## 开发与测试

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m py_compile crawler_app.py crawler_core.py workspace_store.py
```

测试不会访问互联网。网络行为通过 mock 验证；GUI 冒烟测试在没有显示器的 CI 环境中会自动跳过窗口实现部分。

## 本地打包

```bash
python -m pip install -r requirements-dev.txt pyinstaller
pyinstaller --clean --noconfirm crawler_app.spec
```

macOS 应用会生成在 `dist/WebCrawler.app`。Windows 发布包由 GitHub Actions 使用 `--onefile --windowed` 构建。

发布新版本时，在 `main` 上创建并推送版本标签：

```bash
git tag v2.0.0
git push origin v2.0.0
```

也可以在 GitHub Actions 页面手动运行 `Build release packages`，此时安装包会作为工作流 Artifact 保存，但不会自动创建 Release。

## License

本项目使用 [MIT License](LICENSE)。Copyright (c) 2026 Tride。
