# Burner WebUI — 完整需求文档

> 本文档是开发依据，覆盖所有实现细节，可直接交给 Agent 执行。

---

## 1. 项目概述

在 `/home/damaoooo/playground/burner/UI/` 目录下构建前后端分离的 WebUI，用于从控制机通过 SSH 远程协调多台受控机运行 `burner` 压测任务。

- **后端**：Python + FastAPI，运行在控制机上
- **前端**：React + TypeScript + Vite，运行在控制机上
- **通信**：前后端同机，前端请求 `localhost:8000`；后端通过 SSH 控制远端机器
- **无鉴权**：纯内网实验环境，不需要登录

---

## 2. 目录结构

```
burner/UI/
├── REQUIREMENTS.md             # 本文档
├── machines.json               # 机器配置（用户手动维护）
├── waveforms/                  # 用户保存的自定义波形 CSV（持久化目录）
│
├── backend/
│   ├── main.py                 # FastAPI app 入口，WebSocket hub
│   ├── config.py               # 加载和校验 machines.json
│   ├── ssh_manager.py          # asyncssh 持久连接池 + keepalive
│   ├── machine_info.py         # 远程查询 CPU 型号 / GPU 信息
│   ├── burn_controller.py      # 发起 burn、PID 跟踪、kill、同步策略
│   ├── file_transfer.py        # SCP 波形 CSV 到远端
│   ├── update_controller.py    # submodule-safe reset/pull + build
│   ├── sampling_controller.py  # 采样时间远端同步 + rebuild
│   ├── waveform_store.py       # 列出/读取/保存本地波形 CSV
│   └── requirements.txt
│
└── frontend/
    ├── src/
    │   ├── main.tsx
    │   ├── App.tsx
    │   ├── types/
    │   │   └── index.ts         # 所有共享类型定义
    │   ├── api/
    │   │   └── client.ts        # REST 封装 + WebSocket 封装
    │   └── components/
    │       ├── MachineCard.tsx       # 机器卡片（状态/硬件信息/连接按钮）
    │       ├── WaveformEditor.tsx    # 交互式波形编辑画布
    │       ├── ExpressionInput.tsx   # y=f(x) 表达式 → 采样点
    │       ├── WaveformSelector.tsx  # 下拉选择已有 CSV
    │       ├── BurnPanel.tsx         # 全局 burn 参数 + 每机器选项
    │       ├── GlobalBurnBar.tsx     # 一键启动按钮 + 全局进度条
    │       └── UpdatePanel.tsx       # 每台机器的 git 更新区域
    ├── index.html
    ├── package.json
    ├── tsconfig.json
    └── vite.config.ts
```

---

## 3. machines.json 格式

```json
{
  "machines": [
    {
      "id": "node-1",
      "name": "Node 1 (A100 x2)",
      "host": "192.168.1.100",
      "port": 22,
      "username": "user",
      "identity_file": "~/.ssh/id_rsa",
      "workdir": "/home/user/burner",
      "cpu_tdp": 125,
      "gpu_tdp": 400,
      "conda_env": "ReLL"
    },
    {
      "id": "node-2",
      "name": "Node 2 (H100)",
      "host": "192.168.1.101",
      "port": 22,
      "username": "admin",
      "identity_file": "~/.ssh/id_ed25519",
      "workdir": "/home/admin/burner",
      "cpu_tdp": 280,
      "gpu_tdp": 700,
      "conda_env": "burn"
    }
  ]
}
```

字段说明：
- `id`：唯一标识符，API 路径参数用此值
- `identity_file`：SSH 私钥路径，在**控制机**上，支持 `~` 展开
- `workdir`：burner 仓库在**远端机器**上的路径
- `cpu_tdp`：CPU TDP（W），手动填写，无法自动获取
- `gpu_tdp`：单块 GPU 的 TDP（W），手动填写；若远端 `nvidia-smi` 无法返回 power limit，前端和后端用此值兜底
- `conda_env`：远端 conda 环境名，用于 `conda run -n {conda_env}` 执行命令

---

## 4. 后端实现

### 4.1 依赖（requirements.txt）

```
fastapi
uvicorn[standard]
asyncssh
python-multipart
```

### 4.2 SSH 连接管理（ssh_manager.py）

