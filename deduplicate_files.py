#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件处理工具 - 文件去重与图片分类

功能描述：
    1. 文件去重：使用感知哈希算法检测视觉相似的图片，使用MD5哈希检测普通文件
    2. 图片分类：根据拍摄日期或修改日期将图片分类到不同文件夹
    3. 安全处理：重复文件不会被删除，而是移动到专用目录

主要特性：
    - 多线程并行处理，提高大目录扫描效率
    - 支持中文路径
    - 支持多种保留策略
    - 支持命令行和GUI两种模式

使用方法：
    1. GUI模式：直接运行编译后的exe文件或脚本
       - 选择处理目录
       - 选择功能（仅去重、仅分类、先去重再分类）
       - 选择保留策略
       - 点击"开始处理"按钮

    2. 命令行模式：
       - 去重功能：python deduplicate_files.py -f deduplicate -d "目录路径"
       - 分类功能：python deduplicate_files.py -f organize -d "目录路径"
       - 组合功能：python deduplicate_files.py -f both -d "目录路径"

保留策略说明：
    - oldest：保留创建时间最早的文件
    - newest：保留创建时间最新的文件
    - largest：保留文件大小最大的文件
    - smallest：保留文件大小最小的文件
    - shortest_path：保留路径最短的文件
    - longest_path：保留路径最长的文件

依赖项：
    - os, sys, hashlib, datetime, shutil：Python标准库
    - PIL (Pillow)：处理图片EXIF信息和图片读取
    - cv2 (OpenCV)：计算图像感知哈希
    - numpy：数值计算支持
    - tkinter：GUI界面
    - argparse：命令行参数解析
    - concurrent.futures：多线程并行处理

注意事项：
    1. 处理过程中请勿关闭应用程序
    2. 重复文件将被移动到当前目录下的duplicates_YYYYMMDD_HHMMSS文件夹
    3. 支持的图片格式：.jpg, .jpeg, .png, .gif, .bmp, .tiff
    4. 处理大文件夹时可能需要较长时间，建议先在测试目录上运行
