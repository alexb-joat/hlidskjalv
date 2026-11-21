"""Starts a shell on a pty: the child side of PosixShell, run as `python -I _ptyexec.py [--nnp] -- argv...`.

This replaces preexec_fn. Python's own documentation says preexec_fn is not safe in the presence of
threads (the child may deadlock on a lock another thread held at fork), and the dashboard's server is
threaded. Here the work happens after a clean fork+exec, in a fresh single-threaded interpreter:

  1. the pty (already stdin, stdout, stderr) becomes the controlling terminal; Popen's
     start_new_session=True made this process a session leader, which TIOCSCTTY needs;
  2. optionally PR_SET_NO_NEW_PRIVS, so sudo and other setuid programs cannot elevate (Linux);
  3. signals Python changed at start-up are put back, because "ignored" survives exec and a shell
     with SIGPIPE ignored misbehaves in pipelines;
  4. exec the shell. Nothing is left of this process.

Flags come on argv, never the environment (the shell inherits the environment). Standard library only,
and no package imports: `-I` drops the working directory and PYTHONPATH from sys.path.
"""

import fcntl
import os
import signal
import sys
import termios

PR_SET_NO_NEW_PRIVS = 38


def main(argv: list[str]) -> None:
    nnp = bool(argv) and argv[0] == "--nnp"
    if not argv or argv[0] not in ("--nnp", "--") or len(argv) < 2:
        sys.stderr.write("usage: _ptyexec.py [--nnp|--] command [args...]\n")
        os._exit(2)
    command = argv[1:]
    try:
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        if nnp:
            import ctypes
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
                raise OSError(ctypes.get_errno(), "could not set no-new-privileges")
        for name in ("SIGPIPE", "SIGXFSZ"):  # ignored by Python at start-up, inherited across exec
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), signal.SIG_DFL)
        os.execvp(command[0], command)
    except OSError as e:
        sys.stderr.write(f"could not start {command[0]}: {e}\r\n")
        os._exit(127)


if __name__ == "__main__":
    main(sys.argv[1:])
