from flask import Flask, render_template, request, jsonify, Response
from scraper import start_sniffing_with_progress
from downloader import process_download
from rich.console import Console
import threading
import uuid
import time
import json

app = Flask(__name__)
console = Console()

# --- 任务管理器 ---
class TaskManager:
    def __init__(self):
        self.tasks = {}
        self.scan_progress = {
            "status": "idle", 
            "current_url": "", 
            "found_count": 0, 
            "scanned_count": 0
        }
        self.last_scan_update = 0  # 限制更新频率
        self.lock = threading.Lock()
        self.scan_clients = []

    def add_task(self, task_id, media_info):
        with self.lock:
            self.tasks[task_id] = {
                "id": task_id,
                "url": media_info['url'],
                "type": media_info['type'],
                "filename": "Pending...",
                "status": "pending",
                "progress": 0
            }

    def update_task(self, task_id, status=None, filename=None, progress=None):
        with self.lock:
            if task_id in self.tasks:
                if status: self.tasks[task_id]['status'] = status
                if filename: self.tasks[task_id]['filename'] = filename
                if progress is not None: self.tasks[task_id]['progress'] = progress

    def update_scan_progress(self, status="", current_url="", found_count=None, scanned_count=None):
        with self.lock:
            current_time = time.time()
            # 限制更新频率为每 0.3 秒一次，避免前端闪烁
            if current_time - self.last_scan_update < 0.3:
                return
            
            changed = False
            if status and status != self.scan_progress['status']:
                self.scan_progress['status'] = status
                changed = True
            if current_url and current_url != self.scan_progress['current_url']:
                self.scan_progress['current_url'] = current_url
                changed = True
            if found_count is not None and found_count != self.scan_progress['found_count']:
                self.scan_progress['found_count'] = found_count
                changed = True
            if scanned_count is not None and scanned_count != self.scan_progress['scanned_count']:
                self.scan_progress['scanned_count'] = scanned_count
                changed = True
            
            if changed:
                self.last_scan_update = current_time
                self.broadcast_scan_progress()

    def broadcast_scan_progress(self):
        data = json.dumps(self.scan_progress)
        for client in self.scan_clients[:]:
            try:
                client.put(data)
            except:
                self.scan_clients.remove(client)

    def get_all_tasks(self):
        with self.lock:
            return list(self.tasks.values())

task_manager = TaskManager()

# --- SSE 进度推送 ---
@app.route('/scan-progress')
def scan_progress():
    def generate():
        queue = []
        task_manager.scan_clients.append(queue)
        try:
            # 立即发送当前状态
            yield f"data: {json.dumps(task_manager.scan_progress)}\n\n"
            while True:
                if queue:
                    data = queue.pop(0)
                    yield f"data: {data}\n\n"
                else:
                    yield f"data: {json.dumps(task_manager.scan_progress)}\n\n"
                time.sleep(0.3)  # 降低推送频率
        except GeneratorExit:
            if queue in task_manager.scan_clients:
                task_manager.scan_clients.remove(queue)
    return Response(generate(), mimetype='text/event-stream')

# --- 路由 ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    data = request.json
    url = data.get('url')
    if not url or not url.startswith(('http://', 'https://')):
        return jsonify({"error": "Invalid URL"}), 400

    console.print(f"[bold magenta]Analyzing:[/bold magenta] {url}")
    task_manager.update_scan_progress(status="starting", current_url=url)
    
    try:
        media_list = start_sniffing_with_progress(url, task_manager)
        console.print(f"[bold green]Found {len(media_list)} items.[/bold green]")
        task_manager.update_scan_progress(status="completed", found_count=len(media_list))
        time.sleep(0.5)  # 确保最后状态被推送
        return jsonify({"success": True, "data": media_list})
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        task_manager.update_scan_progress(status="error")
        return jsonify({"error": str(e)}), 500

@app.route('/download', methods=['POST'])
def download():
    data = request.json
    items = data.get('items', [])
    
    if not items and data.get('media_info'):
        items = [data.get('media_info')]
    
    if not items:
        return jsonify({"error": "No items selected", "received": data}), 400

    task_ids = []
    
    def run_download_task(media_info):
        task_id = str(uuid.uuid4())
        task_manager.add_task(task_id, media_info)
        
        try:
            task_manager.update_task(task_id, status="downloading")
            result = process_download(media_info, task_manager, task_id)
            
            if result.get("success"):
                task_manager.update_task(task_id, status="completed", filename=result.get("path"))
                console.print(f"[green]Task {task_id[:8]} Completed[/green]")
            else:
                task_manager.update_task(task_id, status="failed")
                console.print(f"[red]Task {task_id[:8]} Failed[/red]")
        except Exception as e:
            task_manager.update_task(task_id, status="failed")
            console.print(f"[red]Task {task_id[:8]} Exception: {e}[/red]")

    for item in items:
        task_id = str(uuid.uuid4())
        task_ids.append(task_id)
        thread = threading.Thread(target=run_download_task, args=(item,))
        thread.start()
        time.sleep(0.1) 

    return jsonify({
        "success": True, 
        "message": f"Started {len(items)} tasks", 
        "task_ids": task_ids, 
        "count": len(items)
    })

@app.route('/tasks', methods=['GET'])
def get_tasks():
    tasks = task_manager.get_all_tasks()
    return jsonify({"tasks": tasks})

if __name__ == '__main__':
    console.print("[bold blue]Server Starting...[/bold blue]")
    app.run(debug=True, port=5000, threaded=True)