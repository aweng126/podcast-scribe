# 长文分批整理

这些命令只在本地读写 JSON，不调用文本模型。由运行 Skill 的 Agent 整理文字、核对来源、记录各批笔记，再依据全篇笔记生成摘要与章节。按 [工作流程](workflow.md) 使用 `bash "$PS_SKILL_ROOT/scripts/run.sh"`，由入口选择解释器。以下示例的文稿路径和批次文件名由 Agent 按实际单集替换，无需用户填写。

有其他字幕证据时做 [字幕辅助整理](subtitles.md)；已经采用同一份字幕的正文不能用自我对照证明准确。全片画面 OCR 仅用于精准模式或用户明确要求时，用 `subtitle-batch` 只读取差异，再针对相关 ID 获取新的正文 `batch`。对照报告不会代替编辑批次，也不会自动设置已校对；修订后需重新对照以避免使用陈旧证据。

## 读取进度和正文

```bash
bash "$PS_SKILL_ROOT/scripts/run.sh" status data/my-episode/episode.json
bash "$PS_SKILL_ROOT/scripts/run.sh" batch data/my-episode/episode.json --output output/my-episode/batch-001.json
```

`status` 保留修订号、各校对状态数量、剩余段落与字符数、未知人物、模式和完成依据；其中 `remaining` 仍统计尚未 `reviewed` 的段落，须结合 `review.basis` 理解，不是“尚未核对原音”的数量。新增的 `editing_progress` 单独报告有效登记数 `registered`、失效数 `invalidated`、待整理数 `remaining_to_edit`、下一段 ID、笔记数与模式。

默认 `batch`（无 `--after`、`--raw`、`--include-reviewed`）在自动模式中，除已 `reviewed` 的段落外，只跳过本地进度里明确登记且当前仍有效的已处理段落。没有进度登记的旧 `edited` 仍须读取；精准模式不因自动整理登记而跳过 `edited`，`needs_review` 也不会被登记掩盖。`batch.done` 只表示当前筛选范围没有待处理目标，不表示整集已完成。

`batch` 默认每批 6000 字符，标准输出与保存文件相同。`--max-chars` 可调整完整紧凑 JSON 的字符上限，包括元信息和换行；字符数不是 token 数。主要字段：

- `targets`：本批需要处理的完整段落，包含稳定 ID、时间戳、说话人 ID、当前正文和校对状态。
- `context.before` / `context.after`：相邻段落的只读摘录，默认各最多 300 字符；裁剪时 `truncated=true`。预算不足时会进一步缩短，完全省略的方向列在 `context_omitted`。
- `speakers`：只包含本批与上下文出现的人物 ID 和名称。
- `base_revision` / `base_sha256`：写回时用于确认文稿仍是读取批次时的版本。
- `next_after`：下一批的续读位置；为 `null` 时已到当前筛选范围的末尾。
- `done`：当前游标之后没有符合筛选条件的目标时为 `true`，此时 `targets` 为空。

默认只发送当前 `text`，不重复原始 `raw_text`、整集历史、导出路径或全文其他段落。单个目标段落也放不进预算时命令明确报错并给出所需最小预算，不截断正文。通过 `--context-chars` 调整上下文长度；需要重读已校对内容时加 `--include-reviewed`。

## 增量写回和续读

编辑 JSON 只写变化字段。例如整理了一个段落，但尚未核对原音频：

```json
{
  "segments": [
    {"id": "seg-00001", "text": "整理后的完整段落。", "review_status": "edited"}
  ]
}
```

```bash
bash "$PS_SKILL_ROOT/scripts/run.sh" edit data/my-episode/episode.json --edits output/my-episode/edits-001.json --batch output/my-episode/batch-001.json --note 'seg-00001：本批关键事实、论据与待查疑点。'
bash "$PS_SKILL_ROOT/scripts/run.sh" batch data/my-episode/episode.json --output output/my-episode/batch-002.json
```

