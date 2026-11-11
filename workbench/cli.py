"""查看本地资料文件及其字节指纹；TTML字幕抽取、HTTP采集与批量入口。"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from . import demo as _demo
from .collect import batch as run_batch
from .collect import collect_file, collect_http
from .ttml import DocumentError


def _emit(obj, out):
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    if out:
        Path(out).write_text(text + '\n', encoding='utf8')
    else:
        print(text)


def _inspect(directory):
    root = Path(directory)
    rows = []
    for path in sorted(root.rglob('*')):
        if path.is_file():
            data = path.read_bytes()
            rows.append({'path': path.relative_to(root).as_posix(), 'size': len(data),
                         'sha256': hashlib.sha256(data).hexdigest()})
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def main(argv=None):
    p = argparse.ArgumentParser(prog='workbench.cli')
    sub = p.add_subparsers(dest='command', required=True)
    pi = sub.add_parser('inspect', help='列出资料目录文件及字节指纹')
    pi.add_argument('directory')
    pe = sub.add_parser('extract', help='抽取本地TTML字幕')
    pe.add_argument('file')
    pe.add_argument('--window', nargs=2, metavar=('BEGIN', 'END'), help='显式查询时间窗')
    pe.add_argument('--out', help='结果写入文件而非标准输出')
    pc = sub.add_parser('collect', help='采集HTTP来源并按摘要保存原文件')
    pc.add_argument('url')
    pc.add_argument('--raw-dir', required=True, help='原始字节按摘要保存的目录')
    pc.add_argument('--window', nargs=2, metavar=('BEGIN', 'END'), help='显式查询时间窗')
    pc.add_argument('--out', help='结果写入文件而非标准输出')
    pb = sub.add_parser('batch', help='批量入口：按配置逐个来源采集抽取')
    pb.add_argument('config', help='JSON配置：{"sources": [{"url"|"path": ...}], "window": {...}}')
    pb.add_argument('--raw-dir', help='HTTP来源原始字节保存目录')
    pb.add_argument('--out-dir', help='每个来源的结果与index.json写入目录')
    pb.add_argument('--out', help='汇总结果写入文件而非标准输出')
    sub.add_parser('demo', help='演示嵌套seq、帧率分数、继承空白、隐藏span与交叠p')
    a = p.parse_args(argv)
    if a.command == 'inspect':
        if not Path(a.directory).is_dir():
            p.error('资料目录不存在')
        _inspect(a.directory)
        return 0
    if a.command == 'extract':
        try:
            result = collect_file(a.file, window=a.window)
        except DocumentError as exc:
            print(f'文档错误: {exc}', file=sys.stderr)
            return 2
        _emit(result, a.out)
        return 0
    if a.command == 'collect':
        try:
            result = collect_http(a.url, a.raw_dir, window=a.window)
        except DocumentError as exc:
            print(f'文档错误: {exc}', file=sys.stderr)
            return 2
        _emit(result, a.out)
        return 0
    if a.command == 'batch':
        config = json.loads(Path(a.config).read_text(encoding='utf8'))
        if isinstance(config, dict):
            sources = config['sources']
            window = config.get('window')
        else:
            sources, window = config, None
        results = run_batch(sources, raw_dir=a.raw_dir, out_dir=a.out_dir, window=window)
        summary = [{'index': r['index'], 'ok': r['ok'],
                    **({'source': r['result']['source']} if r['ok'] else {'error': r['error']})}
                   for r in results]
        _emit({'results': summary}, a.out)
        return 0 if all(r['ok'] for r in results) else 1
    if a.command == 'demo':
        return _demo.main()
    return 0


if __name__ == '__main__':
    sys.exit(main())
