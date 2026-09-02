---
name: review-japanese-dictionary-entries
description: 对 easyjapanese 中已完成人工审核但 ai_reviewed_at 仍为空的日语词条执行端到端 AI 二次复审。用于持续循环获取词条直至队列完成、逐字段清洗并提交结果；只有关键内容存在无法消除的互斥冲突且不能形成安全一致结果时，才原样提交并记录问题。
---

# 日语词条 AI 二次复审

## 任务目标

持续处理 japanese_dict_review 中存在有效人工审核记录且 ai_reviewed_at 为空的词条。默认在一次任务中持续循环，直到 GET 明确返回 null；不得因已处理数量达到某个默认值而提前停止。每条数据都必须经过检查。只要存在至少一个高置信、可安全落地的字段修正，就使用 `submit_changed`；完整复核后没有修正点时使用 `submit_unchanged`。只有关键内容存在无法消除的互斥冲突，并且保留不确定字段也无法形成安全一致结果时，才使用 `submit_original_with_issue`，原样提交并单独记录问题，随后继续循环。

内容问题不得中断整体循环。这里的“原样提交”是指保持 GET 返回的 `id、words、kana、tone、detail、rome、description` 七个业务字段不变，只增加接口要求的 `ai_source`。不得把猜测性修复混入原样提交。

不要使用 japanese_dict.review 判断本任务范围。实际候选条件由 Agent API 负责。

## 依赖能力

开始前使用并遵守以下三个 sibling skill：

- $use-dictionary-review-agent-api：接口契约、调用方式和错误处理。
- $clean-japanese-dictionary-entry：字段级清洗、校验和输出结构。
- $consult-shinmeikai-ocr-dictionary：可选的新明解 OCR 文本证据。

xmj.txt 只提供辅助证据，不具有最终裁决权。未命中或出现普通 OCR 噪声时继续复审；关键字段发生无法消除的重大冲突时交由清洗 skill 判定 `submit_original_with_issue`。

## 决策护栏

每个字段独立审核，不确定性不得从一个字段扩散到整条词条：

1. 先修正所有高置信错误，再对缺少可靠证据的字段保留原值。
2. 只要最终对象中任一业务字段发生高置信修改，决策就是 `submit_changed`，即使 `tone` 仍为空或其他非关键字段保持原值。
3. 没有可靠修改点且原内容可安全提交时，决策是 `submit_unchanged`。
4. `submit_original_with_issue` 是最后手段。只有关键字段存在两个或更多合理但互斥的结论，且既不能可靠选择，也不能通过保留该字段、删除坏义项或坏例句形成一致结果时才可使用。

下列情况不得单独触发 `submit_original_with_issue`：

- `tone` 为空且没有可靠声调证据；保持空字符串即可。
- `xmj.txt` 未命中、命中乱码、缺少完整复合词或只给出弱证据。
- 词条是复合词、短语、机构名、专名，或仅因其“不像普通单词”而产生疑问。
- `rome` 含空格、大小写或与已确认 `kana` 存在可直接修正的不一致。
- `type` 标签不规范但能明确映射到项目已有类型。
- 个别义项或例句错误、重复、串入无关内容，但可以安全修正或删除。
- 无法确认是否应新增冷僻义项；不新增即可。

例如，确认 `博士課程` 的读音应为 `はくしかてい` 时，应修正 `kana` 和 `rome`，未知声调继续保留空字符串，并使用 `submit_changed`。不得因为声调为空或 OCR 没有完整命中而整条原样提交。

### 词条身份分裂硬规则

如果关键字段明显分成两组，并且每组分别指向不同但都成立的日语词条或用法，则视为“词条身份分裂”。自动循环不得根据字段数量、个人判断或“较可能的正确答案”选择其中一组，也不得重写成某一目标词条；必须使用 `submit_original_with_issue`，记录冲突字段并交由人工确定目标。

典型情况：

- `words=金子、kana=かねこ` 指向姓氏“金子”，而 `rome=kinsu` 与“金钱、金币”义项指向 `きんす`。这是两个词条身份之间的冲突，必须原样提交。
- `words=建、kana=けん`，但 `rome=tate` 且例句全部使用动词 `建てる`。无法从记录本身确定目标是 `建`、`建て` 还是 `建てる`，必须原样提交。

只有用户明确指定目标词条，或存在能够直接确认该记录目标身份的可信上游依据时，才可按明确指示修正。普通 OCR 命中或通用日语知识只能说明某个候选本身成立，不能证明数据库原本想保存哪一个候选。

## 运行配置

- 基础地址优先读取 EASYJAPANESE_AGENT_BASE_URL，默认 http://127.0.0.1:8000。
- 默认不设处理条数上限，持续运行到队列 COMPLETE 或触发其他停止条件。
- 只有显式把 EASYJAPANESE_REVIEW_BATCH_SIZE 配置为正整数时，才将其作为本次任务的可选处理上限；未配置或为空时不得启用批次上限。
- ai_source 固定为 chatgpt-5.6，除非任务调用方明确指定其他值。
- 本地事件日志优先读取 EASYJAPANESE_REVIEW_LOG_PATH，默认 .agents/logs/agent_dictionary_review.jsonl。
- 问题词条目录优先读取 EASYJAPANESE_REVIEW_ISSUE_DIR，默认使用项目根目录下的 `review_issue_logs`。每个问题词条单独保存为 `word-<id>.json`。
- 审核快照目录优先读取 EASYJAPANESE_REVIEW_AUDIT_DIR，默认使用项目根目录下的 `review_audit_logs`。每个成功提交的词条保存为 `<id>.json`。
- 接口当前无认证，只允许连接用户指定的可信后端地址。

