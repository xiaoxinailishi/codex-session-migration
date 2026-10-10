# 私有迁移包传输

## 推荐：USB 或私有目录

把 `manifest.json` 和完整 `payload` 复制到接收 stage，运行 `check-package`。迁移包包含选定真实聊天和图片，不可上传 GitHub Issue、公开 Release 或公开网盘。

`bundle.zip` 只包含 payload 内的数据，不包含外层 manifest。手动解压时把内容解到 `<stage>/payload`，再把对应 manifest 放在 stage 根。最少要保留两者的层次和批次一致性。

## 可选：可信局域网短时服务

该 HTTP 服务不加密。只用于可信、受控的局域网；不用于互联网、不端口转发，也不把授权 token 放到 URL/日志。需要加密时使用自己的加密传输渠道。服务只提供该批次的 manifest 和 ZIP，限定接收 IP、Bearer token 和 1–120 分钟有效期。

下面 `10.0.0.10` / `10.0.0.11` 是虚构地址，替换为用户自己核对的源端/接收端地址。

```powershell
# 源电脑：保留该独立窗口，结束时 Ctrl+C
python -m codex_migrate serve --stage C:\Transfer\run-a --bind 10.0.0.10 --peer 10.0.0.11 --connection C:\Transfer\connection-run-a.json --minutes 30
```

连接文件包含 token 与期望 SHA，私下交给指定接收电脑。不要粘贴到公开聊天或 Issue。若使用 AI 协助跨聊天交接，先由用户明确授权具体接收聊天、数据范围和令牌交付；用户自己复制文件不需要本工具代发消息。

```powershell
# 接收电脑：下载、断点续传、SHA、CRC 与解包检查，不更新真实数据库
python -m codex_migrate receive --connection D:\Transfer\connection-run-a.json --output D:\Transfer\run-a
python -m codex_migrate prepare --home D:\Data\CodexTarget --stage D:\Transfer\run-a
```

服务正常关闭或 Ctrl+C 时，源连接文件删除 token 并标记 REVOKED。接收端旧连接文件的 token 即使仍在，也无法连接已关闭服务。异常强杀没有 finally 保证，应确认监听关闭并删除私有连接文件。

## 防火墙

先检查监听和 TCP，再判断是否需要防火墙规则。只开放准确的本地地址、端口、Python 程序和接收端地址；网络 profile 与规则不匹配也会造成超时。辅助脚本可临时创建精确规则，并在正常结束时删除：

```powershell
# 仅在需要改防火墙时，在管理员独立 PowerShell 使用 -OpenFirewall
.\scripts\Serve-LanPackage.ps1 -Python C:\Python312\python.exe -Stage C:\Transfer\run-a -BindAddress 10.0.0.10 -PeerAddress 10.0.0.11 -Connection C:\Transfer\connection-run-a.json -OpenFirewall
```

管理员权限是 Windows 修改防火墙的要求；不需要改规则时不加该开关。不要让整个 Python 对所有远端常驻开放。手动关闭或中断后用 `Get-NetTCPConnection -LocalPort 49473 -State Listen` 和 `Get-NetFirewallRule -Name 'CodexSessionMigration-*'` 核对。脚本被强行终止时，按实际规则名手动移除，不能删除别人的规则。

ZIP 解包拒绝重复路径、绝对路径、`..`、冒号、反斜杠和链接逃逸，并检查内容范围与 manifest。一旦 SHA 不符，不应用。
