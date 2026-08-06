# Codex 执行工作流：算法侧在线 Client（实时闭环）

> 用途：将本文件放到项目根目录，交给 Codex 直接执行。  
> 权威协议：`api.md`。背景说明：`开题报告.md`。  
> 实现范围：算法侧在线推理 Client，不实现 Webots 仿真、SFT 训练、数据侧专家采集和完整评测框架。

---

## 0. 直接给 Codex 的总指令

你现在是本仓库的实现代理。请先完整阅读：

1. `api.md`，尤其是 §0、§3–§6、§8.6–§9、§10.3、§10.5、§11；
2. `开题报告.md` 中算法侧在线推理、评测和 RL 后续扩展相关内容；
3. 本文件。

随后直接检查仓库、修改代码、运行测试并完成算法侧在线 Client。不要只输出方案，不要停留在伪代码，不要在每个阶段等待人工确认。

执行原则：

- `api.md` 是 WebSocket 协议的唯一权威来源。
- 不修改仿真侧协议字段，不自行增加 server 必填字段。
- 生产路径必须使用 Hermes Agent；允许提供仅用于测试和故障隔离的 Fake/OpenAI-compatible 后端。
- 没有 Webots、GPU、LoRA 权重或 vLLM 时，也必须通过 mock server 和 fake policy 完成离线验收。
- 每完成一个 Phase 就运行对应测试；失败则先修复，再继续。
- 不提交 Git commit，不推送远程，不修改与本任务无关的代码。
- 最终给出：新增/修改文件、运行命令、测试结果、尚需真实环境验证的项目。

---

# 1. 目标和边界

实现以下实时闭环：

```text
Webots WebSocket server
        │
        │ obs
        ▼
serialize_state(obs)
        │
        │ 状态文本 + episode 内历史
        ▼
Hermes Agent
        │
        │ OpenAI-compatible API
        ▼
vLLM: Qwen2.5-7B-Instruct + LoRA
        │
        │ 最终文本
        ▼
parse_action()
        │
        │ {"type":"action", ...}
        ▼
Webots WebSocket server
```

必须同时支持：

1. **独立 CLI 模式**

```bash
python -m agent.run_agent \
  --sim-url ws://127.0.0.1:8765 \
  --api-base http://127.0.0.1:8000/v1 \
  --model drone-nav-lora \
  --seeds 0-99 \
  --output results/agent_episodes.jsonl
```

2. **Python 调用模式**

```python
from agent.run_agent import run_episode

result = run_episode(
    sim_url="ws://127.0.0.1:8765",
    seed=100,
    agent_config={
        "model": "drone-nav-lora",
        "api_base": "http://127.0.0.1:8000/v1",
        "api_key": "EMPTY",
        "backend": "hermes",
    },
)
```

返回至少包含：

```python
{
    "seed": int,
    "outcome": "success" | "collision" | "timeout",
    "steps": int,
    "final_dist": float,
    "path_length": float,
    "min_obstacle_dist": float | None,
}
```

3. **RL 在线收集扩展点**

本阶段不实现 PPO/GRPO，但必须保留 transition hook，使后续训练侧能够获得：

```python
{
    "seed": int,
    "episode_id": int,
    "step_id": int,
    "obs": dict,
    "state_text": str,
    "raw_model_output": str,
    "action_id": int,
    "next_obs": dict,
    "latency_ms": float,
    "parse_status": str,
}
```

默认不保存原始轨迹；仅在传入 `--trace-output` 时写 JSONL。

---

# 2. 必须先修正的四个文档级伪代码问题

这些修正只影响算法侧实现，不改变 WebSocket 协议。

## 2.1 Hermes 导入方式

不要实现：

```python
from hermes import Agent
```

生产适配层应使用 Hermes 当前源码库中的：

```python
from run_agent import AIAgent
```

由于 Hermes 作为 Python 库通常从源码 checkout 运行，代码必须通过环境变量定位：

```text
HERMES_REPO=/absolute/path/to/hermes-agent
```

`hermes_adapter.py` 应：

