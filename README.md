# 答辩字幕资料的层级计时文本抽取

此项目维护本地合成来源、协议资料及原始字节工具，用于复查机器人赛事与课程资料。所有名称、数据和地址都不代表真实组织或设备。

`source_lab` 提供资料服务，`sources` 保存业务样本，`workbench.raw_store` 按摘要存取原始字节。HTTP来源的每条路由可依次返回多个响应，最后一个响应会重复；服务仅绑定环回地址。详细数据格式见 [资料约定](docs/source-format.md)。

使用 Python 3.11 或以上版本，无第三方Python依赖。在项目根目录执行：

```sh
python -m unittest discover -s tests -v
python -m compileall -q workbench source_lab tests
python -m workbench.cli inspect sources
```

## 字幕抽取与采集

`workbench.ttml` 按 [资料约定](docs/source-format.md) 的受限配置做结构化抽取：`parse_document(data)` 解析文档，`Document.to_dict()` 给出每条 p 的 xml:id 与稳定结构路径、按可见文本节点集合变化点切分的半开时间片、原文本节点路径、精确有理数区间及仅用于展示的毫秒值；`query_window(doc, begin, end)` 支持显式时间窗查询，被裁切的结果在 `origin` 中保留来源关系。`workbench.collect` 的 `collect_http` 把 HTTP 原文件按摘要存入 raw 目录后再抽取，`collect_file` 处理本地文件，`batch` 是批量入口（单个来源失败不中断，错误记入结果）。

```sh
python -m workbench.cli extract sources/captions.ttml --window 0.5s 1.5s
python -m workbench.cli collect http://127.0.0.1:8765/captions.ttml --raw-dir raw
python -m workbench.cli batch batch.json --raw-dir raw --out-dir out
python -m workbench.cli demo
```

批量配置为 `{"sources": [{"url": ...} 或 {"path": ...}], "window": {"begin": ..., "end": ...}}`，来源可带各自的 `window`。`demo` 演示嵌套 seq、帧率分数、继承空白、隐藏 span 与交叠 p，并逐变化点核对前后文字。

启动本地HTTP资料服务：

```sh
python -m source_lab.http sources/normal.json --port 8765
```

容器方式可执行 `docker build -t resource-source .`，随后 `docker run --rm resource-source python -m unittest discover -s tests -v`。服务地址只对本次运行有效，客户端应从配置取得地址。输出资料写入单独目录，原始样本保持只读。
