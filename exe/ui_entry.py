"""
EXE 版 Streamlit 入口 — License 门禁 + 加载原版 ui.py

此文件在构建时替代原 ui.py 作为 Streamlit 入口:
  streamlit run ui_entry.py

流程:
  1. 检查 License → 未激活则显示激活界面
  2. 将 tier 写入环境变量
  3. exec() 加载原 ui.py 的全部逻辑
"""
import json
import os
import sys

# 确保 app 目录在 path 中
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from license_manager import (
    check_license, save_license, get_machine_id,
    set_tier_env, get_tier_env, TIER_ENV_KEY
)

import streamlit as st


def _show_activation_ui():
    """显示 License 激活界面"""
    st.set_page_config(page_title="AGBC — 激活", page_icon="🔑", layout="centered")

    st.markdown("""
    <style>
        .block-container { max-width: 600px; padding-top: 80px; }
        .stButton > button[kind="primary"] {
            background: linear-gradient(135deg, #6366f1, #4f46e5);
            border: none; color: white; border-radius: 8px;
            font-weight: 600; letter-spacing: 0.03em;
        }
    </style>
    """, unsafe_allow_html=True)

    st.markdown(
        '<h1 style="text-align:center;letter-spacing:3px;margin-bottom:0;">🔑 AGBC</h1>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p style="text-align:center;opacity:0.6;margin-top:0;">请输入 License Key 激活程序</p>',
        unsafe_allow_html=True,
    )

    st.divider()

    # 显示机器码 (买家需要提供给你)
    machine_id = get_machine_id()
    st.text_input("你的机器码 (请提供给管理员)", value=machine_id, disabled=True,
                  help="复制此机器码发送给管理员以获取 License Key")

    # License Key 输入
    license_key = st.text_area("License Key", placeholder="粘贴你的 License Key...",
                               height=100, key="w_license_key")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("激活", type="primary", use_container_width=True):
            if not license_key or not license_key.strip():
                st.error("请输入 License Key")
                return

            valid, tier, msg = check_license(license_key.strip())
            if valid:
                # 保存 License 到文件
                save_license(license_key.strip())
                st.success(f"✅ 激活成功! {msg}")
                st.balloons()
                import time
                time.sleep(2)
                st.rerun()
            else:
                st.error(f"❌ 激活失败: {msg}")

    with col2:
        if st.button("复制机器码", use_container_width=True):
            st.code(machine_id, language=None)
            st.info("请将上方机器码发送给管理员")

    st.stop()


def main():
    """主入口: License 检查 → 加载 ui.py"""

    # 1. 检查 License
    valid, tier, msg = check_license()

    if not valid:
        _show_activation_ui()
        return  # st.stop() 已在上面调用

    # 2. 将 tier 写入环境变量 (供 ui.py 读取)
    set_tier_env(tier)

    # 3. 注入 tier 到 Streamlit session_state (首次)
    if "_license_tier" not in st.session_state:
        st.session_state["_license_tier"] = tier

    # 4. 加载原版 UI
    #    Streamlit 每次交互重新执行入口脚本
    #    清除模块缓存后重新 import, 确保 UI 代码每次都执行
    try:
        sys.modules.pop("_original_ui", None)
        import _original_ui
    except Exception as e:
        st.error(f"UI 加载失败: {e}")
        st.stop()
        return


# ── Streamlit 入口 ──
main()
