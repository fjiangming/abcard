# AGBC EXE 打包构建指南

## 文件说明

| 文件 | 用途 | 分发给买家？ |
|------|------|:----:|
| `license_manager.py` | License 验证模块 | ✅ (编译为 .pyd) |
| `license_gen.py` | 生成 License Key | ❌ 管理员专用 |
| `ui_entry.py` | Streamlit 入口 (License 门禁) | ✅ (保持 .py) |
| `launcher.py` | Windows 启动器 | ✅ (编译为 .exe) |
| `build.py` | 自动化构建脚本 | ❌ 管理员专用 |
| `installer.iss` | Inno Setup 安装包 | ❌ 管理员专用 |

## 前置要求

```powershell
# 1. 安装 Nuitka (编译 .py → .pyd)
pip install nuitka

# 2. 安装 C 编译器 (Nuitka 依赖)
#    推荐: Visual Studio Build Tools (包含 MSVC)
#    或者 Nuitka 会自动下载 MinGW

# 3. (可选) 安装 Inno Setup 6
#    下载: https://jrsoftware.org/isinfo.php
```

## 构建步骤

### 一键构建

```powershell
cd exe
python build.py
```

### 分步构建

```powershell
# 仅下载 Python + 安装依赖 (不编译)
python build.py --skip-compile

# 仅编译 (Python 已准备)
python build.py --skip-download

# 清理构建目录
python build.py --clean
```

### 生成安装包 (可选)

1. 完成上述构建
2. 用 Inno Setup 打开 `installer.iss`
3. 点击 "Compile" 生成 `dist/AGBC_Setup_1.0.0.exe`

### 修改代码后重新构建

修改了项目源码后需要重新打包，跳过已下载的 Python 和依赖：

```powershell
cd exe
python build.py --skip-download
```

如果只修改了**单个文件**（如 `ui.py`），可以只编译该文件（更快）：

```powershell
# 复制修改后的文件到 build 目录
copy ui.py exe\build\app\_original_ui.py

# 只编译这一个
python -m nuitka --module --remove-output --output-dir=exe\build\app exe\build\app\_original_ui.py

# 删掉源码
del exe\build\app\_original_ui.py
```

## 交付方式

| 方式 | 做法 | 买家体验 |
|------|------|---------|
| **压缩包（推荐）** | 将 `exe/build/` 目录压缩为 zip | 解压 → 双击 `AGBC.bat` → 直接用 |
| **安装包（可选）** | 用 Inno Setup 编译 `installer.iss` | 双击安装 → 桌面快捷方式 |

> 程序免安装，买家解压后双击 `AGBC.bat` 即可运行，无需安装任何依赖。

## License 管理

### 生成 License

```powershell
# 查看买家的机器码 (买家运行程序后在激活界面可看到)
# 或买家手动运行:
python license_gen.py --show-machine-id

# 为买家生成 License
python license_gen.py --tier pro --machine-id <买家机器码>
python license_gen.py --tier lite --machine-id <买家机器码>

# 验证 License
python license_gen.py --verify <License Key>
```

### 销售流程

1. 买家安装程序 → 首次启动显示激活界面
2. 买家复制 **机器码** 发给你
3. 你运行 `license_gen.py` 生成对应等级的 License Key
4. 买家粘贴 License Key → 激活成功

### 功能分层

| 功能 | Lite | Pro |
|------|:----:|:---:|
| 自动注册 | ✅ | ✅ |
| 批量注册 | ✅ | ✅ |
| 账号管理 | ✅ | ✅ |
| NewAPI 同步 | ✅ | ✅ |
| 自动支付开通 | ❌ | ✅ |
| 计划选择 | ❌ | ✅ |

## 产出目录结构

```
exe/build/
├── python/              # 嵌入式 Python 3.12 + 依赖
│   ├── python.exe
│   └── Lib/site-packages/
├── app/                 # 编译后的应用 (全部 .pyd)
│   ├── ui_entry.py      # 唯一可见的 .py (5行入口)
│   ├── _original_ui.pyd # UI 逻辑 (编译后)
│   ├── auth_flow.pyd    # 注册流程 (编译后)
│   ├── license_manager.pyd
│   └── ...
├── data/                # 运行时数据
│   ├── config.json
│   ├── data.db
│   └── license.key
└── AGBC.bat / AGBC.exe  # 启动器
```

## 重要提示

> ⚠️ `license_manager.py` 中的 `_HMAC_SECRET` 必须在正式发布前修改!
> 否则任何人都可以用默认密钥生成 License。

修改位置: `exe/license_manager.py` 第 20 行
```python
_HMAC_SECRET = b"你的自定义密钥"
```
