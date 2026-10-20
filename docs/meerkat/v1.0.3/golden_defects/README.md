# 金缺陷集（Golden Defects）

> 用途：评审链三层（代码检查器 / Laya / Claude 深审）的**检出率回归基准**。
> 设计依据：`design/评审与调优机制设计.md` §4.1（v3.1：可注入性筛选 + 固定宿主）。

## 目录

- `manifest.json`：全部缺陷的索引（id / 分类 / 预期检出层 / 证据指针 / 素材文件）
- `text_injectable/`：**可文本注入**的缺陷（17 项，含 R2 增补的 GD-17 图表配套型），每项一个文件：描述 + 注入素材 + 预期检出层
- `process_checklist/`：**过程性缺陷**（4 项）——无法文本注入，由对应机制的验收项覆盖
- `inject.py`：把 text_injectable 缺陷注入固定宿主，产出「带雷宿主」+ ground truth 标注
- `run_checker_regression.py`：检查器层回归脚本——按 manifest 对宿主跑 report_qa 全量检查，逐缺陷判定 TP/FP
- `回归报告_检查器层.md` / `回归报告_Claude深审层.md`：R1 回归结果（三层合计 16/16 全命中，误报 0）

## 固定宿主（两层，服务不同评审层）

- **Claude/Laya 层**：基线报告切片（`baseline/report_merged.md`，按 ≤700 字符切片），经 `inject.py` 注入。
- **检查器层**：冻结的完整 workspace `baseline/baseline_workspace/`（13 章 + 台账 + outline/progress）——
  META 型缺陷（台账停更、schema 违规等）需要状态文件才能检出，纯文本切片不够。
  宿主已冻结为工件，回归经 `run_checker_regression.py --workspace` 指向，可复现。

同一宿主 + 同一缺陷集，轮间检出率的唯一变量 = 评审链版本。

## 验收判定（与执行计划 T1 咬合）

对宿主的每条检查器命中：对应已标注/已注入缺陷 → TP；不对应 → FP。
**合格 = TP 率 ≥90% 且 FP = 0**。标注（manifest + inject 产出的 ground truth）即判定依据。

## 用法

```powershell
# Claude/Laya 层：注入带雷宿主
python inject.py --host <切片目录或报告md> --out <输出目录>
# 产出：injected/（带雷宿主）+ ground_truth.json（标注）

# 检查器层：对冻结宿主回归（在 mapid-harness 仓库根，PYTHONPATH=src）
python run_checker_regression.py --workspace ..\baseline\baseline_workspace\
# 产出：逐缺陷 TP/FP 判定 + 汇总（验收口径见回归报告_检查器层.md）
```

注：GD-08（倒挂检测）预期检出层为 Claude 深审（检查器数值比对只查台账内一致性，
倒挂语义判定 T1 有意推迟），不参与检查器层 TP 判定。
