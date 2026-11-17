# DAI OpenWebUI Adapter

独立的 **配置发布组件**：读取 M3 模型能力与 Agent Career Hub Gateway 的可用模型清单，通过 OpenWebUI 管理 API 发布普通聊天策略。它不代理推理，不操作 SSH、GPU、模型进程或 systemd，也不更改两个共享网关。

```text
推理：OpenWebUI -> Agent Gateway -> M3 -> Worker
配置：M3 能力 + Agent Gateway 清单 -> 本组件 -> OpenWebUI 模型管理 API
```

当前版本 0.1.0 仅面向普通文本聊天，要求 `chat`、`streaming` 均为 `verified`。它发布：

- 同 ID 基础模型覆盖，`base_model_id=null`，照顾直接使用基础模型的已有聊天。
- 独立工作空间预设，ID 为 `dai.chat.{logical_model_id}`，指向 OpenWebUI 的实际基础 ID。

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

`apply` 会重新读取全部来源并与已审阅计划比较，再在每次写入前检查目标哈希。目录、配置或将被写入的记录发生变化时，需重新生成计划。一个计划不能通过修改其中的布尔值绕过实际策略。重复发布不会生成重复条目。

`verify` 检查受管配置、映射及当前凭据可见的模型列表，**不发送模型推理请求，不代替浏览器验收**。返回值明确包含 `inference_tested=false`。

### 策略和字段边界

普通聊天预设明确设置 `builtin_tools=false`、`vision=false`，即使 M3 已声明这些附加能力为 verified，也不会在未经本项目相应链路验收时开启。原生工具、视觉和非流式专用配置不属于 0.1.0 的发布范围。文件上传、知识/RAG 能力属于应用策略，不从模型视觉能力推断。

新条目默认仅发布账号拥有，不隐式扩大受众；后续共享权限需单独审阅。现有受管记录保留名称、参数/系统提示词、知识绑定、ACL 和非受管元数据。受管字段为基础映射、`meta.dai`、上述两项能力开关以及来源决定的启用状态。检测到现有外部工具绑定会停止，不静默移除绑定。没有正确标记或由其他账号拥有的记录不会自动收养。

来源移除或模型不再满足发布条件时，审阅计划会禁用受管预设和对应基础覆盖，先禁用预设。不会删除用户聊天。当前不自动隐藏原始模型入口，以免改变可见性和基础访问权限。

## 故障恢复与回滚

每次 apply 在 `state_dir/journals` 保存私有日志，记录每次写入前的完整可写表单。日志可能包含既有系统提示词和知识元数据，权限为 0600，不能提交到 Git。进程崩溃、写请求超时但服务器可能已提交等情况，都会留下可检查的恢复状态。

```bash
dai-openwebui --config adapter.yaml rollback --journal /absolute/state/journals/operation.json
```

回滚恢复更新前的可写配置；新建条目会被禁用并保留，不执行删除。支持重试。发现回滚后有人工修改、连接归属变化或记录漂移时会停止；不覆盖这些变化。未完成的操作须处理/回滚后才能继续 apply。

本地锁阻止同一状态目录内并发操作。OpenWebUI API 没有在此使用服务端比较交换机制，因此不能声称跨管理员的数据库事务隔离：代码采用写前检查、写后回读和恢复日志。写入期间应避免管理员同时编辑同一受管记录。

## 验证范围和部署边界

测试使用带状态的模拟 HTTP API，覆盖两模型发布、未来模型发现、幂等、上下游漂移、错误归属、前缀映射、秘密脱敏、部分/不确定写入、禁用与回滚。它们不是生产联调结果。

上线前还需要：确认实际管理 API/凭据与连接索引，先 plan 审阅，再应用到受控范围，在 OpenWebUI 普通聊天界面验证两个模型的流式与非流式回答，最后才启用计划任务。OpenWebUI 内部 note 聊天可能强制注入工具，不在本版普通聊天支持范围内。代码不会通过静默丢弃请求工具或放宽共享网关能力校验来兼容该模式。

此仓库不安装/启动服务、不写生产数据库、不配置反向代理、不改变共享网关或 OpenWebUI 的上游连接地址。部署与真实模型验收是后续独立步骤。
