(() => {
  "use strict";
  const data = JSON.parse(document.getElementById("transcript-data").textContent);
  const episodes = data.episodes;
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
  const badge = episode => episode.is_demo ? '<span class="badge">演示内容</span>' : episode.status === "draft" ? '<span class="badge">草稿预览</span>' : "";
  const seriesMap = new Map();
  episodes.forEach(episode => {
    const id = episode.series.id;
    if (!seriesMap.has(id)) seriesMap.set(id, {...episode.series, episodes: []});
    seriesMap.get(id).episodes.push(episode);
  });
  const series = [...seriesMap.values()];
  const byDate = items => [...items].sort((a, b) => b.published_at.localeCompare(a.published_at));
  const empty = (title, description, link = "") => `<div class="empty-state"><span class="empty-icon" aria-hidden="true">〔 〕</span><h2>${safe(title)}</h2><p>${safe(description)}</p>${link}</div>`;
  const sectionHeading = (eyebrow, title, count) => `<div class="section-heading"><div><span class="eyebrow">${safe(eyebrow)}</span><h2>${safe(title)}</h2></div>${count !== undefined ? `<span class="section-count">${count} ${eyebrow === "SERIES" ? "个系列" : "篇文稿"}</span>` : ""}</div>`;
  const episodeCard = (episode, index, showSeries = true) => `<article class="episode-row">
    <span class="episode-number" aria-hidden="true">${String(index + 1).padStart(2, "0")}</span>
    <div class="episode-details"><div class="episode-meta">${showSeries ? `<a href="${route("series", episode.series.id)}">${safe(episode.series.title)}</a><span>·</span>` : ""}<span>${safe(date(episode))}</span><span>·</span><span>${duration(episode)}</span>${badge(episode)}</div>
    <h3><a href="${route("episode", episode.id)}">${safe(episode.title)}</a></h3>
    <p>${safe(episode.description || episode.summary[0] || "打开完整文稿，阅读这场对话。")}</p>
    <div class="episode-bottom"><span>${safe(episode.speakers.map(speaker => speaker.name).join(" / ") || "说话人待确认")}</span><a class="read-link" href="${route("episode", episode.id)}" aria-label="阅读：${safe(episode.title)}">阅读全文 <span aria-hidden="true">↗</span></a></div></div>
  </article>`;

  function home() {
    document.title = "文稿集 · 播客里的好对话";
    main.innerHTML = `<section class="hero"><div class="hero-copy"><span class="eyebrow"><span class="accent-line"></span> GOOD CONVERSATIONS, IN WORDS</span><h1>把声音，<br>留在<span>纸上。</span></h1><p>读一场完整的对话。<br>循着章节找到观点，跟着文字重新思考。</p><a class="text-link" href="#/search">寻找你感兴趣的话题 <span aria-hidden="true">↗</span></a></div><div class="hero-art" aria-hidden="true"><div class="art-caption">THE READING ROOM <span>01 — ∞</span></div><div class="art-quote">“</div><div class="art-lines"><i></i><i></i><i></i><i></i><i></i></div><div class="art-bottom">声音有回响<br>文字有留白 <span>↙</span></div></div></section>
    <section class="library-section" aria-label="播客系列">${sectionHeading("SERIES", "从一个系列开始", series.length)}${series.length ? `<div class="series-grid">${series.map((item, index) => `<a class="series-card tone-${index % 4}" href="${route("series", item.id)}"><div class="series-cover"><span class="series-cover-label">PODCAST SERIES</span><span class="series-cover-title">${safe(item.title)}</span><span class="series-cover-foot">${String(index + 1).padStart(2, "0")} <span aria-hidden="true">↗</span></span></div><div class="series-card-info"><h3>${safe(item.title)}</h3><span>${item.episodes.length} 篇</span><p>${safe(item.description || "收录这个系列的完整对话与章节整理。")}</p></div></a>`).join("")}</div>` : empty("第一场对话，正在路上", "发布第一篇经过校对的文稿后，系列和单集会出现在这里。")}</section>
    ${episodes.length ? `<section class="library-section">${sectionHeading("LATEST READINGS", "最近收录", episodes.length)}<div class="episode-list">${byDate(episodes).slice(0, 6).map((episode, index) => episodeCard(episode, index)).join("")}</div>${episodes.length > 6 ? '<a class="button button-quiet" href="#/search">浏览全部文稿 ↗</a>' : ""}</section>` : ""}`;
  }

  function seriesPage(id) {
    const item = seriesMap.get(id);
    if (!item) return notFound();
    document.title = `${item.title} · 文稿集`;
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

  function episodePage(id) {
    const episode = episodes.find(item => item.id === id);
    if (!episode) return notFound();
    document.title = `${episode.title} · 文稿集`;
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
    episode.chapters.forEach(chapter => {
      const index = chapterTarget(chapter);
      if (!chapterAt.has(index)) chapterAt.set(index, []);
      chapterAt.get(index).push(chapter);
    });
    const reviewNote = `${episode.review.speakers_confirmed ? "段落说话人归属已确认。" : "段落说话人归属待复核。"}${episode.review.content_checked ? "文稿已校对。" : "文字存疑处请结合来源核对。"}`;
    const draftNotice = episode.status !== "published" || episode.is_demo ? `<div class="draft-notice"><strong>${episode.is_demo ? "演示内容" : "草稿预览"}</strong><span>${episode.is_demo ? "此页用于检验阅读与导出效果，不代表真实节目内容。" : `此文稿尚未发布；${reviewNote}`}</span></div>` : "";
    const references = (episode.references || []).filter(reference => {
      try { return ["https:", "http:"].includes(new URL(reference.url).protocol); }
      catch (_) { return false; }
    });
    const referenceSection = references.length ? `<section class="summary-block" aria-labelledby="references-title"><span class="eyebrow">SOURCES &amp; VERIFICATION</span><h2 id="references-title">来源与人物核验</h2><ul>${references.map(reference => `<li><a class="text-link" href="${safe(reference.url)}" target="_blank" rel="noopener noreferrer">${safe(reference.title || "核验来源")} <span aria-hidden="true">↗</span></a>${reference.note ? `<p>${text(reference.note)}</p>` : ""}</li>`).join("")}</ul></section>` : "";
    main.innerHTML = `<article class="reader"><nav class="breadcrumb" aria-label="面包屑"><a href="#/">所有系列</a><span aria-hidden="true">/</span><a href="${route("series", episode.series.id)}">${safe(episode.series.title)}</a><span aria-hidden="true">/</span><span>本期文稿</span></nav>${draftNotice}<header class="episode-header"><a class="eyebrow series-name" href="${route("series", episode.series.id)}">${safe(episode.series.title)}</a><h1>${safe(episode.title)}</h1>${episode.description ? `<p class="episode-description">${text(episode.description)}</p>` : ""}<div class="episode-meta"><span>${safe(date(episode))}</span><span>·</span><span>${duration(episode)}</span><span>·</span><span>${episode.turns.length} 段对话</span>${badge(episode)}</div><div class="episode-actions">${sourceURL ? `<a class="button button-primary" href="${safe(sourceURL)}" target="_blank" rel="noopener noreferrer">回到原视频 <span aria-hidden="true">↗</span></a>` : '<span class="unavailable">原视频链接待补充</span>'}${episode.downloads.markdown ? `<a class="button" href="${safe(episode.downloads.markdown)}" download>↓ Markdown</a>` : ""}${episode.downloads.pdf ? `<a class="button" href="${safe(episode.downloads.pdf)}" download>↓ PDF</a>` : ""}${!episode.downloads.markdown && !episode.downloads.pdf ? '<span class="unavailable">下载文件尚未生成</span>' : ""}</div></header>
    <div class="reader-layout"><aside class="reader-sidebar"><details class="chapter-panel" open><summary>本期目录 <span aria-hidden="true">⌄</span></summary><nav aria-label="本期章节"><button class="chapter-link" data-scroll="episode-summary"><span class="chapter-time">INTRO</span><span>本期摘要</span></button>${episode.chapters.map((chapter, index) => `<button class="chapter-link" data-scroll="segment-${chapterTarget(chapter)}"><span class="chapter-time">${stamp(chapter.start)}</span><span>${safe(chapter.title || `章节 ${index + 1}`)}</span></button>`).join("")}${!episode.chapters.length ? '<button class="chapter-link" data-scroll="transcript"><span class="chapter-time">TEXT</span><span>完整对话</span></button>' : ""}</nav></details><section class="speaker-panel"><h2>参与对话</h2>${episode.speakers.length ? episode.speakers.map((speaker, index) => `<div class="speaker-entry"><span class="speaker-dot speaker-${index % 6}" aria-hidden="true"></span><div><strong>${safe(speaker.name)}</strong>${speaker.role ? `<small>${safe(speaker.role)}</small>` : ""}</div></div>`).join("") : '<p class="muted">人物信息待确认</p>'}<p class="editorial-note">${reviewNote}</p></section></aside>
    <div class="reader-body"><section class="summary-block" id="episode-summary" tabindex="-1"><span class="eyebrow">IN THIS EPISODE</span><h2>这期聊了什么</h2>${episode.summary.length ? `<ul>${episode.summary.map(item => `<li>${text(item)}</li>`).join("")}</ul>` : '<p class="muted">本期摘要尚未生成，可以直接阅读下方完整对话。</p>'}</section><section class="transcript" id="transcript" tabindex="-1"><div class="transcript-heading"><h2>完整对话</h2><span>整理稿</span></div><p class="transcript-note">保留完整对话，整理口头重复，同一人的连续发言合并展示。点击时间戳可回到原视频核对。</p>${episode.turns.length ? episode.turns.map(turn => {
      const speaker = speakers.get(turn.speaker_id);
      const url = originalLink(episode, turn.start);
      const headings = (chapterAt.get(turn.segment_indices[0]) || []).map(chapter => `<h3 class="transcript-chapter">${safe(chapter.title || "章节")}</h3>`).join("");
      const content = turn.text_parts.map((part, index) => `<span id="segment-${turn.segment_indices[index]}" class="segment-anchor" tabindex="-1">${text(part)}</span>`).join("");
      return `${headings}<div class="dialogue"><div class="dialogue-label"><span class="speaker-dot speaker-${speaker ? speaker.color : "unknown"}" aria-hidden="true"></span><strong>${safe(speaker ? speaker.name : "未识别说话人")}</strong>${url ? `<a class="timestamp" href="${safe(url)}" target="_blank" rel="noopener noreferrer" aria-label="在原视频中查看 ${stamp(turn.start)}">${stamp(turn.start)} ↗</a>` : `<span class="timestamp">${stamp(turn.start)}</span>`}${turn.needs_review ? '<span class="segment-review">待核对</span>' : ""}</div><p>${content}</p></div>`;
    }).join("") : empty("对话文稿尚未生成", "音视频完成转写与整理后，完整对话会显示在这里。")}</section>${referenceSection}<div class="reading-end"><span aria-hidden="true">◆</span><p>这场对话，读到这里。</p>${sourceURL ? `<a class="text-link" href="${safe(sourceURL)}" target="_blank" rel="noopener noreferrer">去原视频听一听 ↗</a>` : ""}${episode.source.author ? `<small>原作者：${safe(episode.source.author)}</small>` : ""}</div></div></div></article>`;
    main.querySelectorAll("[data-scroll]").forEach(button => button.addEventListener("click", () => {
      const target = document.getElementById(button.dataset.scroll);
      if (target) { target.scrollIntoView({behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start"}); target.focus({preventScroll: true}); }
      main.querySelectorAll(".chapter-link").forEach(link => link.removeAttribute("aria-current"));
      button.setAttribute("aria-current", "location");
    }));
  }

  function searchPage(query) {
    document.title = "搜索文稿 · 文稿集";
    main.innerHTML = `<div class="page-content search-page"><span class="eyebrow">FIND A CONVERSATION</span><h1>你想读些什么？</h1><form class="search-form" role="search"><label class="sr-only" for="search-input">搜索标题、系列、人物和文稿内容</label><span aria-hidden="true">⌕</span><input type="search" id="search-input" name="q" placeholder="搜索标题、系列、人物或一句话…" value="${safe(query)}" autocomplete="off"><button type="submit">搜索 <span aria-hidden="true">↗</span></button></form><p class="search-hint">在 ${episodes.length} 篇文稿中，寻找值得重读的对话。</p><section id="search-results" aria-label="搜索结果"></section></div>`;
    const input = document.getElementById("search-input");
    const results = document.getElementById("search-results");
    function update(value) {
      const terms = value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
      const matches = byDate(episodes).filter(episode => {
        const haystack = [episode.title, episode.description, episode.series.title, ...episode.summary, ...episode.speakers.map(speaker => speaker.name), ...episode.turns.map(turn => turn.text)].join(" ").toLocaleLowerCase();
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
    document.title = "页面未找到 · 文稿集";
    main.innerHTML = `<div class="page-content">${empty("这篇文稿暂时不在书架上", "它可能尚未发布，或链接已经更改。", '<a class="button button-primary" href="#/">返回系列首页</a>')}</div>`;
  }
  function render() {
    const hash = location.hash.slice(1) || "/";
    if (hash === "content") { main.focus(); return; }
    const [path, parameters] = hash.split("?");
    const parts = path.split("/").filter(Boolean);
    try {
      if (!parts.length) home();
      else if (parts[0] === "series" && parts[1]) seriesPage(decodeURIComponent(parts[1]));
      else if (parts[0] === "episode" && parts[1]) episodePage(decodeURIComponent(parts[1]));
      else if (parts[0] === "search") searchPage(new URLSearchParams(parameters || "").get("q") || "");
      else notFound();
    } catch (error) { notFound(); }
    window.scrollTo(0, 0);
    if (parts[0] !== "search") main.focus({preventScroll: true});
  }
  if (data.preview) document.getElementById("preview-banner").innerHTML = '<div class="preview-banner"><strong>本地预览</strong><span>此版本可能包含未发布草稿；演示内容会单独标记。</span></div>';
  document.querySelector(".skip-link").addEventListener("click", event => { event.preventDefault(); main.focus(); });
  window.addEventListener("hashchange", render);
  render();
})();
