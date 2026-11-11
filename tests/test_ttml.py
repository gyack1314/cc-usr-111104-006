"""核对TTML受限配置的抽取、采集与批量行为。"""
import json
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from source_lab.http import start
from workbench.collect import batch, collect_file, collect_http
from workbench.demo import run as demo_run
from workbench.raw_store import read_bytes
from workbench.ttml import (DocumentError, TimingParameters, parse_document,
                            parse_time, query_window)

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / 'sources'

HEAD = ('<head><styling>'
        '<style xml:id="vis" tts:display="auto"/>'
        '<style xml:id="hid" style="vis" tts:display="none"/>'
        '</styling></head>')


def doc(body, head='', params=''):
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<tt xmlns="http://www.w3.org/ns/ttml" '
            'xmlns:ttp="http://www.w3.org/ns/ttml#parameter" '
            'xmlns:tts="http://www.w3.org/ns/ttml#styling" '
            f'ttp:timeBase="media" {params}>{head}'
            f'<body>{body}</body></tt>')


def texts(p):
    return [s.text for s in p.slices]


class SampleTests(unittest.TestCase):
    def setUp(self):
        self.doc = parse_document((SOURCES / 'captions.ttml').read_bytes())

    def test_paragraph_identities(self):
        got = [(p.xml_id, p.path) for p in self.doc.paragraphs]
        self.assertEqual(got, [
            ('p1', '/tt/body[1]/div[1]/p[1]'),
            ('p2', '/tt/body[1]/div[1]/p[2]'),
            ('p3', '/tt/body[1]/div[2]/p[1]'),
        ])

    def test_seq_and_hidden_span(self):
        p1 = self.doc.paragraphs[0]
        self.assertEqual((p1.begin, p1.end), (Fraction(0), Fraction(3)))
        self.assertEqual(texts(p1), ['定位 ，继续\n展示',
                                     '定位 已完成，继续\n展示',
                                     '定位 ，继续\n展示'])
        # 隐藏备选字幕不进正文，也不占来源
        for s in p1.slices:
            self.assertNotIn('备选内容', s.text)
            self.assertNotIn('span[2]', [path for path, _ in s.sources])
        # 变化点前后文字
        changes = [(str(cur.begin), prev.text, cur.text)
                   for prev, cur in zip(p1.slices, p1.slices[1:])]
        self.assertEqual(changes, [
            ('1', '定位 ，继续\n展示', '定位 已完成，继续\n展示'),
            ('2', '定位 已完成，继续\n展示', '定位 ，继续\n展示'),
        ])
        # 原文本节点路径
        mid = p1.slices[1]
        self.assertEqual([path for path, _ in mid.sources], [
            '/tt/body[1]/div[1]/p[1]/text()[1]',
            '/tt/body[1]/div[1]/p[1]/span[1]/text()[1]',
            '/tt/body[1]/div[1]/p[1]/text()[2]',
            '/tt/body[1]/div[1]/p[1]/br[1]',
            '/tt/body[1]/div[1]/p[1]/text()[3]',
        ])

    def test_frame_fraction_and_preserve(self):
        p2 = self.doc.paragraphs[1]
        self.assertEqual((str(p2.begin), str(p2.end)), ('3', '2501/500'))
        self.assertEqual(texts(p2), ['  行走 完成  ',
                                     '  行走 第二阶段  完成  ',
                                     '  行走 完成  '])
        mid = p2.slices[1]
        self.assertEqual((str(mid.begin), str(mid.end)), ('10001/3000', '4001/1000'))
        # 首尾两片文字与来源相同但不相邻，不得合并
        self.assertEqual(len(p2.slices), 3)

    def test_clock_subframe_and_ticks(self):
        p3 = self.doc.paragraphs[2]
        self.assertEqual((str(p3.begin), str(p3.end)),
                         ('121001/60000', '151001/60000'))
        self.assertEqual(texts(p3), ['并行答辩'])
        # 毫秒值仅用于展示，精确值以有理数为准
        sl = p3.slices[0].to_dict()['interval']
        self.assertEqual(sl['begin'], '121001/60000')
        self.assertEqual(sl['begin_ms'], 2016.683)

    def test_overlapping_paragraphs_kept_separate(self):
        p1, _, p3 = self.doc.paragraphs
        self.assertLess(p3.begin, p1.end)  # 交叠
        self.assertEqual(len(self.doc.paragraphs), 3)


