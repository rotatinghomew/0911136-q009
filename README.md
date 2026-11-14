# 轮班交叉试点中的叫应方式效果分析

本项目组织领域资料并提供当前摘要，通过本地 HTTP 供数服务连接应用。业务代码在 `raincheck`，字段与单位约定见 `docs/data-contract.md`。示例均为本地合成记录，不代表真实县域记录或业务结论。

## 环境

Python 3.11 及以上，业务运行仅使用标准库。构建工具及其依赖完整固定在 `requirements-build.txt`；先用 `python3 -m pip install -r requirements-build.txt` 安装。源码和业务测试无需访问外部服务，首次安装构建依赖需要访问 Python 包仓库。

## 当前摘要

在项目根目录运行，会临时启动真实本地 HTTP 服务、核验资料代际、计算现有摘要并完整打印 JSON：

```sh
python3 -m raincheck.runner --output /tmp/raincheck-summary.json
```

当前能力是源码 `analysis.py` 定义的摘要，不会自动给出其他统计推断。输出中的 scope 说明边界，sources 记录资料依据。

## 试点效果分析

在保留现行摘要的同时，`pilot.py` 提供遵循受限随机分配的设计内推断：

```sh
python3 -m raincheck.runner --pilot --output /tmp/raincheck-pilot.json
```

主分析（confirmatory）按原始分配（assigned_mode，ITT）比较两种叫应方式的处置完成时长与漏接风险：以 team-block 为随机化单位，每个区组仅在允许序列内整体重抽，枚举全部 2^8 种分配做精确检验；观察窗内未结事项按删失处理，不计为零耗时也不剔除。违背设计或样本不足的区组被降级，不进入主分析。输出中 comparability 量化跨班遗留、共用队伍与删失对可比性的影响，exploratory 为不遵循分配设计的探索性描述，conclusion 给出是否具备推广依据的判断；证据不足时不会强行选择赢家。

## 分开运行供数与应用

```sh
python3 -m raincheck.runner --serve --port 8765
```

另一个终端可运行：

```sh
python3 -m raincheck.runner --source-url http://127.0.0.1:8765 --output /tmp/raincheck-summary.json
```

## 测试与检查

```sh
python3 -m unittest discover -s tests -t . -v
python3 -m compileall -q raincheck tests
python3 -m build --wheel --no-isolation
```

测试覆盖现有领域摘要、非法引用、真实 HTTP 换代拒绝、表清单内容核验及结果保存。展示现有产品可直接运行上述摘要命令，不需要回放历史日志。扩展分析时应同步说明新增能力的实际运行入口，保留现行摘要调用。

## 容器运行

Dockerfile 固定官方 Python 3.11.14 slim-bookworm 镜像摘要，不依赖本机私有镜像；首次构建需下载基础镜像和固定构建工具。构建与运行实际摘要：

```sh
docker build -t local/raincheck .
docker run --rm local/raincheck
```

容器内测试：

```sh
docker run --rm --entrypoint python3 local/raincheck -m unittest discover -s tests -t . -v
```
