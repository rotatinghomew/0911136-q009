# 领域资料与供数约定

## 数据含义

每个 block 保存按 shift_id 排序的 allowed_sequences，每个序列与这些班次一一对应，分配方式为 A/B。randomization_unit 为 team-block；只有表列序列可能被抽中，不能按事项独立置换。completed_at 为 null 表示观察窗内未见完成；missed 可为 null，表示漏接状态未知。共用 team_id 的乡镇会相互占用处置资源。分析截点用 known_at 过滤，遗留事项仍引用原打开班次。

## 现行摘要

`raincheck/analysis.py` 生成现行资料摘要。源码和运行结果中的 scope 说明其统计边界，不能把未提供的推断当成已有能力。

## 供数接口

`GET /manifest` 返回 generation、tables（每表 count 与 sha256）及 metadata。`GET /tables/<name>?generation=<generation>` 返回同代 rows。供数换代时旧 generation 返回 HTTP 409，不允许将新代记录接入旧分析；不存在的表返回 404。表内容为稳定键唯一的完整同代记录，不是增量事件流。客户端对清单摘要进行核验。摘要使用 UTF-8、排序对象键、紧凑 JSON 的 SHA256，数组顺序保留供数端顺序。

字段与主键声明位于 `raincheck/schema.json`，合成资料位于 `fixtures/dataset.json`。`metadata.cutoff` 是供数展示截点，不自动为分析选择训练截止点。服务可将整个 dataset 原子替换为新一代，但不在同一 generation 下更改内容。

## 结果保存

`reports.py` 保存现有摘要及来源表摘要，report_id 是内容摘要，不是随机编号。相同内容生成相同编号，临时文件写入完成后原子替换目标文件；历史内容是否保留由调用者选择不同目标路径。服务只监听本机，不接入生产系统。
