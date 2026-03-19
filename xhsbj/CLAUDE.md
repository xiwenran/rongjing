# PPT 场景合成工具 — CLAUDE.md

> **维护约定（重要）**：每次对话结束前，必须主动将本次新增的功能、修复的 Bug、发现的约定追加到本文件对应章节，保持文件始终反映项目最新状态。

## 项目简介

将 PPT 截图（或视频录屏）通过透视变换嵌入到实拍背景图的屏幕区域，批量生成合成图片或视频。
面向场景：教师把 PPT 内容嵌入教室大屏背景照片/视频，用于制作课程素材。

运行方式：
```bash
cd /Users/xili/xhsbj
python3 main.py
```

打包方式：
```bash
bash build_app.sh   # 生成 dist/PPT场景合成工具.app + dist/PPT场景合成工具_arm64.dmg
```

> 架构说明：PyInstaller 生成的包只能在同架构 Mac 运行（arm64 = Apple Silicon，x86_64 = Intel）。DMG 文件名含架构后缀（`_arm64.dmg` / `_x86_64.dmg`），按需在对应机器上分别打包。
> 首次运行未签名 .app：右键点击 → 打开 → 点击"打开"（不能直接双击，会被 Gatekeeper 拦截）。

---

## 项目结构

```
xhsbj/
├── main.py                  # 入口：QApplication + MainWindow
├── requirements.txt         # PyQt6, Pillow, opencv-python, numpy, av, pyinstaller
├── build_app.sh             # PyInstaller 打包脚本（--collect-all av cv2）
├── templates/               # 模板 JSON 文件存储目录（运行时读写）
│   └── <name>.json
├── core/
│   ├── image_processor.py   # 透视变换核心：embed_image / embed_image_pil
│   └── batch_runner.py      # BatchRunner(QThread) / VideoRunner(QThread)
├── models/
│   └── template_model.py    # Template(dataclass) + TemplateManager
└── ui/
    ├── canvas_widget.py     # 交互式角点标注画布（左键放点，拖拽，右键撤销）
    └── main_window.py       # 主窗口：全部 UI 逻辑（1100+ 行）
```

---

## 技术栈

| 层次 | 库 | 用途 |
|------|-----|------|
| GUI | PyQt6 | 主界面、QThread 异步处理 |
| 图像 | Pillow (PIL) | 图片读写、格式转换 |
| 变换 | OpenCV (cv2) | 透视变换 `getPerspectiveTransform` + `warpPerspective` |
| 视频 | PyAV (av ≥11) | 视频帧解码/编码（libx264 + AAC），无需外部 ffmpeg |
| 设置持久化 | QSettings | 记忆每个选择器上次使用的路径 |
| 打包 | PyInstaller | macOS .app 双击运行 |

---

## 数据模型

### Template（`models/template_model.py`）
```python
@dataclass
class Template:
    name: str
    background_path: str               # 背景图片绝对路径
    screen_points: List[List[float]]   # 4个角点 [x,y]，顺序 TL→TR→BR→BL（背景图坐标）
    output_width: int = 0              # 0 = 自动（等于背景图尺寸）
    output_height: int = 0
```
- 每个模板存为 `templates/<name>.json`
- `TemplateManager` 提供 `save / load / load_all / delete`

---

## 核心算法（`core/image_processor.py`）

### `embed_image_pil(ppt_img, bg_img, points, feather=2)`
1. 用 `cv2.getPerspectiveTransform` + `cv2.warpPerspective(INTER_LINEAR)` 把 PPT 图透视到背景坐标
2. 用 `cv2.fillPoly` 生成四边形 mask
3. **Inward feathering**：先 3×3 椭圆核 erode，再 GaussianBlur(ksize=feather*2+1)，clip 到原 mask 内
4. Alpha blend：`result = (1-mask)*bg + mask*warped`

> **重要**：使用 `INTER_LINEAR` 不用 `INTER_LANCZOS4`（避免振铃）；feather 默认 2（不要改大，否则出白边）；erode 必须在 blur 之前（隐藏边缘插值 fringe）

---

## 批量处理（`core/batch_runner.py`）

