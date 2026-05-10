# AGENTS.md

## 项目概述

`burner` 是一个用于控制 CPU 和 GPU 功率负载的工具。它可以根据用户提供的功率曲线，在指定时间内按照周期性波形对 CPU / GPU 进行 burn，从而模拟可控的功耗变化。

本项目主要包含两个核心工具：

- `burner`：按照指定波形控制 CPU / GPU 的 burn 百分比。
- `watcher`：实时采集 CPU / GPU 功率数据，并以 CSV 格式记录。

## 项目结构

```text
.
├── scripts/       # Bash 脚本，用于安装、部署和命令行交互
├── docs/          # 开发文档与维护文档
├── third_party/   # 第三方源代码，包括 lookbusy 和 gpu_burn
├── tests/         # 单元测试与功能测试
└── wrapper/       # Python 封装脚本，用于提升 burner 易用性
````

> 注意：目录名应为 `wrapper`。如果当前仓库中实际目录名是 `warpper`，请保持现状，除非明确要求重命名。

## 开发指南

* C / C++ 代码使用 `gcc` / `g++` 编译。
* Python 脚本使用 Conda 环境 `ReLL` 运行。
* 开发流程遵循 TDD 原则：

  * 优先编写测试；
  * 再实现功能；
  * 最后进行重构与清理。
* 修改核心 burn 逻辑时，必须补充或更新对应测试。
* 修改命令行参数行为时，必须同步更新文档和相关测试。

## 第三方依赖

本项目依赖以下第三方工具源码：

* `lookbusy`：用于 CPU burn。
* `gpu_burn`：用于 GPU burn。

第三方源码位于：

```text
third_party/
```

除非任务明确要求，不要随意修改 `third_party/` 下的源代码。若必须修改，应在文档中说明修改原因和影响范围。

## 构建与运行环境

### C / C++

使用 `gcc` / `g++` 进行编译。

示例：

```bash
gcc ...
g++ ...
```

具体构建命令请优先参考 `scripts/` 或 `docs/` 中已有说明。

### Python

Python 相关脚本应在 Conda 环境 `ReLL` 中运行。

示例：

```bash
conda activate ReLL
python wrapper/xxx.py
```

## 测试要求

本项目包含单元测试和功能测试，位于：

```text
tests/
```

开发时应遵循以下规则：

* 新增功能前，优先新增对应测试。
* 修复 bug 时，优先添加回归测试。
* 修改 `burner` 或 `watcher` 的命令行参数时，需要更新对应功能测试。
* 提交前应尽可能运行相关测试。

如仓库中存在统一测试入口，请优先使用，例如：

```bash
bash scripts/test.sh
```

或：

```bash
pytest tests/
```

## 命令行工具说明

## `burner`

`burner` 用于根据给定功率曲线，对 CPU 和 / 或 GPU 执行周期性 burn。

### 基本用法

```bash
./burner [--cpu] [--gpu] -f <file_name> -t <duration> -p <period> [-s <start_time>]
```

### 参数说明

| 参数                           | 含义                              |
| ---------------------------- | ------------------------------- |
| `--cpu`                      | 使用 CPU 进行 burn                  |
| `--gpu`                      | 使用 GPU 进行 burn                  |
| `-f`, `--file <file_name>`   | 指定 CSV 波形文件                     |
| `-t`, `--time <duration>`    | 指定总 burn 时长，例如 `1h`、`30m`、`20s` |
| `-p`, `--period <duration>`  | 指定一个波形周期对应的时间长度                 |
| `-s`, `--start <start_time>` | 指定 UTC+0 时间下的启动时间               |

### 波形文件格式

`-f` / `--file` 指定的文件必须是 CSV 文件。

CSV 文件用于描述一个完整周期内的功率波形，要求如下：

* 波形横轴表示归一化后的周期位置，范围为 `0` 到 `1`。
* 波形纵轴表示 burn 强度，最小值为 `0`，最大值为 `1`。
* 如果 burn 强度超过 `1`，程序应将其 cap 为 `1`。
* 如果 burn 强度小于 `0`，应根据项目约定处理；若没有明确约定，建议 cap 为 `0`。
* `1` 表示 100% burn，`0` 表示不进行 burn。

### 行为说明

当指定 `--cpu` 时，`burner` 应按照输入波形控制 CPU burn 强度。

当指定 `--gpu` 时，`burner` 应按照输入波形控制 GPU burn 强度。

当同时指定 `--cpu` 和 `--gpu` 时，`burner` 应同时控制 CPU 和 GPU 的 burn 强度。

`-p` / `--period` 用于指定一个完整波形周期持续多久。程序应在 `-t` / `--time` 指定的总运行时间内，重复执行该周期波形。

### 示例

使用 CPU burn，运行 30 分钟，每个周期 60 秒：

```bash
./burner --cpu -f curve.csv -t 30m -p 60s
```

同时使用 CPU 和 GPU burn，运行 1 小时，每个周期 5 分钟：

```bash
./burner --cpu --gpu -f curve.csv -t 1h -p 5m
```

指定 UTC+0 启动时间：

```bash
./burner --cpu --gpu -f curve.csv -t 1h -p 5m -s "2026-05-10T12:00:00Z"
```

## `watcher`

`watcher` 用于检测 CPU 和 GPU 的实时功率曲线，并将采样结果写入 CSV 文件。

### 基本用法

```bash
./watcher -n <interval> -f <file_name>
```

### 参数说明

| 参数               | 含义         |
| ---------------- | ---------- |
| `-n <interval>`  | 采样间隔，单位为秒  |
| `-f <file_name>` | 输出 CSV 文件名 |

### 行为说明

`watcher` 应每隔 `-n` 指定的时间采集一次 CPU / GPU 实时功率数据，并将结果以 CSV 格式追加或写入到 `-f` 指定的文件中。

### 示例

每隔 `0.1s` 采集一次功率数据，并写入 `power.csv`：

```bash
./watcher -n 0.1 -f power.csv
```

## 代码修改原则

* 保持改动小而明确。
* 不要进行无关重构。
* 不要随意修改第三方源码。
* 修改命令行参数时，需要同步更新：

  * 相关测试；
  * 开发文档；
  * 使用说明。
* 新增 Python wrapper 时，应确保命令行行为与底层 `burner` / `watcher` 保持一致。
* 新增 shell script 时，应保证脚本具有清晰的错误处理逻辑。

## 安全与稳定性要求

* 不要提交 Conda 环境中的本地路径。
* 不要提交 `.env`、私钥、token 或机器相关配置。
* 不要假设所有机器都有 GPU。
* GPU 相关功能应在无 GPU 环境下给出清晰错误信息。
* 长时间 burn 任务应支持安全退出。
* 对输入文件、时间参数和采样间隔进行必要校验。

## AI Agent 注意事项

在修改本项目时，请优先遵循以下规则：

1. 先阅读相关测试和文档，再修改实现。
2. 优先补充测试，再实现功能。
3. 如果修改 `burner` 的曲线解析、时间调度或 burn 强度控制逻辑，必须添加对应测试。
4. 如果修改 `watcher` 的采样逻辑或 CSV 输出格式，必须添加对应测试。
5. 不要大范围重写已有代码，除非任务明确要求。
6. 对不确定的行为，应优先查看 `docs/` 中的设计说明；如果文档缺失，应在实现中保持行为简单、可测试、可解释。

