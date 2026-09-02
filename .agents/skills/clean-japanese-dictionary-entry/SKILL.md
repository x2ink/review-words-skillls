---
name: clean-japanese-dictionary-entry
description: 按 easyjapanese 的修订版字段规则逐字段复审和清洗单条日语词典数据。用于纠正高置信错误、保留证据不足的局部原值并生成严格 JSON；只有关键字段互斥且无法形成安全一致结果时才返回原样提交及问题记录信息。
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

## 字段独立原则

- 分别判断 `words`、`kana`、`tone`、`detail`、`rome` 和 `description`。一个字段未知，不得阻止其他字段的高置信修正。
- 对可确认的错误直接修正；对证据不足的局部字段保留原值。修正后的对象只要整体一致，就使用 `submit_changed`。
- `tone: ""` 是允许的已审核结果。没有可靠声调证据时保持空字符串，不记录问题，也不得据此原样提交整条词条。
- OCR 未命中完整词条很常见，特别是复合词、短语和专名。它只表示没有获得这项辅助证据，不表示词条身份可疑。
- 可安全删除的坏义项、坏例句、重复内容，以及可明确规范化的 `rome` 或 `type`，都属于普通清洗，不属于重大不确定性。
- 只有无法消除的关键互斥冲突使得任何可构造结果都可能明显误导学习者时，才允许 `submit_original_with_issue`。

典型决策：

- `博士課程` 的 `kana` 若误为 `はかせかてい`，应改为 `はくしかてい`，同步修正 `rome`；没有声调依据则保留 `tone: ""`，决策为 `submit_changed`。
- `期間中` 的内容可靠但 `rome` 含空格时，去除空格并使用 `submit_changed`；空声调和 OCR 未命中不改变该决策。
- 所有字段均合理且没有高置信修改点时使用 `submit_unchanged`，不能为了制造审核痕迹而改写。

## 判断原则

- 高置信错误：直接修正，例如明显错字、错误读音、例句翻译与日文相反、义项放错词性。
- 中等置信疑点：结合 OCR 上下文、词形、假名和例句交叉验证后再决定。
- 低置信疑点：保留原值。不要凭空新增冷僻义项、词形、声调或例句。
- OCR 与可靠日语知识冲突时，先考虑 OCR 识别错误。普通疑点无法消除时仅保留对应字段的合理原值；只有关键字段互斥且无法安全保留任一方案、删除局部坏数据或构造一致结果时，才判定 `submit_original_with_issue`。
- 罕见义项只有在证据足够时保留，不能仅因“不常见”删除。
- xmj.txt 只是含噪参考资料。单个 OCR 命中、单个声调符号或相邻文本不能单独证明需要修改词条。
- 如果原值本身合理，小范围证据不足不属于重大不确定性；保留原值并继续提交。
- 如果关键字段存在互斥结论，且选择任何一种都会有较大概率误导学习者，同时保留原字段也无法得到安全一致的结果，才判定为 `submit_original_with_issue`，不得猜测修改。

## 输出

使用以下三种内部决策之一：

- submit_changed：高置信完成修改，输出可 JSON 序列化的提交对象。
- submit_unchanged：完整复核后原数据仍可靠，输出保留原内容的提交对象。
- submit_original_with_issue：存在无法通过局部保留、修正或删除解决的关键互斥冲突，输出原样提交对象，并向总控返回 word_id、reason_code=major_uncertainty、uncertain_fields、简短 reason 和 issue_log_required=true。

`submit_original_with_issue` 的提交对象必须保持 GET 返回的 `id、words、kana、tone、detail、rome、description` 不变，只增加 `ai_source`。reason 只陈述无法消除的冲突，不输出思维过程。提交成功后由总控调用 `$use-dictionary-review-agent-api` 的 `log-issue` 写入单独问题文件，然后继续下一条。

可提交对象必须增加 ai_source，并且禁止在 JSON 前后写说明。

如果输入结构无法满足 words 和 detail 非空，导致原样对象也无法通过接口最低校验，返回 `blocked_entry` 给总控 skill，不要伪造可提交对象。普通内容冲突不得返回 blocked_entry。
