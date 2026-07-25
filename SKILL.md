---
name: rongjing
description: 融景图片合成：把 PPT 截图/图片嵌入实拍背景大屏，批量生成合成图。触发词：融景、合成图、嵌入大屏、PPT嵌入背景、把图片嵌入模板、大屏合成、用模板合成。
---

# 融景 Skill

将用户提供的图片（PPT截图等）透视嵌入到实拍背景图的屏幕区域，批量生成合成图片。

## CLI 路径

```
python3 ~/rongjing/cli.py <子命令>
```

## 工作流程

### Step 0：了解用户需求

用户会说类似：
- "用模板3，处理桌面上的图片文件夹"
- "帮我合成，用1和2号模板"
- "把 ~/Downloads/ppt截图/ 用所有模板合成"

需要确认三件事：
1. **输入**：图片文件夹路径 或 具体图片路径（可多个）
2. **模板**：用哪些模板（名称或编号），不确定时先列出可用模板让用户选
3. **输出目录**：没指定时默认用 `~/Desktop/融景输出/`

### Step 1：列出可用模板（需要时）

```bash
cd ~/rongjing && python3 cli.py list-templates
```

输出 JSON，展示给用户：模板 `key` + 显示名称 + 分类 + 背景图文件名。同名模板按分类区分时，后续命令必须使用 `key`。

### Step 2：确认参数后执行

```bash
cd ~/rongjing && python3 cli.py process \
  --input <路径1> [路径2 ...] \
  --templates <模板key1> [模板key2 ...] \
  --output <输出目录> \
  --format JPEG
```

- `--input`：文件夹（自动扫描内部图片）或具体图片文件，可传多个
- `--templates`：模板 `key`，或没有重名时的唯一模板名称；同名模板必须用 `list-templates` 里的 `key`
- `--format`：默认 JPEG（质量95），需无损时用 PNG
- `--fit`：源图与模板承载区宽高比不一致时的适配方式，默认 `stretch`（拉伸铺满，零变化）；传 `contain` 会先把源图等比缩放并补白（letterbox）到承载区的等效宽高比，再走透视合成，避免内容被非均匀拉伸变形——文档类源图（如整页 Word/PDF 截图）宽高比经常和背景模板不一致，需要保真时用这个

### Step 3：报告结果

执行完成后告诉用户：
- 处理了多少张图片
- 使用了哪些模板
- 输出目录在哪里（可点击打开）

**Token 节制要求（与 ppt-batch-tool 衔接）**：融景环节只输出摘要给上下文，不读完整生成 JSON。
报告格式：主题数、模板数、缺图/失败项（文件名）、输出目录。不逐一展开每张合成图路径。
若上游已有 `convert_summary.json`，直接引用其 `success_count` 和 `output_dir`，无需重新列举图片文件。

**多主题任务的后续衔接**：若本次合成了多个主题，完成重整脚本（把连续编号切回「主题/模板/图片」结构）并验证文件数正确后，下一步取决于起点——从 PPT 开始的全链路任务走 **zhifa-pipeline** skill；已有合成图只需上传走 **zhifa-upload** skill。

## 注意事项

- 模板存储在 `~/Library/Application Support/融景/templates/`，用 `list-templates` 查看
- 每个模板对应一张背景图，合成结果按 `输出目录/模板key/1.jpg, 2.jpg...` 存放
- 如果背景图路径不存在，CLI 会报错并说明哪个模板有问题
- 用户说"所有模板"时，先 list-templates 获取 `key` 列表，再传给 --templates
- **多主题批量合成的输出结构（必看）**：`--input` 传入多个文件夹（多主题）时，融景把所有主题的图片合并后按模板分目录、连续编号——输出是 `模板名/1.jpg ~ N.jpg`，不会按主题切分。若需要「主题/模板/图片」结构，合成完成后必须额外派 codex-rescue 写重整脚本，按每个主题的图片数量把连续编号切回各自的主题子目录，并验证每个主题子目录的文件数与用户给出的数字一致。**多主题作业前必须先问用户"每个主题各有几张图"**，拿到确认的数字后才能继续，不可跳过。

## 新建模板（create-template）

```bash
cd ~/rongjing && python3 cli.py create-template \
  --bg <背景图路径> [--name <模板名>] [--category <分类>] \
  [--detect screen|paper] [--inset-ratio <比例>] \
  [--preview-out <预览图路径>] [--json-result] [--force]
```

- `--detect`：识别方式，默认 `screen`（走绿幕→VLM 融合→经典算法三级识别路径，面向屏幕/白板类背景，零变化）；`paper` 走亮区分割识别纸张四角，适用于「实拍空白纸张放在桌面上」的文档纸张模板背景
- `--inset-ratio`：仅 `--detect paper` 生效，默认 `0.03`；识别到的纸张四角会朝质心方向按该比例内缩，避免合成内容压在纸张物理边缘
- `--category 文档纸张`：会让模板的 `template_type` 落为 `document_paper`，合成时走纸张光影混合路径（`embed_document_paper_pil`），而不是屏幕透视路径
- 建完模板务必看一眼 `--preview-out` 生成的预览图（四角连线+绿点），确认识别准确再投入批量合成；`--json-result` 输出里的 `quality.aspect_ratio`/`area_ratio`/`method` 可用于快速判断识别质量

## 不支持的功能

- 视频合成（需要 PyAV，当前 CLI 只支持图片）。注意：视频笔记不经过融景合成，视频文件由 zhifa-upload / zhifa-pipeline 直接上传飞书
