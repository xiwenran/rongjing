# 融景 — 功能需求清单与完成状态

> 记录所有已实现、计划中的功能，细化到文件命名、路径约定、交互行为等实现细节。

---

## 一、模板管理

### 1.1 模板创建
- [x] 点击「+ 新建」清空表单，进入新建状态
- [x] 必填字段：模板名称、背景图片路径、屏幕 4 个角点坐标
- [x] 保存时写入 JSON 文件，路径：`{data_dir}/templates/{模板名称}.json`
- [x] 保存成功后模板列表自动刷新，并高亮选中新保存的模板

### 1.2 模板编辑
- [x] 从列表选中模板后，左侧表单自动填充所有字段
- [x] 修改后点击「保存模板」覆盖原 JSON 文件（同名则覆盖）

### 1.3 模板删除
- [x] 点击「删除」弹出确认对话框，确认后删除对应 JSON 文件
- [x] 删除后列表自动刷新，清空当前表单

### 1.4 模板存储格式
- [x] 每个模板独立存为一个 JSON 文件
- [x] 文件名规则：`{模板名称}.json`（名称直接用于文件名，特殊字符由操作系统处理）
- [x] JSON 字段：`name`、`background_path`（绝对路径）、`screen_points`（4 个 [x,y] 坐标）、`output_width`、`output_height`
- [x] 存储位置与 app bundle 完全分离，更新 app 不丢数据：
  - Mac：`~/Library/Application Support/融景/templates/`
  - Windows：`%APPDATA%\融景\templates\`

### 1.5 模板列表 UI
- [x] 每个模板条目有浅灰背景条，多条目时易于区分
- [x] 选中态：浅绿色背景 `rgba(7,193,96,0.18)`
- [x] 列表放在圆角边框容器内（`border-radius: 10px`，白底灰边）

---

## 二、角点标注（画布）

- [x] 右侧 `CanvasWidget`：按比例缩放显示背景图
- [x] 左键依次点击放置 4 个角点，顺序：左上 → 右上 → 右下 → 左下（TL→TR→BR→BL）
- [x] 角点以彩色圆圈标记（4 色区分：红/绿/黄/蓝）
- [x] 已放置的角点可拖拽调整位置
- [x] 右键点击撤销最后一个角点
- [x] 点击「清除」按钮重置所有角点
- [x] 4 个角点均放置后，屏幕区域用黄色多边形轮廓线高亮显示
- [x] 角点坐标实时保存到模型（画布坐标 → 背景图原始坐标自动换算）

---

## 三、嵌入预览

- [x] 侧边栏「嵌入预览（可选）」区域：加载一张 PPT 图片实时查看效果
- [x] 预览在「保存模板」前即可使用（不要求先保存）
- [x] 预览图以缩略图形式显示在侧边栏，最大高度 200px
- [x] 背景图或角点变更后，预览自动刷新

---

## 四、批量导出 — 图片文件夹模式

### 4.1 输入
- [x] 选择一个主文件夹，支持两种目录结构：
  - **含子文件夹**：主文件夹下每个子文件夹视为一组，子文件夹名即为组名
  - **直接平铺**：主文件夹下直接放图片（无子文件夹），自动归组为 `(根目录)`
- [x] 扫描文件夹后，在表格中列出每组的子文件夹名和图片数量
- [x] 支持的图片格式：`.jpg` `.jpeg` `.png` `.bmp` `.webp` `.tiff`

### 4.2 模板选择（步骤 2）
- [x] 表格每行独立选择模板，可多选（支持同一组图片套用多个模板）
- [x] 点击「选择模板」按钮弹出 `TemplatePickerDialog`，含全选/全不选
- [x] 「全部应用」一键为所有行统一设置同一批模板

### 4.3 输出
- [x] 输出目录结构：`{输出根目录}/{子文件夹名}/{模板名称}/{序号}.png`
  - 示例：`output/课件A/教室1/1.png`、`output/课件A/教室1/2.png`
- [x] 序号从 `1` 开始，按文件名字典序排列
- [x] 支持 PNG（默认）和 JPEG 两种输出格式
  - PNG：直接保存，无损
  - JPEG：先转 RGB，`quality=95` 保存
- [x] 文件扩展名：`.png` 或 `.jpg`（由输出格式决定）
- [x] 输出尺寸：使用模板配置的 `output_width × output_height`；若为 0 则使用背景图原始尺寸

---

## 五、批量导出 — 图片批量模式

- [x] 手动多选图片文件（可跨文件夹）
- [x] 选中后显示「已选择 N 张图片」
- [x] 统一选择模板（同文件夹模式的模板选择 UI，组名固定为 `图片批量`）
- [x] 输出目录结构：`{输出根目录}/图片批量/{模板名称}/{序号}.png`
- [x] 序号按选择顺序排列，从 `1` 开始

---

## 六、批量导出 — 视频文件模式

### 6.1 输入
- [x] 多选视频文件，支持格式：`.mp4` `.mov` `.avi` `.mkv` `.m4v` `.wmv`
- [x] 表格显示视频文件名、时长/帧数、已选模板

### 6.2 处理逻辑
- [x] 视频每帧 = PPT 内容（被嵌入的内容），模板背景图 = 场景容器
- [x] 使用 PyAV 解码输入视频，逐帧进行透视变换嵌入
- [x] 多线程并行处理帧（`ThreadPoolExecutor`，最多 `min(6, CPU核数-1)` 线程）
- [x] 保留原始音频（重编码为 AAC，支持多音轨）
- [x] 视频编码：libx264，`crf=18`，`preset=veryfast`
- [x] 输出分辨率 = 背景图尺寸（`bg_w × bg_h`）

### 6.3 输出
- [x] 输出目录结构：`{输出根目录}/{视频文件名（无扩展名）}/{模板名称}/{视频文件名}.mp4`
  - 示例：`output/录屏01/教室1/录屏01.mp4`
- [x] 输出格式固定为 `.mp4`（H.264 + AAC）
- [x] 视频模式下隐藏「图片格式」选择器（PNG/JPEG 选项不显示）

---

## 七、透视变换核心算法

- [x] 纯 PIL/NumPy 实现，无 OpenCV 依赖（解决 PyInstaller 打包兼容性问题）
- [x] `precompute_template_cache(bg_img, points, feather=2, ppt_size=None)`：
  - 背景图转 RGB（3 通道，节省 25% 内存）
  - 用 `ImageDraw.polygon` 绘制四边形 mask
  - Inward feathering：先 `ImageFilter.MinFilter(3)` 腐蚀，再 `GaussianBlur(feather)` 模糊，clip 到原始 mask 内
  - 可选预计算透视系数（视频模式下利用流元数据预填充，使 cache 完全只读/线程安全）
- [x] `embed_image_pil_fast(ppt_img, cache)`：
  - PIL `Image.PERSPECTIVE` + BILINEAR 插值（比 BICUBIC 快 2-3×，透视后质量差异不可感知）
  - RGB 3 通道混合（`result = (1-mask)*bg + mask*warped`）
  - 透视系数按源分辨率懒缓存（图片批量）或预计算（视频）

---

## 八、性能

- [x] 图片批量：每个模板预计算一次 cache，所有图片复用（避免重复 mask 计算）
- [x] 视频：多线程并行帧处理，滑动窗口 deque 保证顺序编码，内存占用可控
- [x] 视频编码预设 `veryfast`（比 `fast` 快约 30-50%，画质无明显差异）

---

## 九、用户体验

### 9.1 路径记忆（跨会话持久化）
- [x] 每个文件/文件夹选择器独立记忆上次使用路径，下次打开默认从该路径开始
- [x] 使用 `QSettings("xhsbj", "PPTComposer")` 持久化

| 键名 | 用途 |
|------|------|
| `last_dir_bg` | 模板背景图选择器 |
| `last_dir_preview` | 预览 PPT 图选择器 |
| `last_dir_input` | 图片主文件夹选择器 |
| `last_dir_output` | 输出文件夹选择器 |
| `last_dir_images` | 图片批量文件选择器 |
| `last_dir_videos` | 视频文件选择器 |

### 9.2 文件选择器
- [x] macOS：优先使用 `osascript` 调起原生 Finder 选择器（支持 `default location`）
- [x] macOS 失败 / Windows：回退到 `QFileDialog`
- [x] Windows 不使用 `DontUseNativeDialog`（避免黑色背景 bug）

### 9.3 进度反馈
- [x] 合成进行中显示进度条（`done/total` 百分比）
- [x] 状态文字实时显示当前处理文件名
- [x] 视频模式每 30 帧更新一次进度文字（避免 UI 刷新过于频繁）
- [x] 「取消」按钮可中止处理（设置 `_abort` 标志，线程在每个文件/帧入口检查）
- [x] 完成后弹出成功/失败提示，显示处理数量或错误信息（含 traceback）

---

## 十、界面设计

### 10.1 主题
- [x] WeChat 风格亮色主题，主色调：`#07C160`（微信绿）
- [x] 卡片白色渐变背景（`stop:0 #FFFFFF → stop:1 #F8F8F8`），`border-radius: 16px`
- [x] 章节标题绿色左边框（`border-left: 3px solid #07C160`）

