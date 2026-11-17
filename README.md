# DAI OpenWebUI Adapter

独立的 **配置发布组件**：读取 M3 模型能力与 Agent Career Hub Gateway 的可用模型清单，通过 OpenWebUI 管理 API 发布每个模型的最大可用能力交集。它不代理推理，不操作 SSH、GPU、模型进程或 systemd，也不更改两个共享网关。

```text
推理：OpenWebUI -> Agent Gateway -> M3 -> Worker
配置：M3 能力 + Agent Gateway 清单 -> 本组件 -> OpenWebUI 模型管理 API
```

当前版本 **0.2.0** 默认 `publication.profile: auto`，逐模型协商，不再把视觉、工具等能力固定关闭。计划中同时显示模型与 OpenWebUI 的候选交集、实际网关链路能启用的交集，以及缺失声明/绑定的 TODO。`verified` 才进入交集，`unverified`、缺字段和 `unsupported` 不会因模型名称或 UI 默认值被提升。

对具备已验证聊天链路的模型发布：

- 同 ID 基础模型覆盖，`base_model_id=null`，覆盖直接使用基础模型的已有聊天。
- 独立工作空间预设，ID 为 `dai.chat.{logical_model_id}`，指向 OpenWebUI 的实际基础 ID。
- 流式未声明或不支持时，默认 `stream_response=false`；不再因此排除整个聊天模型。

逻辑模型 ID 与 GPU 节点无关。13 号机的 Muse、14 号机的 Swift 只是测试数据；新增模型不需要修改代码。OpenWebUI 连接的 `prefix_id` 会被正确应用到基础 ID，工作空间 ID 保持逻辑命名。

## 开发

