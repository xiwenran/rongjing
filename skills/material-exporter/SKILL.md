---
name: material-exporter
version: 1.0.0
description: "资料导出工具：把文件或文件夹中的 PPT 或 Word 资料批量导出为 PNG 页面。用户提到资料导出、资料转图片、PPT 转图片、Word 转图片、批量导出 PPT、批量导出 Word、PPT 转 PNG、Word 转 PNG、把课件转成图片、导出幻灯片截图时使用。"
---

# 资料导出工具

## 触发条件

- 用户要把 PPT 或 Word 文件、目录递归导出为 PNG 页面时进入本 Skill。
- 如果用户同时要继续做融景合成或拼图，改用 `ppt-notes-pipeline`。

## 流程（触发 + 硬底线 + 细则指针）

### 步骤一：锁定资料类型和路径

- **触发**：收到导出请求后，先确定输入路径、输出根目录和资料类型。
- **硬底线**：资料类型必须明确为 `ppt` 或 `word`；`ppt` 模式只扫描 PPT，`word` 模式只扫描 Word。用户未说明且无法从单文件扩展名确定时，只追问资料类型，不混合扫描。
- **细则**：输入可为单个文件或目录；目录由融景递归扫描。AppleDouble、隐藏项、Office 临时文件和非普通文件统一由融景输入策略过滤。

### 步骤二：执行本轮导出

- **触发**：类型和路径已确定后执行。
- **硬底线**：只调用现役融景 CLI；输出根目录必须显式传入。命令失败时保留原始错误并停止，不把部分结果报成整批成功。
- **细则**：参数以 `python3 ~/rongjing/cli.py export-material --help` 的当前帮助为准。

```bash
python3 ~/rongjing/cli.py export-material \
  --type <ppt|word> \
  --input <资料文件或目录> \
  --output <输出根目录> \
  --max-pages <最多页数>
```

需要固定转换后端时才加：

```bash
--backend <ppt_mac|ppt_com|word_mac|word_com|libreoffice>
```

### 步骤三：按本轮回执汇报

- **触发**：CLI 返回 JSON 后汇报结果。
- **硬底线**：只读取本轮 stdout 的结构化 summary，不扫描历史输出目录推算结果。只把 `results` 中 `success: true` 的 `output_dir` 当作本轮可消费页面目录。
- **细则**：汇报 `success_count`、`failed_count`、`skipped_count`、`output_dir`，失败时列出 `failed_files`；不展开全部成功图片路径。

## 硬底线汇总

- 必须先确定 `ppt` 或 `word`，两种模式不混扫。
- 现役入口固定为 `python3 ~/rongjing/cli.py export-material`。
- AppleDouble、隐藏项、临时项和非文件交给融景统一过滤。
- 结果只认本轮结构化 summary，不扫描历史输出根补数。

## 已知边界

- 当前 PPT 输入类型为 `.ppt`、`.pptx`，Word 输入类型为 `.doc`、`.docx`。
- 本 Skill 只负责资料转 PNG 页面，不负责融景合成、拼图、封面、文案或发布。