class TimingTests(unittest.TestCase):
    def test_parse_time_units(self):
        params = TimingParameters()
        self.assertEqual(parse_time('2h', params), Fraction(7200))
        self.assertEqual(parse_time('2m', params), Fraction(120))
        self.assertEqual(parse_time('1.5s', params), Fraction(3, 2))
        self.assertEqual(parse_time('500ms', params), Fraction(1, 2))
        self.assertEqual(parse_time('30f', params), Fraction(1))
        self.assertEqual(parse_time('30t', params), Fraction(1))
        self.assertEqual(parse_time('00:00:01.5', params), Fraction(3, 2))
        self.assertEqual(parse_time('01:02:03', params), Fraction(3723))

    def test_parse_time_invalid(self):
        params = TimingParameters()
        for bad in ('unknown', '1x', '-1s', '1:02:03', '00:61:00', '1.2.3s'):
            with self.assertRaises(DocumentError, msg=bad):
                parse_time(bad, params)

    def test_frame_and_subframe_bounds(self):
        params = TimingParameters(frame_rate=30, sub_frame_rate=2)
        with self.assertRaises(DocumentError):
            parse_time('00:00:01:30', params)
        with self.assertRaises(DocumentError):
            parse_time('00:00:01:00.2', params)
        self.assertEqual(parse_time('00:00:01:00.1', params),
                         Fraction(1) + Fraction(1, 2) / 30)

    def test_tick_rate_default(self):
        d = parse_document(doc('<div><p begin="0s" dur="100t">甲</p></div>',
                               params='ttp:frameRate="25" ttp:subFrameRate="2"'))
        self.assertEqual(d.paragraphs[0].end, Fraction(2))

    def test_end_dur_earlier_wins(self):
        d = parse_document(doc('<div><p begin="1s" end="5s" dur="2s">甲</p>'
                               '<p begin="1s" end="2s" dur="5s">乙</p></div>'))
        self.assertEqual((d.paragraphs[0].begin, d.paragraphs[0].end),
                         (Fraction(1), Fraction(3)))
        self.assertEqual((d.paragraphs[1].begin, d.paragraphs[1].end),
                         (Fraction(1), Fraction(2)))

    def test_child_clipped_to_ancestor(self):
        d = parse_document(doc('<div begin="0s" end="2s">'
                               '<p begin="1s" dur="5s">甲</p></div>'))
        self.assertEqual((d.paragraphs[0].begin, d.paragraphs[0].end),
                         (Fraction(1), Fraction(2)))

    def test_empty_interval_no_text(self):
        d = parse_document(doc('<div><p begin="1s" dur="0s">甲</p>'
                               '<p begin="2s" end="1s">乙</p></div>'))
        self.assertEqual(d.paragraphs[0].slices, [])
        self.assertEqual(d.paragraphs[1].slices, [])

    def test_nested_seq_sync_points(self):
        d = parse_document(doc(
            '<div timeContainer="seq">'
            '<p dur="1s">甲</p>'
            '<div timeContainer="seq" dur="2s">'
            '<p dur="1s">乙</p><p begin="0.5s" dur="1s">丙</p>'
            '</div>'
            '<p dur="1s">丁</p>'
            '</div>'))
        got = [(p.begin, p.end) for p in d.paragraphs]
        self.assertEqual(got, [(Fraction(0), Fraction(1)),
                               (Fraction(1), Fraction(2)),
                               # 丙的dur越出内层div，被裁在祖先有效区间内
                               (Fraction(5, 2), Fraction(3)),
                               (Fraction(3), Fraction(4))])

    def test_seq_unknown_previous_end_rejected(self):
        with self.assertRaises(DocumentError):
            parse_document(doc('<div timeContainer="seq"><p>甲</p><p>乙</p></div>'))

    def test_seq_unbounded_last_child_ok(self):
        d = parse_document(doc('<div timeContainer="seq"><p>甲</p></div>'))
        p = d.paragraphs[0]
        self.assertIsNone(p.end)
        self.assertEqual(texts(p), ['甲'])
        self.assertIsNone(p.slices[0].end)

    def test_drop_frame_rejected(self):
        with self.assertRaises(DocumentError):
            parse_document(doc('<div/>', params='ttp:dropMode="dropNTSC"'))
        parse_document(doc('<div/>', params='ttp:dropMode="nonDrop"'))

    def test_time_base_rejected(self):
        with self.assertRaises(DocumentError):
            parse_document(doc('<div/>', params='ttp:timeBase="smpte"'))

    def test_bad_time_sample_rejected(self):
        with self.assertRaises(DocumentError):
            parse_document((SOURCES / 'bad-time.ttml').read_bytes())


