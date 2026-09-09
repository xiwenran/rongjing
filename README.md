# 融景

> 将 PPT 截图或视频录屏，通过透视变换嵌入到实拍背景图的屏幕区域，批量生成合成图片或视频。

**典型场景**：把 PPT 内容嵌入教室大屏幕背景照片/视频，制作真实感课程素材。

---

## 下载安装

前往 [**Releases 页面**](../../releases/latest) 下载最新版本：

| 平台 | 文件 | 说明 |
|------|------|------|
| Mac（Apple Silicon）| `融景_arm64.dmg` | M1/M2/M3/M4 芯片 |
| Mac（Intel） | `融景_x86_64.dmg` | 老款 Intel Mac |
| Windows | `融景_windows_x64.zip` | 解压后运行 `融景.exe` |

> **Mac 首次打开**：右键点击 .app → 打开 → 点击「打开」（绕过系统安全提示，只需操作一次）

---

## 功能

### 模板配置
- 在背景图上点击标注屏幕的 4 个角点（左上→右上→右下→左下），角点可拖拽微调
- 模板保存为 JSON 文件，存储在系统目录（更新 app 不丢失数据）
  - Mac：`~/Library/Application Support/融景/templates/`
  - Windows：`%APPDATA%\融景\templates\`
- 支持创建多个模板（多场景、多设备并行使用）
- 可选配置输出尺寸；默认使用背景图原始尺寸

### 批量合成图片（两种模式）

**图片文件夹模式**
- 选择主文件夹，自动扫描子文件夹结构
- 每个子文件夹视为一组（子文件夹名作为分组名）；若无子文件夹，则以所选文件夹名作为分组名
- 输出结构：`输出目录 / 分组名 / 模板名称 / 1.png, 2.png, ...`

**图片批量模式**
- 手动多选图片文件（可跨文件夹）
- 输出结构：`输出目录 / 图片批量 / 模板名称 / 1.png, 2.png, ...`

共同特性：
- 每组可独立选择多个模板，一次处理输出所有模板的合成结果
- 「全部应用」一键为所有组统一设置模板
- 输出格式：PNG（无损）或 JPEG（quality=95）
- 输出分辨率可选：默认按 1920 宽导出并保持画面比例，也可切回原始 / 模板尺寸

### 批量合成视频
- 视频入口同时接收真实视频、图片文件和图片文件夹
- 真实视频继续由 `VideoRunner` 逐帧嵌入场景模板；自动保留原始音频（重编码为 AAC）
- 单张图片生成静态视频；多张图片在 Mac 上优先调用系统 Core Image 生成真实曲面卷页、纸张背面、折痕与阴影，非 Mac 或助手不可用时回退到 CPU 平面翻页
- 页面序列视频复用每页静态合成结果；每组相邻页最多渲染 8 张独立曲面帧，最终视频按所选帧率映射，避免重复计算
- 正式页面视频复用批量图片分辨率规则：`0` 为原始/模板尺寸，默认宽度 1920，也可选 2560 或 3840；翻页预览固定宽度 960，真实视频不使用该规则
- 背景缩放使用 LANCZOS；目标尺寸与背景一致时不重复缩放
- Mac 优先使用 VideoToolbox 硬件编码，码率限制在 8–20 Mbps；探测或打开失败时回退到 libx264 CRF 17
- 翻页方向默认为从右向左，也可选择从左向右；正式导出与预览、CPU 与 Core Image 使用同一方向设置
- 页面图片模式提供「预览翻页」按钮，使用前两张有效页面和当前所选的首个屏幕模板，在 900×560 应用内循环播放弹窗中预览；支持播放、暂停、重新播放和关闭
- 预览视频以唯一 MP4 平铺写入专用 `page_preview_cache`；软件启动时只清理其直属层超过 24 小时的普通 MP4，目录、符号链接和其他文件保持不变
- 页面视频支持不配乐、固定配乐和随机配乐，默认音量 35%；从音频第 0 秒开始使用，短音频每轮也从第 0 秒循环同一首曲目，并编码为 48 kHz 双声道 AAC
- 音乐库固定存放于 App Data 的 `融景/music/`，可从音频、文件夹或视频导入；视频只提取首条音轨，不保存画面，重复内容按 SHA-256 去重
- 随机模式实际使用的曲目会写入完成回执；真实视频继续保留原声，不使用页面视频 BGM 设置
- 翻页预览生成的视频包含 BGM，但当前应用内预览弹窗只播放画面，不播放声音
- 页面序列采用固定 FPS 和递增 PTS 流式编码，无需一次性把全部帧载入内存
- 支持视频格式：`.mp4` `.mov` `.avi` `.mkv` `.m4v` `.wmv`

### 资料导出

- 独立「资料导出」页面支持把 PPT 或 Word 批量导出为 PNG 页面
- PPT 与 Word 严格按所选类型扫描；PPT 模式不接收 Word，Word 模式不接收 PPT
- CLI 使用 `export-material` 子命令，可指定平台原生后端或 LibreOffice
- macOS PowerPoint 使用固定 `~/Documents/融景Office中转/`：复制原 PPT 到中转目录，原件不移动；PowerPoint 从该目录打开，并把 PDF 输出到同一目录
- 每次运行以 manifest 记录本批副本和 PDF；成功或正常失败时精确清理本批文件，程序崩溃遗留由启动清理在严格超过 24 小时后处理
- 固定根权限为 `0700`；复制后核对 SHA-256 与大小，清理使用 no-follow、`dir_fd`、quarantine 及 inode + size 身份校验
- 同一文件夹或多个文件夹首次使用均只需授权该固定根一次；macOS 是否持续保留授权由用户在真机验证
- LibreOffice 导出流程保持不变

### 统一输入与输出规则

- 所有扫描入口先过滤隐藏文件、AppleDouble `._*`、Office 临时文件 `~$*` 和非文件项，再进行排序、计数、封面选择、manifest 生成和任务清单组装
- 拼图、图片合成、真实视频和资料导出每次运行都会创建新的来源目录；同名目录已存在时依次使用 `_2`、`_3`，旧产物不覆盖、不清理
- `material-exporter` 与 `ppt-notes-pipeline` 两个现役 Skill 已归入融景维护；旧 `ppt-batch-tool` 项目暂停维护

### 其他
- 路径记忆：每个文件选择器独立记忆上次使用路径，跨会话持久化
- 实时预览：标注角点后可加载 PPT 图片实时查看嵌入效果
- 取消处理：合成进行中可随时点击「取消」中止

---

## 技术实现

| 模块 | 技术 | 说明 |
|------|------|------|
| 界面 | PyQt6 | 跨平台 GUI，QThread 异步处理 |
| 图像处理 | Pillow + NumPy | 透视变换（`Image.PERSPECTIVE`）、mask 羽化（MinFilter + GaussianBlur）、Alpha 混合 |
| 视频处理 | PyAV | libx264 视频编码 + AAC 音频，无需安装 ffmpeg |
| Mac 曲面翻页 | Swift + Core Image | `CIPageCurlWithShadowTransition` 批量渲染；助手随 Mac App 打包 |
| Mac 视频编码 | VideoToolbox | 探测到编码器可真正打开时使用，否则回退 libx264 |
| 性能优化 | ThreadPoolExecutor | 视频帧多线程并行处理（PIL/NumPy 的 C 实现释放 GIL，真正并行） |
| 缓存优化 | 预计算 cache | mask、背景数组、透视系数在处理前一次性计算，所有帧复用 |
| 路径持久化 | QSettings | 记忆每个选择器的上次路径 |
| 打包 | PyInstaller | Mac 本机打包；Windows 由 GitHub Actions 自动构建 |

## 当前验证边界

- P1 已在 Mac 源码环境确认系统 `CIPageCurlWithShadowTransition` 可离屏输出首、中、尾 3 帧；首尾与源页面一致，中间帧可见曲面卷边、纸张背面、折痕与阴影。
- P2 已由正式页面视频流程确认实际使用 Core Image 与 VideoToolbox，样本为 H.264、10 FPS、640×360、10 帧且 PTS 递增；静态页每个模板每页只合成一次，每组相邻页只批量调用一次助手，独立曲面帧不超过 8 张。
- P3 已用 offscreen 界面检查确认「预览翻页」仅在页面图片输入时显示，并使用前两张有效页面与首个屏幕模板；后续 `fefe3d3` 增加 900×560 应用内循环播放器与双向翻页，`db45103` 将预览缓存收紧为专用目录直属普通 MP4 的 24 小时安全清理。
- M1–M3 已验证音乐库导入、固定/随机/不配乐、35% 音量、零秒起播、同曲从零秒循环与 48 kHz 双声道 AAC；AAC 尾部可能出现编码器填充，音乐库索引尚未使用持久 sidecar，这两项为非阻断风险。
- 清晰度与零秒配乐短样本回读为 1920×1440 / 1024×768，编码前后 ROI 边缘能量 9.710 / 9.887；前 200 ms RMS 0.0963，与源开头相关系数 0.9998。真机文字观感与冻结 App 未验证。
- Mac 打包脚本现会先编译 Swift 助手，再把可执行文件加入 App；助手编译失败会直接停止打包。本轮尚未实际生成冻结 App，也未完成冻结包真机与 GUI 交互验证。
- Windows 不打包 Core Image 助手，页面翻页继续使用 CPU 回退；Windows COM 资料导出仍未验证。

### 透视变换算法细节
1. 用 `ImageDraw.polygon` 生成四边形 mask
2. Inward feathering：先 3×3 腐蚀（`MinFilter`）再高斯模糊，clip 到原始 mask 内（消除边缘插值伪影）
3. 用 PIL `Image.PERSPECTIVE` + BILINEAR 插值做透视变换（比 BICUBIC 快 2-3×，透视后质量无明显差异）
4. Alpha 混合：`result = (1 - mask) × bg + mask × warped`

---

## 输出文件命名规则

| 模式 | 输出路径格式 |
|------|------------|
| 图片文件夹 | `{输出目录}/{子文件夹名}/{模板名}/{序号}.png` |
| 图片批量 | `{输出目录}/图片批量/{模板名}/{序号}.png` |
| 视频 | `{输出目录}/{视频名}/{模板名}/{视频名}.mp4` |
| 资料导出 | `{输出目录}/{资料名}/{页码}.png` |

- 根目录直接图片使用所选文件夹名，不使用「根目录」占位名
- 所有来源目录均按不覆盖规则分配；重名时追加 `_2`、`_3`
- 图片序号从 `1` 开始，按自然顺序排列
- 扩展名：PNG 模式为 `.png`，JPEG 模式为 `.jpg`，视频固定为 `.mp4`

---

## 支持的文件格式

| 类型 | 格式 |
|------|------|
| 输入图片 | `.jpg` `.jpeg` `.png` `.bmp` `.webp` `.tiff` |
| 输入视频 | `.mp4` `.mov` `.avi` `.mkv` `.m4v` `.wmv` |
| 输入资料 | PPT：`.ppt` `.pptx` `.pps` `.ppsx`；Word：`.doc` `.docx` |
| 输出图片 | `.png`（无损）或 `.jpg`（quality=95） |
| 输出视频 | `.mp4`（H.264 + AAC） |

---

*Made with PyQt6 + Pillow + PyAV*

---

## 命令行工具（CLI）

除图形界面外，融景提供 `cli.py` 支持无界面批量处理，可由 Claude Code 等 AI 工具直接调用。

### 依赖

```bash
pip install Pillow numpy
```

### 列出可用模板

```bash
python3 cli.py list-templates
```

输出 JSON，包含模板 `key`、显示名称、分类与背景图信息。同名模板按分类区分时，后续命令优先使用 `key`。

### 批量合成图片

```bash
python3 cli.py process \
  --input <文件夹或图片路径...> \
  --templates <模板key或唯一模板名...> \
  --output <输出目录> \
  --format JPEG   # 或 PNG
