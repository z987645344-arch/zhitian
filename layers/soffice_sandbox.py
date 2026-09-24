# -*- coding: utf-8 -*-
"""在 exec LibreOffice 前限制该进程树创建网络套接字。"""

import ctypes
import errno
import os
import socket
import sys


_PR_SET_NO_NEW_PRIVS = 38
_SCMP_ACT_ALLOW = 0x7FFF0000
_SCMP_ACT_ERRNO_EPERM = 0x00050000 | errno.EPERM
_SCMP_CMP_NE = 1
_SANDBOX_UNAVAILABLE_EXIT = 126


class _ArgComparison(ctypes.Structure):
    _fields_ = [
        ("arg", ctypes.c_uint),
        ("op", ctypes.c_int),
        ("datum_a", ctypes.c_uint64),
        ("datum_b", ctypes.c_uint64),
    ]


def install_no_network_filter() -> None:
    """只允许 AF_UNIX；禁止当前进程及其后代创建网络套接字。

    LibreOffice 启动自身子进程需要 Unix socket，不能禁用全部 socket()。
    API 调用者以 close_fds=True 启动本程序，不传入已有网络描述符；
    exec 后及 soffice 派生的子进程都会继承 seccomp 过滤器。安装失败时
    必须拒绝转换，绝不能回退到未经隔离的 soffice。
    """
    if sys.platform != "linux":
        raise RuntimeError("linux_seccomp_required")

    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [
        ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong
    ]
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS")

    seccomp = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
    # seccomp_rule_add 是变参函数；前四个参数固定，第五个传比较结构体。
    seccomp.seccomp_rule_add.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint
    ]
    seccomp.seccomp_rule_add.restype = ctypes.c_int
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_load.restype = ctypes.c_int
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]

    context = seccomp.seccomp_init(_SCMP_ACT_ALLOW)
    if not context:
        raise RuntimeError("seccomp_init_failed")
    try:
        for name in (b"socket", b"socketpair"):
            syscall = seccomp.seccomp_syscall_resolve_name(name)
            if syscall < 0:
                raise RuntimeError("seccomp_syscall_unavailable")
            comparison = _ArgComparison(0, _SCMP_CMP_NE, socket.AF_UNIX, 0)
            result = seccomp.seccomp_rule_add(
                context, _SCMP_ACT_ERRNO_EPERM, syscall, 1, comparison
            )
            if result != 0:
                raise OSError(-result, "seccomp_rule_add")

        # 防止通过 io_uring 创建套接字而绕开 socket() 过滤。
        io_uring_setup = seccomp.seccomp_syscall_resolve_name(b"io_uring_setup")
        if io_uring_setup >= 0:
            result = seccomp.seccomp_rule_add(
                context, _SCMP_ACT_ERRNO_EPERM, io_uring_setup, 0
            )
            if result != 0:
                raise OSError(-result, "seccomp_rule_add")

        result = seccomp.seccomp_load(context)
        if result != 0:
            raise OSError(-result, "seccomp_load")
    finally:
        seccomp.seccomp_release(context)


def main() -> int:
    if len(sys.argv) < 2 or not os.path.isabs(sys.argv[1]):
        print("LibreOffice 网络隔离不可用：invalid_command", file=sys.stderr)
        return _SANDBOX_UNAVAILABLE_EXIT
    try:
        install_no_network_filter()
        os.execv(sys.argv[1], sys.argv[1:])
    except Exception as exc:
        print(
            "LibreOffice 网络隔离不可用：error_type=%s" % type(exc).__name__,
            file=sys.stderr,
        )
        return _SANDBOX_UNAVAILABLE_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
