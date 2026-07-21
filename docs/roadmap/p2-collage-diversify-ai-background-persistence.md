原文快照：下沉自 docs/roadmap.md（2026-07-21 迁移），文中状态记号为历史残留

## 第二期 P2 — 拼图 + 批次差异化 + AI 背景 + 背景图持久化

启动日期：2026-05-06
背景：在小红书发布 PPT 课件笔记的工作流中，需要拼图展示多页课件、批次导出避免视觉雷同、能用 AI 生成真实背景图、模板原图删除后不影响使用。详细方案见 `~/.claude/plans/1-ppt-4-2-ppt-ppt-ppt-2-ppt-3-ai-gpt-im-dazzling-walrus.md`。

经过圆桌讨论 + Codex 对抗性审查，对原方案做了以下调整：
- 「去重」更名「批次差异化导出」，默认关闭但记忆上次状态
- AI 背景图增加 D-0 spike test 阶段，验证 GPT-image-2 API 假设
- 模板迁移策略明确分场景（不会让原本可用的模板变不可用）
- 大阶段（C-1、D-2）拆成可独立验收的小阶段

### 🚨 全期硬性约束 — 零回归

**用户正在使用当前版本，本期所有改动必须保证现有功能不受影响**。任何改动都要遵守：

1. **数据零丢失**：用户在 `~/Library/Application Support/融景/templates/` 下已有的模板 JSON 必须能继续使用，迁移失败时降级到旧路径，不能因迁移直接报错失败
2. **API 零破坏**：`TemplateManager.load_all() / load() / save() / delete()` 等公开 API 签名和返回值不变
3. **CLI 零破坏**：`cli.py` 的 `list-templates / process` 子命令行为不变
4. **现有 UI 零破坏**：模板配置、批量导出（图片/视频）三个模式的现有交互不变
5. **批次差异化默认关闭**：首次使用是关闭的，开启后写 QSettings，下次自动恢复
6. **依赖最小**：除了 AI 背景需要 `openai`，其他模块不引入新依赖

**派 Codex 时每个派单必须显式带上这一条约束**。

---

### 阶段 P2.A — 基础模块（独立可并行）

#### P2.A-1: 拼图数据模型 + 算法
- 目标：实现 `CollageTemplate` 数据类、`CollageManager` 持久化、`create_collage()` 拼图算法、`calculate_auto_split()` 自动分页
- 涉及文件：
  - `models/collage_model.py`（新增）
  - `core/collage_processor.py`（新增）
- 不涉及：UI、batch_runner、main_window、main.py（main.py 改在 A-3）
- 验收标准（机械可测）：
  - 写一个 `_test_collage.py`（或 pytest），构造 12 张 100×100 不同颜色的测试图
  - 调用 `create_collage(images, layout="grid", rows=3, cols=4, gap=4, padding=0, output_width=1280)` 返回 PIL Image
  - 输出图片尺寸：宽=1280，高 = padding*2 + rows*cell_h + (rows-1)*gap，cell_h 由 cols 和 cell_aspect_ratio 推算
  - 单元格内容像素的左上角颜色 = 对应输入图的左上角颜色（顺序 P1→P12 从左到右、从上到下）
  - 不足 12 张时，剩余单元格填充 `background_color`（用 ImageDraw 验证 4 个角点颜色）
  - `calculate_auto_split(48, 4, 12)` 返回 `[(0,12),(12,24),(24,36),(36,48)]`
  - `calculate_auto_split(50, 4, 12)` 返回 `[(0,12),(12,24),(24,36),(36,50)]`（最后一张少 2 张）
- 状态：✅ 已完成（2026-05-06）

#### P2.A-2: 批次差异化引擎
- 目标：实现 `DiversifyConfig` + `diversify_image()` + 辅助函数 + 三档预设
- 涉及文件：
  - `core/diversifier.py`（新增）
- 验收标准（机械可测）：
  - `DiversifyConfig.preset("low" | "medium" | "high")` 返回三档预设
  - 同一张测试图（1024×1024 渐变图），相同 config 不同 seed 调用 2 次 `diversify_image()`，输出像素级不同（`np.array_equal()` 返回 False）
  - 同一张测试图，相同 config 相同 seed 调用 2 次，输出像素级完全相同（可重现）
  - `jitter_points([[0,0],[100,0],[100,100],[0,100]], 5, rng)` 返回的每个点都在原点 ±5 范围内
  - `strip_metadata()` 后的图片用 `PIL.ExifTags` 读取应为空
  - "中"档输出与原图的 PSNR ≥ 35dB（肉眼无感的客观指标）
- 状态：✅ 已完成（2026-05-06）

#### P2.A-3: 模板背景图持久化（含分支处理）
- 目标：保存模板时复制背景图到应用数据目录，旧模板按 5 种分支安全迁移
- 涉及文件：
  - `models/template_model.py`（修改 ~50 行）
  - `main.py`（修改 ~5 行：创建 `backgrounds/` 目录）