**连接建立：**
```python
conn = await asyncssh.connect(
    host, port=port, username=username,
    client_keys=[expanded_identity_file],
    known_hosts=None,           # 内网环境不校验 host key
    keepalive_interval=30,      # 每 30 秒发 keepalive
    keepalive_count_max=3       # 3 次无响应则断开
)
```

**连接池：**`dict[machine_id: str, asyncssh.SSHClientConnection | None]`

**状态枚举：**`"disconnected"` | `"connecting"` | `"connected"` | `"error"`

**run_command 接口：**
```python
async def run_command(machine_id: str, cmd: str) -> tuple[str, str, int]:
    # 返回 (stdout, stderr, exit_code)
```

**连接失败处理：** 捕获所有 asyncssh 异常，状态置为 `"error"`，通过 WebSocket 推送 `machine_status` 事件，事件里附带原始错误信息字符串（前端弹窗展示给用户）。

### 4.3 硬件信息查询（machine_info.py）

SSH 连接成功后，执行以下命令：

```bash
# CPU 型号
lscpu | grep "Model name" | awk -F: '{print $2}' | xargs

# GPU 信息（返回多行，每行一块 GPU）
nvidia-smi --query-gpu=name,power.max_limit --format=csv,noheader
```

GPU 示例返回：
```
NVIDIA A100-SXM4-80GB, 400.00 W
NVIDIA A100-SXM4-80GB, 400.00 W
```

解析为：
```json
{
  "cpu_model": "Intel Xeon Gold 6348",
  "cpu_tdp": 125,
  "gpu_tdp": 400,
  "gpus": [
    {"index": 0, "name": "NVIDIA A100-SXM4-80GB", "tdp_watts": 400.0},
    {"index": 1, "name": "NVIDIA A100-SXM4-80GB", "tdp_watts": 400.0}
  ]
}
```

若 nvidia-smi 不存在或返回错误，`gpus` 字段为空数组 `[]`，不报错。

### 4.4 文件传输（file_transfer.py）

使用 asyncssh 的 SCP 功能：

```python
async def scp_to_remote(machine_id: str, local_path: str, remote_path: str):
    conn = ssh_manager.get_connection(machine_id)
    await asyncssh.scp(local_path, (conn, remote_path))
```

Burn 启动前，将所选波形文件 SCP 到 `{workdir}/ui_waveform.csv`，固定覆盖此路径。

### 4.5 Burn 控制器（burn_controller.py）

#### 命令构造

```python
inner_parts = [f"cd {workdir}"]
burner_args = ["./burner"]
if burn_cpu:
    burner_args.append("--cpu")
if burn_gpu:
    burner_args.append("--gpu")
burner_args += ["-f", "./ui_waveform.csv", "-t", duration, "-p", period]
if start_time_utc:  # ISO 格式: "2026-05-12T10:00:05Z"
    burner_args += ["--start", start_time_utc]

# nohup 后台运行，捕获 PID
burner_cmd = " ".join(burner_args)
nohup_cmd = f"nohup {burner_cmd} > /tmp/burner_{machine_id}.log 2>&1 & echo $!"

# 优先使用 <conda root>/envs/<env>/bin 直接启动，降低 realtime 多机器启动偏差；
# 找不到环境路径时再 fallback 到 conda run。
full_cmd = conda_env_path_command(conda_env, nohup_cmd)
```

执行后从 stdout 读取 PID，存入 `job_registry: dict[machine_id, JobInfo]`。

#### JobInfo 结构

```python
@dataclass
class JobInfo:
    machine_id: str
    pid: int
    started_at: float       # time.time()
    duration_seconds: float # 从 -t 参数解析
    burn_cpu: bool
    burn_gpu: bool
```

#### 同步策略

**立即同步模式**（用户未设置任何延时）：

所有机器同时发出 SSH 命令，不加 `--start` 参数：
```python
await asyncio.gather(*[_start_single(m) for m in machines])
```
注：SCP 阶段先串行或并行完成，再统一 gather 执行 burn 命令。

**延时模式**（用户为部分或全部机器设置了延时）：

