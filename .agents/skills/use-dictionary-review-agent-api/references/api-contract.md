# 词条复审 Agent API 契约

## 目录

- 基本信息
- 获取下一条待审核词条
- 提交审核结果
- 错误响应
- 问题词条与队列语义
- 助手脚本行为
- 本地事件日志

## 基本信息

- 默认基础地址：`http://127.0.0.1:8000`
- 接口前缀：`/api/v2/agent/dictionary/reviews`
- 请求和响应类型：`application/json`
- 认证：当前两个接口均不需要认证
- 安全边界：无认证接口只能用于可信网络，不得直接暴露到公网

所有成功响应使用统一包装：

```json
{
  "code": "OK",
  "msg": "success",
  "data": {}
}
```

所有错误响应也使用 `code`、`msg`、`data` 三个字段。业务错误的 `data` 通常为 `null`。

## 获取下一条待审核词条

请求：

```http
GET /api/v2/agent/dictionary/reviews/next
Accept: application/json
```

接口每次最多返回一条数据。候选词条必须满足：

- `japanese_dict` 未软删除。
- `japanese_dict_review` 中存在相同 `dict_id` 的有效记录。
- `japanese_dict_review` 未软删除。
- `japanese_dict_review.ai_reviewed_at` 为 `null`。

查询按审核记录 `created_at`、词条 `id` 升序取第一条。业务上的“已人工审核”由存在有效的 `japanese_dict_review` 记录表示，不读取 `japanese_dict.review` 字段。

有待审数据时返回 HTTP 200：

```json
{
  "code": "OK",
  "msg": "success",
  "data": {
    "id": 30259,
    "words": ["左"],
    "kana": "ひだり",
    "tone": "⓪",
    "detail": [
      {
        "type": "名词",
        "meanings": [
          {
            "zh": "左；左边",
            "examples": [
              {
                "jp": "最初の十字路を左へ曲がります",
                "zh": "在第一个十字路口向左转。"
              }
            ]
          }
        ]
      }
    ],
    "rome": "hidari",
    "description": "左；左边"
  }
}
```

`data` 中的词条字段为：

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| `id` | integer | 否 | 词条 ID |
| `words` | string[] | 否 | 词形列表 |
| `kana` | string | 是 | 假名 |
| `tone` | string | 是 | 声调 |
| `detail` | object[] | 否 | 词义详情 |
| `rome` | string | 是 | 罗马音 |
| `description` | string | 是 | 简短释义 |

返回的 `detail` 已移除以下服务端管理字段：

- `meanings[].jp`
- `meanings[].examples[].read`
- `meanings[].examples[].voice`

没有待审数据时仍返回 HTTP 200，只有 `data` 为 `null`：

```json
{
  "code": "OK",
  "msg": "success",
  "data": null
}
```

`data === null` 是队列全部完成的唯一判断依据，不能把整个响应对象与 `null` 比较。

## 提交审核结果

请求：

```http
POST /api/v2/agent/dictionary/reviews/submit
Content-Type: application/json
```

请求体示例：

```json
{
  "id": 30259,
  "words": ["左"],
  "kana": "ひだり",
  "tone": "⓪",
  "detail": [
    {
      "type": "名词",
      "meanings": [
        {
          "zh": "左；左边",
          "examples": [
            {
              "jp": "最初の十字路を左へ曲がります",
              "zh": "在第一个十字路口向左转。"
            }
          ]
        }
      ]
    }
  ],
  "rome": "hidari",
  "description": "左；左边",
  "ai_source": "chatgpt-5.6"
}
```

请求字段约束：

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `id` | integer | 是 | 大于等于 1，必须与本轮 GET 返回的 `data.id` 一致 |
| `words` | string[] | 是 | 不得为空 |
| `kana` | string/null | 否 | 最长 255 字符 |
| `tone` | string/null | 否 | 最长 100 字符 |
| `detail` | object[] | 是 | 不得为空 |
| `rome` | string/null | 否 | 最长 255 字符 |
| `description` | string/null | 否 | 最长 255 字符 |
| `ai_source` | string/null | 否 | 最长 50 字符；接口缺省值为 `agent` |

`detail` 使用以下嵌套结构：

```text
detail[]
  type: string | null
  meanings[]
    zh: string | null
    examples[]
      jp: string | null
      zh: string | null
```

本 Agent 契约不得提交 `meanings[].jp`、`examples[].read` 或 `examples[].voice`。服务端会根据日文例句重新生成 `read`，响应时再移除这些服务端管理字段。

成功时返回 HTTP 200，包装结构与 GET 相同，`data` 为更新后的词条对象。成功提交会在同一事务中：

- 覆盖词条的 `words`、`kana`、`tone`、`detail`、`rome`、`description`。
- 根据词形同步词条别名。
- 把 `japanese_dict_review.ai_source` 更新为请求值；空值按 `agent` 处理。
- 把 `japanese_dict_review.ai_reviewed_at` 更新为当前时间。