### BatchRunner(QThread)
- 信号：`progress(done, total, msg)` / `finished(success, msg)`
- tasks 格式：`List[(group_name: str, file_list: List[str], templates: List[Template])]`
- 输出目录结构：`output_dir/group_name/template_name/1.png, 2.png, ...`
- 输出尺寸来自 `template.output_width/height`（不再有全局尺寸参数）
- **速度优化**：每个模板调用一次 `precompute_template_cache(bg_img, points)` 预计算 mask 和背景数组（RGB，3通道），再对所有图片调用 `embed_image_pil_fast(ppt_img, cache)`，避免重复计算；使用 BILINEAR 替代 BICUBIC 插值（2-3× 速度提升）

### VideoRunner(QThread)
- tasks 格式：`List[(video_path: str, templates: List[Template])]`
- **逻辑**：视频每帧 = PPT 内容（嵌入目标），模板背景图 = 场景（接收画面）
- 使用 PyAV 重编码：libx264 视频 + AAC 音频
- **速度优化（多重）**：
  - `precompute_template_cache(..., ppt_size=(w,h))` 在视频开始前预计算透视系数（利用流元数据获取帧尺寸），使 cache 完全只读，线程安全
  - `embed_image_pil_fast` 使用 RGB 3通道 + BILINEAR 插值（比 BICUBIC RGBA 快约 3-4×）
  - `ThreadPoolExecutor(max_workers=N-1)` 并行处理视频帧：PIL/numpy C 代码释放 GIL，多线程可真正并行；使用滑动窗口（`deque` 最多 `num_workers*2` 帧在飞）保证按顺序编码，音频仍串行处理以保持同步
- **编码预设**：`preset=veryfast`（比 fast 快约 30-50%，画质无明显损失）
- **PTS 修复**：视频用帧计数器 `out_frame.pts = frame_i`；音频用样本计数器 `resampled.pts = audio_pts; audio_pts += resampled.samples`
- 音频用 `av.AudioResampler(format="fltp", layout, rate)` 转格式后编 AAC

---

## UI 结构（`ui/main_window.py`）

### 主题色（WeChat 风格亮色）
```python
_WIN   = "#F7F7F7"   # 页面背景
_CARD  = "#FFFFFF"   # 卡片
_INPUT = "#F0F0F0"   # 输入框背景
_SEP   = "#E5E5E5"   # 分隔线
_TEXT  = "#191919"   # 主文字
_TEXT2 = "#888888"   # 次要文字
_GREEN = "#07C160"   # 主色（微信绿）
_RED   = "#FA5151"   # 危险色
```

### 重要 CSS 约定
- **禁止在任何子 QWidget 上调用 `setStyleSheet("background:...")`**（无选择器的 stylesheet 会创建 style 作用域隔离，导致全局 `QPushButton#primary` 等规则失效）
- 模式切换按钮（modeBtn）用 Python `setStyleSheet()` 显式设置颜色，不依赖 CSS `:checked` 伪类（macOS 下伪类初始化不可靠）
- 表格选中色直接设在 table widget 上：`QTableWidget::item:selected { background: rgba(7,193,96,0.18); }`

### 两个标签页

**模板配置（编辑器）**
- 左侧 sidebar 固定宽 420px：模板库列表 + 场景配置（名称/背景图/角点/输出尺寸）+ 嵌入预览
- 右侧 `CanvasWidget`：左键依次点击放置 4 个角点（TL→TR→BR→BL），可拖拽，右键撤销

**批量导出**
- 内容区居中，max-width 960px
- 顶部 3 个模式按钮（modeBtn）：图片文件夹 / 图片批量 / 视频文件
- 步骤 1 根据模式显示不同 Card（`_c1_folder` / `_c1_image` / `_c1_video`）
- 步骤 2（`_c2`）：为每组/每行选择模板，视频模式下隐藏
- 步骤 3：输出目录 + 图片格式（视频模式隐藏格式选择器）

### 路径记忆（`QSettings("xhsbj", "PPTComposer")`）
每个选择器独立记忆上次路径，跨会话持久化：

| 变量 | 用途 |
|------|------|
| `_last_dir_bg` | 模板背景图 |
| `_last_dir_preview` | 预览 PPT 图 |
| `_last_dir_input` | 批量输入文件夹 |
| `_last_dir_output` | 输出文件夹 |
| `_last_dir_images` | 图片批量文件 |
| `_last_dir_videos` | 视频文件 |