后端计算一个缓冲基准时间（`now + 5s`），每台机器加上各自的延时：
```python
base_time = datetime.now(UTC) + timedelta(seconds=5)
for m in machines:
    m.start_time = base_time + timedelta(seconds=m.delay_seconds)
# 仍然同时发出所有命令，由各机器本地的 burner --start 等待
await asyncio.gather(*[_start_single(m) for m in machines])
```
远端 burner 的 `_wait_until()` 每次 sleep 最多 1 秒轮询，精度够用。

#### 停止命令

```python
async def stop_burn(machine_id: str):
    job = job_registry.get(machine_id)
    if not job:
        return
    await ssh_manager.run_command(
        machine_id,
        f"kill {job.pid} 2>/dev/null || true"
    )
    del job_registry[machine_id]
```

#### WebSocket 断开后的 grace period

- 后端维护全局计数器 `active_ws_count: int`
- 当计数归零时，启动 60 秒异步定时器
- 60 秒内有新 WebSocket 连接：取消定时器，向新客户端推送所有 job 的当前状态
- 60 秒超时：对所有 `job_registry` 里的机器执行 `stop_burn()`

### 4.6 更新控制器（update_controller.py）

```python
async def run_update(machine_id: str, has_gpu: bool):
    workdir = config.get_machine(machine_id).workdir
    conda_env = config.get_machine(machine_id).conda_env

    commands = [
        f"cd {workdir}",
        "git reset --hard HEAD",
        "git clean -fd",
        "git submodule foreach --recursive 'git reset --hard HEAD && git clean -fd'",
        "git pull --recurse-submodules",
        "git submodule sync --recursive",
        "git submodule update --init --recursive --force",
        "bash scripts/build_lookbusy.sh",
    ]
    if has_gpu:
        commands.append("bash scripts/build_gpu_burn.sh")

    full_cmd = " && ".join(commands)

    # 逐行流式输出，通过 WebSocket 推送 update_log 事件
    async with ssh_manager.get_connection(machine_id).create_process(
        f"conda run -n {conda_env} bash -c '{full_cmd}'"
    ) as process:
        async for line in process.stdout:
            await ws_hub.broadcast({
                "event": "update_log",
                "id": machine_id,
                "line": line.rstrip()
            })
    exit_code = process.exit_status
    await ws_hub.broadcast({
        "event": "update_done",
        "id": machine_id,
        "exit_code": exit_code
    })
```

**互锁**：`update_controller.run_update()` 在调用前检查 `job_registry`，若该机器有 job 则抛出异常，API 返回 409 Conflict，前端在机器 burn 期间直接将更新按钮置灰（前端状态判断优先，后端做兜底）。

### 4.7 波形存储（waveform_store.py）

- 读取来源 1：`../tests/fixtures/*.csv`（相对于 `UI/backend/` 的路径）
- 读取来源 2：`../waveforms/*.csv`

`list_waveforms()` 返回两个来源合并的列表，标注来源（fixtures / custom）。

保存时写入 `../waveforms/{name}.csv`，格式：无表头，两列 `x,y`，值均为 float，x 严格递增，范围 [0,1]。

---

## 5. REST API

### 机器管理

```
GET  /api/machines
```
返回所有机器列表，包含连接状态和 hardware info（若已查询过）。

```
POST /api/machines/{id}/connect
```
建立 SSH 连接，连接成功后自动查询 hwinfo，通过 WebSocket 推送结果。
响应：`{"status": "connecting"}`

```
POST /api/machines/{id}/disconnect
```
断开 SSH 连接，若有 burn job 运行则先 kill。

```
GET  /api/machines/{id}/hwinfo
```
（Re）查询 CPU/GPU 信息，返回结果并通过 WebSocket 广播。

---

### 波形管理

```
GET  /api/waveforms
```
返回所有可用波形：
```json
[
  {"name": "sine", "source": "fixtures", "points": [[0.0, 0.5], ...]},
  {"name": "plate", "source": "fixtures", "points": [[0.0, 1.0], ...]},
  {"name": "my_custom", "source": "custom", "points": [...]}
]
```

```
GET  /api/waveforms/{name}
```
返回单个波形的完整点数组。

```
POST /api/waveforms
```
请求体：
```json
{"name": "my_wave", "points": [[0.0, 0.5], [0.25, 1.0], [0.75, 0.0], [1.0, 0.5]]}
```
校验：points 至少 2 个，x 严格单调递增，x/y 范围 [0,1]。
保存到 `UI/waveforms/{name}.csv`。

