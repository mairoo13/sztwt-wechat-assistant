# 深圳天文台预约助手 · 电脑微信窗口模式

Windows 本地工具。使用者在电脑微信的官方预约网页提前填写人员资料，助手在指定时间刷新名额，选择目标日期与时段，并点击一次提交。无需导出微信会话。

**当前为预览版。** 已在电脑微信 4.1.15.13 实测页面控件识别和一次名额刷新；完整的日期选择、有名额时提交和官方成功结果尚未实测。点击提交不等于预约成功。详见 [验证记录](docs/verification.md)。

## 下载与使用

在 [Releases](https://github.com/mairoo13/sztwt-wechat-assistant/releases) 下载 Windows 压缩包，解压后打开 `WeChatWindowAssistant.exe`，无需安装 Python。

1. 临近放号，在电脑微信打开深圳天文台官方“个人预约”页，填写人员资料，阅读并勾选网页底部须知。
2. 打开助手，设置北京时间的放号时间、实际参观日期、上午／下午／任一时段。参观日期须属于所设放号时间的同一周；请核对默认值。
3. 点击“检测页面”，按提示补齐资料。“试刷一次”只刷新名额；“试选日期”只选择并核对目标日期，两者均不提交。
4. 勾选助手中的自动提交选项，点击“开始”。到点后助手把微信置于前台，按设定间隔刷新，满足日期、时段条件后点击一次提交。
5. 保持电脑唤醒和微信预约窗口在前台。按 **Esc** 停止。验证码与后续确认在微信中手动处理，最终到微信“预约信息”核对结果。

页面公布的放号安排是每周一上午 10:00，具体以官方当日页面为准。“上限 150 人”是场次容量，不能视为剩余名额。窗口模式根据时段控件是否可选判断，实际同行人数是否满足由官方提交校验。

## 运行行为

- 刷新间隔至少 5 秒，单次持续 1–60 分钟。
- 使用 Windows UI Automation 读取日期、时段、按钮和必填字段；日期选中状态另用官方页面蓝色背景核对。
- 资料仅用于内存中的完整性检查和变化检测，不填写或保存姓名、证件、手机号，也不读取登录令牌。
- 控件不匹配、窗口离开前台、资料变化、弹窗或验证码出现时停止，交给使用者处理。
- 每次任务只点击一次提交。点击前将尝试时间、目标日期和时段写入本机提交锁；结果不明时不重试。核对官方结果后，才能手动解除锁。

提交锁位于 `%LOCALAPPDATA%\SztwtReservationAssistant\window-submit.json`，不含人员资料或登录凭据。关闭助手不会取消已经提交的预约。

## 从源码运行

需要 Windows、Python 3.12（含 Tk）以及已经登录的电脑微信。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe wechat_mode.py
```

运行离线测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试使用模拟页面，不会访问预约网站。

## 构建 Windows 程序

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\build.ps1 -Python .\.venv\Scripts\python.exe
```

输出位于 `dist/WeChatWindowAssistant.exe`。`build.ps1` 会先运行离线测试，再调用 PyInstaller 打包。构建环境与历史实测范围记录在 [验证记录](docs/verification.md)。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `wechat_mode.py` | 窗口、设置、任务启动与停止 |
| `wechat_engine.py` | 微信控件识别、日期时段选择、定时流程和提交锁 |
| `tests/test_wechat_engine.py` | 离线行为测试 |
| `build.ps1` | Windows 测试与构建入口 |
| `docs/verification.md` | 已验证与未验证范围 |
| `third-party-licenses/` | 第三方组件许可证 |

本仓库只保留窗口模式。旧版接口程序、会话导出器、浏览器记录、调试抓取、个人资料和本机构建缓存均不纳入项目。项目为个人工具，与微信、深圳市天文台无隶属关系。
