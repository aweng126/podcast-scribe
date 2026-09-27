# 字幕辅助核验

在逐句回听前，先用已有字幕与当前整理稿对照。字幕只提供另一份文字证据：可能经过删改、错字或自动识别，不自动替换正文、合并人物，也不自动设置 `reviewed`。用户仍只需提供 Skill 名称与素材；以下命令、缓存路径和抽帧参数由 Agent 选择。

## 默认字幕采集

新 B站 `ingest` 会先采集字幕轨，转写完成后自动生成对照报告。字幕不可用时保留诊断，继续音频转写；已有稿件不会重新转写，可独立补查：

```sh
bash "$PS_SKILL_ROOT/scripts/run.sh" check-subtitles data/my-episode/episode.json
```

字幕查询同时覆盖普通提取和公开 API 路径，区分可用、查询成功无独立字幕轨、需要登录及查询失败；弹幕不作为字幕。沿用用户已授权的 cookies 参数，不另行读取浏览器。`inspect` 的空语言列表并非总能证明已经完成字幕查询；以 `check-subtitles` 的明确结果为准。

优先使用中文轨，记录语言和可确认的人工/自动来源；不能确认来源时标为未知。成功采集保存到 `data/cache/<id>/subtitles.json`，缓存校验身份和内容摘要。需重新检查平台更新、登录后的字幕或已缓存的无轨结果时加 `--refresh`。缓存不保存 cookies 或临时签名链接。

已有字幕文件可直接离线对照，无需 ASR、API key 或 OCR 依赖：

```sh
bash "$PS_SKILL_ROOT/scripts/run.sh" check-subtitles data/my-episode/episode.json --file ./captions.srt
```

支持 B站 JSON、统一 cues JSON、SRT 和 VTT。字幕不会通过 `import` 覆盖现稿。若字幕与原视频有明确固定偏移，用 `--offset 秒数`；正数将字幕向后移动。不要为了提高一致率随意调整偏移。

## 画面字幕 OCR

成功查询却没有独立轨时，不能据此推断画面没有字幕。检查视频画面；有印在画面里的字幕时，使用本地 OCR。先确认环境：

```sh
bash "$PS_SKILL_ROOT/scripts/run.sh" doctor --require ocr
```

需要可执行的 Tesseract、`chi_sim` 和 `eng` 语言包，以及 ffmpeg。`setup` 只安装 Python 依赖，不安装 Tesseract 或语言数据。按平台补齐系统依赖，或配置 `TESSDATA_PREFIX` 指向已有语言数据目录；仅本地字幕文件对照不需要这些依赖。OCR 不调用云端视觉模型。

先取有连续字幕的短片，验证识别内容和字幕区域：

```sh
bash "$PS_SKILL_ROOT/scripts/run.sh" check-subtitles data/my-episode/episode.json --video ./episode.mp4 --start 60 --end 80
```

B站素材可将 `--video ./episode.mp4` 换为 `--ocr`，复用同一获授权登录态；程序先检查 OCR 依赖，再下载所选分 P 的低分辨率视频轨到缓存。即使仅识别短片，首次也会缓存完整视频轨，可能产生较大下载；不会再次调用音频转写 API。

默认每 0.5 秒抽帧，区域为画面下方的 `x=0.05,y=0.65,width=0.9,height=0.3`。Agent 根据实际画面用 `--region X Y WIDTH HEIGHT` 调整，避免把标题、水印或弹幕当作字幕；不要求用户填写这些参数。确认短片结果可用后省略 `--start/--end` 提取全片。仅有短片时，明确核验范围，不宣称覆盖完整节目。

识别逐批进行并缓存文字，失败后复用已完成批次，临时帧自动清理。原始素材或参数变化使用不同缓存；`--refresh` 重新识别。连续重复字幕合并，低置信度文字保留并提示；长时间不变的叠字单独列为疑似静态文字。OCR 置信度不能证明原音正确，短于采样间隔的字幕可能漏检，时间边界也可能存在采样误差。

## 差异、覆盖与校对

输出保存在 `output/<id>/subtitles/`：原字幕证据 JSON、对照报告 JSON、可阅读的 Markdown 清单。文件按内容摘要命名，保留旧版本。命令默认只输出统计与路径，不把整份字幕送入模型上下文。

对齐按时间及局部文字进行，支持一条字幕跨多个转写切片或反向拆分。标点、空白与全半角可归一化；数字、否定和有意义的符号仍参与比较。报告区分文字一致、差异、正文缺字幕和字幕缺正文。超长或模糊的对齐窗口保留为待检查，不强行认定一致。段落覆盖比例不等于音频准确率。

按预算只读取差异组：

```sh
bash "$PS_SKILL_ROOT/scripts/run.sh" subtitle-batch data/my-episode/episode.json --report output/my-episode/subtitles/comparison-<摘要>.json
```

默认 6000 字符，按返回的 `next_after` 用 `--after` 续读。单组超过预算时提高 `--max-chars`；报告中异常长的文本可能以 `detail_truncated` 标记摘录，须按 ID 回读原字幕证据和正文，不能把摘录当成完整依据。正文、时间、人物或校对状态改变后，报告会被识别为陈旧，需重新对照；此过程复用字幕缓存，不重新识别音频。

Agent 优先处理数字、否定、人名术语、漏句、低置信 OCR 和说话人交叠。对照报告本身不修改文稿；依据字幕和原音作出修订时，仍使用新的 `batch` 与 `edit --batch` 保存，保留原始转写和历史。实际逐段核验后才标记 `reviewed`，记录依据和范围；“字幕文字一致”不等于“已经逐句听音”。人物归属另行核对。完成后重新生成摘要、章节和导出，按原分享规则投稿。

实现依据：[yt-dlp 字幕选项](https://github.com/yt-dlp/yt-dlp#subtitle-options)、[Tesseract 文档](https://tesseract-ocr.github.io/tessdoc/)。
