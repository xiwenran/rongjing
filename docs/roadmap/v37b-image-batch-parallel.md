原文快照：下沉自 docs/roadmap.md（2026-07-21 迁移），文中状态记号为历史残留

### V3.7-B: 图片批量导出并行加速
- 目标：把 `BatchRunner.run` 中逐张串行的图片处理改为线程池并行，保持输出文件顺序与内容零回归
- 涉及文件：
  - `core/batch_runner.py`（`BatchRunner.run` 重构为线程池并行）
  - `core/image_processor.py`（保证多线程下 cache 读写线程安全）
- 关键约束（零回归）：
  - 输出文件名 `{i}.png/.jpg` 与原索引严格对应，顺序不乱
  - 差异化（diversify）seed 仍按 `(run_seed, template, group, i)` 确定性计算，开关行为不变
  - JPEG/PNG 格式、输出尺寸 resize 行为不变
  - abort 取消能及时响应
  - 视频路径（VideoRunner）完全不受影响
- 验收标准（机械可测）：
  - 关闭差异化时，并行版与原串行版输出逐字节一致
  - 造一批测试图实测并行 vs 串行耗时，记录加速比
- 实测结果：60 张 1920x1080，串行 2.57s → 并行 0.71s，**加速约 3.6 倍**（10 核 Mac，worker 上限 6）
- 状态：✅ 已完成（2026-05-25，commit 52fa1be，冷眼审查 6/6 通过 + 混合尺寸 8 线程压测）
