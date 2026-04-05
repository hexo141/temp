import sys
import multiprocessing
from PySide6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout,
                               QLabel, QProgressBar, QMessageBox)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

# 延迟导入，以便先显示进度条
# 注意：我们需要先导入 wmi，如果没安装会自动处理
try:
    import wmi
    WMI_AVAILABLE = True
except ImportError:
    WMI_AVAILABLE = False

class CustomProgressDialog(QDialog):
    def __init__(self, title="初始化中", label_text="正在启动程序，请稍候...", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.resize(400, 85)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # 文本标签
        self.text_label = QLabel(label_text)
        layout.addWidget(self.text_label)
        
        # 水平布局：箭头图标 + 进度条
        h_layout = QHBoxLayout()
        h_layout.setSpacing(8)
        
        # 创建图标标签
        self.icon_label = QLabel()
        try:
            pixmap = QPixmap("arrow_right.png")
            if not pixmap.isNull():
                scaled_pixmap = pixmap.scaled(24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.icon_label.setPixmap(scaled_pixmap)
            else:
                self.icon_label.setText("→")
                self.icon_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        except:
            self.icon_label.setText("→")
            self.icon_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        
        h_layout.addWidget(self.icon_label)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        h_layout.addWidget(self.progress_bar)
        
        layout.addLayout(h_layout)
        
    def setValue(self, value):
        self.progress_bar.setValue(value)
        QApplication.processEvents()
        
    def setLabelText(self, text):
        self.text_label.setText(text)
        QApplication.processEvents()

def dynamic_imports(progress_callback):
    progress_callback(10, "正在导入 OpenCV...")
    import cv2
    progress_callback(30, "OpenCV 导入完成")

    progress_callback(40, "正在准备其他模块...")
    import os
    from multiprocessing import Process, Queue, Event, Manager
    progress_callback(50, "基础模块准备完成")

    return cv2, os, Process, Queue, Event, Manager

def get_camera_names(cv2, max_id=10):
    """
    强制获取摄像头名称的函数
    优先使用 WMI 获取 Windows 设备名称，如果失败则回退到 OpenCV 尝试
    """
    cameras = []
    
    # 方法 1: 使用 WMI (仅 Windows) - 最可靠
    if WMI_AVAILABLE:
        try:
            c = wmi.WMI()
            # 查询所有图像设备
            for device in c.Win32_PnPEntity():
                if device.ConfigManagerErrorCode == 0:  # 设备正常
                    # 粗略判断是否为摄像头
                    if device.Name and ("Camera" in device.Name or 
                                      "Camera" in device.Description or 
                                      "Image" in device.Description or
                                      "USB Video" in device.Description):
                        # 这里我们获取到了名称，但需要映射到 OpenCV 的索引
                        # 我们先记录名称，稍后通过探测匹配索引
                        pass
            
            # 由于 WMI 无法直接给出 OpenCV 索引，我们做一个映射表
            # 我们假设按顺序打开的设备对应 WMI 列表中的顺序（通常成立）
            wmi_names = []
            for device in c.Win32_PnPEntity():
                if (device.Name and ("Camera" in device.Name or 
                                   "Camera" in device.Description or 
                                   "Image" in device.Description)) and device.ConfigManagerErrorCode == 0:
                    wmi_names.append(device.Name)
            
            # 现在探测 OpenCV 索引
            for i in range(max_id):
                cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                if cap.isOpened():
                    ret, _ = cap.read()
                    if ret:
                        # 尝试从 WMI 名单里取名字
                        if i < len(wmi_names):
                            name = wmi_names[i]
                        else:
                            # 如果 WMI 名单不够，尝试用 OpenCV 属性
                            try:
                                prop_id = getattr(cv2, 'CAP_PROP_DEVICE_FRIENDLY_NAME', None)
                                if prop_id is not None:
                                    friendly_name = cap.get(prop_id)
                                    if isinstance(friendly_name, str) and friendly_name.strip():
                                        name = friendly_name
                                    else:
                                        name = f"摄像头 {i} - {device.Name if 'device' in locals() else 'Unknown'}"
                                else:
                                    name = f"摄像头 {i} (WMI回退)"
                            except:
                                name = f"摄像头 {i} (WMI回退)"
                        cameras.append((i, name))
                    cap.release()
                    # 如果连续几个打不开，就break（防止遍历太久）
                    if len(cameras) > 0 and not ret:
                        break
            if cameras:
                return cameras
        except Exception as e:
            print(f"WMI 获取失败: {e}")

    # 方法 2: 传统回退 (如果 WMI 失败)
    # 强制使用 DirectShow 并尝试获取属性
    for i in range(max_id):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                name = f"摄像头 {i}"
                try:
                    # 尝试获取友好名称
                    prop_id = getattr(cv2, 'CAP_PROP_DEVICE_FRIENDLY_NAME', 0)
                    friendly_name = cap.get(prop_id)
                    if friendly_name != 0.0: # OpenCV 有时返回 float
                        if isinstance(friendly_name, float):
                            name = f"摄像头 {i} (ID: {int(friendly_name)})"
                        else:
                            name = str(friendly_name)
                except:
                    pass
                cameras.append((i, name))
            cap.release()
    
    # 最终兜底
    if not cameras:
        cameras = ")]
    
    return cameras

def main():
    multiprocessing.freeze_support()
    
    app = QApplication(sys.argv)
    
    progress = CustomProgressDialog("初始化中", "正在启动程序，请稍候...")
    progress.show()
    
    def update_progress(value, text):
        progress.setValue(value)
        progress.setLabelText(text)
    
    try:
        cv2, os, Process, Queue, Event, Manager = dynamic_imports(update_progress)
        
        update_progress(55, "检查模型文件夹...")
        MODEL_PATH = "./yolo26n-face_openvino_model"
        if not os.path.exists(MODEL_PATH):
            progress.close()
            QMessageBox.critical(None, "错误", f"模型文件夹 {MODEL_PATH} 不存在，请放入程序目录。")
            sys.exit(1)
        
        update_progress(60, "正在扫描摄像头...")
        available_cams = get_camera_names(cv2, max_id=5) # 减少扫描数量提高速度
        
        # 调试输出：打印获取到的摄像头信息
        print("=== 扫描到的摄像头列表 ===")
        for idx, name in available_cams:
            print(f"索引: {idx}, 名称: {name}")
        print("=========================")
        
        if not available_cams:
            progress.close()
            QMessageBox.critical(None, "错误", "未检测到可用摄像头，程序将退出。")
            sys.exit(1)
            
        selected_cam = available_cams[0][0]
        update_progress(70, f"已选择: {available_cams[0][1]}")
        
        update_progress(75, "准备多进程通信...")
        frame_queue = Queue(maxsize=1)
        raw_queue = Queue(maxsize=1)
        init_queue = Queue(maxsize=1)
        stop_event = Event()
        manager = Manager()
        shared_dict = manager.dict()
        shared_dict['latest_detections'] = []
        
        update_progress(80, "启动人脸检测引擎...")
        from core import worker_process_v2, MODEL_PATH as CORE_MODEL_PATH
        worker = Process(
            target=worker_process_v2,
            args=(selected_cam, CORE_MODEL_PATH, frame_queue,
                  raw_queue, stop_event, shared_dict, init_queue)
        )
        worker.start()
        
        update_progress(85, "等待摄像头和模型加载...")
        import time
        timeout = 60
        start_time = time.time()
        while init_queue.empty():
            if time.time() - start_time > timeout:
                progress.close()
                QMessageBox.critical(None, "错误", "初始化超时，请检查摄像头或模型文件。")
                worker.terminate()
                sys.exit(1)
            QApplication.processEvents()
            time.sleep(0.05)
        init_success = init_queue.get()
        if not init_success:
            progress.close()
            QMessageBox.critical(None, "错误", "子进程初始化失败（摄像头打开或模型加载错误）。")
            worker.terminate()
            sys.exit(1)
        
        update_progress(95, "准备界面...")
        
        from ui import FaceRollCallApp
        window = FaceRollCallApp(
            frame_queue=frame_queue,
            raw_queue=raw_queue,
            shared_dict=shared_dict,
            stop_event=stop_event,
            worker_process=worker,
            selected_cam_index=selected_cam,
            available_cams=available_cams # 传入 (index, name) 列表
        )
        
        update_progress(100, "启动完成")
        progress.close()
        
        window.show()
        sys.exit(app.exec())
        
    except Exception as e:
        progress.close()
        QMessageBox.critical(None, "初始化失败", f"发生错误：{str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
