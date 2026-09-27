# 开发与验收

从仓库根目录运行（Python 3.10+）：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e './skills/podcast-scribe[test]'
.venv/bin/python -m pytest -q
node --check skills/podcast-scribe/podcast_scribe/assets/app.js
```

完整 PDF 测试需要可嵌入的中文 TrueType 字体。可通过 `PODCAST_SCRIBE_FONT` 指定；PDF 测试依赖已包含 `pypdf` 和 `pymupdf`。Node.js 用于页面行为验证。

字幕采集测试使用模拟来源，不连接 B站或读取浏览器登录态。字幕对齐、差异批次与非破坏编辑测试离线运行。OCR 的真实合成视频测试需要 Tesseract 的英文语言包及 ffmpeg，缺依赖时跳过；中文验收另需 `chi_sim`，可通过 `TESSDATA_PREFIX` 指定语言数据目录。OCR 候选与置信度不能代替原音校对。

`skills/podcast-scribe/` 是完整可安装单元；测试、社区文稿和 CI 工具保留在仓库根目录。安装后的运行命令见 [工作流](../skills/podcast-scribe/references/workflow.md)。

社区站本地构建与部署设置见 [社区投稿与部署](community.md)。只使用获准公开的内容进行线上验收；合成样本和私人文稿留在临时目录。结构测试不能代替全文语义校对或 PDF 视觉验收。
