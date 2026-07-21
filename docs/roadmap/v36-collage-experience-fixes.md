原文快照：下沉自 docs/roadmap.md（2026-07-21 迁移），文中状态记号为历史残留

## V3.6 — 拼图模块体验修复（2026-05-08）

### P3.6-A: 自动适配算法修正
- 目标：修复自动适配传参错误 + 优化评分策略
- 根因：传入总图片数而非每张拼图页数，导致超过 16 张直接 fallback 到 4×4
- 改动：
  - 传入 `math.ceil(total / output_count)` 而非 total
  - 搜索空间从 cols 1-4 扩展到 1-6（与 UI 控件一致）
  - 评分策略：竖版优先 + 允许少量空格换合理比例 + 极端长条惩罚
- 涉及文件：`core/collage_processor.py`、`ui/collage_tab.py`
- 状态：✅ 已完成（commit 8f4e67e）

### P3.6-B: PPT 导入进度显示到预览区
- 目标：导入 PPT 时在右侧大预览区显示进度和授权提示
- 原因：左侧侧边栏的进度文字太小，全屏时 PowerPoint 授权弹窗被遮挡
- 涉及文件：`ui/collage_tab.py`
- 状态：✅ 已完成（commit 8f4e67e）

### P3.6-C: 背景色快捷预设
- 目标：在背景色输入框下方新增 8 个常用颜色快捷色块
- 预设色：白色、浅灰、灰色、黑色、米黄、浅绿、浅蓝、浅粉
- 涉及文件：`ui/collage_tab.py`
- 状态：✅ 已完成（commit 8f4e67e）

### P3.6-D: 侧边栏对齐 + 横向滚动修复
- 目标：色块预设和自动拆分控件左右对齐，禁止侧边栏横向滚动
- 改动：
  - 色块从固定尺寸 QLabel 改为 Expanding QWidget，合并进统一 QGridLayout
  - 自动拆分改用双列 QGridLayout，「已选 X 页」右对齐
  - 所有 grid 加 `setColumnStretch` 保证占满侧边栏宽度
  - `body.setMaximumWidth(340)` 锁定内容宽度防止 trackpad 弹性横滚
- 涉及文件：`ui/collage_tab.py`
- 状态：✅ 已完成（commit 9452e82, 4cb4a34）

### P3.6-E: AI 背景图模块升级
- 目标：中国场景提示词 + 预览交互优化 + C2PA 元数据剥离 + 默认 3:4
- 改动：
  - 完全重写 `_build_prompt()`，加入中国特有元素（希沃、国旗、黑板、中文教材）
  - 预览卡片最小 320×220 + 点击任意位置选择/取消 + 选中绿色边框
  - `generate_backgrounds()` 返回图片执行 `Image.new + paste` 剥离 C2PA/EXIF 元数据
  - `screen_detector.py` interior_std 阈值从 25 放宽到 40
  - 默认比例从 4:3 改为 3:4
- 涉及文件：`ui/ai_generate_tab.py`、`core/ai_background.py`、`core/screen_detector.py`
- 状态：✅ 已完成（commit d221f8c）

### P3.6-F: 拼图前满后补 + 按钮状态修复
- 目标：拼图分页策略从均匀分配改为前满后补 + 修复上一页按钮不可点击
- 改动：
  - `calculate_auto_split` 改为前满后补：前面每张填满格子，只有最后一张放剩余
  - `_change_preview` 调用 `_refresh_state()` 替代 `_refresh_collage_preview()` 修复按钮状态
- 涉及文件：`core/collage_processor.py`、`ui/collage_tab.py`
- 状态：✅ 已完成（commit d221f8c）

### P3.6-G: UI 体验细节优化
- 目标：4 项 UI 细节打磨
- 改动：
  - 「恢复默认」按钮从靠右改为全宽居中（Expanding 策略）
  - 默认导出格式从 PNG 改为 JPEG
  - AI 生成页「开始生成」按钮固定在侧边栏底部不随滚动
  - AI 生成页「保存选中」按钮固定在右侧面板底部不随滚动
  - 所有 h2 大标题和 cap 小标题改为居中对齐
- 涉及文件：`ui/diversify_widget.py`、`ui/collage_tab.py`、`ui/ai_generate_tab.py`、`ui/main_window.py`
- 状态：✅ 已完成（commit 25f7629）

### P3.6-H: AI 生成异步化 + 屏幕构图优化
- 目标：生成过程不卡界面 + 笔记本/台式机屏幕占比更大
- 改动：
  - QThread 后台线程执行 API 调用，UI 不再冻结
  - 生成期间按钮和预览区显示等待状态提示
  - 笔记本/台式机 prompt 增加屏幕占 60-70% 特写构图约束
- 涉及文件：`ui/ai_generate_tab.py`
- 状态：✅ 已完成（commit 0e2737a）

### P3.6-I: 希沃去手写字 + 历史记录 + 精确比例
- 目标：希沃一体机画面干净 + 生成历史可管理 + 比例精确
- 改动：
  - 希沃一体机 prompt 去掉书法标语和黑板手写字，墙面/黑板保持干净
  - 希沃一体机加入屏幕占 60-70% 特写构图
  - 新增历史记录功能：自动缓存到 `~/.rongjing/ai_cache/` + 历史对话框（加载/删除/清除/Finder 打开）
  - gpt-image-2 比例修正为精确尺寸（3:4→1152×1536、4:3→1536×1152、16:9→1536×864）
- 涉及文件：`ui/ai_generate_tab.py`、`core/ai_background.py`
- 状态：✅ 已完成（commit f788076, 51eb143）

### P3.6-J: AI 禁止年级文字 + 拼图自动拆分布局
- 目标：AI 生成不出现年级/作业文字 + 自动拆分区域布局优化
- 改动：
  - prompt 增加禁止年级、作业、科目等教学内容文字约束
  - 拼图「自动拆分」区域从两列改为一行（spin + 页数左对齐，已选页数右对齐）
- 涉及文件：`ui/ai_generate_tab.py`、`ui/collage_tab.py`
- 状态：✅ 已完成（commit 62c59b1）

### P3.6-K: 批量生成修复 + 连续生成状态 + 希沃 prompt 精调
- 目标：批量生成真正生成多张 + 连续生成状态正确 + 希沃标语保留
- 改动：
  - gpt-image-2 不支持 n>1，改为循环调用 + 实时进度显示
  - 连续生成时先清除旧预览、重新显示进度标签
  - 希沃一体机 prompt 精调：保留墙上标语和国旗，只禁黑板板书
- 涉及文件：`ui/ai_generate_tab.py`
- 状态：✅ 已完成（commit 6d73dea, 86d5b88, 4b73393）
