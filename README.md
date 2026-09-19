# 简历罗盘 · 项目文件夹

用于「学生简历竞争力评估系统」的持久化数据，跨对话/跨会话保留。

## 文件结构

- `framework.json` — 评估框架的标准数据源：7个通用维度（含评分锚点）、**29个领域**的权重/加分项/常见短板、分级标准。是本项目的唯一真相来源（single source of truth）。以后调整权重、增删领域，都改这份文件。
- `jd-reference-library/` — 分领域的真实JD参考库，**已接入打分逻辑**（`app/scoring.py` 会自动读取对应领域的文件塞进评分prompt里，不是摆设）。目录结构是 `jd-reference-library/<领域id>/<描述性文件名>.md`，一个领域一个子文件夹、通常只有1个主文件（文件名不强制统一，但要能一眼看出是这个领域专属的，不要和别的领域共用同一份内容/标题——2026-09-18之前 `ds`（数据科学家）和 `mle`（机器学习工程师）两个领域曾共用过同一份文件和标题，已经拆分并各自改名为 `data_science_analytics_intern.md`（标题 *Data Science / Analytics*）和 `machine_learning_engineer_intern.md`（标题 *Machine Learning Engineering*），今后再拆分领域时要照此处理）。
  - 每个文件结构统一为：H1标题=领域英文名，H2=每条JD记录（`## JD N: Role Title, Company (note)`，附 `Source:` 来源链接 + `Collected: 日期`），内含三个固定顺序的H3子小节：`Responsibilities`（岗位实际工作内容）→ `Basic Requirements`（基本要求）→ `Bonus / Preferred Qualifications`（加分项）；文件末尾再加一个H2 `Implications for Our Framework`（写给我们自己看的分析笔记，评分时会自动过滤掉、不会喂给模型）。
  - **JD库内容全部是英文**。每个领域最初由人工研究收集了5份真实岗位JD打底（2026-08-28～09-01），此后由每天自动运行的定时任务（"JD库每日自动更新"）给每个领域研究并追加1条新的真实JD（同样用英文，且要求`公司+核心岗位性质`不能和该领域已有条目、以及有选题重叠风险的相邻领域（如ds/mle）重复）。目前（2026-09-18）每个领域共9条JD，累计261条。
  - 这个定时任务会在 `jd-reference-library/.auto_jd_run_count.txt` 里记录已经连续运行的天数，累计满30天会自动把自己暂停（`enabled: false`）并提醒Paul决定要不要继续/设上限——这是唯一控制JD库无限增长的机制，改动前留意一下这个计数文件和对应定时任务的状态。
- `reports/` — 存放具体学生的评估结果PDF报告（目前是逐份生成，暂无批量汇总功能）。

## 后续规划

- 每次新增JD参考，同步检查是否需要微调 `framework.json` 里对应领域的权重或加分项。
- 定期(比如每次JD库有大批量更新后)抽查一下各领域JD文件的标题和内容是不是还专属于自己这个领域，避免再出现类似ds/mle当初共用一份文件的情况。
- 目标是把这套框架和数据沉淀到可以支撑一个独立应用（网页/小程序），而不是仅停留在对话里——`framework.json` 就是为了这个目的设计的结构化数据层，将来后端/前端都可以直接读取它。
