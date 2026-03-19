#!/usr/bin/env python3
"""
卡片信息格式转换工具

将原始卡片数据转换为统一的 "key: value" 格式，并过滤掉无关字段。

输入示例:
    卡号 5349336357118246
    有效期 0332
    CVV 719
    🕐开卡时间 Invalid Date
    剩余时间 {{COUNTDOWN:2026-03-19T21:41:20.540361627}}
    地区美国
    姓名 Cody Stanley
    地址 10094 Southeast Linwood Avenue
    城市 Milwaukie
    州 OR
    邮编 97222
    国家 United States

输出示例:
    卡号: 5349336357118246
    有效期: 0332
    CVV: 719
    姓名: Cody Stanley
    地址: 10094 Southeast Linwood Avenue
    城市: Milwaukie
    州: OR
    邮编: 97222
    国家: United States
"""

import re
import sys
import argparse

# 需要保留的字段及其匹配 pattern（按输出顺序排列）
FIELDS = [
    ("卡号", r"卡号\s+(.+)"),
    ("有效期", r"有效期\s+(.+)"),
    ("CVV", r"CVV\s+(.+)"),
    ("姓名", r"姓名\s+(.+)"),
    ("地址", r"地址\s+(.+)"),
    ("城市", r"城市\s+(.+)"),
    ("州", r"州\s+(.+)"),
    ("邮编", r"邮编\s+(.+)"),
    ("国家", r"国家\s+(.+)"),
]


def convert_card(raw_text: str) -> str:
    """将单张卡片的原始文本转换为标准格式。"""
    lines = []
    for label, pattern in FIELDS:
        match = re.search(pattern, raw_text)
        if match:
            value = match.group(1).strip()
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


def convert_batch(raw_text: str) -> str:
    """
    批量转换：用空行分隔多张卡片，逐张转换后合并输出。
    如果输入中没有明显的多卡分隔，则视为单张卡片处理。
    """
    # 按连续的空行拆分为多张卡片
    blocks = re.split(r"\n\s*\n", raw_text.strip())
    results = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        converted = convert_card(block)
        if converted:
            results.append(converted)
    return "\n\n".join(results)


def main():
    parser = argparse.ArgumentParser(
        description="卡片信息格式转换工具：将原始数据转换为 'key: value' 格式"
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="输入文件路径（省略则从 stdin 读取，支持管道和交互式粘贴）",
    )
    parser.add_argument(
        "-o", "--output",
        help="输出文件路径（省略则输出到 stdout）",
    )
    parser.add_argument(
        "-c", "--clipboard",
        action="store_true",
        help="从剪贴板读取输入（需要 pyperclip）",
    )
    args = parser.parse_args()

    # 读取输入
    if args.clipboard:
        try:
            import pyperclip
            raw = pyperclip.paste()
        except ImportError:
            print("错误: 使用 --clipboard 需要安装 pyperclip: pip install pyperclip", file=sys.stderr)
            sys.exit(1)
    elif args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            raw = f.read()
    else:
        if sys.stdin.isatty():
            # 交互模式：连续两个空行（即连按两次回车）自动结束输入
            print("请粘贴卡片数据，完成后连按两次回车：", file=sys.stderr)
            lines = []
            empty_count = 0
            while True:
                try:
                    line = input()
                except EOFError:
                    break
                if line.strip() == "":
                    empty_count += 1
                    if empty_count >= 2:
                        break
                    lines.append(line)
                else:
                    empty_count = 0
                    lines.append(line)
            raw = "\n".join(lines)
        else:
            # 管道模式：直接读取全部 stdin
            raw = sys.stdin.read()

    if not raw.strip():
        print("错误: 输入为空", file=sys.stderr)
        sys.exit(1)

    result = convert_batch(raw)

    # 输出结果
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result + "\n")
        print(f"已写入: {args.output}", file=sys.stderr)
    else:
        print(result)

    # 同时复制到剪贴板（如果可用）
    try:
        import pyperclip
        pyperclip.copy(result)
        print("（已复制到剪贴板）", file=sys.stderr)
    except ImportError:
        pass


if __name__ == "__main__":
    main()
