---
name: rongjing
description: 融景图片与资料处理：把图片嵌入实拍模板、生成页面序列视频，或导出 PPT/Word 页面。触发词：融景、合成图、嵌入大屏、PPT嵌入背景、把图片嵌入模板、大屏合成、用模板合成、资料导出、页面翻页视频、翻页预览、Core Image翻页。
---

# 融景 Skill

用于图片透视合成、PPT/Word 页面导出和 App 内页面序列视频。CLI 入口为 `python3 ~/rongjing/cli.py <子命令>`。

## 触发条件

- 图片或 PPT 截图需要嵌入融景模板时，使用 `process`。
- PPT 或 Word 需要导出为 PNG 页面时，使用 `export-material`；完整的资料转图或笔记流水线分别进入 `material-exporter`、`ppt-notes-pipeline`。
- 图片、图片文件夹或真实视频需要生成视频时，使用融景 App 的视频入口：真实视频交给 `VideoRunner`，图片走页面序列视频。
- 需要快速确认页面转场时，在页面图片模式使用「预览翻页」；该入口取前两张有效页面与首个屏幕模板，在 900×560 应用内循环播放弹窗中提供播放、暂停、重新播放和关闭。
- 需要创建模板时，使用 App 可视化标注，或使用 `create-template` 自动识别。

## 硬底线

- PPT 与 Word 按 `--type` 严格分流，目录扫描只接收所选类型。
- 输入扫描先过滤隐藏文件、AppleDouble `._*`、Office 临时文件 `~$*` 和非文件项，再排序、计数、选封面、生成 manifest 和组装任务清单。
- macOS PowerPoint 只使用固定 `~/Documents/融景Office中转/`：复制原 PPT 后从同根打开并输出 PDF，原件不移动；成功或正常失败按 manifest 精确清理本批副本与 PDF，严格超过 24 小时的崩溃残留由启动清理处理。固定根使用 `0700`、no-follow、`dir_fd`、quarantine、inode + size 校验，副本另核对 SHA-256 + size；LibreOffice 流程保持不变。
- 拼图、图片合成、真实视频和资料导出每次分配新来源目录；重名时使用 `_2`、`_3`，不覆盖或清理旧产物。
- 跨分类同名模板使用 `list-templates` 返回的 `key`，避免选错模板。
- 执行后报告实际输出目录、成功数量、失败项和未验证边界，不把 CLI 返回 0 或文件存在单独当作最终验收。
- Mac 页面序列优先使用随 App 打包的 Core Image 助手，并在可用时使用 VideoToolbox；非 Mac、助手不可用或编码器无法打开时，必须明确报告 CPU/libx264 回退，不把回退结果写成 Core Image/VideoToolbox 已生效。
- 翻页方向默认为从右向左，可选从左向右；正式导出与预览、CPU 与 Core Image 必须使用同一方向。预览只写专用 `page_preview_cache` 的唯一 MP4，启动清理仅处理直属层超过 24 小时的普通 MP4。

## CLI 入口

### 图片合成

```bash
cd ~/rongjing && python3 cli.py process \
  --input <路径1> [路径2 ...] \
  --templates <模板key1> [模板key2 ...] \
  --output <输出目录> \
  [--format PNG|JPEG] \
  [--cover-source <封面源目录>] \
  [--fit stretch|contain|cover] \
  [--no-realism] \
  [--realism-strength 0-100] \
  [--json-result]
```

`--format` 默认 JPEG。`--fit` 默认 `stretch`；`contain` 保留完整内容并补白，`cover` 铺满后居中裁切。屏幕模板的实拍质感默认强度为 70，文档纸张模板默认为 0；显式传入 `--realism-strength` 时以参数为准。

### 资料导出

```bash
cd ~/rongjing && python3 cli.py export-material \
  --type ppt|word \
  --input <文件或目录> \
  --output <输出根目录> \
  [--max-pages <页数>] \
  [--backend ppt_mac|ppt_com|word_mac|word_com|libreoffice]
```

`--max-pages` 默认 17，并兼容 `--max-slides`。省略 `--backend` 时按当前平台自动选择并回退。

macOS 首次使用 PowerPoint 后端时，固定中转根预期只需授权一次，同一文件夹或多个文件夹共用该授权；授权持久性需在实际 Mac 和冻结 App 中确认。

### 新建模板

```bash
cd ~/rongjing && python3 cli.py create-template \
  --bg <背景图路径> [--name <模板名>] [--category <分类>] \
  [--detect screen|paper] [--inset-ratio <比例>] \
  [--preview-out <预览图路径>] [--json-result] [--force] \
  [--no-vlm] [--min-screen-width <像素>]
```

- `--detect`：识别方式，默认 `screen`（走绿幕→VLM 融合→经典算法三级识别路径，面向屏幕/白板类背景，零变化）；`paper` 走亮区分割识别纸张四角，适用于「实拍空白纸张放在桌面上」的文档纸张模板背景
- `--inset-ratio`：仅 `--detect paper` 生效，默认 `0.03`；识别到的纸张四角会朝质心方向按该比例内缩，避免合成内容压在纸张物理边缘
- `--category 文档纸张`：会让模板的 `template_type` 落为 `document_paper`，合成时走纸张光影混合路径（`embed_document_paper_pil`），而不是屏幕透视路径
- `--no-vlm`：关闭 VLM 粗定位融合，仅使用经典识别；`--min-screen-width` 默认 1600，屏幕区域低于该宽度时按上限 3 倍放大背景图
- 建完模板务必看一眼 `--preview-out` 生成的预览图（四角连线+绿点），确认识别准确再投入批量合成；`--json-result` 输出里的 `quality.aspect_ratio`/`area_ratio`/`method` 可用于快速判断识别质量

## 细则指针

- 功能、输出目录和当前验证边界见 `README.md`「功能」「输出文件命名规则」「当前验证边界」节。
- Core Image 曲面翻页、静态缓存、每组相邻页最多 8 张独立曲面帧、VideoToolbox 与 CPU 回退的已实现项和验证边界，见 `FEATURES.md`「十五、资料导出、统一文件规则与页面翻页视频」节。
- 资料批量导出与笔记全流程分别见 `skills/material-exporter/SKILL.md`、`skills/ppt-notes-pipeline/SKILL.md` 的「工作流程」节。
