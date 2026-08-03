# PPT 场景合成工具 — CLAUDE.md

> **维护约定（重要）**：每次对话结束前，必须主动将本次新增的功能、修复的 Bug、发现的约定追加到本文件对应章节，保持文件始终反映项目最新状态。

## 项目简介

将 PPT 截图（或视频录屏）通过透视变换嵌入到实拍背景图的屏幕区域，批量生成合成图片或视频。
面向场景：教师把 PPT 内容嵌入教室大屏背景照片/视频，用于制作课程素材。

**本地路径**：`~/rongjing/`（GitHub 仓库名 `xiwenran/rongjing`，与本地目录名拼音一致但 Obsidian 项目名用中文"融景"，见下方项目坐标）

---

## 项目坐标（AI 找本项目信息的固定入口）

Obsidian 里本项目名为**融景**（中文，与本地路径拼音 `rongjing` 不一致，注意区分）。找本项目的方案/进度/风险 → 直接读这几个路径，不用扫全库：

| 类别 | 路径 |
|---|---|
| 项目主线 roadmap（本地） | `~/rongjing/docs/roadmap.md` |
| Obsidian 项目主页 | `~/Obsidian/PersonalWiki/项目/融景/README.md` |
| Obsidian changelog / 进度 | `~/Obsidian/PersonalWiki/项目/融景/changelog/`（按日期命名，取目录列表最新一个） |
| Obsidian 踩坑记录 | `~/Obsidian/PersonalWiki/项目/融景/踩坑记录.md` |

> 坐标卡只存**常青入口的固定路径**；具体内容会变，查最新进度需要实际读文件。每次开始新任务前建议先读一遍踩坑记录了解历史上下文。

---

运行方式：
```bash
python3 main.py
```

打包方式：
```bash
双击 打包融景启动器.app   # 推荐：自动打开终端并生成 dist/融景.app + dist/融景_arm64.dmg
bash 打包融景.command     # 备用：终端方式
```

> 架构说明：PyInstaller 生成的包只能在同架构 Mac 运行（arm64 = Apple Silicon，x86_64 = Intel）。DMG 文件名含架构后缀（`_arm64.dmg` / `_x86_64.dmg`），按需在对应机器上分别打包。
> 首次运行未签名 .app：右键点击 → 打开 → 点击"打开"（不能直接双击，会被 Gatekeeper 拦截）。

---

## 项目结构