1. 优先尝试正常导入 `from run_agent import AIAgent`；
2. 若失败，读取 `HERMES_REPO`，将该目录加入 `sys.path` 后再次导入；
3. 仍失败时抛出清晰错误，给出 `scripts/setup_hermes.sh` 的运行方式；
4. 不在 import 时自动联网或自动 clone。

Hermes 实例建议配置：

```python
AIAgent(
    model=model_name,
    base_url=api_base,
    api_key=api_key or "EMPTY",
    quiet_mode=True,
    skip_context_files=True,
    skip_memory=True,
    enabled_toolsets=[],
    max_iterations=1,
    ephemeral_system_prompt=SYSTEM_PROMPT,
)
```

若当前锁定的 Hermes 版本构造参数不同，Codex 应检查其 `run_agent.py` 的 `AIAgent` 签名，在 `hermes_adapter.py` 内做兼容，不得把版本差异泄漏到其他模块。

## 2.2 LoRA 不传给 Hermes 构造器

不要实现：

```python
AIAgent(..., lora_path="./outputs/lora_weights")
```

LoRA 由 vLLM 启动时加载，并注册为一个可请求的模型名：

```bash
vllm serve Qwen/Qwen2.5-7B-Instruct \
  --host 127.0.0.1 \
  --port 8000 \
  --enable-lora \
  --lora-modules drone-nav-lora=./outputs/lora_weights \
  --generation-config vllm
```

Hermes 只请求：

```text
model = drone-nav-lora
base_url = http://127.0.0.1:8000/v1
```

CLI 可保留 `--lora-path` 兼容参数，但它只能用于：

- 生成/打印 vLLM 启动提示；
- 校验路径是否存在；
- 不得传给 Hermes API 调用。

更推荐把生产 CLI 改为 `--model drone-nav-lora`，并将 `--model-path` 标记为旧参数别名。

## 2.3 Hermes 返回值不能假定为固定字典

不要假定：

```python
response = {"reasoning_content": "...", "content": "9"}
```

适配层对外统一返回：

```python
@dataclass
class PolicyDecision:
    raw_text: str
    reasoning_content: str | None
    messages: list[dict]
    latency_ms: float
```

Hermes 调用应使用多轮历史：

```python
result = agent.run_conversation(
    user_message=state_text,
    conversation_history=history,
)
history = result["messages"]
raw_text = result["final_response"]
```

仅当 Hermes 返回消息中实际存在 `reasoning_content` 时才提取；在线控制不能依赖该字段存在。

## 2.4 `serialize_state()` 的单位和空值错误

`api.md` 全局约定规定角度为弧度，因此不能把 `yaw` 原值标成“度”。

二选一，并在训练/采集/推理三侧保持完全一致：

- 推荐：输出 `yaw` 的弧度值并标记 `rad`；
- 或显式 `math.degrees(yaw)` 后标记“度”。

本任务默认采用第一种：

```text
- 无人机朝向(yaw)：0.785 rad
```

同时：

```python
if obs["height_agl"]:
```

必须改为：

```python
if obs["height_agl"] is not None:
```

因为 `0.0` 是合法值。

---

# 3. 推荐目录结构

Codex 应优先适配已有目录；若仓库尚无实现，创建：

```text
pyproject.toml
.env.example
README-agent.md

src/
└── agent/
    ├── __init__.py
    ├── config.py
    ├── schemas.py
    ├── protocol.py
    ├── serialize.py
    ├── action_parser.py
    ├── policy.py
    ├── hermes_adapter.py
    ├── metrics.py
    └── run_agent.py

tests/
├── __init__.py
├── fixtures/
│   ├── hello.json
│   ├── obs_initial.json
│   ├── obs_middle.json
│   └── obs_done.json
├── mock_sim_server.py
├── test_protocol.py
├── test_serialize.py
├── test_action_parser.py
├── test_metrics.py
└── test_run_agent_integration.py

scripts/
├── setup_hermes.sh
├── serve_vllm.sh
├── smoke_mock.sh
└── smoke_live.sh

results/
└── .gitkeep
```

依赖控制在最小范围：

