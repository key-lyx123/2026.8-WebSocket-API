# 算法侧在线 Client

本目录实现 `api.md` 协议 1.x 的算法侧在线推理 Client：WebSocket obs 经确定性文本序列化后交给 policy，严格解析 0–9 动作并回发仿真。生产 policy 是 Hermes Agent；Fake 和 OpenAI-compatible 后端仅用于离线测试和故障隔离。

## 当前审计结论

实现前仓库只有三份设计文档，没有 Python 包、测试、Hermes/vLLM/Webots 代码或 Git 仓库。本机验证环境为 Python 3.14.4；离线路径不要求 GPU、模型权重或外部服务。本机检测到 NVIDIA GeForce RTX 5070 Laptop GPU（8 GB），但没有安装 vLLM，也没有 LoRA 权重或已启动的模型服务。

## 安装与离线验收

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python scripts/smoke_mock.py
```

Linux、macOS、WSL 或 Git Bash 也可运行：

```bash
bash scripts/smoke_mock.sh
```

Windows 当前没有 Bash，因此以跨平台 Python smoke 为本机验收命令。mock server 会严格校验 action 的 episode/step ID、动作范围和状态机，并在一条连接上连续运行两个 episode。

## 独立 CLI

```bash
python -m agent.run_agent \
  --backend fake \
  --sim-url ws://127.0.0.1:18765 \
  --seeds 0-1 \
  --output results/agent_episodes.jsonl \
  --trace-output results/agent_trace.jsonl \
  --overwrite
```

结果文件每行包含 `seed/outcome/steps/final_dist/path_length/min_obstacle_dist`。只有指定 `--trace-output` 才写原始 transition；两个文件均为 UTF-8 JSONL，每局完成即 flush，并拒绝 NaN/Infinity。

`--model-path` 是 `--model` 的旧别名。`--lora-path` 只检查本地路径并打印部署提示，绝不会传入 Hermes；LoRA 必须由 vLLM 加载并注册为模型别名。

## 真实环境启动顺序

1. **vLLM + LoRA**

   ```bash
   LORA_PATH=./outputs/lora_weights MODEL_ALIAS=drone-nav-lora bash scripts/serve_vllm.sh
   python scripts/check_vllm.py --model drone-nav-lora
   ```

   脚本默认仅监听 `127.0.0.1:8000`，不替用户猜测 tensor parallel、量化、上下文长度或 LoRA rank。

2. **Hermes Agent**

   ```bash
   bash scripts/setup_hermes.sh
   export HERMES_REPO=/absolute/path/to/hermes-agent
   "$HERMES_REPO/.venv/bin/python" -m pip install -e .
   ```

   `setup_hermes.sh` 会在用户主动运行时 clone Hermes 并输出实际 commit。当前机器尚未安装 Hermes，因此没有可记录的已验证 commit SHA。运行时优先正常导入 `run_agent.AIAgent`，否则从 `HERMES_REPO` 导入；代码不会自动联网。

3. **Webots server**

   启动 Group A 的 Webots world/controller，使协议 server 监听 `ws://127.0.0.1:8765`。本项目不实现仿真侧。

4. **Agent client**

   ```bash
   python -m agent.run_agent \
     --backend hermes \
     --sim-url ws://127.0.0.1:8765 \
     --api-base http://127.0.0.1:8000/v1 \
     --api-key EMPTY \
     --model drone-nav-lora \
     --seeds 100-119 \
     --output results/agent_episodes.jsonl
   ```

`scripts/smoke_live.sh` 依次执行 vLLM 最小检查和单 seed 联调。只有 vLLM、Hermes、LoRA 与 Webots 都已准备好时才运行。

## Python 调用和长连接复用

单局兼容入口：

```python
from agent.run_agent import run_episode

result = run_episode("ws://127.0.0.1:8765", 100, {
    "backend": "hermes",
    "model": "drone-nav-lora",
    "api_base": "http://127.0.0.1:8000/v1",
    "api_key": "EMPTY",
})
```

批量评测必须复用一条连接，避免每个 seed 后让 server 退出：

```python
from agent.run_agent import OnlineAgentSession, run_episode
from agent.schemas import AgentConfig

config = {"backend": "hermes", "model": "drone-nav-lora", "api_base": "http://127.0.0.1:8000/v1"}
with OnlineAgentSession("ws://127.0.0.1:8765", AgentConfig.from_dict(config)) as session:
    config["_session"] = session
    results = [run_episode(session.sim_url, seed, config) for seed in range(100, 120)]
    session.finish("converged", len(results))
```

RL 后续收集可向 `OnlineAgentSession.run_episode(seed, transition_hook=...)` 传 callback。默认不保存轨迹。

## 协议问题与边界

- `api.md` 的动作 7/8 名称与 yawrate 正负说明存在符号歧义。本 Client 只发送 `action_id`，不自行重定义速度，完全交由 server 动作表执行。
- 文档旧伪代码中的 `from hermes import Agent`、把 LoRA 传入构造器、固定返回字典、yaw 标成度和 `height_agl` 真值判断均未采用。
- v1 不自动重连，不实现应用层 ping/pong；terminal obs 后不再发 action；episode 中断时直接关闭连接。
- 不包含 Webots、数据采集、SFT、PPO/GRPO 或完整评测框架。

## 真实环境验证状态

- mock server、Fake policy、协议/序列化/指标/Hermes 兼容单元测试：已在本机离线通过。
- GPU 硬件：已检测到 RTX 5070 Laptop GPU（8 GB）；尚未验证其运行目标模型的可行性或性能。
- 真实 Hermes、vLLM 推理、LoRA 权重、Webots server、20-seed 真实评测和真实断线联调：必须在相应环境就绪后验证，当前不声称通过。
