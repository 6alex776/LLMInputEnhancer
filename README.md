# LLMInputEnhancer

当前版本：`v0.2.1-beta`

一个面向 Windows 桌面输入场景的本地大模型输入增强工具。

它的目标不是替代聊天界面，而是把本地 LLM 直接接入你正在使用的输入框：先选中文本，再通过全局热键完成润色、翻译、扩写、缩写或自定义改写，并把结果回灌到原输入位置。

## 项目特点

- 面向桌面输入场景，而不是单独的聊天窗口
- 支持全局热键，跨应用触发
- 优先读取当前选中文本，失败时回退到剪贴板方案
- 支持本地 OpenAI 兼容接口，默认适配 `llama-server`
- 支持流式生成并持续写回目标输入框
- 内置一个基于 PyTorch 的轻量文本任务分类器，可通过 `Alt+A` 自动判断任务类型
- 不修改系统输入法，不注入驱动，保持项目轻量

## 当前支持的任务

- 文本润色
- 中英互译
- 文本扩写
- 文本缩写
- 自定义指令处理

## 运行效果

典型使用流程如下：

1. 在任意应用中选中文本
2. 按下全局快捷键
3. 程序读取选区内容
4. 调用本地模型生成结果
5. 将结果尽量写回原输入框

适用场景包括但不限于：

- 微信、QQ、飞书等聊天输入框
- 浏览器网页输入框
- Office 文档编辑
- IDE、记事本等文本编辑器

不同应用对自动化输入的兼容程度不同，因此“尽量兼容”比“绝对兼容”更符合这个项目的定位。

## 技术栈

- Python
- PySide6
- httpx
- pywin32
- pyperclip
- UIAutomation for Windows（可选）
- PyTorch（分类器功能可选）

## 项目结构

```text
LLMInputEnhancer/
├─ config.json              # 本地配置文件
├─ src/
│  ├─ main.py               # 程序入口与整体控制器
│  ├─ ui_components.py      # 托盘、指令面板、设置窗口
│  ├─ hotkey_listener.py    # Win32 全局热键监听
│  ├─ clipboard_manager.py  # 选中文本获取、剪贴板处理、流式写回
│  ├─ llm_client.py         # 本地模型调用封装
│  ├─ config.py             # 配置读取、保存与校验
│  ├─ app_logger.py         # 日志初始化
│  ├─ app_info.py           # 应用名称与版本信息
│  └─ classifier/           # 文本任务分类器
│     ├─ train.py           # 训练脚本
│     ├─ infer.py           # 推理运行时
│     ├─ model.py           # Hybrid TextCNN 模型
│     ├─ dataset.py         # 数据读取与特征提取
│     ├─ data/              # 训练/验证样本
│     └─ artifacts/         # 导出的模型与词表
└─ logs/                    # 运行日志
```

## 环境要求

- Windows
- Python 3.11+ 或与你当前依赖兼容的 Python 环境
- 已启动的本地大模型服务

推荐你优先使用独立虚拟环境或 Conda 环境运行本项目。

## 使用前准备

发布包本身只包含客户端，不包含大模型本体，也不包含本地推理服务。

在使用本项目之前，请先完成下面这些准备工作：

1. 下载本项目对应版本的可执行发布包
2. 额外下载并安装 `llama.cpp`
3. 准备并部署你自己的本地大模型文件
4. 启动本地模型服务后，再运行本项目

推荐下载的 `llama.cpp` 版本：

