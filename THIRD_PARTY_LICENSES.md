# 第三方依赖与许可证

WizFlow 以 **GPL-2.0** 发布。以下是运行时/构建依赖的许可证信息，实际以各依赖包内 `LICENSE` 文件为准。

| 依赖 | 用途 | 许可证 |
|---|---|---|
| CustomTkinter | 桌面 UI | MIT |
| OpenCV (opencv-python) | 模板匹配、图像处理 | Apache-2.0 |
| NumPy | 图像数组运算 | BSD-3-Clause |
| mss | 屏幕抓取 | MIT |
| Pillow | 图像读写、预览 | MIT-CMU / HPND |
| pynput | 全局键鼠录制 | LGPL-3.0 |
| pywin32 | Windows API、键鼠注入 | PSF |
| json5 | 兼容 KeymouseGo 脚本格式 | MIT |
| darkdetect | 主题检测（CustomTkinter 依赖） | BSD-3-Clause |
| Python | 运行时 | PSF |
| Parsec VDD (parsec-vdd) | 虚拟显示器驱动（`assets/vdd/driver`） | MIT |
| nefcon (nefconw.exe) | 驱动/设备节点安装工具 | MIT |

## pynput / LGPL-3.0

`pynput` 使用 LGPL-3.0。闭源分发 WizFlow 时：

- 保留 pynput 的 LGPL-3.0 许可证文本；
- 保留 pynput 的版权声明；
- 允许用户替换/重新链接 pynput；
- 如果修改了 pynput 本身，修改部分按 LGPL-3.0 开源。

WizFlow 本身以 GPL-2.0 发布，源码公开，因此上述 LGPL 合规要求自然满足。

## Parsec VDD / nefcon

`assets/vdd/` 内包含：

- `driver/mm.inf`、`mm.dll`、`mm.cat`：来自 [nomi-san/parsec-vdd](https://github.com/nomi-san/parsec-vdd) v0.45.1，MIT，驱动已签名；
- `nefconw.exe`：来自 [nefarius/NefCon](https://github.com/nefarius/NefCon)，MIT，用于创建设备节点和安装驱动；
- `LICENSE-parsec-vdd.txt`：上游 MIT 许可证原文。

仅在用户主动启用「虚拟屏保活」时安装，可随时在设置页卸载。

## KeymouseGo

WizFlow 的录制/回放模型参考了 [KeymouseGo](https://github.com/taojy123/KeymouseGo)，该项目使用 GPL-2.0。WizFlow 同样使用 GPL-2.0，并保留上游项目署名。

## GPL-2.0

WizFlow 的完整许可证见仓库根目录 `LICENSE`。
