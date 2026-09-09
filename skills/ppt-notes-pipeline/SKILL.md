---
name: ppt-notes-pipeline
version: 1.0.0
description: "资料一键制作笔记：把 PPT 或 Word 资料导出为本轮页面，再用融景模板合成或拼图。用户提到一键制作笔记、资料制作笔记、PPT 制作笔记、导出并合成、PPT 转笔记图、把 PPT 做成笔记、批量制作笔记图、全流程制作时使用。"
---

# 资料导出 → 融景处理流水线

## 触发条件

- 用户要把 PPT 或 Word 资料先导出页面，再继续做融景模板合成或拼图时进入本 Skill。
- 只要导出页面、不做后续视觉处理时，改用 `material-exporter`。

## 流程（触发 + 硬底线 + 细则指针）

### 步骤一：导出本轮页面

- **触发**：确定资料类型、输入路径和输出根目录后执行。
- **硬底线**：资料类型必须明确为 `ppt` 或 `word`；通过现役 `export-material` 导出，保留 stdout 的本轮 JSON summary。
- **细则**：资料类型、过滤边界和汇报字段见同仓 `../material-exporter/SKILL.md`「流程（触发 + 硬底线 + 细则指针）」节。

```bash
python3 ~/rongjing/cli.py export-material \
  --type <ppt|word> \
  --input <资料文件或目录> \
  --output <页面输出根目录> \
  --max-pages <最多页数>
```

### 步骤二：锁定本轮输入和处理方式

- **触发**：`export-material` 成功返回 summary 后执行。
- **硬底线**：只消费本轮 summary 的 `results[]` 中 `success: true` 对应的实际 `output_dir`；不扫描页面输出根目录，也不把失败项的预留目录送入后续步骤。
- **细则**：用户要把每页嵌入场景时走 `process`；用户要把多页排成单张图时走 `collage`。两者参数分别以 `python3 ~/rongjing/cli.py process --help` 和 `python3 ~/rongjing/cli.py collage --help` 为准。

### 步骤三 A：用模板逐页合成

- **触发**：用户选择融景场景合成。
- **硬底线**：先运行 `python3 ~/rongjing/cli.py list-templates`，从返回项选择唯一 `key`；不得只用可能重名的显示名。必须加 `--json-result` 并保存本轮实际输出目录回执。
- **细则**：把步骤二筛出的一个或多个实际页面目录传给 `--input`；输出格式默认 `JPEG`，仅在用户指定时改为 `PNG`。

```bash
python3 ~/rongjing/cli.py process \
  --input <本轮成功output_dir> [<本轮成功output_dir> ...] \
  --templates <唯一模板key> [<唯一模板key> ...] \
  --output <合成输出根目录> \
  --format JPEG \
  --json-result
```

### 步骤三 B：把页面拼成单图

- **触发**：用户选择拼图；对步骤二的每个成功页面目录分别执行一次。
- **硬底线**：`--input-dir` 必须是本轮 summary 给出的实际页面目录；`--template` 与 `--rows/--cols` 二选一；必须加 `--json-result` 获取实际输出文件和目录。
- **细则**：预设拼图用 `--template <预设名>`；自定义网格同时传 `--rows <行数> --cols <列数>`。用户未限定页面时省略 `--pages`。

```bash
python3 ~/rongjing/cli.py collage \
  --input-dir <本轮成功output_dir> \
  --output <拼图输出根目录/文件名.jpg> \
  --template <拼图预设名> \
  --json-result
```

### 步骤四：汇总本轮结果

- **触发**：后续处理结束后汇报。
- **硬底线**：导出数据来自 `export-material` 本轮 summary；融景数据来自本轮 `process` 或 `collage` 的 JSON 回执。任一步失败都要标明 partial，不用目录扫描补齐数字。
- **细则**：汇报导出成功/失败数、成功页面目录，以及融景本轮实际输出目录或文件；不展开全部图片路径。

## 硬底线汇总

- 先运行 `export-material`，再串联 `process` 或 `collage`。
- 后续只消费本轮 summary 中成功结果的实际 `output_dir`。
- `process` 模板使用 `list-templates` 返回的唯一 `key`。
- 每一步只认本轮结构化回执；失败按 partial 汇报。

## 已知边界

- 本 Skill 不负责教师发布、封面制作、笔记文案或账号操作。
- 每个拼图命令只处理一个本轮页面目录；多个资料来源逐项执行并分别保留回执。
