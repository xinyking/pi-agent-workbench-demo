---
name: task-board
description: 将其他编码助手或工具中的任务导入本机 Agent 任务看板，并回传步骤进度、工具结果与完成状态。适用于外部软件执行、工作台观察进度的协作流程。
---

# 本机任务看板接入

本技能调用本机工作台 API，不提供额外工具权限。只有用户已要求执行的工作才可运行；导入任务本身不授权执行命令。执行仍由当前软件负责，看板只接收状态和结果。

默认地址 `http://localhost:4311`；Windows/WSL 需能访问同一个本机服务。先读取 `scripts/board.py --help`。脚本仅使用 Python 标准库，可将本目录整体复制到其他支持 SKILL.md 的软件技能目录；不要求安装到本项目的 Agent 角色库。

## 导入与逐步回传

```bash
python3 <技能目录>/scripts/board.py import --source my-editor --external-id stable-task-001 --prompt '核查项目并修复问题'
python3 <技能目录>/scripts/board.py report <返回的id> --sequence 1 --status running --step '正在检查代码'
python3 <技能目录>/scripts/board.py report <id> --sequence 2 --status running --step '测试完成' --tool pytest --result '12 passed'
python3 <技能目录>/scripts/board.py report <id> --sequence 3 --status completed --step '已交付' --result '说明实际改动、验证结果和限制'
```

同一 source + external-id 重复导入返回同一卡片，prompt 必须一致。使用稳定 ID，不能每次报告随机生成新任务。报告 sequence 从 1 连续递增；相同末条序号和内容可重试。超时或序号冲突先 `inspect <id>`，核对已收到的进度再决定下一步，不自动重做工具操作。

执行中每完成一个有意义的步骤回传一次。缺信息用 blocked 并写明原因；失败用 failed；取消用 cancelled。completed 必须带实际交付摘要，工具退出零不代表业务验收通过。外部结果可在任务详情「执行记录」查看，工具报告在时间线中与结果配对。不要上传认证、密钥或无关大日志，result 上限 16000 字。

## 包装单个已获授权的命令

```bash
python3 <技能目录>/scripts/board.py run --source my-editor --external-id test-001 --prompt '运行指定测试' --timeout 600 -- python3 -m unittest discover -s tests
```

命令通过参数数组执行，不经过 shell；需要管道时由调用软件显式决定是否使用 shell。返回末尾最多 12000 字节的合并输出，并记录退出码。已开始的 external-id 不会自动再次执行；失败后先核对副作用，再以新 ID 发起经授权的重试。

若 `/api/external/tasks` 不存在，后台尚未加载接入功能：明确报告需要安全重启，勿伪造成功或退回直接写 SQLite。看板停止按钮不控制外部进程；停止需在实际执行软件中完成并回传取消状态。