### 10.2 按钮形状
- [x] 普通按钮：`border-radius: 18px`（胶囊形）
- [x] 主操作按钮（绿色填充）：`border-radius: 22px`，`min-height: 44px`
- [x] 大扫描/选择按钮：`border-radius: 22px`，`min-height: 44px`
- [x] 模式切换按钮：`border-radius: 22px`，`height: 44px`

### 10.3 步骤徽章
- [x] 固定 28×28px 正圆形，绿色背景白色数字

### 10.4 布局
- [x] 批量导出页：内容居中，`max-width: 960px`
- [x] 模板配置页：左侧 sidebar 420px（可滚动），右侧画布自适应
- [x] Sidebar 内容超出屏幕时可滚动，「保存模板」和「清除数据」按钮固定在底部

---

## 十一、跨平台兼容

- [x] macOS（Apple Silicon arm64 / Intel x86_64）
- [x] Windows 10/11（x86_64）
- [x] Windows QTabWidget 黑色背景修复（QPalette + setAutoFillBackground）
- [x] Windows 对话框按钮区黑色背景修复
- [x] 避免 `QWidget { setStyleSheet("background:...") }` 无选择器写法（会破坏 Windows 样式继承）

---

## 十二、打包与发布

### 12.1 Mac 打包
- [x] 脚本：`bash build_app.sh`
- [x] 产物：`dist/融景.app`（双击运行）、`dist/融景_{arch}.dmg`（分发用）
- [x] DMG 文件名含架构后缀：`融景_arm64.dmg`（Apple Silicon）、`融景_x86_64.dmg`（Intel）
- [x] 首次运行提示：右键点击 .app → 打开（绕过 Gatekeeper）