class StyleTests(unittest.TestCase):
    def test_hidden_subtree_cannot_reshow(self):
        d = parse_document(doc(
            '<div><p begin="0s" dur="1s">甲'
            '<span style="hid">乙<span tts:display="auto">丙</span></span>丁</p></div>',
            head=HEAD))
        self.assertEqual(texts(d.paragraphs[0]), ['甲丁'])

    def test_inline_overrides_reference(self):
        d = parse_document(doc(
            '<div><p begin="0s" dur="1s">甲'
            '<span style="hid" tts:display="auto">乙</span></p></div>', head=HEAD))
        self.assertEqual(texts(d.paragraphs[0]), ['甲乙'])

    def test_later_reference_wins(self):
        d = parse_document(doc(
            '<div><p begin="0s" dur="1s">'
            '<span style="hid vis">甲</span><span style="vis hid">乙</span>'
            '</p></div>', head=HEAD))
        self.assertEqual(texts(d.paragraphs[0]), ['甲'])

    def test_missing_style_rejected(self):
        with self.assertRaises(DocumentError):
            parse_document(doc('<div><p style="nope" begin="0s" dur="1s">甲</p></div>'))

    def test_style_cycle_sample_rejected(self):
        with self.assertRaises(DocumentError):
            parse_document((SOURCES / 'style-cycle.ttml').read_bytes())

    def test_invalid_display_value(self):
        with self.assertRaises(DocumentError):
            parse_document(doc('<div><p tts:display="block" begin="0s" dur="1s">甲</p></div>'))

    def test_invalid_time_container(self):
        with self.assertRaises(DocumentError):
            parse_document(doc('<div timeContainer="foo"><p>甲</p></div>'))


