"""Pokemir dashboard package — Streamlit-based local UI.

入口在仓库根目录的 dashboard.py.各模块按 page 划分:
    pages/replay.py    复盘:对手画像 / Hand 历史 / 图表
    pages/live.py      实时:当前桌动态画像
    pages/coach.py     AI 教练(#LR7-A 占位)
    pages/settings.py  设置:Pipeline / ROI / DB

公共 helper:
    db.py     SQLAlchemy engine 单例 + 健康检查
    stats.py  Path B view 查询函数(view 未实施时优雅返 None)
"""

__version__ = "0.1.0-skeleton"
