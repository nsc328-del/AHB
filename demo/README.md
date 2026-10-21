# AHB 当前测试快照

2026-10-22 发布版本，按项目负责人授权公开。包括 30 份素材与样片、8 个剪辑项目、16 份方案、15 条成片，以及动画、分析、候选、任务和用量记录。星尚历史数据不在此快照内。

先安装仓库 README 所列依赖，再在仓库根目录导入：

```sh
.venv/bin/python scripts/demo_data.py import --data-dir data
VIDEO_STUDIO_DAILY_SUMMARY=0 .venv/bin/python app.py
```

已有数据目录不会被覆盖。其他目录的用法、来源署名和公开授权范围见[测试数据说明](../docs/demo-data.md)。

- `records.json`：可迁移的原有记录，导入时重建数据库。
- `files/`：原件、证据帧、代理、各版成片和动画源文件。
- `external/output/`：原始下载文件、历史对照报告、回执及界面验收记录。
- `manifest.json`：文件哈希、记录数量、路径转换与完整性清单。
- `exclusions.json`：因含星尚历史数据而保留在本机的附加文件清单。

原媒体未重编码，失败与未采用的候选仍保持原状态。历史报告按当时口径保留，不能代替当前 README 的能力说明；其中引用的被排除材料不随本次快照提供。

核对快照与导入结果：

```sh
.venv/bin/python scripts/demo_data.py check --data-dir data
```
