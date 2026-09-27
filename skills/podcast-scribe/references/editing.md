# 长文分批整理

这些命令只在本地读写 JSON，不调用文本模型。由运行 Skill 的 Agent 整理文字、核对来源、记录各批笔记，再依据全篇笔记生成摘要与章节。按 [工作流程](workflow.md) 使用 `bash "$PS_SKILL_ROOT/scripts/run.sh"`，由入口选择解释器。以下示例的文稿路径和批次文件名由 Agent 按实际单集替换，无需用户填写。

有原字幕或画面字幕时，先做 [字幕辅助核验](subtitles.md)，用 `subtitle-batch` 只读取差异，再针对相关 ID 获取新的正文 `batch`。对照报告不会代替编辑批次，也不会自动设置已校对；修订后需重新对照以避免使用陈旧证据。

## 读取进度和正文

```bash
bash "$PS_SKILL_ROOT/scripts/run.sh" status data/my-episode/episode.json
bash "$PS_SKILL_ROOT/scripts/run.sh" batch data/my-episode/episode.json --output output/my-episode/batch-001.json
```

`status` 仅给出修订号、各校对状态数量、剩余段落与字符数、未知说话人数量和下一待处理 ID。`edited`、`needs_review`、兼容的旧状态及缺失状态均计为未校对；只有明确的 `reviewed` 被跳过。逐段全部已核对也不会自动确认整集复核标记。

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
bash "$PS_SKILL_ROOT/scripts/run.sh" edit data/my-episode/episode.json --edits output/my-episode/edits-001.json --batch output/my-episode/batch-001.json
bash "$PS_SKILL_ROOT/scripts/run.sh" batch data/my-episode/episode.json --after seg-00012 --output output/my-episode/batch-002.json
```

第二条命令的 `seg-00012` 须替换为上一批实际返回的 `next_after`。每批使用新的输出文件名；已有批次文件不会被覆盖。整理阶段即使段落仍是 `edited`，也可以借助该游标继续读后文，保留每批笔记并核对 ID 覆盖。只有实际核对过的段落才提交 `review_status: "reviewed"`；无需重复其正文。

`edit --batch` 拒绝目标之外的段落补丁，包括只读上下文；仍保留原文、时间戳、顺序和历史备份。文稿读取后被修改，即使修订号没有增加，摘要检查也会拒绝陈旧补丁。此时重新读取批次并依据新正文调整补丁，不复用旧版本强行覆盖。导出文件路径变化不会令批次过期。

中断后先运行 `status`，再省略 `--after` 运行 `batch`，从第一个尚未 `reviewed` 的段落恢复核对。游标只表示读到哪里，不是全篇完成证明；即使最后一批 `next_after=null`，仍须检查 `status.remaining`。整理但未核对的内容会保留在剩余数量中。

## 按需读取原文与全篇收尾

```bash
bash "$PS_SKILL_ROOT/scripts/run.sh" batch data/my-episode/episode.json --raw --include-reviewed --after seg-00012
```

`--raw` 以原始 `raw_text` 替代当前正文视图，不同时发送两份全文。按需回看疑点对应范围，与整理稿及原音频核对；不要因此把原始转写当成已核对事实。省略 `--after` 可从开头读取。

记录批次笔记时保留主题、事实、疑点、人物核验依据和相关段落 ID，避免复写整批对话。处理全部段落后，用笔记生成全片摘要和实际章节，并回读必要段落检查跨批衔接。整集摘要、章节和复核标记仍通过 `edit` 的既有格式设置；即使指定 `--batch`，整集 `content_checked=true` 也要求每个段落均已明确标为 `reviewed`。结构校验不能替代语义或人物归属核对。
