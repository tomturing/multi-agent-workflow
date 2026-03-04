"""
CLI 入口 — python -m dispatcher

子命令:
    run      主轮询循环（默认）
    status   显示当前调度状态
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .core import Dispatcher, DispatcherConfig


def cmd_run(args: argparse.Namespace):
    """主轮询循环"""
    config = _load_config(args)
    dispatcher = Dispatcher(config, dry_run=args.dry_run)

    if args.once:
        dispatcher.poll_once()
    else:
        dispatcher.run()


def cmd_status(args: argparse.Namespace):
    """显示当前调度状态"""
    config = _load_config(args)
    dispatcher = Dispatcher(config, dry_run=True)

    # 执行一次轮询以刷新数据
    if not args.cached:
        try:
            dispatcher.poll_once()
        except Exception as e:
            print(f"⚠ 轮询失败 ({e})，使用缓存状态", file=sys.stderr)

    print(dispatcher.get_status_report())


def _load_config(args: argparse.Namespace) -> DispatcherConfig:
    """加载配置文件"""
    project_dir = os.path.abspath(args.project_dir)
    config_path = os.path.join(project_dir, args.config)

    if not os.path.isdir(project_dir):
        print(f"错误: 项目目录不存在: {project_dir}", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(config_path):
        print(f"错误: 配置文件不存在: {config_path}", file=sys.stderr)
        print("提示: 运行 init.sh 或手动创建 .vk/dispatcher.json", file=sys.stderr)
        sys.exit(1)

    return DispatcherConfig.load(config_path, project_dir)


def _setup_logging(verbose: bool = False):
    """配置日志格式"""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "[%(asctime)s] [%(levelname)-5s] %(message)s"
    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def main():
    parser = argparse.ArgumentParser(
        description="VK 中央调度器 — 多 Agent 工作流自动化引擎",
        prog="python -m dispatcher",
    )

    # 全局参数
    parser.add_argument(
        "--project-dir",
        "-d",
        default=os.getcwd(),
        help="目标项目根目录 (默认: 当前目录)",
    )
    parser.add_argument(
        "--config",
        "-c",
        default=".vk/dispatcher.json",
        help="配置文件路径，相对于 project-dir (默认: .vk/dispatcher.json)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="显示 DEBUG 级别日志",
    )

    sub = parser.add_subparsers(dest="command")

    # run 子命令
    p_run = sub.add_parser("run", help="主轮询循环")
    p_run.add_argument("--once", action="store_true", help="单次轮询后退出")
    p_run.add_argument("--dry-run", action="store_true", help="仅检测不执行动作")
    p_run.set_defaults(func=cmd_run)

    # status 子命令
    p_status = sub.add_parser("status", help="显示当前调度状态")
    p_status.add_argument("--cached", action="store_true", help="仅显示缓存状态，不轮询")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    _setup_logging(args.verbose)

    # 默认子命令: run
    if not args.command:
        args.command = "run"
        args.once = False
        args.dry_run = False
        args.func = cmd_run

    args.func(args)


if __name__ == "__main__":
    main()
