# 安装与运行

在仓库目录安装，或把整个目录作为可发现 skill 安装到所用 agent 的 skills 目录（不能只复制 SKILL.md）：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python scripts/vtm.py doctor
```

`reportlab` 用于 PDF；`imageio-ffmpeg` 提供无需全局安装的 ffmpeg。中文 PDF 自动检测系统中文 TrueType 字体，或用 `VIDEO_TO_MARKDOWN_FONT` 指定有相应字形的 `.ttf` 字体。网络转写需在运行环境配置 `OPENAI_API_KEY`；不要在聊天、文稿或 Git 中保存密钥。依赖安装不等于实际模型可访问，`doctor` 不发 API 请求。

## 单集生产

```sh
.venv/bin/python scripts/vtm.py inspect 'https://www.bilibili.com/video/BV1GZbT6UE7o' --output output/source.json
.venv/bin/python scripts/vtm.py ingest 'https://www.bilibili.com/video/BV1GZbT6UE7o' --series-id my-series --series-title '系列名称' --output data/my-episode/episode.json
```

本地备用路径：

```sh
.venv/bin/python scripts/vtm.py transcribe '/path/to/episode.mp4' --id my-episode --title '本期标题' --series-id my-series --series-title '系列名称' --output data/my-episode/episode.json
.venv/bin/python scripts/vtm.py import '/path/to/transcript.json' --id my-episode --title '本期标题' --series-id my-series --series-title '系列名称' --output data/my-episode/episode.json
```

上面两种输入选一种，不重复写到相同目标。`ingest/transcribe` 会将音频发送到配置的 OpenAI endpoint，并产生接口费用。现用 `gpt-4o-transcribe-diarize`、`diarized_json`、`chunking_strategy=auto`。整段音频压缩为单声道 16 kHz、32 kbps MP3，上传前限制 24 MB；超限明确停止，要求导入已有整集说话人转写。第一版不实现独立音频分块之间的说话人匹配。相同内容与配置命中的成功转写缓存不会重复付费请求；失败响应不算成功缓存。

B站普通网页提取失败时，程序尝试正常公开元数据与播放 API，核验所选分 P、权限/预览标记和时长；音频主地址失败后最多使用该音轨响应提供的两个备用地址。平台仍可能限制公开 API，因此不能保证每次都可获取。已下载音频和成功转写应复用，避免无意义重复请求。

`import` 只解析已有文件，不需要 API，不会伪造人物。CLI 不独立调用文本模型润色；由本 Skill 所在 agent 阅读完整 JSON，完成整理、摘要与章节，再通过 `edit` 保存。

## 校对、导出与阅读站

```sh
.venv/bin/python scripts/vtm.py edit data/my-episode/episode.json --edits output/editorial.json
.venv/bin/python scripts/vtm.py validate data/my-episode/episode.json
.venv/bin/python scripts/vtm.py export data/my-episode/episode.json --formats markdown pdf --output-dir output/exports
.venv/bin/python scripts/vtm.py site data --preview --output-dir output/preview
```

直接打开 `output/preview/index.html`。站点支持离线阅读、系列导航、全文搜索、章节定位和本地下载。第一次只有草稿时，正式站点为空是预期行为。

完成实际校对后按 schema 文档应用 review 字段，然后：

```sh
.venv/bin/python scripts/vtm.py publish data/my-episode/episode.json
.venv/bin/python scripts/vtm.py export data/my-episode/episode.json --formats markdown pdf --output-dir output/exports
.venv/bin/python scripts/vtm.py site data --output-dir output/site
```

只有 `output/site/` 是准备部署的静态目录。不要上传 `data/`、缓存、转写原始响应、诊断、编辑历史或 `.venv`。草稿预览与正式构建使用不同输出目录。`publish` 不执行部署、购域名或更改外部账户。

第一版管理入口是 Skill + CLI；尚无网页编辑后台、自动订阅、多用户账户。时间戳链接返回原视频，未实现内嵌同步播放器。画面 OCR、人物人脸识别、截图提取也不在第一版。

## 无网络演示与测试

```sh
.venv/bin/python scripts/demo.py
.venv/bin/python -m pytest -q
python3 /path/to/skill-creator/scripts/quick_validate.py .
```

演示及其预览只输出到 `output/demo/`，不会覆盖真实节目的 `output/preview/` 或正式站。它是自制功能说明对话，不能称为用户链接的转写。

## 官方接口依据

- [yt-dlp 支持站点及兼容性说明](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)
- [yt-dlp Bilibili 提取器](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py)
- [OpenAI 文件转写与说话人分离](https://developers.openai.com/api/docs/guides/speech-to-text)
