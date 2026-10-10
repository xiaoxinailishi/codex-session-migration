# Codex Session Migration / Codex 会话迁移

[English](README.en.md) · [快速开始](docs/QUICKSTART.zh-CN.md) · [原理](docs/HOW-IT-WORKS.md) · [问题与解决](docs/TROUBLESHOOTING.md) · [备份清理](docs/CLEANUP.md)

把选定的 **Codex 本地聊天记录和用户上传的图片**迁移到另一台 Windows 电脑，或在同一台电脑上迁移到新的 `CODEX_HOME`。支持会话备份、恢复和带基线检查的单向更新。

这个项目将一次实际迁移中采用的方法、失败原因和修复过程整理成通用工具。公开内容只有代码、脱敏案例和人工构造的测试数据。工具生成的迁移包仍包含用户选定的聊天内容，属于私人数据。

> 这是社区工具，直接处理 Codex 内部 SQLite / JSONL 数据格式，并非 OpenAI 官方迁移接口。先在副本上预演；遇到未知数据库版本会停止。当前离线写入支持 Windows，Python 3.11+，运行时只使用标准库。

## 能做什么

- 按会话 UUID 或项目目录选择记录，保留接收电脑已有的其他会话与项目。
- 原图片存在时复制原字节；临时图片已消失时，从用户发送记录中的 base64 恢复实际传送的图片字节。
- 修改结构化图片引用，保护历史正文；重写 JSONL 后重新计算数据库中的字节偏移。
- 先准备接收端数据库副本，再完全退出 Codex，由独立终端离线应用。
- 应用前备份，检查预演后是否产生新修改；应用异常时恢复已尝试写入的文件。
- 更新已有会话时检查上次导出的基线；接收端有新增或改动就停止，避免覆盖分叉。
- 按用户明确选中的临时文件清理。回退备份默认保留，正在使用的图片目录受到保护。
- 可选短时局域网传输，限定单个接收 IP、令牌与文件范围，支持断点续传和 SHA-256 校验。

## 五步使用

下载或克隆本仓库，在仓库根目录运行命令。以下路径、IP 和 UUID 均为虚构示例。请先阅读[完整操作步骤](docs/QUICKSTART.zh-CN.md)，尤其是接收端项目注册和退出 Codex 的要求。

```powershell
# 1. 源电脑：只读查看真实目录、数据库版本、会话 ID
python -m codex_migrate inventory --home C:\Data\CodexSource --output C:\Transfer\inventory.json

# 2. 源电脑：导出一条会话，指定接收端的项目目录
python -m codex_migrate export --home C:\Data\CodexSource --thread 00000000-0000-4000-8000-000000000001 --map "C:\Projects\demo=D:\Projects\demo" --output C:\Transfer\run-a

# 3. 把 run-a 的 manifest.json 和完整 payload 文件夹私下复制到接收电脑。
# 接收电脑：先在 Codex 中添加目标项目，随后检查迁移包并预演
python -m codex_migrate check-package --stage D:\Transfer\run-a
python -m codex_migrate prepare --home D:\Data\CodexTarget --stage D:\Transfer\run-a

# 4. 在独立 PowerShell 中完全退出 Codex后，填入 prepare 返回的 prepared_root
python -m codex_migrate apply --home D:\Data\CodexTarget --prepared "D:\Transfer\run-a\prepared-<实际值>" --confirm

# 5. 先核验数据，再启动 Codex检查会话、项目归属和图片
python -m codex_migrate verify --stage D:\Transfer\run-a
```

复制项目源文件、登录账户、机器配置以及修改系统环境变量是独立操作；本工具的会话包不包含它们。`CODEX_HOME` 与 SQLite 根目录可能不同，必要时每次指定 `--sqlite-home`。Python 命令无法启动时使用已安装解释器的完整路径。

## 文档与脚本

| 目的 | 入口 |
|---|---|
| 首次迁移、选定会话更新、回退、验收 | [操作手册](docs/QUICKSTART.zh-CN.md) |
| 数据库、桌面目录、图片、JSONL 偏移的处理 | [实现原理与边界](docs/HOW-IT-WORKS.md) |
| 图片缺失、版本不同、临时窗口退出、网络错误 | [排障与脱敏案例](docs/TROUBLESHOOTING.md) |
| C 盘迁至其他盘、私有全目录副本 | [CODEX_HOME 搬迁](docs/HOME-RELOCATION.md) |
| USB / 私有目录 / 限定局域网传输 | [传输方法](docs/TRANSFER.md) |
| 列出占用、让用户选择、保留回退与真实图片 | [清理说明](docs/CLEANUP.md) |
| 支持的模式和测试证据 | [兼容性](docs/COMPATIBILITY.md) |
| 独立终端离线应用或回退 | [Run-OfflineMigration.ps1](scripts/Run-OfflineMigration.ps1) |
| 显示清单并交互选择清理 | [Review-Cleanup.ps1](scripts/Review-Cleanup.ps1) |
| 临时防火墙与传输服务一起关闭 | [Serve-LanPackage.ps1](scripts/Serve-LanPackage.ps1) |
| 安全报告与不应公开的文件 | [SECURITY.md](SECURITY.md) |

## 测试与证据

```powershell
python -m unittest discover -v
python -m codex_migrate --help
```

测试使用人工构造的数据库、虚构会话和一张 1×1 PNG，覆盖首次导入、图片字节恢复、基线更新、冲突阻止、未知结构停止、写入失败回退、后续工作保护与用户选择清理。测试中的离线进程检查被模拟；单元测试通过不等于在某一版本 Codex 上完成界面验收。原迁移方法曾用于实际迁移，通用化后的工具仍需使用者在自己的版本上预演并检查界面。

## 搜索与反馈

常见叫法：Codex 会话迁移、Codex 聊天记录备份恢复、Codex 换电脑、Codex C 盘搬迁、CODEX_HOME 迁移、Codex 图片丢失修复、Codex session migration、chat history backup restore、cross-device sync、SQLite JSONL local image recovery。查看[搜索说明](docs/SEARCH.md)。

提交 Issue 时提供软件版本、数据库迁移版本和**脱敏错误**即可。不要上传迁移包、真实聊天、图片、登录文件或连接令牌。贡献代码前阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。MIT 许可证。
