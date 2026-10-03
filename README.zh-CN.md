# Obscura Gateway

**一个带认证的 MCP 地址，让每个 AI agent 在任何机器上都能用上自己独立的无头浏览器。**

[English](README.md) · [部署](docs/deployment.md) · [客户端接入](docs/clients.md) · [配置](docs/configuration.md) · [安全](docs/security.md) · [运维](docs/operations.md)

[Obscura](https://hub.docker.com/r/h4ckf0r0day/obscura) 是一个很快的无头浏览器，自带 MCP 服务。Obscura Gateway 放在它前面：**每个 agent 的 session 单独起一个 Obscura 容器**，负责认证、路由、生命周期管理和崩溃恢复，对外只提供一个 Streamable HTTP 的 MCP 地址。Claude Code、Codex、Pi、Cursor 等 MCP 客户端只需要一个 URL 和一个 token 就能接入。

```
 笔记本: Claude Code ─┐                                   ┌─▶ 浏览器容器  (session 1)
 笔记本: Codex ───────┤   HTTPS    ┌──────────────────┐   ├─▶ 浏览器容器  (session 2)
 服务器: Pi ──────────┼──────────▶ │  Obscura Gateway │ ──┼─▶ 浏览器容器  (session 3)
 CI 任务: 任意 MCP ────┘  + token   │  认证 · 路由      │   └─▶ ...  (浏览器池，空闲自动回收)
                                   │  生命周期 · PDF   │
                                   └──────────────────┘   出口防火墙：只允许访问公网
```

## 为什么需要它

Obscura 很适合给 agent 用。但要让多台机器上的多个 agent 一起用，会遇到几个它本身没解决的问题：

| 直接用 Obscura 的痛点 | Gateway 的做法 |
|---|---|
| `obscura mcp --http` 让所有客户端**共用一个浏览器**：agent B 能看到 agent A 的 cookie 和当前页面 | 每个 MCP session 一个独立的浏览器容器；session 只属于创建它的 token |
| 只有一个所有人共用的 token（`OBSCURA_MCP_TOKEN`），没法单独撤销某个 agent | 每台机器或每个 agent 一个 token，热加载，一条命令就能撤销 |
| 每台机器都要单独安装和配置 Obscura | 只需要一台服务器；客户端只要 URL 和 token。`add-token` 会直接打印 Claude Code、Codex、Pi 的配置命令 |
| 浏览器一崩溃，所有客户端都断，而且没留下任何记录 | 崩溃的 session 在下次请求时自动重建；退出码、是否内存不足、最近访问的网址和日志都会保存下来 |
| 打开 PDF 返回的是被截断的原始 `%PDF-` 字节 | `browser_pdf_text` 在沙箱里提取文字并分段返回；其他工具返回原始 PDF 时也会被自动替换成文字 |
| 把浏览器开放到网络上有 SSRF 风险 | 浏览器跑在单独的 Docker 网络里，出口防火墙只放行公网（局域网、VPN、云元数据、宿主机都访问不了） |
| 手动部署 | `docker compose up -d` 或 `sudo ./deploy/install.sh` |

## 对比

| | Obscura `mcp`（stdio） | Obscura `mcp --http` | **Obscura Gateway** |
|---|:---:|:---:|:---:|
| 一条命令部署服务端 | — | `docker run` | ✅ `docker compose up` / `install.sh` |
| 从其他机器访问 | ❌ 只能本机 | ✅ | ✅（提供 tunnel / VPN / 反向代理方案） |
| 客户端之间浏览器隔离 | 每个本地进程各一个 | ❌ 共用一个浏览器 | ✅ 每个 session 一个容器 |
| 多个 agent 同时使用 | 各自在本机跑浏览器 | ❌ 互相覆盖对方的页面 | ✅ 浏览器池（默认 25 个，每个 token 5 个） |
| 认证 | 不需要 | 一个共用 token | ✅ 每台机器 / 每个 agent 一个 token |
| 单独撤销某个 agent | 不适用 | ❌ 只能换掉共用 token | ✅ `obscura-gw revoke-token <名字>` |
| 增删 token 不用重启 | 不适用 | ❌ | ✅ 热加载 |
| session 归属（别人拿到 session ID 也用不了） | 不适用 | ❌ | ✅ |
| 浏览器崩溃恢复 | 重启客户端 | 所有人一起断 | ✅ 按 session 自动重建 |
| 崩溃诊断 | — | 容器日志（如果还在） | ✅ 退出码、是否内存不足、最近 10 个网址、日志 |
| 空闲回收 / 资源上限 | — | — | ✅ 空闲回收、池上限、每个 token 上限、请求大小限制 |
| PDF 转文字 | ❌ 原始字节 | ❌ 原始字节 | ✅ `browser_pdf_text` + 自动替换 |
| 内网（SSRF）防护 | 应用层 | 应用层 | 应用层 **+ 网络层出口防火墙** |
| 客户端接入 | 手动 | 手动 | ✅ `curl …/setup.sh \| sh -s -- <token>` |
| 服务器依赖 | Obscura 程序 | Docker | Docker + Python 3.8（只用标准库） |

"浏览器隔离"一行是实测结果：两个客户端连同一个 `obscura mcp --http` 容器（v0.2.3），都拿不到 session ID；第一个客户端设置的 cookie 和打开的页面，第二个都能看到。

## 快速开始

需要一台装了 Docker 的 Linux 服务器。

### 方式 A：Docker Compose

```bash
git clone https://github.com/<you>/obscura-gateway && cd obscura-gateway
mkdir -p config && cp deploy/gateway.env.example config/gateway.env
docker compose up -d --build
docker compose exec gateway obscura-gw add-token my-laptop
```

### 方式 B：直接安装（systemd）

```bash
git clone https://github.com/<you>/obscura-gateway && cd obscura-gateway
sudo ./deploy/install.sh --first-token my-laptop
```

两种方式默认都只监听 `127.0.0.1:8080`。要让其他机器访问，设置 `OBSCURA_GW_PUBLIC_BASE`，再在前面加一个 tunnel、VPN 或反向代理（见 [docs/deployment.md](docs/deployment.md#remote-access)）。

### 接入 agent

`add-token` 会打印客户端机器上需要的所有命令。最简单的方式是一行：

```bash
curl -fsSL https://browser.example.com/setup.sh | sh -s -- <TOKEN>
```

它会先验证 token，再自动配置机器上找到的所有支持的 CLI：

- **Claude Code**：`claude mcp add --scope user --transport http obscura-remote …`
- **Codex**：`codex mcp add obscura-remote --url … --bearer-token-env-var OBSCURA_REMOTE_TOKEN`
- **Pi**：写入 `~/.config/mcp/mcp.json`

其他任意 MCP 客户端只需要：

```
传输方式: Streamable HTTP
URL:      https://browser.example.com/mcp
请求头:   Authorization: Bearer <TOKEN>
```

只支持 stdio 的客户端可以用转接：`npx -y mcp-remote <URL> --header "Authorization: Bearer <TOKEN>"`。详见 [docs/clients.md](docs/clients.md)。

## token：一台机器一个，还是一个 agent 一个

token 代表的是"身份"，不是"一个浏览器"。每个 MCP session 都有自己的浏览器，所以一台机器上的多个 agent 共用一个 token，彼此仍然是隔离的。共用同一个 token 的 agent 会共享：

- 日志里显示的名字，
- session 上限（`OBSCURA_GW_MAX_PER_AGENT`，默认 5），
- 撤销（撤销后一起失效）。

| | 一台机器一个 token | 一个 agent 一个 token |
|---|---|---|
| 配置 | 每台机器配一次 | 每个 agent 配一次 |
| 撤销 | 整台机器 | 单个 agent |
| 日志里显示 | 机器 | agent |
| session 上限 | 整台机器共用 | 每个 agent 各自 |

```bash
obscura-gw add-token lab-server        # 打印可以直接复制的客户端配置命令
obscura-gw setup lab-server            # 以后再打印一遍
obscura-gw tokens                      # 列出所有 token（部分隐藏）
obscura-gw revoke-token lab-server     # 立即生效，它开着的浏览器也会被关掉
```

## 工具

Obscura 的所有浏览器工具都可以用（`browser_navigate`、`browser_markdown`、`browser_click`、`browser_fill_form`、`browser_screenshot`、`browser_evaluate`、cookie、标签页等，v0.2.3 共 37 个）。gateway 另外提供：

| 工具 | 说明 |
|---|---|
| `browser_pdf_text` | 提取 PDF 文字（参数 `url`、`first_page`、`last_page`、`offset`、`max_chars`）。默认读当前页面；长文档用 `offset` 分段读取。 |

任何工具返回原始 PDF 字节时（比如对 PDF 链接调用 `browser_markdown`），gateway 都会自动替换成提取出的文字。

## 容量

用 Obscura 0.2.3 实测：一个浏览器空闲时约 2 MB，打开重页面后约 250 MB。在 4 核 16 GB 的机器上，先到瓶颈的是 CPU 而不是内存；默认同时 25 个浏览器是比较合适的起点。空闲 15 分钟的浏览器会被自动回收。

## 安全概要

token 是 192 位随机数，比较时用常数时间算法。没有配置任何 token 时，所有请求都会被拒绝。每个 session 只能用创建它的 token 访问。浏览器容器可以访问公网，但访问不了局域网、VPN、云元数据和宿主机。PDF 在一个用完即删的容器里下载和解析：只读文件系统、没有任何 capability、以 `nobody` 用户运行。gateway 本身需要访问 Docker socket，这相当于 root 权限，所以请部署在你自己控制的机器上。公开到公网之前，请先读 [docs/security.md](docs/security.md)。

## 局限

- 只支持 Linux 服务器（Docker 部署用的是 host 网络；没有在 macOS、Windows 上测试过）。
- 反爬很强的网站（Cloudflare 验证页、AWS WAF、Anubis、Google CAPTCHA）仍然会拦截 Obscura。在我们测过的网站上，`OBSCURA_GW_STEALTH=1` 没有效果。
- `browser_pdf_text` 下载时不带浏览器的 cookie，所以需要登录才能看的 PDF 会失败，并给出明确的错误提示。
- session 保存在内存里：重启 gateway 会关掉所有浏览器。
- 只支持单机部署，不能横向扩展。

## 目录结构

```
gateway.py            gateway 本体（单文件，只用 Python 标准库）
obscura-gw            管理命令（token、状态、session、崩溃日志、客户端配置）
client-setup.sh       客户端安装脚本，通过 /setup.sh 提供
pdftext/              沙箱化的 PDF 转文字镜像
deploy/               install.sh、uninstall.sh、systemd 单元、firewall.sh、配置模板
docker-compose.yml    Docker 部署
tests/test_gateway.py 针对运行中 gateway 的端到端测试
docs/                 部署、客户端、配置、安全、运维、架构文档
```

## 参与贡献

欢迎提 issue 和 pull request。提交修改前，请先对一个运行中的 gateway 跑一遍端到端测试：

```bash
python3 tests/test_gateway.py http://127.0.0.1:8080/mcp /etc/obscura-gateway/gateway.env "$(command -v obscura-gw)"
```

## 许可与致谢

MIT 许可，见 [LICENSE](LICENSE)。

[Obscura](https://hub.docker.com/r/h4ckf0r0day/obscura) 是一个独立的项目，由其作者以 Apache License 2.0 发布。Obscura Gateway 不包含也不修改 Obscura 的代码，只是在运行时拉取并运行它公开发布的 Docker 镜像，与 Obscura 项目没有隶属关系。
