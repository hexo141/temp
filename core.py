import sys
import cv2
import os
import time
import threading
import queue
from multiprocessing import Queue, Event

# Ultralytics YOLO 是唯一需要的推理库
try:
    from ultralytics import YOLO
    ULTRALYTICS_AVAILABLE = True
except ImportError:
    ULTRALYTICS_AVAILABLE = False
    print("错误：请安装 ultralytics (pip install ultralytics)")

# 可选：检查 OpenVINO 是否可用（用于提示）
try:
    from openvino import Core
    OV_AVAILABLE = True
except ImportError:
    OV_AVAILABLE = False

# ==============================================================================
# 配置部分
# ==============================================================================
MODEL_PATH = "yolo26n-face.pt"           # 原始 PyTorch 模型路径
DEFAULT_IMG_SZ = 320                     # 推理图像尺寸
CONF_THRESHOLD = 0.45                    # 置信度阈值
MAX_DETECTIONS = 100                     # 最大检测数量
DEVICE = "cpu"                           # ultralytics 设备（openvino 后端自动使用 CPU）

# ==============================================================================
# 辅助函数：确保 OpenVINO IR 模型存在，返回模型文件夹路径
# ==============================================================================
def ensure_openvino_model(original_pt_path):
    """
    如果 OpenVINO IR 模型文件夹不存在，则使用 ultralytics 导出。
    返回模型文件夹路径（如 'yolo26n-face_openvino_model'），失败则返回 None。
    """
    base_name = os.path.splitext(os.path.basename(original_pt_path))[0]
    ov_dir = f"{base_name}_openvino_model"
    ov_xml = os.path.join(ov_dir, f"{base_name}.xml")

    if os.path.exists(ov_xml):
        print(f"OpenVINO IR 模型已存在: {ov_dir}")
        return ov_dir

    if not ULTRALYTICS_AVAILABLE:
        print("错误：需要 ultralytics 来导出 OpenVINO 模型")
        return None

    print("正在将 PyTorch 模型导出为 OpenVINO IR 格式（首次运行需要，后续将直接使用）...")
    try:
        model = YOLO(original_pt_path)
        # 导出为 OpenVINO 格式，返回导出路径（通常是文件夹路径）
        export_path = model.export(format="openvino", imgsz=DEFAULT_IMG_SZ, half=False)
        if os.path.exists(export_path):
            if os.path.isfile(export_path):
                ov_dir = os.path.dirname(export_path)
            else:
                ov_dir = export_path
            print(f"导出成功: {ov_dir}")
            return ov_dir
        else:
            print("导出失败：找不到生成的 IR 文件")
            return None
    except Exception as e:
        print(f"导出 OpenVINO 模型失败：{e}")
        return None