---

### Burn 控制

```
POST /api/burn/start
```
请求体：
```json
{
  "sync_mode": "scheduled",
  "start_time_utc": "2026-05-13T10:00:00Z",
  "duration": "60s",
  "period": "10s",
  "tick_seconds": 0.1,
  "machines": [
    {
      "id": "node-1",
      "enabled": true,
      "burn_cpu": true,
      "burn_gpu": true,
      "delay_seconds": 0,
      "waveform_name": "sine"
    },
    {
      "id": "node-2",
      "enabled": true,
      "burn_cpu": false,
      "burn_gpu": true,
      "delay_seconds": 5.0,
      "waveform_name": "plate"
    }
  ]
}
```
- `sync_mode`：`"immediate"` | `"delayed"` | `"scheduled"`
- `start_time_utc`：仅 `sync_mode=scheduled` 时必填，UTC ISO 格式，例如 `"2026-05-13T10:00:00Z"`
- `duration`：格式同 burner `-t`，如 `"60s"`、`"2m"`
- `period`：格式同 burner `-p`，如 `"10s"`、`"1.5s"`
- `tick_seconds`：burner 调度 tick，前端使用已应用采样时间换算出的秒值
- `delay_seconds`：支持小数秒；`sync_mode=delayed` 或 `sync_mode=scheduled` 时生效，后端生成毫秒级 `--start` 时间
- `waveform_name`：各机器可独立指定；若前端未开启独立波形模式，所有机器填同一个名字

流程：
1. 校验所有目标机器均已连接
2. 并行 SCP 各机器对应的波形文件
3. 按同步策略构造命令并 `asyncio.gather()` 发出
4. 收集各机器 PID，写入 job_registry
5. 通过 WebSocket 广播 `burn_started` 事件

```
POST /api/burn/stop
```
请求体：`{"machine_ids": ["node-1", "node-2"]}`、`{"machine_ids": "all"}`、`{"job_ids": ["node-1-abc123"]}` 或 `{"job_ids": "all"}`
对匹配任务执行 `kill PID`，清理 job_registry，广播 `burn_stopped` 事件。前端 Schedule Manager 使用 `job_ids` 删除单个预约。

同一机器上的任务不能重叠。重叠判定包含任务结束后的 5 秒 grace window：新任务开始时间必须晚于已有任务结束时间 5 秒之后；反向也一样，已有预约开始前 5 秒内不能插入新任务。

```
GET  /api/burn/status
```
返回当前所有 job 状态：
```json
[
  {
    "machine_id": "node-1",
    "pid": 12345,
    "started_at": 1715500000.0,
    "duration_seconds": 60.0,
    "elapsed_seconds": 23.4
  }
]
```

---

### 更新

```
POST /api/update/{id}
```
若该机器正在 burn，返回 `409 Conflict: {"detail": "Machine is currently burning"}`。
否则在远端执行 submodule-safe reset/pull/update + build，输出通过 WebSocket 流式推送。该流程会清理远端仓库和 submodule 中未提交的本地改动，避免 patched third-party 源码阻塞 checkout。

### 采样时间应用

```
POST /api/sampling/apply
```

请求体：
```json
{"sampling_ms": 100, "machine_ids": ["node-1", "node-2"]}
```

- `sampling_ms`：整数，范围 `10` 到 `1000`，单位毫秒。
- `machine_ids`：目标机器；前端传所有 connected 机器。
- 若 sampling rebuild 正在运行，burn/update 请求返回 409。
- 每台远端机器执行顺序固定为：主仓库 `git reset --hard HEAD` + `git clean -fd`、所有 submodule `git reset --hard HEAD` + `git clean -fd`、`git pull --recurse-submodules`、`git submodule sync --recursive`、`git submodule update --init --recursive --force`、SCP 本地 patched 源码/构建脚本覆盖远端、`BURNER_CONTROL_INTERVAL_MS=<value> bash scripts/build_lookbusy.sh`、有 GPU 时执行 `BURNER_CONTROL_INTERVAL_MS=<value> bash scripts/build_gpu_burn.sh`。

---

## 6. WebSocket

