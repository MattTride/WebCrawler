# 网页爬虫小程序

一个用 Python 编写的桌面网页爬虫应用。它提供类似 Claude 工作台的浅色界面：左侧显示抓取状态，中间展示结果，上方固定一个醒目的 URL 输入框。用户输入网页地址后，应用会抓取单个页面并提取标题、描述、正文、链接、图片、视频和 HTML 预览。

## 下载

根据操作系统点击下载（链接指向最新 Release，会自动跳转到对应安装包）：

[![Download for macOS](https://img.shields.io/badge/Download-macOS-111111?style=for-the-badge&logo=apple&logoColor=white)](https://github.com/MattTride/WebCrawler/releases/latest/download/WebCrawler-macOS.zip)
[![Download for Windows](https://img.shields.io/badge/Download-Windows-0067b8?style=for-the-badge&logo=windows&logoColor=white)](https://github.com/MattTride/WebCrawler/releases/latest/download/WebCrawler-Windows.zip)

- **macOS**：[下载 WebCrawler-macOS.zip](https://github.com/MattTride/WebCrawler/releases/latest/download/WebCrawler-macOS.zip)。解压后双击 `WebCrawler.app`；若提示“来自未识别的开发者”，右键点图标选“打开”。
- **Windows**：[下载 WebCrawler-Windows.zip](https://github.com/MattTride/WebCrawler/releases/latest/download/WebCrawler-Windows.zip)。解压后双击 `WebCrawler.exe`；若 SmartScreen 拦截，点“更多信息”→“仍要运行”。

> 安装包由 GitHub Actions 在推送 `v*` 版本标签时自动在 macOS / Windows 机器上构建（见 [`.github/workflows/release.yml`](.github/workflows/release.yml)），二进制不提交进仓库。首个 Release 生成后，上述链接即生效。

## 项目特点

- 独立桌面界面，使用 Python 标准库 `tkinter` 构建。
- 不依赖第三方运行库，普通 Python 环境即可运行。
- 上方大 URL 输入框，支持直接粘贴网页地址。
- 自动补全缺省的 `https://`。
- 抓取结果分为概览、正文、链接、图片、视频、HTML 预览六个区域。
- 链接、图片和视频地址支持复制单条、一键复制全部、双击打开。
- 右侧媒体预览会直接展示图片和视频资源卡片，图片不需要进入列表也能看到。
- 图片和视频支持从预览卡片或列表中下载到本地。
- 支持将抓取结果保存为 JSON 或 Markdown 文件。
- 网络请求在后台线程执行，界面不会在抓取时卡死。
- 默认只抓取用户输入的单个页面，避免对目标网站造成压力。
- 抓取前自动检查目标站点的 robots.txt，默认遵守，可在界面上关闭。
- 遇到超时或连接失败会自动重试，并给出清晰的中文错误提示。
- 可一键查看服务器“原始响应”（状态行、响应头、原始正文），方便排查 JS 渲染或异常返回。

## 界面结构

应用界面分为三块：

- 左侧状态栏：显示当前任务说明，以及链接、图片、视频、标题、状态等统计信息。
- 中央结果区：使用标签页展示抓取结果，并在右侧显示媒体预览。
- 上方输入区：固定显示“将 URL 输入⬇️”输入框，输入框下方有醒目的“获取”按钮。

## 运行要求

- macOS、Windows 或 Linux。
- Python 3.9 及以上版本。
- Python 需要包含 `tkinter`。macOS 系统自带 Python 或 Homebrew Python 通常都可以使用。

本项目运行时不需要安装 `requests`、`beautifulsoup4` 等第三方库。

## 快速开始

在项目目录中运行：

```bash
python3 crawler_app.py
```

不想装 Python？用打包好的桌面版，见上面的 [下载](#下载)（macOS / Windows）。macOS 也可以双击备用启动脚本 `run_crawler.command` 从源码启动。

## 使用方法

1. 打开应用。
2. 在上方“将 URL 输入⬇️”输入框中粘贴或输入网址。
3. 点击“获取”，或在输入框里按回车。
4. 在中央区域查看抓取结果。
5. 切换“正文”“链接”“图片”“视频”“HTML预览”等标签页查看不同内容。
6. 在链接、图片或视频列表中选中一条记录后，可以复制地址或打开地址；也可以点“复制全部”一次性复制该列表的所有地址。
7. 在右侧媒体预览中点击图片或视频卡片，可以选择是否下载。
8. 点击“保存”可以把当前抓取结果保存为 JSON 或 Markdown 文件（在保存对话框里选择类型或用对应后缀）。

> 按钮行右侧的“遵守 robots.txt”默认勾选：若目标页面被 robots.txt 禁止，应用会提示并停止。取消勾选可强制抓取，请自行确保合规。

> 点击“原始响应”会在弹窗中显示目标服务器当前返回的状态行、响应头和原始正文——对依赖 JavaScript 渲染、正文提取很少的页面尤其有用。

## 输出内容

应用会尽量提取以下信息：

- 请求地址和最终跳转地址。
- HTTP 状态码和响应说明。
- Content-Type 和页面编码。
- 页面标题。
- meta description 或 Open Graph description。
- 页面标题结构，包含 H1 到 H6。
- 页面可读正文。
- 页面中的链接。
- 页面中的图片地址，包括 img 标签、srcset 和常见图片 meta 信息。
- 页面中的视频地址，包括 video/source 标签、常见视频 meta 信息和直接指向视频文件的链接。
- HTML 预览内容。
- 抓取时间、读取大小和是否截断。

## 保存结果格式

保存功能默认生成一个 JSON 文件（也可在对话框中选择 `.md`，导出一份可读的 Markdown 摘要）。JSON 结构大致如下：

```json
{
  "requested_url": "https://example.com",
  "final_url": "https://example.com",
  "status_code": 200,
  "reason": "OK",
  "content_type": "text/html; charset=utf-8",
  "encoding": "utf-8",
  "title": "Example Domain",
  "description": "",
  "headings": [],
  "links": [],
  "images": [],
  "videos": [],
  "text": "...",
  "html_preview": "...",
  "bytes_read": 1256,
  "truncated": false,
  "fetched_at": "2026-05-08 11:00:00"
}
```

## 项目文件

```text
.
├── crawler_app.py          # 桌面界面（tkinter）
├── crawler_core.py         # 抓取与解析核心（无界面依赖，可单独测试）
├── tests/                  # pytest 单元测试
├── conftest.py             # 让 tests 能导入 crawler_core
├── requirements-dev.txt    # 开发依赖（仅 pytest）
├── run_crawler.command     # macOS 备用启动脚本
├── crawler_app.spec        # PyInstaller 打包配置
├── .github/workflows/      # GitHub Actions：打 tag 自动构建 macOS/Windows 安装包
├── README.md               # 项目说明
├── LICENSE                 # MIT License
└── .gitignore              # Git 忽略规则
```

## 开发说明

项目分成两个文件，职责清晰：

- `crawler_core.py`：抓取与解析核心。使用 `urllib.request` 获取网页，使用 `html.parser.HTMLParser` 提取标题、描述、正文、链接、图片和视频。没有任何界面依赖，可以脱离窗口单独测试或复用。
- `crawler_app.py`：桌面界面。使用 `tkinter` 构建窗口、输入框、按钮、标签页和结果展示区域，并调用 `crawler_core` 完成抓取。

为了保持项目轻量，当前版本没有引入第三方 HTML 解析库。对于复杂的现代网页，尤其是依赖 JavaScript 渲染内容的网站，应用只能抓取服务器返回的初始 HTML，无法执行页面脚本。

## 运行测试

核心逻辑由 pytest 单元测试覆盖，运行时不联网、也不会弹出窗口：

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest
```

## 打包说明

如果希望重新生成 macOS 可执行应用，可以使用 PyInstaller：

```bash
python3 -m pip install pyinstaller
pyinstaller --noconfirm crawler_app.spec
```

打包结果会生成在 `dist/` 目录中。`dist/` 和 `build/` 属于生成文件，默认不会提交到 Git。

发布带下载包的版本：推送一个 `v` 开头的标签，即可触发 GitHub Actions 在 macOS 与 Windows 上分别构建，并发布到对应的 GitHub Release：

```bash
git tag v1.0.0
git push origin v1.0.0
```

也可以在 GitHub 仓库的 Actions 页面手动运行 “Build release packages”。

## 注意事项

- 应用默认会检查并遵守目标网站的 robots.txt；若手动关闭，请自行确保抓取行为符合其服务条款和当地法律法规。
- 不要对同一网站进行高频请求。
- 当前应用只抓取单个页面，不做递归深度爬取。
- 某些网站可能会拒绝自动化请求，或返回与浏览器不同的内容。
- 依赖 JavaScript 渲染的内容可能无法被提取。

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
