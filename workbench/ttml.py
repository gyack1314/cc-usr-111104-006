"""TTML受限配置的结构化抽取。

精确有理数计时、display可见性、xml:space空白与时间片切分。
配置细则见 docs/source-format.md；舍入只发生在展示的毫秒值上。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

TTML_NS = 'http://www.w3.org/ns/ttml'
TTP_NS = 'http://www.w3.org/ns/ttml#parameter'
TTS_NS = 'http://www.w3.org/ns/ttml#styling'
XML_NS = 'http://www.w3.org/XML/1998/namespace'

__all__ = [
    'DocumentError', 'TimingParameters', 'Slice', 'Paragraph', 'Document',
    'parse_time', 'parse_document', 'query_window',
]


class DocumentError(Exception):
    """文档违反受限配置或计时无法确定。"""


def _qn(ns, local):
    return f'{{{ns}}}{local}'


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) and tag.startswith('{') else tag


def _is_ttml(el):
    return isinstance(el.tag, str) and el.tag.startswith('{' + TTML_NS + '}')


# ---------------------------------------------------------------- 时间参数

@dataclass(frozen=True)
class TimingParameters:
    """ttp参数；缺省frameRate=30、subFrameRate=1、tickRate=有效frameRate×subFrameRate。"""
    frame_rate: int = 30
    frame_rate_multiplier: Optional[tuple] = None
    sub_frame_rate: int = 1
    tick_rate: Optional[Fraction] = None

    @property
    def effective_frame_rate(self):
        rate = Fraction(self.frame_rate)
        if self.frame_rate_multiplier is not None:
            num, den = self.frame_rate_multiplier
            rate *= Fraction(num, den)
        return rate

    @property
    def effective_tick_rate(self):
        if self.tick_rate is not None:
            return self.tick_rate
        return self.effective_frame_rate * self.sub_frame_rate

    def to_dict(self):
        return {
            'frameRate': self.frame_rate,
            'frameRateMultiplier': list(self.frame_rate_multiplier) if self.frame_rate_multiplier else None,
            'subFrameRate': self.sub_frame_rate,
            'tickRate': str(self.tick_rate) if self.tick_rate is not None else None,
            'effectiveFrameRate': str(self.effective_frame_rate),
            'effectiveTickRate': str(self.effective_tick_rate),
        }


def _positive_int(text, what):
    try:
        value = int(text)
    except ValueError:
        raise DocumentError(f'{what}不是正整数: {text!r}') from None
    if value <= 0:
        raise DocumentError(f'{what}不是正整数: {text!r}')
    return value


def _parse_params(root):
    get = lambda name: root.get(_qn(TTP_NS, name))
    time_base = get('timeBase')
    if time_base is not None and time_base != 'media':
        raise DocumentError(f'仅支持media时间基准: {time_base!r}')
    drop = get('dropMode')
    if drop is not None and drop != 'nonDrop':
        raise DocumentError(f'未支持的drop-frame声明: {drop!r}')
    frame_rate = _positive_int(get('frameRate'), 'frameRate') if get('frameRate') is not None else 30
    multiplier = None
    if get('frameRateMultiplier') is not None:
        parts = get('frameRateMultiplier').split()
        if len(parts) != 2:
            raise DocumentError(f'frameRateMultiplier需要两个正整数: {get("frameRateMultiplier")!r}')
        multiplier = (_positive_int(parts[0], 'frameRateMultiplier分子'),
                      _positive_int(parts[1], 'frameRateMultiplier分母'))
    sub = _positive_int(get('subFrameRate'), 'subFrameRate') if get('subFrameRate') is not None else 1
    tick = Fraction(_positive_int(get('tickRate'), 'tickRate')) if get('tickRate') is not None else None
    return TimingParameters(frame_rate, multiplier, sub, tick)


_OFFSET_RE = re.compile(r'(\d+(?:\.\d+)?)(h|m|s|ms|f|t)')
_CLOCK_FRAMES_RE = re.compile(r'(\d{2,}):([0-5]\d):([0-5]\d):(\d+)(?:\.(\d+))?')
_CLOCK_FRAC_RE = re.compile(r'(\d{2,}):([0-5]\d):([0-5]\d)\.(\d+)')
_CLOCK_PLAIN_RE = re.compile(r'(\d{2,}):([0-5]\d):([0-5]\d)')


def parse_time(value, params):
    """把clock-time或offset-time解析为精确秒数(Fraction)；非法时间报文档错误。"""
    text = value.strip()
    m = _OFFSET_RE.fullmatch(text)
    if m:
        amount = Fraction(m.group(1))
        unit = m.group(2)
        if unit == 'h':
            return amount * 3600
        if unit == 'm':
            return amount * 60
        if unit == 's':
            return amount
        if unit == 'ms':
            return amount / 1000
        if unit == 'f':
            return amount / params.effective_frame_rate
        return amount / params.effective_tick_rate
    m = _CLOCK_FRAMES_RE.fullmatch(text)
    if m:
        hh, mm, ss, ff, sub = m.groups()
        frames = int(ff)
        if frames >= params.frame_rate:
            raise DocumentError(f'帧号不小于frameRate: {value!r}')
        frame = Fraction(frames)
        if sub is not None:
            number = int(sub)
            if number >= params.sub_frame_rate:
                raise DocumentError(f'子帧号不小于subFrameRate: {value!r}')
            # 小数子帧字段是整数子帧编号，不是十进制帧比例
            frame += Fraction(number, params.sub_frame_rate)
        return Fraction(int(hh) * 3600 + int(mm) * 60 + int(ss)) + frame / params.effective_frame_rate
    m = _CLOCK_FRAC_RE.fullmatch(text)
    if m:
        hh, mm, ss, frac = m.groups()
        return Fraction(int(hh) * 3600 + int(mm) * 60 + int(ss)) + Fraction(int(frac), 10 ** len(frac))
    m = _CLOCK_PLAIN_RE.fullmatch(text)
    if m:
        hh, mm, ss = m.groups()
        return Fraction(int(hh) * 3600 + int(mm) * 60 + int(ss))
    raise DocumentError(f'非法时间: {value!r}')


# ---------------------------------------------------------------- 样式

def _parse_styles(root):
    table = {}
    for head in root:
        if not (_is_ttml(head) and _local(head.tag) == 'head'):
            continue
        for styling in head:
            if not (_is_ttml(styling) and _local(styling.tag) == 'styling'):
                continue
            for style in styling:
                if not (_is_ttml(style) and _local(style.tag) == 'style'):
                    continue
                sid = style.get(_qn(XML_NS, 'id'))
                if sid is None:
                    continue
                if sid in table:
                    raise DocumentError(f'样式ID重复: {sid}')
                display = style.get(_qn(TTS_NS, 'display'))
                if display is not None and display not in ('auto', 'none'):
                    raise DocumentError(f'不支持的display值 {display!r}: 样式{sid}')
                refs = style.get('style')
                table[sid] = {'refs': refs.split() if refs else [], 'display': display}
    return table


def _resolve_style(sid, table, resolved, stack):
    """解析样式链的display；引用缺失或成环拒绝文档。多个引用从左到右覆盖。"""
    if sid in resolved:
        return resolved[sid]
    if sid not in table:
        raise DocumentError(f'样式引用缺失: {sid}')
    if sid in stack:
        raise DocumentError(f'样式引用成环: {" -> ".join(stack + [sid])}')
    entry = table[sid]
    value = None
    for ref in entry['refs']:
        candidate = _resolve_style(ref, table, resolved, stack + [sid])
        if candidate is not None:
            value = candidate
    if entry['display'] is not None:
        value = entry['display']
    resolved[sid] = value
    return value


# ---------------------------------------------------------------- 结构路径

def _assign_paths(root):
    """给每个元素分配稳定结构路径，如 /tt/body[1]/div[2]/p[1]。"""
    paths = {}

    def walk(el, path):
        paths[id(el)] = path
        counts = {}
        for child in el:
            if not isinstance(child.tag, str):
                continue
            label = _local(child.tag)
            counts[label] = counts.get(label, 0) + 1
            walk(child, f'{path}/{label}[{counts[label]}]')

    walk(root, f'/{_local(root.tag)}')
    return paths


# ---------------------------------------------------------------- 计时解析

@dataclass
class _Context:
    params: TimingParameters
    styles: dict
    resolved: dict
    paths: dict


@dataclass
class TimedNode:
    tag: str
    path: str
    xml_id: Optional[str]
    begin: Fraction
    end: Optional[Fraction]  # None表示无界（继承自同样无界的祖先）
    visible: bool
    space: str
    element: ET.Element
    children: list = field(default_factory=list)


_CHILDREN = {'body': {'div', 'p'}, 'div': {'div', 'p'}, 'p': {'span'}, 'span': {'span'}}


def _resolve(el, tag, ctx, parent_begin, parent_end, parent_container, sync_base,
             parent_display, parent_visible, parent_space):
    """计算元素的绝对有效区间、可见性与空白模式。起止相对父容器原点。"""
    path = ctx.paths[id(el)]
    explicit_begin = None
    raw_begin = el.get('begin')
    if raw_begin is not None:
        explicit_begin = parse_time(raw_begin, ctx.params)
    if parent_container == 'seq':
        if sync_base is None:
            # 未知或无界的前项结束不能凭零猜出后项起点
            raise DocumentError(f'seq子项起点无法确定，前一子项结束未知或无界: {path}')
        # 显式begin是同步起点之后的延迟；缺省begin即同步起点
        begin = sync_base + (explicit_begin if explicit_begin is not None else Fraction(0))
    else:
        begin = parent_begin + (explicit_begin if explicit_begin is not None else Fraction(0))
    ends = []
    raw_end = el.get('end')
    if raw_end is not None:
        ends.append(parent_begin + parse_time(raw_end, ctx.params))
    raw_dur = el.get('dur')
    if raw_dur is not None:
        ends.append(begin + parse_time(raw_dur, ctx.params))
    end = min(ends) if ends else None  # end与dur同有取较早者
    if end is None:
        end = parent_end  # 未设置结束的子节点继承父界限
    elif parent_end is not None and end > parent_end:
        end = parent_end  # 裁在祖先有效区间内
    if end is not None and end < begin:
        end = begin  # 空时间区间不产出文字
    specified = None
    refs = el.get('style')
    if refs:
        for ref in refs.split():
            value = _resolve_style(ref, ctx.styles, ctx.resolved, [])
            if value is not None:
                specified = value
    inline = el.get(_qn(TTS_NS, 'display'))
    if inline is not None:
        if inline not in ('auto', 'none'):
            raise DocumentError(f'不支持的display值 {inline!r}: {path}')
        specified = inline  # 内联最后覆盖
    display = specified if specified is not None else parent_display
    visible = parent_visible and display != 'none'  # display=none整棵子树不可见
    space_attr = el.get(_qn(XML_NS, 'space'))
    if space_attr is not None:
        if space_attr not in ('default', 'preserve'):
            raise DocumentError(f'不支持的xml:space值 {space_attr!r}: {path}')
        space = space_attr
    else:
        space = parent_space
    container = el.get('timeContainer', 'par')
    if container not in ('par', 'seq'):
        raise DocumentError(f'不支持的timeContainer {container!r}: {path}')
    node = TimedNode(tag, path, el.get(_qn(XML_NS, 'id')), begin, end, visible, space, el)
    sync = begin
    unknown_sync = False
    for child in el:
        if not _is_ttml(child):
            continue
        ctag = _local(child.tag)
        if ctag not in _CHILDREN[tag]:
            continue
        cnode = _resolve(child, ctag, ctx, begin, end, container,
                         None if unknown_sync else sync, display, visible, space)
        node.children.append(cnode)
        if container == 'seq':
            if cnode.end is None:
                unknown_sync = True
            else:
                sync = cnode.end  # seq缺省begin为前一子项有效结束
    return node


# ---------------------------------------------------------------- 文本原子

@dataclass
class _Atom:
    kind: str  # 'text' | 'br'
    text: str
    path: str
    begin: Fraction
    end: Optional[Fraction]
    visible: bool
    space: str


def _collect_atoms(pnode, ctx):
    """按文档顺序收集p正文内的文本与br原子；p的tail不属于p正文。"""
    atoms = []

    def walk(node):
        el = node.element
        cmap = {id(c.element): c for c in node.children}
        tidx = 0
        if el.text is not None:
            tidx += 1
            atoms.append(_Atom('text', el.text, f'{node.path}/text()[{tidx}]',
                               node.begin, node.end, node.visible, node.space))
        for child in el:
            cnode = cmap.get(id(child))
            if cnode is not None:
                walk(cnode)
            elif _is_ttml(child) and _local(child.tag) == 'br':
                atoms.append(_Atom('br', '\n', ctx.paths[id(child)],
                                   node.begin, node.end, node.visible, node.space))
            if child.tail is not None:
                tidx += 1
                atoms.append(_Atom('text', child.tail, f'{node.path}/text()[{tidx}]',
                                   node.begin, node.end, node.visible, node.space))

    walk(pnode)
    return atoms


_XML_WS = re.compile(r'[ \t\r\n]+')


def _build_text(atoms):
    """拼接当片真实可见的文本，保持父文本、尾文本、br与xml:space的顺序。"""
    segments = []  # [kind, text, space]
    for atom in atoms:
        if atom.kind == 'br':
            segments.append(['br', '\n', None])
        elif segments and segments[-1][0] == 'text' and segments[-1][2] == atom.space:
            segments[-1][1] += atom.text
        else:
            segments.append(['text', atom.text, atom.space])
    for seg in segments:
        if seg[0] == 'text' and seg[2] == 'default':
            seg[1] = _XML_WS.sub(' ', seg[1])  # 拼接后压缩XML空白
    while segments and segments[0][0] == 'text' and segments[0][2] == 'default':
        stripped = segments[0][1].lstrip(' \t\r\n')
        if stripped:
            segments[0][1] = stripped
            break
        segments.pop(0)
    while segments and segments[-1][0] == 'text' and segments[-1][2] == 'default':
        stripped = segments[-1][1].rstrip(' \t\r\n')
        if stripped:
            segments[-1][1] = stripped
            break
        segments.pop()
    text = ''.join(seg[1] for seg in segments)  # br的换行不被压缩
    sources = tuple((a.path, a.kind) for a in atoms)
    return text, sources


# ---------------------------------------------------------------- 时间片

@dataclass(frozen=True)
class Slice:
    """半开时间片[begin, end)；sources为当片可见文本节点(path, kind)。"""
    begin: Fraction
    end: Optional[Fraction]
    text: str
    sources: tuple

    def to_dict(self):
        return {
            'interval': _interval_dict(self.begin, self.end),
            'text': self.text,
            'sources': [{'path': p, 'kind': k} for p, k in self.sources],
        }


def _slice(pnode, atoms):
    begin, end = pnode.begin, pnode.end
    if end is not None and begin >= end:
        return []
    clipped = []
    for atom in atoms:
        if not atom.visible:
            continue
        lo = max(atom.begin, begin)
        hi = atom.end
        if end is not None:
            hi = end if hi is None else min(hi, end)
        if hi is not None and lo >= hi:
            continue
        clipped.append((atom, lo, hi))
    points = {begin}
    if end is not None:
        points.add(end)
    for _, lo, hi in clipped:
        points.add(lo)
        if hi is not None:
            points.add(hi)
    ordered = sorted(points)
    spans = list(zip(ordered, ordered[1:]))
    if end is None:
        spans.append((ordered[-1], None))
    slices = []
    for lo, hi in spans:
        current = [a for a, alo, ahi in clipped if alo <= lo and (ahi is None or lo < ahi)]
        text, sources = _build_text(current)
        slices.append(Slice(lo, hi, text, sources))
    merged = []
    for s in slices:
        # 相邻完全同内容及来源集合相同者允许合并
        if merged and merged[-1].text == s.text and merged[-1].sources == s.sources:
            merged[-1] = Slice(merged[-1].begin, s.end, s.text, s.sources)
        else:
            merged.append(s)
    return merged


# ---------------------------------------------------------------- 文档

@dataclass
class Paragraph:
    path: str
    xml_id: Optional[str]
    begin: Fraction
    end: Optional[Fraction]
    slices: list

    def to_dict(self):
        return {
            'p': {'path': self.path, 'xml_id': self.xml_id},
            'interval': _interval_dict(self.begin, self.end),
            'slices': [s.to_dict() for s in self.slices],
        }


def _display_ms(value):
    """便于展示的毫秒值；唯一的舍入发生在这里。"""
    return round(float(value) * 1000, 3)


def _interval_dict(begin, end):
    return {
        'begin': str(begin),
        'end': str(end) if end is not None else None,
        'begin_ms': _display_ms(begin),
        'end_ms': _display_ms(end) if end is not None else None,
    }


class Document:
    """解析后的字幕文档；paragraphs按文档顺序，交叠的p各自独立。"""

    def __init__(self, params, paragraphs):
        self.params = params
        self.paragraphs = paragraphs

    def to_dict(self):
        return {
            'profile': self.params.to_dict(),
            'paragraphs': [p.to_dict() for p in self.paragraphs],
        }

    def query(self, begin, end):
        return query_window(self, begin, end)


def _collect_paragraphs(node, ctx, out):
    if node.tag == 'p':
        atoms = _collect_atoms(node, ctx)
        out.append(Paragraph(node.path, node.xml_id, node.begin, node.end,
                             _slice(node, atoms)))
    for child in node.children:
        _collect_paragraphs(child, ctx, out)


def _check_forbidden(raw):
    for token in (b'<!DOCTYPE', b'<!ENTITY'):
        if token in raw:
            raise DocumentError(f'禁止DTD或实体声明: {token.decode()}')


def parse_document(data):
    """解析受限TTML文档(bytes或str)；违反配置时报DocumentError。"""
    raw = data.encode('utf-8') if isinstance(data, str) else bytes(data)
    _check_forbidden(raw)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise DocumentError(f'XML解析失败: {exc}') from exc
    if not (_is_ttml(root) and _local(root.tag) == 'tt'):
        raise DocumentError('根元素不是TTML命名空间的tt')
    params = _parse_params(root)
    styles = _parse_styles(root)
    resolved = {}
    for sid in styles:
        _resolve_style(sid, styles, resolved, [])
    ctx = _Context(params, styles, resolved, _assign_paths(root))
    space = root.get(_qn(XML_NS, 'space'), 'default')
    if space not in ('default', 'preserve'):
        raise DocumentError(f'不支持的xml:space值: {space!r}')
    paragraphs = []
    for body in root:
        if not (_is_ttml(body) and _local(body.tag) == 'body'):
            continue  # head不是字幕正文
        node = _resolve(body, 'body', ctx, Fraction(0), None, 'par', None,
                        'auto', True, space)
        _collect_paragraphs(node, ctx, paragraphs)
    return Document(params, paragraphs)


def _coerce_time(value, params):
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool):
        raise TypeError('时间不接受布尔值')
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        return Fraction(str(value))
    if isinstance(value, str):
        return parse_time(value, params)
    raise TypeError(f'不支持的时间类型: {type(value).__name__}')


def query_window(document, begin, end):
    """显式时间窗查询：切片裁进窗口，origin保留被裁切的来源关系。"""
    lo = _coerce_time(begin, document.params)
    hi = _coerce_time(end, document.params)
    if lo >= hi:
        raise ValueError('查询时间窗为空或颠倒')
    items = []
    for p in document.paragraphs:
        for s in p.slices:
            a = max(s.begin, lo)
            b = hi if s.end is None else min(s.end, hi)
            if b <= a:
                continue
            items.append({
                'p': {'path': p.path, 'xml_id': p.xml_id},
                'clip': _interval_dict(a, b),
                'clipped': a != s.begin or b != s.end,
                'text': s.text,
                'origin': {
                    'interval': _interval_dict(s.begin, s.end),
                    'sources': [{'path': x, 'kind': k} for x, k in s.sources],
                },
            })
    return {'window': _interval_dict(lo, hi), 'items': items}