路径：`ws://localhost:8000/ws`

前端连接后，后端立即推送所有机器的当前状态（连接状态 + job 状态）。

### 后端 → 前端事件

```jsonc
// 机器连接状态变化
{"event": "machine_status", "id": "node-1", "status": "connected"}
{"event": "machine_status", "id": "node-1", "status": "disconnected"}
{"event": "machine_status", "id": "node-1", "status": "error", "message": "Connection refused"}

// 硬件信息（连接成功后自动推送）
{
  "event": "hw_info",
  "id": "node-1",
  "cpu_model": "Intel Xeon Gold 6348",
  "cpu_tdp": 125,
  "gpu_tdp": 400,
  "gpus": [{"index": 0, "name": "A100", "tdp_watts": 400.0}]
}

// Burn 事件
{"event": "burn_started", "id": "node-1", "pid": 12345, "duration_seconds": 60.0}
{"event": "burn_stopped", "id": "node-1", "exit_code": 0}

// 新版事件会附带 job_id；前端用 job_id 管理多个预约
{"event": "burn_started", "job_id": "node-1-abc123", "id": "node-1", "pid": 12345, "started_at": 1715500000.0, "duration_seconds": 60.0}
{"event": "burn_stopped", "job_id": "node-1-abc123", "id": "node-1", "exit_code": 0}

// 更新日志（流式，每行一条）
{"event": "update_log", "id": "node-1", "line": "Already up to date."}
{"event": "update_done", "id": "node-1", "exit_code": 0}

// 采样时间远端重编译
{"event": "sampling_build_log", "id": "node-1", "line": "[pull] git pull --recurse-submodules"}
{"event": "sampling_build_progress", "id": "node-1", "sampling_ms": 100, "step": "build_cpu", "status": "running", "completed": 3, "total": 5, "progress": 0.6}
{"event": "sampling_build_done", "id": "node-1", "sampling_ms": 100, "exit_code": 0, "status": "success"}
{"event": "sampling_build_complete", "sampling_ms": 100, "exit_code": 0}
```

---

## 7. 前端实现

### 7.1 依赖

```json
{
  "dependencies": {
    "react": "^18",
    "react-dom": "^18",
    "chart.js": "^4",
    "react-chartjs-2": "^5",
    "chartjs-plugin-dragdata": "^2",
    "mathjs": "^13",
    "axios": "^1"
  },
  "devDependencies": {
    "typescript": "^5",
    "vite": "^5",
    "@vitejs/plugin-react": "^4",
    "@types/react": "^18",
    "@types/react-dom": "^18"
  }
}
```

### 7.2 MachineCard.tsx

每台机器一张卡片，显示：
- 机器名称（`name` 字段）+ IP
- 连接状态指示灯：绿（connected）/ 灰（disconnected）/ 红（error）/ 黄（connecting）
- 连接 / 断开按钮（根据状态显示）
- 连接失败时弹出 Modal 显示原始错误信息
- 硬件信息区（连接后填充）：
  - CPU 型号 + TDP（W）
  - 每块 GPU：型号 + TDP（W）
  - 若 GPU 列表为空，显示"No GPU detected"
- 更新按钮：机器正在 burn 时置灰，点击展开 UpdatePanel

### 7.3 WaveformEditor.tsx

**图表库**：Chart.js + react-chartjs-2 + chartjs-plugin-dragdata

**功能：**
- 折线图，x 轴 [0, 1]，y 轴 [0, 1]，散点可见
- **拖动点**：chartjs-plugin-dragdata 实现，拖动时实时更新坐标，x 坐标拖动时不允许越过相邻点（保持严格单调递增）
- **新增点**：点击图表空白区域，按点击的 x 坐标插入到正确位置（保持排序）
- **删除点**：右键已有数据点，弹出小菜单"删除此点"；第一个点和最后一个点不可删除（x=0 和 x=1 必须存在）
- **导出 CSV**：点击"保存为 CSV"，弹出输入框输入波形名，调用 `POST /api/waveforms`
- 外部可通过 props 传入初始点数组（来自选择器或表达式）

### 7.4 ExpressionInput.tsx

**输入框**：用户输入数学表达式，如 `sin(2*pi*x)*0.5+0.5` 或 `sin(2x)` 或 `0.5`