## 错误响应

错误响应示例：

```json
{
  "code": "AGENT_DICTIONARY_ALREADY_REVIEWED",
  "msg": "word has already been ai reviewed",
  "data": null
}
```

| HTTP 状态 | `code` | `msg` | 含义 |
| --- | --- | --- | --- |
| 400 | `AGENT_DICTIONARY_WORDS_EMPTY` | `words can not be empty` | `words` 为空 |
| 400 | `AGENT_DICTIONARY_DETAIL_EMPTY` | `detail can not be empty` | `detail` 为空 |
| 404 | `DICTIONARY_WORD_NOT_FOUND` | `word does not exist` | 词条不存在或已软删除 |
| 404 | `DICTIONARY_REVIEW_NOT_FOUND` | `review record does not exist` | 没有有效审核记录 |
| 409 | `AGENT_DICTIONARY_ALREADY_REVIEWED` | `word has already been ai reviewed` | 词条已经完成 AI 审核 |
| 422 | `COMMON_VALIDATION_FAILED` | `请求参数校验失败` | 类型、长度或嵌套结构不符合 Schema |
| 500 | `INTERNAL_ERROR` | `internal server error` | 服务端或数据库异常 |

422 的 `data.issues` 包含字段位置和校验原因。5xx 或提交过程异常时，数据库事务会回滚。

提交接口不是幂等接口。第一次成功后再次提交同一 `id` 会返回 HTTP 409。调用方只有在收到 HTTP 2xx、响应 `code` 为 `OK` 且存在 `data` 字段后，才能把本条计为完成。

## 问题词条与队列语义

清洗结果为 `submit_original_with_issue` 时，调用方必须保持 GET 返回的 `id、words、kana、tone、detail、rome、description` 七个业务字段不变，只增加 `ai_source` 后调用提交接口。

当前 GET 接口没有 `cursor`、`exclude_ids` 或 `skip` 参数。为避免问题词条永久阻塞队列，原样提交成功后使用 `log-issue` 在项目根目录为该 ID 创建独立问题文件，然后继续 GET。内容问题本身不得停止连续审核任务。

## 助手脚本行为

助手脚本已适配 v2 成功响应包装：它会验证响应 `code === "OK"`，然后只把 `data` 写到标准输出。因此：

- `next` 有数据时输出词条对象。
- `next` 无数据时输出 JSON `null`。
- `submit` 成功时输出更新后的词条对象。
- `log-issue` 成功时输出问题文件路径，不调用远程接口。
- HTTP 错误、无效 JSON 或无效成功包装写到标准错误，并返回非零退出码。

脚本没有改变接口本身的响应格式；直接发 HTTP 请求时仍需读取统一包装中的 `data`。

获取下一条：

```powershell
python .agents/skills/use-dictionary-review-agent-api/scripts/review_api.py next
```

指定服务地址：

```powershell
python .agents/skills/use-dictionary-review-agent-api/scripts/review_api.py --base-url http://127.0.0.1:8000 next
```

从 UTF-8 JSON 文件提交：

```powershell
python .agents/skills/use-dictionary-review-agent-api/scripts/review_api.py submit --input payload.json
```

也可以从标准输入提交。`EASYJAPANESE_AI_SOURCE` 可覆盖脚本默认注入的 `chatgpt-5.6`。

## 本地事件日志

助手脚本默认把事件追加到 `.agents/logs/agent_dictionary_review.jsonl`。可通过环境变量 `EASYJAPANESE_REVIEW_LOG_PATH` 或全局参数 `--log-path` 指定其他本地路径。

以下接口错误必须写入 JSONL 日志：

- `next` 或 `submit` 的输入校验错误。
- HTTP 4xx、5xx 响应。
- 网络、连接或超时错误。
- 响应不是有效 JSON，或成功响应不符合 v2 包装结构。

接口请求错误由脚本自动记录。

## 问题词条文件

无法可靠修正的词条原样提交成功后，使用：

```powershell
python .agents/skills/use-dictionary-review-agent-api/scripts/review_api.py log-issue --word-id 30259 --word 左 --uncertain-field kana --uncertain-field type --message "词形对应两个互斥读音，现有证据不足以消歧。"
```

默认目录是项目根目录 `review_issue_logs`，可用 `EASYJAPANESE_REVIEW_ISSUE_DIR` 或 `--issue-dir` 覆盖。每个词条保存为 `word-<id>.json`，内容包括 `timestamp`、`submission_status=submitted_original`、`word_id`、`words`、`uncertain_fields`、`reason` 和 `ai_source`。

问题文件禁止保存认证信息和模型内部推理。写入失败时最多重试 3 次；最终失败计入完成报告，但继续处理队列。
