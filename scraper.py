import asyncio
from playwright.async_api import async_playwright
from rich.console import Console
from urllib.parse import urlparse, urljoin
import re
import time

console = Console()

MEDIA_MIME_TYPES = [
    'video/', 'audio/', 'image/', 
    'application/vnd.apple.mpegurl',
    'application/x-mpegURL',
    'application/dash+xml'
]

MEDIA_EXTENSIONS = [
    '.mp4', '.mp3', '.wav', '.jpg', '.jpeg', '.png', '.gif', 
    '.webp', '.m3u8', '.mpd', '.ts', '.aac', '.flac'
]

async def sniff_media_with_progress(url: str, task_manager=None):
    found_media = []
    seen_urls = set()
    response_count = 0
    last_update_count = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        )
        page = await context.new_page()

        async def handle_response(response):
            nonlocal response_count, found_media, last_update_count
            response_count += 1
            
            try:
                content_type = response.headers.get("content-type", "").lower()
                response_url = response.url
                
                if response_url in seen_urls:
                    # 每 50 个响应更新一次进度，减少更新频率
                    if response_count % 50 == 0 and task_manager:
                        task_manager.update_scan_progress(
                            scanned_count=response_count, 
                            found_count=len(found_media)
                        )
                    return
                seen_urls.add(response_url)

                is_media = False
                media_type = "unknown"

                for m_type in MEDIA_MIME_TYPES:
                    if m_type in content_type:
                        is_media = True
                        if 'video' in content_type: media_type = 'video'
                        elif 'audio' in content_type: media_type = 'audio'
                        elif 'image' in content_type: media_type = 'image'
                        elif 'mpegurl' in content_type or 'm3u8' in content_type: media_type = 'stream'
                        break
                
                if not is_media:
                    parsed = urlparse(response_url)
                    path = parsed.path.lower()
                    for ext in MEDIA_EXTENSIONS:
                        if path.endswith(ext):
                            is_media = True
                            if ext in ['.m3u8', '.mpd']: media_type = 'stream'
                            elif ext in ['.mp4', '.ts']: media_type = 'video'
                            elif ext in ['.mp3', '.wav', '.aac']: media_type = 'audio'
                            else: media_type = 'image'
                            break
                
                if is_media:
                    content_length = response.headers.get("content-length", "0")
                    if int(content_length) > 10240:
                        media_info = {
                            "url": response_url,
                            "type": media_type,
                            "size": content_length,
                            "source": response.request.url
                        }
                        found_media.append(media_info)
                        console.print(f"[green]Found:[/green] {media_type} - {response_url[:50]}...")
                        
                        # 找到媒体时立即更新
                        if task_manager:
                            task_manager.update_scan_progress(
                                scanned_count=response_count, 
                                found_count=len(found_media),
                                current_url=f"Found {media_type}: {response_url[:60]}"
                            )
            except Exception as e:
                pass
            
            # 每 50 个响应更新一次扫描计数
            if response_count % 50 == 0 and task_manager:
                task_manager.update_scan_progress(
                    scanned_count=response_count, 
                    found_count=len(found_media)
                )

        page.on("response", handle_response)

        console.print(f"[blue]Analyzing:[/blue] {url}")
        if task_manager:
            task_manager.update_scan_progress(status="scanning", current_url=url)
        
        try:
            # 明确设置状态为 scanning
            if task_manager:
                task_manager.update_scan_progress(status="scanning", current_url=f"Loading: {url[:60]}")
            
            await page.goto(url, wait_until="networkidle", timeout=30000)
            
            # 等待动态内容
            if task_manager:
                task_manager.update_scan_progress(status="waiting", current_url="Waiting for dynamic content...")
            await page.wait_for_timeout(3000)
            
            # 完成扫描
            if task_manager:
                task_manager.update_scan_progress(status="completed", current_url="Scan completed")
                
        except Exception as e:
            console.print(f"[red]Error loading page:[/red] {e}")
            if task_manager:
                task_manager.update_scan_progress(status="error", current_url=str(e)[:60])
        
        await browser.close()

    unique_media = {m['url']: m for m in found_media}.values()
    return list(unique_media)

def start_sniffing_with_progress(url, task_manager=None):
    return asyncio.run(sniff_media_with_progress(url, task_manager))

def start_sniffing(url):
    return start_sniffing_with_progress(url, None)