# 字幕资料计时配置

时间基准仅media，clock支持hh:mm:ss(.分数)与hh:mm:ss:ff(.子帧)，offset支持h/m/s/ms/f/t，frameRateMultiplier的分子分母为正整数。默认frameRate=30、subFrameRate=1，默认tickRate=有效frameRate×subFrameRate，所有值用有理数。小数子帧字段表示整数子帧编号，不是十进制帧比例。begin/end相对父容器原点，end与dur同有时取较早者；未设置结束的子节点继承父界限。par缺省begin=0，seq缺省begin为前一子节点有效结束，显式begin是该同步起点之后的延迟。空时间区间不产出文字。

样式只涉及display，支持auto/none；多个引用从左到右覆盖，内联最后覆盖，缺省继承auto。xml:space支持default/preserve，default在文本拼接后压缩XML空白且裁掉首尾，br单独生成换行且不被压缩；preserve的空白原样。每个文本节点保留来源路径，p的tail不属于p正文。禁止DTD、实体声明及外部资源。head不是字幕正文。输出时间片以可见文本节点集合变化点切分，相邻完全同内容及来源集合相同者允许合并。
