# JD 参考库结构说明

每个评分方向一个专属子文件夹，文件夹名 = `framework.json` 里的 `field_id`。往某个方向的文件夹里放 `.md` 格式的真实招聘JD（岗位描述），评分时会自动读取该方向文件夹下的所有 `.md` 文件、拼接注入到打分prompt里，作为真实招聘标准的参考——不放的话不影响正常打分，只是少一份参考依据。

一个方向可以放多份JD（比如同一方向下不同公司的JD），文件名随意，只要是 `.md` 后缀、放对文件夹即可。

## 目录 = 29个评分方向

| field_id | 名称 | 分类 |
|---|---|---|
| swe | 软件工程师 | 技术与工程 |
| ds | 数据科学家 | 技术与工程 |
| mle | 机器学习工程师 | 技术与工程 |
| engineering | 硬件工程师 | 技术与工程 |
| cybersecurity | 网络安全工程师 | 技术与工程 |
| robotics | 机器人/自动化工程师 | 技术与工程 |
| fintech_eng | 金融科技工程师 | 技术与工程 |
| finance | 投行分析师 | 商业与管理 |
| risk_analyst | 风险分析师 | 商业与管理 |
| actuary | 精算师 | 商业与管理 |
| consulting | 咨询顾问 | 商业与管理 |
| marketing | 市场营销 | 商业与管理 |
| pm | 产品经理 | 商业与管理 |
| ba | 商业分析师 | 商业与管理 |
| ops | 供应链管理 | 商业与管理 |
| data_analyst | 数据分析师 | 商业与管理 |
| accounting | 会计师/审计师 | 商业与管理 |
| hr | 人力资源 | 商业与管理 |
| sales | 销售 | 商业与管理 |
| law | 律师 | 专业服务与内容 |
| clinical_research | 临床研究助理 | 专业服务与内容 |
| biomed | 生物医药 / 医疗健康 | 专业服务与内容 |
| media | 传媒/公关 | 专业服务与内容 |
| film_production | 影视制作人 | 专业服务与内容 |
| teacher | 教师 | 专业服务与内容 |
| ux | UI/UX设计师 | 设计与创意 |
| graphic_design | 平面设计师 | 设计与创意 |
| architecture | 建筑师 | 设计与创意 |
| game_design | 游戏设计师 | 设计与创意 |

其中 `cybersecurity`、`robotics`、`fintech_eng`、`risk_analyst`、`actuary`、`clinical_research`、`film_production`、`teacher`、`graphic_design`、`architecture`、`game_design` 这11个是本次新加入的方向（对应 `app/mascots/` 里28个职业角色插画中原本没有评分方向的部分）。

方向的显示名称（`name` 字段）已按 `app/mascots/职业名称.txt` 里的原始措辞逐条对齐（如"咨询"→"咨询顾问"、"商业分析"→"商业分析师"、"用户体验设计"→"UI/UX设计师"等），确保和插画命名完全一致；`biomed`（生物医药 / 医疗健康）不在这28个插画角色名单里，名称保持原样未改动。