```
rongjing/
├── main.py                  # 入口：QApplication + MainWindow
├── requirements.txt         # PyQt6, Pillow, opencv-python, numpy, av, pyinstaller
├── 打包融景.command          # 终端打包入口，转调 scripts/package_rongjing.sh
├── 打包融景启动器.app        # 双击打包入口，自动打开终端执行打包
├── scripts/
│   ├── package_rongjing.sh  # PyInstaller 打包脚本（collect av；cv2 只 hidden-import）
│   └── create_packaging_launcher.sh  # 重新生成双击打包启动器
├── templates/               # 模板 JSON 文件存储目录（运行时读写）
│   └── <name>.json
├── core/
│   ├── image_processor.py   # 透视变换核心：embed_image / embed_image_pil
│   ├── realism_filter.py    # 实拍质感滤镜：光照适配 + 拍摄损耗（默认开启）
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
- 使用 PyAV 重编码：H.264 视频 + AAC 音频
- **三段流水线**（核心性能架构）：把「单线程从头干到尾」拆成三段并行运转
  - **解码线程**：跑 `inp.demux()`，视频帧 `to_image()` 后放入有界 `decode_q`，音频帧重采样后放入 `audio_q`
  - **嵌入线程池**：主线程从 `decode_q` 取帧 `pool.submit(embed_image_pil_fast)`，沿用 `pending deque` 滑动窗口（`num_workers*2` 帧在飞）
  - **编码线程**：消费 `audio_q` + `encode_q`，做 `encode` + `mux`。**所有 `outp.mux` 只在这一个线程发生**（PyAV container 非线程安全）
  - 收益：解码（PyAV）和编码（libx264/VideoToolbox）不再被关在同一线程轮流跑，可与嵌入真正重叠
- **Mac 硬件编码（VideoToolbox）**：`_detect_encoder()` 探测 `h264_videotoolbox` 可用性
  - 可用（Mac）→ 用 VideoToolbox，码率用 `bit_rate`（不支持 crf），按背景分辨率估算 `bg_w*bg_h*fps*0.07`，上限 20Mbps
  - 不可用（Windows/Linux）→ 降级回 `libx264 + {crf:18, preset:veryfast}`
- **帧顺序保证**：嵌入并行（乱序完成），但 `_drain` 永远 `popleft` 取最小 `frame_i` 且 `fut.result()` 阻塞等待，`encode_q` FIFO，输出严格有序
- **底层优化**：`precompute_template_cache(..., ppt_size=(w,h))` 预计算透视系数使 cache 只读；`embed_image_pil_fast` 用 RGB 3通道 + BILINEAR 插值
- **PTS 修复**：视频用帧计数器 `out_frame.pts = frame_i`；音频用样本计数器 `resampled.pts = audio_pts; audio_pts += resampled.samples`
- 音频用 `av.AudioResampler(format="fltp", layout, rate)` 转格式后编 AAC
- **abort / 异常**：`_abort` 标志三线程轮询；`_send_sentinel` 在 abort 或消费线程已死时腾队列槽位避免永久阻塞；解码/编码线程异常存入共享变量，主线程 join 后检查并经 `finished` 信号报错

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
- [x] PyInstaller 打包脚本（双击 `打包融景启动器.app`，或运行 `bash 打包融景.command`）
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
- [x] 视频导出三段流水线重构（解码/嵌入/编码三线程并行，解码与编码不再串行轮流跑）
- [x] Mac 硬件编码（VideoToolbox），Windows/Linux 自动降级 libx264
- [x] 修复视频导出丢尾帧 bug（`dts=None` flush packet 被跳过，导致解码器重排序缓冲未排空）
- [x] 图片批量导出线程池并行（`BatchRunner.run` 每模板内 `ThreadPoolExecutor`，worker 上限 6，60 张 1920×1080 实测提速约 3.6 倍；`embed_image_pil_fast` 遇异常尺寸改用局部透视系数、不写回共享 cache，消除多线程并发写竞争；输出顺序/内容零回归，视频路径不受影响）
- [x] 模板配置页按「教师场景 / 台式机电脑 / 笔记本室内 / 文档纸张 / 自定义场景」提供分类预设，并支持自定义分类输入；合成类型不再展示，按分类自动推断
- [x] 修复打包 .app 里自动角点识别静默失效（main.py 冻结环境跳过 sys.path.insert，见踩坑 21）；识别失败落盘 detect_error.log + RONGJING_DETECT_SELFTEST 无头自测入口
- [x] CLI 新增 `create-template` 子命令：背景图 → 自动识别角点 → 生成模板（复用 TemplateManager 存储约定），附带识别框预览图与质量指标（面积占比/宽高比/是否触边），识别失败非零退出不静默；为「Codex 生背景 → 自动建模板 → 合成发布」流水线提供无 GUI 建模板能力
- [x] 屏幕角点识别算法重写（五路候选生成 + 统一梯度打分 + 角点直线拟合精修；28 模板基准识别失败清零、精度大幅提升，近黑关屏笔记本类场景仍为已知难点）
- [x] VLM 粗框融合识别（core/vlm_locator.py 调 ark-worker 看图粗定位 + IoU 先验参与打分，不可用时逐位退回经典）与精修保护（_refine_quad_guarded 精修变差即丢弃）
- [x] 绿幕背景检测路径：AI 生成背景按「屏幕纯绿幕」约束出图，detect_green_screen_points 颜色分割像素级出角点（角点外扩 8px 实测零渗色），create-template 自动路由，普通图不受影响
- [x] 建模板背景自动放大保清晰度（create-template 屏幕宽 <1600px 时 LANCZOS 放大背景入库，PPT 压缩比 2.9:1→1.2:1，清晰度约 10 倍）；GUI「自动识别」按钮绿幕优先、失败退经典；RONGJING_DETECT_SELFTEST 与 GUI 同路由
- [x] AI 背景页新增背景场景：教师场景、台式机电脑、笔记本室内、文档纸张、自定义场景；生成张数支持 1-8 张自定义
- [x] 实拍质感滤镜（`core/realism_filter.py`，**默认开启、强度 70**）：合成结果套上符合该背景图的环境光照 + 拍屏损耗，让画面像随手拍而非数字贴图。两层——①光照适配，原屏有纹理则继承其低频，绿幕/纯色屏则从屏幕外圈环带反推光照方向与色温；②拍摄损耗，动态范围收窄到 [22,232]、轻失焦、含彩色分量的暗部加权噪点、暗角；③环境亮度自适应（仅绿幕路径），屏幕明暗跟随背景环境亮度，避免背景一暗屏幕就「自己发光」。只作用于屏幕/纸面区域（实测屏幕区外像素差异为 0）。三条路径全接线：图片批量、视频（逐帧换噪点）、CLI；GUI 批量导出页有开关+强度，CLI 用 `--no-realism` / `--realism-strength`
- [x] AI 背景生成提示词加入「老旧 iPhone 随手实拍」基调（`core/bg_prompt.py` 开头 parts）：轻微手持倾斜但主体清晰、正常室内灯光、略欠曝偏暗、可见传感器噪点与颗粒。GUI 与 CLI 共用同一份（两侧都委托 `build_prompt`）。绿幕分支单独加豁免句保护绿幕不被「偏暗」波及，避免削弱颜色分割建模板
- [x] AI 背景预设按实拍参考重设：标签组按场景联动裁剪（教室元素组替代摆件组、灯光过滤、去俯视角），近景硬约束（屏幕占比 55-75%），新增「屏幕显示绿幕」开关默认勾选（生成即可自动识别建模板）

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
10. **Windows 黑色区域（两处）**：`QWidget { background: transparent }` 在 Windows 上凡是没有显式绘制背景的 widget 都渲染为黑色。需要对 `root`（central widget）和 `self.tabs`（QTabWidget）都调用 `QPalette + setAutoFillBackground(True)` 才能彻底消除。`root` 用 `_WIN`，`self.tabs` 用 `_CARD`。
11. **侧边栏布局压缩**：侧边栏使用单一 QVBoxLayout 时，在小屏幕（1366×768）上表单内容超出高度，addStretch 收缩为零，模板列表被压到最小高度只显示 1 条。解决方案：将表单内容放入 QScrollArea，「保存/卸载」按钮固定在 QScrollArea 下方（不参与滚动）。
12. **macOS 26 Tahoe beta 兼容性**：GitHub Actions 用 macOS 14/15 编译的 PyQt6 在 macOS 26 上 PAC 签名校验失败崩溃。解决方案：Mac 版本在本机用 `打包融景启动器.app` 或 `bash 打包融景.command` 打包，Windows 版本用 GitHub Actions 打包。
13. **模板数据目录**：`main.py` 中 `get_data_dir()` 返回系统级目录，与 app bundle 完全分离。
14. **demux 的 `dts=None` flush packet 不能跳过**：PyAV `inp.demux()` 结束时会发一个 `dts=None` 的 flush packet，用来排空 H.264 解码器的 B 帧重排序缓冲。若 `if packet.dts is None: continue` 跳过它，会丢失视频末尾若干帧（实测丢 2 帧）。正确做法是照常 `packet.decode()`。
15. **视频三段流水线的线程安全靠 `queue.Queue` 屏障**：`audio_pts`、`AudioResampler` 等共享对象被解码线程和编码线程先后访问，没有显式锁——安全性依赖 `queue.Queue` 的 put/get 内部锁提供的 happens-before 屏障（解码线程发 sentinel 前的写，编码线程取到 sentinel 后可见）。改动流水线时不要绕过队列直接传递这些对象。
16. **`outp.mux` 必须单线程**：PyAV 的 output container 非线程安全，所有 `mux()` 调用只能在编码线程里发生，视频和音频包都走同一个编码线程串行 mux。
17. **VideoToolbox 不支持 crf**：`h264_videotoolbox` 编码器只认 `bit_rate`，不认 `crf`。`_detect_encoder()` 探测到它可用时用 `codec_context.bit_rate` 设码率；探测失败（Windows/Linux）才降级回 `libx264 + crf`。
18. **命令行能跑 ≠ 打包 .app 能跑（网络）**：`core/ai_background.py` 调 openai SDK，打包成 .app 后报 `Connection error.` 但命令行各种环境都连得通、无法复现。根因是 .app 比命令行多出的环境差异：① PyInstaller 打包后 httpx 的 CA 证书路径可能丢失 ② .app 由 launchd 启动不继承 shell 环境变量，httpx 又不读 macOS 系统代理。解决：`_build_http_client()` 显式用 `certifi.where()` 建 SSL context + `urllib.request.getproxies()` 读系统代理注入 httpx.Client，整体包 try/except 降级；错误信息按 SSL/DNS/拒绝/超时分类。GUI 工具的真实运行形态是 .app，命令行测通不代表打包环境 OK。
19. **QSettings domain 不稳定**：macOS 上 `QSettings('融景','RongJing')` 落到哪个 plist 受进程 bundle identifier 影响——命令行跑落 `org.python.python.RongJing.plist`，打包 .app 落另一个。用户跨版本/跨运行方式可能「配置丢失」。未根治；根治方案是 `main.py` 启动时 `QCoreApplication.setOrganizationDomain()` 设固定值 + 迁移旧配置。
20. **打包入口不要要求用户记命令**：`打包融景.command` 只保留为终端备用入口；面向用户优先提供 `打包融景启动器.app`，双击后自动打开 Terminal 执行 `scripts/package_rongjing.sh`。
21. **冻结 app 里禁止把 sys._MEIPASS 顶到 sys.path[0]**：`main.py` 开头的 `sys.path.insert(0, 仓库目录)` 在打包 .app 里会解析成 `Frameworks` 目录（含 cv2 源码包目录），把 cv2 引导器插到 sys.path[1] 的二进制目录遮蔽掉，触发 `recursion is detected during loading of "cv2"`，自动识别静默失效（界面只报「未能识别」，与算法失败无法区分）。修复：`if not getattr(sys, "frozen", False)` 时才 insert。配套防线：`screen_detector.py` 识别失败落盘 `detect_error.log`；`RONGJING_DETECT_SELFTEST=<图片路径>` 环境变量可在打包 app 里无头跑识别自测（结果写 `detect_selftest.txt`），每次改打包配置后应跑一次。
22. **绿幕背景没有光照信息可继承**：实拍质感滤镜最初的设计是「从背景图原屏幕区域取低频当光照层」，但本项目主力路径是 AI 生成的绿幕背景——实测 63 个模板，绿幕区亮度 p5-p95 只差 2-3 级，是完全均匀的纯色，可继承的信息为零。所以 `_build_light` 必须双路：绿幕/纯色走「从屏幕外圈环带（四边形缩放 1.02→1.25 之间）线性拟合环境光方向与色温」的程序化重建路径。判路时**绿幕一票判定**（G 显著大于 R/B 的像素占比 >0.6），不要只依赖亮度标准差。
23. **判路判据不能建立在模糊结果上**：`_build_light` 早期版本用「全图大核高斯模糊后在屏内取标准差」判断原屏有没有光照，但模糊核是屏幕跨度的 6%，会把屏幕外的红色横幅、绿黑板糊进屏内，绿幕模板因此被误判成「有真实光照」而走了继承路径，再从被污染的低频里取 RGB 比值，直接在 PPT 白底上拉出粉/青彩色渐变。判据只看屏幕内部的**原始像素**。同理，继承路径取低频前必须先把屏幕外区域填成屏内均值，否则同样被污染。
24. **环带采样出的色温/梯度必须限幅**：环带常落在红色横幅、绿黑板这类高饱和物体上，原始 RGB 比值可达 ±40%、亮度梯度可达 100+ 级，照搬会把屏幕白底整片染色、或把一侧压成黑块。色温钳到 ±5%、梯度归一化到屏幕跨度后钳到 ±10%。
25. **「暗淡」不是整体压暗，是两端一起收窄**：滤镜第一版用「整体曝光下压 + 黑位抬升 + 对比压缩」三个系数，前两者方向相反、实测互相抵消（屏幕中心区只压暗 3 级），用户反馈「不够像随手拍」。改为直接给黑白位映射 `out = black + out*(white-black)/255`（白 255→216、黑 0→22）后观感才对。堆多个语义重叠的系数不如给一个物理意义明确的映射。
26. **滤镜层不要按整图存储和运算**：`precompute_realism` 第一版把 mask/light/12张噪点都按背景图整图存，单模板实测 396 MB（噪点占 297 MB），批量跑多模板会吃光内存；`apply_realism` 也在整图上运算，单帧 144 ms，视频导出会被拖垮。改为①所有层只裁到屏幕外接框（外扩 8px 给羽化和模糊留余量）②噪点只存 512×512 小块、用时平铺并按 frame_index 滚动错位。实测 396 MB → 47.5 MB、144 ms → 80 ms，输出等价。
27. **视频路径的噪点必须逐帧变、光照层必须逐帧不变**：`apply_realism(img, cache, frame_index)` 的 `frame_index` 只切换噪点块；固定噪点在视频里看起来像镜头脏了，而不是高 ISO。`VideoRunner` 里要把 embed + apply_realism 包成一个函数一起提交进线程池，不要放在 `_drain` 里串行做（会拖慢三段流水线）。
28. **滤镜参数必须用大面积白底源图校准，不能只看彩色内容**：实拍质感滤镜的白点和色温偏移在彩色 PPT 上完全看不出问题，换成大面积白底的源图（聊天窗口、文档、白底封面）立刻暴露——白点压到 216 时整页发灰显脏，色温偏移 ±5% 在白底上直接显成一片黄绿（同样的偏移在彩色内容上无感）。收到 232 和 ±3.5% 后才对。**调完滤镜参数务必用白底图复验一遍**，彩色图看着没问题不代表没问题。
29. **实拍质感优先在生成端解决，不在合成端模拟**：曾在滤镜里加过程序化「屏幕表面脏污」层（多尺度低频云斑模拟指纹/擦拭痕/浮尘），技术上跑通了（乘性系数场叠进光照层，亮区自动明显、暗区自动隐形，零额外逐帧开销），但**实际观感不稳**——合成出来的脏稍重就糊成一片灰、稍轻又看不出来，难以控制，已移除。改为在 `core/bg_prompt.py` 的开头基调里让 AI 直接把「老旧 iPhone 随手拍」质感画进背景图：AI 画出来的暗部、噪点、光照是真实自洽的，且滤镜第一层是从背景反推光照的，背景变暗后光照层会自动跟着暗，两者天然配合。**判断原则**：能让生成端画出来的真实感，不要在后期用程序合成——后者永远在「太假」和「看不见」之间摇摆。
30. **给 AI 的提示词一律正向写**：被否定的概念会被带进注意力，禁得越具体越容易被复现。表达「轻微晃动但画面清晰」要写 `handheld with a slight natural tilt, while the subject itself stays sharp and in focus`，不能写 `not blurry`。
31. **绿幕上没有信息可正片叠底，这是提示词自己造成的**：专业绿幕合成会用「绿幕区除以纯绿基准」提取出布料褶皱阴影、灯光不均、物体投影，做成乘性图层叠回新内容（正片叠底），光照自然继承。本项目用不上这招，因为提示词明确要求绿幕 `perfectly flat and uniform, no gradient, no reflection`——实测 63 个模板绿幕区 p5-p95 只差 2-3 级，除出来是常数 1.0。**这是可逆的设计选择**：若改提示词让 AI 把屏幕光照渐变画到绿幕上，就能启用正片叠底路径拿到真实光照；代价是绿幕带明暗后颜色分割检测的余量变小，需要重新验证检测通过率。
32. **环境亮度自适应的基准不能取环带**：滤镜早期只用环带的梯度和色温、没用绝对亮度，导致背景环境压暗 60% 时合成屏幕亮度纹丝不动，屏幕/环境亮度比从 1.66 飙到 4.14，屏幕像自己在发光。加自适应时第一版拿 `_ring_stats` 的环带亮度当基准，结果正常亮度场景也被误压暗 20 级——因为环带贴着屏幕外沿，常整圈落在黑色边框上（实测同模板环带 60 vs 非屏幕区 105）。基准必须取**整个屏幕外区域**。跟随用次幂曲线（gamma 0.45）软化并设下限 0.68：屏幕本来就该比环境亮，要跟随的是「亮多少」，且不能把内容压到看不清。
33. **给 AI 加全局风格基调时要检查它会不会波及关键功能区**：实拍基调里的「整体偏暗」（`slightly underexposed`）会连带把绿幕也画暗，而绿幕靠颜色分割出角点建模板，变暗会拉低 G 与 R/B 的比值、削弱检测。必须在绿幕分支单独加豁免句，把绿幕区从「暗」里摘出来：环境可以暗，绿幕保持明亮饱和。加任何全局风格词前，先过一遍「哪些区域是被下游算法依赖的」。
31. **探测「编码器名字存在」≠「能打开」**：`VideoRunner` 探测 VideoToolbox 用过 `av.codec.Codec("h264_videotoolbox","w")`，但这只查 FFmpeg 编码器注册表，不调 `avcodec_open2`。打包 .app 后名字注册了（探测通过）但实际打开失败 → 编码崩溃。修复：`_probe_videotoolbox()` 真正 `CodecContext.create(...) + .open()` 一次（这步才调 `avcodec_open2`），能打开才算可用。原则：探测要探到「真正会失败的那一步」，不能探一个早于失败点的代理指标。注意 PyAV `CodecContext` 无公开 `.close()`。

---

## 发布流程（每次功能更新后）

> **⚠️ 对话结束前必须提醒用户运行同步脚本！**

1. 代码改完、本地 `python3 main.py` 验证正常
2. 双击 `同步到GitHub.command`（自动完成：本地打包 Mac → push 代码 → 上传 Release）
3. 等 10-15 分钟后去 [Actions 页面](https://github.com/xiwenran/rongjing/actions) 下载 Windows 包

GitHub Releases Mac 下载地址：https://github.com/xiwenran/rongjing/releases/latest

---

## 项目专属护栏（全局规则外的补充）

- 暂无。
- 通用护栏（冷眼审查 / 圆桌 / Obsidian 捕获 / 脱敏 / 规则同步）均以全局 CLAUDE.md 为准，本文件不再重抄，源头改则处处改。
