# WeRead Desk — 微信读书桌面版

将微信读书网页版（weread.qq.com）包装为独立的 Windows 桌面程序：独立窗口与任务栏图标、登录态持久化、窗口位置记忆、单实例、系统托盘常驻、全局热键唤起。

## 运行（开发）

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python src\app.py
```

## 打包

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
# 产物：项目根目录 WeReadDesk.exe（单文件，约 18 MB，需目标机器已装 WebView2）
```

## 使用

- 启动后窗口加载微信读书网页版，扫码登录，登录态长期有效
- 最小化 → 收缩到系统托盘；点击托盘图标或按 `Ctrl+Alt+W`（可改）唤起/隐藏
- 托盘菜单：显示/隐藏窗口、设置…、退出
- **设置面板**（托盘菜单「设置…」）：自定义全局快捷键（点击输入框按新组合）、最小化到托盘、关闭到托盘、启动时最小化、开机自启
- 重复启动会自动唤起已有窗口（单实例）

## 结构

```
├── src/app.py        # 壳程序：窗口、单实例、位置记忆、图标、托盘、热键、设置
├── src/settings.html # 设置面板页面（本地文件 + js_api，打包时 --add-data 内置）
├── scripts/          # make_icon.py（生成图标）、build_exe.ps1（打包）
├── assets/           # icon_src.png（官方源图）、icon.png/icon.ico（圆角生成产物）
└── requirements.txt
```

## 数据位置

- 配置：`%APPDATA%\WeReadDesk\config.json`
- 登录态（WebView2 用户数据）：`%APPDATA%\WeReadDesk\webview-data\`

## 说明

个人学习用途的桌面壳，不修改、不抓取微信读书任何数据，全部内容来自 weread.qq.com 官方网页版；图标为官方站点 favicon 素材，仅供个人学习使用。
