# 社区分享与维护

本地 Markdown/PDF 是默认产物；离线页面和社区分享均为可选功能。安装与使用见 [README](../README.md)。

## 投稿

用户从安装、生成文稿到上传、跟踪上线的完整步骤见 [分享、修改与撤稿指引](../skills/podcast-scribe/references/sharing.md#用户完整流程)。Skill 只生成公开 JSON 和预填 Issue 链接，由用户检查后上传提交。

校验通过后，机器人创建 `community/issue-<编号>` 分支和内容 PR，并在 Issue 留下链接。维护者审核合并后，Pages 自动更新。Issue 和附件在收录审核前就已公开，附件上限为 2 MiB。

一个 Issue 对应一份固定快照。首次有效投稿之后，编辑 Issue 或重跑工作流不会更新 PR 中的正文。需要改稿时新建分享 Issue，在评论中说明原投稿；不要在投稿表单正文中新增字段，解析器要求保留原有五个字段。校验失败且尚未生成快照时，可以修改原表单重试。

## 首次启用

仓库管理员完成以下设置：

1. **Settings → General → Features**：启用 Issues。
2. **Settings → Actions → General**：允许运行本仓库工作流；在 **Workflow permissions** 中启用 **Allow GitHub Actions to create and approve pull requests**。组织策略若禁止此项，需要组织管理员调整。工作流按任务声明所需 token 权限，不需要添加 PAT。
3. **Settings → Pages → Build and deployment → Source**：选择 **GitHub Actions**。
4. 在 **Actions → Deploy community reader → Run workflow** 中选择 `main`，部署首次空目录页面。后续相关 `main` 提交自动部署。

标准访问地址为 `https://aweng126.github.io/podcast-scribe/`；若账号配置了自定义域名，实际地址以 Pages 页面或成功部署的输出为准。只有 Pages 工作流成功后站点才可用。

`Community submission` 创建 PR 若被权限设置阻止，会在 Issue 提示修改上述设置。调整后重跑该次工作流即可恢复；已经生成的快照会保留。

## 审核与收录

审核 PR 的公开 JSON、来源、署名及 Issue 中的公开分享确认，查看 `Community validation` 检查结果，再由维护者合并。机器人创建的 PR 检查可能显示等待批准，维护者需要批准运行。不要启用投稿自动合并。

正式数据保存在 `content/episodes/issue-<编号>.json`，包含公开投稿、来源 Issue、投稿账号和内容 SHA-256。生成的页面及下载文件只作为 Actions 部署产物。构建会拒绝重复单集 ID、重复投稿、错误摘要、额外字段和不安全路径。

本地检查与预览：

```sh
python3 scripts/build_public_site.py --content-dir content/episodes --output-dir output/site
python3 -m http.server 8000 --directory output/site
```

打开 `http://localhost:8000/`。公共站点按需加载正文，预览需要 HTTP 服务；本地离线 HTML 功能仍通过 Skill 的 `site` 命令使用。

## 修改已收录文稿

用户修订本地稿并重新校对后，通过新 Issue 投稿，在评论中提供旧 Issue 链接。新投稿 PR 尚未合并时，维护者切换到它的 `community/issue-<新编号>` 分支，删除旧的 `content/episodes/issue-<旧编号>.json`，将删除提交到同一个 PR 分支。核对新增与删除的是同一单集，再运行检查并合并。

同一单集保留原 `id`，直接同时收录新旧记录会触发重复 ID 校验失败。旧稿还未收录时，关闭旧 PR 即可。机器人不会因为评论或 Issue 正文变化而自动替换已固定内容。

## 撤稿与删除

普通投稿人可在原 Issue 评论中申请撤稿，提供阅读链接；无法留言时另开 `[撤稿]` 普通 Issue。维护者核对请求与原投稿账号。当前没有自动撤稿按钮，也不会因评论、关闭或删除 Issue 而自动删除文章。

**PR 尚未合并：** 关闭对应内容 PR，并关闭投稿 Issue，防止后续误合并。如果工作流仍在运行，处理完成后再检查是否生成了新的 PR。关闭 PR 表示不予收录，不等于抹除分支、PR 差异或附件。

**PR 已合并或文稿已上线：** 以原分享 Issue `#17` 为例，维护者在 GitHub 网页执行：

1. 打开仓库 `main` 下的 `content/episodes/issue-17.json`。这里使用原分享 Issue 编号，不是 PR 编号。
2. 点击文件右上角菜单中的 **Delete file**。
3. 提交说明填写撤稿原因，选择新建分支并创建 PR；确认差异只删除目标文稿记录。删除文件的网页操作见 [GitHub 说明](https://docs.github.com/en/repositories/working-with-files/managing-files/deleting-files-in-a-repository)。
4. 等待 `Community validation` 通过，合并到 `main`。内容目录变更会自动触发 `Deploy community reader`。
5. 等待 **build** 和 **deploy** 均成功，刷新站点，核对目录与搜索不再出现该文章，原阅读链接不能再打开正文，原站内下载链接不可再下载该稿。检查成功后回复原 Issue 说明已下线。

构建器会从当前内容库重新生成目录和搜索索引，不再生成已撤稿的正文与下载；复用输出目录时，还会清理上一轮生成的相应文件。部署失败时，线上旧版本可能仍可访问，不能仅凭删除 PR 合并就报告已撤稿。已经打开的页面可能保留浏览器内存中的旧正文，需要重新加载检查。

已撤稿后重新打开原分享 Issue，不会自动恢复文稿；脚本识别原先已关闭或合并的 PR，要求重新投稿。

### 站点撤稿与彻底清理的区别

| 位置 | 删除公开 JSON 并部署后的结果 |
| --- | --- |
| 当前 Pages 站点 | 目录、搜索、正文和站内 Markdown 下载移除。 |
| 投稿人的本地文件 | 保留，由投稿人自行管理。 |
| Issue、评论、附件、PR 差异与分支 | 不会自动删除，需要按具体位置分别处理。 |
| Git 历史、旧 Actions 产物、他人的下载或克隆 | 不会被普通撤稿清除。 |

本仓库属于个人账号，永久删除 Issue 需仓库所有者操作：打开 Issue，在右侧找到 **Delete issue** 并确认。这与关闭 Issue 不同，也不能替代删除内容记录。[GitHub Issue 删除说明](https://docs.github.com/en/issues/tracking-your-work-with-issues/administering-issues/deleting-an-issue)

如果涉及误传隐私，先记录待处理的链接与位置，再核查 Git 历史、PR 引用、附件及部署产物。不要承诺删除附件链接或整个 Issue 就已删除附件存储。历史重写不是普通撤稿步骤；需要独立评估，必要时联系 GitHub 支持。GitHub 对支持范围和他人副本的限制见 [敏感数据清理说明](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)。

## 工作流边界

- `Community submission` 仅从 `main` 运行维护中的脚本。附件按 JSON 校验，下载请求不携带仓库 token，只接受 GitHub 附件及指定存储地址；自动创建的提交只新增该 Issue 对应的内容文件。
- `Community validation` 在 PR 中只读运行校验、构建和测试，不部署。
- `Deploy community reader` 只部署 `main` 中已合并的数据。无需数据库、常驻服务或浏览器端 GitHub token。

配置依据：[Issue 表单](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-githubs-form-schema)、[Pages 自定义工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)、[GitHub token 与工作流触发](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)。
