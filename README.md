# 文件处理工具

一个功能强大的文件去重和图片分类工具，支持多线程并行处理，提供图形界面和命令行两种使用方式。

## 功能特性

- **智能文件去重**
  - 使用感知哈希算法检测视觉相似的图片
  - 使用MD5哈希检测普通文件的重复内容
  - 支持多种保留策略（最早创建、最新创建、文件最大、文件最小、路径最短、路径最长）
  - 重复文件不会被删除，而是移动到专用目录

- **图片自动分类**
  - 根据拍摄日期（EXIF信息）自动分类图片
  - 如果没有EXIF信息，则使用文件修改日期
  - 支持按日期或按季度分类
  - 支持多种图片格式：.jpg, .jpeg, .png, .gif, .bmp, .tiff

- **高效处理**
  - 多线程并行处理，提高大目录扫描效率
  - 支持中文路径
  - 处理大文件夹时性能优化

- **双模式支持**
  - 图形用户界面（GUI）：直观易用
  - 命令行模式：适合批处理和自动化脚本

## 安装

### 依赖项

- Python 3.7+
- Pillow >= 10.0.0
- opencv-python >= 4.8.0
- numpy >= 1.24.0

### 安装步骤

1. 克隆或下载本项目

2. 安装依赖项：
```bash
pip install -r requirements.txt
```

3. （可选）编译为可执行文件：
```bash
python -m PyInstaller deduplicate_files.py --onefile --name deduplicate_files
```

## 使用方法

### GUI 模式

直接运行脚本或编译后的可执行文件：

```bash
python deduplicate_files.py
```

或双击 `deduplicate_files.exe`

操作步骤：
1. 选择要处理的目录
2. 选择功能（仅去重、仅分类、先去重再分类）
3. 选择保留策略（去重时）
4. 选择分类方式（分类时）
5. 点击"开始处理"按钮

### 命令行模式

#### 仅去重
```bash
python deduplicate_files.py -f deduplicate -d "目录路径"
```

#### 仅分类
```bash
python deduplicate_files.py -f organize -d "目录路径"
```

#### 先去重再分类
```bash
python deduplicate_files.py -f both -d "目录路径"
```

#### 命令行参数说明

- `-f, --function`: 功能选择，可选值：deduplicate（去重）、organize（分类）、both（两者都做）
- `-d, --directory`: 要处理的目录路径

## 保留策略说明

- **最早创建**：保留创建时间最早的文件
- **最新创建**：保留创建时间最新的文件
- **文件最大**：保留文件大小最大的文件
- **文件最小**：保留文件大小最小的文件
- **路径最短**：保留路径最短的文件
- **路径最长**：保留路径最长的文件

## 分类方式说明

- **按日期**：将图片按拍摄日期或修改日期分类到 YYYY-MM-DD 格式的文件夹
- **按季度**：将图片按季度分类到 YYYY-QX 格式的文件夹（例如：2024-Q1）

## 注意事项

1. 处理过程中请勿关闭应用程序
2. 重复文件将被移动到当前目录下的 `duplicates_YYYYMMDD_HHMMSS` 文件夹
3. 如果目标位置已存在同名文件，则跳过移动，不改变文件名
4. 处理大文件夹时可能需要较长时间，建议先在测试目录上运行
5. 建议在处理重要文件前先备份

## 技术实现

- **图片感知哈希**：使用 OpenCV 的 DCT（离散余弦变换）算法计算图像的 pHash
- **文件哈希**：使用 MD5 算法计算普通文件的哈希值
- **多线程处理**：使用 `concurrent.futures.ThreadPoolExecutor` 实现并行处理
- **EXIF 读取**：使用 Pillow 读取图片的拍摄日期信息

## 许可证

MIT License - 详见 [LICENSE](LICENSE) 文件

## 贡献

欢迎提交 Issue 和 Pull Request！
