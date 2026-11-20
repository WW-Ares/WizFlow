# WizFlow 项目约定（长期记忆）

## 发版标题

- 只写 `WizFlow vX.Y.Z`，不加任何后缀说明。
- 示例：`WizFlow v1.9.3`

## 界面字号

- 全项目只有一套默认字号，不存在 100% / 115% / 130% 等缩放档位。
- 文档、验收、Release 说明中不得再出现缩放档位概念。

## Release 正文模板（Markdown，章节顺序固定）

```markdown
### 新增

- **功能名**：一句话说明

### 改进

- 窗口 / 页面：改了什么

### 验证

- `命令`：结果
- `命令`：结果

### 说明

- 兼容性 / 默认值 / 注意事项

### 修复

- 现象：原因与修复方式
```

## 产物

- `WizFlow.exe`（单文件）
- `WizFlow-win64-X.Y.Z.zip`（文件夹版）

## 发布流程

1. 回归：`compileall`、`tests/test_core.py`、`tests/test_monitor_group.py`、`--selftest`
2. 打包单文件与文件夹版，两个 exe 各跑一次 `--selftest`，再做一次 UI 冒烟
3. 提交并推送 `main`
4. 创建/覆盖 Release：标题纯版本号，正文按上面的模板
5. 推送与发版需用户确认（用户当次明确授权除外）
