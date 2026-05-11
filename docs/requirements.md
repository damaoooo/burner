# Burner / Watcher 需求文档

## 1. 项目目标

`burner` 是一个用于按功率曲线控制 CPU / GPU 负载的工具集，目标是模拟可控、周期性的功耗变化，并提供实时功率观测能力。

项目包含两个核心命令：

- `burner`：读取 CSV 曲线，在指定总时长内周期性控制 CPU / GPU burn 强度。
- `watcher`：实时采集 CPU / GPU 功率，在 terminal TUI 中绘图，并保存 CSV。

开发必须遵循 TDD：

1. 先新增测试和 mock 数据。
2. 再实现功能。
3. 最后重构、补文档、补构建脚本。

## 2. 总体约束

- 主控逻辑使用 Python 实现。
- 根目录提供可执行入口：`./burner` 和 `./watcher`。
- Python 业务代码放在当前已有的 `warpper/` 目录下；第一版不重命名该目录。
- C / C++ 第三方工具使用 `gcc` / `g++` / 原项目 Makefile 编译。
- 允许修改 `third_party/lookbusy` 和 `third_party/gpu-burn`，但必须在文档中说明原因和影响范围。
- 自动化测试不得执行长时间真实烤机。
- 不假设所有机器都有 GPU。
- 无 GPU、无 CUDA、无 `nvidia-smi`、无 RAPL 时，工具必须给出清晰状态或错误。
- 不提交 Conda 本地路径、`.env`、token、私钥或机器相关配置。

## 3. `burner` 功能需求

### 3.1 CLI

基本用法：

```bash
./burner [--cpu] [--gpu] -f <curve.csv> -t <duration> -p <period> [-s <start_time>]
```

参数：

| 参数 | 必填 | 含义 |
| --- | --- | --- |
| `--cpu` | 否 | 启用 CPU burn |
| `--gpu` | 否 | 启用 GPU burn |
| `-f`, `--file <curve.csv>` | 是 | 输入曲线 CSV |
| `-t`, `--time <duration>` | 是 | 总运行时长 |
| `-p`, `--period <duration>` | 是 | 一个曲线周期对应的实际时间 |
| `-s`, `--start <start_time>` | 否 | UTC 启动时间 |

校验规则：

- `--cpu` 和 `--gpu` 至少指定一个。
- `-f` 文件必须存在且格式合法。
- `-t` 必须大于 `0`。
- `-p` 必须大于 `0`。
- `-s` 必须是 UTC ISO 时间，格式示例：`2026-05-10T12:00:00Z`。
- 如果 `-s` 是未来时间，程序等待到该时间后开始 burn。
- 如果 `-s` 是过去时间，程序立即开始。

时长格式：

- `-t` 支持整数加 `s`、`m`、`h` 后缀，例如 `20s`、`30m`、`1h`。
- `-p` 支持正数小数加 `s`、`m`、`h` 后缀，例如 `0.5s`、`1.25m`。
- 不支持无单位、负数、零值、组合格式如 `1h30m`。

### 3.2 曲线 CSV 格式

`burner` 的 `-f` / `--file` 输入文件为无表头 CSV，固定两列：

```csv
x,y
```

实际文件不包含表头，例如：

```csv
0.0,0.5
0.25,1.0
0.5,0.5
0.75,0.0
1.0,0.5
```

字段含义：

- `x`：归一化周期位置，范围必须为 `[0, 1]`。
- `y`：burn 强度，运行时 clamp 到 `[0, 1]`。

解析规则：

- 文件必须至少包含 2 个点。
- 每行必须恰好 2 列。
- `x` 必须是数字，且必须单调递增。
- `x` 不允许小于 `0` 或大于 `1`。
- `y` 必须是数字。
- `y < 0` 按 `0` 处理。
- `y > 1` 按 `1` 处理。
- 空行忽略。
- 非法行报错并退出。

插值规则：