```

**示例**：用模板 1、2、3 处理某文件夹下所有图片：

```bash
python3 cli.py process \
  --input ~/Desktop/ppt截图/ \
  --templates 1 2 3 \
  --output ~/Desktop/合成结果/
```

**输出结构**：`输出目录 / 模板key / 1.jpg, 2.jpg, ...`

### 注意

- App 的视频入口负责真实视频合成和图片页面序列视频；当前 CLI 子命令用于图片合成、拼图和资料导出
- 新建/编辑模板须在融景 App 内完成（需要可视化标注角点）
- 模板文件存储于 `~/Library/Application Support/融景/templates/`
- 跨分类允许同名模板；如果 CLI 提示名称重复，先运行 `list-templates` 查看 `key`，再用 `--templates <key>` 精确选择

### 导出 PPT 或 Word 资料

```bash
python3 cli.py export-material \
  --type ppt \
  --input <PPT文件或目录> \
  --output <输出根目录> \
  --max-pages 17 \
  --backend libreoffice
```

- `--type` 必须是 `ppt` 或 `word`，目录扫描只处理所选类型
- `--max-pages` 可写为兼容别名 `--max-slides`
- `--backend` 可选 `ppt_mac`、`ppt_com`、`word_mac`、`word_com`、`libreoffice`；省略时按当前平台自动选择并回退

### 当前验证边界

- 已验证：LibreOffice 导出 1 页 PPT 为 1440×1080 PNG；导出 1 页 Word 为 1224×1584 PNG，连续 2 次运行未覆盖旧目录
- 已验证：macOS PowerPoint 固定中转的复制、manifest 精确清理、24 小时崩溃残留清理及身份校验已通过定向验证和独立窄复核；功能提交为 `251d024`、安全加固为 `115bdf6`、源文件快照修复为 `3b31ad1`
- 已验证：页面序列视频为 H.264、10 FPS、96×64、10 帧，PTS 递增；offscreen GUI 与 41 项 Skill 链接检查通过
- 未验证：macOS 对固定中转根的持久授权、PowerPoint 真机运行、Windows COM、冻结 App 和 GUI 真机交互

---

## Claude Code Skill

已提供 `rongjing` Skill（`~/.claude/skills/rongjing/SKILL.md`），在 Claude Code 中可直接用自然语言触发批量合成：

> 「用模板1到5，把桌面上的图片文件夹合成，输出到Downloads」
