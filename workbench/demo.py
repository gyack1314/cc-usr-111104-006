"""特性演示：嵌套seq、帧率分数、继承空白、隐藏span与交叠p，逐变化点核对前后文字。"""
from fractions import Fraction

from .ttml import parse_document

DEMO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttp="http://www.w3.org/ns/ttml#parameter" xmlns:tts="http://www.w3.org/ns/ttml#styling" ttp:timeBase="media" ttp:frameRate="30" ttp:frameRateMultiplier="1000 1001" ttp:subFrameRate="2">
<head><styling><style xml:id="base" tts:display="auto"/><style xml:id="alt" style="base" tts:display="none"/></styling></head>
<body>
<div timeContainer="seq">
<p xml:id="n1" dur="1s">  甲   甲  </p>
<div timeContainer="seq" dur="2.5025s">
<p xml:id="n2" dur="30f">乙<span begin="15f" dur="15f">乙插</span></p>
<p xml:id="n3" begin="15f" dur="30f">丙</p>
</div>
<p xml:id="n4" dur="1s">丁</p>
</div>
<div xml:space="preserve">
<p xml:id="w1" begin="0.5s" dur="2s">  外 <span> 内 </span> 尾<br/> 次行  </p>
</div>
<div>
<p xml:id="h1" begin="00:00:01:07.1" dur="1s">显<span style="alt">隐</span><span style="alt" tts:display="auto">现</span>形</p>
</div>
</body>
</tt>
"""

# 每个p期望的半开时间片：(begin, end, 当片文本)，区间用精确有理数
EXPECTED_SLICES = {
    'n1': [('0', '1', '甲 甲')],
    'n2': [('1', '3001/2000', '乙'), ('3001/2000', '2001/1000', '乙乙插')],
    'n3': [('5003/2000', '1401/400', '丙')],
    'n4': [('1401/400', '1801/400', '丁')],
    'w1': [('1/2', '5/2', '  外  内  尾\n 次行  ')],
    'h1': [('5001/4000', '9001/4000', '显现形')],
}

# 每个变化点前后的文字
EXPECTED_CHANGES = {
    'n2': [('3001/2000', '乙', '乙乙插')],
}


def run():
    """执行演示核对；不一致时抛AssertionError，否则返回报告行。"""
    doc = parse_document(DEMO_XML)
    by_id = {p.xml_id: p for p in doc.paragraphs}
    problems = []

    def check(label, actual, expected):
        if actual != expected:
            problems.append(f'{label}: 期望 {expected!r}，实际 {actual!r}')

    check('段落数量(交叠的p各自独立)', len(doc.paragraphs), 6)
    for pid, expected in EXPECTED_SLICES.items():
        p = by_id.get(pid)
        if p is None:
            problems.append(f'缺少段落 {pid}')
            continue
        got = [(str(s.begin), str(s.end), s.text) for s in p.slices]
        check(f'{pid} 时间片', got, [(b, e, t) for b, e, t in expected])
    for pid, changes in EXPECTED_CHANGES.items():
        p = by_id[pid]
        got = [(str(cur.begin), prev.text, cur.text)
               for prev, cur in zip(p.slices, p.slices[1:])]
        check(f'{pid} 变化点前后文字', got, changes)

    # 嵌套seq：内层seq子项接前一子项有效结束，显式begin是延迟；外层后项接内层div有效结束
    check('嵌套seq内层延迟起点', by_id['n3'].begin, Fraction(5003, 2000))
    check('嵌套seq外层后继起点', by_id['n4'].begin, Fraction(1401, 400))
    # 帧率分数：frameRateMultiplier=1000/1001，帧长1001/30000秒
    check('帧率分数帧尾', by_id['n2'].end, Fraction(2001, 1000))
    check('子帧clock起点', by_id['h1'].begin, Fraction(5001, 4000))
    # 继承空白：div上的xml:space=preserve被p与span继承，空白原样
    check('继承空白文本', by_id['w1'].slices[0].text, '  外  内  尾\n 次行  ')
    # 隐藏span：display=none不进正文；内联auto可覆盖引用样式
    h1_text = ''.join(s.text for s in by_id['h1'].slices)
    check('隐藏span不进正文', '隐' in h1_text, False)
    check('内联覆盖引用后可见', '现' in h1_text, True)
    hidden_paths = [path for s in by_id['h1'].slices for path, _ in s.sources]
    check('隐藏span无来源路径', any('span[1]' in p for p in hidden_paths), False)
    # 交叠p：w1/h1/n2时间交叠但不合并
    def overlap(a, b):
        return a.begin < b.end and b.begin < a.end
    check('交叠p保持独立',
          overlap(by_id['w1'], by_id['h1']) and overlap(by_id['h1'], by_id['n2']), True)

    if problems:
        raise AssertionError('\n'.join(problems))

    lines = ['演示文档：嵌套seq、帧率分数(1000/1001)、继承空白、隐藏span、交叠p', '']
    for p in doc.paragraphs:
        lines.append(f'{p.xml_id}  {p.path}  有效区间 [{p.begin}, {p.end})')
        for s in p.slices:
            end = '无界' if s.end is None else str(s.end)
            lines.append(f'  [{s.begin}, {end})  {s.text!r}  来源{len(s.sources)}个节点')
        for prev, cur in zip(p.slices, p.slices[1:]):
            lines.append(f'  变化点 {cur.begin}: {prev.text!r} -> {cur.text!r}')
    lines.append('')
    lines.append('全部变化点前后文字核对通过')
    return lines


def main():
    try:
        lines = run()
    except AssertionError as exc:
        print('演示核对失败:')
        print(exc)
        return 1
    for line in lines:
        print(line)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