- 验收标准（机械可测，5 个测试场景）：
  1. **新模板（A 分支）**：`save()` 一个新模板（背景图在外部路径）→ `backgrounds/` 下出现副本，JSON 中 `background_path` 指向副本，源文件 SHA-256 == 副本 SHA-256
  2. **旧模板已迁移（B 分支）**：`load()` 一个旧 JSON（path 指外部存在的图）→ 自动复制到 `backgrounds/`，JSON 自动更新；再次 `load()` 不重复迁移
  3. **迁移失败保留原状（B 分支异常）**：mock `shutil.copy2` 抛 PermissionError → 原 JSON 不变，模板仍可加载（用旧路径），无异常向上抛
  4. **原图缺失（C 分支）**：`load()` 一个旧 JSON（path 指不存在的文件）→ 模板对象返回但有 `is_broken=True` 标记，JSON 文件保留
  5. **原子性**：mock 复制成功但 hash 校验失败 → 副本被删除，原 JSON 不变
- 不涉及 UI 改动（C 分支的 UI 提示在 P2.C-2 实现）
- 状态：✅ 已完成（2026-05-06）

---

### 阶段 P2.B — 集成模块（依赖 A）

#### P2.B-1: 拼图批处理线程
- 目标：实现 `CollageBatchRunner(QThread)`，整合拼图 + 差异化
- 涉及文件：`core/collage_batch_runner.py`（新增）
- 依赖：P2.A-1、P2.A-2
- 验收标准（机械可测）：
  - 用 12 张测试图 + 一个 3×4 拼图配置 + DiversifyConfig.preset("medium") 启动 runner
  - `progress` 信号至少触发 1 次，最终 `finished(success=True, ...)` 触发
  - 输出目录下有 1 张拼图文件
  - 同 runner 跑 2 次（相同输入），2 次输出像素级不同（差异化生效）
- 状态：✅ 已完成（2026-05-06）

#### P2.B-2: 差异化 UI 组件
- 目标：实现 `DiversifyWidget(QWidget)`，含强度选择 + 高级参数折叠 + QSettings 持久化
- 涉及文件：`ui/diversify_widget.py`（新增）
- 依赖：P2.A-2
- 验收标准（机械可测）：
  - 单独运行 `python3 -c "from ui.diversify_widget import DiversifyWidget; from PyQt6.QtWidgets import QApplication; app=QApplication([]); w=DiversifyWidget(); w.show(); app.exec()"` 能弹出
  - 切换强度档位时，`get_config()` 返回的 DiversifyConfig 数值同步变化
  - 勾选"启用" + 选"中" + 关闭再打开应用 → QSettings 中有 `diversify/enabled=true`、`diversify/strength=medium`
  - 下次启动 widget 自动恢复上次状态
- 状态：✅ 已完成（2026-05-06）

---

### 阶段 P2.C — UI 集成（依赖 B），拆细两阶段

#### P2.C-1a: 拼图 Tab 左侧布局表单
- 目标：实现拼图 Tab 左侧 sidebar——快捷预设、行列/间距/边距、比例、背景色、拼图模板 CRUD、嵌入 DiversifyWidget
- 涉及文件：`ui/collage_tab.py`（新增 ~200 行）
- 依赖：P2.A-1（CollageTemplate）、P2.B-2（DiversifyWidget）
- 验收标准：
  - 单独启动该 widget 能展示
  - 点击快捷预设按钮（如 3×4），行列 SpinBox 同步更新
  - 模板列表能加载已有 JSON、保存新模板、删除模板
- 状态：✅ 已完成（2026-05-06）

#### P2.C-1b: 拼图 Tab 右侧步骤区
- 目标：实现拼图 Tab 右侧——选图片、自动拆分、缩略图排除、预览、输出 + 接入 CollageBatchRunner
- 涉及文件：`ui/collage_tab.py`（追加 ~200 行）
- 依赖：P2.B-1（CollageBatchRunner）、P2.C-1a
- 验收标准：
  - 端到端跑通：选 12 张图 → 自动拆分显示"1 张" → 排除 2 张 → 显示"剩 10 张" → 导出 → 输出目录有 1 张拼图
- 状态：✅ 已完成（2026-05-06）

#### P2.C-2: 主窗口集成 + 批量导出接入差异化 + 模板缺失提示
- 目标：
  - 在 `main_window.py` 加导航项「拼图」
  - 批量导出页（仅图片模式，不含视频）接入 `DiversifyWidget`
  - 模板列表显示"⚠ 背景图缺失"标记（P2.A-3 的 C 分支配套 UI）
- 涉及文件：
  - `ui/main_window.py`（修改 ~50 行）
  - `core/batch_runner.py`（修改 ~30 行：接入 DiversifyConfig）