"""

import os
import sys
import hashlib
import datetime
import shutil
from PIL import Image
import argparse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
import cv2
import numpy as np

# 支持的图片格式常量（元组类型，用于endswith方法）
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff")

# 哈希计算的最大工作线程数
# 取CPU核心数和8的较小值，避免过多线程导致性能下降
HASH_WORKERS = min(8, os.cpu_count() or 4)


def calculate_md5(file_path, block_size=8192):
    """
    计算文件的MD5哈希值，用于检测普通文件的重复内容

    参数：
        file_path：文件路径
        block_size：每次读取的块大小，默认8192字节

    返回：
        文件的MD5哈希值（十六进制字符串），失败返回None
    """
    md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for block in iter(lambda: f.read(block_size), b""):
                md5.update(block)
        return md5.hexdigest()
    except Exception:
        return None


def calculate_phash(image):
    """
    使用OpenCV计算图像的感知哈希值（pHash）

    pHash算法原理：
        1. 将图像缩放到32x32像素
        2. 转换为灰度图
        3. 对灰度图进行DCT（离散余弦变换），提取低频分量
        4. 取DCT系数的8x8区域（排除直流分量）
        5. 计算系数平均值，将高于平均值的系数设为1，否则为0
        6. 生成64位哈希值

    参数：
        image：OpenCV格式的图像（BGR格式）

    返回：
        图像的感知哈希值（十六进制字符串）
    """
    resized = cv2.resize(image, (32, 32), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    dct = cv2.dct(np.float32(gray))
    dct_roi = dct[0:8, 0:8]
    mean_val = np.mean(dct_roi[1:])
    bits = (dct_roi[1:, :] > mean_val).astype(np.uint8).flatten()
    return bits.tobytes().hex()


def get_image_hash(file_path):
    """
    计算图像的感知哈希值，支持中文路径

    参数：
        file_path：图像文件路径（支持中文）

    返回：
        图像的感知哈希值，失败返回None
    """
    try:
        with Image.open(file_path) as img:
            if img.mode == "RGBA":
                img = img.convert("RGB")
            image = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
            if image is not None and image.size > 0:
                return calculate_phash(image)
    except Exception:
        pass
    return None


def get_image_exif(file_path):
    """
    获取图片的EXIF信息，特别是拍摄日期

    EXIF 36867标签表示拍摄日期时间（DateTimeOriginal）

    参数：
        file_path：图像文件路径

    返回：
        拍摄日期字符串（格式：YYYY-MM-DD），获取失败返回None
    """
    try:
        with Image.open(file_path) as img:
            exif_data = img._getexif()
            if exif_data and 36867 in exif_data:
                return exif_data[36867].split()[0].replace(":", "-")
    except Exception:
        pass
    return None


def process_single_file(entry):
    """
    处理单个文件，计算其哈希值并返回文件信息

    使用os.scandir获取的文件条目进行处理，避免重复调用stat()

    参数：
        entry：os.DirEntry对象

    返回：
        包含文件信息的字典，失败或空文件返回None
    """
    path = entry.path
    try:
        stat = entry.stat(follow_symlinks=False)
    except Exception:
        return None

    if stat.st_size == 0:
        return None

    name = entry.name
    if name.lower().endswith(IMAGE_EXTENSIONS):
        file_hash = get_image_hash(path)
        file_type = "image"
    else:
        file_hash = calculate_md5(path)
        file_type = "file"

    if not file_hash:
        return None

    return {
        "path": path,
        "size": stat.st_size,
        "created_time": stat.st_ctime,
        "modified_time": stat.st_mtime,
        "hash": file_hash,
        "type": file_type,
    }


def scan_directory_fast(directory):
    """
    快速扫描目录，使用多线程并行计算文件哈希值

    优化策略：
        1. 先遍历收集所有文件条目
        2. 使用线程池并行计算哈希值
        3. 自动区分图片和普通文件

    参数：
        directory：要扫描的目录路径

    返回：
        包含图片和普通文件哈希结果的字典
    """
    results = {"image": {}, "file": {}}
    entries_list = []

    for root, dirs, files in os.walk(directory):
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    entries_list.append(entry)
        except Exception:
            continue

    with ThreadPoolExecutor(max_workers=HASH_WORKERS) as executor:
        futures = {
            executor.submit(process_single_file, entry): entry for entry in entries_list
        }
        for future in as_completed(futures):
            result = future.result()
            if result:
                file_hash = result["hash"]
                if result["type"] == "image":
                    if file_hash not in results["image"]:
                        results["image"][file_hash] = []
                    results["image"][file_hash].append(result)
                else:
                    if file_hash not in results["file"]:
                        results["file"][file_hash] = []
                    results["file"][file_hash].append(result)

    return results


def find_duplicate_files_fast(directory):
    """
    快速查找目录中的重复文件

    参数：
        directory：要扫描的目录路径

    返回：
        元组 (图片重复字典, 普通文件重复字典)
        每个字典的键是哈希值，值是具有相同哈希的文件列表
    """
    results = scan_directory_fast(directory)
    return {k: v for k, v in results["image"].items() if len(v) > 1}, {
        k: v for k, v in results["file"].items() if len(v) > 1
    }


def manage_duplicate_files(duplicate_files, keep_strategy="oldest"):
    """
    管理重复文件，根据策略保留一个，将其余文件移动到专用文件夹

    处理流程：
        1. 创建重复文件存放目录
        2. 根据保留策略对文件进行排序
        3. 保留第一个文件，将其余文件移动到目标目录
        4. 处理文件名冲突，自动添加序号

    参数：
        duplicate_files：重复文件字典（哈希值 -> 文件列表）
        keep_strategy：保留策略，支持oldest/newest/largest/smallest/shortest_path/longest_path

    返回：
        元组 (移动文件数, 移动文件大小, 移动文件列表, 目标目录路径)
    """
    moved_count = 0
    moved_size = 0
    moved_files_list = []

    duplicates_dir = os.path.join(
        os.getcwd(), f"duplicates_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    os.makedirs(duplicates_dir, exist_ok=True)

    strategy_sort_keys = {
        "oldest": lambda x: x["created_time"],
        "newest": lambda x: x["created_time"],
        "shortest_path": lambda x: len(x["path"]),
        "longest_path": lambda x: len(x["path"]),
        "largest": lambda x: x["size"],
        "smallest": lambda x: x["size"],
    }

    reverse_keys = {"newest", "longest_path", "largest"}

    for file_hash, files in duplicate_files.items():
        key = strategy_sort_keys.get(keep_strategy, lambda x: x["created_time"])
        files.sort(key=key, reverse=(keep_strategy in reverse_keys))

        for file_info in files[1:]:
            try:
                file_name = os.path.basename(file_info["path"])
                target_path = os.path.join(duplicates_dir, file_name)

                # 如果目标路径已存在同名文件，则跳过移动，不改变文件名
                if not os.path.exists(target_path):
                    shutil.move(file_info["path"], target_path)
                    moved_count += 1
                    moved_size += file_info["size"]
                    moved_files_list.append(file_info)
            except Exception:
                pass

    return moved_count, moved_size, moved_files_list, duplicates_dir


def organize_images_fast(directory, organize_mode="date"):
    """
    根据拍摄日期快速分类图片到不同文件夹

    处理策略：
        1. 优先使用EXIF中的拍摄日期
        2. 如果没有EXIF信息，使用文件修改日期
        3. 按日期或季度创建文件夹
        4. 使用缓存减少重复的文件夹创建操作
        5. 处理文件名冲突，自动添加序号

    参数：
        directory：要分类的目录路径
        organize_mode：分类模式，"date"表示按日期分类，"quarter"表示按季度分类

    返回：
        包含统计信息的字典（total_images, organized_images, skipped_images）
    """
    total_images = 0
    organized_images = 0
    skipped_images = 0
    date_folders = {}

    def get_quarter_folder(date_str):
        """
        根据日期字符串获取季度文件夹名称

        参数：
            date_str：日期字符串，格式为YYYY-MM-DD

        返回：
            季度文件夹名称，格式为YYYY-QX（例如：2024-Q1）
        """
        try:
            year, month, _ = map(int, date_str.split("-"))
            quarter = (month - 1) // 3 + 1
            return f"{year}-Q{quarter}"
        except Exception:
            return None

    for root, dirs, files in os.walk(directory):
        for name in files:
            if not name.lower().endswith(IMAGE_EXTENSIONS):
                continue

            file_path = os.path.join(root, name)
            total_images += 1

            shooting_date = get_image_exif(file_path)
            target_folder = None

            if shooting_date:
                if organize_mode == "quarter":
                    folder_key = get_quarter_folder(shooting_date)
                    if folder_key:
                        target_folder = date_folders.get(folder_key)
                        if target_folder is None:
                            target_folder = os.path.join(directory, folder_key)
                            date_folders[folder_key] = target_folder
                            if not os.path.exists(target_folder):
                                os.makedirs(target_folder)
                else:
                    target_folder = date_folders.get(shooting_date)
                    if target_folder is None:
                        target_folder = os.path.join(directory, shooting_date)
                        date_folders[shooting_date] = target_folder
                        if not os.path.exists(target_folder):
                            os.makedirs(target_folder)
            else:
                try:
                    stat = os.stat(file_path)
                    modify_date = datetime.datetime.fromtimestamp(
                        stat.st_mtime
                    ).strftime("%Y-%m-%d")
                    if organize_mode == "quarter":
                        folder_key = get_quarter_folder(modify_date)
                        if folder_key:
                            target_folder = date_folders.get(folder_key)
                            if target_folder is None:
                                target_folder = os.path.join(directory, folder_key)
                                date_folders[folder_key] = target_folder
                                if not os.path.exists(target_folder):
                                    os.makedirs(target_folder)
                    else:
                        target_folder = date_folders.get(modify_date)
                        if target_folder is None:
                            target_folder = os.path.join(directory, modify_date)
                            date_folders[modify_date] = target_folder
                            if not os.path.exists(target_folder):
                                os.makedirs(target_folder)
                except Exception:
                    skipped_images += 1
                    continue

            if target_folder is None:
                skipped_images += 1
                continue

            target_file_path = os.path.join(target_folder, name)

            # 如果目标路径已存在同名文件，则跳过移动，不改变文件名
            if not os.path.exists(target_file_path):
                try:
                    shutil.move(file_path, target_file_path)
                    organized_images += 1
                except Exception:
                    skipped_images += 1
            else:
                # 如果文件已存在，跳过该文件
                skipped_images += 1

    return {
        "total_images": total_images,
        "organized_images": organized_images,
        "skipped_images": skipped_images,
    }


class FileProcessorGUI:
    """
    文件处理工具的图形用户界面类

    界面布局：
        - 目录选择区域：输入或浏览选择要处理的目录
        - 功能选择区域：选择去重、分类或两者都做
        - 保留策略区域：选择去重时保留哪个文件
        - 按钮区域：开始处理、停止处理、清空日志
        - 状态显示区域：显示当前状态和进度条
        - 日志显示区域：显示处理过程的日志信息
    """

    def __init__(self, root):
        """
        初始化GUI界面

        参数：
            root：tkinter根窗口对象
        """
        self.root = root
        self.root.title("文件处理工具")
        self.root.geometry("800x600")
        self.root.resizable(True, True)

        self.queue = queue.Queue()
        self.directory_var = tk.StringVar(value=os.getcwd())
        self.function_var = tk.StringVar(value="organize")
        self.keep_strategy_var = tk.StringVar(value="最早创建")
        self.organize_mode_var = tk.StringVar(value="按日期")
        self.is_processing = False
        self.cancel_requested = False

        self.main_frame = ttk.Frame(root, padding="20")
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        self.create_directory_section()
        self.create_function_section()
        self.create_strategy_section()
        self.create_organize_mode_section()
        self.create_button_section()
        self.create_status_section()
        self.create_log_section()

    def create_directory_section(self):
        """创建目录选择区域"""
        section = ttk.LabelFrame(self.main_frame, text="目录选择", padding="10")
        section.pack(fill=tk.X, pady=5)

        ttk.Label(section, text="处理目录:").grid(
            row=0, column=0, sticky=tk.W, padx=5, pady=5
        )
        ttk.Entry(section, textvariable=self.directory_var, width=50).grid(
            row=0, column=1, sticky=tk.EW, padx=5, pady=5
        )
        ttk.Button(section, text="浏览", command=self.browse_directory).grid(
            row=0, column=2, padx=5, pady=5
        )
        section.columnconfigure(1, weight=1)

    def create_function_section(self):
        """创建功能选择区域（单选按钮）"""
        section = ttk.LabelFrame(self.main_frame, text="功能选择", padding="10")
        section.pack(fill=tk.X, pady=5)

        for i, (text, value) in enumerate(
            [
                ("仅去重", "deduplicate"),
                ("仅分类", "organize"),
                ("先去重再分类", "both"),
            ]
        ):
            ttk.Radiobutton(
                section, text=text, variable=self.function_var, value=value
            ).grid(row=0, column=i, sticky=tk.W, padx=5, pady=5)

    def create_strategy_section(self):
        """创建保留策略选择区域（下拉框）"""
        section = ttk.LabelFrame(self.main_frame, text="保留策略", padding="10")
        section.pack(fill=tk.X, pady=5)

        ttk.Label(section, text="去重时保留:").grid(
            row=0, column=0, sticky=tk.W, padx=5, pady=5
        )

        self.strategy_map = {
            "最早创建": "oldest",
            "最新创建": "newest",
            "文件最大": "largest",
            "文件最小": "smallest",
            "路径最短": "shortest_path",
            "路径最长": "longest_path",
        }

        ttk.Combobox(
            section,
            textvariable=self.keep_strategy_var,
            values=list(self.strategy_map.keys()),
            state="readonly",
            width=20,
        ).grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)

    def create_organize_mode_section(self):
        """创建分类方式选择区域（下拉框）"""
        section = ttk.LabelFrame(self.main_frame, text="分类方式", padding="10")
        section.pack(fill=tk.X, pady=5)

        ttk.Label(section, text="分类时按:").grid(
            row=0, column=0, sticky=tk.W, padx=5, pady=5
        )

        self.organize_mode_map = {
            "按日期": "date",
            "按季度": "quarter",
        }

        ttk.Combobox(
            section,
            textvariable=self.organize_mode_var,
            values=list(self.organize_mode_map.keys()),
            state="readonly",
            width=20,
        ).grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)

    def create_button_section(self):
        """创建按钮区域"""
        section = ttk.Frame(self.main_frame)
        section.pack(fill=tk.X, pady=10)

        self.start_btn = ttk.Button(
            section, text="开始处理", command=self.start_processing
        )
        self.start_btn.pack(side=tk.LEFT, padx=5)

        self.stop_btn = ttk.Button(
            section, text="停止处理", command=self.stop_processing, state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        ttk.Button(section, text="清空日志", command=self.clear_log).pack(
            side=tk.RIGHT, padx=5
        )

    def create_status_section(self):
        """创建状态显示区域"""
        section = ttk.Frame(self.main_frame)
        section.pack(fill=tk.X, pady=5)

        self.status_var = tk.StringVar(value="就绪")
        self.progress_var = tk.DoubleVar(value=0.0)

        ttk.Label(section, text="状态:").grid(row=0, column=0, sticky=tk.W, padx=5)
        ttk.Label(section, textvariable=self.status_var).grid(
            row=0, column=1, sticky=tk.W, padx=5
        )
        ttk.Progressbar(
            section, variable=self.progress_var, mode="determinate", length=200
        ).grid(row=0, column=2, sticky=tk.E, padx=5)
        section.columnconfigure(1, weight=1)

    def create_log_section(self):
        """创建日志显示区域（带滚动条的文本框）"""
        section = ttk.LabelFrame(self.main_frame, text="处理日志", padding="10")
        section.pack(fill=tk.BOTH, expand=True, pady=5)

        self.log_text = tk.Text(
            section, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 10)
        )
        scrollbar = ttk.Scrollbar(
            section, orient=tk.VERTICAL, command=self.log_text.yview
        )
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def browse_directory(self):
        """打开目录选择对话框"""
        directory = filedialog.askdirectory(
            initialdir=self.directory_var.get(), title="选择处理目录"
        )
        if directory:
            self.directory_var.set(directory)

    def start_processing(self):
        """
        开始处理文件

        处理流程：
            1. 检查目录是否有效
            2. 更新UI状态（禁用开始按钮，启用停止按钮）
            3. 在新线程中执行文件处理任务
            4. 启动队列处理以更新日志
        """
        if self.is_processing:
            return

        directory = self.directory_var.get()
        if not directory or not os.path.exists(directory):
            messagebox.showerror("错误", "请选择有效的目录")
            return

        self.is_processing = True
        self.cancel_requested = False
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.status_var.set("处理中...")
        self.progress_var.set(0.0)
        self.clear_log()

        threading.Thread(target=self.process_files, daemon=True).start()
        self.root.after(100, self.process_queue)

    def stop_processing(self):
        """请求停止处理"""
        self.cancel_requested = True
        self.status_var.set("正在停止...")

    def clear_log(self):
        """清空日志显示区域"""
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

    def add_log(self, message):
        """向日志队列添加消息"""
        self.queue.put(f"{message}\n")

    def process_queue(self):
        """
        处理日志队列，将消息显示到日志区域

        定时检查队列中的消息并更新UI
        """
        try:
            while True:
                message = self.queue.get_nowait()
                self.log_text.config(state=tk.NORMAL)
                self.log_text.insert(tk.END, message)
                self.log_text.see(tk.END)
                self.log_text.config(state=tk.DISABLED)
        except queue.Empty:
            pass

        if self.is_processing:
            self.root.after(100, self.process_queue)

    def process_files(self):
        """
        在后台线程中执行文件处理任务

        处理流程：
            1. 根据选择的功能执行去重和/或分类
            2. 实时记录处理日志
            3. 处理完成后显示总结信息
            4. 异常处理和UI状态恢复
        """
        directory = self.directory_var.get()
        function = self.function_var.get()
        keep_strategy = self.strategy_map.get(self.keep_strategy_var.get(), "oldest")
        organize_mode = self.organize_mode_map.get(self.organize_mode_var.get(), "date")

        deduplicate_stats = {
            "total_duplicate_files": 0,
            "unique_duplicate_files": 0,
            "moved_count": 0,
            "moved_size": 0,
        }
        organize_stats = None

        try:
            if function in ("deduplicate", "both"):
                if self.cancel_requested:
                    raise Exception("用户取消处理")

                self.add_log("开始查找重复文件...")
                image_dups, file_dups = find_duplicate_files_fast(directory)

                if self.cancel_requested:
                    raise Exception("用户取消处理")

                total_dups = sum(len(f) for f in image_dups.values()) + sum(
                    len(f) for f in file_dups.values()
                )
                unique_dups = len(image_dups) + len(file_dups)

                if total_dups > 0:
                    self.add_log(
                        f"发现重复文件: {total_dups} 个，涉及 {unique_dups} 组"
                    )
                    self.add_log("开始处理重复文件...")

                    all_dups = {**image_dups, **file_dups}
                    moved_count, moved_size, moved_files_list, duplicates_dir = (
                        manage_duplicate_files(all_dups, keep_strategy)
                    )

                    deduplicate_stats = {
                        "total_duplicate_files": total_dups,
                        "unique_duplicate_files": unique_dups,
                        "moved_count": moved_count,
                        "moved_size": moved_size,
                        "duplicates_dir": duplicates_dir,
                    }

                    self.add_log(
                        f"处理完成，共移动 {moved_count} 个文件到目录: {duplicates_dir}"
                    )
                    self.add_log(f"移动文件总大小: {moved_size / (1024 * 1024):.2f} MB")
                else:
                    self.add_log("未发现重复文件")

            if function in ("organize", "both"):
                if self.cancel_requested:
                    raise Exception("用户取消处理")

                self.add_log("开始分类图片...")
                organize_stats = organize_images_fast(directory, organize_mode)
                self.add_log(
                    f"分类完成，共扫描 {organize_stats['total_images']} 张图片，"
                    f"成功分类 {organize_stats['organized_images']} 张，跳过 {organize_stats['skipped_images']} 张"
                )

            self.add_log("\n" + "=" * 50)
            self.add_log("处理完成！综合总结如下：")
            self.add_log("=" * 50)

            if function in ("deduplicate", "both"):
                self.add_log(f"\n去重情况：")
                self.add_log(f"- 扫描目录: {directory}")
                self.add_log(
                    f"- 发现重复文件: {deduplicate_stats['total_duplicate_files']} 个"
                )
                self.add_log(
                    f"- 涉及重复组: {deduplicate_stats['unique_duplicate_files']} 组"
                )
                self.add_log(f"- 总共移动: {deduplicate_stats['moved_count']} 个文件")
                self.add_log(
                    f"- 移动文件大小: {deduplicate_stats['moved_size'] / (1024 * 1024):.2f} MB"
                )
                if deduplicate_stats.get("duplicates_dir"):
                    self.add_log(
                        f"- 重复文件保存目录: {deduplicate_stats['duplicates_dir']}"
                    )

            if function in ("organize", "both"):
                self.add_log(f"\n分类情况：")
                self.add_log(f"- 扫描目录: {directory}")
                self.add_log(f"- 总共扫描图片: {organize_stats['total_images']} 个")
                self.add_log(f"- 成功分类图片: {organize_stats['organized_images']} 个")
                self.add_log(f"- 跳过图片: {organize_stats['skipped_images']} 个")

            self.add_log("=" * 50)
            self.status_var.set("处理完成")
            messagebox.showinfo("成功", "文件处理完成！")
        except Exception as e:
            if "用户取消" not in str(e):
                self.add_log(f"处理过程中发生错误: {e}")
                self.status_var.set("处理失败")
                messagebox.showerror("错误", f"处理过程中发生错误: {e}")
            else:
                self.add_log("处理已取消")
                self.status_var.set("已取消")
        finally:
            self.is_processing = False
            self.start_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)
            self.progress_var.set(100.0)


def run_command_line():
    """
    命令行模式执行文件处理任务

    支持通过命令行参数指定：
        - -d/--directory：指定处理目录
        - -f/--function：指定执行功能（deduplicate/organize/both）

    输出：
        处理结果和统计信息到控制台
    """
    parser = argparse.ArgumentParser(description="文件处理工具")
    parser.add_argument(
        "-d", "--directory", type=str, default=os.getcwd(), help="要处理的目录路径"
    )
    parser.add_argument(
        "-f",
        "--function",
        type=str,
        choices=["deduplicate", "organize", "both"],
        default="organize",
        help="要执行的功能: deduplicate(去重), organize(分类), both(先去重再分类)",
    )

    args = parser.parse_args()
    current_dir = args.directory
    deduplicate_stats = {
        "total_duplicate_files": 0,
        "unique_duplicate_files": 0,
        "moved_count": 0,
        "moved_size": 0,
    }
    organize_stats = None

    if args.function in ("deduplicate", "both"):
        print("正在扫描目录...")
        image_dups, file_dups = find_duplicate_files_fast(current_dir)

        total_dups = sum(len(f) for f in image_dups.values()) + sum(
            len(f) for f in file_dups.values()
        )
        unique_dups = len(image_dups) + len(file_dups)

        if total_dups > 0:
            print(f"发现重复文件: {total_dups} 个，涉及 {unique_dups} 组")

            all_dups = {**image_dups, **file_dups}
            moved_count, moved_size, moved_files_list, duplicates_dir = (
                manage_duplicate_files(all_dups, keep_strategy="oldest")
            )

            deduplicate_stats = {
                "total_duplicate_files": total_dups,
                "unique_duplicate_files": unique_dups,
                "moved_count": moved_count,
                "moved_size": moved_size,
                "duplicates_dir": duplicates_dir,
            }

            if moved_files_list:
                log_file = f"duplicate_files_log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                with open(log_file, "w", encoding="utf-8") as f:
                    f.write(
                        f"文件去重日志 - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    )
                    f.write(f"扫描目录: {current_dir}\n")
                    f.write(f"移动文件数量: {moved_count}\n")
                    f.write(f"移动文件大小: {moved_size / (1024 * 1024):.2f} MB\n")
                    f.write(f"重复文件保存目录: {duplicates_dir}\n")
                    f.write("\n移动的文件列表:\n")
                    for file_info in moved_files_list:
                        f.write(f"{file_info['path']}\n")
        else:
            print("未发现重复文件")

    if args.function in ("organize", "both"):
        print("正在分类图片...")
        organize_stats = organize_images_fast(current_dir)
        print(
            f"分类完成，共扫描 {organize_stats['total_images']} 张图片，"
            f"成功分类 {organize_stats['organized_images']} 张，跳过 {organize_stats['skipped_images']} 张"
        )

    print("\n" + "=" * 50)
    print("处理完成！综合总结如下：")
    print("=" * 50)

    if args.function in ("deduplicate", "both"):
        print(f"\n去重情况：")
        print(f"- 扫描目录: {current_dir}")
        print(f"- 发现重复文件: {deduplicate_stats['total_duplicate_files']} 个")
        print(f"- 涉及重复组: {deduplicate_stats['unique_duplicate_files']} 组")
        print(f"- 总共移动: {deduplicate_stats['moved_count']} 个文件")
        print(
            f"- 移动文件大小: {deduplicate_stats['moved_size'] / (1024 * 1024):.2f} MB"
        )

    if args.function in ("organize", "both"):
        print(f"\n分类情况：")
        print(f"- 扫描目录: {current_dir}")
        print(f"- 总共扫描图片: {organize_stats['total_images']} 个")
        print(f"- 成功分类图片: {organize_stats['organized_images']} 个")
        print(f"- 跳过图片: {organize_stats['skipped_images']} 个")

    print("=" * 50)


def main():
    """
    程序入口函数

    根据命令行参数决定启动GUI模式还是命令行模式：
        - 有命令行参数：启动命令行模式
        - 无命令行参数：启动GUI模式
    """
    if len(sys.argv) > 1:
        run_command_line()
    else:
        root = tk.Tk()
        app = FileProcessorGUI(root)
        root.mainloop()


if __name__ == "__main__":
    main()