统一用 `self._save_dir(key, path)` 更新（同时写内存和 QSettings）。

### TemplatePickerDialog
- 多选对话框，含全选/全不选
- 必须在 dialog 上显式 `setStyleSheet(...)` 覆盖（对话框默认不继承主窗口样式）
- `QDialogButtonBox QPushButton` 需要单独在 dialog stylesheet 中声明

### macOS 文件选择
- 优先用 `osascript` 打开原生 Finder 选择器（支持 `default location`）
- 失败则回退 `QFileDialog`

---

## 已完成功能清单

- [x] 模板创建/编辑/删除，JSON 持久化
- [x] 交互式画布标注 4 角点，实时透视预览
- [x] 图片文件夹批量处理（支持子文件夹 + 根目录平铺两种结构）
- [x] 图片批量模式（手动选多张图片）
- [x] 视频文件模式（帧级嵌入，保留音频，PyAV 重编码）
- [x] 每行独立选择多个模板（TemplatePickerDialog）
- [x] 全部应用（一键为所有行设置同一批模板）
- [x] 视频音画同步修复（PTS 计数器策略）
- [x] WeChat 风格亮色主题
- [x] 路径跨会话记忆（QSettings）
- [x] macOS 原生文件/文件夹选择器
- [x] PyInstaller 打包脚本（`bash build_app.sh`）
- [x] DMG 打包（`hdiutil create -format UDZO`，含架构后缀命名，内置 Gatekeeper 使用说明）
- [x] 模式按钮初始绿色样式修复（Python 显式 setStyleSheet，不依赖 CSS :checked）
- [x] 表格行选中色改为浅绿（直接在 table widget 上设置，避开 scope 隔离）
- [x] 模板选择按钮填满列宽（QSizePolicy.Expanding）
- [x] 去除 cv2 依赖，改用 PIL 纯实现（`Image.PERSPECTIVE` + `ImageDraw` + `ImageFilter`）解决 PyInstaller 打包 cv2 bootstrap 递归崩溃
- [x] Windows 文件选择器黑色背景修复（仅 macOS 使用 `DontUseNativeDialog`，Windows 使用原生对话框）
- [x] GitHub Actions 自动打包（Windows only，push main 分支触发）
- [x] `同步到GitHub.command` 一键脚本：本地打包 Mac + push 代码 + 上传 Release（需 `gh` CLI）
- [x] 软件改名为「融景」
- [x] 模板存储迁移到系统级持久目录（Mac: `~/Library/Application Support/融景/templates/`，Windows: `%APPDATA%\融景\templates\`），更新 app 不丢数据
- [x] 批量/视频处理速度优化（`precompute_template_cache` + `embed_image_pil_fast`，mask/背景/透视系数按模板预计算复用）
- [x] Windows 黑色对话框按钮区修复（去掉 no-selector `setStyleSheet`；补 `QDialogButtonBox { background }` 规则）
- [x] Windows 黑色 tab bar 修复（QTabWidget 使用 QPalette + setAutoFillBackground，绕开 CSS transparent 渲染问题）
- [x] 侧边栏改为 QScrollArea（表单内容可滚动，「保存模板」和「清除数据」按钮固定在底部），解决小屏下模板列表只显示 1 条的问题
- [x] Windows 软件名称改为「融景」（build.yml 同步更新）
- [x] 按钮改为胶囊/圆角形状（QPushButton `border-radius:18px`，#primary `22px`，#scan `22px` + `min-height:44px`，modeBtn `22px`，Python setStyleSheet 也同步更新）
- [x] 卡片区块视觉区分（QWidget#card 渐变背景 + border-radius:16px；QLabel#h2 绿色左边框 border-left）
- [x] step_n 徽章改为固定 28×28px 正圆形（`min/max-width/height: 28px; border-radius: 14px; padding: 0`）
- [x] 模板列表每项加浅灰背景条（`background: {_INPUT}`），多模板时条目清晰可辨
- [x] 模板列表外加圆角边框容器（`QWidget#tpl_list_frame`，border-radius:10px）
- [x] 步骤 2 标题/说明文字动态切换（文件夹模式/图片批量模式不同描述）
- [x] 新建 FEATURES.md：详细需求清单（含文件命名、路径、格式等实现细节）
- [x] 更新 README.md：GitHub 展示页，含功能说明、技术实现、文件命名规则、格式支持表