```toml
dependencies = [
  "websockets>=15,<17",
]

dev = [
  "pytest>=8",
  "pytest-asyncio>=0.25",
]
```

如果已有项目使用其他 WebSocket 库，优先复用，不要为风格统一而大规模改造。

---

# 4. 核心架构

## 4.1 数据结构

在 `schemas.py` 中定义轻量 dataclass 或 TypedDict：

```python
@dataclass(frozen=True)
class AgentConfig:
    model: str
    api_base: str
    api_key: str = "EMPTY"
    backend: str = "hermes"
    request_timeout_s: float = 180.0
    max_action_retries: int = 1
    invalid_action_fallback: int = 0

@dataclass
class EpisodeResult:
    seed: int
    outcome: str
    steps: int
    final_dist: float
    path_length: float
    min_obstacle_dist: float | None

@dataclass
class PolicyDecision:
    raw_text: str
    reasoning_content: str | None
    messages: list[dict]
    latency_ms: float
```

不要把完整 obs 强制建模成庞大的 Pydantic 模型；对协议必需字段做集中校验即可。

## 4.2 分层职责

### `protocol.py`

只负责 WebSocket 协议：

- 建立连接；
- 接收/发送 JSON；
- `hello` 兼容性校验；
- reset/action/all_finish 消息构造；
- obs 基础字段校验；
- server `error` 消息转换为异常；
- 不做 LLM 推理；
- 不计算指标。

### `serialize.py`

只负责：

```python
def serialize_state(obs: dict) -> str
```

要求：

- 输出确定性文本；
- 不修改传入 obs；
- 单位与 `api.md` 一致；
- 不序列化完整 16×180 LiDAR 原始数组；
- 使用 `obstacles`、`ultrasonic`、自身状态和 goal；
- 对缺失可选字段、空 obstacles、`height_agl=None` 有明确行为；
- 训练、数据采集和在线推理必须 import 同一个函数。

### `policy.py`

定义稳定接口：

```python
class PolicyBackend(Protocol):
    def reset_episode(self) -> None: ...
    def decide(self, state_text: str) -> PolicyDecision: ...
```

提供：

- `FakePolicyBackend`：测试用，按固定动作或动作序列返回；
- 可选 `OpenAICompatiblePolicyBackend`：仅用于隔离 Hermes 故障；
- 生产默认 `HermesPolicyBackend`。

### `action_parser.py`

统一解析模型输出：

```python
def parse_action_id(text: str) -> int
```

允许：

- `"9"`
- `" 9\n"`
- 可选兼容 `{"action_id": 9}`

不允许：

- `"-1"`
- `"10"`
- `"动作是 9，因为……"` 直接静默通过
- 多个候选数字

策略：

1. 首次严格解析；
2. 失败时向同一 policy 发一次纠错提示：
   `你的上一条输出不合法。只输出 0 到 9 的一个整数。`
3. 再失败则使用 `invalid_action_fallback=0`（悬停）；
4. 记录 `parse_status = exact | json | retry | fallback`；
5. 不因单次格式错误终止 episode。

### `metrics.py`

计算：

- `path_length`：连续 obs 的 `agent.pos` 三维欧氏距离之和；
- `min_obstacle_dist`：所有帧 `obstacles[*].distance` 的最小值；
- `final_dist`：终止 obs 的 `goal.dist`；
- `outcome`：按 `collision > goal_reached > timeout`；
- `steps`：终止 obs 的 `step_id`。

禁止向 JSON 写 `inf`/`nan`。整个 episode 没有可见障碍物时，`min_obstacle_dist` 写 `null`。

### `hermes_adapter.py`

只负责 Hermes 版本兼容和多轮历史：

- 每个 episode 开始清空 history；
- episode 内累积；
- 跨 episode 不累积；
- 默认关闭 Hermes memory、context files、tools；
- `max_iterations=1`；
- 将 Hermes 的返回值归一化为 `PolicyDecision`；
- 不解析动作。

### `run_agent.py`

负责组合以上模块：

- 连接；
- hello；
- reset；
- obs → serialize → policy → parse → action；
- 指标；
- JSONL；
- CLI；
- `run_episode()` 对外接口；
- `OnlineAgentSession` 长连接。

