"""Subtitle evidence is local, bounded and cannot certify audio or speaker identity."""
from copy import deepcopy
import json

import pytest

from podcast_scribe.model import ContentError
from podcast_scribe.subtitles import compare_subtitles, episode_digest, load_subtitle_file, report_is_current


def episode(rows):
    return {'id': 'test', 'review': {'content_checked': False, 'speakers_confirmed': False},
            'speakers': [{'id': 'one', 'name': '未确认'}],
            'segments': [{'id': f'seg-{i}', 'start': start, 'end': end, 'text': text,
                          'raw_text': text, 'speaker_id': 'one', 'review_status': 'needs_review'}
                         for i, (start, end, text) in enumerate(rows)]}


def subtitles(rows, **extras):
    return {'schema_version': 1, 'status': 'available', 'source': {'kind': 'local'},
            'cues': [{'id': f'cue-{i}', 'start': start, 'end': end, 'text': text}
                     for i, (start, end, text) in enumerate(rows)], **extras}


def statuses(report):
    return [g['status'] for g in report['groups'] if g['segment_ids']]


def test_many_to_many_is_not_segmentwise_equality_and_does_not_mutate():
    ep = episode([(0, 2, '大家好。'), (2, 4, '欢迎收听！'), (4, 6, '今天讨论人工智能。')])
    ep['segments'][1]['speaker_id'] = 'second'
    doc = subtitles([(0, 4, '大家好，欢迎收听'), (4, 5, '今天讨论'), (5, 6, '人工智能')])
    before = deepcopy((ep, doc))
    report = compare_subtitles(ep, doc)
    assert statuses(report) == ['match'] * 3
    assert (ep, doc) == before
    assert report['summary']['matched_segments'] == 3
    assert all('transcript_text' not in g for g in report['groups'])
    assert len(report['warnings']) >= 3


@pytest.mark.parametrize(('spoken', 'caption'), [
    ('我同意', '我不同意'), ('利润100万元', '利润1000万元'),
    ('利润-10%', '利润10%'), ('利润10%', '利润10'),
    ('数字1.5', '数字15'), ('日期1:30', '日期130'),
    ('曾鸣认为', '曾明认为'), ('不可能', '可能'),
])
def test_content_differences_cannot_be_normalized_away(spoken, caption):
    report = compare_subtitles(episode([(0, 5, spoken)]), subtitles([(0, 5, caption)]))
    assert statuses(report) == ['text_difference']


def test_normalization_is_limited_to_formatting():
    report = compare_subtitles(episode([(0, 5, '“AI”，可以！ １２３')]), subtitles([(0, 5, 'AI 可以123')]))
    assert statuses(report) == ['match']


def test_offset_gaps_and_subtitle_only_content():
    ep = episode([(10, 12, '开始'), (15, 17, '字幕遗漏'), (20, 22, '结束')])
    doc = subtitles([(0, 2, '开始'), (10, 12, '结束'), (20, 22, '额外字幕')])
    report = compare_subtitles(ep, doc, offset=10)
    assert statuses(report) == ['match', 'no_subtitle', 'match']
    assert report['summary']['coverage_ratio'] == 2 / 3
    assert report['summary']['unaligned_cues'] == 1
    assert report['groups'][-1]['status'] == 'unaligned_subtitle'
    assert statuses(compare_subtitles(ep, doc)) != ['match', 'no_subtitle', 'match']


def test_repeated_overlapping_cues_deduplicate_but_separate_speech_does_not():
    ep = episode([(0, 3, '你好'), (8, 10, '你好')])
    doc = subtitles([(0, 2, '你好'), (1, 3, '你好'), (8, 10, '你好')])
    report = compare_subtitles(ep, doc)
    assert statuses(report) == ['match', 'match']
    assert report['summary']['deduplicated_cues'] == 2
    assert report['groups'][0]['cue_ids'] == ['cue-0', 'cue-1']