- [llama.cpp b8591](https://github.com/ggml-org/llama.cpp/releases/tag/b8591)

建议优先下载其中带 `llama-server` 的 Windows 预编译包。

你至少需要准备以下内容：

- `llama-server.exe`
- 你自己的 GGUF 模型文件
- 可用的本地模型服务地址，例如 `http://127.0.0.1:8080/`

如果没有先部署并启动本地模型服务，本项目即使可以打开，也无法实际完成文本处理。

## 安装依赖

基础运行依赖：

```powershell
pip install PySide6 httpx pyperclip pywin32 uiautomation
```

如果你需要使用分类器训练或推理功能，再额外安装：

```powershell
pip install torch
```

## 启动本地模型服务

本项目默认对接 OpenAI 兼容接口，最常见的用法是连接 `llama-server`。

示例：

```powershell
.\llama-server.exe -m E:\llama\model\Qwen3.5-0.8B-IQ4_NL.gguf -ngl 80 -c 4096 -np 1 --chat-template-kwargs '{\"enable_thinking\": false}'
```

默认情况下，本项目会请求：

- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`

只要你的本地服务地址可访问，并且兼容上述接口协议，就可以接入。

### 首次使用建议

首次使用时，建议按这个顺序完成：

1. 下载本项目的 exe 发布包
2. 从 [llama.cpp b8591](https://github.com/ggml-org/llama.cpp/releases/tag/b8591) 下载 Windows 版本
3. 准备你自己的 GGUF 模型
4. 用 `llama-server` 启动模型服务
5. 启动本项目
6. 在设置中确认 `local_url` 和 `local_model`
7. 通过托盘菜单手动执行一次“检查本地模型服务”

## 启动项目

```powershell
python src/main.py
```

程序启动后会常驻系统托盘。

## 默认快捷键

- `Alt+\``：打开指令面板
- `Alt+1`：文本润色
- `Alt+2`：中英互译
- `Alt+3`：文本扩写
- `Alt+4`：文本缩写
- `Alt+A`：自动分类并执行高置信度任务

说明：

- `Alt+A` 默认不会弹出面板
- 如果分类器不可用或判断不确定，程序会只给出提示，不强制执行

## 配置说明

配置文件位于：

- `config.json`

当前主要配置项如下：

| 配置项 | 说明 |
| --- | --- |
| `local_url` | 本地模型服务地址，真正决定能否连接 |
| `local_model` | 请求体中的模型名；部分本地服务会忽略它，但设置页当前要求非空 |
| `temperature` | 生成温度 |
| `max_tokens` | 最大输出长度 |
| `enable_classifier_recommendation` | 是否启用 `Alt+A` 智能分类 |
| `auto_classify_execute_threshold` | 高置信度自动执行阈值 |
| `auto_classify_recommend_threshold` | 中等置信度提示阈值 |

## 分类器说明

项目内置了一个轻量级文本任务分类器，用于判断当前选中文本更适合走：

- `polish`
- `translate`
- `expand`
- `summarize`

当前实现不是大模型分类，而是项目内置的 `Hybrid TextCNN` 小模型，适合本地低延迟推理。

### 训练数据

默认数据位置：

- `src/classifier/data/train.jsonl`
- `src/classifier/data/val.jsonl`

每行一个 JSON 对象，例如：

```json
{"text":"请把这段话改得更正式一些", "label":"polish"}
```

### 训练命令

```powershell
python src/classifier/train.py
```

常用参数示例：

```powershell
python src/classifier/train.py --epochs 20 --batch-size 32 --device cuda
```

训练完成后会在以下目录生成产物：

- `src/classifier/artifacts/model.pt`
- `src/classifier/artifacts/vocab.json`
- `src/classifier/artifacts/meta.json`

## 设计说明

### 1. 文本获取

优先尝试通过 UIAutomation 获取当前控件中的选中文本；如果失败，则回退到剪贴板复制方案。

### 2. 模型调用

通过 `llm_client.py` 统一封装对本地模型服务的同步、异步和流式调用。

### 3. 输出回灌

对于标准编辑控件，优先使用更直接的消息写回；对于普通输入场景，则回退到模拟输入方案，以兼顾通用性。

### 4. 智能分类

`Alt+A` 不直接依赖大模型，而是先用本地小分类器快速判断任务类型，再决定是否自动执行。

## 日志

运行后日志自动创建文件夹，并默认写入：

- `logs/app.log`

如果你遇到以下问题，建议优先看日志：

- 本地模型连接失败
- 热键没有生效
- 未能读取选中文本
- 流式写回失败
- 分类器没有加载成功

## 常见问题

### 1. 为什么模型地址对了，但模型名写错仍然能调用成功？

因为当前很多本地 OpenAI 兼容服务只要求地址可达，并不一定严格校验请求体中的 `model` 字段。

在本项目中：

- `local_url` 是真正决定能否连接的关键项
- `local_model` 目前仍会写进请求体
- 手动“检查本地模型服务”时会尝试校验模型名是否存在

### 2. 为什么有些软件里写回效果不稳定？

不同应用的输入框实现方式差异很大。标准编辑框通常兼容更好，浏览器、自绘控件、特殊富文本编辑器则更容易出现行为差异。

### 3. 为什么 `Alt+A` 没有反应？

可能原因包括：

- 分类器功能已在设置中关闭
- 当前没有选中文本
- 分类器模型文件尚未训练或未正确生成
- `torch` 没有安装在当前运行解释器中

## 后续可扩展方向

- 支持更多任务类别
- 将分类器升级为轻量 Transformer
- 增加更细粒度的输入控件兼容策略
- 支持更多本地模型服务后端
- 增加打包与发布流程

## License

详见：

- `LICENSE.md`