- 依赖：P2.C-1b
- 验收标准（含回归）：
  - 旧的批量导出（图片文件夹/图片批量）行为不变（关闭差异化时 hash 与 P1 输出一致）
  - 视频导出完全不变（不接入差异化）
  - 拼图 Tab 可访问且功能正常
  - 缺失背景图的旧模板在列表中有"⚠"标记，点击弹出"重新选择/删除"对话框
- 状态：✅ 已完成（2026-05-06）

---

### 阶段 P2.D — AI 背景（独立，最后做）

#### P2.D-0: 🔴 API Spike Test（必做前置）
- 目标：30 分钟内验证 GPT-image-2 API 的核心假设
- 涉及文件：临时测试脚本（不进 git，验证完删除）
- 验收标准：
  - 至少 1 个比例（4:3 或 1:1）能成功返回图片
  - 用户中转站能调通（或确认需要切官方 API）
  - 记录确切的：模型名（gpt-image-2 还是 dall-e-3）、size 参数支持的值、返回是 b64 还是 URL、单张实际费用
- **如果 spike 失败**：记录失败原因，砍掉 D-1/D-2，本期不做 AI 背景
- 状态：✅ 已完成（2026-05-06）

#### P2.D-1: AI API 调用模块
- 目标：封装 OpenAI 图像生成 API
- 涉及文件：
  - `core/ai_background.py`（新增）
  - `requirements.txt`（+1 行 `openai`）
- 依赖：P2.D-0 通过
- 验收标准：
  - 能调用 API 返回图片 PIL Image 对象
  - 错误处理覆盖：429 限流、配额耗尽、网络超时、Base URL 不通、API Key 无效
  - 失败时抛具名异常（`AIQuotaError`、`AIRateLimitError`、`AIBaseURLError`、`AIAuthError`）
- 状态：✅ 已完成（2026-05-06）

#### P2.D-2a: AI Tab UI 骨架（标签按钮组件）
- 目标：实现可点击切换/取消的标签按钮组件 + 静态布局
- 涉及文件：`ui/ai_generate_tab.py`（新增 ~150 行）
- 依赖：P2.D-1
- 验收标准：
  - 点击标签变绿（选中），再点变灰（取消）
  - 各标签组（设备类型、灯光、角度、桌面摆件）独立工作
- 状态：✅ 已完成（2026-05-06）

#### P2.D-2b: 联动 + 随机
- 目标：设备-场景联动 + 「随机未选项」按钮
- 涉及文件：`ui/ai_generate_tab.py`（追加 ~100 行）
- 依赖：P2.D-2a
- 验收标准：
  - 选「希沃一体机」时，使用场景标签自动切换为教室相关，原个人场景标签隐藏
  - 切回「笔记本/台式机」时，使用场景标签恢复个人场景
  - 点「随机未选项」，所有未选中的标签组各随机选 1 个
- 状态：✅ 已完成（2026-05-06）

#### P2.D-2c: 保存为模板 + 设置页 API 配置
- 目标：批量勾选生成结果保存为模板背景 + 设置页添加 API Key/Base URL
- 涉及文件：
  - `ui/ai_generate_tab.py`（追加 ~100 行）
  - `ui/main_window.py`（设置页 +50 行）
- 依赖：P2.D-2b、P2.A-3（持久化路径）
- 验收标准：
  - 设置页能填写 API Key 和 Base URL，关闭重启后值保留（QSettings）
  - 生成 4 张图后，勾选其中 2 张点保存 → `backgrounds/` 下新增 2 个文件，自动跳转到模板配置页
- 状态：✅ 已完成（2026-05-06）

---

### 阶段 P2.E — 收尾

#### P2.E-1: 文档更新
- 目标：更新 `FEATURES.md`（新增功能章节）、`CLAUDE.md`（如有架构变化）
- 涉及文件：`FEATURES.md`、`CLAUDE.md`（按需）
- 状态：✅ 已完成（2026-05-06）

#### P2.E-2: 端到端测试（手动）
- 目标：按下列 7 项手动测试，全部通过才算 P2 完工
  1. 启动应用：`python3 main.py`，无报错
  2. 拼图：选 PPT 截图文件夹 → 配置 → 预览 → 导出，输出正确
  3. 批次差异化：同模板同图片导出 2 次，结果像素级不同
  4. 背景图持久化：保存模板 → 删除原图 → 重启 → 模板仍可用
  5. 旧模板迁移：用预先准备的旧 JSON 启动，第一次加载自动迁移
  6. AI 背景：选「教室投影」→ 生成 → 保存 → 标注角点 → 导出
  7. 现有功能回归：模板配置/图片批量/视频处理三个旧功能行为完全不变
- 状态：✅ 已完成（2026-05-06）

## 历史

### 第二期 P2 — 拼图 + 批次差异化 + AI 背景 + 背景图持久化（已完成）
- 4 大新功能 + 1 个老 bug 修复，全部通过测试 + 冷眼审查 + 对抗性审查
- 详见本文件上方 P2 各阶段
- 状态：✅ 已完成（2026-05-06）
