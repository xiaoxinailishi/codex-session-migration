# 备份和临时文件：由用户决定删除

工具不会自动删除迁移备份。导出 ZIP、日志快照、预演库、回退库和长期图片各有不同用途；不能只按体积或“backup”名称判断是否是垃圾。

## 先列清单

完成数据核验，并由用户确认重启后的界面，再执行：

```powershell
python -m codex_migrate cleanup-plan --stage D:\Transfer\run-a
```

此命令仅生成清单，不删除。每个顶层项包含角色、文件数量、字节数、树内 SHA 和保留建议。所有 `delete_selected` 默认 false，`backups_selected_by_default` 为 false。

| 内容 | 通常用途 | 默认建议 |
|---|---|---|
| `backup-*` | 恢复迁移前真实文件和 WAL/SHM | 保留；删除就失去该回退选项 |
| `payload/export.sqlite` | 选定记录导出；下次刷新基线 | 如需后续刷新，先另存此文件到私有备份目录 |
| `payload/images` | 传输用图片副本 | live 图片已应用并验收后可考虑删除 |
| `prepared-*` | 预演目标库与用于 verify 的比较副本 | 验收结束前保留；删除后 verify 无法再比较这些副本 |
| `snapshots` | 导出时捕获的日志副本 | 不再排障后可考虑删除 |
| `bundle.zip`、`.part` | 传输压缩包和断点文件 | 不再需要重传时可考虑删除 |
| manifest、结果、说明 | 占用较小的操作证据 | 通常保留在私有位置 |
| `<home>/migration-assets/<run_id>` 或自定义 `image-root` | 会话正在引用的真实图片 | 不属于临时垃圾；受保护，不随 stage 清理 |

清理会拒绝 stage 内包含 live home、SQLite 或图片根目录的布局，也拒绝 symlink/junction 与越界路径。它不能替用户完成界面确认。

## 明确选择

```powershell
# 示例仅选传输包和源快照；请先确认清单中的实际名称
python -m codex_migrate cleanup --plan D:\Transfer\run-a\cleanup-plan.json --select bundle.zip --select snapshots --confirm
```

没有全部删除选项；没有选中项就不会删除。运行前再次核对当前核验报告、批次 ID、受保护 live 根目录和各选中项的签名。清单生成之后内容变过，要重新 cleanup-plan。

如用户明确决定不再保留某个回退备份，额外加 `--delete-backups`，且仍要选中那个实际目录：

```powershell
python -m codex_migrate cleanup --plan D:\Transfer\run-a\cleanup-plan.json --select "backup-<实际值>" --delete-backups --confirm
```

`--delete-backups` 只是允许删除已选中的备份，不自动选中全部备份。也可使用交互脚本 [Review-Cleanup.ps1](../scripts/Review-Cleanup.ps1)，逐项输入名称、确认删除和备份处理。

## 清理前自己的判断

确认会话和代表性图片可以打开；判断还需不需要回退；若需后续刷新，保存上一批成功导出的 `export.sqlite`；若需重做数据核验，保留 prepared 副本。临时传输服务结束、连接令牌撤销、防火墙规则删除是另一类收尾操作，不应因为保留数据备份就让服务继续监听。

本项目发布流程不会替用户删除真实迁移产生的备份。清理命令只处理用户指定的 stage 和精确选定项。
