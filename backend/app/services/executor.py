"""执行器抽象层

统一本机执行与远程 SSH 执行接口，所有功能基于用户配置的 Target 运行，
不依赖任何硬编码环境。

除命令行执行 run() 外，另提供 write_file()：通过 scp（远程）或本地文件
写入大体积内容，绕开 Windows cmd.exe 命令行 8191 字符上限——长 prompt 测速
必须走此通道，否则 base64 内嵌的命令会被截断。
"""

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional

from ..models.target import Target


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _decode(data: bytes) -> str:
    """尝试 UTF-8 解码，失败回退 GBK（Windows 中文系统常见）"""
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gbk", errors="replace")


class Executor:
    """执行器基类"""

    def run(self, cmd: str, timeout: int = 15) -> ExecResult:
        raise NotImplementedError

    def write_file(self, content: str, path: str) -> bool:
        """把文本内容以 UTF-8 写入目标机的 path（不受命令行长度限制）"""
        raise NotImplementedError

    def read_file_bytes(self, path: str) -> Optional[bytes]:
        """读取目标机上 path 的二进制内容（用于把成片等产物拉回控制端）。失败返回 None。"""
        raise NotImplementedError

    def write_file_bytes(self, data: bytes, path: str) -> bool:
        """把二进制内容写入目标机的 path（用于上传首帧图等产物）。成功返回 True。"""
        raise NotImplementedError

    def close(self):
        pass


class LocalExecutor(Executor):
    """本机执行器"""

    def run(self, cmd: str, timeout: int = 15) -> ExecResult:
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, timeout=timeout
            )
            return ExecResult(
                stdout=_decode(result.stdout).strip(),
                stderr=_decode(result.stderr).strip(),
                returncode=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(stdout="", stderr="命令执行超时", returncode=-1)
        except Exception as e:
            return ExecResult(stdout="", stderr=str(e), returncode=-1)

    def write_file(self, content: str, path: str) -> bool:
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(path, "wb") as f:
                f.write(content.encode("utf-8"))
            return True
        except Exception:
            return False

    def read_file_bytes(self, path: str) -> Optional[bytes]:
        try:
            with open(path, "rb") as f:
                return f.read()
        except Exception:
            return None

    def write_file_bytes(self, data: bytes, path: str) -> bool:
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
            return True
        except Exception:
            return False


class SSHExecutor(Executor):
    """远程 SSH 执行器（复用系统 OpenSSH 客户端 ssh / scp）

    不使用 paramiko：系统 ssh/scp 能正确读取 ~/.ssh/config、使用默认密钥
    （id_ed25519 等标准名）、与 Windows OpenSSH 完成算法协商，兼容性更好，
    也避免 paramiko 在某些环境下连接握手卡住的问题。
    """

    def __init__(self, target: Target):
        self.target = target

    def _dest(self) -> str:
        return f"{self.target.user}@{self.target.host}"

    def _common_opts(self) -> list:
        # BatchMode=yes：需要交互（密码/确认）时立即失败而非永久挂起，
        # 对后台服务至关重要。
        return [
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
        ]

    def _identity(self) -> list:
        # 指定了私钥则用之；否则交给系统 ssh 使用默认密钥（~/.ssh/id_ed25519 等）
        if self.target.key_path:
            return ["-i", os.path.expanduser(self.target.key_path)]
        return []

    def _wrap_pw(self, base: list) -> list:
        """密码认证且装有 sshpass 时前置 sshpass；否则原样返回。
        未装 sshpass 时 BatchMode 会让密码认证快速失败而非挂起。"""
        t = self.target
        if t.auth_type == "password" and t.password and shutil.which("sshpass"):
            return ["sshpass", "-p", t.password] + base
        return base

    def run(self, cmd: str, timeout: int = 15) -> ExecResult:
        argv = self._wrap_pw(
            ["ssh"] + self._common_opts() + self._identity()
            + ["-p", str(self.target.port), self._dest(), cmd]
        )
        try:
            result = subprocess.run(argv, capture_output=True, timeout=timeout)
            return ExecResult(
                stdout=_decode(result.stdout).strip(),
                stderr=_decode(result.stderr).strip(),
                returncode=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(stdout="", stderr="SSH 命令执行超时", returncode=-1)
        except Exception as e:
            return ExecResult(stdout="", stderr=str(e), returncode=-1)

    def _scp_argv(self, src: str, dst: str) -> list:
        # scp 用 -P 指定端口（与 ssh 的 -p 不同）
        base = (["scp"] + self._common_opts() + self._identity()
                + ["-P", str(self.target.port), src, dst])
        return self._wrap_pw(base)

    def write_file(self, content: str, path: str) -> bool:
        return self.write_file_bytes(content.encode("utf-8"), path)

    def write_file_bytes(self, data: bytes, path: str) -> bool:
        """通过 scp 上传，绕开命令行长度限制。

        Windows OpenSSH 的 scp 接受正斜杠路径（如 C:/temp/bench.json），
        目录需调用方先行创建。"""
        remote = path.replace("\\", "/")
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp()
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            result = subprocess.run(
                self._scp_argv(tmp, f"{self._dest()}:{remote}"),
                capture_output=True, timeout=60,
            )
            return result.returncode == 0
        except Exception:
            return False
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def read_file_bytes(self, path: str) -> Optional[bytes]:
        """通过 scp 下载目标机文件（如把成片 mp4 拉回控制端）。

        Windows OpenSSH 的 scp 接受正斜杠路径，调用方需先把反斜杠转过来。"""
        remote = path.replace("\\", "/")
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp()
            os.close(fd)
            result = subprocess.run(
                self._scp_argv(f"{self._dest()}:{remote}", tmp),
                capture_output=True, timeout=120,
            )
            if result.returncode != 0 or not os.path.exists(tmp):
                return None
            with open(tmp, "rb") as f:
                return f.read()
        except Exception:
            return None
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def close(self):
        # 系统 ssh/scp 每次调用都是独立连接，无需维护长连接
        pass


def make_executor(target: Target) -> Executor:
    """根据 Target 配置创建对应执行器"""
    if target.conn_type == "ssh":
        return SSHExecutor(target)
    return LocalExecutor()