class TextTests(unittest.TestCase):
    def test_default_space_compress_and_trim(self):
        d = parse_document(doc('<div><p begin="0s" dur="1s">  甲   乙\n丙  </p></div>'))
        self.assertEqual(texts(d.paragraphs[0]), ['甲 乙 丙'])

    def test_br_newline_survives(self):
        d = parse_document(doc('<div><p begin="0s" dur="1s">甲<br/>  乙</p></div>'))
        self.assertEqual(texts(d.paragraphs[0]), ['甲\n 乙'])

    def test_preserve_keeps_whitespace(self):
        d = parse_document(doc(
            '<div xml:space="preserve"><p begin="0s" dur="1s"> 甲 <span> 乙 </span></p></div>'))
        self.assertEqual(texts(d.paragraphs[0]), [' 甲  乙 '])

    def test_invalid_xml_space(self):
        with self.assertRaises(DocumentError):
            parse_document(doc('<div xml:space="weird"><p>甲</p></div>'))

    def test_p_tail_not_in_body(self):
        d = parse_document(doc('<div><p begin="0s" dur="1s">正文</p>尾巴</div>'))
        self.assertEqual(texts(d.paragraphs[0]), ['正文'])

    def test_identical_text_keeps_own_sources(self):
        d = parse_document(doc('<div><p begin="0s" dur="1s">'
                               '<span>同</span><span>同</span></p></div>'))
        s = d.paragraphs[0].slices[0]
        self.assertEqual(s.text, '同同')
        self.assertEqual(len(s.sources), 2)
        self.assertNotEqual(s.sources[0][0], s.sources[1][0])

    def test_head_is_not_body(self):
        d = parse_document(doc('<div><p begin="0s" dur="1s">正文</p></div>',
                               head='<head>垃圾文字<styling/></head>'))
        self.assertEqual(texts(d.paragraphs[0]), ['正文'])
        self.assertNotIn('垃圾', json.dumps(d.to_dict(), ensure_ascii=False))

    def test_dtd_and_entity_rejected(self):
        with self.assertRaises(DocumentError):
            parse_document('<!DOCTYPE tt [<!ENTITY x "y">]>' + doc('<div/>'))
        with self.assertRaises(DocumentError):
            parse_document(doc('<div><p begin="0s" dur="1s">&undefined;</p></div>'))


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.doc = parse_document((SOURCES / 'captions.ttml').read_bytes())

    def test_window_clips_and_keeps_origin(self):
        result = query_window(self.doc, '0.5s', '1.5s')
        self.assertEqual(result['window']['begin'], '1/2')
        self.assertEqual(len(result['items']), 2)
        first, second = result['items']
        self.assertEqual((first['clip']['begin'], first['clip']['end']), ('1/2', '1'))
        self.assertEqual(first['origin']['interval']['begin'], '0')
        self.assertTrue(first['clipped'])
        self.assertEqual(first['text'], '定位 ，继续\n展示')
        self.assertEqual(first['p']['xml_id'], 'p1')
        self.assertEqual((second['clip']['begin'], second['clip']['end']), ('1', '3/2'))
        self.assertEqual(second['origin']['interval']['end'], '2')
        self.assertEqual(second['text'], '定位 已完成，继续\n展示')

    def test_window_outside_all(self):
        result = query_window(self.doc, '10s', '11s')
        self.assertEqual(result['items'], [])

    def test_window_must_be_nonempty(self):
        with self.assertRaises(ValueError):
            query_window(self.doc, '1s', '1s')


class CollectTests(unittest.TestCase):
    def test_collect_http_saves_raw_digest(self):
        server = start(SOURCES / 'normal.json')
        try:
            url = f'http://127.0.0.1:{server.server_port}/captions.ttml'
            with tempfile.TemporaryDirectory() as d:
                result = collect_http(url, d)
                raw = (SOURCES / 'captions.ttml').read_bytes()
                self.assertEqual(result['source']['status'], 200)
                self.assertEqual(read_bytes(d, result['source']['sha256']), raw)
                self.assertEqual(len(result['document']['paragraphs']), 3)
        finally:
            server.shutdown()
            server.server_close()

    def test_collect_http_document_error(self):
        server = start(SOURCES / 'bad-time.json')
        try:
            url = f'http://127.0.0.1:{server.server_port}/captions.ttml'
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaises(DocumentError):
                    collect_http(url, d)
        finally:
            server.shutdown()
            server.server_close()

    def test_collect_file_with_window(self):
        result = collect_file(SOURCES / 'captions.ttml', window=('0.5s', '1.5s'))
        self.assertEqual(len(result['query']['items']), 2)
        self.assertEqual(result['source']['kind'], 'file')

    def test_batch_continues_after_failure(self):
        server = start(SOURCES / 'normal.json')
        try:
            url = f'http://127.0.0.1:{server.server_port}/captions.ttml'
            sources = [{'path': str(SOURCES / 'captions.ttml')},
                       {'url': url},
                       {'path': str(SOURCES / 'bad-time.ttml')}]
            with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as out:
                results = batch(sources, raw_dir=raw, out_dir=out)
                self.assertEqual([r['ok'] for r in results], [True, True, False])
                self.assertIn('DocumentError', results[2]['error'])
                index = json.loads((Path(out) / 'index.json').read_text(encoding='utf8'))
                self.assertEqual(len(index), 3)
                self.assertEqual(len(list(Path(out).glob('*.json'))), 4)
        finally:
            server.shutdown()
            server.server_close()


class DemoTests(unittest.TestCase):
    def test_demo_change_points(self):
        lines = demo_run()
        self.assertTrue(any('变化点 3001/2000' in line for line in lines))
        self.assertEqual(lines[-1], '全部变化点前后文字核对通过')


if __name__ == '__main__':
    unittest.main()
