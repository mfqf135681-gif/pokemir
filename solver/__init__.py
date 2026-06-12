"""solver/ — #226 守恒求解器(圈梁实装,解释层)。

消费识别层(已冻结,contracts/recognition-freeze.md)的输出,绝不修改识别代码与原始事件。

🔗 圈梁纪律继承清单(requirement-discussions 圈梁设计 2026-05-29,逐条强制):
  1. 2+ 印证才推断(单一线索不补账);
  2. 推断深度 ≤1 —— 推出来的事件【不许】当下一次推断的输入(红线);
  3. 资金流容忍 ±rake(per-table 学习,冷启动期用保守上限);
  4. view-only:求解结果写独立重建工件,主表 action_events 永不 mutate(R3 血案);
  5. 每条推断带 inference_sources 追溯到具体真值;sanity 失败 → 标记不强推;
  6. 反 dim-creep:MVP 锁圈梁 8 维(D1/D5/D7/D14/D22/D23/D25/D26),扩维需数据立项;
  7. "补不平"的手自动登记 contracts/recognition-freeze.md §5(解冻保险条款)
     + 圈梁 §10 反例协议(同一张登记簿的两面)。
"""
