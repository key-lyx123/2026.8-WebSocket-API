# 仿真侧 WebSocket API 定义（api.md）

适用人员：Group A（仿真侧 / Webots Controller）、Group B/C（算法侧 / LLM Agent / 数据采集）、Group D（评测侧）
协议版本：`1.0`

本文档是唯一权威接口定义。仿真侧发送的每一帧 obs 必须严格对齐本文档字段；算法侧按本文档解析。

---

## 0. 全局约定

| 约定项 | 值 | 说明 |
|---|---|---|
| 长度单位 | 米 (m) | 所有距离、位置 |
| 角度单位 | 弧度 (rad) | 偏航角、方位角、角速度；离散动作编号除外 |
| 时间单位 | 秒 (s) | `step_id` 为 episode 内步数，`step_id × control_dt` 即累计仿真时间 |
| 消息格式 | JSON，UTF-8，一条消息一个 JSON 对象 | 禁止发送裸 `inf`/`nan`（JSON 不支持），无效值用 `null` 或量程上限替代 |
| 坐标系 | ENU（X=东, Y=北, Z=上） | 与 Webots 默认一致，与 `motion_control_ws.py` 的 Crazyflie 坐标一致 |
| 方位角 bearing 约定 | 无人机机体坐标系，机头方向为 0，左正右负，取值 (-π, π] | 与 A2 一致 |
| 动作范围 | vx,vy ∈ [-0.5, 0.5] m/s，vz ∈ [-0.5, 0.5] m/s，yawrate ∈ [-2.094, 2.094] rad/s (±120°/s) | 发送方可发任意值，server 端 clip |
| 归一化职责 | **仿真侧发原始物理量；算法侧负责归一化/文本序列化** | 仿真侧不管 LLM 吃什么格式 |
| 连接保活 | **使用 WebSocket 库自带 ping/pong**，应用层不实现心跳 | Webots controller 可能阻塞数十秒（物理步推进），库级 ping/pong 超时应设 ≥60s |
| 断线策略 | v1 不支持断线重连：连接断开双方直接退出，重新启动 | |
| episode 终止原因 | 三种：`collision` / `goal_reached` / `timeout`，互斥 | |

### 全局常量（hello 中 server 下发，以此表为默认）

| 常量 | 默认值 | 说明 |
|---|---|---|
| `lidar_layers` | 16 | 3D LiDAR 垂直层数，每层一条水平 360° 扫描环 |
| `lidar_points_per_layer` | 180 | 每层水平采样点数（2° 间隔） |
| `lidar_max_range` | 50.0 | LiDAR 最大量程 (m)。无回波或超距按此值发送 |
| `ultrasonic_count` | 8 | 超声波阵列方向数（8 方向：N/NE/E/SE/S/SW/W/NW） |
| `ultrasonic_max_range` | 5.0 | 超声波最大量程 (m) |
| `control_dt` | 0.1 | 决策周期：server 每收到一个 action，把仿真推进 0.1 s（内部 10 个物理步 × 10 ms）。等价于 10 Hz 决策频率 |
| `max_episode_time` | 60.0 | 单 episode 仿真时间上限，60 s ÷ 0.1 s = 600 步封顶 |
| `v_max_xy` | 0.5 | 水平速度上限 (m/s)，clip 范围 [-0.5, 0.5] |
| `v_max_z` | 0.5 | 垂直速度上限 (m/s)，clip 范围 [-0.5, 0.5] |
| `yawrate_max` | 2.094 | 偏航角速度上限 (rad/s)，即 ±120°/s，clip 范围 [-2.094, 2.094] |
| `goal_tolerance` | 0.5 | 到达判定半径 (m)，无人机中心与目标 3D 距离 ≤ 0.5 即 `goal_reached` |
| `robot_radius` | 0.08 | 无人机外接圆半径近似 (m)，碰撞判定简化为 `min(lidar) < robot_radius` |
| `arena_size` | [20.0, 20.0, 3.0] | 场地尺寸 [长x, 宽y, 高z] (m) |

---

## 1. 系统组成与角色

