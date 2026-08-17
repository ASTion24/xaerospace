# Xaerospace 平台支持

Xaerospace 的平台兼容性以真实安装和物理后端门禁为准，不以纯 Python 导入成功
代替完整支持。

## 支持矩阵

| 平台 | 架构 | Python | CI 门禁 |
|---|---|---|---|
| macOS | arm64 | 3.12 | 完整四后端 |
| Linux | x86_64 | 3.10–3.12 | 安装与 wheel；3.12 完整四后端 |
| Windows | x86_64 | 3.10–3.12 | 安装与 wheel；3.12 完整四后端 |

四后端指 RocketPy、TudatPy、JSBSim 和 Basilisk。Python 3.12 的平台门禁执行：

1. 依赖安装和锁文件校验；
2. Ruff 静态检查；
3. 单元与协议测试；
4. wheel 构建和临时安装冒烟；
5. 平台对应的锁定 TudatPy 环境安装；
6. 16 个注册任务变体的真实后端执行；
7. wheel 可复现构建和资源检查。

Linux 和 Windows 还会在 Python 3.10、3.11 上执行依赖安装、静态检查、确定性
单元测试、wheel 构建和临时安装冒烟。

## 安装

建议使用 Python 3.12：

```bash
python -m pip install uv==0.12.4
uv sync --extra test --extra release --python 3.12
uv run xaerospace setup-tudatpy
uv run xaerospace web
```

`setup-tudatpy` 是跨平台 Python 安装器，不依赖 Bash、curl 或系统 Conda。它会：

- 检测操作系统和 CPU 架构；
- 下载并校验固定版本的 micromamba；
- 使用平台专用显式锁文件创建隔离环境；
- 校验 TudatPy 版本和必要资源；
- 将环境放入 Xaerospace 用户数据目录。

## 用户数据目录

| 平台 | 默认目录 |
|---|---|
| macOS | `~/Library/Application Support/Xaerospace/` |
| Linux | `$XDG_DATA_HOME/xaerospace/` 或 `~/.local/share/xaerospace/` |
| Windows | `%LOCALAPPDATA%\Xaerospace\` |

设置 `XAEROSPACE_HOME` 可以覆盖默认位置。Provider 用户配置默认位于：

| 平台 | 默认配置 |
|---|---|
| macOS | `~/Library/Application Support/Xaerospace/providers.local.json` |
| Linux | `$XDG_CONFIG_HOME/xaerospace/providers.local.json` 或 `~/.config/xaerospace/providers.local.json` |
| Windows | `%LOCALAPPDATA%\Xaerospace\providers.local.json` |

## 明确不支持

- Linux aarch64；
- Windows arm64；
- 未经门禁验证的 Python 版本；
- 将本地 Studio 绑定到非回环地址。

不支持的平台会在 TudatPy 安装前明确失败，不会回退到其他物理模型。
