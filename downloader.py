import os
import subprocess
import requests
from rich.console import Console
from urllib.parse import urlparse
import uuid

console = Console()
DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

def get_filename_from_url(url):
    parsed = urlparse(url)
    name = os.path.basename(parsed.path)
    if not name or '.' not in name:
        name = f"{uuid.uuid4().hex}.mp4"
    # 清理文件名中的非法字符
    name = "".join([c for c in name if c.isalnum() or c in "._-"])
    return name

def download_file(url, save_path, task_manager, task_id):
    try:
        if task_manager: task_manager.update_task(task_id, filename=os.path.basename(save_path))
        console.print(f"[cyan]Downloading:[/cyan] {save_path}")
        
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            total = int(r.headers.get('content-length', 0))
            downloaded = 0
            
            with open(save_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    # 更新进度 (仅适用于已知长度的文件)
                    if task_manager and total > 0:
                        progress = int((downloaded / total) * 100)
                        task_manager.update_task(task_id, progress=progress)
        
        console.print(f"[green]Success:[/green] {save_path}")
        return True
    except Exception as e:
        console.print(f"[red]Failed:[/red] {e}")
        return False

def download_stream(url, save_path, task_manager, task_id):
    """使用 ffmpeg 下载 m3u8 流"""
    try:
        if task_manager: task_manager.update_task(task_id, filename=os.path.basename(save_path))
        console.print(f"[cyan]Streaming (FFmpeg):[/cyan] {save_path}")
        
        cmd = [
            'ffmpeg', '-y', 
            '-i', url, 
            '-c', 'copy', 
            '-hide_banner', 
            '-loglevel', 'error',
            save_path
        ]
        # 注意：subprocess.run 会阻塞线程，直到完成
        # 若要获取 ffmpeg 实时进度需使用 pipe  stdout，此处简化处理
        subprocess.run(cmd, check=True)
        
        if task_manager: task_manager.update_task(task_id, progress=100)
        console.print(f"[green]Stream Saved:[/green] {save_path}")
        return True
    except Exception as e:
        console.print(f"[red]FFmpeg Error:[/red] {e}")
        return False

def process_download(media_info, task_manager=None, task_id=None):
    url = media_info['url']
    m_type = media_info['type']
    filename = get_filename_from_url(url)
    
    if m_type == 'stream':
        filename = filename.replace('.m3u8', '.mp4').replace('.mpd', '.mp4')
    
    save_path = os.path.join(DOWNLOAD_FOLDER, filename)
    
    if m_type == 'stream':
        success = download_stream(url, save_path, task_manager, task_id)
    else:
        success = download_file(url, save_path, task_manager, task_id)
        
    return {"success": success, "path": save_path if success else None}