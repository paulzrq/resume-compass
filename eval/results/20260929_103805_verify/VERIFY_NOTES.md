# 便宜模式真实验证（2026-09-29）

模型：claude-haiku-4-5-20251001，max_revisions=1，JD 检索 k=2，
prompt caching 开启，JD 截断 1200 字符。只跑新链路。

## 实测成本（美元/份）

| 简历 | calls | input | output | cache_write | cache_read | 费用 | critic |
|------|-------|-------|--------|-------------|------------|------|--------|
| R02 | 7 | 26632 | 8396 | 4244 | 16976 | $0.0544 | 打回，2 corrections |
| R03 | 10 | 72494 | 7965 | 4521 | 31647 | $0.0850 | 通过，0 corrections |
| R01（重试） | 9 | 43224 | 7897 | 4484 | 26904 | $0.0596 | 打回，2 corrections |

- 平均约 **$0.066/份**（三次：0.0596 / 0.0544 / 0.0850）。
- 对比：静态估算 $0.11/份（估算偏高但量级正确）；原新链路 $0.35/份（便宜约 5 倍）；
  旧链路 $0.023/份（仍贵约 3 倍）。
- 成本结构：input 的 44–64% 命中 cache read（$0.1/1M，几乎免费）；
  **output token 是成本大头**（R02 output 占 77%）。进一步压缩应优先考虑输出长度，
  而不是输入。
- cost_of() 口径验证：input ≥ cache_write + cache_read 恒成立，
  减法公式不会算出负数，与 Anthropic usage 字段口径一致。

## Critic 行为验证

- R02：critic 抓出 scorer 2 处"证据造假"（proj/skill 的 rationale 臆测简历没有的内容），
  各降 1 分（3→2），**corrections 已进入最终 dimension_scores**（proj=2, skill=2）。
  critic v2 直接改分链路端到端跑通。
- R03：critic 通过，只给了 2 条不影响分数的措辞建议。
- 快照 bug 仍在：n_snapshots=0，round1_scores/final_snap_scores 为空，
  修订前后对比依然测不出（已知遗留问题）。

## R01 失败：JSON 鲁棒性

- `JSONDecodeError: Invalid control character at line 7 column 40`
- 与原 eval 中 R13 失败同一类问题。Haiku 下 scorer 直接输出 JSON 时偶发非法控制字符，
  面试可作为"已知短板 + 修复方向（结构化输出/容错解析）"来讲。
- R01 重试一次即成功（$0.0596，9 calls，critic 打回 2 corrections），说明是偶发非必现。
