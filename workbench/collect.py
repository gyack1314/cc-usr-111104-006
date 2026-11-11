"""HTTP采集与批量抽取：原始字节按摘要保存，随后结构化抽取。"""
import hashlib
import json
import urllib.request
from pathlib import Path

from .raw_store import save_bytes
from .ttml import parse_document, query_window

USER_AGENT = 'workbench-collect/1.0'


def _window_tuple(window):
    if window is None:
        return None
    if isinstance(window, dict):
        return (window['begin'], window['end'])
    begin, end = window
    return (begin, end)


def collect_http(url, raw_dir, window=None, timeout=10.0):
    """抓取HTTP来源，原始字节按摘要入库，再解析抽取。"""
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = resp.status
        content = resp.read()
    key = save_bytes(raw_dir, content)
    meta = {'kind': 'http', 'url': url, 'status': status, 'sha256': key, 'size': len(content)}
    return _result(meta, content, window)


def collect_file(path, window=None):
    """抽取本地字幕文件；摘要一并写入结果。"""
    content = Path(path).read_bytes()
    meta = {'kind': 'file', 'path': str(Path(path)),
            'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content)}
    return _result(meta, content, window)


def _result(meta, content, window):
    doc = parse_document(content)
    out = {'source': meta, 'document': doc.to_dict()}
    w = _window_tuple(window)
    if w is not None:
        out['query'] = query_window(doc, w[0], w[1])
    return out


def batch(sources, raw_dir=None, out_dir=None, window=None):
    """批量入口：逐个来源采集抽取，单个失败不中断，错误记入结果。"""
    results = []
    out = Path(out_dir) if out_dir else None
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
    index = []
    for i, spec in enumerate(sources):
        entry = {'index': i, 'spec': spec}
        try:
            w = _window_tuple(spec.get('window')) or _window_tuple(window)
            if 'url' in spec:
                if raw_dir is None:
                    raise ValueError('HTTP来源需要raw_dir保存原始字节')
                result = collect_http(spec['url'], raw_dir, window=w)
            elif 'path' in spec:
                result = collect_file(spec['path'], window=w)
            else:
                raise ValueError('来源需要url或path字段')
            entry['ok'] = True
            entry['result'] = result
        except Exception as exc:
            entry['ok'] = False
            entry['error'] = f'{type(exc).__name__}: {exc}'
        results.append(entry)
        if out is not None:
            if entry['ok']:
                name = f"{i:03d}-{entry['result']['source']['sha256'][:8]}.json"
            else:
                name = f'{i:03d}-error.json'
            (out / name).write_text(json.dumps(entry, ensure_ascii=False, indent=2) + '\n',
                                    encoding='utf8')
            index.append({'index': i, 'ok': entry['ok'], 'file': name, 'spec': spec})
    if out is not None:
        (out / 'index.json').write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    return results
