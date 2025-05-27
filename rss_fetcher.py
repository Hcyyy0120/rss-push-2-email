import os
import json
import time
import html
import smtplib
import schedule
import requests
import feedparser
from datetime import datetime
from dateutil import parser
from typing import Dict, List, Set
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

class EmailSender:
    def __init__(self, config):
        self.config = config
        self.smtp_server = config['smtp_server']
        self.smtp_port = config['smtp_port']
        self.sender_email = config['sender_email']
        self.sender_password = config['sender_password']
        self.receiver_email = config['receiver_email']
        
    def send_email(self, subject: str, content: str):
        """发送邮件"""
        try:
            # 创建邮件对象
            msg = MIMEMultipart()
            msg['From'] = self.sender_email
            msg['To'] = self.receiver_email
            msg['Subject'] = Header(subject, 'utf-8')
            
            # 添加正文
            msg.attach(MIMEText(content, 'plain', 'utf-8'))
            
            # 连接SMTP服务器并发送
            with smtplib.SMTP_SSL(self.smtp_server, self.smtp_port) as server:
                server.login(self.sender_email, self.sender_password)
                server.send_message(msg)
                #在发送过程中需要手动调用一下server.quit()方法关闭会话，否则会报一个错误
                server.quit() # 结束会话
                
            print(f"邮件已发送: {subject}")
            return True
            
        except Exception as e:
            print(f"发送邮件时出错: {str(e)}")
            return False

