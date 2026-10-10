# Compatibility / 兼容性与验证范围

| 情况 | 本版本行为 |
|---|---|
| Python 3.11+ | 标准库运行；TOML 使用 `tomllib` |
| Windows 离线应用/回退 | 支持，检查桌面/CLI/app-server 和数据库独占打开 |
| Linux/macOS | 可运行纯数据逻辑与测试；live apply/rollback 明确停止 |
| `state_5.sqlite`、`thread_history_1.sqlite` | 当前识别的文件布局，版本后缀不是内部 migration 版本 |
| 内部 migration 上限 | 当前保护上限：state 59 / history 8；相同但超上限也停止，不承诺范围内所有历史版本都已实测 |
| 源与目标 migration 链/选定表列一致 | 正常准备 |
| history 8 → 7，新增 `root_turn_id` | 仅 NULL/自引用允许省略，必须匹配已知迁移链描述 |
| 未知 migration、未知列差异 | 停止，先对齐应用并重新采集 |
| 桌面目录存在 | 必须有目标 local project 注册，选定 catalog 列结构严格一致 |
| CLI-only 源、无 desktop catalog | 可以没有 desktop 库；已有 desktop 库仍保留/检查 |
| 选定非图片 `thread_attachments` | 停止，需要另一个附件感知流程 |
| PNG/JPEG/GIF/WebP 用户图片 | 恢复原字节/传送字节并验证 SHA；未解码作像素比对 |
| 活动 turn / realtime stream | 停止，等待完成 |
| 目标有选定会话的新消息或修改 | 基线不匹配，停止 |
| 混合新增/已有的会话批次 | 停止，拆批处理 |
| 网络传输 | 可选受限 HTTP，可信 LAN；不提供 TLS |

`_sqlx_migrations` 的版本、描述与 success 都用于比较，不仅比较表中最大整数。上限来自实现所观察的内部布局，是保护边界，不是官方版本支持列表。数据库更名、字段语义改变和未来格式需要更新代码与人工构造的兼容测试。不得手工修改 migration 表“让版本相同”。

## 测试证据如何读

`python -m unittest discover -v` 用合成数据库和虚构会话运行完整导出/预演/应用/核验/刷新流程。模拟的 Windows offline 检查避免触碰真实 Codex 和进程。测试包含原始图片缺失、传送字节恢复、源文件保护、未选中数据保护、预演文件篡改、目标新改动、版本不兼容、失败回退及选择清理。

GitHub Actions 在 Windows/Linux 与多个 Python 版本上执行这些测试。它证明合成用例通过，不证明各平台 native Codex 的写入许可、桌面版本或图片渲染。检查运行结果时注明：静态/合成测试、实际离线应用、重启后界面验收，三者不能混写。

本项目整理自一次真实迁移的方法与问题。通用脚本是将方法拆分重写后的版本；不把旧现场的成功回执等同于当前通用脚本在所有真实客户端上的验收。
