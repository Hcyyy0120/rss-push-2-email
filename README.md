# RSS推文抓取和邮件推送工具

这是一个功能强大的RSS源监控和邮件推送工具，可以定期检查多个RSS源的更新，并通过邮件发送新内容。支持HTML邮件格式，可以显示图片和视频缩略图。

## 功能特点

- 支持多个RSS源的并行抓取和监控
- 每个源可独立配置抓取间隔和资源限制
- HTML格式邮件，支持嵌入图片和视频缩略图
- 图片内容直接嵌入邮件，无需打开外部链接
- 自动提取YouTube等视频的缩略图
- 自动清理过期缓存，防止磁盘空间占用过大
- 强大的错误处理和自动重试机制
- 完善的日志记录，方便排查问题
- 详细的配置验证，防止错误配置
- 支持命令行参数，灵活运行模式
- 数据持久化存储，避免重复推送
- 自动创建数据存储目录

## 安装依赖

```bash
pip install -r requirements.txt
```

## 配置文件

在 `config.json` 中配置RSS源和邮件设置：

```json
{
    "email_config": {
        "smtp_server": "smtp.example.com",
        "smtp_port": 465,
        "sender_email": "your-email@example.com",
        "sender_password": "your-password",
        "receiver_email": "receiver@example.com"
    },
    "base_rss_url": "http://base-url.com",
    "rss_sources": [
        {
            "name": "source1",
            "url": "http://example.com/rss",
            "interval_minutes": 60,
            "save_dir": "data/source1",
            "max_cache_days": 30,
            "max_image_size_mb": 10.0,
            "max_images_per_mail": 20
        },
        {
            "name": "source2",
            "url": "http://another.com/rss",
            "interval_minutes": 30
        }
    ]
}
```

### 配置项说明

#### 邮件配置 (email_config)
- smtp_server: SMTP服务器地址
- smtp_port: SMTP服务器端口
- sender_email: 发件人邮箱
- sender_password: 发件人密码或授权码
- receiver_email: 收件人邮箱

#### RSS源配置 (每项)
- name: RSS源的唯一标识名
- url: RSS源的URL地址
- interval_minutes: 抓取间隔（分钟）
- save_dir: 数据保存目录（可选，默认为data/源名称）
- txt_dir: 文本文件保存目录（可选，默认为./rsspush）
- max_cache_days: 缓存保留天数（可选，默认30天）
- max_image_size_mb: 单张图片最大大小，单位MB（可选，默认10MB）
- max_images_per_mail: 每封邮件最大图片数量（可选，默认20张）

#### 其他配置
- base_rss_url: 基础URL（可选，用于相对路径的RSS源）

## 使用方法

### 基本运行

```bash
python rss_fetcher.py
```

### 指定配置文件路径

```bash
python rss_fetcher.py -c /path/to/config.json
```

### 调试模式

```bash
python rss_fetcher.py --debug
```

### 一次性运行模式（不持续监听）

```bash
python rss_fetcher.py --once
```

## 数据存储

- RSS内容以JSON格式按源名称分目录保存
- 文件名格式：`YYYYMMDD_哈希值.json`
- 每个条目保存为单独的JSON文件
- 新文章内容同时以TXT格式保存在txt_dir目录

## 数据格式

每个JSON文件包含以下字段：
- title: 文章标题
- link: 原文链接
- published: 发布时间
- description: 文章描述/内容
- content: 完整内容（如果RSS提供）
- fetch_time: 抓取时间

## 高级功能

### 图片和视频支持
- 自动提取RSS内容中的图片，并嵌入到邮件中
- 检测YouTube、Vimeo等视频链接，提取缩略图
- 将视频嵌入转换为缩略图+链接形式

### 资源限制
- 可设置图片大小上限，防止过大图片导致邮件发送失败
- 可限制每封邮件中的图片数量
- 自动清理过期的缓存文件，防止磁盘空间无限增长

### 错误处理
- 网络请求、邮件发送等操作自动重试
- 详细的错误记录，包括堆栈跟踪
- 配置文件格式严格验证，避免运行时错误

## 添加新的RSS源

1. 编辑 `config.json` 文件
2. 在 `rss_sources` 数组中添加新的源配置
3. 保存文件后重启程序，或者使用 reload_config() 方法动态重载

## 日志记录

程序会自动创建带时间戳的日志文件，记录详细的运行信息。同时也会在控制台输出关键信息 