## 循环流程

1. 调用 GET /api/v2/agent/dictionary/reviews/next，并由 API skill 把返回的完整词条对象原封不动保存为本轮 before 临时文件。
2. 返回 null 时立即停止，不调用提交接口，并把队列标记为 COMPLETE。
3. 返回对象时记录原始 id，随后只处理这一条。不得在成功提交前预取下一条。
4. 使用 OCR skill 查找同词形、同读音的证据。未找到或未安装词典时记录 unavailable 或 not_found，继续下一步。
5. 使用清洗 skill 逐字段检查 words、kana、tone、detail、rome、description，先应用所有高置信修正，再按“有修改即 changed、无修改且安全即 unchanged、仅无法形成安全一致结果才 original_with_issue”的顺序得到决策。
6. 如果决策为 `submit_original_with_issue`，使用本轮 GET 对象构造原样提交对象：七个业务字段保持不变，只增加 `ai_source`。同时保留 `word_id`、`words`、`uncertain_fields` 和简短 `reason`，供提交成功后写问题文件。
7. 对提交对象做提交前校验：id 必须等于本轮 GET 的 id；words 和 detail 非空；detail 不含 meanings.jp、examples.read、examples.voice；ai_source 已设置。
8. 调用 POST /api/v2/agent/dictionary/reviews/submit。API skill 必须同时读取 before 临时文件和实际提交对象；只有收到 2xx，且成功创建 `review_audit_logs/<id>.json` 后，才完成本条流程。
9. 审核快照必须逐字保存两个完整对象：`before` 等于本轮 GET 返回数据，`after` 等于实际 POST 请求体，包括最终 `ai_source`。不得只保存差异，不得用清洗后的数据覆盖 before，也不得把解释或推理写入任一对象。
10. 如果 POST 已成功但审核快照写入失败，只调用 `log-audit` 重试写文件，禁止重复 POST。最多重试 3 次；仍失败则停止本轮并报告 AUDIT_LOG_ERROR，避免后续词条继续缺失审核轨迹。
11. 对 `submit_original_with_issue`，提交成功后立即调用 API skill 的 `log-issue`，在项目根目录 `review_issue_logs/word-<id>.json` 写入问题 ID、词形、冲突字段、问题原因、提交状态和时间。问题文件只写结论，不写内部推理。写入失败最多重试 3 次；即使最终失败，也记录 `issue_log_failure` 并继续 GET，不得因内容问题打断队列。
12. 累加 processed、changed、unchanged 和 problematic 计数。原样问题提交同时计入 submitted、unchanged 和 problematic，然后回到步骤 1。
13. 仅当 EASYJAPANESE_REVIEW_BATCH_SIZE 已显式配置为正整数且本轮达到该上限时，停止并标记 BATCH_LIMIT。未配置该变量时必须回到步骤 1，直到返回 null 或触发非内容类停止条件。

内容没有可靠修改点时，提交经过验证的原内容并计为 unchanged。服务端会写入 ai_reviewed_at，因此成功提交仍代表完成了二次复审。

## 停止与失败规则

- COMPLETE：GET 明确返回 null。这是唯一的全量任务完成条件。
- BATCH_LIMIT：仅在显式配置 EASYJAPANESE_REVIEW_BATCH_SIZE 且本轮达到该正整数上限时使用，队列可能仍有数据。不得使用隐含或默认的条数上限生成此状态。
- 内容疑点、字段串条、读音冲突和无法可靠消歧均不再是停止条件。普通疑点按字段保留原值并正常提交；只有满足“关键结论互斥且无法形成安全一致结果”双重门槛时，才原样提交、单独记录并继续。
- BLOCKED_ENTRY：只用于原始对象本身不满足接口最低结构要求，导致无法构造合法的原样提交对象，例如 words 或 detail 为空。不得为通过接口而虚构内容。
- INFRA_ERROR：接口不可达、超时或连续返回服务端错误。
- VALIDATION_ERROR：提交持续返回 422，或返回体不符合接口契约。
- AUDIT_LOG_ERROR：POST 已成功但 `<id>.json` 审核快照连续 3 次写入失败。不得重复 POST，也不得继续下一条。

连接失败和 5xx 最多重试 3 次。相同 id 连续失败 2 次后停止本轮，避免无限读取同一条。POST 返回“已经 AI 审核”时视为并发跳过并重新 GET；其他 400/404 必须停止并报告，不要绕过业务校验或直接改数据库。

所有接口请求错误必须由助手脚本自动落入本地 JSONL 日志。只有被清洗 skill 明确判定为 `submit_original_with_issue` 的重大冲突，才在原样提交成功后写独立问题文件；写入失败不得假装成功，应计入 `issue_log_failures`，但继续处理队列。

## 完成报告

每轮结束只给出简短结构化摘要：

- status：COMPLETE、BATCH_LIMIT、BLOCKED_ENTRY、INFRA_ERROR、VALIDATION_ERROR 或 AUDIT_LOG_ERROR。
- fetched、submitted、changed、unchanged、problematic、failed。
- queue_complete：仅 COMPLETE 为 true。
- last_word_id：没有取到词条时为 null。
- log_path：本轮使用的本地 JSONL 日志路径。
- issue_log_dir：问题词条文件目录。
- issue_logs_written：成功写入的问题词条文件数。
- issue_log_failures：问题文件最终写入失败数。
- audit_log_dir：完整 before/after 审核快照目录。
- audit_logs_written：成功写入的逐词条审核文件数。
- audit_log_failures：审核快照最终写入失败数。
- error：正常结束时为 null，否则写简短错误，不包含推理过程。

不要在报告中输出整批词条内容，也不要把内部分析写入 API 数据。