系统分多方，共用同一套 WebSocket 协议。仿真侧为 server，其余各方为 client，同一时刻只有一个 client 连接。

         WebSocket (ws://127.0.0.1:8765)

  ┌──────────────┐                        ┌──────────────┐
  │ Group A      │    JSON 消息 (一问一答)   │   Client 侧   │
  │ 仿真侧        │◀═══════════════════════▶│ (同时只有一个  │
  │ Webots       │                         │   连接)       │
  │ (env server) │                         │              │
  └──────────────┘                         └──────┬───────┘
                                                  │
                          ┌───────────────────────┼───────────────────────┐
                          │                       │                       │
                   ┌──────┴──────┐        ┌───────┴───────┐        ┌──────┴──────┐
                   │ 算法侧       │        │ 数据侧         │        │ 评测侧       │
                   │ (在线推理)   │        │ (离线采集)     │        │ (批量评测)   │
                   │             │        │               │        │             │
                   │ 微调Qwen    │        │ SOTA LLM      │        │ 所有方法     │
                   │ + Hermes    │        │ + Hermes      │        │ 批量跑       │
                   │ 累积上下文   │        │ 累积上下文     │        │ 指标统计     │
                   │ 推理+决策    │        │ 推理+收集对话  │        │             │
                   └─────────────┘        └───────────────┘        └─────────────┘
                              └─────────────┘    └───────────────┘       └─────────────┘
- **Group A（仿真侧 / server）**：Webots 世界 + Crazyflie 仿真模型 + 虚拟传感器，充当 WebSocket **server**（监听端口 `8765`）。对所有 client 一视同仁，不关心 action 是 LLM 产生的还是专家程序产生的。
- **算法侧（在线 client）**：实时闭环。obs 文本序列化 -> 微调后 LLM 推理 -> 动作解析 -> 发 action。用于训练后评测、RL 在线收集。Agent 框架使用 **Hermes Agent**（Python 原生），不使用 OpenClaw（Node.js，与 Python 训练侧跨语言交互麻烦）。微调后的 Qwen2.5-7B + LoRA 通过 vLLM 部署为 OpenAI 兼容 API，Hermes Agent 直接调用。
- **数据侧（离线 client）**：SFT 数据生产管线（详见 §8）。SOTA LLM（deepseek-v4-flash）通过 Hermes Agent 连接仿真，累积上下文做多轮对话决策，直接收集含 reasoning_content 的完整对话作为 SFT 训练数据。一次到位，不需要程序专家或二次加工。
- **评测侧（批量 client）**：跑所有方法（基线 + LLM）的批量评测，统计指标。

> 三种 client 共用同一套协议，同一时刻只有一个连接。仿真侧无法区分也不需要区分 client 类型——收到 `action` 就推进，收到 `reset` 就开新局。

---

## 2. 无人机传感器配置

仿真无人机搭载以下虚拟传感器（Webots 中用对应节点实现）：

| # | 传感器 | Webots 节点 | 关键参数 | 原始输出 |
|---|---|---|---|---|
| 1 | **3D LiDAR** | `Lidar` | 16 层 × 180 点/层，360° 水平，±15° 垂直，50m 量程，10Hz | `lidar_3d[layers][points]`：每层一条水平扫描环的距离值数组 |
| 2 | **RTK 定位** | `GPS` (high accuracy) | 精度 ±1cm，20Hz | `agent.pos [x,y,z]`：ENU 绝对坐标 |
| 3 | **战术级 IMU** | `InertialUnit` + `Accelerometer` + `Gyro` | 姿态精度 0.1°，200Hz | `agent.yaw`、`agent.angular_vel [wx,wy,wz]`、`agent.linear_accel [ax,ay,az]` |
| 4 | **下视 ToF** | `DistanceSensor` | 量程 4m，精度 ±1cm，50Hz | `height_agl`：距地高度 |
| 5 | **8 向超声波** | `DistanceSensor` ×8 | 8 方向 (N/NE/E/SE/S/SW/W/NW)，量程 5m，40Hz | `ultrasonic[8]`：8 方向距离值 |

---

## 3. WebSocket 消息总表

| 消息 type | 方向 | 触发时机 |
|---|---|---|
| `hello` | server -> client | 连接建立后 server 立即发送 |
| `reset` | client -> server | 开始新 episode 前 |
| `obs` | server -> client | ①reset 后的初始观测 ②每收到一个合法 action 推进后 |
| `action` | client -> server | 对某条 obs 的回应，一一对应 |
| `all_finish` | client -> server | 训练/评测全部结束 |
| `bye` | server -> client | 收到 all_finish 后回应，随后关连接 |
| `error` | 双向 | 任何非法消息/状态错误 |

> 无 `ping`/`pong` 应用层消息--WebSocket 库自带的心跳机制处理保活。

---

## 4. `obs` 消息详解（server -> client）

这是核心消息：仿真侧每步发给算法侧的全部传感器数据。`reset` 的响应和 `action` 的响应都是 `obs`，字段完全一致；`reset` 响应中 `step_id=0`、`t=0.0`。

```json
{
  "type": "obs",
  "episode_id": 1,
  "step_id": 17,
  "agent": {
    "pos": [2.50, 3.10, 1.20],
    "vel": [0.30, 0.00, 0.00],
    "yaw": 0.785,
    "angular_vel": [0.0, 0.0, 0.0],
    "linear_accel": [0.0, 0.0, -9.81]
  },
  "lidar_3d": [
    [0.83, 0.91, 1.20, "..."],
    [0.85, 0.93, 1.22, "..."],
    "..."
  ],
  "ultrasonic": [1.2, 3.5, 2.1, 5.0, 4.8, 5.0, 3.2, 1.8],
  "height_agl": 1.20,
  "goal": {
    "pos": [8.00, 6.00, 1.50],
    "dist": 6.02,
    "bearing_xy": 0.42,
    "bearing_z": 0.05
  },
  "obstacles": [
    {"id": 0, "type": "cylinder", "pos": [4.0, 3.0, 0.0], "radius": 0.3, "height": 2.0, "distance": 1.51},
    {"id": 1, "type": "box", "pos": [5.0, 5.0, 0.0], "size": [1.0, 1.0, 1.5], "distance": 3.16}
  ],
  "flags": {"collision": false, "goal_reached": false, "timeout": false},
  "done": false
}
```

### 4.1 字段来源与说明

#### `agent` - 无人机自身状态

| 字段 | 类型 | 单位 | 来源传感器 | 获取方式 | 精度/噪声 |
|---|---|---|---|---|---|
| `pos` | [float×3] | m | RTK 定位 | **直接测量** | ±0.01m |
| `vel` | [float×3] | m/s | RTK 定位 | **算法：位置差分** `v = (pos_t - pos_{t-1}) / Δt` | ±0.05m/s（由位置噪声差分放大） |
| `yaw` | float | rad | IMU (磁力计+陀螺仪) | **直接测量**（EKF 融合输出） | ±0.002rad |
| `angular_vel` | [float×3] | rad/s | IMU 陀螺仪 | **直接测量** | ±0.0002rad/s |
| `linear_accel` | [float×3] | m/s² | IMU 加速度计 | **直接测量** | ±0.005m/s² |

> `pos` 和 `yaw` 是传感器直接输出，无累积漂移。`vel` 由 RTK 位置差分得到。

#### `lidar_3d` - 3D 激光雷达扫描

| 属性 | 值 |
|---|---|
| 来源传感器 | 3D LiDAR（Ouster OS0-32 仿真） |
| 获取方式 | **直接测量** |
| 数据结构 | 二维数组 `lidar_3d[layers][points]`，外层 16 个层（从上到下 -15° 到 +15°），内层每层 180 个距离值（0° 到 358°，2° 间隔） |
| 排列方向 | 每层第 0 个点为无人机机头正前方，逆时针排列 |
| 无效回波 | 超距或无回波按 `lidar_max_range` (50.0) 填充 |
| 噪声 | ±0.02m 高斯噪声 |
| 遮挡 | 遵循物理遮挡：障碍物后方无回波 |

> 这是原始传感器数据。算法侧可据此自行做点云聚类、障碍检测；也可直接使用 `obstacles` 字段（仿真侧已处理）。

#### `ultrasonic` - 8 向超声波阵列

| 属性 | 值 |
|---|---|
| 来源传感器 | 超声波阵列 ×8 |
| 获取方式 | **直接测量** |
| 数据结构 | `ultrasonic[8]`，8 个距离值 |
| 方向排列 | [N, NE, E, SE, S, SW, W, NW]，N=机头正前，顺时针 |
| 无效回波 | 超距按 `ultrasonic_max_range` (5.0) 填充 |
| 噪声 | ±0.01m |

> 短距补盲：LiDAR 在 0.5m 内有盲区，超声波覆盖近距离避障。

#### `height_agl` - 距地高度

| 属性 | 值 |
|---|---|
| 来源传感器 | 下视 ToF 激光测距 |
| 获取方式 | **直接测量** |
| 单位 | m |
| 精度 | ±0.01m |
| 量程 | 0–4m，超出量程返回 `null` |

> 与 `agent.pos[2]` (RTK Z) 互补：RTK 给绝对高度，ToF 给距地高度。室内地面可能不完全水平，两者有差异。

#### `goal` - 目标信息

| 字段 | 类型 | 单位 | 来源 | 获取方式 |
|---|---|---|---|---|
| `pos` | [float×3] | m | **任务下发**（非传感器） | episode 开始时由 server 指定，全程不变 |
| `dist` | float | m | RTK 自身位置 + 任务给定目标位置 | **算法：欧氏距离** `||agent.pos - goal.pos||` |
| `bearing_xy` | float | rad | 同上 | **算法：水平方位角**，机体坐标系，(-π, π] |
| `bearing_z` | float | rad | 同上 | **算法：垂直俯仰角**，(-π/2, π/2] |

> `goal.pos` 是**任务输入**，不是传感器测量。无人机"知道要去哪"（任务给定），但"自己在哪"靠 RTK 测量。`dist` 和 `bearing` 是两者的几何计算，精度取决于 RTK（±1cm 级别），可视为精确。

#### `obstacles` - 障碍物列表（已处理）

| 字段 | 类型 | 单位 | 来源传感器 | 获取方式 |
|---|---|---|---|---|
| `id` | int | - | - | 仿真侧分配，episode 内不变 |
| `type` | string | - | LiDAR 点云 + 形状分类 | **算法：点云聚类 + 形状拟合** |
| `pos` | [float×3] | m | LiDAR 点云 | **算法：聚类质心** |
| `radius` | float | m | LiDAR 点云 | **算法：圆柱/球体拟合**（type 为 cylinder/sphere 时有） |
| `height` | float | m | LiDAR 点云 | **算法：点云 Z 范围**（type 为 cylinder 时有） |
| `size` | [float×3] | m | LiDAR 点云 | **算法：AABB 包围盒**（type 为 box 时有） |
| `distance` | float | m | LiDAR 点云 | **算法：到表面最近距离** |

> **这是仿真侧从 LiDAR 原始点云处理后的结果**，使用 DBSCAN 聚类 + PCA 形状分类。算法侧可直接使用此字段，也可忽略它自行从 `lidar_3d` 原始数据做处理。
>
> **遮挡限制**：只有 LiDAR 视线可达的障碍物才会出现在列表中。被其他障碍物完全遮挡的物体不可见。但 3D LiDAR 360° 扫描 + 无人机移动过程中多角度扫描，大多数障碍物至少有部分点云可被探测。

#### `flags` - 终止标志

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `collision` | bool | LiDAR + 碰撞检测 | 本步发生碰撞（`min(lidar) < robot_radius`） |
| `goal_reached` | bool | RTK + goal.pos | 本步到达目标（`dist ≤ goal_tolerance`） |
| `timeout` | bool | 仿真时钟 | 本步达到 `max_episode_time` |

> 终止优先级：`collision` > `goal_reached` > `timeout`，三者互斥。

### 4.2 数据来源汇总

```
                    传感器直接测量              算法处理（仿真侧内部）        任务输入
                    ┌────────────┐             ┌──────────────────┐       ┌──────────┐
                    │ RTK        │──► pos      │ RTK 位置差分      │──► vel │          │
                    │ IMU        │──► yaw      │                  │        │          │
                    │            │──► angular  │                  │        │          │
                    │            │──► accel    │                  │        │          │
                    │ 3D LiDAR   │──► lidar_3d │ 点云聚类+形状拟合  │──► obs  │ goal.pos │
                    │ 超声波 ×8   │──► ultra   │                  │        │ (任务下发)│
                    │ ToF        │──► height   │                  │        │          │
                    └────────────┘             │ 几何计算          │──► dist│          │
                                               │                  │   bear│          │
                                               │ 碰撞检测          │──►flag│          │
                                               └──────────────────┘       └──────────┘
```

**一图总结**：

| obs 字段 | 传感器直接出 | 仿真侧算法处理 | 任务输入 |
|---|---|---|---|
| `agent.pos` | ✅ RTK | | |
| `agent.vel` | | ✅ RTK 差分 | |
| `agent.yaw` | ✅ IMU | | |
| `agent.angular_vel` | ✅ IMU 陀螺仪 | | |
| `agent.linear_accel` | ✅ IMU 加速度计 | | |
| `lidar_3d` | ✅ 3D LiDAR | | |
| `ultrasonic` | ✅ 超声波阵列 | | |
| `height_agl` | ✅ ToF | | |
| `goal.pos` | | | ✅ 任务下发 |
| `goal.dist` | | ✅ RTK+goal 几何计算 | |
| `goal.bearing_*` | | ✅ 同上 | |
| `obstacles` | | ✅ LiDAR 点云聚类 | |
| `flags` | | ✅ 碰撞/距离/时钟判定 | |

---

## 5. 各消息详细定义

### 5.1 `hello`（server -> client）

连接建立后 server 立即发送。client 校验 `protocol_version` 主版本兼容、`config` 常量与预期一致。

```json
{
  "type": "hello",
  "protocol_version": "1.0",
  "env_name": "webots_crazyflie_3d_v1",
  "config": {
    "lidar_layers": 16,
    "lidar_points_per_layer": 180,
    "lidar_max_range": 50.0,
    "ultrasonic_count": 8,
    "ultrasonic_max_range": 5.0,
    "control_dt": 0.1,
    "max_episode_time": 60.0,
    "v_max_xy": 0.5,
    "v_max_z": 0.5,
    "yawrate_max": 2.094,
    "goal_tolerance": 0.5,
    "robot_radius": 0.08,
    "arena_size": [20.0, 20.0, 3.0]
  }
}
```

### 5.2 `reset`（client -> server）

```json
{
  "type": "reset",
  "seed": 42,
  "config_override": null
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `seed` | int | 是 | `-1` = 随机（训练用）；非负整数 = 固定场景复现（评估/调试用）。相同 seed 必须产生相同场景 |
| `config_override` | object\|null | 是 | 本局临时覆盖的参数，目前仅支持 `max_episode_time`；不需要时填 `null` |

server 收到后：用 seed 初始化 RNG -> `generate_scenario(seed)` 生成保证有解的 3D 避障场景（§7.4）-> 写入 Webots 世界 -> 传送无人机到起点 -> `episode_id` 自增、`step_id=0` -> 回初始 obs。

### 5.3 `obs`（server -> client）

见 §4 完整定义。`reset` 响应和 `action` 响应都是此格式。

### 5.4 `action`（client -> server）

**连续模式**：

```json
{
  "type": "action",
  "episode_id": 1,
  "step_id": 17,
  "vx": 0.3,
  "vy": 0.0,
  "vz": 0.2,
  "yawrate": 0.0
}
```

| 字段 | 类型 | 单位 | 说明 |
|---|---|---|---|
| `episode_id` | int | - | 必须等于所回应 obs 的 episode_id |
| `step_id` | int | - | 必须等于所回应 obs 的 step_id |
| `vx` | float | m/s | 前后速度，正=前进，server clip 到 [-v_max_xy, v_max_xy] |
| `vy` | float | m/s | 左右速度，正=左移，server clip 到 [-v_max_xy, v_max_xy] |
| `vz` | float | m/s | 上下速度，正=上升，server clip 到 [-v_max_z, v_max_z] |
| `yawrate` | float | rad/s | 偏航角速度，正=左转，server clip 到 [-yawrate_max, yawrate_max] |

**离散模式**：

```json
{
  "type": "action",
  "episode_id": 1,
  "step_id": 17,
  "action_id": 9
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `action_id` | int (0–9) | 见下表，server 内部转换为 (vx, vy, vz, yawrate) 后执行 |

离散动作表：

| ID | 名称 | (vx, vy, vz, yawrate) |
|---|---|---|
| 0 | 悬停 | (0, 0, 0, 0) |
| 1 | 前进 | (0.3, 0, 0, 0) |
| 2 | 后退 | (-0.3, 0, 0, 0) |
| 3 | 左移 | (0, 0.3, 0, 0) |
| 4 | 右移 | (0, -0.3, 0, 0) |
| 5 | 上升 | (0, 0, 0.3, 0) |
| 6 | 下降 | (0, 0, -0.3, 0) |
| 7 | 左转 | (0, 0, 0, -1.047) |
| 8 | 右转 | (0, 0, 0, 1.047) |
| 9 | 前进+上升 | (0.3, 0, 0.2, 0) |

### 5.5 `all_finish`（client -> server）

```json
{"type": "all_finish", "reason": "converged", "total_episodes": 1200}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `reason` | string | `"converged"` / `"interrupted"` / `"error"` |
| `total_episodes` | int | 累计 episode 总数 |

### 5.6 `bye`（server -> client）

```json
{"type": "bye", "reason": "all_finish received"}
```

### 5.7 `error`（双向）

```json
{"type": "error", "code": "WRONG_STATE", "detail": "action received while waiting for reset"}
```

| code | 含义 |
|---|---|
| `BAD_JSON` | JSON 解析失败 |
| `BAD_TYPE` | type 未知 |
| `BAD_FIELD` | 缺字段/类型错/数组长度错 |
| `WRONG_STATE` | 状态机不允许的消息 |
| `VERSION_MISMATCH` | 协议主版本不一致 |
| `INTERNAL` | 内部异常，detail 带堆栈摘要 |

---

## 6. 状态机与完整时序

### 6.1 server 状态机

```
连接建立 -> 发 hello -> WAIT_RESET
WAIT_RESET ──收到 reset──▶ RUNNING（回初始 obs）
RUNNING ──收到 action──▶ 推进 0.1s ──▶ 回 obs ──▶ RUNNING
RUNNING ──obs.done=true──▶ WAIT_RESET
WAIT_RESET ──收到 all_finish──▶ 发 bye ──▶ 关闭
其余消息 -> 回 error 并退出
```

### 6.2 完整时序

```
client (算法侧)                      server (仿真侧)
   │──── connect ──────────────────▶│
   │◀────────── hello ─────────────│  client 校验版本/常量
   │──── reset(seed=42) ──────────▶│  内部: RNG->障碍->放无人机->定目标
   │◀──────── obs(ep=1, s=0) ──────│
   │  序列化为文本->LLM推理->解析动作  │
   │──── action(ep=1, s=0) ───────▶│  内部: clip->运动学->推进0.1s->LiDAR读取->判终止
   │◀──────── obs(ep=1, s=1) ──────│
   │              ……                │
   │◀────── obs(done=true) ────────│  本 episode 结束
   │  存数据/更新模型                 │  （server 原地等待）
   │──── reset(seed=-1) ──────────▶│
   │              ……                │
   │──── all_finish ──────────────▶│
   │◀──────────── bye ─────────────│  双方退出
```

---


### 6.3 算法侧主循环（伪代码）


> 文件路径：`src/agent/run_agent.py`（算法侧入口），依赖 `src/agent/protocol.py`（协议工具）、`src/agent/serialize.py`（序列化）。LLM 推理通过 Hermes Agent 框架（`pip install hermes-agent`），微调模型由 vLLM 部署。与数据侧（§8.3）使用同一套 Hermes Agent + 累积上下文模式。

```python
from agent.protocol import connect, recv, send, assert_compatible, all_finish_msg
from agent.serialize import serialize_state
from hermes import Agent

SYSTEM_PROMPT = "你是一架无人机的自动驾驶 Agent。请根据当前 3D 状态和对话历史输出下一步动作。只输出动作编号。"

ws = connect("ws://127.0.0.1:8765")
hello = recv(); assert_compatible(hello)

agent = Agent(model="Qwen/Qwen2.5-7B-Instruct", lora_path="./outputs/lora_weights",
              api_base="http://localhost:8000/v1")  # vLLM 部署的微调模型

for episode in range(MAX_EPISODES):
    send({"type": "reset", "seed": schedule(episode), "config_override": None})
    obs = recv()

    # 每个 episode 重置对话历史（只保留 system prompt）
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    while not obs["done"]:
        state_text = serialize_state(obs)          # src/agent/serialize.py::serialize_state
        response = agent.chat(messages=messages, user_message=state_text)
        # response = {"reasoning_content": "...", "content": "9"}

        # 累积到对话历史（训练时也是这个格式）
        messages.append({"role": "user", "content": state_text})
        messages.append({"role": "assistant", "content": response["content"],
                         "reasoning_content": response["reasoning_content"]})

        action = {"action_id": int(response["content"])}  # 只解析动作编号
        send({"type": "action", "episode_id": obs["episode_id"],
              "step_id": obs["step_id"], **action})
        obs = recv()

    log(episode)
    if converged(): break

send(all_finish_msg(reason="converged", total_episodes=episode+1))
recv()  # bye
```
```
## 7. 仿真侧内部实现要点（Group A 参考）

### 7.1 Webots 世界

- Crazyflie 2.1 模型（或自定义无人机模型），`supervisor TRUE`，`basicTimeStep=10`
- 3D LiDAR：`Lidar` 节点，`numberOfLayers=16`，`horizontalResolution=180`，`maxRange=50`，360° 水平 FOV
- IMU：`InertialUnit` (quaternion -> yaw) + `Accelerometer` + `Gyro`
- RTK：`GPS` 节点，`accuracy=0.01`，`speed=20`（20Hz）
- ToF：`DistanceSensor` 向下，`type="generic"`，`maxValue=4.0`
- 超声波：8 个 `DistanceSensor`，水平面 8 方向，`maxValue=5.0`
- 场地：20m × 20m × 3m 室内场景；障碍物类型与布局见 §7.4 场景生成

### 7.2 action 内部流程

1. 校验状态与 `episode_id`/`step_id`，不符回 error
2. 离散模式：查表将 `action_id` 转为 `(vx, vy, vz, yawrate)`
3. clip `(vx, vy, vz, yawrate)` 到合法范围
4. 运动学积分：每物理步更新位置/速度/姿态，10 个物理步共 0.1s
5. 每物理步检查碰撞：`min(lidar_3d 所有层所有点) < robot_radius` -> 立即停止，置 `collision`
6. 读取全部传感器：LiDAR、IMU、RTK、ToF、超声波
7. 点云处理：DBSCAN 聚类 -> PCA 形状分类 -> 计算 obstacle 列表
8. 计算 goal 距离/方位
9. 判 `goal_reached`（`dist ≤ goal_tolerance`）与 `timeout`（`t ≥ max_episode_time`）
10. 组装 obs 发送

### 7.3 LiDAR 点云 -> obstacles 处理流程

```
原始 lidar_3d[16][180]
    │
    ▼ 极坐标 -> 笛卡尔 (x,y,z) 点云
点云 P = {(x_i, y_i, z_i)}
    │
    ▼ DBSCAN 聚类 (eps=0.3, min_samples=5)
簇 C_1, C_2, ..., C_k
    │
    ▼ 对每个簇:
    │   ├─ 质心 = mean(C_j)          -> obstacle.pos
    │   ├─ PCA 主成分分析
    │   │   ├─ 三轴方差比 -> 形状判定
    │   │   │   ├─ 一轴显著大 + 圆截面 -> "cylinder"
    │   │   │   ├─ 三轴相近           -> "sphere"
    │   │   │   └─ 三轴不同但无显著圆  -> "box"
    │   │   ├─ cylinder: radius = mean(到主轴距离), height = Z范围
    │   │   └─ box: size = [dx, dy, dz] (AABB)
    │   └─ distance = min(点到无人机表面距离) = min(|点 - agent.pos|) - robot_radius
    │
    ▼
obstacles = [{id, type, pos, radius/size, height, distance}, ...]
```


### 7.4 场景生成（Group A 核心职责）

场景生成不是"随机撒障碍物"这么简单。一个合格的场景必须满足两个硬约束：

1. **有解性**：起点到目标之间必须存在一条物理可飞的路径（无人机能实际通过的 3D 通道）。
2. **3D 意义**：障碍物布局必须让 z 轴（高度维度）参与决策--如果最优策略只是 2D 平面绕障、高度恒定，那 3D 仿真就白做了。

#### 7.4.1 有解性保证

**生成-验证-重试流程**：

```
随机放置障碍物
      │
      ▼
3D 栅格可达性检查（BFS/A*）
  ├─ 可达 → 接受该场景
  └─ 不可达 → 移除阻断路径的障碍物，或重新随机，最多重试 200 次
```

**可达性检查细节**：

> 文件路径：`src/sim/solvability.py`
>
> ```python
> def check_solvability(
>     start: tuple[float, float, float],
>     goal: tuple[float, float, float],
>     obstacles: list[dict],
>     clearance: float = 0.2,  # 通道最小宽度 = 2 x robot_radius(0.08) + 安全余量(0.04)
>     grid_resolution: float = 0.2,  # 栅格分辨率
>     arena_size: tuple[float, float, float] = (20.0, 20.0, 3.0),
> ) -> bool:
>     """src/sim/solvability.py :: 在 3D 栅格上 BFS 检查 start->goal 是否存在
>     一条任意位置离障碍物表面 >= clearance 的通道。返回 True/False。"""
> ```

**关键参数**：

| 参数 | 值 | 说明 |
|---|---|---|
| `clearance` | 0.2m | 通道最小半宽。= 2 x robot_radius(0.08) + 0.04 安全余量。比这窄无人机物理上过不去 |
| `grid_resolution` | 0.2m | 栅格分辨率。太粗会漏掉窄缝，太细 BFS 变慢。0.2m约为无人机直径的 2.5 倍 |
| `max_retries` | 200 | 单个场景最多重试 200 次障碍物布局，仍无解则降低障碍物数量重试 |

**重试策略**：如果 200 次仍无解，按以下顺序降级：
1. 移除距离起点/目标最近的障碍物（最可能阻断路径的）
2. 减少 `n_obstacles` 1 个，重新生成
3. 如果 n_obstacles 已降到 3 仍无解，记录该 seed 为" inherently_unsolvable"并跳过

#### 7.4.2 3D 避障环境设计

环境必须让高度维度有决策意义。障碍物分三类，分别制造不同的 3D 避障需求：

| 障碍类型 | 几何 | 高度特征 | 避障方式 | 场景含义 |
|---|---|---|---|---|
| **柱类** | cylinder | floor-to-ceiling (2.5-3.0m) | **必须绕行**，无法飞越 | 承重柱、管道 |
| **矮类** | box | 0.3-1.2m 高 | **可飞越**，也可绕行；飞越更短但需爬升 | 桌椅、矮柜、地面设备 |
| **悬空类** | box，底面在 1.5-2.5m | 底面悬空，顶面接天花板 | **必须从下方穿过**，需下降 | 吊灯、横梁、悬挂设备 |

**三类障碍物的配比**决定场景难度：

| 难度 | 柱类 | 矮类 | 悬空类 | 总数 | 场景特征 |
|---|---|---|---|---|---|
| easy | 2-3 | 3-5 | 0-1 | 5-9 | 稀疏，多数可飞越，路径选择多 |
| medium | 3-5 | 4-6 | 1-2 | 8-13 | 中等密度，部分需绕行+部分需高度变化 |
| hard | 5-7 | 5-8 | 2-3 | 12-18 | 密集，柱类封路需绕远+悬空类逼下降+矮类逼爬升 |

> **设计意图**：柱类迫使水平绕行（xy 决策），矮类迫使爬升决策（z 决策），悬空类迫使下降决策（z 决策）。三类混搭让 LLM 必须同时规划 xy 路径和 z 高度，而不是把高度固定在某值只做 2D 导航。

**起始和目标位置约束**：

| 约束 | 规则 | 原因 |
|---|---|---|
| 起始高度 | 0.8-1.2m | 典型室内巡航高度 |
| 目标高度 | 0.5-2.0m | 与起始有高度差，迫使 z 轴运动 |
| 水平距离 | >= 5m | 太近没有避障空间 |
| 起始/目标离最近障碍 | >= 0.5m | 不能出生在障碍物里或贴着障碍起飞 |
| 起始-目标直线是否可达 | 不要求 | 直线不可达才需要绕障，这才是有意义的场景 |

#### 7.4.3 场景生成完整流程

> 文件路径：`src/sim/scenario.py`
>
> ```python
> def generate_scenario(
>     seed: int,
>     difficulty: str = "medium",   # "easy" / "medium" / "hard"
>     arena_size: tuple = (20.0, 20.0, 3.0),
> ) -> dict:
>     """src/sim/scenario.py :: 生成一个保证有解的 3D 避障场景。
>
>     返回:
>         {
>             "seed": int,
>             "start_pos": [x, y, z],
>             "goal_pos": [x, y, z],
>             "obstacles": [
>                 {"type": "cylinder", "pos": [x,y,z], "radius": r, "height": h},
>                 {"type": "box", "pos": [x,y,z], "size": [lx, ly, lz]},
>                 ...
>             ],
>             "difficulty": str,
>         }
>
>     流程:
>         1. rng = default_rng(seed)
>         2. 根据 difficulty 确定三类障碍物数量配比
>         3. 随机放置障碍物 (满足起始/目标/障碍物间距约束)
>         4. check_solvability() 验证有解性
>         5. 无解则重试 (最多 200 次)，仍无解则降级
>         6. 返回场景 dict
>     """
> ```

**reset 内部流程补充**（§5.2 中 server 收到 reset 后的详细步骤）：

```
1. generate_scenario(seed, difficulty)        # src/sim/scenario.py
2. 将场景写入 Webots 世界 (supervisor setSFVec3f 移动障碍物节点)
3. 传送无人机到 start_pos
4. simulationResetPhysics()
5. 静置 3 个物理步
6. episode_id++, step_id=0
7. 读取初始传感器数据，组装 obs，发送
```
---
## 8. 数据侧：SFT 数据生产管线

### 8.1 思路

SOTA LLM（GPT-4 / Claude 等）本身就是最好的"专家"--它理解 3D 空间、能推理避障逻辑、能输出动作。问题是它太慢（秒级/步），无法实时跑仿真闭环。

但**数据收集是离线的，不需要实时**。所以直接用 SOTA LLM + Hermes Agent 连接仿真，让 SOTA LLM 一步一步做决策，同时收集完整对话（含推理过程）。一次到位，不需要程序专家 + 二次加工。

**Hermes Agent 累积上下文**：每个 episode 是一个多轮对话，每步把新 obs 序列化后追加为 user 消息，SOTA LLM 的回复（含 reasoning_content + action）追加为 assistant 消息。收集到的数据天然就是长上下文多轮对话格式，与推理时 Hermes Agent 的行为完全一致。

**reasoning_content 与 output 分离**：每步 SOTA LLM 的回复拆为两个字段：
- `reasoning_content`：CoT 推理过程（"前方有障碍，需上升越过..."）
- `output`：动作编号（"9"）

SFT 训练时模型学习生成两者；推理时模型也生成两者，但控制逻辑只解析 `output`。`reasoning_content` 让模型"想清楚再回答"，类似 DeepSeek-R1 的推理模式。

### 8.2 管线

```
         离线（慢，但无所谓）
┌──────────────────────────────────────────────────────────┐
│                                                          │
│  仿真侧 ──obs──► Hermes Agent ──action──► 仿真侧          │
│  (WebSocket)    ├─ 累积多轮对话上下文                      │
│                 ├─ SOTA LLM 推理 (reasoning + action)     │
│                 │                                        │
│                 ▼ episode 结束后保存                      │
│           对话数据 (成功 episode)                          │
│         sft_conversations.jsonl                          │
│                                                          │
└──────────────────────────────────────────────────────────┘
                      │
                      │ 训练
                      ▼
           Qwen2.5-7B + LoRA (SFT)
                      │
                      │ RL（GRPO/PPO，在线收集，可选）
                      ▼
           Qwen2.5-7B + LoRA (SFT + RL)
```

### 8.3 第一步：SOTA LLM + Hermes Agent 轨迹采集

SOTA LLM 通过 Hermes Agent 框架连接仿真，作为 WebSocket client。每个 episode 是一个多轮对话，Hermes Agent 维护完整对话历史。

> 文件路径：`src/data/collect.py`（采集入口），依赖 `src/agent/protocol.py`（协议工具，与算法侧共用）、`src/agent/serialize.py`（序列化，与算法侧共用）、`hermes`（Agent 框架）。
>
> 函数签名：
>
> ```python
> def collect_episode(ws, sota_agent, seed: int) -> dict:
>     """src/data/collect.py :: 用 SOTA LLM + Hermes Agent 跑一个 episode。
>     返回完整对话 + episode 结果。成功才保留，失败丢弃。"""
> ```

**采集流程**：

```python
from agent.protocol import connect, recv, send, assert_compatible, all_finish_msg
from agent.serialize import serialize_state
from hermes import Agent

ws = connect("ws://127.0.0.1:8765")
hello = recv(); assert_compatible(hello)

# SOTA LLM 作为专家，通过 Hermes Agent 运行
sota_agent = Agent(
    model="claude-sonnet-4-20250514",   # 或 GPT-4 等 SOTA 模型
    api_base="https://api.anthropic.com",  # 或 OpenAI 等
)

for seed in range(N_SCENES):               # 100 个场景
    send({"type": "reset", "seed": seed, "config_override": None})
    obs = recv()

    # Hermes Agent 初始化新一轮对话（累积上下文）
    conversation = {
        "seed": seed,
        "messages": [],
        "system_prompt": SYSTEM_PROMPT,   # 角色设定 + 动作空间说明
    }

    while not obs["done"]:
        # 序列化当前状态为 user 消息
        state_text = serialize_state(obs)

        # Hermes Agent 累积上下文推理：SOTA LLM 看到完整历史 + 当前状态
        response = sota_agent.chat(
            messages=conversation["messages"],
            user_message=state_text,
        )
        # response = {"reasoning_content": "前方有障碍...", "content": "9"}

        # 追加到对话历史
        conversation["messages"].append({"role": "user", "content": state_text})
        conversation["messages"].append({
            "role": "assistant",
            "content": response["content"],                    # "9"
            "reasoning_content": response["reasoning_content"], # "前方有障碍..."
        })

        # 解析动作，发送给仿真
        action = {"action_id": int(response["content"])}
        send({"type": "action", "episode_id": obs["episode_id"],
              "step_id": obs["step_id"], **action})
        obs = recv()

    # 只保留成功的 episode
    if obs["flags"]["goal_reached"]:
        conversation["outcome"] = "success"
        save_to_jsonl("sft_conversations.jsonl", conversation)
    else:
        # 失败 episode 丢弃（或另存用于失败分析）
        pass

send(all_finish_msg(reason="converged", total_episodes=N_SCENES))
recv()  # bye
```

**关键点**：
- SOTA LLM 是专家，不需要 A\* / 势场法程序专家。
- Hermes Agent 累积上下文：第 N 步的决策能看到前 N-1 步的全部状态和动作，有完整时序信息。
- 对话越来越长：第 80 步的 prompt 包含前 79 轮对话 + 当前状态。SOTA LLM 上下文窗口足够（Claude 200K tokens，GPT-4 128K tokens）。
- 只保留成功 episode（`goal_reached=true`）。碰撞/超时的丢弃。
- 速度慢但无所谓：100 场景 × 80 步 × ~3s/步 ≈ 24,000s ≈ 7 小时。可并行多场景加速。

### 8.4 筛选

SOTA LLM 是专家，能成功到达目标就是好数据。有些场景天生就需要穿窄缝、贴障碍，这些恰恰是有价值的困难场景数据。筛选只看一个条件：

**成功到达目标（`goal_reached=true`）就保留，碰撞/超时的丢弃。**

```python
def filter_conversations(all_convs: list) -> list:
    """src/data/filter.py :: 只保留成功的 episode。"""
    return [c for c in all_convs if c.get("outcome") == "success"]
```

**预期数据量**：
- 采集：100 场景，每场景 1 次 SOTA LLM 尝试
- 筛选后：预计 60-80 条对话（部分场景 SOTA LLM 可能失败）
- 每条对话 ~60-100 轮（每轮 = 1 个 user + 1 个 assistant）
- 注意：每条对话是一个**完整的多轮对话样本**，不是独立的 pairs

### 8.5 SFT 数据格式

> 文件路径：`src/data/sft_gen.py`，依赖 `src/agent/serialize.py`。
>
> ```python
> def to_sft_format(conversation: dict) -> dict:
>     """src/data/sft_gen.py :: 将采集的对话转为 SFT 训练格式。"""
> ```

`sft_dataset.jsonl`，每行一个完整 episode 的多轮对话：

```json
{
  "messages": [
    {
      "role": "system",
      "content": "你是一架无人机的自动驾驶 Agent。请根据当前 3D 状态和对话历史输出下一步动作。只输出动作编号。"
    },
    {
      "role": "user",
      "content": "当前状态：\n- 无人机位置：(0.50, 0.50, 1.00)  单位: m\n- 无人机速度：(0.00, 0.00, 0.00)  单位: m/s\n- 无人机朝向(yaw)：0.0度\n- 距地高度：1.00 m\n- 目标位置：(8.00, 6.00, 1.50)\n- 目标距离：10.30 m\n- 目标水平方位：0.54 rad\n- 目标垂直方位：0.05 rad\n\n障碍物列表：\n  0. 类型:圆柱 位置(4.0,3.0,0.0) 半径0.3 高度2.5  距你3.54m\n  1. 类型:方盒 位置(5.0,5.0,0.0) 尺寸1.0x1.0x1.2  距你6.02m\n  2. 类型:方盒 位置(6.0,2.0,1.8) 尺寸0.8x0.8x0.5  距你5.52m\n\n超声波读数(8方向 N,NE,E,SE,S,SW,W,NW):\n  3.50, 4.20, 3.50, 5.00, 5.00, 5.00, 5.00, 5.00  单位: m\n\n请从以下动作中选择一个：\n[0] 悬停  [1] 前进  [2] 后退  [3] 左移  [4] 右移\n[5] 上升  [6] 下降  [7] 左转  [8] 右转  [9] 前进+上升\n\n只输出动作编号，不要输出其他内容。"
    },
    {
      "role": "assistant",
      "content": "1",
      "reasoning_content": "目标在右前方约10m处，当前速度为零，前方3.5m处无障碍，应开始前进。超声波前方读数3.5m确认前方安全。"
    },
    {
      "role": "user",
      "content": "当前状态：\n- 无人机位置：(0.53, 0.50, 1.00)  单位: m\n- 无人机速度：(0.30, 0.00, 0.00)  单位: m/s\n..."
    },
    {
      "role": "assistant",
      "content": "9",
      "reasoning_content": "前方3.2m处有圆柱障碍(高度2.5m，无法飞越)，但目标在右前方。需同时前进并上升至1.4m以上，从障碍上方通过。"
    }
  ]
}
```

**字段说明**：

| 字段 | 说明 |
|---|---|
| `messages` | 多轮对话列表，一个完整 episode |
| `messages[i].role` | `system` / `user` / `assistant` |
| `messages[i].content` | user = `serialize_state(obs)` 的输出；assistant = 动作编号字符串（如 `"9"`） |
| `messages[i].reasoning_content` | 仅 assistant 消息有。CoT 推理过程，SOTA LLM 生成 |

> `messages` 中的 user 消息格式与 §9 `serialize_state()` 输出完全一致，与推理时 Hermes Agent 的输入完全一致，保证 SFT 训练和推理的输入分布对齐。

### 8.6 推理时的上下文累积

SFT 训练数据是多轮对话，推理时也必须累积上下文，否则训练/推理不匹配：

```python
# src/agent/run_agent.py（推理时）
from hermes import Agent
from agent.serialize import serialize_state

agent = Agent(model="Qwen/Qwen2.5-7B-Instruct", lora_path="./outputs/lora_weights",
              api_base="http://localhost:8000/v1")  # vLLM 部署

messages = [{"role": "system", "content": SYSTEM_PROMPT}]

while not obs["done"]:
    state_text = serialize_state(obs)
    response = agent.chat(messages=messages, user_message=state_text)
    # response = {"reasoning_content": "...", "content": "9"}

    messages.append({"role": "user", "content": state_text})
    messages.append({"role": "assistant", "content": response["content"],
                     "reasoning_content": response["reasoning_content"]})

    action = {"action_id": int(response["content"])}
    send(action)
    obs = recv()
```

> **上下文增长**：第 N 步的 prompt 包含前 N-1 轮对话。到第 80 步时上下文约 20,000-40,000 tokens。Qwen2.5-7B 支持 128K 上下文，够用。
>
> **推理速度**：上下文越长推理越慢。前几步快（短上下文），后几步慢（长上下文）。但仿真是同步的（Server 等 Client），不影响正确性，只影响墙钟时间。
>
> **episode 间重置**：每个 episode 开始时 `messages` 清空，只保留 system prompt。跨 episode 不累积。

### 8.7 数据侧 vs 算法侧对比

| 维度 | 数据侧（离线采集） | 算法侧（在线推理） |
|---|---|---|
| **LLM** | SOTA LLM（GPT-4 / Claude） | 微调后 Qwen2.5-7B + LoRA |
| **框架** | Hermes Agent | Hermes Agent |
| **上下文** | 累积多轮对话 | 累积多轮对话（与训练一致） |
| **速度** | 慢（秒级/步），无所谓 | 慢（秒级/步），仿真同步等待 |
| **产出** | `sft_conversations.jsonl` | 在线决策 |
| **输出** | `reasoning_content` + `content`（动作编号） | `reasoning_content` + `content`（动作编号），只解析 `content` |

> 数据侧和算法侧用**同一个 Hermes Agent 框架**、**同一种对话累积模式**、**同一个 `serialize_state` 函数**。唯一区别是 LLM 模型不同（SOTA vs 微调 Qwen）。这保证训练和推理的输入分布完全对齐。

## 9. 文本序列化参考（Group B/C 参考）


> 文件路径：`src/agent/serialize.py`。此文件被算法侧、数据侧、评测侧三方共用。函数签名：`def serialize_state(obs: dict) -> str`。
> 输入：§4 定义的 obs dict。输出：LLM 可读的状态文本字符串。

```python
def serialize_state(obs: dict) -> str:
    a = obs["agent"]
    g = obs["goal"]
    obs_list = obs["obstacles"]

    lines = [
        "你是一架无人机的自动驾驶 Agent。请根据当前 3D 状态输出下一步动作。",
        "",
        "当前状态：",
        f"- 无人机位置：({a['pos'][0]:.2f}, {a['pos'][1]:.2f}, {a['pos'][2]:.2f})  单位: m",
        f"- 无人机速度：({a['vel'][0]:.2f}, {a['vel'][1]:.2f}, {a['vel'][2]:.2f})  单位: m/s",
        f"- 无人机朝向(yaw)：{a['yaw']:.1f}度",
        f"- 距地高度：{obs['height_agl']:.2f} m" if obs['height_agl'] else "",
        f"- 目标位置：({g['pos'][0]:.2f}, {g['pos'][1]:.2f}, {g['pos'][2]:.2f})",
        f"- 目标距离：{g['dist']:.2f} m",
        f"- 目标水平方位：{g['bearing_xy']:.2f} rad",
        f"- 目标垂直方位：{g['bearing_z']:.2f} rad",
        "",
        "障碍物列表：",
    ]

    for ob in obs_list:
        d = ob["distance"]
        danger = " (危险!)" if d < 0.5 else ""
        if ob["type"] == "cylinder":
            lines.append(f"  {ob['id']}. 类型:圆柱 位置({ob['pos'][0]:.1f},{ob['pos'][1]:.1f},{ob['pos'][2]:.1f}) "
                        f"半径{ob['radius']:.1f} 高度{ob['height']:.1f}  距你{d:.2f}m{danger}")
        elif ob["type"] == "box":
            s = ob["size"]
            lines.append(f"  {ob['id']}. 类型:方盒 位置({ob['pos'][0]:.1f},{ob['pos'][1]:.1f},{ob['pos'][2]:.1f}) "
                        f"尺寸{s[0]:.1f}x{s[1]:.1f}x{s[2]:.1f}  距你{d:.2f}m{danger}")

    lines += [
        "",
        "超声波读数(8方向 N,NE,E,SE,S,SW,W,NW):",
        "  " + ", ".join(f"{v:.2f}" for v in obs["ultrasonic"]) + "  单位: m",
        "",
        "请从以下动作中选择一个：",
        "[0] 悬停  [1] 前进  [2] 后退  [3] 左移  [4] 右移",
        "[5] 上升  [6] 下降  [7] 左转  [8] 右转  [9] 前进+上升",
        "",
        "只输出动作编号，不要输出其他内容。",
    ]

    return "\n".join(lines)
```

> 此函数同时用于：
> - **算法侧**：实时推理时把 obs 序列化为 LLM prompt
> - **数据侧**：SOTA LLM 加工时生成 SFT 的 `instruction` 字段
> - **评测侧**：评测时把 obs 序列化为 LLM prompt
>
> 三侧共用同一份序列化代码，保证 SFT 训练和推理的输入分布对齐。
> 算法侧可自由调整序列化格式（增减字段、改变措辞、加 few-shot 示例等），不影响协议。仿真侧只管发 obs，不管 client 怎么用。

---

## 10. 各侧入口与对外接口

§3-§5 定义了仿真侧的 WebSocket 接口（仿真侧 <-> 任意 client）。本节定义其余各侧的**入口命令**和**对外接口**，让各方知道怎么被调用、产出什么。

### 10.1 仿真侧（Group A）

| 项 | 说明 |
|---|---|
| **启动** | Webots 打开世界文件，Controller 进程自动启动 WebSocket server |
| **对外接口** | §3-§5 定义的 WebSocket API（`ws://127.0.0.1:8765`） |
| **输入** | WebSocket 消息（reset/action） |
| **输出** | WebSocket 消息（hello/obs/bye/error） |
| **无文件输出** | 仿真侧不写文件，所有数据通过 WebSocket 实时返回 |

### 10.2 数据侧（离线采集）

| 项 | 说明 |
|---|---|
| **入口** | `python -m data.collect --n-scenes 100 --sim-url ws://127.0.0.1:8765 --output data/sft_conversations.jsonl` |
| **输入** | 场景数量、SOTA LLM 配置（API key/model）、仿真 server URL |
| **对外接口** | **产出文件 `sft_conversations.jsonl`**（格式见 §8.5）。文件即接口--训练侧读这个文件做 SFT。 |
| **依赖** | 仿真侧必须已启动；SOTA LLM API 可用 |

> 文件路径：`src/data/collect.py`。CLI 参数：
> ```
> --n-scenes N        场景数量（默认 100）
> --sim-url URL       仿真侧 WebSocket 地址（默认 ws://127.0.0.1:8765）
> --output PATH       输出文件路径（默认 data/sft_conversations.jsonl）
> --sota-model NAME   SOTA LLM 模型名（默认 claude-sonnet-4-20250514）
> --seed-start N      起始 seed（默认 0，场景 seed = 0,1,...,N-1）
> ```

数据侧产出后，筛选由 `src/data/filter.py` 完成：
```bash
python -m data.filter --input data/sft_conversations.jsonl --output data/sft_dataset.jsonl
```

### 10.3 算法侧（在线推理）

算法侧有两种被调用方式：

**方式一：独立运行（联调/演示用）**

```bash
python -m agent.run_agent \
    --sim-url ws://127.0.0.1:8765 \
    --model-path Qwen/Qwen2.5-7B-Instruct \
    --lora-path ./outputs/lora_weights \
    --seeds 0-99 \
    --output results/agent_episodes.jsonl
```

直接连接仿真，跑指定 seed 的场景，输出每 episode 结果。

**方式二：被评测侧 import（评测用）**

> 文件路径：`src/agent/run_agent.py`
>
> ```python
> def run_episode(
>     sim_url: str,
>     seed: int,
>     agent_config: dict,   # {"model": "...", "lora_path": "...", "api_base": "..."}
> ) -> dict:
>     """src/agent/run_agent.py :: 跑一个 episode，返回结果。
>     
>     返回:
>         {
>             "seed": int,
>             "outcome": "success" | "collision" | "timeout",
>             "steps": int,
>             "final_dist": float,
>             "path_length": float,
>             "min_obstacle_dist": float,
>         }
>     """
> ```

评测侧 import 此函数，对每个测试 seed 调用一次，收集结果。

> CLI 参数（方式一）：
> ```
> --sim-url URL        仿真侧 WebSocket 地址
> --model-path PATH    模型路径
> --lora-path PATH     LoRA 权重路径
> --seeds A-B          seed 范围（如 0-99）
> --output PATH        结果输出文件
> ```

**算法侧输出文件格式**（`results/agent_episodes.jsonl`）：

```json
{"seed": 0, "outcome": "success", "steps": 87, "final_dist": 0.12, "path_length": 11.3, "min_obstacle_dist": 0.15}
{"seed": 1, "outcome": "collision", "steps": 23, "final_dist": 5.22, "path_length": 3.1, "min_obstacle_dist": -0.10}
```

### 10.4 评测侧（批量评测）

| 项 | 说明 |
|---|---|
| **入口** | `python -m eval.run_eval --methods random,straight,pf,llm --seeds 100-119 --sim-url ws://127.0.0.1:8765 --output results/` |
| **输入** | 方法列表、测试 seed 列表、仿真 server URL |
| **对外接口** | **产出目录 `results/`**，含汇总指标 + 每 episode 详情 |
| **对算法侧的调用** | 对 `llm` 方法，`from agent.run_agent import run_episode` 逐 seed 调用 |

> 文件路径：`src/eval/run_eval.py`，依赖 `src/agent/run_agent.py::run_episode`（LLM 方法）、`src/eval/baselines.py`（基线方法）。
>
> ```python
> def run_eval(
>     methods: list[str],       # ["random", "straight", "pf", "llm"]
>     seeds: list[int],         # [100, 101, ..., 119]
>     sim_url: str,
>     agent_config: dict | None,  # LLM 方法需要；基线不需要
> ) -> dict:
>     """src/eval/run_eval.py :: 跑所有方法 × 所有 seed，返回汇总指标。"""
> ```

**基线方法**（`src/eval/baselines.py`）：

```python
def random_policy(obs: dict) -> dict:
    """随机动作。返回 {"action_id": random_int(0,9)}。"""

def straight_policy(obs: dict) -> dict:
    """直行朝目标。不避障。返回 {"action_id": ...}。"""

def pf_policy(obs: dict) -> dict:
    """3D 势场法。引力=目标，斥力=障碍。返回 {"action_id": ...}。"""
```

> 基线方法签名与数据侧的专家策略一致（`obs -> action`），但它们作为评测 client 连接仿真，不是离线采集。

**评测侧输出文件格式**：

`results/metrics.json`（汇总）：
```json
{
  "llm": {"success_rate": 0.75, "collision_rate": 0.15, "timeout_rate": 0.10, "avg_steps": 95.3, "avg_path_efficiency": 1.42},
  "pf":  {"success_rate": 0.60, "collision_rate": 0.25, "timeout_rate": 0.15, "avg_steps": 110.5, "avg_path_efficiency": 1.68},
  "random": {"success_rate": 0.05, "collision_rate": 0.85, "timeout_rate": 0.10, "avg_steps": 150.0, "avg_path_efficiency": 5.0},
  "straight": {"success_rate": 0.10, "collision_rate": 0.80, "timeout_rate": 0.10, "avg_steps": 50.0, "avg_path_efficiency": 1.0}
}
```

`results/detail.jsonl`（每 episode 详情）：
```json
{"method": "llm", "seed": 100, "outcome": "success", "steps": 87, "path_length": 11.3, "min_obstacle_dist": 0.15}
{"method": "llm", "seed": 101, "outcome": "collision", "steps": 23, "path_length": 3.1, "min_obstacle_dist": -0.10}
{"method": "pf", "seed": 100, "outcome": "success", "steps": 120, "path_length": 14.5, "min_obstacle_dist": 0.12}
```

### 10.5 接口依赖关系总览

```
仿真侧 (WebSocket API)
    ↑
    ├── 数据侧 (CLI: python -m data.collect)
    │       └──产出──> sft_conversations.jsonl ──> 筛选 ──> sft_dataset.jsonl ──> SFT 训练
    │
    ├── 算法侧 (CLI: python -m agent.run_agent)
    │       └── import ──> run_episode() 函数
    │                         ↑
    │                         │
    ├── 评测侧 (CLI: python -m eval.run_eval)
    │       ├── import run_episode() ──> 调用算法侧跑 LLM 方法
    │       ├── import baselines ──> 自己跑基线方法
    │       └──产出──> results/metrics.json + results/detail.jsonl
    │
    └── (各侧都通过 WebSocket 连接仿真侧)
```

**共享模块**（多方 import，不是独立服务）：

| 模块 | 路径 | 谁用 |
|---|---|---|
| `serialize_state()` | `src/agent/serialize.py` | 算法侧、数据侧、评测侧（LLM 方法） |
| `protocol` 工具 | `src/agent/protocol.py` | 算法侧、数据侧、评测侧 |
| `run_episode()` | `src/agent/run_agent.py` | 评测侧 import |
| `baselines` | `src/eval/baselines.py` | 评测侧自用 |

## 11. 联调顺序

1. **Group A 先交**：Webots 世界 + 传感器 + WebSocket server，能跑通 `hello -> reset -> obs -> action -> obs` 单 episode
2. **Group B/C 并行**：自写 echo server 桩（收到 action 回伪造 obs），先把 LLM 推理 + 动作解析跑通
3. **数据侧并行**：SOTA LLM + Hermes Agent 连真实仿真，先跑通 5 个 episode 采集对话；确认 sft_conversations.jsonl 格式正确
4. **Group D 并行**：自写随机 client 桩，先把评测框架搭好
5. 联调：hello 校验 -> 单 episode 全流程 -> 多 episode -> 批量评测
6. Group D 接入基线方法

## 12. 变更管理

任何字段、常量、流程的改动：改本文档 -> 升 `protocol_version` 次版本号 -> 群里通知 -> 各组同步改代码。主版本号变更表示不兼容改动，client/server 必须拒绝连接。
