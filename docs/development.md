# 开发与验收

从仓库根目录运行（Python 3.10+）：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e './skills/podcast-scribe[test]'
.venv/bin/python -m pytest -q
node --check skills/podcast-scribe/podcast_scribe/assets/app.js
```

按测试范围准备依赖：

- **PDF**：可嵌入的中文 TrueType 字体，可用 `PODCAST_SCRIBE_FONT` 指定。
- **页面行为**：Node.js。
- **OCR 合成视频**：ffmpeg、Tesseract 及英文语言包；缺少时相应测试跳过。中文验收另需 `chi_sim`，可用 `TESSDATA_PREFIX` 指定语言数据目录。

字幕采集使用模拟来源，不连接 B站或读取登录态；字幕对齐与编辑测试离线运行。结构测试和 OCR 置信度不能替代语义校对、原音核验或 PDF 视觉验收。

`skills/podcast-scribe/` 是完整安装单元；测试、社区文稿和 CI 工具位于仓库根目录。运行命令见 [工作流](../skills/podcast-scribe/references/workflow.md)，站点构建与部署见 [社区维护](community.md)。线上验收只用获准公开的内容，合成样本和私人文稿留在临时目录。