# ==============================================================================
# 子进程工作函数 v3 (异步推理版)
# ==============================================================================
def worker_process_v2(camera_index, model_path, frame_queue, raw_queue,
                      stop_event, manager_dict, init_queue):
    """
    在独立进程中运行摄像头捕获和 YOLO 推理（异步模式：捕获与推理解耦）
    :param camera_index: 摄像头索引
    :param model_path: 原始 PyTorch 模型路径（.pt）
    :param frame_queue: 存放带框图像帧的队列（供 UI 显示）
    :param raw_queue:   存放原始图像帧的队列（供点名截图）
    :param stop_event:  停止信号
    :param manager_dict: 共享字典，存储最新检测结果
    :param init_queue:   初始化完成队列，发送 True/False 表示成功/失败
    """
    # ---------- 初始化摄像头 ----------
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        print(f"无法打开摄像头 {camera_index}")
        init_queue.put(False)
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)

    # ---------- 加载模型（优先使用 OpenVINO） ----------
    ov_model_dir = ensure_openvino_model(model_path)
    use_openvino = False
    if ov_model_dir and os.path.exists(ov_model_dir):
        try:
            model = YOLO(ov_model_dir, task='detect')
            print("使用 OpenVINO 加速推理 (通过 Ultralytics)")
            use_openvino = True
        except Exception as e:
            print(f"OpenVINO 模型加载失败：{e}，回退到 PyTorch CPU")
            model = YOLO(model_path)
    else:
        print("未找到 OpenVINO 模型，使用 PyTorch CPU 推理")
        model = YOLO(model_path)

    # 初始化成功，通知主进程
    init_queue.put(True)

    # ---------- 异步队列 ----------
    # 待推理队列（存放原始帧，供推理线程消费）
    inference_queue = queue.Queue(maxsize=1)   # 只保留最新一帧待推理
    # 推理结果队列（存放带框帧和检测结果）
    result_queue = queue.Queue(maxsize=1)

    # 停止事件标志（用于线程安全退出）
    capture_stop = threading.Event()
    inference_stop = threading.Event()

    # ---------- 捕获线程 ----------
    def capture_loop():
        """持续捕获摄像头帧，放入 raw_queue 和 inference_queue"""
        while not capture_stop.is_set() and not stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                break

            # 放入原始帧队列（供主进程点名截图）
            try:
                # 清空旧帧，保证最新
                if raw_queue.full():
                    raw_queue.get_nowait()
                raw_queue.put(frame)
            except:
                pass

            # 放入待推理队列（供推理线程处理）
            try:
                # 如果队列满，丢弃旧帧（保证最新）
                if inference_queue.full():
                    inference_queue.get_nowait()
                inference_queue.put(frame)
            except:
                pass

        print("捕获线程退出")

    # ---------- 推理线程 ----------
    def inference_loop():
        """从 inference_queue 取帧进行推理，结果放入 frame_queue 和 shared_dict"""
        while not inference_stop.is_set() and not stop_event.is_set():
            try:
                # 阻塞等待新帧，超时 0.5 秒以响应停止事件
                frame = inference_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                # 执行推理（同步调用，但不影响捕获线程）
                results = model(frame, imgsz=DEFAULT_IMG_SZ, conf=CONF_THRESHOLD,
                                max_det=MAX_DETECTIONS, verbose=False, device=DEVICE)
                result = results[0]
                boxes = result.boxes

                frame_display = frame.copy()
                current_detections = []

                if boxes is not None:
                    for box in boxes:
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        conf = float(box.conf[0])

                        cv2.rectangle(frame_display, (x1, y1), (x2, y2), (0, 255, 0), 2)
                        label = f"Face {conf:.2f}"
                        cv2.putText(frame_display, label, (x1, y1 - 10),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

                        current_detections.append({
                            'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
                            'conf': conf
                        })

                # 更新共享字典（最新检测结果）
                manager_dict['latest_detections'] = current_detections

                # 转换颜色并放入带框帧队列（供 UI 显示）
                frame_rgb = cv2.cvtColor(frame_display, cv2.COLOR_BGR2RGB)
                try:
                    if frame_queue.full():
                        frame_queue.get_nowait()
                    frame_queue.put(frame_rgb)
                except:
                    pass

                # 可选：将结果放入 result_queue（本例未使用）
                # result_queue.put((frame_rgb, current_detections))

            except Exception as e:
                print(f"推理错误：{e}")
                # 出错时至少传递原始帧（无框）
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                try:
                    if frame_queue.full():
                        frame_queue.get_nowait()
                    frame_queue.put(frame_rgb)
                except:
                    pass

        print("推理线程退出")

    # ---------- 启动线程 ----------
    capture_thread = threading.Thread(target=capture_loop, daemon=True)
    inference_thread = threading.Thread(target=inference_loop, daemon=True)
    capture_thread.start()
    inference_thread.start()

    # 等待停止信号
    try:
        while not stop_event.is_set():
            time.sleep(0.1)
    finally:
        # 通知线程停止
        capture_stop.set()
        inference_stop.set()
        capture_thread.join(timeout=1)
        inference_thread.join(timeout=1)

    cap.release()
    print("推理进程已停止")
    sys.exit(0)