---
name: review-japanese-dictionary-entries
description: 对 easyjapanese 中已完成人工审核但 ai_reviewed_at 仍为空的日语词条执行端到端 AI 二次复审。用于持续循环获取词条直至队列完成、按修订规则清洗、谨慎参考 xmj.txt、提交结果，并把无法可靠修正的问题词条原样提交后逐条记录到项目根目录的问题日志中。
---

# 日语词条 AI 二次复审

## 任务目标

持续处理 japanese_dict_review 中存在有效人工审核记录且 ai_reviewed_at 为空的词条。默认在一次任务中持续循环，直到 GET 明确返回 null；不得因已处理数量达到某个默认值而提前停止。每条数据都必须经过检查。可靠词条使用 `submit_changed` 或 `submit_unchanged`；存在无法可靠消除的内容问题时使用 `submit_original_with_issue`，原样提交并单独记录问题，随后继续循环。

内容问题不得中断整体循环。这里的“原样提交”是指保持 GET 返回的 `id、words、kana、tone、detail、rome、description` 七个业务字段不变，只增加接口要求的 `ai_source`。不得把猜测性修复混入原样提交。

不要使用 japanese_dict.review 判断本任务范围。实际候选条件由 Agent API 负责。

## 依赖能力

开始前使用并遵守以下三个 sibling skill：

- $use-dictionary-review-agent-api：接口契约、调用方式和错误处理。
- $clean-japanese-dictionary-entry：字段级清洗、校验和输出结构。
- $consult-shinmeikai-ocr-dictionary：可选的新明解 OCR 文本证据。

xmj.txt 只提供辅助证据，不具有最终裁决权。未命中或出现普通 OCR 噪声时继续复审；关键字段发生无法消除的重大冲突时交由清洗 skill 判定 `submit_original_with_issue`。

## 运行配置

- 基础地址优先读取 EASYJAPANESE_AGENT_BASE_URL，默认 http://127.0.0.1:8000。
- 默认不设处理条数上限，持续运行到队列 COMPLETE 或触发其他停止条件。
- 只有显式把 EASYJAPANESE_REVIEW_BATCH_SIZE 配置为正整数时，才将其作为本次任务的可选处理上限；未配置或为空时不得启用批次上限。
- ai_source 固定为 chatgpt-5.6，除非任务调用方明确指定其他值。
- 本地事件日志优先读取 EASYJAPANESE_REVIEW_LOG_PATH，默认 .agents/logs/agent_dictionary_review.jsonl。
- 问题词条目录优先读取 EASYJAPANESE_REVIEW_ISSUE_DIR，默认使用项目根目录下的 `review_issue_logs`。每个问题词条单独保存为 `word-<id>.json`。
- 接口当前无认证，只允许连接用户指定的可信后端地址。

## 循环流程

1. 调用 GET /api/v2/agent/dictionary/reviews/next。
2. 返回 null 时立即停止，不调用提交接口，并把队列标记为 COMPLETE。
3. 返回对象时记录原始 id，随后只处理这一条。不得在成功提交前预取下一条。
4. 使用 OCR skill 查找同词形、同读音的证据。未找到或未安装词典时记录 unavailable 或 not_found，继续下一步。
5. 使用清洗 skill 检查 words、kana、tone、detail、rome、description，并先得到 `submit_changed`、`submit_unchanged` 或 `submit_original_with_issue` 决策。
6. 如果决策为 `submit_original_with_issue`，使用本轮 GET 对象构造原样提交对象：七个业务字段保持不变，只增加 `ai_source`。同时保留 `word_id`、`words`、`uncertain_fields` 和简短 `reason`，供提交成功后写问题文件。
7. 对提交对象做提交前校验：id 必须等于本轮 GET 的 id；words 和 detail 非空；detail 不含 meanings.jp、examples.read、examples.voice；ai_source 已设置。
8. 调用 POST /api/v2/agent/dictionary/reviews/submit。只有 2xx 响应才计为已完成。
9. 对 `submit_original_with_issue`，提交成功后立即调用 API skill 的 `log-issue`，在项目根目录 `review_issue_logs/word-<id>.json` 写入问题 ID、词形、冲突字段、问题原因、提交状态和时间。问题文件只写结论，不写内部推理。写入失败最多重试 3 次；即使最终失败，也记录 `issue_log_failure` 并继续 GET，不得因内容问题打断队列。
10. 累加 processed、changed、unchanged 和 problematic 计数。原样问题提交同时计入 submitted、unchanged 和 problematic，然后回到步骤 1。
11. 仅当 EASYJAPANESE_REVIEW_BATCH_SIZE 已显式配置为正整数且本轮达到该上限时，停止并标记 BATCH_LIMIT。未配置该变量时必须回到步骤 1，直到返回 null 或触发非内容类停止条件。

内容没有可靠修改点时，提交经过验证的原内容并计为 unchanged。服务端会写入 ai_reviewed_at，因此成功提交仍代表完成了二次复审。

## 停止与失败规则

- COMPLETE：GET 明确返回 null。这是唯一的全量任务完成条件。
- BATCH_LIMIT：仅在显式配置 EASYJAPANESE_REVIEW_BATCH_SIZE 且本轮达到该正整数上限时使用，队列可能仍有数据。不得使用隐含或默认的条数上限生成此状态。
- 内容疑点、字段串条、读音冲突和无法可靠消歧均不再是停止条件；必须原样提交、单独记录并继续。
- BLOCKED_ENTRY：只用于原始对象本身不满足接口最低结构要求，导致无法构造合法的原样提交对象，例如 words 或 detail 为空。不得为通过接口而虚构内容。
- INFRA_ERROR：接口不可达、超时或连续返回服务端错误。
- VALIDATION_ERROR：提交持续返回 422，或返回体不符合接口契约。

连接失败和 5xx 最多重试 3 次。相同 id 连续失败 2 次后停止本轮，避免无限读取同一条。POST 返回“已经 AI 审核”时视为并发跳过并重新 GET；其他 400/404 必须停止并报告，不要绕过业务校验或直接改数据库。

所有接口请求错误必须由助手脚本自动落入本地 JSONL 日志。内容问题在原样提交成功后写独立问题文件；写入失败不得假装成功，应计入 `issue_log_failures`，但继续处理队列。

## 完成报告

每轮结束只给出简短结构化摘要：

- status：COMPLETE、BATCH_LIMIT、BLOCKED_ENTRY、INFRA_ERROR 或 VALIDATION_ERROR。
- fetched、submitted、changed、unchanged、problematic、failed。
- queue_complete：仅 COMPLETE 为 true。
- last_word_id：没有取到词条时为 null。
- log_path：本轮使用的本地 JSONL 日志路径。
- issue_log_dir：问题词条文件目录。
- issue_logs_written：成功写入的问题词条文件数。
- issue_log_failures：问题文件最终写入失败数。
- error：正常结束时为 null，否则写简短错误，不包含推理过程。

不要在报告中输出整批词条内容，也不要把内部分析写入 API 数据。