自动模式处理完的段落须在 patch 中显式带 `review_status: edited`；即使文字无需修改，也写该段 ID 与状态。只有 `edit --batch` 成功保存的实际补丁中显式 `edited/reviewed` 段落才登记进度；漏写的目标、只读上下文、`needs_review`、仅改文字而自动变成的 `edited` 均不登记。这样第二条命令可安全找到尚未处理的正文。精准模式只将实际核验过的段落提交为 `reviewed`，无需重写未变正文。

每批使用新的输出文件名。明确需要从指定位置遍历时可用 `--after <上一批 next_after>`；指定游标、`--raw` 或 `--include-reviewed` 都不使用自动整理的跳过记录，仍保留各自原有筛选规则。游标只是读取位置，不能证明前文已处理。

`edit --batch` 拒绝目标之外的段落补丁，包括只读上下文；仍保留原文、时间戳、顺序和历史备份。文稿读取后被修改，即使修订号没有增加，摘要检查也会拒绝陈旧补丁。此时重新读取批次并依据新正文调整补丁，不复用旧版本强行覆盖。导出文件路径变化不会令批次过期。

进度与笔记保存在文稿旁的 `<文件名>.editing-progress.json`，绑定该文稿路径与来源。段落正文、原始文本、时间、人物归属、校对状态或相关人物定义变化会使对应登记失效；`complete` 将已整理段落改为 `reviewed` 时同步保留有效登记，旧版完成记录也可只读识别这种状态变化；通过 `edit` 切换模式会清除登记，切回自动模式也不能复活旧进度。普通 `edit` 只撤销受影响的登记，不为新修改登记已处理。不要删除侧文件来“重置错误”；损坏或混用其他稿件的侧文件会报错并保留笔记，需先检查。

中断后先运行 `status` 与下面的 `editing-notes`，再省略 `--after` 运行 `batch`。自动模式检查 `editing_progress.remaining_to_edit`，精准模式还需处理原校对统计里的未完成项；即使 `done=true` 或 `next_after=null`，也要核对全文覆盖、人物、元数据、摘要与章节，再执行 `complete`、`validate`、`export`。

## 短笔记续接

`--note` 可选，仅与 `edit --batch` 一起使用，每批最多 1200 字符；记录关键事实、疑点、人物依据和段落 ID，不复写整批对话。正文写回时同时保存笔记，中断后按页读取：

```sh
bash "$PS_SKILL_ROOT/scripts/run.sh" editing-notes data/my-episode/episode.json
bash "$PS_SKILL_ROOT/scripts/run.sh" editing-notes data/my-episode/episode.json --after 3
```

第二条命令的 `3` 替换为上一页返回的数字 `next_after`。默认完整 JSON 上限 6000 字符，可用 `--max-chars` 调整；单条笔记放不下时提高预算，程序不截断。笔记绑定本批全部 `targets`，包括仍待处理或 `needs_review` 的段落；其正文、原文、人物、时间或处理模式变化会使笔记标为 `stale`；仅校对状态变化不使事实笔记过期。旧版笔记按相同内容原则读取，不改写原记录。输出保留修订号、模式和这些段落 ID；已失效笔记只能作为历史线索，须回读正文确认后再用于摘要。笔记覆盖某段不表示该段已有可跳过的处理登记；它也不改变完成依据，不进入导出和分享文件。

## 按需读取原文与全篇收尾

```bash
bash "$PS_SKILL_ROOT/scripts/run.sh" batch data/my-episode/episode.json --raw --include-reviewed --after seg-00012
```

`--raw` 以原始 `raw_text` 替代当前正文视图，不同时发送两份全文。按需回看疑点对应范围，与整理稿及原音频核对；不要因此把原始转写当成已核对事实。省略 `--after` 可从开头读取。

处理全部段落后，用仍有效的笔记生成全片摘要和实际章节，并回读必要段落检查跨批衔接。整集摘要、章节和复核标记仍通过 `edit` 的既有格式设置；即使指定 `--batch`，整集 `content_checked=true` 也要求每个段落均已明确标为 `reviewed`。自动模式在全文整理后运行 `complete`，精准模式用 `complete --basis source_checked` 完成已逐段核验的稿件；用户明确接受当前稿时可用 `complete --basis user_accepted`。详见 [模式与完成状态](schema.md#校对状态)。本地进度与结构校验都不能替代实际整理。