---

# 5. 长连接设计：解决 `run_episode()` 与 server 状态机冲突

`api.md` 的 server 设计是一条连接连续跑多个 episode，最后再 `all_finish`。因此不能让 CLI 每个 seed 都重新连接。

必须实现：

```python
class OnlineAgentSession:
    def __init__(self, sim_url: str, agent_config: AgentConfig): ...
    def connect(self) -> None: ...
    def run_episode(self, seed: int, transition_hook=None) -> EpisodeResult: ...
    def finish(self, reason: str, total_episodes: int) -> None: ...
    def close(self) -> None: ...
```

CLI 使用：

```python
with OnlineAgentSession(sim_url, config) as session:
    for seed in seeds:
        result = session.run_episode(seed)
    session.finish("converged", len(results))
```

兼容函数：

```python
def run_episode(sim_url: str, seed: int, agent_config: dict) -> dict:
    """
    单 episode 兼容入口。
    若 agent_config 中传入 _session，则复用长连接；
    否则创建一次性 session，跑完后发送 all_finish 并关闭。
    """
```

建议评测侧的高效调用方式：

```python
config = {...}

with OnlineAgentSession(sim_url, AgentConfig.from_dict(config)) as session:
    config["_session"] = session
    for seed in seeds:
        result = run_episode(sim_url, seed, config)
    session.finish("converged", len(seeds))
```

`_session` 是进程内对象，不写入 JSON，不出现在 CLI 配置文件中。

若评测侧尚未实现，`README-agent.md` 必须说明这一点，避免每个 seed 都导致仿真 server 退出。

---

# 6. WebSocket 协议实现要求

必须严格遵守：

- 连接后第一条消息必须是 `hello`；
- 协议主版本必须为 `1`；
- 一条 WebSocket 消息对应一个 UTF-8 JSON 对象；
- `json.dumps(..., allow_nan=False)`；
- 不实现应用层 ping/pong；
- 库级 `ping_timeout >= 60s`；
- v1 不自动断线重连；
- `episode_id`、`step_id` 必须原样回填当前 obs；
- terminal obs 收到后不能再发 action；
- 仅在 server 处于 `WAIT_RESET` 时发送 `all_finish`；
- server 返回 `error` 时立即抛出带 code/detail 的异常。

建议同步连接：

```python
from websockets.sync.client import connect
```

配置至少包括：

```python
connect(
    sim_url,
    open_timeout=15,
    ping_interval=20,
    ping_timeout=60,
    close_timeout=10,
    max_size=None,
)
```

若当前项目依赖版本的参数不同，Codex 应按锁定版本适配并用测试验证。

---

# 7. 系统提示词

在 `config.py` 中集中定义，不要散落：

```text
你是一架无人机的自动驾驶 Agent。
你将收到当前 3D 状态以及本 episode 的历史状态和动作。
目标是在避免碰撞的前提下尽快到达目标点。

动作空间：
0 悬停
1 前进
2 后退
3 左移
4 右移
5 上升
6 下降
7 左转
8 右转
9 前进并上升

只输出 0 到 9 的一个整数，不要输出解释、标点、代码块或其他文本。
```

注意：

- `api.md` 的离散动作表中，动作 7/8 的文字名称与 `yawrate` 正负描述存在潜在符号歧义。算法侧只发送 `action_id`，不要自行重定义速度值；最终由 server 的动作表执行。
- 后续如果修订动作表，先修改 `api.md` 并升级协议版本。

---

# 8. `serialize_state()` 的基线格式

输出至少包含：

```text
当前状态：
- episode_id：1
- step_id：17
- 无人机位置：(2.50, 3.10, 1.20) m
- 无人机速度：(0.30, 0.00, 0.00) m/s
- 无人机朝向(yaw)：0.785 rad
- 距地高度：1.20 m
- 目标位置：(8.00, 6.00, 1.50) m
- 目标距离：6.02 m
- 目标水平方位：0.420 rad
- 目标垂直方位：0.050 rad

障碍物：
- id=0 type=cylinder pos=(4.0,3.0,0.0) radius=0.3 height=2.0 distance=1.51 m
...

超声波[N,NE,E,SE,S,SW,W,NW]：
1.20, 3.50, 2.10, 5.00, 4.80, 5.00, 3.20, 1.80 m

请选择下一步动作，只输出动作编号。
```