### 12.2 Windows 打包（GitHub Actions 自动）
- [x] 触发条件：push 到 `main` 分支
- [x] 产物：`融景_windows_x64.zip`（含 `融景.exe` 及所有依赖）
- [x] 自动上传到对应 GitHub Release

### 12.3 发布流程
- [x] 一键脚本：双击 `同步到GitHub.command`
  1. 本地打包 Mac（`bash build_app.sh`）
  2. 创建 GitHub Release，上传 Mac DMG（**先建 Release，再 push 代码**，避免 Actions 上传时 Release 不存在的竞争问题）
  3. Push 代码触发 Windows Actions 打包，Windows ZIP 约 10-15 分钟后自动附加到同一 Release

### 12.4 下载地址
- [x] Mac + Windows 统一在 Releases 页面下载：`https://github.com/xiwenran/-/releases/latest`

---

## 十三、计划中 / 待评估

- [ ] 批量导出时支持设置每张图的透明度（PPT 叠加强度）
- [ ] 模板支持预设 PPT 尺寸（目前自适应 PPT 图片原始尺寸）
- [ ] 支持拖拽图片/视频到窗口直接导入
- [ ] 输出文件夹选择后显示预计磁盘占用
- [ ] 模板导入/导出（打包为 .zip 含背景图和 JSON）
- [ ] 网页版 / 订阅制卡密验证（正式商业化评估中）

---

*最后更新：2026-03-19*