**语法预处理**（前端正则，在 mathjs 解析前）：
- `2x` → `2*x`（数字紧跟字母）
- `sin2x` → `sin(2*x)`（函数名后直接跟数字字母时加括号，简单处理）
- `pi` → `pi`（mathjs 内建常量，直接支持）

**采样**：`x` 从 0 到 1 均匀取 64 个点，用 mathjs `evaluate(expr, {x, pi: Math.PI})` 计算每个 y

**校验**：
- 计算异常（语法错误、除零等）：输入框边框变红，显示错误信息
- y 值超出 [0,1]：显示黄色警告"部分值超出范围，将被 burner 截断到 [0,1]"，仍允许使用
- 计算成功：将 64 个点传给 WaveformEditor 替换当前点集

**实时预览**：用户停止输入 500ms 后自动触发计算（debounce）

### 7.5 WaveformSelector.tsx

下拉列表，选项来自 `GET /api/waveforms`，分两组：
- Fixtures（sine, plate 等）
- Custom（用户保存的）

选中后，调用 `GET /api/waveforms/{name}` 获取点数组，传给 WaveformEditor。

### 7.6 BurnPanel.tsx

**全局参数区（所有机器共用）：**
- 波形区：WaveformSelector + WaveformEditor + ExpressionInput 三合一
- Duration 输入框：显示单位为秒，标签为 `Duration (s)`，默认 `16`；前端提交时转换为 burner `-t` 所需的 `16s`
- Period 输入框：显示单位为秒，标签为 `Period (s)`，默认 `1`；前端提交时转换为 burner `-p` 所需的 `1s`，支持小数秒
- 顶部启动区域提供 Realtime / Schedule 两个主模式；Realtime 为默认模式，Schedule 模式显示时间选择器，前端选择本地时间并转换成 UTC `start_time_utc` 提交给后端
- `Sampling Time (ms)`：整数范围 `10` 到 `1000`，默认 `100`；点击 Apply 后对所有 connected 机器执行远端 reset/pull/scp/build，完成前禁用 burn/update 操作
- **独立波形开关**：关闭时所有机器用全局波形；开启时每张 MachineCard 下方展示独立的 WaveformSelector

**每台机器参数区（在 MachineCard 内）：**
- 勾选框：Burn CPU / Burn GPU（默认两者都勾）
- `Delay (s)` 输入框：支持小数秒，默认 `0`；Realtime 和 Schedule 模式均可配置。Realtime 下所有 delay 为 0 时提交 `immediate`，任一 delay 大于 0 时提交 `delayed`；Schedule 下提交 `scheduled`
- 独立波形开关打开时：显示该机器的 WaveformSelector

**机器启用开关：** 每台机器可整体勾选/取消，取消的机器不参与 burn。

### 7.7 GlobalBurnBar.tsx

**启动按钮：**
- 默认可点击，文字"一键 Burn"
- 点击后：
  1. 检查至少一台机器已勾选且已连接
  2. 调用 `POST /api/burn/start`
  3. 按钮置灰，文字变"正在 Burn..."
- 任意机器 burn 结束后，按钮恢复可点击
- Scheduled 模式下先弹出确认窗口，展示每台机器的计划开始/结束时间，用户点击确认后才提交
- 提交成功后弹出窗口展示已创建的 schedule 列表
- Immediate / Delayed / Scheduled 提交前都需要检查同机任务窗口是否重叠；若重叠，弹窗阻止提交

**进度条：**
- Burn 开始时记录 `startTime = Date.now()`，从 `-t` 参数解析 `totalMs`
- 每 100ms 更新一次：`progress = Math.min((Date.now() - startTime) / totalMs, 1.0)`
- 多台机器并行时，进度条显示最长的那台（即总 duration 相同则同步，有延时则取最大值）
- 收到 `burn_stopped` WebSocket 事件后进度条消失

**停止按钮：**
- Burn 期间显示"停止"按钮（红色）
- 点击后调用 `POST /api/burn/stop`（`machine_ids: "all"`）

### 7.8 SchedulePanel.tsx

显示所有尚未开始的 schedule：
- 机器名
- 开始时间
- 结束时间
- 波形名
- job id 短码
- Cancel 按钮

