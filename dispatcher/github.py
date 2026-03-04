"""
GitHub API 客户端 — PR 创建 / 合并 / 评论

纯标准库实现（urllib），零外部依赖。
通过 GitHub REST API v3 操作 PR 生命周期。

认证方式:
    1. 环境变量 GITHUB_TOKEN
    2. 文件 .vk/github_token（不提交 git）

设计原则:
- 每个方法是一个独立的 HTTP 请求，无状态
- 所有错误通过 GitHubAPIError 异常抛出
- 返回值为解析后的 JSON 字典
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import urllib.error
import urllib.request

logger = logging.getLogger("dispatcher.github")


class GitHubAPIError(Exception):
    """GitHub API 调用失败"""

    def __init__(self, message: str, status: int = 0, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class GitHubClient:
    """GitHub REST API v3 客户端

    典型用法:
        gh = GitHubClient.from_project("/path/to/project")
        pr = gh.create_pr("feature-branch", "main", "PR title", "body")
        gh.merge_pr(pr["number"], merge_method="squash")
    """

    API_BASE = "https://api.github.com"

    def __init__(self, owner: str, repo: str, token: str):
        self.owner = owner
        self.repo = repo
        self.token = token

    @classmethod
    def from_project(cls, project_dir: str) -> GitHubClient:
        """从项目目录自动检测 owner/repo 和 token

        检测逻辑:
        1. git remote get-url origin → 解析 owner/repo
        2. GITHUB_TOKEN 环境变量 → token
        3. .vk/github_token 文件 → token（后备）
        """
        # 解析 remote URL
        owner, repo = cls._parse_remote(project_dir)

        # 获取 token
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            token_file = os.path.join(project_dir, ".vk", "github_token")
            if os.path.isfile(token_file):
                with open(token_file) as f:
                    token = f.read().strip()

        if not token:
            raise GitHubAPIError(
                "未找到 GitHub Token。设置 GITHUB_TOKEN 环境变量 "
                "或创建 .vk/github_token 文件"
            )

        return cls(owner, repo, token)

    # ---- PR 操作 ----

    def create_pr(
        self,
        head: str,
        base: str,
        title: str,
        body: str = "",
        draft: bool = False,
    ) -> dict:
        """创建 Pull Request

        Args:
            head: 源分支（feature 分支）
            base: 目标分支（通常是 main/master）
            title: PR 标题
            body: PR 描述（Markdown）
            draft: 是否创建为 Draft PR

        Returns:
            PR 字典，包含 number, html_url, state 等字段
        """
        data = {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
            "draft": draft,
        }
        return self._request("POST", f"/repos/{self.owner}/{self.repo}/pulls", data)

    def get_pr(self, pr_number: int) -> dict:
        """获取 PR 详情"""
        return self._request("GET", f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}")

    def merge_pr(
        self,
        pr_number: int,
        merge_method: str = "squash",
        commit_title: str | None = None,
        commit_message: str | None = None,
    ) -> dict:
        """合并 Pull Request

        Args:
            pr_number: PR 编号
            merge_method: 合并方式 — "merge" (no-ff) / "squash" / "rebase"
            commit_title: 自定义合并提交标题
            commit_message: 自定义合并提交消息

        Returns:
            合并结果字典，包含 merged, sha, message
        """
        data: dict = {"merge_method": merge_method}
        if commit_title:
            data["commit_title"] = commit_title
        if commit_message:
            data["commit_message"] = commit_message

        return self._request(
            "PUT", f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}/merge", data
        )

    def delete_branch(self, branch: str) -> None:
        """删除远程分支（对应 GitHub auto-delete head branch after merge）

        使用 DELETE /repos/{owner}/{repo}/git/refs/heads/{branch}
        分支不存在时静默忽略（404）。
        """
        try:
            self._request(
                "DELETE",
                f"/repos/{self.owner}/{self.repo}/git/refs/heads/{branch}",
            )
        except GitHubAPIError as e:
            if e.status != 404:
                raise

    def add_pr_comment(self, pr_number: int, body: str) -> dict:
        """在 PR 上添加评论"""
        return self._request(
            "POST",
            f"/repos/{self.owner}/{self.repo}/issues/{pr_number}/comments",
            {"body": body},
        )

    def close_pr(self, pr_number: int) -> dict:
        """关闭 PR（不合并）"""
        return self._request(
            "PATCH",
            f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}",
            {"state": "closed"},
        )

    def list_open_prs(self, head: str | None = None) -> list[dict]:
        """列出 open 状态的 PR

        Args:
            head: 按源分支过滤（格式: "owner:branch" 或 "branch"）
        """
        url = f"/repos/{self.owner}/{self.repo}/pulls?state=open"
        if head:
            # GitHub API 要求格式 "owner:branch"
            if ":" not in head:
                head = f"{self.owner}:{head}"
            url += f"&head={head}"
        return self._request("GET", url)

    def find_pr_by_head_branch(self, branch: str) -> dict | None:
        """通过 head branch 名查找 PR（包含已关闭/已合并）

        用于删前二次确认：验证 PR 确实处于 merged 状态再删分支。
        state=all 确保覆盖 merged/closed 两种结束态。
        """
        if ":" not in branch:
            branch = f"{self.owner}:{branch}"
        result = self._request(
            "GET",
            f"/repos/{self.owner}/{self.repo}/pulls?state=all&head={branch}&per_page=1",
        )
        if isinstance(result, list) and result:
            return result[0]
        return None

    # ---- Git 操作（本地 + push）----

    @staticmethod
    def push_branch(project_dir: str, branch: str, force: bool = False) -> bool:
        """推送分支到 GitHub remote

        Args:
            project_dir: 项目根目录
            branch: 要推送的分支名
            force: 是否 force push
        """
        cmd = ["git", "-C", project_dir, "push", "origin", branch]
        if force:
            cmd.append("--force")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=True,
            )
            logger.info("推送分支 %s → origin", branch)
            return True
        except subprocess.CalledProcessError as e:
            logger.error("推送失败: %s", e.stderr.strip())
            return False

    @staticmethod
    def generate_diff(
        project_dir: str, base: str, head: str, stat_only: bool = False
    ) -> str:
        """生成两个分支之间的 diff

        Args:
            project_dir: 项目根目录
            base: 基础分支（如 main）
            head: 目标分支（如 feature）
            stat_only: 仅输出文件变更统计

        Returns:
            diff 文本（可能很长，调用方需截断）
        """
        cmd = ["git", "-C", project_dir, "--no-pager", "diff"]
        if stat_only:
            cmd.append("--stat")
        cmd.append(f"{base}...{head}")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=True,
            )
            return result.stdout
        except subprocess.CalledProcessError:
            return ""

    # ---- 内部方法 ----

    @staticmethod
    def _parse_remote(project_dir: str) -> tuple[str, str]:
        """从 git remote 解析 owner 和 repo

        支持格式:
        - https://github.com/owner/repo.git
        - git@github.com:owner/repo.git
        """
        try:
            result = subprocess.run(
                ["git", "-C", project_dir, "remote", "get-url", "origin"],
                capture_output=True, text=True, check=True,
            )
            url = result.stdout.strip()
        except subprocess.CalledProcessError:
            raise GitHubAPIError("无法获取 git remote URL（确认 origin 已配置）")

        # HTTPS 格式
        if "github.com/" in url:
            parts = url.split("github.com/")[1]
            parts = parts.rstrip("/").removesuffix(".git")
            segments = parts.split("/")
            if len(segments) >= 2:
                return segments[0], segments[1]

        # SSH 格式
        if "github.com:" in url:
            parts = url.split("github.com:")[1]
            parts = parts.rstrip("/").removesuffix(".git")
            segments = parts.split("/")
            if len(segments) >= 2:
                return segments[0], segments[1]

        raise GitHubAPIError(f"无法解析 GitHub remote URL: {url}")

    def _request(
        self, method: str, path: str, data: dict | None = None
    ) -> dict | list:
        """发送 GitHub API 请求"""
        url = f"{self.API_BASE}{path}" if path.startswith("/") else path

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        body = None
        if data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            resp = urllib.request.urlopen(req, timeout=30)
            resp_body = resp.read().decode()
            if resp_body:
                return json.loads(resp_body)
            return {}
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode()
            except Exception:
                pass
            logger.error("GitHub API %s %s → %d: %s", method, path, e.code, error_body[:200])
            raise GitHubAPIError(
                f"GitHub API 错误: {method} {path} → {e.code}",
                status=e.code,
                body=error_body,
            ) from e
