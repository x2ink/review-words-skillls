---
name: use-dictionary-review-agent-api
description: 调用 easyjapanese 无认证词条复审 Agent API，获取并提交清洗结果，为每次成功提交按词条 ID 保存完整 before/after 审核快照，同时记录接口错误和原样问题词条。用于自动审核循环、接口联调、质量追踪和队列完成判断。
---

# 词条复审 Agent API

## 必读文档

调用前完整读取 references/api-contract.md。接口路径、字段、状态变化和失败语义以该文档及当前项目代码为准。

## 助手脚本

优先使用 scripts/review_api.py，避免在命令行手工转义大型 JSON。

- next 子命令只读，调用获取下一条接口并向标准输出写 JSON，同时默认把完全相同的数据保存到 `.review_before.json`。
- submit 子命令从文件或标准输入读取 JSON，并读取本轮 `.review_before.json`。该操作会真实更新词条、写入 ai_reviewed_at，并自动创建完整 before/after 审核文件。
- log-audit 子命令不调用接口，只用于在提交已经成功但审核文件写入失败时重试写文件。
- log-issue 子命令不调用接口，为一个已原样提交的问题词条创建独立 JSON 文件。
- log-uncertainty 是兼容旧流程的 JSONL 命令；当前连续审核流程不得用它代替 log-issue。
- 基础地址优先读取 EASYJAPANESE_AGENT_BASE_URL，也可通过 --base-url 指定。
- 日志路径优先读取 EASYJAPANESE_REVIEW_LOG_PATH，默认 .agents/logs/agent_dictionary_review.jsonl，也可通过 --log-path 指定。
- 问题文件目录优先读取 EASYJAPANESE_REVIEW_ISSUE_DIR，默认项目根目录 `review_issue_logs`，也可通过 --issue-dir 指定。
- 审核快照目录优先读取 EASYJAPANESE_REVIEW_AUDIT_DIR，默认项目根目录 `review_audit_logs`，也可通过 --audit-dir 指定。每个词条保存为 `<id>.json`。
- 脚本只使用 Python 标准库，不要求激活项目环境。

在项目根目录运行脚本。提交前先在内存中检查对象；如果使用临时 JSON 文件，任务结束后删除该临时文件，不要把词条数据长期留在仓库。

## 调用纪律

1. 每次先 next，成功获得对象后才允许 submit。
2. submit 必须同时读取本轮 next 自动保存的 before 文件；before.id、after.id 和本轮 next 的 id 必须完全一致。
3. next 返回 null 时禁止 submit。
4. 本 skill 不自行扩大或缩小“重大不确定性”的范围。只有清洗 skill 已按双重门槛明确返回 `submit_original_with_issue` 时，才以 GET 返回的七个业务字段原样构造提交对象，只增加 ai_source；空声调、OCR 未命中或普通可修字段错误不得触发此流程，而清洗 skill 已识别的词条身份分裂不得降级为普通修改。提交成功后调用 log-issue，并继续 next。
5. 每次 submit 成功后必须存在 `review_audit_logs/<id>.json`。其中 `before` 必须是 GET 返回的完整原始数据，`after` 必须是实际发送给 POST 的完整请求体；不得摘要、删字段、重写或只记录差异。
6. 审核快照写入失败时不得再次 submit；必须使用 log-audit 最多重试 3 次。仍失败则停止本轮并报告 AUDIT_LOG_ERROR，避免继续产生无法追踪的提交。
7. log-issue 必须为每个问题 ID 单独创建 `review_issue_logs/word-<id>.json`，写明 ID、词形、问题字段、简短原因、提交状态和时间。不得写模型内部推理。
8. 问题文件写入失败最多重试 3 次；最终失败要计入报告，但不得中断队列循环。
9. next、submit 的输入、HTTP、网络和响应解析错误由脚本自动写入本地 JSONL 日志。发现输出中的 log_error 时，必须在任务报告中说明日志写入失败。
10. 提交成功前不要并行获取下一条。当前接口不提供租约或锁。
11. HTTP 2xx 且响应 JSON 可解析，才视为成功。
12. 不得通过数据库 SQL 代替提交接口，也不得直接填写 ai_reviewed_at。
13. 不要向未得到用户授权的远程地址发送词条数据。

## 输出约束

助手脚本的标准输出只包含接口 JSON，适合交给下一个 skill。失败信息写到标准错误并返回非零退出码。调用方必须保留错误信息用于本轮摘要，但不要把错误文本混进提交 JSON。

错误事件日志每行一个 JSON 对象，只保存时间、事件类型、接口上下文、word_id、状态码、冲突字段和简短消息。完整词条和完整请求体只写入本地 `review_audit_logs/<id>.json` 的 `before` 与 `after`，不得写入错误 JSONL；任何日志都不得包含内部推理或认证信息。
