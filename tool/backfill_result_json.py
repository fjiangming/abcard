"""
回填脚本: 将 credentials JSON 文件中的数据同步到 data.db executions 表

功能:
  1. 遍历 test_outputs/credentials_*.json, 以 email 为 key
  2. 与数据库 executions 表按 email 对比
  3. 数据库中【已有】的记录: 合并缺失字段到 result_json (不覆盖已有值)
  4. 数据库中【不存在】的记录: 自动插入, 关联到指定兑换码
  5. 同时给 JSON 文件注入 code 字段 (兑换码来源)

用法:
  python backfill_result_json.py                          # 仅合并缺失字段, 不插入新记录
  python backfill_result_json.py MKNK-FCV8-PEZ2           # 合并 + 插入 (关联到该兑换码)
  python backfill_result_json.py MKNK-FCV8-PEZ2 --dry-run # 预览模式
"""
import json
import os
import sqlite3
import sys
from datetime import datetime
from glob import glob

# ── 配置 ──
DB_PATH = os.environ.get("ABC_DB_PATH", os.path.join(os.path.dirname(__file__), "data.db"))
CRED_DIR = os.path.join(os.path.dirname(__file__), "test_outputs")

# credentials 白名单字段 (与 auth_result.to_dict() 一致)
CRED_KEYS = {
    "type", "email", "expired", "id_token", "account_id",
    "access_token", "last_refresh", "refresh_token",
    "session_token", "device_id", "csrf_token", "password", "code",
}


def load_cred_files() -> dict[str, dict]:
    """扫描 credentials JSON 文件, 以 email 为 key"""
    index = {}
    pattern = os.path.join(CRED_DIR, "credentials_*.json")
    for filepath in sorted(glob(pattern)):
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


def get_db_emails(conn) -> set:
    """获取数据库中所有已有 email"""
    rows = conn.execute(
        "SELECT DISTINCT email FROM executions WHERE email IS NOT NULL AND email != ''"
    ).fetchall()
    return {r["email"] for r in rows}


def backfill(code: str = "", dry_run: bool = False):
    """执行回填"""
    # 1. 加载 JSON 文件索引
    cred_index = load_cred_files()
    print(f"找到 {len(cred_index)} 个 credentials 文件 (按 email 去重)")
    if not cred_index:
        print("没有可用的 credentials 文件, 退出")
        return

    # 2. 连接数据库
    if not os.path.isfile(DB_PATH):
        print(f"数据库不存在: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    db_emails = get_db_emails(conn)
    print(f"数据库中已有 {len(db_emails)} 个不同邮箱\n")

    # ── 阶段 1: 合并缺失字段到已有记录 ──
    print("═" * 50)
    print("阶段 1: 合并缺失字段到已有记录")
    print("═" * 50)

    rows = conn.execute(
        "SELECT id, email, result_json FROM executions WHERE result_json IS NOT NULL"
    ).fetchall()

    updated = 0
    skipped = 0

    for row in rows:
        eid = row["id"]
        email = row["email"] or ""

        try:
            rd = json.loads(row["result_json"])
        except Exception:
            skipped += 1
            continue

        rd_email = rd.get("email", "") or email
        if not rd_email:
            skipped += 1
            continue

        cred = cred_index.get(rd_email)
        if not cred:
            continue

        # 检查缺失字段
        missing = []
        for key in CRED_KEYS:
            cred_val = cred.get(key, "")
            rd_val = rd.get(key, "")
            if cred_val and not rd_val:
                missing.append(key)

        # 如果有 code 参数且 result_json 中没有 code, 也补上
        if code and not rd.get("code"):
            rd["code"] = code
            if "code" not in missing:
                missing.append("code")

        if not missing:
            skipped += 1
            continue

        for key in missing:
            if key == "code" and code:
                rd[key] = code
            elif key in cred:
                rd[key] = cred[key]

        new_json = json.dumps(rd, ensure_ascii=False, default=str)
        print(f"  [UPDATE] id={eid} email={rd_email}  回填: {', '.join(missing)}")

        if not dry_run:
            conn.execute("UPDATE executions SET result_json = ? WHERE id = ?", (new_json, eid))
        updated += 1

    print(f"\n  合并完成: 更新 {updated} 条, 跳过 {skipped} 条\n")

    # ── 阶段 2: 插入数据库中不存在的记录 ──
    print("═" * 50)
    print("阶段 2: 插入数据库中不存在的记录")
    print("═" * 50)

    if not code:
        print("  未指定兑换码, 跳过插入。用法: python backfill_result_json.py <兑换码>")
        inserted = 0
    else:
        # 重新查询 (可能阶段1更新了一些)
        db_emails = get_db_emails(conn)
        missing_emails = []
        for email, cred in cred_index.items():
            if email not in db_emails:
                missing_emails.append((email, cred))

        if not missing_emails:
            print("  ✅ 所有凭证文件在数据库中均已存在, 无需插入。")
            inserted = 0
        else:
            print(f"  发现 {len(missing_emails)} 条缺失记录:")
            inserted = 0
            now = datetime.now().isoformat()
            for email, cred in missing_emails:
                cred_copy = {k: v for k, v in cred.items() if k != "_filepath"}
                cred_copy["code"] = code
                result_json = json.dumps(cred_copy, ensure_ascii=False, default=str)

                print(f"    [INSERT] {email}  ← {os.path.basename(cred.get('_filepath', '?'))}")

                if not dry_run:
                    conn.execute(
                        "INSERT INTO executions (code, plan_type, status, reserved_amount, "
                        "email, error_msg, result_json, created_at, updated_at) "
                        "VALUES (?, '', 'success', 0, ?, '', ?, ?, ?)",
                        (code, email, result_json, now, now),
                    )
                inserted += 1

    print(f"\n  插入完成: {inserted} 条\n")

    # ── 阶段 3: 给 JSON 文件注入 code 字段 ──
    if code:
        print("═" * 50)
        print("阶段 3: 给 JSON 文件注入 code 字段")
        print("═" * 50)
        patched = 0
        for email, cred in cred_index.items():
            filepath = cred.get("_filepath", "")
            if not filepath:
                continue
            try:
                with open(filepath, encoding="utf-8") as f:
                    original = json.load(f)
                if original.get("code") != code:
                    original["code"] = code
                    if not dry_run:
                        with open(filepath, "w", encoding="utf-8") as f:
                            json.dump(original, f, indent=2, ensure_ascii=False)
                    patched += 1
            except Exception:
                pass
        print(f"  已更新 {patched} 个 JSON 文件\n")

    if not dry_run:
        conn.commit()
    conn.close()

    print(f"{'[DRY RUN] ' if dry_run else ''}全部完成!")
    print(f"  阶段1 合并: {updated} 条")
    print(f"  阶段2 插入: {inserted} 条")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    _code = args[0] if args else ""
    _dry = "--dry-run" in sys.argv
    if _dry:
        print("=== DRY RUN 模式 (不实际写入) ===\n")
    if _code:
        print(f"目标兑换码: {_code}\n")
    else:
        print("未指定兑换码, 仅执行合并 (不插入新记录)\n")
    backfill(code=_code, dry_run=_dry)

