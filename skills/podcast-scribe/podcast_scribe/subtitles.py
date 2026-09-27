"""Local subtitle evidence: bounded alignment, never transcript or review mutation."""
from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
from difflib import SequenceMatcher
import hashlib
from html import unescape
import json
import math
from pathlib import Path
import re
import unicodedata

from .model import ContentError
from .transcripts import read_transcript

# Bounds apply to each local alignment, independently of programme duration.
_MAX_CHARS = 4096
_MAX_ROWS = 64
_WINDOW_SECONDS = 30.0
_TIME_TOLERANCE = 0.25
_FORMAT_PUNCTUATION = frozenset('，,。.!！?？;；:：“”"\'‘’()（）[]【】《》〈〉…—、')


def _digest(value):
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ContentError('字幕对照数据必须是可序列化的有限 JSON 值') from exc
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def episode_digest(episode: dict) -> str:
    """Include text, boundaries, identity and review state; exports do not stale evidence."""
    return _digest({key: episode.get(key) for key in ('id', 'source', 'duration_seconds', 'segments', 'speakers', 'review')})


def subtitle_digest(document: dict) -> str:
    return _digest(document)


def report_is_current(report: dict, episode: dict, subtitle_document: dict, *, offset=None) -> bool:
    return (report.get('episode_digest') == episode_digest(episode)
            and report.get('subtitle_digest') == subtitle_digest(subtitle_document)
            and (offset is None or report.get('offset_seconds') == offset))


