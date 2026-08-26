# PPT 场景合成工具（融景）— Codex 项目说明

> **适用环境**：Codex Mac 桌面端 / CLI
> **维护约定**：每次对话结束前，必须主动将本次新增的功能、修复的 Bug、发现的约定追加到本文件对应章节。

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

> 架构说明：PyInstaller 生成的包只能在同架构 Mac 运行（arm64 = Apple Silicon，x86_64 = Intel）。DMG 文件名含架构后缀。
> 首次运行未签名 .app：右键点击 → 打开 → 点击「打开」（不能直接双击，会被 Gatekeeper 拦截）。

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
- 输出尺寸默认按导出页全局分辨率处理：默认 1920 宽并保持比例；选择「原始 / 模板尺寸」时回到 `template.output_width/height`，若模板为 0 则使用背景图原始尺寸
- **速度优化**：每个模板调用一次 `precompute_template_cache(bg_img, points)` 预计算 mask 和背景数组（RGB，3通道），再对所有图片调用 `embed_image_pil_fast(ppt_img, cache)`；使用 BILINEAR 替代 BICUBIC（2-3× 速度提升）

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
- [x] 去水印模块拼图式布局（左侧设置栏 / 右侧处理预览 / 底部输出与开始条）
- [x] 图片批量导出增加分辨率选项，默认 1920 宽，解决 1024×768 背景导致成品偏低清晰度的问题
- [x] 模板模型新增分类、合成类型和渲染预设，旧模板分类自动映射为「教室场景 / 文档纸张 / 台式机电脑」
- [x] 新增文档纸张合成类型 `document_paper`，默认固定使用「真实纸感 + 清晰」打印融合效果；旧 `clear` / `warm` 参数自动归一
- [x] 模板配置页按「教室场景 / 台式机电脑 / 笔记本室内 / 文档纸张 / 自定义场景」提供分类预设，并支持自定义分类输入；合成类型不再展示，按分类自动推断
- [x] AI 背景页新增背景场景：教室场景、台式机电脑、笔记本室内、文档纸张、自定义场景；生成张数用数字控件自定义
- [x] 批量图片导出对 `document_paper` 模板使用文档纸面融合算法，输出结构保持不变；视频模式只支持 `screen` 模板
- [x] 跨分类允许同名模板，模板 JSON 使用隐藏 key 区分；列表按分类排序，CLI `list-templates` 暴露 key，批量导出按 key 选择
- [x] 文档纸张合成改为整页实拍光照贴合：保留完整页面，叠加背景光照、纸面细纹、轻微噪声和模糊，不做白底透明化
- [x] 文档纸张 `paper` 预设切换为「打印实拍」模式：不再生成新纸层，只保留背景纸张本身，把源图用正片叠底、轻微退色和纸面噪声融合成打印效果
- [x] 模板库按分类显示组头，模板列表滚轮减速，避免轻滑跳过多行
- [x] 批量导出模板选择弹窗支持按分类组勾选，分组标题可一键选中/取消该组模板
- [x] 拼图新增「上大图」布局：顶部 1 张大图，下方按行列数排小图，自动拆分按「1 + 行×列」张/页计算
- [x] 普通模板库与拼图模板库的删除按钮显性改为「批量删除」，支持 Ctrl/Shift 多选后一次删除
- [x] 设置页备份导出/导入扩展为普通模板、背景图、拼图模板和软件设置；导入支持合并/覆盖并恢复设置项
- [x] 设置页新增缓存清理：统计缓存、按保留天数手动清理、启动时自动清理开关；范围限 AI 生成历史、拼图 PPT 导出缓存和孤立背景图
- [x] 拼图页输入入口简化为「导入文件夹 / 导入文件」；文件夹自动识别单文件夹或批量子文件夹，文件入口支持多图和单个/多个 PPT
- [x] 拼图导出按来源名称自动建立子文件夹，避免输出总目录不变时新 PPT 覆盖旧成品
- [x] 拼图预览刷新做防抖和图片缓存，减少调整行列数时的卡顿
- [x] 拼图多 PPT / 多文件夹切换保留每个来源的布局、行列、排除页、预览页和输出张数，并用于对应来源的批量导出
- [x] 拼图多来源时支持「导出当前」和「导出全部」，单来源时只显示「开始导出」
- [x] 拼图行列数、快捷预设、自动适配和背景色变化会直接调度右侧预览刷新
- [x] 拼图布局行列或快捷预设变化后自动重算拆分张数；重复导出成功后按 manifest 清理本软件生成的旧图残留
- [x] 拼图同名 PPT 缓存按路径哈希隔离；「导出全部」点击时冻结每份来源的配置快照
- [x] 批量导出扫描跳过隐藏文件 / macOS `._` 元数据文件，运行时遇到无法识别的图片会跳过并继续处理
- [x] Windows Actions 上传 Release 改为优先附加到最新 Release，避免旧 `RELEASE_TAG` 把新 Windows 包传回旧版本

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
12. **macOS 26 Tahoe beta 兼容性**：Mac 版本在本机用 `打包融景启动器.app` 或 `bash 打包融景.command` 打包，Windows 版本用 GitHub Actions
13. **模板数据目录**：`main.py` 中 `get_data_dir()` 返回系统级目录（`~/Library/Application Support/融景/templates/`），与 app bundle 完全分离
14. **去水印预览缩放**：右侧预览图不要直接写死尺寸，需跟随 `QLabel` 尺寸变化重新 `scaled(..., KeepAspectRatio)`，否则窗口缩放后预览会发虚或留大片空白
15. **去水印来源按钮语义**：顶部「单张 / 多张图」「批量文件夹」是用户第一眼看到的入口，点击时必须直接打开对应选择器，不能只切换状态；强度选项不要把子控件塞进空文本 `QPushButton`，否则按钮高度按空文本计算，文字可能被裁掉
16. **批次差异化高档性能**：高档差异化的主要耗时在后处理，不在透视合成本身；`strip_metadata()` 不要用逐像素 `getdata()` 搬运，噪声用 `float32` 路径，轻微缩放/旋转优先用 `Image.BILINEAR`，避免 `Image.BICUBIC` 把批量导出拖慢
17. **模板分类与合成类型分离**：`category` 负责用户可见分组；UI 用「预设下拉 + 自定义输入」展示分类，不展示 `template_type`，保存时按分类自动推断，`文档纸张` → `document_paper`，其他分类 → `screen`
18. **文档纸张模板边界**：视频模式只允许 screen 模板；document_paper 以可读性和自然纸面融合为优先，不做 OCR、文字识别或内容修复。注意不要把白底透明化或只提取墨迹，否则会出现灰脏、嵌入感强的失败效果；正确目标是整张页面像被实拍到纸面上一样继承光照和纹理。
19. **PyInstaller 不要 `--collect-all cv2`**：自动角点识别只需要懒加载 `cv2`，打包脚本保留 `--hidden-import cv2` 即可；`--collect-all cv2` 会让 PyInstaller 收集 OpenCV 全量资源，容易在 macOS 上被 `Killed: 9` 杀掉
20. **打包入口不要要求用户记命令**：`打包融景.command` 只保留为终端备用入口；面向用户优先提供 `打包融景启动器.app`，双击后自动打开 Terminal 执行 `scripts/package_rongjing.sh`
21. **AI 背景返回格式不能只认 `.data`**：兼容 OpenAI 或第三方 Base URL 时，图片结果可能是 SDK 对象、dict、JSON 字符串，或放在 `output[].result`；解析时要同时兼容 `b64_json` / `result` / `url`，否则会出现 `'str' object has no attribute 'data'` 或空结果。
22. **第三方 AI 连接地址通常要到 `/v1`**：用户给 `{"key":"...","url":"https://api.example.com"}` 这类连接 JSON 时，设置页要自动拆出 key/url，并把根域名规范成 `https://api.example.com/v1`；否则 OpenAI SDK 会打到错误路径，接口可能返回纯文本/网页。
23. **模板同名后的 CLI 选择**：模板显示名可以跨分类重复，但脚本和 CLI 必须使用 `list-templates` 返回的 `key` 精确选择；如果用户只传显示名且存在多个分类重名，CLI 要报错并列出可用 key，不能静默选错模板。
24. **文档实拍效果优先打印融合而不是嵌入纸层**：`document_paper` 的 `paper` 预设不要生成额外白边、纸层或接触阴影；正确做法是保留背景纸张本身，让源图以正片叠底、轻微退色、纸面噪声和边缘极窄渐隐融合进去。白色区域应接近背景纸张不变，彩色和黑色区域像印刷墨迹。
25. **文档纸张不再暴露预设选择**：界面不显示「清晰优先 / 真实纸感 / 暖光纸面」按钮；文档模板统一使用真实纸感且保持清晰的打印融合模式，旧模板里保存的 `clear` 或 `warm` 读取后按 `paper` 处理。
26. **模板分组交互**：模板库和批量模板选择都要按分类分组展示；批量选择弹窗的分组标题本身可勾选，用来选择整组模板。模板库列表和外层配置侧栏都要使用小像素步长滚动，避免高精度鼠标轻滑跳过过多模板或配置项。
27. **拼图上大图布局容量**：`hero` 布局的每张容量不是 `rows * cols`，而是顶部大图 1 张 + 下方缩略图 `rows * cols` 张；自动拆分、预览和批量导出必须都用 `CollageTemplate.total_cells`，避免预览和实际导出分组不一致。
28. **缓存清理边界**：缓存清理只能处理 AI 生成历史、拼图 PPT 导出缓存、未被任何模板 JSON 引用的背景图；正式模板 JSON、仍被引用的背景图和用户选择的输出目录不属于缓存。自动清理默认关闭，用户开启后才在启动时按保留天数执行。
29. **拼图输出目录防覆盖**：拼图页的输出文件夹是总目录，实际成品必须写入 `总目录/来源名称/`；单个 PPT 用 PPT 文件名，批量文件夹用子文件夹名，多图导入用固定来源名，避免用户换 PPT 但不改输出目录时覆盖上一批成品。
30. **拼图预览性能**：行列、间距、边距等高频控件只调度防抖预览刷新，不直接同步重算大图；右侧预览使用内存里的缩小版页面图，避免同一批图片在切换布局时反复从硬盘读原图。正式导出仍读取原图，不受预览缓存降采样影响。
31. **Windows Release 上传目标**：GitHub Actions 自动打包 Windows 后优先查询最新 Release 并上传 ZIP，`RELEASE_TAG` 只做兜底；否则当本地未提交 `RELEASE_TAG` 或普通 `git push` 触发 Actions 时，新 Windows 包会被旧 tag 带回旧 Release。
32. **拼图来源状态**：多 PPT / 多文件夹列表切换前必须保存当前来源状态，切回时恢复布局、行列、间距、边距、比例、背景色、排除页、预览页和输出张数；批量导出要按每个来源自己的状态运行，不能只套用当前选中来源的配置。
33. **拼图导出按钮文案**：多来源时底部显示「导出当前」和「导出全部」，明确当前来源与整批来源的范围；单来源时只显示「开始导出」，避免把「全部」误读成当前文件内全部页面。
34. **拼图预览触发**：行列数、快捷预设、自动适配、背景色和输出张数变化都要直接触发 `_refresh_state()` 或预览调度；不能只依赖 `config_changed` 的间接连接，否则高频控件或信号阻断路径会出现右侧预览不更新。
35. **拼图重复导出**：同一来源重复导出时，布局容量变化必须先重算自动拆分张数；成功写出新结果后只清理 `.rongjing_collage_manifest.json` 中记录过、且本轮未写出的旧成品，普通用户文件不在清理范围内。清理放在成功导出后，避免失败时先删旧结果。
36. **同名 PPT 隔离**：PPT 导出图片缓存目录必须包含源文件路径哈希，不能只用文件名 stem；同批同名 PPT 的用户可见来源名要自动加序号，避免状态、缓存和输出目录互相覆盖。
37. **导出全部快照**：点击「导出全部」时先把每份来源的配置、排除页和输出张数冻结到队列，后续逐份处理只读队列快照；不能在运行中继续读取可变 UI 状态。
38. **批量导出坏图容错**：扫描图片文件夹时跳过隐藏文件，尤其是外置盘常见的 macOS `._xxx.jpg` AppleDouble 元数据文件；运行时仍要捕获 `UnidentifiedImageError` / `OSError`，跳过坏图并在进度和完成文案中说明，不能让一张坏图中断整批。

---

## 发布流程

> **对话结束前必须提醒用户运行同步脚本！**

1. 代码改完、本地 `python3 main.py` 验证正常
2. 双击 `同步到GitHub.command`（自动：本地打包 Mac → push 代码 → 上传 Release）
3. 等 10-15 分钟后去 Actions 页面下载 Windows 包

GitHub Releases Mac 下载地址：https://github.com/xiwenran/rongjing/releases/latest

---

## 项目专属护栏（全局规则外的补充）

- 暂无。
- 通用护栏（冷眼审查 / 圆桌 / Obsidian 捕获 / 脱敏 / 规则同步）均以全局 AGENTS.md 为准，本文件不再重抄，源头改则处处改。
