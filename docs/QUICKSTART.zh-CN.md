# 操作手册：首次导入、选定更新与回退

所有示例路径、ID 和网络地址均为虚构。命令从仓库根目录执行，或先 `python -m pip install .` 后把 `python -m codex_migrate` 换成 `codex-migrate`。无需安装方式更适合离线操作。PowerShell 参数中的路径带空格时加引号。

## 1. 找到实际数据目录

源端和接收端分别执行：

```powershell
python -m codex_migrate inventory --home C:\Data\CodexSource --output C:\Transfer\inventory.json
python -m codex_migrate inventory --home D:\Data\CodexTarget --output D:\Transfer\inventory.json
```

先创建输出文件的父目录。`inventory` 输出包含私有会话标题和路径，请保留在本地。默认 `--home` 取 `CODEX_HOME`，未设置时取用户目录下 `.codex`。SQLite 目录优先级为：显式 `--sqlite-home` > `config.toml` 中的 `sqlite_home` > `CODEX_SQLITE_HOME` > 当前 home。不要根据旧电脑的目录猜新电脑的位置。

源端待迁移会话必须完成当前回复；发现活动 turn 或 realtime stream 会停止。接收端先在 Codex 中添加目标项目，例如 `D:\Projects\demo`，不要伪造项目 ID。源文件需要另外复制时，由用户决定复制范围，本工具不会搬运项目源码。

## 2. 导出选定会话

```powershell
python -m codex_migrate export --home C:\Data\CodexSource --thread 00000000-0000-4000-8000-000000000001 --thread 00000000-0000-4000-8000-000000000003 --map "C:\Projects\demo=D:\Projects\demo" --output C:\Transfer\run-a
```

`--thread` 可重复；或用 `--under C:\Projects\demo` 选择该根目录及其子目录的会话。每个不同项目根都要提供 `--map`。第一次先选一个小会话预演。输出目录必须是新的、且不在源端 `CODEX_HOME` 中。

成功状态为 `EXPORTED`；源数据库通过只读连接读取。选定聊天仍可能在正文中包含密码、业务信息或源码，迁移包不是脱敏公共资料。

如果原图片已消失，导出会尝试恢复发送记录里的图片字节。缺图时会停止，查看输出中的 `payload/image-recovery-audit.json`。多个传送版本无法唯一选择时，用私有 JSON 将源图片路径映射到审计表中选定的 SHA-256，再传 `--image-choices <文件>`；不要随便选一个版本来让校验通过。

## 3. 私下复制并预演

把 `manifest.json` 和完整 `payload` 目录复制到接收电脑同一 stage 根。USB 或私有共享目录即可；局域网方式见[传输文档](TRANSFER.md)。可保留源电脑的其他报告，但导入只依赖上述内容。

```powershell
python -m codex_migrate check-package --stage D:\Transfer\run-a
python -m codex_migrate prepare --home D:\Data\CodexTarget --stage D:\Transfer\run-a
```

`prepare` 只写 stage 下的数据库副本，返回 `PREPARED_NOT_APPLIED`、`prepared_root`、模式和兼容处理。不会把副本覆盖到真实数据库。请记录返回值，而不是自行猜时间戳目录。

图片默认将写入接收端 `<home>\migration-assets\<run_id>`。可通过 `--image-root D:\CodexAssets\run-a` 改为长期保留目录；不能放入可清理 stage。预演失败不要运行 apply，先处理明确错误。

## 4. 独立终端离线应用

在 Windows「运行」或开始菜单打开独立 PowerShell。不要依赖从 Codex 的工具终端派生出来的窗口是否能在退出后存活。

可使用辅助脚本，先打开脚本所在的终端，再完全退出 Codex，最后按回车：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Tools\codex-session-migration\scripts\Run-OfflineMigration.ps1" -Python "C:\Python312\python.exe" -CodexHome "D:\Data\CodexTarget" -Prepared "D:\Transfer\run-a\prepared-<实际值>"
```

或在独立终端手动执行：

```powershell
Set-Location C:\Tools\codex-session-migration
python -m codex_migrate apply --home D:\Data\CodexTarget --prepared "D:\Transfer\run-a\prepared-<实际值>" --confirm
```

退出包括托盘与 app-server。工具检查进程和数据库文件独占访问，检查预演之后的数据库摘要与非数据库文件 SHA；发现变化要求重新 prepare。不要强杀进程后立刻删 WAL/SHM。工具备份数据库与侧文件，然后才替换。失败时自动尝试恢复，保留错误与回退报告。

## 5. 数据核验与界面验收

```powershell
python -m codex_migrate verify --stage D:\Transfer\run-a
```

`DATA_VERIFICATION_PASSED` 表示选定记录与预演一致、文件和图片 SHA 通过。启动 Codex 后，再由用户检查：会话可打开、最新消息完整、项目归属正确、代表性图片确实显示、原有会话仍存在。记录检查结果即可，不需要公开截图。

在继续聊天之前核验。使用后 Codex 会产生新行、更新偏移、改写索引，严格比较可能失败；这不自动等于数据损坏，也不能为了“通过”强制覆盖后续工作。

## 6. 后续更新已有会话

更新是单向刷新，源端作为本次选定内容来源。先保留上次成功导出 stage 的 `payload/export.sqlite`。它是冲突基线，不是完整回退数据库。

```powershell
# 源电脑：用上次导出的 stage 或 export.sqlite 作为基线
python -m codex_migrate export --home C:\Data\CodexSource --thread 00000000-0000-4000-8000-000000000001 --map "C:\Projects\demo=D:\Projects\demo" --baseline C:\Transfer\run-a --output C:\Transfer\run-b
```

接收端重复复制、check-package、prepare、离线 apply、verify。若目标会话出现本地新增、删除或正文变化，会停止并要求人工调和。不得重建空目标来绕过冲突。选定批次不能同时混合首次导入与已有会话，请拆成两批。工具不提供自动双向合并。

## 7. 手动回退与清理

```powershell
# 完全退出 Codex 后，使用 apply 返回的真实 backup 路径
python -m codex_migrate rollback --home D:\Data\CodexTarget --backup "D:\Transfer\run-a\backup-<实际值>" --confirm
python -m codex_migrate cleanup-plan --stage D:\Transfer\run-a
```

回退前检查所有当前目标文件与刚应用时的 SHA。一旦产生后续工作，就停止回退，避免把新消息和其他改动一起覆盖。清理不是迁移的自动尾步骤，参见[用户选择清理](CLEANUP.md)。
