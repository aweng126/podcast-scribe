# 社区分享与维护

本文面向维护者。用户操作见 [分享、修改与撤稿指引](../skills/podcast-scribe/references/sharing.md#用户完整流程)。

用户提交 Issue → 机器人校验并创建 `community/issue-<编号>` 内容 PR → 维护者审核合并 → Pages 部署。Issue 和附件在审核前就已公开。

## 首次启用

1. **Settings → General → Features**：启用 Issues。
2. **Settings → Actions → General**：允许运行工作流，并在 **Workflow permissions** 勾选 **Allow GitHub Actions to create and approve pull requests**。若被组织策略禁用，联系组织管理员；无需添加 PAT。
3. **Settings → Pages → Build and deployment → Source**：选择 **GitHub Actions**。
4. **Actions → Deploy community reader → Run workflow**：选择 `main`，完成首次部署。后续内容变更合并到 `main` 后自动部署，访问地址以 Pages 设置或部署输出为准。

若创建 PR 被权限设置阻止，按 Issue 反馈调整后重跑该次工作流，已有投稿快照会保留。

## 审核与收录

核对正文、来源、署名、系列归属和 Issue 中的公开分享确认，等待 `Community validation` 通过后手动合并。若检查等待批准，先批准运行；不要启用投稿自动合并。阅读站会展示自动整理、用户接受或来源精校的完成方式，自动模式不额外要求投稿人逐句听音。

数据保存在 `content/episodes/issue-<原投稿编号>.json`，编号不是 PR 编号。机器人自动格式化 JSON，便于 **Files changed** 审核；格式化后超过 4 MiB 的记录改存为清单及同名目录中的 `part-*.bin` 分片。

大稿或需要完整预览时，先确认 PR 只涉及投稿数据及必要的分类文件，再在干净的工作区检出内容 PR（如 `gh pr checkout <PR编号>`），从仓库根目录运行：

```sh
python3 scripts/build_public_site.py
python3 -m http.server 8000 --bind 127.0.0.1 --directory output/site
```

打开 `http://127.0.0.1:8000/` 检查正文、人物和章节。构建会重组分片并验证摘要、格式及重复单集，但不能代替正文审阅；页面按需加载内容，预览需使用 HTTP 服务。

首次有效投稿形成固定快照，编辑 Issue 或重跑工作流不会替换正文；校验失败且尚未生成快照时可修正原表单重试。修订稿按下文新建投稿，不直接修改冻结 JSON 或重算 SHA-256。

## 系列归属维护

Skill 根据官方资料填写系列，维护者审核最终归属；无法确认时可保留“未分类”，不阻止收录。

标准目录为 [`series-catalog.json`](../skills/podcast-scribe/podcast_scribe/assets/series-catalog.json)。新增系列先核实官方名称和来源；同一节目的不同写法加入 `aliases`。更名时保留 `id`，旧名保留为别名；删除或合并系列前先处理引用它的分类覆盖。

纠正单篇归属时，在内容 PR 中编辑 [`content/series-overrides.json`](../content/series-overrides.json)，以**原投稿 Issue 编号**添加映射并保留其他记录。例如：

```json
{
  "schema_version": 1,
  "issues": {
    "17": {
      "series_id": "zhang-xiaojun-business",
      "evidence_url": "https://www.xiaoyuzhoufm.com/podcast/626b46ea9cbbf0451cf5a962"
    }
  }
}
```

替换为实际 Issue 编号、目录 ID 和能证明本期归属的官方链接。撤销错误分类且暂无可靠替代时，用 `inbox` 并附核查来源；原分类正确时无需覆盖。

运行 `python3 scripts/build_public_site.py`，等待 `Community validation` 通过后合并。分类覆盖只影响站点展示与下载，保留原投稿及 SHA-256；已收录稿的分类调整也通过 PR 完成。若机器人重试因人工添加分类文件而拒绝分支，由维护者核对并完成 PR，不覆盖人工修改。

## 修改已收录文稿

1. 用户修订本地稿、重新完成所选模式后，新建分享 Issue，在评论中关联旧 Issue，保持原表单字段不变。同一单集沿用原 `id`。
2. 检出新内容 PR，先保留新旧记录。以 Issue #23 替换 #17 为例：

   ```sh
   python3 scripts/build_public_site.py --check-replacement 17 23
   ```

   此命令只报告原始系列、有效系列与分类覆盖，不修改文件或构建站点。核实是同一单集的修订稿；**新 Issue 不继承旧分类覆盖**。若依据仍适用且新稿仍需纠正，给新编号添加映射；已有新映射须先核实，不能直接覆盖。修改后重跑检查。
3. 在同一 PR 中删除旧 `content/episodes/issue-<旧编号>.json` 及存在的同名分片目录，可一并移除旧分类覆盖。新旧记录同时保留会导致重复单集 ID 校验失败。
4. 运行 `python3 scripts/build_public_site.py`，等待 `Community validation` 通过后合并，确认 Pages 部署成功。

旧稿尚未收录时，关闭旧内容 PR 即可；机器人不会根据评论或 Issue 编辑自动替换固定快照。

## 撤稿与删除

投稿人在原 Issue 留言申请并附阅读链接；无法留言时另建 `[撤稿]` Issue。维护者先核对原投稿账号。关闭或删除 Issue 不会自动撤稿。

- **尚未合并**：关闭内容 PR 和投稿 Issue；若工作流仍在运行，结束后检查是否又生成 PR。
- **已收录**：通过 PR 删除 `content/episodes/issue-<原投稿编号>.json` 及存在的同名分片目录，可同时清理分类覆盖。检查差异与 `Community validation`，通过后合并。
- **确认下线**：等待 `Deploy community reader` 的 build、deploy 均成功，重新加载站点，确认目录、搜索、原阅读链接及 Markdown 下载均不再提供该稿，再回复原 Issue。部署失败时旧站仍可能可用。

重新打开原分享 Issue 不会恢复已撤稿内容，需要重新投稿。

### 站点撤稿与彻底清理的区别

撤稿只移除当前站点内容，不会清除 Issue、附件、PR、分支、Git 历史、旧部署产物或已下载副本。删除 Issue 也不能替代删除内容记录。隐私误传需按具体位置分别处理，参见 [GitHub 敏感数据清理说明](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)；不要承诺普通撤稿会彻底擦除数据。

## 工作流边界

| 工作流 | 职责 |
| --- | --- |
| `Community submission` | 使用 `main` 上的脚本校验并固定投稿，创建内容 PR；附件下载不携带仓库 token。 |
| `Community validation` | 在 PR 中只读校验、构建和测试，不部署。 |
| `Deploy community reader` | 仅部署 `main` 中已合并内容，无需数据库或常驻后端。 |

单稿上限 **512 MiB**；超过 GitHub Issue 附件限制的文件使用公开 Release JSON 资产，步骤见[上传指引](../skills/podcast-scribe/references/sharing.md#4-上传并提交-issue)。大稿按页加载，站内仅搜索其元数据，下载 Markdown 时才获取全文。

整站另有 **1 GB** 的 [GitHub Pages 限额](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)。构建超限会失败并保留原输出；接近上限时须减少内容或迁移托管，不能只提高单稿限制。
