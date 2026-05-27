"""🤖 AI 教练模块 — LLM 牌局解释 + 建议(#LR7-A 占位).

未实施.等 Path B / Path C 完成后接入 Qwen2.5-7B-Instruct 本地推理.
红线 [[llm-explanation-layer-only]]:LLM 仅用于解释层,禁止自动写库.
"""

import streamlit as st


def render():
    st.title("🤖 AI 教练")

    st.info(
        "📌 AI 教练模块占位(#LR7-A).\n\n"
        "未来功能:\n"
        "- 用户输入一手牌(或选 Hand 历史中某手) + 提问\n"
        "  - 「这把我该 fold 吗?」\n"
        "  - 「对手 VPIP 32% 我的 range 应该怎么调?」\n"
        "  - 「我今天的 leak 是哪些?」\n"
        "- LLM(本地 Qwen2.5-7B-Instruct)读 hand context + 对手画像 + GTO 锚\n"
        "- 输出自然语言推理\n\n"
        "**红线**:LLM 只能解释 / 建议 / 推理,**不写库 / 不自动修数据**.\n"
        "详见 `[[llm-explanation-layer-only]]` 记忆 + #LR7-A roadmap."
    )

    st.divider()
    st.caption("触发条件:Path C dashboard 主线完成后启动 #LR7-A 接入.")
