# Android Event Collector

通过 ADB 连接 Android 设备，实时收集、过滤、导出 Android 事件日志的桌面工具。

## 功能

- **双采集模式**: Logcat（应用日志）+ getevent（底层输入事件）
- **实时过滤**: 关键词/正则、Tag、日志级别多维过滤
- **彩色渲染**: 按日志级别和 Tag 高亮显示
- **设备管理**: 自动检测已连接设备，显示型号/系统信息
- **导出支持**: TXT / JSON / CSV 三种格式
- **暂停/继续**: 不中断采集，仅暂停界面更新

## 安装

```bash
pip install customtkinter
```

并确保 `adb` 已安装并在 PATH 中：
- macOS: `brew install android-platform-tools`
- Windows: 下载 [Android SDK Platform-Tools](https://developer.android.com/studio/releases/platform-tools)
- Linux: `sudo apt install adb`

## 运行

```bash
python android_event_collector.py
```

## 使用步骤

1. 手机开启**开发者模式** → **USB 调试**
2. 用 USB 连接手机，点击 ↻ 刷新设备
3. 选择设备 → 选择采集模式（Logcat / getevent）
4. 点击 **▶ 开始采集**
5. 使用顶部过滤栏筛选关键事件
6. 点击导出保存结果

## 架构说明

| 模块 | 说明 |
|------|------|
| `ADBManager` | ADB 进程管理，设备检测，logcat/getevent 启停 |
| `LogParser` | 正则解析 logcat threadtime 格式 和 getevent 输出 |
| `AndroidEventCollector` | CustomTkinter 主界面，队列驱动的异步渲染 |