需要 Python 3.11+，支持 Linux 本地文件锁。

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff format --check src tests
.venv/bin/python -m pytest -q
```

依赖仅为 HTTPX、PyYAML。组件不导入 M3、Worker、Agent Core 或 OpenWebUI 的 Python 包。

## 配置与凭据

从 [config/adapter.example.yaml](config/adapter.example.yaml) 建立本机配置。该样例是本项目实际接受的 schema；早期架构问答中的设计 YAML 不是运行配置。

- `sources.m3.url`、`sources.gateway.url` 指向各自 `/v1` 根地址。
- `openwebui.url` 指向 OpenWebUI 服务根地址，不带 `/api/v1/models`。
- `connection_index` 是 OpenWebUI 中目标 OpenAI 连接的索引。
- `expected_upstream_url` 必须等于该连接保存的 URL；从 Docker 内部访问的代理地址可能与 `sources.gateway.url` 不同。样例地址不代表当前生产配置。
- `source_id`、`workspace_prefix`、目标服务和连接归属绑定受管条目，变更这些值需要显式迁移，不能自动收养旧记录。

凭据仅通过指定的环境变量提供，代码不会加载 `.env`、读取 SSH 密钥、从数据库提取凭据或伪造登录令牌。OpenWebUI 凭据必须属于用于发布的管理员；已存在记录还必须由同一账号拥有。服务需要访问身份、连接配置和模型管理 API 的权限。

代码中的 API 形状以 N2830 已安装源码审计为依据：

- `GET /api/v1/auths/`：管理员身份。
- `GET /api/config`：认证后的实例功能与不含密钥的语音配置摘要。
- `GET /api/v1/tools/`：当前发布身份可访问的本地工具、OpenAPI 与 MCP 工具目录。
- `GET /openai/config`、`GET /openai/models/{index}`：连接和模型归属。
- `GET /api/v1/models/base`：保存的基础覆盖。
- `GET /api/v1/models/list?page=...`：工作空间预设（分页）。
- `GET /api/v1/models/model?id=...`：完整可写记录。
- `POST /api/v1/models/create`、`POST /api/v1/models/model/update`：发布。
- `GET /api/models?refresh=true`：刷新并校验可见目录。

连接索引和 URL 必须匹配；会检查所有启用的 OpenAI 连接，遇到跨连接同名或预设 ID 与基础模型冲突时停止。上游错误、空目录、未知能力状态、分页不一致都不会直接触发清理。确需清空时，须显式设置 `allow_empty_catalogue: true` 并审阅计划。

## 非交互式命令

以下命令仅为使用说明。本项目开发期间未对生产 OpenWebUI 执行 apply。

```bash
dai-openwebui --config adapter.yaml plan --output review.plan.json
dai-openwebui --config adapter.yaml apply --plan-file review.plan.json
dai-openwebui --config adapter.yaml verify
```

也可使用 `python -m dai_openwebui_adapter`。`plan` 只读远端，只有显式 `apply` 才调用配置写接口。输出计划文件不能覆盖已有文件；计划中没有真实凭据或既有提示词，保存权限为 0600。

`apply` 会重新读取全部来源、实例功能及可调用工具目录，并与已审阅计划比较，再在每次写入前检查目标哈希。目录、配置或将被写入的记录发生变化时，需重新生成计划。一个计划不能通过修改其中的布尔值绕过实际策略。重复发布不会生成重复条目。

`verify` 检查受管配置、映射及当前凭据可见的模型列表，**不发送模型推理请求，不代替浏览器验收**。返回值明确包含 `inference_tested=false`。

### 能力交集与实际字段

| 模型/链路能力 | OpenWebUI 接入位置 | 默认行为 |
| --- | --- | --- |
| `vision` | `meta.capabilities.vision` | 模型与网关都 verified 时开启 |
| `tool_calling` | `builtin_tools`、`builtinTools`、`toolIds`、Native function calling | 开启当前实例可用的内置类别与可调用工具 |
| `parallel_tool_calls` | `params.parallel_tool_calls` | 还要求已启用原生工具调用 |
| `streaming` | `params.stream_response` | 根据逐模型声明开启/关闭 |
| `reasoning_content` | `params.reasoning_tags` | 启用思考内容的标准渲染；不强制模型生成思考 |
| `usage` | `meta.capabilities.usage` | 允许界面请求/显示供应方用量 |
| `json_mode`、`json_schema`、`reasoning_effort` | `meta.dai.supported_request_parameters` | 列出高级请求参数；不把所有普通聊天强制成 JSON |
| 文本文件、知识、引用、状态 | file upload/preload、citations/status updates | 聊天可用即保留应用能力；原生文件工具与全文预加载互斥 |
| 记忆、终端 | memory、terminal | 受实例开关限制；终端还要求原生工具调用 |

内置类别按安装镜像的实际源码覆盖：time、user_input、memory、chats、notes、knowledge、files、channels、notifications、web_search、image_generation、code_interpreter、tasks、automations、calendar、subagents、skills、tools。Web 搜索、绘图和代码解释器还发布 `defaultFeatureIds`，让支持原生工具的模型默认接受这些功能。附件、持久聊天、权限、用户授权和工具健康状态仍由 OpenWebUI 在调用时检查；类别允许不等于每一轮都会注入全部函数。应用绘图工具也不等于该 LLM 本身拥有原生图像输出能力。

工具发现只保留 ID，过滤停用及 `authenticated=false` 的条目，不调用工具、不读取 valves、不请求 MCP specs。`server:{id}` 与 `server:mcp:{id}` 原样使用。自动管理的 ID 单独记录，能力下降或目录移除后可在审阅计划中清退；人工绑定仍保留，若与新交集冲突则停止并要求明确处理。新条目默认仅发布账号拥有，不扩大受众；最终用户仍须具备各工具自己的访问权限。

已有记录保留名称、系统提示词、知识绑定、ACL、非受管参数与元数据；受管字段包括模型映射、`meta.dai`、上表对应的能力/参数、`builtinTools`、工具绑定、三个功能默认项及启用状态。未知 defaultFeatureIds 保留。能力协商同时写入 `plan.negotiation`，每项含 model/openwebui/candidate/gateway/enabled/missing/reasons。

来源移除或不再满足聊天条件时，计划会禁用受管预设和基础覆盖，先禁用预设，不删除用户聊天。没有正确标记、其他账号拥有或跨连接同名的条目不会自动收养。

### 语音、slim 与服务模型

以本机 **v0.13.3-slim 的源码审计**为契约，不直接套用持续更新的官网 UI。slim 缺少本地 torch/transformers/faster-whisper 等包，不代表外部语音服务、浏览器语音、工具和多模态转发全部不可用。

- 标准语音是实例级 STT → 文本聊天 → TTS；`application_audio.standard_voice` 记录配置可用性。外部服务和浏览器后端仍可用；slim 不把本地 Whisper/Transformers 后端判为可用。配置可用不等于健康检查通过，结果明确 `health_verified=false`。
- 实例 Realtime 服务独立于当前聊天模型，记录为 `application_audio.realtime_voice`。不把它算成当前模型原生音频已接通。
- 本机 `ModelVoice` 只有 `voice` 音色字段，没有逐模型原生音频/Realtime 的 provider/model/route 绑定；因此 `audio_input/audio_output/realtime_audio` 的声明会保留并输出绑定 TODO，不伪造 `capabilities.audio=true`。
- ASR、TTS、绘图、编辑和 embedding 模型是实例服务角色，不能仅创建一个聊天预设就算接入。协商已预留角色/接口描述和显式 TODO，本版不修改全局 STT/TTS/Realtime/绘图/embedding 配置，也不擅自决定多个候选服务谁覆盖谁。

### 下层尚缺的声明与接口

当前两个生产模型的 M3 只声明 chat、streaming、multi_turn、reasoning_content、tool_calling、json_schema 六类状态，尚无完整视觉/音频等声明；Agent Gateway 的 `/v1/models` 只给标准 ID 列表，未输出有效能力。本版不修改这些生产组件。

1. **Worker/M2 → M3**：逐逻辑模型补齐能力三态、输入/输出模态、上下文与多模态限制、协议和验证证据；尤其区分原生视觉/音频和应用外挂服务。M3 应完整暴露合并后的有效声明，不能只从模型名称猜测。
2. **M3 与服务网关接口**：为已验证的 ASR/TTS/Realtime/绘图/embedding 暴露实际协议、受控相对路由、角色与模型选择语义、流式/非流式和错误约定。适配器预留 `interfaces`，仅作描述，不把声明中的 URL 当成任意 HTTP 目标。
3. **Agent Gateway/M4**：输出经过自身约束后的 `capabilities`，并说明图文消息、tools/tool_choice/tool results、parallel calls、response_format、reasoning、usage、音频等哪些可穿过该链路。M3 verified 不代表 M4 已支持。旧 ID-only 列表仅兼容既有聊天/流式/多轮基线；其他候选会列 `gateway.capabilities.*` TODO，不要求再修改适配器的白名单开关。
4. **服务角色绑定**：原生音视频还需要当前 OpenWebUI 逐模型绑定契约；实例服务需要显式的选择/冲突与回滚契约。缺失项逐模型输出，不冒充普通聊天发布成功。

[contracts/model-declaration.schema.json](contracts/model-declaration.schema.json) 给出能力字段与描述接口的**下层提案**，不是生产已经实现的 schema，也不是本组件强制完整字段的输入门槛。当前仍兼容稀疏三态能力，缺字段输出 TODO。测试中的完整能力数据是合成数据，不代表两个生产模型具备这些能力。

### 从 0.1.0 升级

将配置的 `publication.profile: chat` 改成 `auto`，重新生成并审阅计划，旧计划不可复用。`chat.v1` 受管记录可原位升级为 `capabilities.v2`，不改变模型 ID、归属或命名空间；旧格式日志仍可回滚。策略升级可能改变能力开关、参数、自动工具和默认功能，必须通过计划审阅后 apply。

## 故障恢复与回滚

每次 apply 在 `state_dir/journals` 保存私有日志，记录每次写入前的完整可写表单。日志可能包含既有系统提示词和知识元数据，权限为 0600，不能提交到 Git。进程崩溃、写请求超时但服务器可能已提交等情况，都会留下可检查的恢复状态。

```bash
dai-openwebui --config adapter.yaml rollback --journal /absolute/state/journals/operation.json
```

回滚恢复更新前的可写配置；新建条目会被禁用并保留，不执行删除。支持重试。发现回滚后有人工修改、连接归属变化或记录漂移时会停止；不覆盖这些变化。未完成的操作须处理/回滚后才能继续 apply。

本地锁阻止同一状态目录内并发操作。OpenWebUI API 没有在此使用服务端比较交换机制，因此不能声称跨管理员的数据库事务隔离：代码采用写前检查、写后回读和恢复日志。写入期间应避免管理员同时编辑同一受管记录。

## 验证范围和部署边界

测试使用带状态的模拟 HTTP API，覆盖完整/稀疏/拒绝声明、逐模型最大交集、实例功能关闭、工具与 OAuth 状态、能力降级、语音边界、非流式、旧策略迁移，以及发布、漂移、秘密脱敏、部分写入与回滚。它们不是生产联调结果。

上线前还需要：确认实际管理 API/凭据与连接索引，先 plan 审阅，再应用到受控范围，在 OpenWebUI 普通聊天界面验证两个模型的流式与非流式回答，最后才启用计划任务。OpenWebUI 内部 note 聊天可能强制注入工具，不在本版普通聊天支持范围内。代码不会通过静默丢弃请求工具或放宽共享网关能力校验来兼容该模式。

此仓库不安装/启动服务、不写生产数据库、不配置反向代理、不改变共享网关或 OpenWebUI 的上游连接地址。部署与真实模型验收是后续独立步骤。

## 官方依据与可复核契约

- [Workspace Models](https://docs.openwebui.com/features/workspace/models/)：模型元数据与界面能力。
- [Tools](https://docs.openwebui.com/features/extensibility/plugin/tools/)：Native 内置工具与扩展工具。
- [slim 与性能说明](https://docs.openwebui.com/troubleshooting/performance/)：精简镜像的本地运行库与外部服务。
- [Voice Mode](https://docs.openwebui.com/features/chat-conversations/chat-features/voice-mode/)：标准语音与 Realtime 的区别。

[tests/fixtures/openwebui_slim_contract.json](tests/fixtures/openwebui_slim_contract.json) 记录 2026-11-17 本机只读源码审计的字段、路由和文件哈希。它是本版契约快照，不含数据库内容、凭据或聊天；未来镜像升级需重查字段，不能单凭 image tag 认定兼容。
