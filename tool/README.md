# tool/ — 辅助工具脚本

本目录存放项目的辅助运维工具和一次性补丁脚本，**非项目启动必需**。

## 脚本说明

| 脚本 | 用途 |
|------|------|
| `backfill_result_json.py` | 回填脚本：将 `test_outputs/credentials_*.json` 中的凭证数据同步到 `data.db` 的 `executions` 表，补全缺失字段并关联兑换码 |
| `sync_status_checker.py` | NewAPI 同步状态校验：通过 NewAPI 搜索接口检查每个账号是否已同步，更新本地 JSON 和数据库中的 `synced_to_newapi` 状态，并生成校验报告 |

## 使用方式

```bash
# 回填凭证到数据库（需指定兑换码）
python tool/backfill_result_json.py --code XXXX-XXXX-XXXX

# 校验 NewAPI 同步状态（支持 dry-run 预览）
python tool/sync_status_checker.py [--dry-run]
```
