# CODEX_HOME 从 C 盘搬到其他盘

这是同一用户在自己电脑上的私有目录搬迁方法，独立于本工具的“选定会话包”。全目录副本可能包含认证、配置、聊天、日志和插件；绝不能当作可公开的示例或 GitHub 附件。

OpenAI 的 [environment variables 文档](https://learn.chatgpt.com/docs/config-file/environment-variables)说明 `CODEX_HOME` 管理配置、认证、日志、会话与技能等目录。`sqlite_home` 配置会优先于 `CODEX_SQLITE_HOME`；设置 CODEX_HOME 后，数据库不一定随之搬走。先使用 inventory 和自己的配置查明实际根目录。

## 两种方法

**只搬选定会话：** 用本项目导出到新的目标 home。目标先正常启动、登录和注册项目，按 quickstart 导入。适合保留接收端自己的登录、配置和其他会话。

**整个私人 home 搬盘：** 正常退出 Codex，独立终端保存旧 home 及实际数据库根的完整私有备份，再复制到空的新目录。仅适合同一用户明确希望搬完整环境的情况。此方法包含敏感文件，不使用 `export` 的 credentials=0 宣称。

## 离线复制步骤

先创建目标空目录和私有备份目录，确认目录对应关系与磁盘空间，完全退出 Codex。在独立 PowerShell 运行以下虚构路径示例：

```powershell
# 只复制，不移动源目录。不要使用 /MOVE、/MOV 或会删除目标的 /MIR。
robocopy C:\Data\CodexOld E:\PrivateBackup\CodexBeforeMove /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /XJ
if ($LASTEXITCODE -ge 8) { throw 'Backup copy failed' }
robocopy C:\Data\CodexOld E:\Data\CodexNew /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /XJ
if ($LASTEXITCODE -ge 8) { throw 'Home copy failed' }
```

`/XJ` 跳过 junction；真实数据若放在链接指向的独立目录，先识别并单独备份，不能把跳过当成完整迁移。Robocopy 退出码 0–7 不表示必然字节一致，要继续核对文件清单与 SHA。保留源目录。

```powershell
# 比较相对路径与 SHA。链接/权限错误应停止人工检查。
$taskOldRoot = 'C:\Data\CodexOld'
$taskNewRoot = 'E:\Data\CodexNew'
$taskOldFiles = @(Get-ChildItem -LiteralPath $taskOldRoot -File -Recurse -ErrorAction Stop | ForEach-Object {
    [PSCustomObject]@{ Path=$_.FullName.Substring($taskOldRoot.Length); SHA=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
})
$taskNewFiles = @(Get-ChildItem -LiteralPath $taskNewRoot -File -Recurse -ErrorAction Stop | ForEach-Object {
    [PSCustomObject]@{ Path=$_.FullName.Substring($taskNewRoot.Length); SHA=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
})
$taskDifference = Compare-Object $taskOldFiles $taskNewFiles -Property Path,SHA
if ($taskDifference) { $taskDifference; throw 'Copies differ; do not change the active home yet' }
```

SQLite 根与 home 分离时，同样离线复制那个根及其 WAL/SHM。复制后运行 inventory 作 SQLite integrity 检查。若进程仍在，不能把裸数据库主文件复制当成一致快照；本工具做预演时使用 SQLite backup API。

## 切换与路径检查

确认副本后设置用户变量（这是明确选择全目录搬迁时的操作）：

```powershell
[Environment]::SetEnvironmentVariable('CODEX_HOME','E:\Data\CodexNew','User')
```

退出全部旧终端和 Codex，重新打开以读取新环境。若实际 SQLite 路径受 `config.toml` 的 `sqlite_home` 控制，先备份该配置，明确修改为已复制的目标目录；单改环境变量不能覆盖配置项。

检查 `config.toml` 的工具、插件、技能、MCP、缓存和外部路径；一些资源放在 home 外不会被复制。检查实际安装版本、路径和授权。不要全量替换 JSONL / 聊天文本中的旧盘符来处理配置路径。

历史库、桌面目录和图片若仍引用旧路径，使用本项目在新 home 上做选定记录预演；整个桌面状态不一定能靠改环境变量自动完成搬迁。项目目录如果也搬盘，另行复制并在 Codex 重新注册，按实际路径映射会话。遇到不支持的内部结构保留旧环境，停止切换，不凭文本替换猜修复。

## 验收后才决定旧目录

重启验证当前 home 和 SQLite 路径，打开代表性会话、用户图片及工具。保留一段用户自己决定的恢复窗口，随后再判断旧 home、私有备份和临时复制包是否需要删除。这里没有自动删旧目录脚本；不要通过改变量成功就认定所有数据已经验收。
