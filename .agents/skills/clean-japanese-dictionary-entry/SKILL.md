---
name: clean-japanese-dictionary-entry
description: 按 easyjapanese 的修订版字段规则复审和清洗单条日语词典数据。用于检查词形、假名、声调、罗马字、中文释义、词性、例句和摘要，纠正高置信错误，删除 Agent 禁止字段，生成可提交的严格 JSON，并在无法可靠修正时返回原样提交及问题记录信息。
---

# 日语词条清洗

## 必读规则

每次处理词条前完整读取 references/cleaning-rules-revised.md。它是当前唯一有效的字段契约和质量门槛；不要寻找、引用或重建旧版 cleaning-rules.md，也不得只凭通用语言知识自行设计结构。

## 输入

输入是 GET /api/v2/agent/dictionary/reviews/next 返回的一条对象，包含：

- id
- words
- kana
- tone
- detail
- rome
- description

可以额外接收 $consult-shinmeikai-ocr-dictionary 给出的局部证据。OCR 只用于佐证，不能当作无条件正确的原始数据。

## 清洗流程

1. 锁定 id。任何情况下都不得修改、猜测或替换 id。
2. 检查输入结构，保存可用原值。证据不足时保留原值，不为了显得有改动而改写。
3. 核对词形与读音，再核对声调和罗马字的一致性。
4. 按词性逐项检查 meanings。删除明显重复、错配或无依据的义项；不要遗漏可靠的常见义项。
5. 检查每条例句的日语自然度、是否体现当前义项、中文翻译是否准确。优先修正已有例句；只有全部不可用且能高置信造句时才补充简短常用例句。
6. 生成简洁中文 description，使其概括保留后的主要中文义项。
7. 判断本条属于 `submit_changed`、`submit_unchanged` 还是 `submit_original_with_issue`。三种决策都构造提交对象；第三种必须保持 GET 返回的七个业务字段不变。
8. 对提交对象执行最终校验。不得夹带解释、Markdown 或未定义字段。问题原因作为独立任务控制信息返回，不得放入提交对象。

## 判断原则

- 高置信错误：直接修正，例如明显错字、错误读音、例句翻译与日文相反、义项放错词性。
- 中等置信疑点：结合 OCR 上下文、词形、假名和例句交叉验证后再决定。
- 低置信疑点：保留原值。不要凭空新增冷僻义项、词形、声调或例句。
- OCR 与可靠日语知识冲突时，先考虑 OCR 识别错误。普通疑点无法消除时保留合理原值；影响关键字段且无法安全保留任一方案时判定 `submit_original_with_issue`。
- 罕见义项只有在证据足够时保留，不能仅因“不常见”删除。
- xmj.txt 只是含噪参考资料。单个 OCR 命中、单个声调符号或相邻文本不能单独证明需要修改词条。
- 如果原值本身合理，小范围证据不足不属于重大不确定性；保留原值并继续提交。
- 如果关键字段存在互斥结论，且选择任何一种都会有较大概率误导学习者，则判定为 `submit_original_with_issue`，不得猜测修改。

## 输出

使用以下三种内部决策之一：

- submit_changed：高置信完成修改，输出可 JSON 序列化的提交对象。
- submit_unchanged：完整复核后原数据仍可靠，输出保留原内容的提交对象。
- submit_original_with_issue：存在重大不确定性，输出原样提交对象，并向总控返回 word_id、reason_code=major_uncertainty、uncertain_fields、简短 reason 和 issue_log_required=true。

`submit_original_with_issue` 的提交对象必须保持 GET 返回的 `id、words、kana、tone、detail、rome、description` 不变，只增加 `ai_source`。reason 只陈述无法消除的冲突，不输出思维过程。提交成功后由总控调用 `$use-dictionary-review-agent-api` 的 `log-issue` 写入单独问题文件，然后继续下一条。

可提交对象必须增加 ai_source，并且禁止在 JSON 前后写说明。

如果输入结构无法满足 words 和 detail 非空，导致原样对象也无法通过接口最低校验，返回 `blocked_entry` 给总控 skill，不要伪造可提交对象。普通内容冲突不得返回 blocked_entry。