def test_long_continuous_dialogue_localizes_one_error():
    ep = episode([(i, i + 1.1, f'第{i}句内容') for i in range(1500)])
    doc = subtitles([(i, i + 1.1, f'第{i}句内容' if i != 733 else '第733句不同内容') for i in range(1500)])
    report = compare_subtitles(ep, doc)
    bad = [g for g in report['groups'] if g['status'] == 'text_difference']
    assert 1 <= len(bad) <= 3
    assert any('seg-733' in g['segment_ids'] for g in bad)
    assert report['summary']['matched_segments'] >= 1497


def test_caption_spanning_window_boundary_keeps_all_segments_match():
    ep = episode([(i * 10, (i + 1) * 10, f'第{i}项') for i in range(9)])
    doc = subtitles([(0, 90, ''.join(f'第{i}项' for i in range(9)))])
    assert statuses(compare_subtitles(ep, doc)) == ['match'] * 9


def test_oversized_alignment_is_explicit_instead_of_quadratic_or_guessed():
    ep = episode([(0, 60, '超长' * 3000), (70, 71, '结尾')])
    doc = subtitles([(0, 60, '超长' * 3000), (70, 71, '结尾')])
    report = compare_subtitles(ep, doc)
    assert statuses(report) == ['text_difference', 'match']
    assert 'alignment_window_exceeded' in report['groups'][0]['reasons']


def test_source_scope_and_digest_detect_staleness():
    ep = episode([(0, 1, '原文')])
    doc = subtitles([(0, 1, '原文')], scope={'start': 0, 'end': 1})
    report = compare_subtitles(ep, doc)
    assert report['source_scope'] == {'start': 0, 'end': 1}
    assert report_is_current(report, ep, doc)
    ep['artifacts'] = {'pdf': 'new.pdf'}
    assert report_is_current(report, ep, doc)
    ep['segments'][0]['text'] = '修正'
    assert not report_is_current(report, ep, doc)
    assert episode_digest(ep) != report['episode_digest']
    ep['segments'][0]['text'] = '原文'
    assert not report_is_current(report, ep, doc, offset=2)
    doc['cues'][0]['end'] = 2
    assert not report_is_current(report, ep, doc)


@pytest.mark.parametrize('bad', [None, '', float('nan'), float('inf'), -1, True])
def test_invalid_times_are_rejected(bad):
    doc = subtitles([(0, 2, '文字')])
    doc['cues'][0]['start'] = bad
    with pytest.raises(ContentError):
        compare_subtitles(episode([(0, 2, '文字')]), doc)


@pytest.mark.parametrize('rows', [
    [{'from': 0, 'to': 1, 'content': ''}],
    [{'from': 2, 'to': 1, 'content': '倒置'}],
    [{'from': 3, 'to': 4, 'content': '后'}, {'from': 1, 'to': 2, 'content': '前'}],
    [{'from': 0, 'to': 1, 'content': '可用'}, {'content': '漏时间'}],
])
def test_json_loader_never_silently_discards_bad_rows(tmp_path, rows):
    path = tmp_path / 'bad.json'
    path.write_text(json.dumps({'body': rows}), encoding='utf-8')
    with pytest.raises(ContentError):
        load_subtitle_file(path)


@pytest.mark.parametrize(('suffix', 'text'), [
    ('.srt', '1\n00:00:00,000 --> 00:00:01,000\n你好\n\n2\n坏时间\n不能丢弃'),
    ('.vtt', 'WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n你好\n\n00:00:02.000 --> 00:00:03.000\n'),
    ('.srt', '1\n00:99:00,000 --> 00:99:01,000\n你好'),
])
def test_timed_text_loader_rejects_skipped_blocks(tmp_path, suffix, text):
    path = tmp_path / ('bad' + suffix)
    path.write_text(text, encoding='utf-8')
    with pytest.raises(ContentError):
        load_subtitle_file(path)