- 曲线点之间使用线性插值。
- 周期内位置按 `elapsed % period / period` 计算。
- 当周期位置正好等于已有点时，直接使用该点强度。
- 当周期 wrap 回起点时，从 `x=1` 回到 `x=0` 开始下一个周期。
- 如果 CSV 没有显式包含 `x=0` 或 `x=1`，实现必须仍然可插值，但文档和测试推荐样例包含两端点。

### 3.3 调度规则

- 默认调度 tick 为 `0.1s`。
- 每个 tick 根据当前 elapsed time 计算目标强度。
- 强度 `0` 表示不 burn。
- 强度 `1` 表示 100% burn。
- 总运行时长达到 `-t` 后，停止所有底层 burn 进程并退出。
- 收到 `SIGINT` / `SIGTERM` 时，必须停止所有子进程并退出。

### 3.4 CPU burn

CPU burn 从 `third_party/lookbusy` 实现。

第一版目标：

- `burner --cpu` 能按 CSV 曲线动态调整 CPU burn 百分比。
- CPU backend 支持 mock，用于自动化测试。
- 真实运行时启动 patched `lookbusy`。
- 退出时清理 `lookbusy` 子进程。

允许修改 `lookbusy`：

- 增加外部控制当前 CPU utilization 的机制。
- 保持原有 lookbusy CLI 基本兼容。
- 修改必须记录在 `docs/third_party_changes.md`。

### 3.5 GPU burn

GPU burn 基于 patched `third_party/gpu-burn`。

GPU 控制采用 `--burn-util-file` 内部 throttle：

- `burner` 将当前目标强度写入共享控制文件。
- `gpu_burn` 的 CUDA work loop 读取该文件并调节 kernel 提交节奏。
- 强度 `0` 时不提交 GPU work。
- 强度 `1` 时持续提交 GPU work。
- 当机器存在多个 CUDA GPU 时，默认控制所有 GPU；所有 GPU worker 读取同一个目标强度文件。
- 多 GPU 场景下，强度 `1` 表示每块被检测到的 GPU 都执行 100% burn，强度 `0.5` 表示每块 GPU 都按同一目标执行约 50% burn。

错误处理：

- 找不到 `gpu_burn` 二进制时报错。
- 无 CUDA / 无 GPU / 启动失败时报错。
- 错误信息必须说明缺失项和建议操作。

自动化测试只验证 mock backend 和错误路径，不要求真实 GPU。

## 4. `watcher` 功能需求

### 4.1 CLI

基本用法：

```bash
./watcher -n <interval> -f <output.csv> [--mock]
```

参数：

| 参数 | 必填 | 含义 |
| --- | --- | --- |
| `-n <interval>` | 是 | 采样间隔，单位秒，支持小数 |
| `-f <output.csv>` | 是 | 输出 CSV 文件 |
| `--mock` | 否 | 使用 mock 功率数据 |

校验规则：

- `-n` 必须是大于 `0` 的数字。
- `-f` 的父目录必须存在。
- 输出文件不存在则创建。
- 输出文件存在时覆盖写入，并写入新的 CSV 表头。

### 4.2 CSV 输出格式

固定表头：

```csv
timestamp,cpu_watts,gpu_watts
```

字段说明：

- `timestamp`：UTC ISO 时间。
- `cpu_watts`：CPU 功率，单位 W。
- `gpu_watts`：GPU 功率，单位 W。

缺失数据规则：

- 如果 CPU 数据不可用，`cpu_watts` 留空。
- 如果 GPU 数据不可用，`gpu_watts` 留空。
- 不用 mock 值冒充真实硬件数据。

### 4.3 TUI

`watcher` 使用 Rich TUI。

TUI 必须展示：

- 当前 CPU 功率。
- 当前 GPU 功率。
- 最近一段时间的 CPU/GPU 曲线。
- 数据源状态，例如 RAPL missing、`nvidia-smi` missing、mock mode。
- 输出 CSV 路径。

行为：