规则：

- 数字精度固定，保证数据采集与推理分布稳定；
- obstacles 按 `distance` 升序；
- 可限制只输出最近的 `N` 个障碍物，但默认不截断；
- 不做未在协议中定义的坐标变换；
- 不根据绝对位置“猜测”障碍是否在前后左右；
- 如需增加派生特征，必须同时更新数据侧训练输入。

---

# 9. `run_episode()` 伪代码

Codex 应据此写成完整可运行代码：

```python
def run_episode_on_session(session, seed, transition_hook=None):
    session.policy.reset_episode()

    session.send(reset_msg(seed))
    obs = session.recv_obs(expected_step=0)

    metrics = EpisodeMetrics.from_initial_obs(obs)

    while not obs["done"]:
        state_text = serialize_state(obs)

        decision = session.policy.decide(state_text)

        try:
            action_id, parse_status = parse_action_with_status(decision.raw_text)
        except ActionParseError:
            corrected = session.policy.decide(
                "你的上一条输出不合法。只输出 0 到 9 的一个整数。"
            )
            try:
                action_id, _ = parse_action_with_status(corrected.raw_text)
                parse_status = "retry"
                decision = corrected
            except ActionParseError:
                action_id = session.config.invalid_action_fallback
                parse_status = "fallback"

        action = {
            "type": "action",
            "episode_id": obs["episode_id"],
            "step_id": obs["step_id"],
            "action_id": action_id,
        }

        session.send(action)
        next_obs = session.recv_obs()

        metrics.update(obs, next_obs)

        if transition_hook is not None:
            transition_hook({...})

        obs = next_obs

    return metrics.finalize(seed, obs)
```

额外要求：

- LLM 调用异常：最多按配置重试一次；仍失败时悬停或终止，行为写入日志；
- WebSocket 协议错误：立即终止，不伪造结果；
- terminal flags 不全但 `done=true`：抛协议异常；
- `KeyboardInterrupt` 发生在 episode 中途：直接关闭连接，不发送状态机不允许的 `all_finish`；
- 日志不得输出完整 API key；
- 默认日志输出到 stderr，JSONL 只写结构化结果。

---

# 10. CLI 设计

至少支持：

```text
--sim-url
--api-base
--api-key
--model
--backend hermes|fake|openai
--seeds A-B
--output
--trace-output
--request-timeout
--invalid-action-fallback
--log-level
```

兼容旧参数：

```text
--model-path  -> --model 的别名
--lora-path   -> 仅用于路径校验/提示，不参与请求
```

seed 解析：

```python
parse_seeds("0-3") == [0, 1, 2, 3]
parse_seeds("5") == [5]
```

非法范围、负数、反向范围必须给出明确错误。

JSONL 写入：

- 每完成一个 episode 立即 flush；
- 目标目录不存在时自动创建；
- 使用 UTF-8；
- `allow_nan=False`；
- 已存在文件默认追加，增加 `--overwrite` 可覆盖；
- 每行严格一个 JSON 对象。

---

# 11. Mock server

必须实现 `tests/mock_sim_server.py`，模拟：

```text
connect
  -> hello
reset(seed)
  -> obs(step=0, done=false)
action(step=0)
  -> obs(step=1, done=false)
action(step=1)
  -> obs(step=2, done=true, goal_reached=true)
reset(next seed)
  -> ...
all_finish
  -> bye
```

它必须校验：

- action 的 episode_id/step_id；
- action_id 在 0–9；
- 收到非法消息时返回 `error`；
- 支持至少连续两个 episode；
- 不依赖 Webots、GPU、Hermes、vLLM。

Fake policy 使用固定序列，例如 `[1, 9]`。

---

# 12. 测试清单

## 12.1 协议测试

