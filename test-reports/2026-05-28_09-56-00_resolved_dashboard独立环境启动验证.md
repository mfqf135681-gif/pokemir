# Dashboard 独立环境启动验证

- **诊断时间**：2026-05-28 09:56
- **状态**：resolved
- **关联需求讨论**：无（用户当前对话直接要求验证）
- **触发红线**：R-10（项目工件隔离规则：创建项目内 `.venv-dashboard`，未使用全局安装，未触碰 pipeline 的 `.venv`）
- **无关红线已检查**：R-1, R-2, R-3, R-4, R-5, R-6, R-7, R-8, R-9
- **严重等级汇总**：P0: 0 个 / P1: 0 个 / P2: 0 个 / P3: 2 个

## 阶段 1：测试执行记录

1. 机器身份核验：
   ```powershell
   hostname
   ```
   输出：`Adminiatrator`。

2. 拉取最新代码：
   ```powershell
   cd D:\project\pokemir
   git pull
   ```
   结果：`6d06cff..9776c48` fast-forward，更新 `dashboard.py`。

3. 创建独立 dashboard 环境：
   ```powershell
   python -m venv .venv-dashboard
   .\.venv-dashboard\Scripts\Activate.ps1
   pip install -r requirements-dashboard.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
   ```
   结果：安装成功；安装范围为 Streamlit / pandas / SQLAlchemy / psycopg2 等 dashboard 子集，未安装 PyTorch / EasyOCR / OpenCV。

4. 启动 dashboard：
   ```powershell
   streamlit run dashboard.py
   ```
   结果：Uvicorn server started on `127.0.0.1:8501`，浏览器可访问 `http://127.0.0.1:8501`。

## 阶段 2：发现问题

未发现阻断问题。

已验证的用户可见结果：

- Sidebar 显示 5 个入口：`📊 复盘` / `👁 实时` / `📝 事件标注` / `🤖 AI 教练` / `⚙ 设置`。
- DB 状态从 `DB: 🟢 40 hands` 增长到 `DB: 🟢 44 hands`，说明 dashboard 能读 VPS PG，且 pipeline 正在继续落库。
- `📝 事件标注` 页面能显示中文格式滚动事件，例如：`[01:52:24] TempUser_00001100 (CO) 跟注 置信 100%`，并显示 `纠偏▼`。
- `⚙ 设置` → `Pipeline` tab 能看到 4 个 ROI 模式按钮：`8 人坐下` / `8 人观战` / `9 人坐下` / `9 人观战`。

截图证据：

- `test-reports/2026-05-28_dashboard_home.png`
- `test-reports/2026-05-28_dashboard_labeling.png`
- `test-reports/2026-05-28_dashboard_settings_pipeline.png`

P3 建议项：

- Streamlit 日志提示 `use_container_width` 将在 2025-12-31 后移除。
- Streamlit 日志提示 `st.components.v1.html` 将在 2026-06-01 后移除。

## 阶段 3：原因分析

本次没有启动失败或页面报错。

两个 P3 提示来自当前 Streamlit 1.57.0 对旧 API 的弃用提醒。用户现在看到的页面可以正常运行；风险是未来升级 Streamlit 后需要把对应用法替换为新 API，否则后续版本可能显示警告或失效。

## 阶段 4：修复方案草案

本次任务不允许改源代码，因此不执行修复。

后续如要处理 P3 提示，可在 DEV 模式做小改：

- 把 `st.dataframe(..., use_container_width=True)` 替换为 `st.dataframe(..., width='stretch')`。
- 把事件标注页的 `st.components.v1.html` 自动刷新实现替换为 Streamlit 新推荐方式或更稳定的刷新策略。

## 阶段 5：影响评估

- `.venv-dashboard` 是独立环境，不影响正在跑 pipeline 的 `.venv`。
- 未杀任何 Python 进程，Streamlit 作为独立进程继续运行在 `127.0.0.1:8501`。
- 未改 `.env`，未提交、未推送、未 amend、未 force。
- 工作区仍保留用户原有未提交内容：`rois/party_poker_8.json` 与 `tests/fixtures/showdown/`，本次未触碰。

## 终端最后日志摘录

```text
2026-05-28 09:54:39.932 Please replace `st.components.v1.html` with `st.iframe`.

`st.components.v1.html` will be removed after 2026-06-01.
2026-05-28 09:54:50.281 Please replace `use_container_width` with `width`.

`use_container_width` will be removed after 2025-12-31.

For `use_container_width=True`, use `width='stretch'`. For `use_container_width=False`, use `width='content'`.
2026-05-28 09:54:51.155 Please replace `use_container_width` with `width`.

`use_container_width` will be removed after 2025-12-31.

For `use_container_width=True`, use `width='stretch'`. For `use_container_width=False`, use `width='content'`.
2026-05-28 09:55:14.848 Please replace `use_container_width` with `width`.

`use_container_width` will be removed after 2025-12-31.

For `use_container_width=True`, use `width='stretch'`. For `use_container_width=False`, use `width='content'`.
```

## 违规标注

无。