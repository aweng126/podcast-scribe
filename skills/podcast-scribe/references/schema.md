# 单集与编辑格式

`schema_version: 1`，每集 JSON 是唯一内容来源。完整例子见 `examples/demo/episode.json`（明确的自制演示，不来自 B站视频）。

核心字段：

- `id`：稳定 ASCII 标识；不要用标题变化来改变它。
- `title / description / published_at / duration_seconds`：节目元数据。
- `source: {url, platform, video_id, author}`：原作者与来源。
- `series: {id, title, description}`：产品中的播客系列，独立于 UP 主和平台合集。
- `speakers: [{id, name, role}]`：当前单集内的人物映射。导入标签原样保留在可选 `source_label`，不当作已验证姓名。
- `segments: [{id, start, end, speaker_id, raw_text, text, review_status}]`：秒级时间、匿名人物、不可变原始转写、整理稿；未知人物为 `null`，允许说话时间重叠。
- `summary: [string]` 与 `chapters: [{id, title, start, segment_id}]`：摘要及有真实段落锚点的章节。
- `status: draft | published`、`review: {speakers_confirmed, content_checked, mode?, basis?}`、`revision`。
- `references: [{title, url, note}]`：人物及原节目核验来源，仅 HTTP/HTTPS 链接，三种输出均展示。姓名可据节目资料核实；`speakers_confirmed` 仍表示段落说话人归属是否完成校对。
- `artifacts: {markdown, pdf}`：生成路径。编辑后清空，防止页面下载过期版本。
- 可选 `transcription`：实际来源、请求策略、字幕结构评估、音频修补范围及 `usage` 用量摘要，供本地追溯；不是准确率或校对完成证明。用量区分本次请求与历史缓存，见 [转写用量](long-audio.md#转写用量)，不进入公开投稿。
- 可选 `is_demo: true`：仅用于明确演示，不能发布。

系列使用目录中的稳定 ID 与标准名称；无法确定时为 `inbox` / “未分类”。单独调整归属可使用 `classify`，保存来源并保留已有正文与校对状态；普通 `edit` 仍遵循下面的校对重置规则。具体见 [系列归属](series.md)。

长文先通过 `status` 和 `batch` 读取所需范围，具体预算、续读及 `edit --batch` 的过期保护见 [分批整理](editing.md)。整理时写只含变化字段的 edits JSON，不直接覆盖原记录：

```json
{
  "speakers": [{"id": "speaker-1", "name": "主持人（姓名待确认）", "role": "主持人"}],
  "segments": [{"id": "seg-00001", "text": "这是一段忠实整理后的完整发言。", "review_status": "edited"}],
  "summary": ["本集讨论的主要议题。"],
  "chapters": [{"id": "chapter-1", "title": "话题名称", "start": 0.0, "segment_id": "seg-00001"}]
}
```

示例的章节时间必须替换成真实段落开始时间。编辑可局部更新；不允许删除段落、改原始转写或时间戳。平台字幕或 SRT 无人物标签时，先按证据通过 `speakers` 加入匿名人物定义，再为段落指定 `speaker_id`；未知归属不能靠任意填值补齐。保存会备份前一版本到相邻 `history/`，撤回到草稿并重置整集校对状态。正文或人物归属实际变更时，该段默认退回 `edited`；只有已核对这次变更，才在同一段 patch 中显式设为 `reviewed`。

自动整理续编另用文稿旁的 `<文件名>.editing-progress.json` 登记有效批次和短笔记，不改变文稿校对状态。要登记实际处理段落，使用 `edit --batch` 并在各段 patch 中显式写 `edited/reviewed`；单凭旧稿的 `edited`、笔记内容或遍历到末尾不能认定已处理，详见 [分批整理](editing.md)。

合并重复声源时，先分批将相关段落的 `speaker_id` 指向保留的人物，再通过 `{"remove_speakers":["speaker-3","speaker-4"]}` 移除无引用的旧标签。仍被段落引用、重复或不存在的 ID 会被拒绝；原始标签与分配关系保留在编辑历史。该操作不删除正文，也不自动确认人物或内容已校对。

`segments` 是音频对齐切片，不是阅读段落。三种输出都按相邻且相同的已定义 `speaker_id` 合并为话轮，仅显示一次姓名和起始时间；未知说话人不自动合并。原始切片和章节点仍保留，章节跳转到话轮内的对应文字，不在一句话中间强插章节标题。合并只处理展示；去除重复词或多余断句符号仍需通过 `edit` 修改整理稿，并保留原始文本。明确的自然段可用 `\n\n` 表示。

## 人物归属

人物先用匿名标签。实名须依据原节目官方说明、文字记录或明确自我介绍，并将具体问答与来源核对，更新姓名、角色和 `references`；不能凭面孔或声纹猜测身份。未能核实姓名时保留匿名标签，精准模式可请用户确认。

用户或节目资料已明确为双人访谈时，以采访者、受访者为人物集合，结合问答关系、连续发言与上下文归并碎片声源。自动模式记录语境判断依据，将短应答、抢话歧义留在本地笔记；精准模式仍有疑点则保留 `needs_review` 供确认。语境归并不是逐句听音，参考音频匹配也不等于人物实名核验。多人节目按实际参与者处理，不强行归成两人。

可用材料仍不足以区分发言归属时，保留未知，交付草稿并说明具体阻塞，不能为通过 `complete` 任意填标签。重新分配后按上文方式移除无引用的旧标签；原始归属保留在历史。长音频不要直接拼接独立分片的 Speaker 1，详见 [长音频人物规则](long-audio.md)。

## 校对状态

`review.mode` 为 `auto`（默认自动模式）或 `precise`（用户选择的精准模式）。自动模式由 Agent 完成全文整理后直接交付；精准模式把仍有疑点的片段交给用户确认。新任务可传 `--review-mode precise`；已有稿件通过 `edit` 提交 `{"review":{"mode":"precise"}}` 切换，保留正文与历史，不重做 ASR。

新 B站任务的精准模式配合默认来源 `auto` 会采用独立音频；已有稿切换模式只调整后续核验要求，不会自动补做音频。正文来自字幕时，同一字幕的匹配结果不能成为独立核验依据；所有来源检查和局部转写都仍生成未经整理的草稿。

逐段 `review_status` 使用以下值：

| 状态 | 含义 |
| --- | --- |
| `unreviewed` | 原始导入，尚未整理；缺失字段时同此状态。 |
| `edited` | 已整理，尚未完成本轮收尾。 |
| `needs_review` | 整理时发现疑点；自动模式由 Agent 按可用证据处理，精准模式保留待确认。 |
| `reviewed` | 已按本轮完成方式处理或接受，须结合整集 `review.basis` 理解。 |

`review.basis` 记录完成依据：

| 值 | 含义与入口 |
| --- | --- |
| `automated` | 自动整理完成。全文均已整理、有有效人物标签、摘要和章节后运行 `complete`；不要求用户手动确认，不表示逐句听音。 |
| `user_accepted` | 用户明确接受当前稿、不再处理剩余疑点。运行 `complete --basis user_accepted`，不得自行推定用户已经接受。 |
| `source_checked` | 已依据来源逐段精校。先逐段设为 `reviewed`，再运行 `complete --basis source_checked`；未解疑点不能由此命令批量跳过。 |

精准模式不能以 `automated` 完成。用户在精准模式明确接受当前结果时可记录为 `user_accepted`，仍不宣称已听音。旧稿无 `mode` 时默认自动模式，无 `basis` 的既有校对记录保持兼容；不会仅因加载或导出就改动状态。

`complete` 保存历史、增加修订号、清空旧导出并确认整集人物和内容完成。自动完成拒绝含未整理原始段落的稿件，也不生成摘要、章节或修订文字；Agent 必须先完成这些工作。`content_checked=true` 仍要求全部段落为 `reviewed`，`speakers_confirmed=true` 仍要求每段有有效人物标签，匿名标签可以使用。不要只设置两项布尔值来跳过流程。

普通 `edit` 会撤销整集完成标志并保留模式及前次完成依据；正文或归属变化的段落退回 `edited`。切换到精准模式时，先前自动整理或用户接受的段落回到待精校状态，不能直接当作已听音。旧的 `basis` 在整集完成标志为 false 时不表示本轮已经完成。

旧值 `pending`、`uncertain` 可读取并按未核对处理，新稿使用上表状态。Markdown/PDF 在文首说明整体完成方式，不重复显示通用校对标签。自动模式的识别疑点记在本地笔记中，阅读稿不插入手动核对清单；精准模式保留必要的具体疑点与单独清单。切换模式不会自动删除正文里的注释，需要 Agent 依据用户要求通过 `edit` 处理。

旧版本若留下整集已校对但段落仍未校对的冲突，可通过 `edit` 设置两项整集标志为 false，备份并退回草稿后继续处理。兼容读取只放宽旧校对状态冲突，其他结构错误仍需修复。

## 外部转写导入

外部转写 JSON 支持 `{"segments":[{"start":0,"end":3,"speaker":"A","text":"内容"}]}`、直接段落数组及 B站 `body` 格式。SRT/VTT 支持时间戳；VTT 的 `<v Name>` 标签可用作匿名人物分组线索。

JSON 正文使用 `text` 字段，缺失时读取 B站格式的 `content` 字段。正文必须是字符串；显式 `null`、数字、布尔值、数组或对象会使整次导入报错，不生成部分文稿。缺失正文或仅含空白的段落会跳过。

导入自制测试对话时使用 `import --demo`，明确区分功能演示和真实节目。
