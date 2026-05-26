# PPT 场景合成工具（融景）— Codex 项目说明

> **适用环境**：Codex Mac 桌面端 / CLI
> **维护约定**：每次对话结束前，必须主动将本次新增的功能、修复的 Bug、发现的约定追加到本文件对应章节。

## 项目简介

将 PPT 截图（或视频录屏）通过透视变换嵌入到实拍背景图的屏幕区域，批量生成合成图片或视频。
面向场景：教师把 PPT 内容嵌入教室大屏背景照片/视频，用于制作课程素材。

运行方式：
```bash
python3 main.py
```

打包方式：
```bash
bash build_app.sh   # 生成 dist/PPT场景合成工具.app + dist/PPT场景合成工具_arm64.dmg
```

> 架构说明：PyInstaller 生成的包只能在同架构 Mac 运行（arm64 = Apple Silicon，x86_64 = Intel）。DMG 文件名含架构后缀。
> 首次运行未签名 .app：右键点击 → 打开 → 点击「打开」（不能直接双击，会被 Gatekeeper 拦截）。

---

## 项目结构

```
rongjing/
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

> **三条硬约束**：使用 `INTER_LINEAR` 不用 `INTER_LANCZOS4`（避免振铃）；feather 默认 2（不要改大，否则出白边）；erode 必须在 blur 之前（隐藏边缘插值 fringe）

---

## 批量处理（`core/batch_runner.py`）

### BatchRunner(QThread)
- 信号：`progress(done, total, msg)` / `finished(success, msg)`
- tasks 格式：`List[(group_name: str, file_list: List[str], templates: List[Template])]`
- 输出目录结构：`output_dir/group_name/template_name/1.png, 2.png, ...`
- 输出尺寸来自 `template.output_width/height`（无全局尺寸参数）
- **速度优化**：每个模板调用一次 `precompute_template_cache(bg_img, points)` 预计算 mask 和背景数组（RGB，3通道），再对所有图片调用 `embed_image_pil_fast(ppt_img, cache)`；使用 BILINEAR 替代 BICUBIC（2-3× 速度提升）

### VideoRunner(QThread)
- tasks 格式：`List[(video_path: str, templates: List[Template])]`
- **逻辑**：视频每帧 = PPT 内容（嵌入目标），模板背景图 = 场景（接收画面）
- 使用 PyAV 重编码：libx264 视频 + AAC 音频
- **速度优化（多重）**：
  - `precompute_template_cache(..., ppt_size=(w,h))` 视频开始前预计算透视系数
  - `embed_image_pil_fast` 使用 RGB 3通道 + BILINEAR 插值（比 BICUBIC RGBA 快约 3-4×）
  - `ThreadPoolExecutor(max_workers=N-1)` 并行处理帧：PIL/numpy C 代码释放 GIL，多线程可真正并行；滑动窗口（`deque` 最多 `num_workers*2` 帧在飞）保证按顺序编码
- **编码预设**：`preset=veryfast`（比 fast 快约 30-50%，画质无明显损失）
- **PTS 修复**：视频用帧计数器 `out_frame.pts = frame_i`；音频用样本计数器
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
- **禁止在任何子 QWidget 上调用 `setStyleSheet("background:...")`**（无选择器的 stylesheet 会创建 style 作用域隔离，导致全局规则失效）
- 模式切换按钮（modeBtn）用 Python `setStyleSheet()` 显式设置颜色，不依赖 CSS `:checked` 伪类（macOS 下伪类初始化不可靠）
- 表格选中色直接设在 table widget 上

### 两个标签页

**模板配置（编辑器）**
- 左侧 sidebar 固定宽 420px：模板库列表 + 场景配置 + 嵌入预览
- 右侧 `CanvasWidget`：左键依次点击放置 4 个角点（TL→TR→BR→BL），可拖拽，右键撤销

**批量导出**
- 内容区居中，max-width 960px
- 顶部 3 个模式按钮：图片文件夹 / 图片批量 / 视频文件
- 步骤 1 根据模式显示不同 Card
- 步骤 2：为每组/每行选择模板（视频模式下隐藏）
- 步骤 3：输出目录 + 图片格式（视频模式隐藏格式选择器）

### 路径记忆（`QSettings("xhsbj", "PPTComposer")`）
每个选择器独立记忆上次路径，跨会话持久化：`_last_dir_bg` / `_last_dir_preview` / `_last_dir_input` / `_last_dir_output` / `_last_dir_images` / `_last_dir_videos`

### TemplatePickerDialog
- 多选对话框，含全选/全不选
- 必须在 dialog 上显式 `setStyleSheet(...)` 覆盖
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
- [x] PyInstaller 打包脚本
- [x] DMG 打包（hdiutil，含架构后缀命名，内置 Gatekeeper 使用说明）
- [x] 模式按钮初始绿色样式修复
- [x] 表格行选中色改为浅绿
- [x] 模板选择按钮填满列宽（QSizePolicy.Expanding）
- [x] 去除 cv2 依赖，改用 PIL 纯实现
- [x] Windows 文件选择器黑色背景修复
- [x] GitHub Actions 自动打包（Windows only）
- [x] `同步到GitHub.command` 一键脚本
- [x] 软件改名为「融景」
- [x] 模板存储迁移到系统级持久目录
- [x] 批量/视频处理速度优化
- [x] Windows 黑色对话框/tab bar 修复
- [x] 侧边栏改为 QScrollArea
- [x] 按钮改为胶囊/圆角形状
- [x] 卡片区块视觉区分
- [x] 新建 FEATURES.md / 更新 README.md
- [x] 去水印模块三列布局（左侧输入输出 / 中列强度操作 / 右侧处理预览）

---

## 已知约定 / 踩坑记录

1. **PyAV v17 音频**：不支持 `add_stream(template=in_as)`，必须用 `add_stream("aac", rate=sr)` + `AudioResampler(format="fltp", ...)`
2. **Qt style scope**：任何 `widget.setStyleSheet("background:color")` 无选择器会切断全局样式树，禁止在容器上使用
3. **modeBtn 初始化**：`__init__` 中 `_build_ui()` 之后必须立即调用 `_set_batch_mode(0)` 才能应用初始绿色样式
4. **透视变换边缘**：必须先 erode 再 blur（inward only），feather 保持 ≤ 2，使用 INTER_LINEAR
5. **视频帧嵌入逻辑**：视频帧是被嵌入的内容（≈PPT），模板背景图是场景容器，不要搞反
6. **QPushButton cell widget 列宽**：放入 QTableWidget 的 cell widget 默认不填充列宽，需 `setSizePolicy(Expanding, Preferred)`
7. **DMG 制作**：`hdiutil create -volname ... -srcfolder ... -ov -format UDZO`，先 `xattr -cr app` 移除隔离属性
8. **视频 PTS 根本原因**：libx264 编码器 time_base = 1/fps，`out_frame.pts = frame_i` 对应正确时长。若复制输入 pts 会导致时长虚增
9. **cv2 在 PyInstaller 中的 bootstrap 递归**：已彻底移除 cv2，用 PIL 替代
10. **Windows 黑色区域**：需要对 root 和 tabs 都用 `QPalette + setAutoFillBackground(True)` 才能消除
11. **侧边栏布局压缩**：小屏幕上表单内容放入 QScrollArea，按钮固定在底部
12. **macOS 26 Tahoe beta 兼容性**：Mac 版本在本机用 `bash build_app.sh` 打包，Windows 版本用 GitHub Actions
13. **模板数据目录**：`main.py` 中 `get_data_dir()` 返回系统级目录（`~/Library/Application Support/融景/templates/`），与 app bundle 完全分离
14. **去水印预览缩放**：右侧预览图不要直接写死尺寸，需跟随 `QLabel` 尺寸变化重新 `scaled(..., KeepAspectRatio)`，否则窗口缩放后预览会发虚或留大片空白

---

## 发布流程

> **对话结束前必须提醒用户运行同步脚本！**

1. 代码改完、本地 `python3 main.py` 验证正常
2. 双击 `同步到GitHub.command`（自动：本地打包 Mac → push 代码 → 上传 Release）
3. 等 10-15 分钟后去 Actions 页面下载 Windows 包

GitHub Releases Mac 下载地址：https://github.com/xiwenran/rongjing/releases/latest

---

## 开工前必读

每次开始新任务前，先 Read 以下文件了解历史上下文：
- `~/Obsidian/PersonalWiki/项目/融景/踩坑记录.md`

---

## Echo 收尾检查

完成任何实质性改动后，报告完成之前检查：

1. **高风险改动**（发布逻辑/写库/防误操作）→ 派 worker 读取 echo-reviewer.md 做审查，或使用 App Review
2. **方向性问题**（项目定位、产品路线、架构主线、商业方向、长期维护策略；用户表达「要不要 / 像 A 还是像 B / 感觉但不确定」；或主会话准备列出 ≥2 个互斥方向）→ 先停下，说「这是方向性问题，建议三省讨论」
3. **有实质改动** → 建议写入 Obsidian changelog（`~/Obsidian/PersonalWiki/项目/融景/changelog/`）
4. **git push 前** → 脱敏扫描（绝对路径/token/邮箱/AppID）
5. **规则文件改动** → 检查 rule-sync-matrix.yml 同步状态