---

## 已知约定 / 踩坑记录

1. **PyAV v17 音频**：不支持 `add_stream(template=in_as)`，必须用 `add_stream("aac", rate=sr)` + `AudioResampler(format="fltp", ...)`。
2. **Qt style scope**：任何 `widget.setStyleSheet("background:color")` 无选择器会切断全局样式树，禁止在 scroll_body / outer 等容器上使用。
3. **modeBtn 初始化**：`__init__` 中 `_build_ui()` 之后必须立即调用 `_set_batch_mode(0)` 才能应用初始绿色样式（CSS `:checked` 在 macOS 初始化阶段不生效）。
4. **透视变换边缘**：必须先 erode 再 blur（inward only），feather 保持 ≤ 2，使用 INTER_LINEAR 而非 INTER_LANCZOS4。
5. **视频帧嵌入逻辑**：视频帧是被嵌入的内容（≈PPT），模板背景图是场景容器，不要搞反。
6. **QPushButton cell widget 列宽**：放入 QTableWidget 的 cell widget 默认不填充列宽，需 `btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)` + `table.setCellWidget(row, col, btn)`。
7. **DMG 制作**：无需第三方工具，`hdiutil create -volname ... -srcfolder ... -ov -format UDZO -output xxx.dmg` 即可。先用 `xattr -cr app` 移除本机测试的隔离属性，但分发时建议告知用户右键打开流程。
8. **视频 PTS 根本原因**：libx264 编码器 time_base = 1/fps，`out_frame.pts = frame_i`（整数帧号）恰好对应正确时长。若直接复制输入 pts（time_base ≈ 1/90000），则时长会虚增约 90000/fps 倍，导致 1 分钟视频变 1 小时。
9. **cv2 在 PyInstaller 中的 bootstrap 递归**：opencv-python 的 `__init__.py` 调用 `importlib.import_module("cv2")` 时在冻结环境中触发递归。已彻底移除 cv2，用 PIL `Image.PERSPECTIVE` + `ImageDraw.polygon` + `ImageFilter.MinFilter/GaussianBlur` 替代，质量相当。
10. **Windows QTabWidget 黑色 tab bar**：`QWidget { background: transparent }` 会导致 Windows 上 tab bar 右侧空白区域渲染为黑色。解决方案：在 `_build_ui()` 里对 `self.tabs` 调用 `QPalette + setAutoFillBackground(True)`，直接设定背景色，绕过 CSS 系统。
11. **侧边栏布局压缩**：侧边栏使用单一 QVBoxLayout 时，在小屏幕（1366×768）上表单内容超出高度，addStretch 收缩为零，模板列表被压到最小高度只显示 1 条。解决方案：将表单内容放入 QScrollArea，「保存/卸载」按钮固定在 QScrollArea 下方（不参与滚动）。
12. **macOS 26 Tahoe beta 兼容性**：GitHub Actions 用 macOS 14/15 编译的 PyQt6 在 macOS 26 上 PAC 签名校验失败崩溃。解决方案：Mac 版本在本机用 `bash build_app.sh` 打包，Windows 版本用 GitHub Actions 打包。
11. **模板数据目录**：`main.py` 中 `get_data_dir()` 返回系统级目录，与 app bundle 完全分离。旧版模板在 `xhsbj/templates/`，已手动迁移到新位置。

---

## 发布流程（每次功能更新后）

> **⚠️ 对话结束前必须提醒用户运行同步脚本！**

1. 代码改完、本地 `python3 main.py` 验证正常
2. 双击 `同步到GitHub.command`（自动完成：本地打包 Mac → push 代码 → 上传 Release）
3. 等 10-15 分钟后去 [Actions 页面](https://github.com/xiwenran/-/actions) 下载 Windows 包

GitHub Releases Mac 下载地址：https://github.com/xiwenran/-/releases/latest