- 每 `-n` 秒采样一次并刷新 TUI。
- 同步追加一行 CSV。
- 收到 `SIGINT` / `SIGTERM` 时停止采样并关闭文件。

### 4.4 真实采样源

CPU：

- 优先使用 Linux RAPL `/sys/class/powercap`。
- 通过 energy counter 差分计算功率。
- 如果 RAPL 不可用，CPU 功率为空，并展示清晰状态。

GPU：

- 优先调用 `nvidia-smi` 获取功率。
- 如果 `nvidia-smi` 不存在、无 GPU 或命令失败，GPU 功率为空，并展示清晰状态。

Mock：

- `--mock` 下不访问真实硬件。
- 生成 CPU/GPU 两路周期性 sine/mock 功率数据。
- mock 模式用于测试、演示和无硬件环境。

## 5. 构建与脚本需求

新增脚本：

- `scripts/build_lookbusy.sh`
- `scripts/build_gpu_burn.sh`
- `scripts/test.sh`

要求：

- shell 脚本使用明确错误处理。
- `scripts/test.sh` 作为统一测试入口。
- 优先运行 `pytest tests/`。
- GPU 构建脚本在缺少 CUDA / `nvcc` 时必须清晰失败。
- lookbusy 构建脚本使用其现有 `configure` / `make` 流程。

## 6. 测试需求

测试位于 `tests/`。

必须先写测试，再实现功能。

### 6.1 测试夹具

新增至少以下 fixture：

- `tests/fixtures/sine.csv`
- 非法 CSV 样例。
- mock RAPL energy 文件数据。
- fake `nvidia-smi` 输出。

### 6.2 `burner` 测试

曲线解析：

- 正常两列 `x,y`。
- 空行忽略。
- `y < 0` clamp 到 `0`。
- `y > 1` clamp 到 `1`。
- 空文件报错。
- 非法列数报错。
- 非法数字报错。
- `x < 0` 或 `x > 1` 报错。
- `x` 非递增报错。

插值：

- 边界 `x=0`。
- 边界 `x=1`。
- 中间点线性插值。
- 周期 wrap。
- sine 样例近似值。

CLI：

- 缺少 `--cpu` / `--gpu` 报错。
- 缺少 `-f` 报错。
- 缺少 `-t` 报错。
- 缺少 `-p` 报错。
- 非法 duration 报错。
- mock backend 下调度序列符合曲线。

Backend：

- CPU mock backend 能接收动态百分比。
- GPU mock backend 的占空比启停符合目标强度。
- 退出时清理子进程。
- 缺少真实二进制时错误信息清晰。

### 6.3 `watcher` 测试

- mock 模式生成 CSV 表头。
- mock 模式生成多行数据。
- fake RAPL 数据解析正确。
- fake `nvidia-smi` 输出解析正确。
- 缺少 RAPL 时 CPU 字段为空。
- 缺少 `nvidia-smi` 时 GPU 字段为空。
- Rich TUI 短时 mock run 不阻塞测试。

自动测试不得依赖真实 GPU。

## 7. 验收标准

必须满足：

- `bash scripts/test.sh` 全部通过。
- `./burner --cpu -f tests/fixtures/sine.csv -t 5s -p 2s` 能启动 CPU burn，并按曲线变化。
- `./burner --gpu -f tests/fixtures/sine.csv -t 5s -p 2s` 在有 GPU 环境下能执行占空比 GPU burn。
- 无 GPU 环境运行 GPU burn 时，给出清晰错误。
- `./watcher --mock -n 0.1 -f /tmp/power.csv` 能显示 Rich TUI 并写入 CSV。
- 文档覆盖安装、构建、CLI 参数、CSV 格式、mock 用法、第三方修改说明。

## 8. 非目标

第一版不要求：

- 深改 `gpu-burn` CUDA kernel 实现连续限功率。
- 在自动化测试中执行真实 GPU burn。
- 支持复杂 duration 表达式，如 `1h30m`。
- 支持带表头或多列曲线 CSV。
- 重命名 `warpper/` 为 `wrapper/`。