def _finite(value, label, *, nonnegative=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContentError(f'{label} 必须是有限数值')
    if nonnegative and value < 0:
        raise ContentError(f'{label} 不能小于零')
    return float(value)


def _validated_rows(rows, *, aliases=False):
    if not isinstance(rows, list):
        raise ContentError('字幕 cues 必须是列表')
    result, seen, previous_start = [], set(), -1.0
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ContentError(f'第 {index} 条字幕必须是对象')
        start = row.get('start', row.get('from') if aliases else None)
        end = row.get('end', row.get('to') if aliases else None)
        text = row.get('text', row.get('content') if aliases else None)
        start, end = _finite(start, '字幕 start'), _finite(end, '字幕 end')
        if end < start or start < previous_start:
            raise ContentError('字幕时间倒置或未按开始时间排序')
        if not isinstance(text, str) or not text.strip():
            raise ContentError(f'第 {index} 条字幕文本为空或不是字符串，不能静默丢弃')
        ident = row.get('id', f'cue-{index:05d}')
        if isinstance(ident, int) and not isinstance(ident, bool):
            ident = str(ident)
        if not isinstance(ident, str) or not ident.strip() or ident in seen:
            raise ContentError('字幕 ID 不能为空或重复')
        seen.add(ident)
        previous_start = start
        item = {'id': ident, 'start': start, 'end': end, 'text': text.strip()}
        if 'low_confidence' in row:
            if type(row['low_confidence']) is not bool:
                raise ContentError('字幕 low_confidence 必须是布尔值')
            item['low_confidence'] = row['low_confidence']
        if 'confidence' in row:
            item['confidence'] = _finite(row['confidence'], '字幕 confidence')
        result.append(item)
    return result


def _preflight_timed_text(text, suffix):
    """The legacy importer is permissive: reject skipped/broken cues before using it."""
    count = 0
    for index, block in enumerate(re.split(r'\n\s*\n', text.replace('\r\n', '\n'))):
        lines = block.strip().splitlines()
        if not lines:
            continue
        if suffix == '.vtt' and (lines[0] == 'WEBVTT' or lines[0].startswith('WEBVTT ')):
            if any('-->' in line for line in lines):
                raise ContentError('WEBVTT 文件头和字幕之间需要空行')
            continue
        if suffix == '.vtt' and re.match(r'^(NOTE(?:\s|$)|STYLE$|REGION$)', lines[0]):
            continue
        timing = [i for i, line in enumerate(lines) if '-->' in line]
        if len(timing) != 1 or timing[0] not in (0, 1):
            raise ContentError(f'第 {index + 1} 个字幕块缺少有效时间行，不能静默丢弃')
        line_index = timing[0]
        match = re.fullmatch(r'((?:\d+:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+((?:\d+:)?\d{2}:\d{2}[.,]\d{3})(?:\s+[^\r\n]+)?', lines[line_index])
        if not match:
            raise ContentError('字幕时间行格式无效')
        for stamp in match.groups():
            parts = stamp.replace(',', '.').split(':')
            if float(parts[-1]) >= 60 or (len(parts) == 3 and int(parts[-2]) >= 60):
                raise ContentError('字幕时间中的分或秒超出范围')
        body = unescape(re.sub(r'<[^>]+>', '', ' '.join(lines[line_index + 1:]))).strip()
        if not body:
            raise ContentError('字幕块正文为空，不能静默丢弃')
        count += 1
    if not count:
        raise ContentError('字幕文件没有有效内容')
    return count


def load_subtitle_file(path: Path) -> dict:
    """Read JSON/SRT/VTT strictly, retaining provenance and source cue identifiers."""
    path = Path(path)
    try:
        text = path.read_text(encoding='utf-8-sig')
        if path.suffix.lower() == '.json':
            obj = json.loads(text)
            if isinstance(obj, dict) and 'cues' in obj:
                doc = deepcopy(obj)
                if doc.get('schema_version', 1) != 1:
                    raise ContentError('字幕文档需要 schema_version=1')
                validated = _validated_rows(doc['cues'])
                doc['cues'] = [{**original, **cue} for original, cue in zip(doc['cues'], validated)]
                doc.setdefault('schema_version', 1)
                doc.setdefault('status', 'available')
                doc.setdefault('source', {'kind': 'local', 'path': str(path)})
                return doc
            rows = obj if isinstance(obj, list) else obj.get('segments', obj.get('body')) if isinstance(obj, dict) else None
            cues = _validated_rows(rows, aliases=True)
            if not cues:
                raise ContentError('字幕文件没有有效内容')
            imported, _ = read_transcript(path)
            if len(imported) != len(cues):
                raise ContentError('字幕导入发生内容丢失')
        elif path.suffix.lower() in ('.srt', '.vtt'):
            count = _preflight_timed_text(text, path.suffix.lower())
            imported, _ = read_transcript(path)
            cues = _validated_rows([{'id': f'cue-{i:05d}', 'start': s['start'], 'end': s['end'], 'text': s['text']}
                                    for i, s in enumerate(imported, 1)])
            if len(cues) != count:
                raise ContentError('字幕导入发生内容丢失')
        else:
            raise ContentError('字幕文件支持 JSON、SRT、VTT')
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContentError(f'无法读取字幕文件：{exc}') from exc
    return {'schema_version': 1, 'status': 'available', 'source': {'kind': 'local', 'path': str(path)}, 'cues': cues}


def _normalized(text):
    result, pending = [], []
    for char in unicodedata.normalize('NFKC', text):
        if char.isspace() or char in _FORMAT_PUNCTUATION:
            pending.append(char)
            continue
        # Removing a separator between numeric tokens can change their value:
        # 5—10 != 510, 1.5 != 15, and two numbers "1 23" != 123.
        if pending and result and result[-1][-1].isdigit() and char.isdigit():
            result.append(''.join(c for c in pending if not c.isspace()) or ' ')
        pending.clear()
        result.append(char)
    return ''.join(result)


def _deduplicate(cues):
    result = []
    for cue in cues:
        if result and cue['text'] == result[-1]['text'] and cue['start'] < result[-1]['end']:
            result[-1]['end'] = max(result[-1]['end'], cue['end'])
            result[-1]['ids'].append(cue['id'])
            result[-1]['low_confidence'] = result[-1].get('low_confidence', False) or cue.get('low_confidence', False)
        else:
            result.append({**cue, 'ids': [cue['id']]})
    return result


def _overlap(a, b):
    return a['start'] <= b['end'] + _TIME_TOLERANCE and b['start'] <= a['end'] + _TIME_TOLERANCE


class _Timeline:
    """Interval queries with a result cap, including nested/overlapping subtitle cues."""
    def __init__(self, rows):
        self.rows = rows
        self.starts = [r['start'] for r in rows]
        self.size = 1
        while self.size < len(rows):
            self.size *= 2
        self.ends = [-math.inf] * (2 * self.size)
        for i, row in enumerate(rows):
            self.ends[self.size + i] = row['end']
        for i in range(self.size - 1, 0, -1):
            self.ends[i] = max(self.ends[i * 2], self.ends[i * 2 + 1])

    def query(self, start, end, limit=_MAX_ROWS):
        stop = bisect_right(self.starts, end + _TIME_TOLERANCE)
        found = []
        def visit(node, left, right):
            if len(found) > limit or left >= stop or self.ends[node] < start - _TIME_TOLERANCE:
                return
            if right - left == 1:
                found.append(left)
                return
            mid = (left + right) // 2
            visit(node * 2, left, mid)
            visit(node * 2 + 1, mid, right)
        visit(1, 0, self.size)
        return found


def _blocks(rows, texts):
    start = 0
    while start < len(rows):
        end, chars = start + 1, len(texts[start])
        while end < len(rows) and end - start < _MAX_ROWS:
            if rows[end]['end'] - rows[start]['start'] > _WINDOW_SECONDS or chars + len(texts[end]) > _MAX_CHARS:
                break
            chars += len(texts[end])
            end += 1
        yield start, end
        start = end


def _flatten(texts):
    joined, owners, starts = [], [], []
    length = 0
    for index, text in enumerate(texts):
        starts.append(length)
        joined.append(text)
        owners.extend([index] * len(text))
        length += len(text)
    starts.append(length)
    return ''.join(joined), owners, starts


def _excerpt(parts):
    pieces, length = [], 0
    for part in parts:
        separator = '\n' if pieces else ''
        available = _MAX_CHARS - length
        if len(separator) + len(part) > available:
            pieces.append((separator + part)[:available])
            return ''.join(pieces), True
        pieces.append(separator + part)
        length += len(separator) + len(part)
    return ''.join(pieces), False


def compare_subtitles(episode: dict, subtitle_document: dict, *, offset=0.0) -> dict:
    """Compare current text to subtitle evidence without changing any episode field.

    Matching is many-to-many within bounded temporal windows. Unmatched subtitle
    characters invalidate neighbouring text matches, so inserted negations/numbers
    cannot disappear. This is evidence for review, never proof of audio correctness.
    """
    offset = _finite(offset, '字幕偏移', nonnegative=False)
    if not isinstance(episode, dict) or not isinstance(subtitle_document, dict):
        raise ContentError('文稿和字幕必须是对象')
    if subtitle_document.get('schema_version') != 1:
        raise ContentError('字幕文档需要 schema_version=1')
    source = subtitle_document.get('source', {})
    if not isinstance(source, dict):
        raise ContentError('字幕 source 必须是对象')
    rows = _validated_rows(episode.get('segments'))
    if not rows:
        raise ContentError('文稿没有可对照段落')
    cues_original = _validated_rows(subtitle_document.get('cues', []))
    if subtitle_document.get('status') != 'available' and cues_original:
        raise ContentError('非 available 字幕文档不应包含 cues')
    cues = _deduplicate(cues_original)
    for cue in cues:
        cue['start'] += offset
        cue['end'] += offset
    timeline = _Timeline(cues)
    row_texts = [_normalized(s['text']) for s in rows]
    cue_texts = [_normalized(c['text']) for c in cues]
    cue_text, cue_owners, cue_starts = _flatten(cue_texts)
    claimed = [-1] * len(cue_text)
    statuses = ['no_subtitle'] * len(rows)
    reasons = [set() for _ in rows]
    row_cues = [[] for _ in rows]
    used_cues = set()
    cursor = 0
    for first, stop in _blocks(rows, row_texts):
        indices = timeline.query(rows[first]['start'], max(r['end'] for r in rows[first:stop]))
        for index in range(first, stop):
            relevant = timeline.query(rows[index]['start'], rows[index]['end'])
            row_cues[index] = relevant[:_MAX_ROWS]
            used_cues.update(relevant[:_MAX_ROWS])
            if relevant:
                statuses[index] = 'text_difference'
                reasons[index].add('text_not_identical')
        if not indices:
            continue
        block_text, block_owners, block_starts = _flatten(row_texts[first:stop])
        cue_first, cue_stop = cue_starts[indices[0]], cue_starts[indices[-1] + 1]
        cue_first = max(cue_first, cursor)
        if len(indices) > _MAX_ROWS or len(block_text) > _MAX_CHARS or cue_stop - cue_first > _MAX_CHARS:
            for index in range(first, stop):
                if row_cues[index]:
                    reasons[index].add('alignment_window_exceeded')
            continue
        local_cues = cue_text[cue_first:cue_stop]
        matched = [0] * (stop - first)
        matcher = SequenceMatcher(None, block_text, local_cues, autojunk=False)
        for match in matcher.get_matching_blocks():
            for position in range(match.size):
                row_index = first + block_owners[match.a + position]
                cue_position = cue_first + match.b + position
                cue_index = cue_owners[cue_position]
                if cue_index in row_cues[row_index] and _overlap(rows[row_index], cues[cue_index]) and claimed[cue_position] == -1:
                    claimed[cue_position] = row_index
                    matched[row_index - first] += 1
                    cursor = max(cursor, cue_position + 1)
        for index in range(first, stop):
            if row_texts[index] and matched[index - first] == len(row_texts[index]):
                statuses[index] = 'match'
                reasons[index].clear()
            elif not row_texts[index] and row_cues[index]:
                reasons[index].add('no_comparable_characters')
    # Subtitle insertions must not produce false equality. Scope each run to its
    # immediate mapped neighbours, never every segment in a long connected group.
    extra_cues = set()
    for cue_index, cue in enumerate(cues):
        left, right = cue_starts[cue_index:cue_index + 2]
        position = left
        while position < right:
            if claimed[position] != -1:
                position += 1
                continue
            end = position + 1
            while end < right and claimed[end] == -1:
                end += 1
            neighbours = set()
            if position > left and claimed[position - 1] >= 0:
                neighbours.add(claimed[position - 1])
            if end < right and claimed[end] >= 0:
                neighbours.add(claimed[end])
            for index in neighbours:
                statuses[index] = 'text_difference'
                reasons[index].add('subtitle_addition')
            if not neighbours:
                extra_cues.add(cue_index)
            position = end
        if cue_index not in used_cues:
            extra_cues.add(cue_index)
    for index, indices in enumerate(row_cues):
        if any(cues[i].get('low_confidence') for i in indices):
            statuses[index] = 'text_difference'
            reasons[index].add('low_confidence_ocr')
    groups = []
    for index, row in enumerate(rows):
        indices = row_cues[index]
        group = {'status': statuses[index], 'segment_ids': [row['id']],
                 'cue_ids': [ident for i in indices for ident in cues[i]['ids']],
                 'start': row['start'], 'end': row['end']}
        if statuses[index] != 'match':
            group['transcript_text'], raw_truncated = _excerpt([row['text']])
            group['subtitle_text'], subtitle_truncated = _excerpt(cues[i]['text'] for i in indices)
            if raw_truncated or subtitle_truncated:
                group['detail_truncated'] = True
            group['reasons'] = sorted(reasons[index]) or ['no_temporal_overlap']
            if indices:
                group['subtitle_start'] = min(cues[i]['start'] for i in indices)
                group['subtitle_end'] = max(cues[i]['end'] for i in indices)
        groups.append(group)
    for index in sorted(extra_cues):
        cue = cues[index]
        excerpt, truncated = _excerpt([cue['text']])
        groups.append({'status': 'unaligned_subtitle', 'segment_ids': [], 'cue_ids': cue['ids'],
                       'start': cue['start'], 'end': cue['end'], 'transcript_text': '',
                       'subtitle_text': excerpt, 'detail_truncated': truncated,
                       'reasons': ['unmatched_subtitle_text' if index in used_cues else 'no_temporal_overlap']})
    groups.sort(key=lambda g: (g['start'], g['end'], not bool(g['segment_ids'])))
    matched = statuses.count('match')
    different = statuses.count('text_difference')
    covered = matched + different
    warnings = ['字幕一致仅表示文字对照，不等于已听音校对；不更改 review_status 或全局校对状态。',
                '字幕可能删改或识别错误；文字差异需要回听，不能自动以字幕覆盖文稿。',
                '字幕对照不能确认说话人归属；仍需独立核对人物。']
    source_warnings = subtitle_document.get('warnings', [])
    if not isinstance(source_warnings, list) or any(not isinstance(w, str) for w in source_warnings):
        raise ContentError('字幕 warnings 必须是字符串列表')
    warnings.extend(source_warnings)
    if any(g.get('detail_truncated') for g in groups):
        warnings.append('部分冲突预览超过 4096 字符，已标记 detail_truncated；请按 segment_ids/cue_ids 回读原文稿与原字幕的完整内容。')
    if any('alignment_window_exceeded' in item for item in reasons):
        warnings.append('部分字幕或段落超过本地对齐窗口，已明确标为差异，请拆分字幕或按时间查看。')
    return {'schema_version': 1, 'kind': 'subtitle_comparison', 'episode_id': episode.get('id'),
            'episode_digest': episode_digest(episode), 'subtitle_digest': subtitle_digest(subtitle_document),
            'source': deepcopy(source), 'source_scope': deepcopy(subtitle_document.get('scope', source.get('scope'))),
            'subtitle_status': subtitle_document.get('status'), 'offset_seconds': offset,
            'summary': {'segments_total': len(rows), 'matched_segments': matched, 'different_segments': different,
                        'no_subtitle_segments': statuses.count('no_subtitle'), 'covered_segments': covered,
                        'coverage_ratio': covered / len(rows), 'subtitle_cues_total': len(cues_original),
                        'deduplicated_cues': len(cues), 'unaligned_cues': sum(len(cues[i]['ids']) for i in extra_cues),
                        'scope': 'provided_subtitles'},
            'groups': groups, 'warnings': warnings}
