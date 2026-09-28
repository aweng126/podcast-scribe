# 听稿安装与运行

支持可执行本地脚本的 Codex CLI / IDE，需要 Python 3.10+，以下命令适用于 macOS / Linux。Windows 可在 WSL 中采用同样的命令；原生 PowerShell 尚未验证。其他 Agent 需确认其 Skill 发现机制和脚本执行权限。

## 安装与首次调用

在任务工作区通过 [Skills CLI](https://github.com/vercel-labs/skills) 安装，需要 Node.js/npm：

```sh
npx skills@latest add aweng126/podcast-scribe --skill podcast-scribe -a codex
```

该命令从 GitHub 安装 Skill 及配套文件，无需本项目发布 npm 包。`npx` 不安装 Python 依赖；首次运行时，Agent 会检查能力并按需初始化或复用环境，用户无需指定解释器或逐项安装依赖。

B站默认优先采用通过结构与覆盖检查的字幕；这一路径无需转写 API。需要局部或完整音频转写时，才通过运行环境或宿主密钥设置提供 `OPENAI_API_KEY`，不要把密钥粘贴到聊天、命令、文稿或 Git。仅处理已有转写并输出 Markdown、阅读站或分享文件时，无需 API 或第三方 Python 依赖。

随后在任务工作区启动 Codex：

> 使用 $podcast-scribe https://www.bilibili.com/video/BV1XNtJ6UEmm

已有转写或本地音视频直接换成文件路径：

> 使用 $podcast-scribe ./input.json

默认保留完整对话、说话人与时间戳，按中文阅读习惯整理并保留原意，生成摘要、章节、Markdown 和 PDF。无需用户设置输出路径、解释器或长音频分片。仅有不同需求时补充，例如“只导出 Markdown”。整理与来源校对由宿主 Agent 完成，CLI 不会另外调用文本模型润色。

需要跨项目使用时，安装命令加 `-g`。也可克隆仓库后将完整的 `skills/podcast-scribe/` 复制到宿主的用户 Skill 目录，不能只复制 SKILL.md。在源码仓库工作的用户同样只需提供 Skill 名称和链接，无需再次安装。Skill 未出现在宿主列表时重启宿主；发现位置见 [Codex 官方 Skill 文档](https://learn.chatgpt.com/docs/build-skills)。

## Agent 的运行入口

以下命令由 Agent 执行。`PS_SKILL_ROOT` 由 Agent 按本文件所在的 Skill 安装目录设置为绝对路径，用户无需设置；源码仓库中的位置是 `skills/podcast-scribe/`。标准入口为：

```sh
bash "$PS_SKILL_ROOT/scripts/run.sh" --help
```

`run.sh` 自动选择 Python 3.10+ 与已有虚拟环境；普通命令不会自行联网安装。需要音频转写或 PDF 的依赖、且现有环境不满足时，由 Agent 执行：

```sh
bash "$PS_SKILL_ROOT/scripts/run.sh" setup
```

`setup` 复用已满足依赖的 Python 环境，或在 Skill 目录创建 `.venv` 并安装依赖，安装后重新检查导入与最低版本。主动设置了 `PODCAST_SCRIBE_PYTHON` 时，仅向该解释器所属的有效虚拟环境安装；不向全局 Python 安装。没有 Python 3.10+ 时需要先提供可用的 Python；`setup` 不安装系统字体、不生成密钥，也不授予远端访问权限。执行后重新运行任务所需的 `doctor --require ...`，确认字体与外部程序等前置条件。

始终从**任务工作区**执行，不要切换到 Skill 安装目录。默认文稿为 `data/<id>/episode.json`，导出为 `output/<id>/`，音频缓存为 `data/cache/`；相对路径均基于任务工作区。系列由 Agent 按 [系列归属](series.md) 核实，无法确定时为 `inbox` / “未分类”。更新 Skill 可能替换安装目录，文稿和缓存应留在任务工作区；更新后由 Agent 重新检查环境。

## 按需自检

新 B站任务和已有转写分别检查实际所需能力：

```sh
bash "$PS_SKILL_ROOT/scripts/run.sh" doctor --require ingest markdown pdf
bash "$PS_SKILL_ROOT/scripts/run.sh" doctor --require import markdown pdf
```

用户只要 Markdown 时从检查项中移除 `pdf`；已有单集文稿续编时无需重新检查云端转写能力。`ingest` 自检只检查字幕优先入口；默认字幕路径不因缺少密钥而阻断。实际转入音频路径且环境不足时，再执行 `doctor --require transcribe` 并补齐；本地音视频或明确要求音频、精准模式的新任务可直接检查 `transcribe`。`doctor` 返回各能力状态、问题和修复建议；全部满足时退出码为 `0`，否则为 `2`。实际需要的密钥、输入或权限不能猜测。默认 PDF 暂不可用时应说明阻塞，不能假称已导出。

| 能力 | 本地前置条件 |
| --- | --- |
| `import` / `markdown` / `site` | Python 标准库；无需 API、字体或第三方包。 |
| `pdf` | `reportlab` 与可嵌入的中文 TrueType 字体。 |
| `inspect` | `yt-dlp`；自检不访问 B站。 |
| `subtitle-source` | `yt-dlp`；本地字幕文件对照只需 Python 标准库。 |
| `ocr` | Tesseract、`chi_sim`/`eng` 语言包及 ffmpeg；按需检查。 |
| `transcribe` | `openai` SDK、`OPENAI_API_KEY` 与可执行的 ffmpeg。 |
| `ingest` | `yt-dlp`；只有转入音频路径时才需要 `transcribe` 的条件。 |

完整依赖包含 `reportlab`、`openai`、`yt-dlp` 和提供内置 ffmpeg 的 `imageio-ffmpeg`。启动器与 `doctor` 共用最低版本规则；启动器轻量检查模块位置和安装版本，`doctor` 与 `setup` 额外验证实际导入。`doctor` 的 `dependencies` 列出当前版本、最低版本及诊断状态，缺少版本元数据不能视为已满足。自检会运行 ffmpeg 版本命令，并实际检查 PDF 字体能否加载。`doctor` 不联网、不调用 API，不验证服务端权限、账户额度或密钥有效性。

PDF 自动探测常见系统字体；未找到时可通过环境变量 `PODCAST_SCRIBE_FONT` 指定有中文字形的 `.ttf` 或 TrueType `.ttc`。不是所有 `.ttc` / OpenType 字体都支持嵌入。导出后仍须渲染检查分页、中文和段落完整性。

干净的 Debian/Ubuntu 环境可安装文泉驿正黑，再复查：

```sh
sudo apt-get install fonts-wqy-zenhei
bash "$PS_SKILL_ROOT/scripts/run.sh" doctor --require pdf
```

程序会探测 `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`。没有系统安装权限时，将可嵌入的中文 TrueType 字体放在可读取的本地目录，并设置 `PODCAST_SCRIBE_FONT=/absolute/path/chinese.ttf` 后复查。仅安装 Noto CJK 的 OpenType/CFF 字体不一定可供 ReportLab 嵌入，需以 `doctor --require pdf` 的结果为准；不要反复运行 `setup` 尝试修复字体。

## 单集生产与续编

先检查任务工作区已有稿件；匹配同一输入时直接续编，不重复下载、转写或导入。新 B站任务：

```sh
bash "$PS_SKILL_ROOT/scripts/run.sh" ingest 'https://www.bilibili.com/video/BV1XNtJ6UEmm'
```

本地音视频与外部转写分别使用：

```sh
bash "$PS_SKILL_ROOT/scripts/run.sh" transcribe '/path/to/episode.mp4'
bash "$PS_SKILL_ROOT/scripts/run.sh" import '/path/to/transcript.json'
```

这些命令默认使用自动模式，自动推导 ID、标题和输出路径，输出实际保存或复用的单集 JSON 路径。用户选择精准模式时传 `--review-mode precise`；复用已有稿件时以记录中的实际模式为准，需要切换则通过 `edit` 更新 `review.mode`。`ingest` 内部检查来源；需要单独查看元数据时才用 `inspect`。用户明确指定时才传 `--output`、`--id`、`--title` 等参数；节目系列由 Agent 核实后通过系列参数或 `classify` 保存。已有稿件保留人工修改；需要另存一份草稿时指定新的输出路径，转写仍可复用缓存。

`ingest` 默认 `--transcript-source auto`，在自动模式先评估字幕：充分时直接生成字幕草稿，少量缺口或低置信片段只转写对应完整范围，其余情况使用完整音频。`--transcript-source subtitles` 严格只用合格字幕，不合格便停止，不调用音频 API；`audio` 强制音频。新任务的 `--review-mode precise` 配合默认 `auto` 会采用独立音频。用户无需选择这些参数；Agent 仅在用户明确改变来源要求时覆盖，规则见 [字幕来源选择](subtitles.md#字幕来源选择)。

只有 `ingest` 实际使用音频路径或运行本地 `transcribe` 时，才向配置的 OpenAI endpoint 发送音频并产生接口费用。现用 `gpt-4o-transcribe-diarize`、`diarized_json`、`chunking_strategy=auto`。长音频自动分片并复用成功缓存；局部修补也复用对应范围的缓存，详见 [长音视频](long-audio.md)。声源标签、缓存命中和字幕结构合格都不代表已经校对。

B站普通网页提取失败时，程序尝试正常公开元数据与播放 API，并核验指定分 P、权限、预览标记和时长。仍被拒绝时按下节处理，不能将简介冒充对话全文。

来源选择、评估原因与局部修补范围会记录在本地文稿的 `transcription` 中。纯字幕草稿不会拿同一份字幕生成“独立核验”；混合草稿中的字幕原文也不能靠同源对照证明准确。已有稿件、本地字幕与画面字幕 OCR 见 [字幕辅助核验](subtitles.md)。对照报告不自动修改正文或校对状态，未知说话人仍需按证据处理。

`ingest`、`transcribe`、`import` 生成的文稿仍是未经整理的草稿。获取或复用文稿后，继续执行：

```sh
bash "$PS_SKILL_ROOT/scripts/run.sh" status data/my-episode/episode.json
bash "$PS_SKILL_ROOT/scripts/run.sh" batch data/my-episode/episode.json --output output/my-episode/batch-001.json
```

把示例路径替换成命令返回的实际文稿路径。`batch` 默认每批 6000 字符；Agent 按 [分批整理](editing.md) 用 `edit --batch` 登记实际处理段落、用 `--note` 保存短笔记，默认下次读取会略过仍有效的自动整理进度。中断后先看 `status` 与 `editing-notes`，无需用户管理游标或批次参数。用户只要原始草稿时才省略额外整理。

## B站访问受限时

默认不读取浏览器登录态。匿名网页与公开 API 均失败后，请用户在浏览器正常打开目标视频，自行完成登录或验证码，并明确授权使用该浏览器的 B站登录态。例如回复“已在 Chrome 登录，允许使用 Chrome 的 B站登录态重试”即可，无需用户配置命令参数。已有明确授权时继续复用；不要重复询问，也不要自动轮试其他浏览器或账户。

以下仅供 Agent 在授权后执行，两个来源二选一。先检查元数据，不下载音频，也不调用转写 API：

```sh
bash "$PS_SKILL_ROOT/scripts/run.sh" inspect 'https://www.bilibili.com/video/BV1XNtJ6UEmm' --cookies-from-browser chrome
```

若用户已自行准备本地 Netscape 格式 cookies 文件并授权使用，可改用：

```sh
bash "$PS_SKILL_ROOT/scripts/run.sh" inspect 'https://www.bilibili.com/video/BV1XNtJ6UEmm' --cookies '/absolute/path/bilibili-cookies.txt'
```

`--cookies-from-browser` 支持 `BROWSER[:PROFILE]`，例如 `chrome`、`'chrome:Profile 1'` 或 `firefox`；只选择用户授权的浏览器和配置，不支持 yt-dlp 的 `+KEYRING`、`::CONTAINER` 扩展语法。该选项与 `--cookies` 互斥。元数据验证成功后，Agent 在原有 `ingest` 任务中携带同一获授权选项；`inspect` 成功不代表音频下载或转写一定成功。

授权重试仍失败、浏览器凭据读取被拒绝、需要进一步验证或只能试看时，说明诊断并停止，改由用户提供本地音视频，不切换匿名或其他浏览器继续尝试。登录态不能保证解决 HTTP 412。

不要让用户把 cookies 内容粘贴到聊天。yt-dlp 会读取获授权浏览器的 cookie 库；供请求使用的内存 cookie 仅保留未过期的 `bilibili.com` 及其子域 cookies，不导出或写回登录态，也不把 cookie 值或文件路径写入日志、文稿、缓存元数据或分享文件。用户提供的 cookies 文件保持本地，不能随项目、音频或公开投稿上传。

此流程参考 [bilibili-to-doc](https://github.com/programmerloverun/bilibili-to-doc) 使用浏览器登录态的方式；字幕与音频获取沿用同一获授权范围。字幕没有人物标签时，按所选模式结合明确的节目结构、问答上下文及来源证据处理归属，不凭空造出说话人，未核实名字保留匿名标签。cookies 格式和浏览器提取机制见 [yt-dlp 官方 FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)。

## 校对与导出

按 [数据约定](schema.md) 分批整理全文、保存人物对应与摘要章节后，自动模式按以下方式收尾并导出：

```sh
bash "$PS_SKILL_ROOT/scripts/run.sh" edit data/my-episode/episode.json --edits output/my-episode/edits-001.json --batch output/my-episode/batch-001.json --note '本批主题、关键事实、疑点及段落 ID'
bash "$PS_SKILL_ROOT/scripts/run.sh" complete data/my-episode/episode.json
bash "$PS_SKILL_ROOT/scripts/run.sh" validate data/my-episode/episode.json
bash "$PS_SKILL_ROOT/scripts/run.sh" export data/my-episode/episode.json
```

默认输出 `output/<id>/<id>.md` 和 `output/<id>/<id>.pdf`。用户只要 Markdown 时加 `--formats markdown`；用户指定路径时传 `--output-dir`。

仅已 `complete` 且本次 Markdown、PDF 均成功生成并通过基础验证后，`export` 才自动清理已登记的工具媒体；未完成、导出失败或仅导出一种格式时保留。用户要求保留媒体时加 `--keep-media`，该选择也阻止过期回收。原始输入、转写与字幕证据、最终稿和公开投稿保留；每次新媒体处理自动尝试回收 7 天未使用的登记媒体，编辑历史按版本无损压缩。清理范围、保留条件与手动预览命令见 [存储与清理](storage.md)。

自动模式逐批显式记为 `edited`，Agent 完成全文整理、核对节目元数据、处理人物归属并生成覆盖全篇的摘要章节后，用 `complete` 记录 `automated` 并直接交付。`batch.done` 与 `editing_progress.remaining_to_edit=0` 只表示当前整理范围已处理，不等于整集完成。精准模式逐段核验后记为 `reviewed`，未解项请用户确认，再运行 `complete --basis source_checked`。用户明确接受当前稿时运行 `complete --basis user_accepted`，不声称已经听音。修改正文或归属后需重新收尾；详细状态与旧稿兼容见 [校对约定](schema.md#校对状态)。

## 可选阅读页与分享

用户需要离线阅读页时才执行：

```sh
bash "$PS_SKILL_ROOT/scripts/run.sh" site data --preview
```

打开 `output/preview/index.html` 阅读草稿。只在用户要求自行部署、且内容已校对时运行本地 `publish`，重新导出后执行正式 `site data`，产物为 `output/site/`。`publish` 不执行线上部署，也不是社区分享的前置步骤。

任务完成并交付文件后，在对话中邀请社区分享一次；用户已明确仅本地、任务未完成或内容为演示时不邀请。用户同意后按 [分享投稿](sharing.md) 准备材料；不回复不视为同意，不自动公开。不要上传 `data/`、缓存、原始响应、编辑历史或虚拟环境。

## 官方接口依据

- [yt-dlp 支持站点及兼容性说明](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)
- [yt-dlp Bilibili 提取器](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py)
- [OpenAI 文件转写与说话人分离](https://developers.openai.com/api/docs/guides/speech-to-text)