- hello 主版本 1 通过；
- 主版本 2 拒绝；
- 缺字段拒绝；
- server error 转异常；
- JSON 中 `nan/inf` 无法发送；
- episode_id/step_id 正确回填；
- terminal obs 后不再发送 action；
- 两个 episode 共用一条连接；
- 最后发送 all_finish 并收到 bye。

## 12.2 序列化测试

- 输出确定；
- yaw 单位为 rad；
- `height_agl=0.0` 不被丢弃；
- `height_agl=None` 明确显示 unavailable 或省略；
- obstacles 空列表不崩溃；
- obstacles 按距离排序；
- 不包含完整 `lidar_3d`；
- 输入 obs 未被修改。

## 12.3 动作解析测试

- `"0"` 到 `"9"` 全部通过；
- 空白字符通过；
- JSON action_id 兼容；
- `10`、`-1`、空字符串、多个数字、自然语言解释拒绝；
- retry 成功；
- retry 失败后 fallback=0。

## 12.4 指标测试

给定位置：

```text
(0,0,0) -> (3,0,0) -> (3,4,0)
```

`path_length == 7.0`。

同时测试：

- success/collision/timeout；
- min_obstacle_dist；
- 无障碍时为 None；
- final_dist；
- steps 使用终止 step_id。

## 12.5 集成测试

运行 mock server + fake policy：

```bash
pytest -q
python -m agent.run_agent \
  --backend fake \
  --sim-url ws://127.0.0.1:18765 \
  --seeds 0-1 \
  --output /tmp/agent_episodes.jsonl \
  --overwrite
```

验收：

- 退出码 0；
- 两行结果；
- 两个 episode 均成功；
- server 收到一次 all_finish；
- 没有外部网络和 GPU 依赖。

---

# 13. vLLM 与 Hermes 运行脚本

## 13.1 `scripts/serve_vllm.sh`

要求：

- 使用环境变量覆盖默认值；
- 检查 LoRA 路径；
- 打印实际 model alias；
- 不硬编码 API key；
- 默认只监听 `127.0.0.1`。

示例：

```bash
#!/usr/bin/env bash
set -euo pipefail

BASE_MODEL="${BASE_MODEL:-Qwen/Qwen2.5-7B-Instruct}"
LORA_PATH="${LORA_PATH:-./outputs/lora_weights}"
MODEL_ALIAS="${MODEL_ALIAS:-drone-nav-lora}"
PORT="${VLLM_PORT:-8000}"

vllm serve "$BASE_MODEL" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --enable-lora \
  --lora-modules "${MODEL_ALIAS}=${LORA_PATH}" \
  --generation-config vllm
```

启动后检查：

```bash
curl http://127.0.0.1:8000/v1/models
```

必须能看到 `drone-nav-lora`。

不要在本任务里擅自设定：

- tensor parallel 数量；
- quantization 类型；
- max model len；
- max LoRA rank。

这些值取决于实际 GPU、LoRA rank 和上下文需求，应由部署时明确配置。

## 13.2 `scripts/setup_hermes.sh`

建议：

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_REPO="${HERMES_REPO:-$ROOT/.vendor/hermes-agent}"

if [[ ! -d "$HERMES_REPO/.git" ]]; then
  git clone https://github.com/NousResearch/hermes-agent.git "$HERMES_REPO"
fi

cd "$HERMES_REPO"
uv sync

echo "Hermes commit: $(git rev-parse HEAD)"
echo "export HERMES_REPO=$HERMES_REPO"
```

`.vendor/` 必须加入 `.gitignore`。  
在 `README-agent.md` 记录验证过的 Hermes commit SHA，避免未来 main 分支更新导致接口漂移。

## 13.3 运行算法侧

在 Hermes 环境中安装本项目：

```bash
"$HERMES_REPO/.venv/bin/python" -m pip install -e .
```

或由脚本自动处理，然后：

```bash
export HERMES_REPO=/absolute/path/to/hermes-agent

python -m agent.run_agent \
  --backend hermes \
  --sim-url ws://127.0.0.1:8765 \
  --api-base http://127.0.0.1:8000/v1 \
  --api-key EMPTY \
  --model drone-nav-lora \
  --seeds 100-119 \
  --output results/agent_episodes.jsonl
