---
name: rongjing
description: 融景图片合成：把 PPT 截图/图片嵌入实拍背景大屏，批量生成合成图。触发词：融景、合成图、嵌入大屏、PPT嵌入背景、把图片嵌入模板、大屏合成、用模板合成。
---

# 融景 Skill

将用户提供的图片（PPT截图等）透视嵌入到实拍背景图的屏幕区域，批量生成合成图片。

## CLI 路径

```
python3 /Users/xili/rongjing/cli.py <子命令>
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
cd /Users/xili/rongjing && python3 cli.py list-templates
```

输出 JSON，展示给用户：模板编号 + 背景图文件名。

### Step 2：确认参数后执行

```bash
cd /Users/xili/rongjing && python3 cli.py process \
  --input <路径1> [路径2 ...] \
  --templates <模板名1> [模板名2 ...] \
  --output <输出目录> \
  --format JPEG
```

- `--input`：文件夹（自动扫描内部图片）或具体图片文件，可传多个
- `--templates`：模板名称（即 JSON 文件名不含扩展名，如 `1` `2` `10`）
- `--format`：默认 JPEG（质量95），需无损时用 PNG

### Step 3：报告结果

执行完成后告诉用户：
- 处理了多少张图片
- 使用了哪些模板
- 输出目录在哪里（可点击打开）

## 注意事项

- 模板存储在 `~/Library/Application Support/融景/templates/`，用 `list-templates` 查看
- 每个模板对应一张背景图，合成结果按 `输出目录/模板名/1.jpg, 2.jpg...` 存放
- 如果背景图路径不存在，CLI 会报错并说明哪个模板有问题
- 用户说"所有模板"时，先 list-templates 获取名称列表，再传给 --templates

## 不支持的功能

- 视频合成（需要 PyAV，当前 CLI 只支持图片）
- 新建/编辑模板（需要在融景 App 里点击标注角点）
