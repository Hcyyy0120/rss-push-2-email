# RSS推文抓取器

这是一个用于定期抓取多个RSS源的Python脚本。支持动态配置和管理多个RSS源。

## 功能特点

- 支持多个RSS源的并行抓取
- 每个源可独立配置抓取间隔
- 支持动态添加和修改RSS源
- 将内容保存为JSON格式
- 按源和日期组织文件存储
- 自动创建数据存储目录
- 异常处理和日志输出

## 安装依赖

```bash
pip install -r requirements.txt
```

## 配置文件

在 `config.json` 中配置RSS源：

```json
{
    "rss_sources": [
        {
            "name": "source1",
            "url": "http://example.com/rss",
            "interval_minutes": 60,
            "save_dir": "data/source1"
        },
        {
            "name": "source2",
            "url": "http://another.com/rss",
            "interval_minutes": 30,
            "save_dir": "data/source2"
        }
    ]
}
```

配置项说明：
- name: RSS源的唯一标识名
- url: RSS源的URL地址
- interval_minutes: 抓取间隔（分钟）
- save_dir: 数据保存目录（可选，默认为data/源名称）

## 使用方法

1. 确保已安装所有依赖
2. 配置 `config.json` 文件
3. 运行脚本：

```bash
python rss_fetcher.py
```

## 数据存储

- 所有数据按源名称分目录保存
- 文件名格式：`YYYYMMDD_哈希值.json`
- 每个文章保存为单独的JSON文件

## 数据格式

每个JSON文件包含以下字段：
- title: 文章标题
- link: 原文链接
- published: 发布时间
- description: 文章描述
- content: 文章内容
- fetch_time: 抓取时间

## 添加新的RSS源

1. 编辑 `config.json` 文件
2. 在 `rss_sources` 数组中添加新的源配置
3. 保存文件后，脚本会自动加载新配置 