```

---

# 14. 分阶段执行

## Phase 0：仓库审计

只检查，不改代码：

- 当前目录树；
- Python 包布局；
- 现有 `protocol.py`、`serialize.py`、`run_agent.py`；
- pyproject/requirements；
- 测试框架；
- 是否已有 Hermes/vLLM 脚本；
- 与 Group A/D 的接口是否已存在。

输出简短审计结论，然后直接进入 Phase 1。

## Phase 1：协议和数据结构

实现：

- `schemas.py`
- `protocol.py`
- fixture
- `test_protocol.py`

门禁：

```bash
pytest -q tests/test_protocol.py
```

## Phase 2：序列化和动作解析

实现：

- `serialize.py`
- `action_parser.py`
- 对应测试

门禁：

```bash
pytest -q tests/test_serialize.py tests/test_action_parser.py
```

## Phase 3：指标

实现：

- `metrics.py`
- `test_metrics.py`

门禁：

```bash
pytest -q tests/test_metrics.py
```

## Phase 4：Policy 抽象和 Hermes 适配

实现：

- `policy.py`
- `hermes_adapter.py`
- Fake backend
- Hermes import 失败提示
- 多轮 history 单元测试（使用 monkeypatch，不调用真实模型）

门禁：

```bash
pytest -q -k "policy or hermes"
```

## Phase 5：OnlineAgentSession 和 CLI

实现：

- 长连接；
- `run_episode()`；
- seed 解析；
- JSONL；
- trace hook；
- argparse；
- 日志。

门禁：

```bash
python -m agent.run_agent --help
pytest -q
```

## Phase 6：Mock 集成

实现 mock server 和 smoke script。

门禁：

```bash
bash scripts/smoke_mock.sh
```

## Phase 7：真实 vLLM 健康检查

没有 GPU/模型时只完成脚本，不伪造成功。

有 vLLM 时检查：

```bash
curl http://127.0.0.1:8000/v1/models
```

然后发送一个最小 chat completion，确认模型只输出 0–9。

## Phase 8：真实仿真联调

按顺序：

1. 单 seed；
2. 两个连续 seed；
3. 20 个测试 seed；
4. 验证 JSONL；
5. 验证断线和 server error；
6. 验证评测侧复用 `OnlineAgentSession`。

---

# 15. 最终验收标准

Codex 只有在以下条件全部满足后才算完成：

- [ ] `pytest -q` 全部通过；
- [ ] `python -m agent.run_agent --help` 正常；
- [ ] mock server 下连续两个 episode 成功；
- [ ] 一条 WebSocket 连接跑多个 seed；
- [ ] CLI 结果格式匹配 `api.md` §10.3；
- [ ] `run_episode()` 可 import；
- [ ] Hermes 生产后端存在；
- [ ] LoRA 由 vLLM 加载，不传给 Hermes；
- [ ] episode 内 history 累积、episode 间清空；
- [ ] invalid action 有 retry + hover fallback；
- [ ] `yaw` 单位正确；
- [ ] `height_agl=0.0` 正确；
- [ ] JSON 无 nan/inf；
- [ ] 支持可选 transition trace；
- [ ] README 写清楚 mock、vLLM、Hermes、Webots 四种启动顺序；
- [ ] 未修改仿真侧协议；
- [ ] 未声称未实际运行的真实 GPU/Webots 测试成功。

---

# 16. Codex 最终回复格式

完成后只需按以下结构汇报：

```text
实现结果
- 已完成：
- 未完成/受外部环境阻塞：

主要文件
- path: 用途

验证
- command
  result

运行方式
1. 启动 vLLM
2. 配置 Hermes
3. 启动 Webots server
4. 启动 agent client

已知风险
- ...

协议偏差
- 无
```

若发现 `api.md` 内部矛盾，不要默默选择。应：

1. 保持 WebSocket wire protocol 不变；
2. 在 `README-agent.md` 的“协议问题”中记录；
3. 用兼容层解决；
4. 最终报告明确说明。
