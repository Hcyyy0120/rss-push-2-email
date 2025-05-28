import os
import json
import time
import html
import smtplib
import schedule
import requests
import feedparser
import hashlib
import re
import uuid
import base64
from datetime import datetime, timedelta
from dateutil import parser
from typing import Dict, List, Set, Tuple, Optional, Any, Union
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.header import Header
import logging
from urllib.parse import urljoin, urlparse
from functools import wraps
import socket
import sys
import traceback

DEFAULT_TXT_DIR = "./rsspush"

# 在文件顶部添加日志配置
def setup_logger():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"{timestamp}_rss_fetcher.log"
    
    # 创建日志记录器
    logger = logging.getLogger("rss_logger")
    logger.setLevel(logging.DEBUG)
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # 创建文件处理器
    file_handler = logging.FileHandler(log_filename, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    
    # 创建日志格式
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)
    
    # 添加处理器到日志记录器
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    return logger

# 全局日志记录器
logger = setup_logger()

# 添加重试装饰器
def retry(max_retries=3, delay=5, backoff=2, exceptions=(Exception,)):
    """
    重试装饰器，用于在遇到指定异常时自动重试函数
    
    Args:
        max_retries: 最大重试次数
        delay: 初始延迟时间（秒）
        backoff: 延迟倍数（每次重试后延迟时间增加的倍数）
        exceptions: 需要捕获的异常类型
        
    Returns:
        装饰后的函数
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            mtries, mdelay = max_retries, delay
            last_exception = None
            
            # 尝试调用原始函数
            while mtries > 0:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    mtries -= 1
                    if mtries == 0:
                        logger.warning(f"函数 {func.__name__} 已达到最大重试次数 ({max_retries})，放弃重试")
                        raise
                        
                    logger.info(f"函数 {func.__name__} 出错: {str(e)}，{mdelay}秒后重试 (还剩{mtries}次尝试)")
                    time.sleep(mdelay)
                    mdelay *= backoff  # 增加延迟
                    
            # 不应该到达这里，但以防万一
            if last_exception:
                raise last_exception
                
        return wrapper
    return decorator

class EmailSender:
    def __init__(self, config):
        self.config = config
        self.smtp_server = config['smtp_server']
        self.smtp_port = config['smtp_port']
        self.sender_email = config['sender_email']
        self.sender_password = config['sender_password']
        self.receiver_email = config['receiver_email']
        
    @retry(max_retries=3, delay=5, exceptions=(smtplib.SMTPException, socket.error, TimeoutError))
    def send_email(self, subject: str, content: str, html_content: str = None, images: List[Tuple[str, bytes]] = None):
        """发送邮件，支持HTML和嵌入图片
        
        Args:
            subject: 邮件主题
            content: 纯文本内容
            html_content: HTML格式内容（可选）
            images: 要嵌入的图片列表，每项为(content_id, image_data)元组
        """
        try:
            # 创建邮件对象
            msg = MIMEMultipart('alternative')
            msg['From'] = self.sender_email
            msg['To'] = self.receiver_email
            msg['Subject'] = Header(subject, 'utf-8')
            
            # 添加纯文本版本
            msg.attach(MIMEText(content, 'plain', 'utf-8'))
            
            # 如果提供了HTML内容，添加HTML版本
            if html_content:
                msg.attach(MIMEText(html_content, 'html', 'utf-8'))
            
            # 如果有图片，添加到邮件中
            if images:
                for cid, img_data in images:
                    try:
                        image = MIMEImage(img_data)
                        image.add_header('Content-ID', f'<{cid}>')
                        image.add_header('Content-Disposition', 'inline', filename=f'image_{cid}.jpg')
                        msg.attach(image)
                    except Exception as e:
                        logger.warning(f"添加图片时出错: {str(e)}")
            
            # 连接SMTP服务器并发送
            server = smtplib.SMTP_SSL(self.smtp_server, self.smtp_port, timeout=30)
            try:
                server.login(self.sender_email, self.sender_password)
                server.send_message(msg)
                # 在发送过程中需要手动调用server.quit()方法关闭会话，否则会报一个错误
                server.quit()
            except Exception as e:
                try:
                    server.quit()
                except:
                    pass
                raise e
                
            logger.info(f"邮件已发送: {subject}")
            return True
            
        except Exception as e:
            logger.error(f"发送邮件时出错: {str(e)}")
            raise  # 让retry装饰器处理重试

class RSSFetcher:
    def __init__(self, name: str, url: str, email_sender: EmailSender, base_url: str = '', 
                 save_dir: str = 'data', txt_dir: str = None, max_cache_days: int = 30,
                 max_image_size_mb: float = 10.0, max_images_per_mail: int = 20):
        self.name = name
        self.url = url
        self.base_url = base_url
        self.save_dir = save_dir
        self.txt_dir = txt_dir or DEFAULT_TXT_DIR  # text_dir 是txt文件的保存路径
        self.processed_guids = set()  # 用于存储已处理的条目GUID
        self.cache_file = os.path.join(save_dir, f"{name}_processed_guids.json")
        
        # 资源限制参数
        self.max_cache_days = max_cache_days  # 缓存最长保留天数
        self.max_image_size_mb = max_image_size_mb  # 单张图片最大大小(MB)
        self.max_images_per_mail = max_images_per_mail  # 每封邮件最大图片数
        
        # 创建保存目录
        for directory in [save_dir, self.txt_dir]:
            if directory and not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)
                
        # 加载已处理的GUID缓存
        self.load_processed_guids()
        self.email_sender = email_sender
        
        # 清理旧缓存
        self.cleanup_old_cache()
            
    def load_processed_guids(self):
        """加载已处理的GUID缓存"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self.processed_guids = set(json.load(f))
        except Exception as e:
            logger.error(f"[{self.name}] 加载GUID缓存出错: {str(e)}")
            
    def save_processed_guids(self):
        """保存已处理的GUID缓存"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(list(self.processed_guids), f)
        except Exception as e:
            logger.error(f"[{self.name}] 保存GUID缓存出错: {str(e)}")
            
    def clean_html(self, text: str) -> str:
        """清理HTML标签和转义字符"""
        if not text:
            return ""
            
        # 替换常见的HTML实体
        text = safe_unescape(text)
        
        # 保留换行和段落结构
        text = text.replace('<br>', '\n')
        text = text.replace('<br/>', '\n')
        text = text.replace('<br />', '\n')
        text = text.replace('</p>', '\n\n')
        text = text.replace('</div>', '\n')
        text = text.replace('</h1>', '\n')
        text = text.replace('</h2>', '\n')
        text = text.replace('</h3>', '\n')
        text = text.replace('</h4>', '\n')
        text = text.replace('</h5>', '\n')
        text = text.replace('</li>', '\n')
        
        # 移除所有HTML标签
        text = re.sub(r'<[^>]*>', '', text)
        
        # 移除多余的空行
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # 移除前导和尾随的空白字符
        return text.strip()
            
    def save_new_entries_as_txt(self, new_entries):
        """将新的RSS条目保存为TXT格式"""
        if not new_entries:
            return
            
        try:
            # 构建文件名
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{self.name}_update_{timestamp}.txt"
            file_path = os.path.join(self.txt_dir, filename)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(f"=== RSS更新 - {self.name} ===\n")
                f.write(f"更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"RSS源: {self.url}\n")
                f.write(f"新增文章数: {len(new_entries)}\n\n")
                
                # 写入每篇新文章
                for entry in new_entries:
                    f.write(f"发布时间: {entry.get('published', '')}\n")
                    f.write(f"作者: {entry.get('author', '')}\n")
                    f.write(f"标题: {entry.get('title', '')}\n")
                    f.write(f"链接: {safe_unescape(entry.get('link', ''))}\n")
                    
                    # 清理并写入描述
                    description = self.clean_html(entry.get('description', ''))
                    f.write(f"内容:\n{description}\n")
                    f.write("\n" + "="*50 + "\n\n")
                    
            logger.info(f"[{self.name}] 发现{len(new_entries)}篇新文章，已保存到: {file_path}")
            
        except Exception as e:
            logger.error(f"[{self.name}] 保存新文章时出错: {str(e)}")
            
    def format_entries_for_email(self, entries) -> str:
        """格式化条目为邮件纯文本内容"""
        content = []
        for entry in entries:
            content.append(f"标题: {entry.get('title', '')}")
            content.append(f"作者: {entry.get('author', '')}")
            content.append(f"发布时间: {entry.get('published', '')}")
            content.append(f"链接: {safe_unescape(entry.get('link', ''))}")
            content.append("\n内容:")
            content.append(self.clean_html(entry.get('description', '')))
            content.append("\n" + "="*50 + "\n")
        return "\n".join(content)
    
    def format_entries_for_html_email(self, entries, image_map: Dict[str, str] = None) -> str:
        """格式化条目为HTML邮件内容
        
        Args:
            entries: RSS条目列表
            image_map: 图片URL到Content-ID的映射
            
        Returns:
            HTML格式的邮件内容
        """
        if image_map is None:
            image_map = {}
            
        html_parts = []
        
        # 添加邮件样式
        html_parts.append("""
        <html>
        <head>
            <style>
                body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 800px; margin: 0 auto; }
                h1 { color: #2c3e50; border-bottom: 1px solid #eee; padding-bottom: 10px; }
                h2 { color: #3498db; margin-top: 30px; }
                img { max-width: 100%; height: auto; margin: 10px 0; border-radius: 4px; }
                .entry { margin-bottom: 30px; border-bottom: 1px solid #eee; padding-bottom: 20px; }
                .meta { font-size: 0.9em; color: #7f8c8d; margin-bottom: 15px; }
                .content { margin-top: 15px; }
                a { color: #3498db; text-decoration: none; }
                a:hover { text-decoration: underline; }
                .separator { margin: 30px 0; border-top: 1px dashed #ccc; }
                .video-container { position: relative; padding-top: 10px; height: 0; overflow: hidden; margin: 15px 0; }
                .video-container iframe, .video-container object, .video-container embed { position: absolute; top: 0; left: 0; width: 100%; height: 100%; }
                .summary { background-color: #f9f9f9; padding: 15px; border-left: 4px solid #3498db; margin: 15px 0; }
                .footer { margin-top: 30px; padding-top: 15px; border-top: 1px solid #eee; font-size: 0.9em; color: #7f8c8d; }
            </style>
        </head>
        <body>
            <h1>RSS更新 - """ + self.name + """</h1>
            <div class="summary">
                <p>更新时间: """ + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + """</p>
                <p>RSS源: """ + self.url + """</p>
                <p>新增文章数: """ + str(len(entries)) + """</p>
            </div>
        """)
        
        # 添加每个条目
        for i, entry in enumerate(entries):
            title = entry.get('title', '无标题')
            author = entry.get('author', '未知作者')
            published = entry.get('published', '')
            link = safe_unescape(entry.get('link', ''))
            
            html_parts.append(f'<div class="entry">')
            html_parts.append(f'<h2><a href="{link}" target="_blank">{title}</a></h2>')
            
            html_parts.append(f'<div class="meta">')
            if author:
                html_parts.append(f'作者: {author} | ')
            if published:
                html_parts.append(f'发布时间: {published}')
            html_parts.append('</div>')
            
            # 处理内容
            description = entry.get('description', '')
            if description:
                # 处理视频嵌入内容（转换为缩略图+链接）
                description = self.convert_video_embeds_to_thumbnails(description, link)
                
                # 如果有图片映射表，替换图片URL为Content-ID引用
                if image_map:
                    for url, cid in image_map.items():
                        if url in description:
                            description = description.replace(f'src="{url}"', f'src="cid:{cid}"')
                            description = description.replace(f"src='{url}'", f'src="cid:{cid}"')
                
                html_parts.append(f'<div class="content">{description}</div>')
            
            html_parts.append('</div>')
            
            # 添加分隔符（除了最后一个条目）
            if i < len(entries) - 1:
                html_parts.append('<div class="separator"></div>')
        
        # 添加页脚
        html_parts.append("""
            <div class="footer">
                此邮件由RSS订阅自动发送。
            </div>
        """)
        
        html_parts.append('</body></html>')
        return ''.join(html_parts)
        
    def convert_video_embeds_to_thumbnails(self, html_content: str, article_url: str) -> str:
        """将视频嵌入标签转换为缩略图+链接，避免邮件中直接嵌入视频播放器
        
        Args:
            html_content: HTML内容
            article_url: 文章URL，用于创建视频链接
            
        Returns:
            转换后的HTML内容
        """
        # 匹配iframe视频嵌入
        iframe_pattern = r'<iframe[^>]+src=[\'"]([^\'"]+)[\'"][^>]*>.*?</iframe>'
        
        def replace_iframe(match):
            iframe_src = match.group(1)
            # 检查是否为YouTube嵌入
            if 'youtube.com/embed/' in iframe_src:
                video_id = re.search(r'youtube\.com/embed/([^/?]+)', iframe_src)
                if video_id:
                    video_id = video_id.group(1)
                    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
                    return f'<a href="{iframe_src}" target="_blank"><img src="{thumbnail_url}" alt="YouTube视频" style="max-width:100%;"></a><br><a href="{iframe_src}" target="_blank">点击观看视频</a>'
            
            # 默认返回链接
            return f'<a href="{article_url}" target="_blank">查看原文中的视频内容</a>'
            
        # 替换所有iframe
        return re.sub(iframe_pattern, replace_iframe, html_content)
    
    def send_new_entries_email(self, new_entries):
        """将新条目通过邮件发送，包含图片"""
        if not new_entries:
            return
            
        subject = f"RSS更新 - {self.name} - {len(new_entries)}篇新文章"
        
        # 准备纯文本内容
        plain_content = f"RSS源: {self.url}\n"
        plain_content += f"更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        plain_content += f"新增文章数: {len(new_entries)}\n\n"
        plain_content += self.format_entries_for_email(new_entries)
        
        # 收集所有图片
        all_images_urls = []
        base_url = None
        
        # 提取所有条目中的图片URL
        for entry in new_entries:
            description = entry.get('description', '')
            
            # 尝试获取基础URL
            if not base_url and 'link' in entry:
                try:
                    parsed_url = urlparse(entry['link'])
                    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
                except:
                    pass
                    
            # 提取图片URL
            if description:
                image_urls = self.extract_images_from_html(description, base_url)
                all_images_urls.extend(image_urls)
        
        # 下载图片
        downloaded_images = []
        image_map = {}  # URL到Content-ID的映射
        
        if all_images_urls:
            logger.info(f"[{self.name}] 正在下载{len(all_images_urls)}张图片...")
            downloaded_images = self.download_images(all_images_urls)
            
            # 创建URL到CID的映射
            for cid, url in all_images_urls:
                image_map[url] = cid
        
        # 准备HTML内容
        html_content = self.format_entries_for_html_email(new_entries, image_map)
        
        # 发送邮件
        self.email_sender.send_email(
            subject=subject,
            content=plain_content,
            html_content=html_content,
            images=downloaded_images
        )
    
    @retry(max_retries=3, delay=2, backoff=2, exceptions=(requests.RequestException, socket.error, TimeoutError))
    def fetch_rss(self):
        """获取RSS更新"""
        try:
            # 获取RSS内容
            full_url = self.base_url + self.url if self.url.startswith('/') else self.url
            
            # 添加User-Agent头，减少被拒绝的可能性
            headers = {
                'User-Agent': 'RSS-Fetcher/1.0 (https://github.com/Hcyyy0120/rss-push-2-email)',
                'Accept': 'application/rss+xml, application/xml, text/xml, application/atom+xml'
            }
            
            logger.debug(f"[{self.name}] 正在获取RSS内容: {full_url}")
            
            # 确保URL安全
            full_url = safe_unescape(full_url)
            
            response = requests.get(full_url, headers=headers, timeout=30)
            response.raise_for_status()  # 抛出HTTP错误，让retry处理
            
            # 检查响应内容类型
            content_type = response.headers.get('Content-Type', '')
            logger.debug(f"[{self.name}] 响应内容类型: {content_type}")
            
            feed = feedparser.parse(response.content)
            
            # 检查解析结果是否有效
            if not hasattr(feed, 'entries') or not feed.entries:
                logger.warning(f"[{self.name}] 没有找到RSS条目，可能的原因：1.源地址错误 2.格式不是有效RSS/Atom 3.源暂时无法访问")
                return
                
            # 检查feed版本
            if hasattr(feed, 'version') and feed.version:
                logger.debug(f"[{self.name}] RSS格式版本: {feed.version}")
                
            # 检查feed标题
            if hasattr(feed, 'feed') and hasattr(feed.feed, 'title'):
                logger.debug(f"[{self.name}] Feed标题: {feed.feed.title}")
            
            # 检查新条目
            new_entries = []
            for entry in feed.entries:
                guid = entry.get('guid', '') or entry.get('link', '') or entry.get('id', '')
                if not guid:  # 如果没有可用的标识符，使用标题和发布日期组合
                    title = entry.get('title', '')
                    published = entry.get('published', '') or entry.get('updated', '')
                    guid = f"{title}_{published}"
                    
                if guid and guid not in self.processed_guids:
                    new_entries.append(entry)
                    self.processed_guids.add(guid)
            
            # 限制处理过多的新条目，以防RSS源突然包含大量历史内容
            if len(new_entries) > 20:
                logger.warning(f"[{self.name}] 发现大量新条目 ({len(new_entries)}), 可能是初始化或源发生变化，限制为最新的20条")
                new_entries = new_entries[:20]
            
            # 如果有新条目，保存并发送邮件
            if new_entries:
                self.save_new_entries_as_txt(new_entries)
                self.save_processed_guids()
                self.send_new_entries_email(new_entries)
                
                # 同时保存JSON格式（可选）
                for entry in new_entries:
                    # 解析日期（安全地获取published字段）
                    date_str = datetime.now().strftime('%Y%m%d')  # 默认使用当前日期
                    if 'published' in entry:
                        try:
                            published = parser.parse(entry.get('published'))
                            date_str = published.strftime('%Y%m%d')
                        except Exception as e:
                            logger.warning(f"[{self.name}] 解析发布日期失败: {str(e)}")
                    
                    # 使用MD5哈希代替不可靠的hash函数
                    link = safe_unescape(entry.get('link', ''))
                    hash_str = hashlib.md5(link.encode('utf-8')).hexdigest()[:10]
                    
                    # 准备保存的数据
                    item_data = {
                        'title': entry.get('title', ''),
                        'link': link,
                        'published': entry.get('published', ''),
                        'description': entry.get('description', ''),
                        'content': entry.get('content', [{}])[0].get('value', '') if 'content' in entry else '',
                        'fetch_time': datetime.now().isoformat()
                    }
                    
                    # 构建文件名
                    filename = f"{date_str}_{hash_str}.json"
                    file_path = os.path.join(self.save_dir, filename)
                    
                    # 保存到文件
                    try:
                        with open(file_path, 'w', encoding='utf-8') as f:
                            json.dump(item_data, f, ensure_ascii=False, indent=2)
                    except Exception as e:
                        logger.warning(f"[{self.name}] 保存条目到JSON时出错: {str(e)}")
            else:
                logger.debug(f"[{self.name}] 没有新条目")
            
        except requests.exceptions.RequestException as e:
            logger.error(f"[{self.name}] 网络请求出错: {str(e)}")
            # 抛出异常，以便retry装饰器处理
            raise
        except Exception as e:
            logger.error(f"[{self.name}] 获取RSS内容时出错: {str(e)}")
            logger.error(traceback.format_exc())

    def extract_images_from_html(self, html_content: str, base_url: str = '') -> List[Tuple[str, str]]:
        """从HTML内容中提取图片URL
        
        Args:
            html_content: HTML内容
            base_url: 用于相对URL的基础URL
            
        Returns:
            列表，每项为(content_id, image_url)元组
        """
        if not html_content:
            return []
            
        # 查找所有img标签
        img_pattern = r'<img[^>]+src=[\'"]([^\'"]+)[\'"][^>]*>'
        matches = re.findall(img_pattern, html_content)
        
        results = []
        for i, img_url in enumerate(matches):
            # 解码HTML实体（关键修改）
            img_url = safe_unescape(img_url)
            
            # 处理相对URL
            if img_url.startswith('/') or not (img_url.startswith('http://') or img_url.startswith('https://')):
                if base_url:
                    img_url = urljoin(base_url, img_url)
                else:
                    continue  # 跳过无法解析的相对URL
                    
            # 生成唯一的Content-ID
            content_id = f"img_{i}_{uuid.uuid4().hex[:8]}"
            results.append((content_id, img_url))
            
        # 提取视频缩略图（YouTube, Vimeo等）
        self.extract_video_thumbnails(html_content, results)
            
        return results
        
    def extract_video_thumbnails(self, html_content: str, results: List[Tuple[str, str]]) -> None:
        """提取视频缩略图URL并添加到结果列表
        
        Args:
            html_content: HTML内容
            results: 结果列表，将直接修改此列表添加内容
        """
        # 提取YouTube视频ID
        youtube_patterns = [
            r'youtube\.com/watch\?v=([^&]+)',
            r'youtube\.com/embed/([^/?]+)',
            r'youtu\.be/([^/?]+)'
        ]
        
        for pattern in youtube_patterns:
            for match in re.finditer(pattern, html_content):
                video_id = match.group(1)
                if video_id:
                    # YouTube缩略图URL格式
                    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
                    content_id = f"yt_{video_id}"
                    results.append((content_id, thumbnail_url))
                    
        # 提取Vimeo视频ID (需要额外API调用获取缩略图，此处简化处理)
        vimeo_pattern = r'vimeo\.com/(\d+)'
        for match in re.finditer(vimeo_pattern, html_content):
            video_id = match.group(1)
            if video_id:
                # 这里仅添加视频ID，实际使用中可能需要调用Vimeo API获取真实缩略图
                logger.debug(f"检测到Vimeo视频ID: {video_id}，但需要API获取缩略图")
    
    def cleanup_old_cache(self):
        """清理过期的缓存文件"""
        try:
            # 计算截止日期
            cutoff_date = datetime.now() - timedelta(days=self.max_cache_days)
            cutoff_timestamp = cutoff_date.timestamp()
            
            # 清理数据目录中的旧文件
            if os.path.exists(self.save_dir):
                count = 0
                for filename in os.listdir(self.save_dir):
                    file_path = os.path.join(self.save_dir, filename)
                    # 跳过目录和缓存文件
                    if os.path.isdir(file_path) or filename.endswith('_processed_guids.json'):
                        continue
                        
                    # 检查文件修改时间
                    file_mtime = os.path.getmtime(file_path)
                    if file_mtime < cutoff_timestamp:
                        try:
                            os.remove(file_path)
                            count += 1
                        except Exception as e:
                            logger.warning(f"删除旧缓存文件时出错: {file_path}, {str(e)}")
                            
                if count > 0:
                    logger.info(f"[{self.name}] 清理了 {count} 个过期缓存文件 (超过 {self.max_cache_days} 天)")
                    
            # 清理TXT目录中的旧文件
            if os.path.exists(self.txt_dir):
                txt_count = 0
                for filename in os.listdir(self.txt_dir):
                    if not filename.startswith(self.name + '_update_'):
                        continue
                        
                    file_path = os.path.join(self.txt_dir, filename)
                    file_mtime = os.path.getmtime(file_path)
                    if file_mtime < cutoff_timestamp:
                        try:
                            os.remove(file_path)
                            txt_count += 1
                        except Exception as e:
                            logger.warning(f"删除旧TXT文件时出错: {file_path}, {str(e)}")
                            
                if txt_count > 0:
                    logger.info(f"[{self.name}] 清理了 {txt_count} 个过期TXT文件 (超过 {self.max_cache_days} 天)")
                    
        except Exception as e:
            logger.error(f"[{self.name}] 清理旧缓存时出错: {str(e)}")
    
    @retry(max_retries=3, delay=3, exceptions=(requests.RequestException, socket.error, TimeoutError))
    def download_images(self, image_urls: List[Tuple[str, str]]) -> List[Tuple[str, bytes]]:
        """下载图片
        
        Args:
            image_urls: 图片URL列表，每项为(content_id, image_url)元组
            
        Returns:
            列表，每项为(content_id, image_data)元组
        """
        results = []
        
        # 限制图片数量
        if len(image_urls) > self.max_images_per_mail:
            logger.warning(f"[{self.name}] 图片数量超过限制 ({len(image_urls)} > {self.max_images_per_mail})，将仅下载前 {self.max_images_per_mail} 张")
            image_urls = image_urls[:self.max_images_per_mail]
            
        max_size_bytes = int(self.max_image_size_mb * 1024 * 1024)  # 转换为字节
        
        for content_id, url in image_urls:
            try:
                # 设置较短的超时时间
                response = requests.get(url, timeout=10, stream=True)
                response.raise_for_status()
                
                # 检查Content-Type
                content_type = response.headers.get('Content-Type', '')
                if not content_type.startswith('image/'):
                    logger.warning(f"[{self.name}] 跳过非图片内容: {url}, Content-Type: {content_type}")
                    continue
                    
                # 检查内容长度
                content_length = response.headers.get('Content-Length')
                if content_length:
                    content_length = int(content_length)
                    if content_length > max_size_bytes:
                        logger.warning(f"[{self.name}] 图片太大，已跳过: {url}, 大小: {content_length/1024/1024:.2f}MB > {self.max_image_size_mb}MB")
                        continue
                
                # 读取图片数据，并检查实际大小
                img_data = response.content
                if len(img_data) > max_size_bytes:
                    logger.warning(f"[{self.name}] 图片实际大小超过限制，已跳过: {url}, 大小: {len(img_data)/1024/1024:.2f}MB > {self.max_image_size_mb}MB")
                    continue
                    
                results.append((content_id, img_data))
                logger.debug(f"已下载图片: {url}, 大小: {len(img_data)/1024:.1f}KB")
                
            except requests.RequestException as e:
                logger.warning(f"下载图片失败: {url}, 错误: {str(e)}")
                # 让retry装饰器处理重试
                raise
            except Exception as e:
                logger.warning(f"处理图片时出错: {url}, 错误: {str(e)}")
                
        return results

    def replace_image_urls_with_cids(self, html_content: str, image_map: Dict[str, str]) -> str:
        """将HTML中的图片URL替换为Content-ID引用
        
        Args:
            html_content: 原始HTML内容
            image_map: 图片URL到Content-ID的映射，格式为{image_url: content_id}
            
        Returns:
            替换后的HTML内容
        """
        if not html_content or not image_map:
            return html_content
            
        for url, cid in image_map.items():
            # 替换图片URL为cid引用
            html_content = html_content.replace(f'src="{url}"', f'src="cid:{cid}"')
            html_content = html_content.replace(f"src='{url}'", f'src="cid:{cid}"')
            
        return html_content

class RSSManager:
    def __init__(self, config_file: str = 'config.json'):
        self.config_file = config_file
        self.fetchers: Dict[str, RSSFetcher] = {}
        self.executor = ThreadPoolExecutor(max_workers=5)  # 最多5个并发任务
        self.load_config()
        
    def validate_config(self, config: Dict[str, Any]) -> bool:
        """验证配置文件的完整性和正确性
        
        Args:
            config: 配置字典
            
        Returns:
            配置是否有效
        """
        try:
            # 检查必需的配置部分
            if 'email_config' not in config:
                logger.error("配置错误: 缺少 'email_config' 部分")
                return False
                
            email_config = config['email_config']
            required_email_fields = ['smtp_server', 'smtp_port', 'sender_email', 
                                     'sender_password', 'receiver_email']
            
            for field in required_email_fields:
                if field not in email_config:
                    logger.error(f"配置错误: 'email_config' 中缺少 '{field}' 字段")
                    return False
            
            # 验证SMTP端口
            if not isinstance(email_config['smtp_port'], int):
                logger.error(f"配置错误: 'smtp_port' 必须是整数，当前值: {email_config['smtp_port']}")
                return False
                
            # 验证电子邮件格式
            for email_field in ['sender_email', 'receiver_email']:
                email = email_config[email_field]
                if not isinstance(email, str) or '@' not in email or '.' not in email:
                    logger.error(f"配置错误: '{email_field}' 不是有效的电子邮件地址: {email}")
                    return False
            
            # 检查RSS源配置
            if 'rss_sources' not in config or not config['rss_sources']:
                logger.error("配置错误: 缺少 'rss_sources' 部分或为空")
                return False
                
            for i, source in enumerate(config['rss_sources']):
                if 'name' not in source:
                    logger.error(f"配置错误: RSS源 #{i+1} 缺少 'name' 字段")
                    return False
                if 'url' not in source:
                    logger.error(f"配置错误: RSS源 '{source.get('name', f'#{i+1}')}' 缺少 'url' 字段")
                    return False
                    
                # 验证时间间隔
                if 'interval_minutes' in source and (
                    not isinstance(source['interval_minutes'], int) or 
                    source['interval_minutes'] < 1
                ):
                    logger.error(f"配置错误: RSS源 '{source.get('name')}' 的 'interval_minutes' 必须是正整数")
                    return False
                    
                # 验证资源限制参数
                if 'max_cache_days' in source and (
                    not isinstance(source['max_cache_days'], int) or 
                    source['max_cache_days'] < 1
                ):
                    logger.error(f"配置错误: RSS源 '{source.get('name')}' 的 'max_cache_days' 必须是正整数")
                    return False
                    
                if 'max_image_size_mb' in source and (
                    not isinstance(source['max_image_size_mb'], (int, float)) or 
                    source['max_image_size_mb'] <= 0
                ):
                    logger.error(f"配置错误: RSS源 '{source.get('name')}' 的 'max_image_size_mb' 必须是正数")
                    return False
                    
                if 'max_images_per_mail' in source and (
                    not isinstance(source['max_images_per_mail'], int) or 
                    source['max_images_per_mail'] < 1
                ):
                    logger.error(f"配置错误: RSS源 '{source.get('name')}' 的 'max_images_per_mail' 必须是正整数")
                    return False
                    
            # 检查RSS源名称唯一性
            names = [source['name'] for source in config['rss_sources']]
            if len(names) != len(set(names)):
                logger.error("配置错误: RSS源名称必须唯一")
                return False
                
            return True
            
        except Exception as e:
            logger.error(f"验证配置时出错: {str(e)}")
            return False
        
    def load_config(self):
        """从配置文件加载RSS源配置"""
        try:
            logger.info(f"正在从 {self.config_file} 加载配置...")
            
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
                
            # 验证配置
            if not self.validate_config(config):
                logger.error("配置验证失败，程序将退出")
                sys.exit(1)
                
            # 创建邮件发送器
            email_sender = EmailSender(config['email_config'])
                
            logger.info(f"读取到的配置内容: {json.dumps(config, ensure_ascii=False, indent=2)}")
            logger.info(f"RSS源列表: {[source['name'] for source in config['rss_sources']]}")
                
            # 清除现有的定时任务
            schedule.clear()
            
            # 创建新的RSS获取器
            for source in config['rss_sources']:
                name = source['name']
                logger.info(f"\n配置RSS源: {name}")
                logger.info(f"URL: {source['url']}")
                
                fetcher = RSSFetcher(
                    name=name,
                    url=source['url'],
                    email_sender=email_sender,
                    base_url=config.get('base_rss_url', ''),
                    save_dir=source.get('save_dir', f'data/{name}'),
                    txt_dir=source.get('txt_dir', DEFAULT_TXT_DIR),
                    max_cache_days=source.get('max_cache_days', 30),
                    max_image_size_mb=source.get('max_image_size_mb', 10.0),
                    max_images_per_mail=source.get('max_images_per_mail', 20)
                )
                self.fetchers[name] = fetcher
                
                # 设置定时任务，使用线程池执行
                interval = source.get('interval_minutes', 5)  # 默认5分钟检查一次
                schedule.every(interval).minutes.do(
                    lambda f=fetcher: self.executor.submit(f.fetch_rss)
                )
                logger.info(f"已设置定时任务，间隔: {interval}分钟")
                
            logger.info(f"\n成功加载 {len(self.fetchers)} 个RSS源:")
            for name, fetcher in self.fetchers.items():
                logger.info(f"- {name}: {fetcher.url}")
            
        except Exception as e:
            logger.error(f"加载配置文件时出错: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            sys.exit(1)
        
    def reload_config(self):
        """重新加载配置文件"""
        logger.info("重新加载配置文件...")
        self.load_config()
        
    def run(self):
        """运行所有RSS获取器"""
        try:
            # 先并发执行一次所有获取器
            logger.info("\n执行首次RSS获取...")
            futures = [self.executor.submit(fetcher.fetch_rss) 
                      for fetcher in self.fetchers.values()]
            for future in futures:
                future.result()  # 等待所有首次获取完成
            
            logger.info("\n开始监听RSS更新...")
            logger.info("程序将持续运行，监听以下RSS源的更新:")
            for name, fetcher in self.fetchers.items():
                logger.info(f"- {name}: 每{schedule.jobs[0].interval}分钟检查一次")
            logger.info("\n每当任何源有新文章时，都会自动保存到TXT文件并发送邮件\n")
            
            # 持续运行定时任务
            while True:
                schedule.run_pending()
                time.sleep(10)  # 降低CPU使用率
                
        except KeyboardInterrupt:
            logger.info("\n正在关闭RSS监听...")
            self.executor.shutdown(wait=True)
            logger.info("程序已安全退出")
        except Exception as e:
            logger.error(f"运行时出错: {str(e)}")
            self.executor.shutdown(wait=True)

def safe_unescape(url_str):
    """安全地解码URL字符串，处理可能的HTML实体
    
    Args:
        url_str: 需要解码的URL字符串
        
    Returns:
        解码后的URL字符串
    """
    if not url_str:
        return ''
    try:
        return html.unescape(url_str)
    except Exception as e:
        logger.warning(f"解码URL失败: {str(e)}, 返回原始URL")
        return url_str

def setup_signals():
    """设置信号处理，确保程序能够优雅退出"""
    import signal
    import sys
    
    def signal_handler(sig, frame):
        logger.info("\n收到终止信号，正在优雅退出...")
        # 在这里可以添加资源清理代码
        sys.exit(0)
        
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logger.debug("信号处理器已设置")

def main():
    """主函数"""
    # 解析命令行参数
    import argparse
    parser = argparse.ArgumentParser(description='RSS订阅获取和邮件推送工具')
    parser.add_argument('-c', '--config', default='config.json', help='配置文件路径')
    parser.add_argument('--debug', action='store_true', help='启用调试模式')
    parser.add_argument('--once', action='store_true', help='只获取一次RSS，然后退出')
    parser.add_argument('--version', action='store_true', help='显示版本信息')
    args = parser.parse_args()
    
    # 显示版本信息
    if args.version:
        print("RSS推文抓取和邮件推送工具 v1.0.0")
        return
    
    # 设置调试模式
    if args.debug:
        logger.setLevel(logging.DEBUG)
        for handler in logger.handlers:
            handler.setLevel(logging.DEBUG)
        logger.debug("调试模式已启用")
    
    try:
        # 设置信号处理
        setup_signals()
        
        # 显示启动信息
        logger.info("=" * 50)
        logger.info(" RSS订阅获取和邮件推送工具启动")
        logger.info("=" * 50)
        logger.info(f"配置文件: {args.config}")
        logger.info(f"系统信息: Python {sys.version}")
        logger.info(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 创建并运行RSS管理器
        manager = RSSManager(args.config)
        
        # 如果是一次性运行模式
        if args.once:
            logger.info("一次性运行模式，只获取一次RSS")
            
            # 并发获取所有RSS源
            futures = []
            for name, fetcher in manager.fetchers.items():
                logger.info(f"获取RSS: {name}")
                future = manager.executor.submit(fetcher.fetch_rss)
                futures.append((name, future))
                
            # 等待所有获取任务完成
            for name, future in futures:
                try:
                    future.result()
                    logger.info(f"RSS获取完成: {name}")
                except Exception as e:
                    logger.error(f"RSS获取失败: {name}, 错误: {str(e)}")
                    
            logger.info("所有RSS源获取完成，程序退出")
            manager.executor.shutdown(wait=True)
        else:
            # 持续监听模式
            manager.run()
            
    except KeyboardInterrupt:
        logger.info("\n正在关闭RSS监听...")
        logger.info("程序已安全退出")
    except Exception as e:
        logger.error(f"程序运行时发生错误: {str(e)}")
        logger.error(traceback.format_exc())
        sys.exit(1)
        
if __name__ == "__main__":
    main() 