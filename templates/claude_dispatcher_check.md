## Dispatcher 健康检查

每次对话开始时，检查本项目 dispatcher 是否运行：

```bash
ps aux | grep "dispatcher.*--project-dir.*{{PROJECT_DIR}}" | grep -v grep
```

如果无输出（进程不存在），请提示用户运行以下命令启动 dispatcher，并告知日志路径：

```bash
cd {{DISPATCHER_DIR}}
nohup .venv/bin/python -m dispatcher --project-dir {{PROJECT_DIR}} run \
  > /tmp/dispatcher-{{PROJECT_NAME}}.log 2>&1 &
echo "Dispatcher 已启动，PID: $! | 日志: /tmp/dispatcher-{{PROJECT_NAME}}.log"
```