Cancel 调用 `POST /api/burn/stop` 并传 `job_ids` 删除对应 schedule。

### 7.9 UpdatePanel.tsx

每台机器卡片内折叠区域，展开后显示：
- "检查更新"按钮：执行 `POST /api/update/{id}`
- 日志输出区：滚动文本框，实时显示 WebSocket `update_log` 事件内容
- 完成后显示"成功"或"失败（exit code X）"

---

## 8. 全局状态管理

前端使用 React Context + useReducer 维护全局状态，无需引入 Redux：

```typescript
interface AppState {
  machines: Record<string, MachineState>;
  waveforms: WaveformInfo[];
  globalWaveform: Point[];          // 全局波形点集
  perMachineWaveforms: Record<string, Point[]>;  // 独立波形模式下各机器波形
  usePerMachineWaveform: boolean;   // 独立波形开关
  burnJobs: Record<string, JobInfo | null>;
  updateLogs: Record<string, string[]>;
}

interface MachineState {
  config: MachineConfig;            // 来自 machines.json
  connectionStatus: "disconnected" | "connecting" | "connected" | "error";
  errorMessage?: string;
  hwInfo?: HwInfo;
  burnEnabled: boolean;             // 此机器是否参与本次 burn
  burnCpu: boolean;
  burnGpu: boolean;
  delaySeconds: number;
}
```

WebSocket 收到事件后，dispatch 对应的 action 更新状态。

---

## 9. 错误处理规范

| 场景 | 前端处理 |
|------|---------|
| SSH 连接失败 | 机器卡片状态变红，弹出 Modal 显示错误详情 |
| SCP 失败 | Toast 提示"波形传输失败：{machine_id}"，取消本次 burn |
| Burn 命令执行失败（exit_code != 0）| Toast 提示"启动失败" + 错误日志（可展开） |
| 表达式解析错误 | 输入框红色边框 + 内联错误文字，不 block 其他操作 |
| 波形保存名冲突 | 后端返回 409，前端提示"已存在同名波形，是否覆盖？" |
| 更新时正在 burn | 后端返回 409，前端在机器 burn 期间直接置灰更新按钮（前端优先防止） |
| WebSocket 断开 | 顶部横幅提示"与服务器连接中断，正在重连..."，自动重连 |

---

## 10. 启动方式

**后端（控制机）：**
```bash
cd UI/backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

**前端（控制机）：**
```bash
cd UI/frontend
npm install
npm run dev   # 默认 http://localhost:5173
```

前端 `vite.config.ts` 需配置代理，将 `/api` 和 `/ws` 请求转发到 `localhost:8000`：
```typescript
server: {
  proxy: {
    '/api': 'http://localhost:8000',
    '/ws': { target: 'ws://localhost:8000', ws: true }
  }
}
```

---

## 11. 验证步骤

1. **`--start` 同步精度**：在本机启动两个 burner mock 实例，设置同一个未来 UTC 时间，测量实际启动时间差应 < 1 秒。

2. **SSH + HwInfo**：连接一台真实机器，验证 MachineCard 正确显示 CPU 型号、TDP、GPU 列表。

3. **波形 SCP + Burn**：选择 `plate.csv`，设 `-t 10s -p 1s`，对一台机器发起 burn，验证：
   - 远端 `{workdir}/ui_waveform.csv` 内容正确
   - 远端进程以 nohup 运行
   - 进度条 10 秒后自动消失

4. **多机同步**：两台机器同时 burn，用 watcher 分别采集功率曲线，对比启动时间戳对齐误差。

5. **延时模式**：机器 A 延时 0s，机器 B 延时 5s，验证功率曲线中 B 比 A 滞后 5 秒启动。

6. **表达式波形**：输入 `sin(2*pi*x)*0.5+0.5`，验证图形正确，保存为 CSV，导入后 burner 可正常读取（格式合规）。

7. **停止 + grace period**：burn 开始后关闭浏览器标签，等待 60 秒超时，验证远端进程已被 kill。

8. **更新流程**：点更新按钮，验证 git pull + build 日志流式显示，编译完成后按钮恢复。

9. **独立波形模式**：开启开关，为两台机器分别设置不同波形，验证 SCP 后各机器 `ui_waveform.csv` 内容不同。