class RSSFetcher:
    def __init__(self, name: str, url: str, email_sender: EmailSender, save_dir: str = 'data', txt_dir: str = None):
        self.name = name
        self.url = url
        self.save_dir = save_dir
        self.txt_dir = txt_dir or "C:\\Users\\16691\\Desktop\\rsspush"
        self.processed_guids = set()  # 用于存储已处理的条目GUID
        self.cache_file = os.path.join(save_dir, f"{name}_processed_guids.json")
        
        # 创建保存目录
        for directory in [save_dir, self.txt_dir]:
            if not os.path.exists(directory):
                os.makedirs(directory)
                
        # 加载已处理的GUID缓存
        self.load_processed_guids()
        self.email_sender = email_sender
            
    def load_processed_guids(self):
        """加载已处理的GUID缓存"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self.processed_guids = set(json.load(f))
        except Exception as e:
            print(f"[{self.name}] 加载GUID缓存出错: {str(e)}")
            
    def save_processed_guids(self):
        """保存已处理的GUID缓存"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(list(self.processed_guids), f)
        except Exception as e:
            print(f"[{self.name}] 保存GUID缓存出错: {str(e)}")
            
    def clean_html(self, text: str) -> str:
        """清理HTML标签和转义字符"""
        # 替换常见的HTML实体
        text = html.unescape(text)
        # 移除HTML标签
        text = text.replace('<br>', '\n')
        text = text.replace('</div>', '\n')
        # 移除其他HTML标签
        while '<' in text and '>' in text:
            start = text.find('<')
            end = text.find('>', start)
            if end == -1:
                break
            text = text[:start] + text[end+1:]
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
                f.write(f"新增文章数: {len(new_entries)}\n\n")
                
                # 写入每篇新文章
                for entry in new_entries:
                    # 格式化发布时间
                    published_time = entry.get('published', '')
                    try:
                        if published_time:
                            dt = parser.parse(published_time)
                            published_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                    except Exception:
                        pass  # 如果解析失败，保持原始格式
                    
                    f.write(f"发布时间: {published_time}\n")
                    f.write(f"作者: {entry.get('author', '')}\n")
                    f.write(f"标题: {entry.get('title', '')}\n")
                    f.write(f"链接: {entry.get('link', '')}\n")
                    
                    # 清理并写入描述
                    description = self.clean_html(entry.get('description', ''))
                    f.write(f"内容:\n{description}\n")
                    f.write("\n" + "="*50 + "\n\n")
                    
            print(f"[{self.name}] 发现{len(new_entries)}篇新文章，已保存到: {file_path}")
            
        except Exception as e:
            print(f"[{self.name}] 保存新文章时出错: {str(e)}")
            
    def format_entries_for_email(self, entries) -> str:
        """格式化条目为邮件内容"""
        content = []
        for entry in entries:
            content.append(f"标题: {entry.get('title', '')}")
            content.append(f"作者: {entry.get('author', '')}")
            # 格式化发布时间
            published_time = entry.get('published', '')
            try:
                if published_time:
                    dt = parser.parse(published_time)
                    published_time = dt.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                pass  # 如果解析失败，保持原始格式
            content.append(f"发布时间: {published_time}")
            content.append(f"链接: {entry.get('link', '')}")
            content.append("\n内容:")
            content.append(self.clean_html(entry.get('description', '')))
            content.append("\n" + "="*50 + "\n")
        return "\n".join(content)
    
    def send_new_entries_email(self, new_entries):
        """将新条目通过邮件发送"""
        if not new_entries:
            return
            
        subject = f"RSS更新 - {self.name} - {len(new_entries)}篇新文章"
        content = f"RSS源: {self.url}\n"
        content += f"更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        content += f"新增文章数: {len(new_entries)}\n\n"
        content += self.format_entries_for_email(new_entries)
        
        self.email_sender.send_email(subject, content)
    
    def fetch_rss(self):
        """获取RSS更新"""
        try:
            # 获取RSS内容
            response = requests.get(self.url, timeout=30)
            feed = feedparser.parse(response.content)
            
            # 检查新条目
            new_entries = []
            for entry in feed.entries:
                guid = entry.get('guid', '') or entry.get('link', '')
                if guid and guid not in self.processed_guids:
                    # 格式化发布时间
                    published_time = entry.get('published', '')
                    try:
                        if published_time:
                            dt = parser.parse(published_time)
                            entry['published'] = dt.strftime('%Y-%m-%d %H:%M:%S')
                    except Exception:
                        pass  # 如果解析失败，保持原始格式
                    
                    new_entries.append(entry)
                    self.processed_guids.add(guid)
            
            # 如果有新条目，保存并发送邮件
            if new_entries:
                self.save_new_entries_as_txt(new_entries)
                self.save_processed_guids()
                self.send_new_entries_email(new_entries)
                
                # 同时保存JSON格式（可选）
                for entry in new_entries:
                    # 解析日期
                    published = parser.parse(entry.get('published', ''))
                    date_str = published.strftime('%Y%m%d')
                    
                    # 准备保存的数据
                    item_data = {
                        'title': entry.get('title', ''),
                        'link': entry.get('link', ''),
                        'published': entry.get('published', ''),
                        'description': entry.get('description', ''),
                        'content': entry.get('content', [{}])[0].get('value', '') if 'content' in entry else '',
                        'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }
                    
                    # 构建文件名
                    filename = f"{date_str}_{hash(entry.link)}.json"
                    file_path = os.path.join(self.save_dir, filename)
                    
                    # 保存到文件
                    with open(file_path, 'w', encoding='utf-8') as f:
                        json.dump(item_data, f, ensure_ascii=False, indent=2)
            
        except requests.exceptions.RequestException as e:
            print(f"[{self.name}] 网络请求出错: {str(e)}")
        except Exception as e:
            print(f"[{self.name}] 获取RSS内容时出错: {str(e)}")

class RSSManager:
    def __init__(self, config_file: str = 'config.json'):
        self.config_file = config_file
        self.fetchers: Dict[str, RSSFetcher] = {}
        self.executor = ThreadPoolExecutor(max_workers=5)  # 最多5个并发任务
        self.load_config()
        
    def load_config(self):
        """从配置文件加载RSS源配置"""
        try:
            print(f"\n正在从 {self.config_file} 加载配置...")
            
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
                
            # 创建邮件发送器
            email_sender = EmailSender(config['email_config'])
                
            print(f"读取到的配置内容: {json.dumps(config, ensure_ascii=False, indent=2)}")
            print(f"RSS源列表: {[source['name'] for source in config['rss_sources']]}")
                
            # 清除现有的定时任务
            schedule.clear()
            
            # 创建新的RSS获取器
            for source in config['rss_sources']:
                name = source['name']
                print(f"\n配置RSS源: {name}")
                print(f"URL: {source['url']}")
                
                fetcher = RSSFetcher(
                    name=name,
                    url=source['url'],
                    email_sender=email_sender,
                    save_dir=source.get('save_dir', f'data/{name}'),
                    txt_dir=source.get('txt_dir', "C:\\Users\\16691\\Desktop\\rsspush")
                )
                self.fetchers[name] = fetcher
                
                # 设置定时任务，使用线程池执行
                interval = source.get('interval_minutes', 5)  # 默认5分钟检查一次
                schedule.every(interval).minutes.do(
                    lambda f=fetcher: self.executor.submit(f.fetch_rss)
                )
                print(f"已设置定时任务，间隔: {interval}分钟")
                
            print(f"\n成功加载 {len(self.fetchers)} 个RSS源:")
            for name, fetcher in self.fetchers.items():
                print(f"- {name}: {fetcher.url}")
            
        except Exception as e:
            print(f"加载配置文件时出错: {str(e)}")
            import traceback
            print(traceback.format_exc())
            
    def reload_config(self):
        """重新加载配置文件"""
        print("重新加载配置文件...")
        self.load_config()
        
    def run(self):
        """运行所有RSS获取器"""
        try:
            # 先并发执行一次所有获取器
            print("\n执行首次RSS获取...")
            futures = [self.executor.submit(fetcher.fetch_rss) 
                      for fetcher in self.fetchers.values()]
            for future in futures:
                future.result()  # 等待所有首次获取完成
            
            print("\n开始监听RSS更新...")
            print("程序将持续运行，监听以下RSS源的更新:")
            for name, fetcher in self.fetchers.items():
                print(f"- {name}: 每{schedule.jobs[0].interval}分钟检查一次")
            print("\n每当任何源有新文章时，都会自动保存到TXT文件并发送邮件\n")
            
            # 持续运行定时任务
            while True:
                schedule.run_pending()
                time.sleep(10)  # 降低CPU使用率
                
        except KeyboardInterrupt:
            print("\n正在关闭RSS监听...")
            self.executor.shutdown(wait=True)
            print("程序已安全退出")
        except Exception as e:
            print(f"运行时出错: {str(e)}")
            self.executor.shutdown(wait=True)

def main():
    manager = RSSManager()
    manager.run()

if __name__ == "__main__":
    main() 