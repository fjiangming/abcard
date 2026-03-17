"""
回填脚本: 将 credentials JSON 文件中的完整字段合并到 data.db executions.result_json
用法: python backfill_result_json.py [--dry-run]

原因: 旧版 _run_flow_thread 只把 email/session_token/access_token/device_id 写入 rd,
      导致 result_json 缺少 refresh_token/id_token/expired/account_id/csrf_token 等字段。
      而 credentials_xxx.json 文件是通过 auth_result.to_dict() 完整保存的。

逻辑:
  1. 扫描 test_outputs/credentials_*.json 文件, 以 email 为 key 建立索引
  2. 遍历 executions 表中有 result_json 的记录
  3. 解析 result_json, 通过 email 匹配 JSON 文件
  4. 将 JSON 文件中的 credentials 字段合并到 result_json (不覆盖已有的非空值)
  5. 更新 executions.result_json
"""
import json
import os
import sqlite3
import sys
from glob import glob

# ── 配置 ──
DB_PATH = os.environ.get("ABC_DB_PATH", os.path.join(os.path.dirname(__file__), "data.db"))
CRED_DIR = os.path.join(os.path.dirname(__file__), "test_outputs")

# credentials 白名单字段 (与 auth_result.to_dict() 一致)
CRED_KEYS = {
    "type", "email", "expired", "id_token", "account_id",
    "access_token", "last_refresh", "refresh_token",
    "session_token", "device_id", "csrf_token", "password",
}


def load_cred_files() -> dict[str, dict]:
    """扫描 credentials JSON 文件, 以 email 为 key"""
    index = {}
    pattern = os.path.join(CRED_DIR, "credentials_*.json")
    for filepath in glob(pattern):
        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
            email = data.get("email", "")
            if email:
                # 如果同一个 email 有多个文件, 取最新的 (按文件名排序)
                if email not in index or filepath > index[email]["_filepath"]:
                    data["_filepath"] = filepath
                    index[email] = data
        except Exception as e:
            print(f"  [SKIP] {filepath}: {e}")
    return index


def backfill(dry_run: bool = False):
    """执行回填"""
    # 1. 加载 JSON 文件索引
    cred_index = load_cred_files()
    print(f"找到 {len(cred_index)} 个 credentials 文件")
    if not cred_index:
        print("没有可用的 credentials 文件, 退出")
        return

    # 2. 连接数据库
    if not os.path.isfile(DB_PATH):
        print(f"数据库不存在: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 3. 遍历 executions 表
    rows = conn.execute(
        "SELECT id, email, result_json FROM executions WHERE result_json IS NOT NULL"
    ).fetchall()
    print(f"数据库中有 {len(rows)} 条含 result_json 的记录\n")

    updated = 0
    skipped = 0
    no_match = 0

    for row in rows:
        eid = row["id"]
        email = row["email"] or ""

        try:
            rd = json.loads(row["result_json"])
        except Exception:
            print(f"  [SKIP] id={eid}: result_json 解析失败")
            skipped += 1
            continue

        rd_email = rd.get("email", "") or email
        if not rd_email:
            skipped += 1
            continue

        # 匹配 JSON 文件
        cred = cred_index.get(rd_email)
        if not cred:
            print(f"  [NO MATCH] id={eid} email={rd_email}: 无对应 credentials 文件")
            no_match += 1
            continue

        # 检查是否需要回填
        missing = []
        for key in CRED_KEYS:
            cred_val = cred.get(key, "")
            rd_val = rd.get(key, "")
            if cred_val and not rd_val:
                missing.append(key)

        if not missing:
            skipped += 1
            continue

        # 合并: 只填充缺失字段, 不覆盖已有值
        for key in missing:
            rd[key] = cred[key]

        new_json = json.dumps(rd, ensure_ascii=False, default=str)

        print(f"  [UPDATE] id={eid} email={rd_email}")
        print(f"           回填字段: {', '.join(missing)}")

        if not dry_run:
            conn.execute(
                "UPDATE executions SET result_json = ? WHERE id = ?",
                (new_json, eid),
            )
        updated += 1

    if not dry_run:
        conn.commit()
    conn.close()

    print(f"\n{'[DRY RUN] ' if dry_run else ''}完成!")
    print(f"  更新: {updated} 条")
    print(f"  跳过: {skipped} 条 (已完整或解析失败)")
    print(f"  无匹配: {no_match} 条")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    if dry:
        print("=== DRY RUN 模式 (不实际写入数据库) ===\n")
    backfill(dry_run=dry)