@pytest.mark.parametrize(('suffix', 'text'), [
    ('.json', '{"body":[{"from":0,"to":1,"content":"你好","id":42}]}'),
    ('.srt', '1\n00:00:00,000 --> 00:00:01,000\n你好'),
    ('.vtt', 'WEBVTT\n\nNOTE 说明\n任意文字\n\n00:00:00.000 --> 00:00:01.000 align:start\n<v 人物>你好</v>'),
])
def test_local_formats_load_without_claiming_speakers(tmp_path, suffix, text):
    path = tmp_path / ('captions' + suffix)
    path.write_text(text, encoding='utf-8')
    doc = load_subtitle_file(path)
    assert doc['cues'][0]['text'] == '你好'
    assert doc['cues'][0]['start'] == 0
    assert 'speaker_id' not in doc['cues'][0]
    assert doc['source']['kind'] == 'local'
    if suffix == '.json':
        assert doc['cues'][0]['id'] == '42'


def test_unavailable_document_reports_no_subtitle_without_inventing_evidence():
    doc = {'schema_version': 1, 'status': 'unavailable', 'source': {'kind': 'bilibili'}, 'cues': []}
    report = compare_subtitles(episode([(0, 1, '原文')]), doc)
    assert report['summary']['coverage_ratio'] == 0
    assert statuses(report) == ['no_subtitle']


def test_whole_extra_cue_is_reported_even_inside_a_matching_segment_time():
    ep = episode([(0, 10, '我同意')])
    doc = subtitles([(0, 3, '我同意'), (4, 5, '额外字幕内容')])
    report = compare_subtitles(ep, doc)
    assert report['summary']['unaligned_cues'] == 1
    extra = [g for g in report['groups'] if g['status'] == 'unaligned_subtitle']
    assert extra[0]['cue_ids'] == ['cue-1']
    assert extra[0]['reasons'] == ['unmatched_subtitle_text']


def test_low_confidence_ocr_never_disappears_into_matching_summary(tmp_path):
    ep = episode([(0, 3, '不要删除')])
    doc = subtitles([(0, 3, '不要删除')], source={'kind': 'ocr'}, warnings=['OCR 可能遗漏短暂字幕'])
    doc['cues'][0].update(confidence=92.3, low_confidence=True)
    path = tmp_path / 'ocr.json'
    path.write_text(json.dumps(doc), encoding='utf-8')
    loaded = load_subtitle_file(path)
    assert loaded['cues'][0]['low_confidence'] is True
    report = compare_subtitles(ep, loaded)
    assert statuses(report) == ['text_difference']
    assert 'low_confidence_ocr' in report['groups'][0]['reasons']
    assert 'OCR 可能遗漏短暂字幕' in report['warnings']


def test_huge_details_are_clipped_explicitly_not_repeated_without_bound():
    report = compare_subtitles(episode([(0, 1, '短句')]), subtitles([(0, 1, '巨型字幕' * 10000)]))
    group = report['groups'][0]
    assert group['detail_truncated'] is True
    assert len(group['subtitle_text']) <= 4096


def test_touching_identical_utterances_are_not_collapsed():
    ep = episode([(0, 1, '对'), (1, 2, '对')])
    doc = subtitles([(0, 1, '对'), (1, 2, '对')])
    report = compare_subtitles(ep, doc)
    assert report['summary']['deduplicated_cues'] == 2
    assert statuses(report) == ['match', 'match']


@pytest.mark.parametrize(('spoken', 'caption'), [('5—10', '510'), ('1 23', '123'), ('5、10', '510'), ('5)(10', '510')])
def test_numeric_token_separators_are_content_not_disposable_punctuation(spoken, caption):
    report = compare_subtitles(episode([(0, 5, spoken)]), subtitles([(0, 5, caption)]))
    assert statuses(report) == ['text_difference']


def test_truncated_details_explicitly_require_reading_full_source():
    report = compare_subtitles(episode([(0, 1, '短句')]), subtitles([(0, 1, '很长' * 3000)]))
    assert any('detail_truncated' in warning and 'cue_ids' in warning for warning in report['warnings'])


def test_native_document_keeps_cue_provenance_metadata(tmp_path):
    doc = subtitles([(0, 1, '原文')])
    doc['cues'][0]['frame_times'] = [0.0, 0.5]
    path = tmp_path / 'source.json'
    path.write_text(json.dumps(doc), encoding='utf-8')
    assert load_subtitle_file(path)['cues'][0]['frame_times'] == [0.0, 0.5]
