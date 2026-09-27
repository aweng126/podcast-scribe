(() => {
  "use strict";
  const data = JSON.parse(document.getElementById("transcript-data").textContent);
  const episodes = data.episodes;
  const hosted = data.mode === "public";
  const episodeCache = new Map();
  let searchIndexPromise;
  let routeGeneration = 0;
  const main = document.getElementById("content");
  const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, character => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[character]));
  const safe = escapeHTML;
  const route = (kind, id) => `#/${kind}/${encodeURIComponent(id)}`;
  const stamp = seconds => {
    const total = Math.floor(Number(seconds) || 0);
    const minutes = Math.floor(total / 60);
    return total >= 3600 ? `${Math.floor(total / 3600)}:${String(minutes % 60).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}` : `${String(minutes).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
  };
  const duration = episode => episode.duration_seconds ? `${Math.ceil(episode.duration_seconds / 60)} 分钟` : "时长待补充";
  const date = episode => episode.published_at ? episode.published_at.slice(0, 10).replaceAll("-", ".") : "日期待补充";
  const text = value => safe(value).replaceAll("\n", "<br>");
  const reviewState = episode => {
    const review = episode.review || {};
    const complete = Boolean(review.speakers_confirmed && review.content_checked);
    const precise = review.mode === "precise";
    const label = complete ? ({automated: "自动整理完成", user_accepted: "用户已确认采用当前稿"}[review.basis] || "人物与内容已校对") : precise ? "精准校对进行中" : "自动整理处理中";
    const note = complete ? `${label}。` : precise ? `${review.speakers_confirmed ? "段落说话人归属已确认。" : "段落说话人归属待复核。"}${review.content_checked ? "文稿已校对。" : "文字存疑处请结合来源核对。"}` : "文稿仍在整理中。";
    return {complete, precise, label, note};
  };
  const badge = episode => episode.is_demo ? '<span class="badge">演示内容</span>' : episode.status === "draft" ? '<span class="badge">未发布</span>' : "";
  const seriesMap = new Map();
  episodes.forEach(episode => {
    const id = episode.series.id;
    if (!seriesMap.has(id)) seriesMap.set(id, {...episode.series, episodes: []});
    seriesMap.get(id).episodes.push(episode);
  });
  const series = [...seriesMap.values()];
  const byDate = items => [...items].sort((a, b) => b.published_at.localeCompare(a.published_at));
  const empty = (title, description, link = "") => `<div class="empty-state"><span class="empty-icon" aria-hidden="true">〔 〕</span><h2>${safe(title)}</h2><p>${safe(description)}</p>${link}</div>`;
  const shareLink = hosted ? '<a class="button button-primary" href="https://github.com/aweng126/podcast-scribe/issues/new?template=share.yml" target="_blank" rel="noopener noreferrer">投稿一篇文稿 ↗</a>' : "";
  const sectionHeading = (eyebrow, title, count) => `<div class="section-heading"><div><span class="eyebrow">${safe(eyebrow)}</span><h2>${safe(title)}</h2></div>${count !== undefined ? `<span class="section-count">${count} ${eyebrow === "SERIES" ? "个系列" : "篇文稿"}</span>` : ""}</div>`;
  const episodeCard = (episode, index, showSeries = true) => `<article class="episode-row">
    <span class="episode-number" aria-hidden="true">${String(index + 1).padStart(2, "0")}</span>
    <div class="episode-details"><div class="episode-meta">${showSeries ? `<a href="${route("series", episode.series.id)}">${safe(episode.series.title)}</a><span>·</span>` : ""}<span>${safe(date(episode))}</span><span>·</span><span>${duration(episode)}</span>${badge(episode)}</div>
    <h3><a href="${route("episode", episode.id)}">${safe(episode.title)}</a></h3>
    <p>${safe(episode.description || episode.summary[0] || "打开完整文稿，阅读这场对话。")}</p>
    <div class="episode-bottom"><span>${safe(episode.speakers.map(speaker => speaker.name).join(" / ") || "说话人待确认")}</span><a class="read-link" href="${route("episode", episode.id)}" aria-label="阅读：${safe(episode.title)}">阅读全文 <span aria-hidden="true">↗</span></a></div></div>
  </article>`;

  function home() {
    document.title = "听稿 · 播客里的好对话";
    main.innerHTML = `<section class="hero"><div class="hero-copy"><span class="eyebrow"><span class="accent-line"></span> GOOD CONVERSATIONS, IN WORDS</span><h1>把声音，<br>留在<span>纸上。</span></h1><p>读一场完整的对话。<br>循着章节找到观点，跟着文字重新思考。</p><a class="text-link" href="#/search">寻找你感兴趣的话题 <span aria-hidden="true">↗</span></a></div><div class="hero-art" aria-hidden="true"><div class="art-caption">THE READING ROOM <span>01 — ∞</span></div><div class="art-quote">“</div><div class="art-lines"><i></i><i></i><i></i><i></i><i></i></div><div class="art-bottom">声音有回响<br>文字有留白 <span>↙</span></div></div></section>
    ${hosted ? `<section class="community-note"><p>这里收录社区分享、经维护者审核的播客整理稿。你也可以分享整理完成的文稿，投稿 Issue 及附件会公开。</p>${shareLink}</section>` : ""}
    <section class="library-section" aria-label="播客系列">${sectionHeading("SERIES", "从一个系列开始", series.length)}${series.length ? `<div class="series-grid">${series.map((item, index) => `<a class="series-card tone-${index % 4}" href="${route("series", item.id)}"><div class="series-cover"><span class="series-cover-label">PODCAST SERIES</span><span class="series-cover-title">${safe(item.title)}</span><span class="series-cover-foot">${String(index + 1).padStart(2, "0")} <span aria-hidden="true">↗</span></span></div><div class="series-card-info"><h3>${safe(item.title)}</h3><span>${item.episodes.length} 篇</span><p>${safe(item.description || "收录这个系列的完整对话与章节整理。")}</p></div></a>`).join("")}</div>` : empty("第一场对话，正在路上", hosted ? "第一篇投稿审核通过后，完整文稿会出现在这里。" : "发布第一篇整理完成的文稿后，系列和单集会出现在这里。")}</section>
    ${episodes.length ? `<section class="library-section">${sectionHeading("LATEST READINGS", "最近收录", episodes.length)}<div class="episode-list">${byDate(episodes).slice(0, 6).map((episode, index) => episodeCard(episode, index)).join("")}</div>${episodes.length > 6 ? '<a class="button button-quiet" href="#/search">浏览全部文稿 ↗</a>' : ""}</section>` : ""}`;
  }

  function seriesPage(id) {
    const item = seriesMap.get(id);
    if (!item) return notFound();
    document.title = `${item.title} · 听稿`;
    main.innerHTML = `<div class="page-content"><nav class="breadcrumb" aria-label="面包屑"><a href="#/">所有系列</a><span aria-hidden="true">/</span><span>${safe(item.title)}</span></nav><section class="series-hero"><div><span class="eyebrow">PODCAST SERIES</span><h1>${safe(item.title)}</h1><p>${text(item.description || "这里收录本系列的完整对话，附摘要、章节与原视频时间戳。")}</p><div class="series-stats"><span>${item.episodes.length} 篇文稿</span><span>${Math.ceil(item.episodes.reduce((sum, episode) => sum + episode.duration_seconds, 0) / 60)} 分钟对话</span></div></div><span class="series-hero-mark" aria-hidden="true">“</span></section><section>${sectionHeading("ALL EPISODES", "全部单集", item.episodes.length)}<div class="episode-list">${byDate(item.episodes).map((episode, index) => episodeCard(episode, index, false)).join("")}</div></section></div>`;
  }

  function originalLink(episode, seconds) {
    if (!episode.source.url) return "";
    try {
      const url = new URL(episode.source.url);
      if (!["http:", "https:"].includes(url.protocol)) return "";
      if (seconds !== undefined && /(^|\.)bilibili\.com$/i.test(url.hostname)) url.searchParams.set("t", String(Math.floor(seconds)));
      return url.href;
    } catch (_) { return ""; }
  }

  function episodePage(episode, changePage) {
    document.title = `${episode.title} · 听稿`;
    const speakers = new Map(episode.speakers.map((speaker, index) => [speaker.id, {...speaker, color: index % 6}]));
    const sourceURL = originalLink(episode);
    const segmentIndex = new Map(episode.segments.map((segment, index) => [segment.id, index]));
    const chapterTarget = chapter => {
      if (segmentIndex.has(chapter.segment_id)) return segmentIndex.get(chapter.segment_id);
      const containing = episode.segments.findIndex(segment => segment.start <= chapter.start && chapter.start < segment.end);
      if (containing >= 0) return containing;
      const index = episode.segments.findIndex(segment => segment.start >= chapter.start);
      return index < 0 ? Math.max(0, episode.segments.length - 1) : index;
    };
    const chapterAt = new Map();
    episode.chapters.filter(chapter => !episode.paginated || chapter.page === episode.page_index).forEach(chapter => {
      const index = chapterTarget(chapter);
      if (!chapterAt.has(index)) chapterAt.set(index, []);
      chapterAt.get(index).push(chapter);
    });
    const paging = episode.paginated ? `<nav class="episode-actions" aria-label="文稿分页"><button class="button" data-page="${episode.page_index - 1}" ${episode.page_index === 0 ? "disabled" : ""}>上一页</button><span>第 ${episode.page_index + 1} / ${episode.pages.length} 页</span><button class="button" data-page="${episode.page_index + 1}" ${episode.page_index + 1 === episode.pages.length ? "disabled" : ""}>下一页</button></nav>` : "";
    const review = reviewState(episode);
    const reviewNote = review.note;
    const draftNotice = episode.is_demo || !review.complete ? `<div class="draft-notice"><strong>${episode.is_demo ? "演示内容" : review.label}</strong><span>${episode.is_demo ? "此页用于检验阅读与导出效果，不代表真实节目内容。" : reviewNote}</span></div>` : "";
    const references = (episode.references || []).filter(reference => {
      try { return ["https:", "http:"].includes(new URL(reference.url).protocol); }
      catch (_) { return false; }
    });
    const referenceSection = references.length ? `<section class="summary-block" aria-labelledby="references-title"><span class="eyebrow">SOURCES &amp; VERIFICATION</span><h2 id="references-title">来源与人物核验</h2><ul>${references.map(reference => `<li><a class="text-link" href="${safe(reference.url)}" target="_blank" rel="noopener noreferrer">${safe(reference.title || "核验来源")} <span aria-hidden="true">↗</span></a>${reference.note ? `<p>${text(reference.note)}</p>` : ""}</li>`).join("")}</ul></section>` : "";
    const issueURL = hosted && /^https:\/\/github\.com\/aweng126\/podcast-scribe\/issues\/[1-9][0-9]*$/.test(episode.provenance?.issue_url || "") ? episode.provenance.issue_url : "";
    const submissionNote = hosted ? `<section class="submission-note" aria-label="投稿信息"><p>投稿署名：${text(episode.attribution || "")}</p>${issueURL ? `<a class="text-link" href="${safe(issueURL)}" target="_blank" rel="noopener noreferrer">查看投稿与反馈 ↗</a><p class="muted">投稿账号：${safe(episode.provenance.submitter)}</p>` : ""}</section>` : "";
    main.innerHTML = `<article class="reader"><nav class="breadcrumb" aria-label="面包屑"><a href="#/">所有系列</a><span aria-hidden="true">/</span><a href="${route("series", episode.series.id)}">${safe(episode.series.title)}</a><span aria-hidden="true">/</span><span>本期文稿</span></nav>${draftNotice}<header class="episode-header"><a class="eyebrow series-name" href="${route("series", episode.series.id)}">${safe(episode.series.title)}</a><h1>${safe(episode.title)}</h1>${episode.description ? `<p class="episode-description">${text(episode.description)}</p>` : ""}<div class="episode-meta"><span>${safe(date(episode))}</span><span>·</span><span>${duration(episode)}</span><span>·</span><span>${episode.segment_count || episode.turns.length} 段对话</span><span>·</span><span>${safe(review.label)}</span>${badge(episode)}</div><div class="episode-actions">${sourceURL ? `<a class="button button-primary" href="${safe(sourceURL)}" target="_blank" rel="noopener noreferrer">回到原视频 <span aria-hidden="true">↗</span></a>` : '<span class="unavailable">原视频链接待补充</span>'}${episode.downloads.markdown ? `<a class="button" href="${safe(episode.downloads.markdown)}" download>↓ Markdown</a>` : ""}${episode.downloads.pdf ? `<a class="button" href="${safe(episode.downloads.pdf)}" download>↓ PDF</a>` : ""}${episode.paginated ? `<button class="button" data-download-full>↓ 完整 Markdown（正文约 ${(episode.text_bytes / 1048576).toFixed(1)} MiB）</button><span data-download-status role="status"></span>` : ""}${!episode.paginated && !episode.downloads.markdown && !episode.downloads.pdf ? '<span class="unavailable">下载文件尚未生成</span>' : ""}</div></header>
    <div class="reader-layout"><aside class="reader-sidebar"><details class="chapter-panel" open><summary>本期目录 <span aria-hidden="true">⌄</span></summary><nav aria-label="本期章节"><button class="chapter-link" data-scroll="episode-summary"><span class="chapter-time">INTRO</span><span>本期摘要</span></button>${episode.chapters.map((chapter, index) => `<button class="chapter-link" ${episode.paginated && chapter.page !== episode.page_index ? `data-page="${chapter.page}" data-segment="${safe(chapter.segment_id)}"` : `data-scroll="segment-${chapterTarget(chapter)}"`}><span class="chapter-time">${stamp(chapter.start)}</span><span>${safe(chapter.title || `章节 ${index + 1}`)}</span></button>`).join("")}${!episode.chapters.length ? '<button class="chapter-link" data-scroll="transcript"><span class="chapter-time">TEXT</span><span>完整对话</span></button>' : ""}</nav></details><section class="speaker-panel"><h2>参与对话</h2>${episode.speakers.length ? episode.speakers.map((speaker, index) => `<div class="speaker-entry"><span class="speaker-dot speaker-${index % 6}" aria-hidden="true"></span><div><strong>${safe(speaker.name)}</strong>${speaker.role ? `<small>${safe(speaker.role)}</small>` : ""}</div></div>`).join("") : '<p class="muted">人物信息待确认</p>'}${review.complete ? "" : `<p class="editorial-note">${reviewNote}</p>`}</section></aside>
    <div class="reader-body"><section class="summary-block" id="episode-summary" tabindex="-1"><span class="eyebrow">IN THIS EPISODE</span><h2>这期聊了什么</h2>${episode.summary.length ? `<ul>${episode.summary.map(item => `<li>${text(item)}</li>`).join("")}</ul>` : '<p class="muted">本期摘要尚未生成，可以直接阅读下方完整对话。</p>'}</section><section class="transcript" id="transcript" tabindex="-1">${paging}<div class="transcript-heading"><h2>完整对话</h2><span>整理稿</span></div><p class="transcript-note">保留完整对话，整理口头重复，同一人的连续发言合并展示。点击时间戳可回到原视频核对。</p>${episode.turns.length ? episode.turns.map(turn => {
      const speaker = speakers.get(turn.speaker_id);
      const url = originalLink(episode, turn.start);
      const headings = (chapterAt.get(turn.segment_indices[0]) || []).map(chapter => `<h3 class="transcript-chapter">${safe(chapter.title || "章节")}</h3>`).join("");
      const content = turn.text_parts.map((part, index) => `<span id="segment-${turn.segment_indices[index]}" class="segment-anchor" tabindex="-1">${text(part)}</span>`).join("");
      return `${headings}<div class="dialogue"><div class="dialogue-label"><span class="speaker-dot speaker-${speaker ? speaker.color : "unknown"}" aria-hidden="true"></span><strong>${safe(speaker ? speaker.name : "未识别说话人")}</strong>${url ? `<a class="timestamp" href="${safe(url)}" target="_blank" rel="noopener noreferrer" aria-label="在原视频中查看 ${stamp(turn.start)}">${stamp(turn.start)} ↗</a>` : `<span class="timestamp">${stamp(turn.start)}</span>`}${review.precise && turn.needs_review ? '<span class="segment-review">待核对</span>' : ""}</div><p>${content}</p></div>`;
    }).join("") : empty("对话文稿尚未生成", "音视频完成转写与整理后，完整对话会显示在这里。")}</section>${paging}${referenceSection}${submissionNote}<div class="reading-end"><span aria-hidden="true">◆</span><p>这场对话，读到这里。</p>${sourceURL ? `<a class="text-link" href="${safe(sourceURL)}" target="_blank" rel="noopener noreferrer">去原视频听一听 ↗</a>` : ""}${episode.source.author ? `<small>原作者：${safe(episode.source.author)}</small>` : ""}</div></div></div></article>`;
    if (episode.paginated) {
      main.querySelectorAll("[data-page]").forEach(button => button.addEventListener("click", () => changePage(Number(button.dataset.page), button.dataset.segment)));
      main.querySelector("[data-download-full]").addEventListener("click", event => downloadPagedMarkdown(episode, event.currentTarget));
    }
    main.querySelectorAll("[data-scroll]").forEach(button => button.addEventListener("click", () => {
      const target = document.getElementById(button.dataset.scroll);
      if (target) { target.scrollIntoView({behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start"}); target.focus({preventScroll: true}); }
      main.querySelectorAll(".chapter-link").forEach(link => link.removeAttribute("aria-current"));
      button.setAttribute("aria-current", "location");
    }));
  }

  async function fetchJSON(url) {
    if (!/^(?:episodes\/[0-9a-f]{64}(?:-[0-9]{5})?\.json|search-index\.json)$/.test(url)) throw new Error("Invalid library resource");
    const response = await fetch(url, {credentials: "omit"});
    if (!response.ok) throw new Error("Library resource unavailable");
    return response.json();
  }

  async function openEpisode(id, generation) {
    const metadata = episodes.find(item => item.id === id);
    if (!metadata) return notFound();
    if (!hosted) return episodePage(metadata);
    main.innerHTML = `<div class="page-content" role="status" aria-live="polite">${empty("正在打开文稿", "请稍候…")}</div>`;
    try {
      if (!episodeCache.has(id)) {
        episodeCache.set(id, fetchJSON(metadata.data_url).then(episode => {
          if (episode.id !== id || !Array.isArray(episode.segments) || !Array.isArray(episode.turns)) throw new Error("Invalid episode");
          return episode;
        }).catch(error => { episodeCache.delete(id); throw error; }));
      }
      const episode = await episodeCache.get(id);
      if (generation !== routeGeneration) return;
      if (episode.paginated) await openPage(episode, 0, generation);
      else episodePage(episode);
      main.focus({preventScroll: true});
    } catch (_) {
      if (generation !== routeGeneration) return;
      main.innerHTML = `<div class="page-content">${empty("文稿暂时未能加载", "请检查网络连接后重试。", '<button class="button button-primary" data-retry>重新加载</button> <a class="text-link" href="#/">返回首页 ↗</a>')}</div>`;
      main.querySelector("[data-retry]").addEventListener("click", render);
    }
  }

  let pageGeneration = 0;
  async function openPage(episode, index, generation, segmentID) {
    if (!Number.isInteger(index) || index < 0 || index >= episode.pages.length) return;
    const current = ++pageGeneration;
    main.innerHTML = `<div class="page-content" role="status">正在打开第 ${index + 1} 页…</div>`;
    try {
      const page = await fetchJSON(episode.pages[index].url);
      if (generation !== routeGeneration || current !== pageGeneration) return;
      if (!Array.isArray(page.segments) || page.segments.length !== episode.pages[index].segment_count) throw Error("Invalid page");
      // A page is deliberately not cached: leaving it releases its text and DOM.
      const turns = readingTurns(page.segments, episode.speakers);
      episodePage({...episode, page_index: index, segments: page.segments, turns},
        (next, target) => openPage(episode, next, generation, target));
      if (segmentID) {
        const position = page.segments.findIndex(segment => segment.id === segmentID);
        document.getElementById(`segment-${position}`)?.scrollIntoView({block: "start"});
      } else window.scrollTo(0, 0);
    } catch (_) {
      if (generation !== routeGeneration || current !== pageGeneration) return;
      main.innerHTML = `<div class="page-content">此页未能加载。<button class="button" data-page-retry>重试</button> <a href="#/">返回首页</a></div>`;
      main.querySelector("[data-page-retry]").addEventListener("click", () => openPage(episode, index, generation, segmentID));
    }
  }

  function joinPrefix(previous, value) {
    const left = previous.slice(-1), right = value.slice(0, 1);
    return previous && value && /^[A-Za-z0-9]$/.test(right) && /^[A-Za-z0-9.,!?;:)"']$/.test(left) ? " " : "";
  }

  function readingTurns(segments, speakers) {
    const known = new Set(speakers.map(speaker => speaker.id));
    const turns = [];
    for (let index = 0; index < segments.length; index++) {
      const segment = segments[index];
      if (!turns.length || !known.has(segment.speaker_id) || turns[turns.length - 1].speaker_id !== segment.speaker_id) {
        turns.push({speaker_id: segment.speaker_id, start: segment.start, end: segment.end,
          segment_indices: [], text_parts: [], text: "", needs_review: false, previous: ""});
      }
      const turn = turns[turns.length - 1], value = segment.text.trim();
      const part = joinPrefix(turn.previous, value) + value;
      turn.segment_indices.push(index); turn.text_parts.push(part); turn.text += part;
      turn.end = Math.max(turn.end, segment.end); turn.needs_review ||= segment.review_status !== "reviewed";
      if (value) turn.previous = value;
    }
    return turns;
  }

  async function downloadPagedMarkdown(episode, button) {
    button.disabled = true;
    const status = main.querySelector("[data-download-status]");
    const speakers = new Map(episode.speakers.map(speaker => [speaker.id, speaker.name]));
    const escapeMD = value => String(value || "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replace(/[\\`*_\[\]#]/g, "\\$&");
    const mdURL = value => String(value).replaceAll("(", "%28").replaceAll(")", "%29");
    const chapterBySegment = new Map();
    episode.chapters.forEach(chapter => {
      if (!chapterBySegment.has(chapter.segment_id)) chapterBySegment.set(chapter.segment_id, []);
      chapterBySegment.get(chapter.segment_id).push(chapter);
    });
    // Only explicit download materializes the full manuscript. Blob parts let
    // completed pages release their JSON while keeping the download available.
    const parts = [`# ${escapeMD(episode.title)}\n\n- **系列**：${escapeMD(episode.series.title)}\n- **作者**：${escapeMD(episode.source.author)}\n- **发布日期**：${escapeMD(episode.published_at)}\n- **时长**：${stamp(episode.duration_seconds)}\n- **来源**：[原视频](${mdURL(episode.source.url)})\n- **整理状态**：${escapeMD(reviewState(episode).label)}\n\n${escapeMD(episode.description)}\n\n## 本期人物\n\n${episode.speakers.map(speaker => "- " + escapeMD(speaker.name) + (speaker.role ? "（" + escapeMD(speaker.role) + "）" : "")).join("\n")}\n\n## 本期摘要\n\n${episode.summary.map(item => "- " + escapeMD(item)).join("\n")}\n\n## 章节目录\n\n${episode.chapters.map(chapter => `- [${stamp(chapter.start)} ${escapeMD(chapter.title)}](#segment-${chapter.segment_index + 1})`).join("\n")}\n\n## 完整对话\n\n`];
    let lastSpeaker, previous = "", hasTurn = false;
    try {
      for (let index = 0; index < episode.pages.length; index++) {
        status.textContent = `正在准备下载 ${index + 1}/${episode.pages.length}…`;
        const page = await fetchJSON(episode.pages[index].url);
        let markdown = "";
        for (let offset = 0; offset < page.segments.length; offset++) {
          const segment = page.segments[offset], value = segment.text.trim();
          const segmentIndex = episode.pages[index].segment_start + offset + 1;
          const startsTurn = !hasTurn || !speakers.has(segment.speaker_id) || lastSpeaker !== segment.speaker_id;
          if (startsTurn) {
            markdown += `${hasTurn ? "\n\n" : ""}<a id="segment-${segmentIndex}"></a>\n\n`;
            for (const chapter of chapterBySegment.get(segment.id) || []) markdown += `### ${escapeMD(chapter.title)}\n\n`;
            markdown += `**[${stamp(segment.start)}](${mdURL(originalLink(episode, segment.start))}) · ${escapeMD(speakers.get(segment.speaker_id))}**\n\n`;
            previous = "";
          } else markdown += `<a id="segment-${segmentIndex}"></a>`;
          markdown += escapeMD(joinPrefix(previous, value) + value);
          if (value) previous = value;
          lastSpeaker = segment.speaker_id; hasTurn = true;
        }
        parts.push(new Blob([markdown], {type: "text/markdown;charset=utf-8"}));
      }
      if (episode.references.length) parts.push(`\n\n## 人物与节目来源核验\n\n${episode.references.map(reference => `- [${escapeMD(reference.title)}](${mdURL(reference.url)})`).join("\n")}\n`);
      parts.push(`\n\n---\n\n本文依据提供的转写资料整理。摘要与章节属于辅助阅读内容。\n\n## 投稿信息\n\n投稿署名：${escapeMD(episode.attribution)}\n\n投稿账号：${escapeMD(episode.provenance.submitter)}\n\n投稿记录：${episode.provenance.issue_url}\n`);
      const url = URL.createObjectURL(new Blob(parts, {type: "text/markdown;charset=utf-8"}));
      const link = document.createElement("a");
      link.href = url; link.download = "transcript.md"; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      status.textContent = "下载已准备。";
    } catch (_) { status.textContent = "下载未完成，请检查连接后重试。"; }
    finally { button.disabled = false; }
  }

  function loadSearchIndex() {
    if (!searchIndexPromise) {
      searchIndexPromise = fetchJSON(data.search_url).then(index => {
        if (index.schema_version !== 1 || !Array.isArray(index.episodes)) throw new Error("Invalid search index");
        return new Map(index.episodes.map(episode => [episode.id, String(episode.text).toLocaleLowerCase()]));
      }).catch(error => { searchIndexPromise = undefined; throw error; });
    }
    return searchIndexPromise;
  }

  function searchPage(query) {
    const generation = routeGeneration;
    let searchGeneration = 0;
    document.title = "搜索文稿 · 听稿";
    main.innerHTML = `<div class="page-content search-page"><span class="eyebrow">FIND A CONVERSATION</span><h1>你想读些什么？</h1><form class="search-form" role="search"><label class="sr-only" for="search-input">搜索标题、系列、人物和文稿内容</label><span aria-hidden="true">⌕</span><input type="search" id="search-input" name="q" placeholder="搜索标题、系列、人物或一句话…" value="${safe(query)}" autocomplete="off"><button type="submit">搜索 <span aria-hidden="true">↗</span></button></form><p class="search-hint">在 ${episodes.length} 篇文稿中，寻找值得重读的对话。${data.paginated_episodes ? `其中 ${data.paginated_episodes} 篇大稿仅搜索标题、系列、人物和摘要，正文请打开后按页阅读。` : ""}</p><section id="search-results" aria-label="搜索结果"></section></div>`;
    const input = document.getElementById("search-input");
    const results = document.getElementById("search-results");
    async function update(value) {
      const request = ++searchGeneration;
      const terms = value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
      let index;
      if (hosted && terms.length) {
        results.innerHTML = '<p role="status" aria-live="polite">正在搜索文稿…</p>';
        try { index = await loadSearchIndex(); }
        catch (_) {
          if (generation !== routeGeneration || request !== searchGeneration) return;
          results.innerHTML = empty("搜索暂时不可用", "请检查网络连接后，再次提交搜索。 ");
          return;
        }
      }
      if (generation !== routeGeneration || request !== searchGeneration) return;
      const matches = byDate(episodes).filter(episode => {
        const haystack = hosted ? (index?.get(episode.id) || "") : [episode.title, episode.description, episode.series.title, ...episode.summary, ...episode.speakers.map(speaker => speaker.name), ...episode.turns.map(turn => turn.text)].join(" ").toLocaleLowerCase();
        return terms.every(term => haystack.includes(term));
      });
      results.innerHTML = `<div class="results-heading" role="status" aria-live="polite">${terms.length ? `找到 ${matches.length} 篇相关文稿` : `全部文稿 · ${matches.length} 篇`}</div>${matches.length ? `<div class="episode-list">${matches.map((episode, index) => episodeCard(episode, index)).join("")}</div>` : empty(terms.length ? "还没有找到这场对话" : "文稿库正在准备中", terms.length ? "试试更短的关键词，或搜索系列名称、人物姓名。" : "第一篇文稿发布后，就可以在这里搜索。", '<a class="text-link" href="#/">返回系列首页 ↗</a>')}`;
    }
    input.addEventListener("input", () => update(input.value));
    main.querySelector("form").addEventListener("submit", event => {
      event.preventDefault();
      const hash = `#/search?q=${encodeURIComponent(input.value.trim())}`;
      history.replaceState(null, "", hash);
      update(input.value);
    });
    update(query);
    input.focus({preventScroll: true});
  }

  function notFound() {
    document.title = "页面未找到 · 听稿";
    main.innerHTML = `<div class="page-content">${empty("这篇文稿暂时不在书架上", "它可能尚未发布，或链接已经更改。", '<a class="button button-primary" href="#/">返回系列首页</a>')}</div>`;
  }
  function render() {
    const hash = location.hash.slice(1) || "/";
    if (hash === "content") { main.focus(); return; }
    const generation = ++routeGeneration;
    const [path, parameters] = hash.split("?");
    const parts = path.split("/").filter(Boolean);
    try {
      if (!parts.length) home();
      else if (parts[0] === "series" && parts[1]) seriesPage(decodeURIComponent(parts[1]));
      else if (parts[0] === "episode" && parts[1]) openEpisode(decodeURIComponent(parts[1]), generation);
      else if (parts[0] === "search") searchPage(new URLSearchParams(parameters || "").get("q") || "");
      else notFound();
    } catch (error) { notFound(); }
    window.scrollTo(0, 0);
    if (parts[0] !== "search") main.focus({preventScroll: true});
  }
  if (data.preview) document.getElementById("preview-banner").innerHTML = '<div class="preview-banner"><strong>本地预览</strong><span>此版本可能包含未发布文稿；演示内容会单独标记。</span></div>';
  document.querySelector(".skip-link").addEventListener("click", event => { event.preventDefault(); main.focus(); });
  window.addEventListener("hashchange", render);
  render();
})();
