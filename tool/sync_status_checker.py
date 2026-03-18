"""
sync_status_checker.py — NewAPI 渠道同步状态校验脚本

功能:
  1. 遍历 test_outputs/credentials_*.json 中的 email
  2. 通过 GET /api/channel/search?keyword={email} 检查是否已在 NewAPI 中存在
  3. 如果搜索到了，更新 JSON 文件和数据库中的 synced_to_newapi 状态
  4. 生成校验报告

用法:
    python sync_status_checker.py [--dry-run]
"""
import argparse
import glob
import json
import os
import sqlite3
import sys
from datetime import datetime

import requests


# 项目根目录 (脚本在 tool/ 子目录下)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config():
    """从 config.json 读取 NewAPI 配置"""
    cfg_path = os.path.join(PROJECT_ROOT, "config.json")
    if not os.path.exists(cfg_path):
        print("❌ 找不到 config.json")
        sys.exit(1)
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    newapi = cfg.get("newapi", {})
    base_url = newapi.get("base_url", "").rstrip("/")
    token = newapi.get("admin_token", "")
    if not base_url or not token:
        print("❌ config.json 中 newapi.base_url 或 newapi.admin_token 未配置")
        sys.exit(1)
    return base_url, token


def search_channel(base_url: str, token: str, keyword: str) -> list:
    """调用 GET /api/channel/search?keyword=xxx 搜索渠道"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "New-Api-User": "1",
    }
    resp = requests.get(
        f"{base_url}/api/channel/search",
        params={"keyword": keyword},
        headers=headers,
        timeout=15,
    )
    if resp.status_code == 200:
        rj = resp.json()
        if rj.get("success"):
            data = rj.get("data", {})
            # API 返回 data: {items: [...], total, type_counts}
            if isinstance(data, dict):
                return data.get("items", [])
            elif isinstance(data, list):
                return data
    return []


def get_db():
    db_path = os.path.join(PROJECT_ROOT, "data.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def main():
    parser = argparse.ArgumentParser(description="NewAPI 渠道同步状态校验")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不实际更新")
    args = parser.parse_args()

    base_url, token = load_config()
    print(f"🔧 NewAPI: {base_url}")
    print(f"🔧 Dry-run: {'是' if args.dry_run else '否'}")
    print()

    # ── 1. 收集所有 credentials JSON 文件 ──
    out_dir = os.path.join(PROJECT_ROOT, "test_outputs")
    json_files = sorted(glob.glob(os.path.join(out_dir, "credentials_*.json")))
    print(f"📂 找到 {len(json_files)} 个凭证文件")

    # ── 2. 遍历并校验 ──
    total = 0
    already_synced = 0
    found_in_newapi = 0
    not_found_in_newapi = 0
    status_corrected = 0  # JSON 未标记但 NewAPI 有 → 需要更正
    errors = []
    details = []  # 每条记录的详细报告

    conn = get_db()

    for fpath in json_files:
        fname = os.path.basename(fpath)
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            errors.append(f"读取 {fname} 失败: {e}")
            continue

        email = data.get("email")
        if not email:
            continue

        total += 1
        local_synced = bool(data.get("synced_to_newapi"))

        # 调用 NewAPI 搜索
        try:
            channels = search_channel(base_url, token, email)
        except Exception as e:
            errors.append(f"搜索 {email} 失败: {e}")
            details.append({
                "email": email, "file": fname,
                "local_synced": local_synced, "remote_exists": "错误",
                "action": f"搜索失败: {e}",
            })
            continue

        # 判断该 email 是否存在于 NewAPI 渠道中
        remote_exists = False
        if channels:
            for ch in channels:
                if isinstance(ch, dict):
                    ch_name = ch.get("name", "")
                    ch_key = ch.get("key", "")
                    if email in ch_name or email in ch_key:
                        remote_exists = True
                        break
                elif isinstance(ch, str):
                    if email in ch:
                        remote_exists = True
                        break
                else:
                    # 兜底: 任何形式只要有匹配就算
                    if email in str(ch):
                        remote_exists = True
                        break

        if remote_exists:
            found_in_newapi += 1
        else:
            not_found_in_newapi += 1

        action = "无需操作"
        if remote_exists and not local_synced:
            # 状态异常: NewAPI 有但本地未标记
            status_corrected += 1
            action = "✅ 更正: 标记为已导入"

            if not args.dry_run:
                # 更新 JSON 文件
                data["synced_to_newapi"] = True
                with open(fpath, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

                # 更新数据库
                rows = conn.execute(
                    "SELECT id, result_json FROM executions WHERE email = ? AND result_json IS NOT NULL",
                    (email,),
                ).fetchall()
                for row in rows:
                    try:
                        rj = json.loads(row["result_json"])
                        if not rj.get("synced_to_newapi"):
                            rj["synced_to_newapi"] = True
                            conn.execute(
                                "UPDATE executions SET result_json = ?, updated_at = ? WHERE id = ?",
                                (json.dumps(rj, ensure_ascii=False, default=str),
                                 datetime.now().isoformat(), row["id"]),
                            )
                    except Exception:
                        pass
        elif local_synced and not remote_exists:
            action = "⚠️ 警告: 本地标记已导入但 NewAPI 未找到"
        elif local_synced and remote_exists:
            already_synced += 1
            action = "状态正常 (已导入)"

        details.append({
            "email": email, "file": fname,
            "local_synced": local_synced, "remote_exists": remote_exists,
            "action": action,
        })

        # 进度
        if total % 10 == 0:
            print(f"  已校验 {total} 条...")

    if not args.dry_run:
        conn.commit()
    conn.close()

    # ── 3. 生成报告 ──
    report_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"sync_check_report_{report_time}.md")  # 报告生成在 tool/ 目录

    anomaly_count = sum(1 for d in details if "更正" in d["action"] or "警告" in d["action"])

    lines = [
        f"# NewAPI 渠道同步状态校验报告",
        f"",
        f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**API 地址**: {base_url}",
        f"**模式**: {'预览 (dry-run)' if args.dry_run else '正式执行'}",
        f"",
        f"## 📊 汇总统计",
        f"",
        f"| 指标 | 数量 |",
        f"|------|------|",
        f"| 校验总数 | {total} |",
        f"| NewAPI 已存在 | {found_in_newapi} |",
        f"| NewAPI 未找到 | {not_found_in_newapi} |",
        f"| 状态正常 (已标记已导入) | {already_synced} |",
        f"| **状态异常** | **{anomaly_count}** |",
        f"| **已更正状态** | **{status_corrected}** |",
        f"| 搜索错误 | {len(errors)} |",
        f"",
    ]

    # 异常详情
    anomalies = [d for d in details if "更正" in d["action"] or "警告" in d["action"]]
    if anomalies:
        lines.append("## ⚠️ 异常记录详情")
        lines.append("")
        lines.append("| 序号 | 邮箱 | 本地标记 | NewAPI存在 | 处理 |")
        lines.append("|------|------|---------|-----------|------|")
        for i, d in enumerate(anomalies, 1):
            local = "✅" if d["local_synced"] else "❌"
            remote = "✅" if d["remote_exists"] is True else ("❌" if d["remote_exists"] is False else "❓")
            lines.append(f"| {i} | {d['email']} | {local} | {remote} | {d['action']} |")
        lines.append("")

    if errors:
        lines.append("## ❌ 错误列表")
        lines.append("")
        for e in errors:
            lines.append(f"- {e}")
        lines.append("")

    report_text = "\n".join(lines)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    # ── 4. 控制台输出摘要 ──
    print()
    print("=" * 60)
    print("📊 校验完成")
    print("=" * 60)
    print(f"  校验总数:     {total}")
    print(f"  NewAPI 已存在: {found_in_newapi}")
    print(f"  NewAPI 未找到: {not_found_in_newapi}")
    print(f"  状态正常:     {already_synced}")
    print(f"  状态异常:     {anomaly_count}")
    print(f"  已更正状态:   {status_corrected}")
    print(f"  搜索错误:     {len(errors)}")
    print()
    print(f"📄 报告已生成: {report_path}")


if __name__ == "__main__":
    main()
