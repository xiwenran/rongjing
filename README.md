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
- 每个子文件夹视为一组（子文件夹名作为分组名）；若无子文件夹则处理根目录图片
- 输出结构：`输出目录 / 子文件夹名 / 模板名称 / 1.png, 2.png, ...`

**图片批量模式**
- 手动多选图片文件（可跨文件夹）
- 输出结构：`输出目录 / 图片批量 / 模板名称 / 1.png, 2.png, ...`

共同特性：
- 每组可独立选择多个模板，一次处理输出所有模板的合成结果
- 「全部应用」一键为所有组统一设置模板
- 输出格式：PNG（无损）或 JPEG（quality=95）

### 批量合成视频
- 多选视频文件，每帧嵌入场景模板背景图，输出合成视频
- 自动保留原始音频（重编码为 AAC），音画同步
- 输出结构：`输出目录 / 视频文件名 / 模板名称 / 视频文件名.mp4`
- 支持格式：`.mp4` `.mov` `.avi` `.mkv` `.m4v` `.wmv`

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
| 性能优化 | ThreadPoolExecutor | 视频帧多线程并行处理（PIL/NumPy 的 C 实现释放 GIL，真正并行） |
| 缓存优化 | 预计算 cache | mask、背景数组、透视系数在处理前一次性计算，所有帧复用 |
| 路径持久化 | QSettings | 记忆每个选择器的上次路径 |
| 打包 | PyInstaller | Mac 本机打包；Windows 由 GitHub Actions 自动构建 |

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

- 图片序号从 `1` 开始，按原文件名字典序排列
- 扩展名：PNG 模式为 `.png`，JPEG 模式为 `.jpg`，视频固定为 `.mp4`

---

## 支持的文件格式

| 类型 | 格式 |
|------|------|
| 输入图片 | `.jpg` `.jpeg` `.png` `.bmp` `.webp` `.tiff` |
| 输入视频 | `.mp4` `.mov` `.avi` `.mkv` `.m4v` `.wmv` |
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

输出 JSON，包含模板名称与背景图路径。

### 批量合成图片

```bash
python3 cli.py process \
  --input <文件夹或图片路径...> \
  --templates <模板名...> \
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

**输出结构**：`输出目录 / 模板名 / 1.jpg, 2.jpg, ...`

### 注意

- 视频合成仅支持图形界面（需要 PyAV + QThread）
- 新建/编辑模板须在融景 App 内完成（需要可视化标注角点）
- 模板文件存储于 `~/Library/Application Support/融景/templates/`

---

## Claude Code Skill

已提供 `rongjing` Skill（`~/.claude/skills/rongjing/SKILL.md`），在 Claude Code 中可直接用自然语言触发批量合成：

> 「用模板1到5，把桌面上的图片文件夹合成，输出到Downloads」
