"""Explicit Git btf updates, without altering user data, dependencies or branches."""
from dataclasses import replace
from pathlib import Path
import json
import os
import shutil
import subprocess
import threading
import time

REMOTE = 'https://github.com/Zhaowenb/Pico_4824A.git'
MIN_CHECK_INTERVAL_S = 60
MAX_CHECK_INTERVAL_S = 365 * 24 * 3600
DEFAULT_CHECK_INTERVAL_S = 3600


def valid_check_interval(value):
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_CHECK_INTERVAL_S <= value <= MAX_CHECK_INTERVAL_S:
        raise ValueError('自动检查间隔必须为 1 分钟至 1 年（365 天）的整数秒')
    return value

DEPENDENCIES = {'requirements.txt', 'pyproject.toml', 'setup.ps1', 'deploy/linux/Dockerfile',
                'deploy/linux/Dockerfile.offline'}


class SoftwareUpdater:
    def __init__(self, root, control, restart=None, trusted_remote=REMOTE):
        self.root = Path(root).resolve()
        if self.root.name.casefold() == 'pico_4824a':
            raise RuntimeError('参考 SOURCE 仓库只读，不能执行软件更新')
        self.control, self.restart = control, restart
        self.trusted_remote = trusted_remote
        self._lock = threading.RLock()
        self._thread = None
        self._running = None
        self.preference_path = self.root/'configs/software-updates.json'
        auto = True
        interval = DEFAULT_CHECK_INTERVAL_S
        try:
            saved = json.loads(self.preference_path.read_text(encoding='utf-8'))
            if not isinstance(saved, dict):
                raise ValueError('无效的软件更新配置')
            if isinstance(saved.get('auto_check'), bool):
                auto = saved['auto_check']
            interval = valid_check_interval(saved.get('check_interval_s', DEFAULT_CHECK_INTERVAL_S))
        except (OSError, ValueError, KeyError):
            pass
        self.state = {'phase':'idle', 'auto_check':auto, 'check_interval_s':interval, 'branch':'btf', 'current':None,
                      'running':None, 'latest':None, 'available':False, 'checked_at':None,
                      'message':'尚未检查更新', 'blocked':None, 'changes':0}

    def git(self, *args, timeout=45):
        executable = shutil.which('git')
        if not executable:
            raise RuntimeError('未安装 Git；请安装 Git 后重启服务。容器需使用含 Git 的新版镜像')
        env = os.environ.copy()
        env.update(GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never', PYTHONDONTWRITEBYTECODE='1')
        command = [executable, '-c', f'safe.directory={self.root}', '-c', 'http.version=HTTP/1.1',
                   '-c', f'core.hooksPath={self.root / ".git/waveguard-no-hooks"}']
        if os.name == 'nt':
            command += ['-c', 'http.sslBackend=openssl']
        command += ['-C', str(self.root), *args]
        try:
            reply = subprocess.run(command, env=env, capture_output=True, text=True,
                                   encoding='utf-8', errors='replace', timeout=timeout,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('Git 请求超时，请检查网络后重试；本地代码未被强制覆盖') from exc
        if reply.returncode:
            # Never expose credential-bearing remote URLs in HTTP errors.
            details = (reply.stderr or reply.stdout).strip().splitlines()
            detail = details[-1] if details else 'Git 操作失败'
            if '://' in detail or '@github' in detail:
                detail = 'Git 请求失败，请检查网络、仓库权限和本地修改'
            raise RuntimeError(detail[:300])
        return reply.stdout.strip()

    def inspect(self):
        top = Path(self.git('rev-parse', '--show-toplevel')).resolve()
        if top != self.root:
            raise RuntimeError('当前目录不是独立项目 Git 仓库；请拉取 btf 分支到开发目录')
        branch = self.git('symbolic-ref', '--short', 'HEAD')
        if branch != 'btf':
            raise RuntimeError('当前不在 btf 分支；自动更新不会切换分支')
        remote = self.git('remote', 'get-url', 'origin')
        allowed = {self.trusted_remote, self.trusted_remote.removesuffix('.git')}
        if self.trusted_remote == REMOTE:
            allowed.add('git@github.com:Zhaowenb/Pico_4824A.git')
        if remote not in allowed:
            raise RuntimeError('origin 不是已确认的 WaveGuard 仓库，拒绝自动更新')
        head = self.git('rev-parse', 'HEAD')
        dirty = bool(self.git('status', '--porcelain'))
        if self._running is None:
            self._running = head
        return head, dirty

    def snapshot(self):
        with self._lock:
            if self.state['current'] is None and self.state['phase'] == 'idle':
                try:
                    head, dirty = self.inspect()
                    self.state.update(current=head, running=self._running,
                                      blocked='本地有未提交修改；请先保存修改' if dirty else None)
                except Exception as exc:
                    self.state.update(phase='unavailable', blocked=str(exc), message=str(exc))
            return dict(self.state)

    def preferences(self, raw):
        auto = raw.get('auto_check')
        if not isinstance(auto, bool):
            raise ValueError('auto_check 必须是布尔值')
        with self._lock:
            interval = valid_check_interval(raw.get('check_interval_s', self.state['check_interval_s']))
            self.preference_path.parent.mkdir(parents=True, exist_ok=True)
            self.preference_path.write_text(json.dumps({'auto_check':auto, 'check_interval_s':interval}, indent=2), encoding='utf-8')
            self.state['auto_check'] = auto
            self.state['check_interval_s'] = interval
        return self.snapshot()

    def idle(self):
        c = self.control
        if c.status.state in {'running','paused'} or (c._worker_thread and c._worker_thread.is_alive()):
            raise RuntimeError('仪器任务正在运行，请停止并等待收尾后更新')
        if c.bias_unknown or c.bias_cleanup_pending:
            raise RuntimeError('偏置输出状态未知或设备仍在收尾，不能更新')
        if c.bias_power and c.bias_power.output_state != 'off':
            raise RuntimeError('请先确认偏置电源输出关闭')
        controller = c.bias_controller
        if controller and (getattr(controller, '_pending_group', None) or any(t.is_alive() for t in controller._threads)):
            raise RuntimeError('仍有待保存数据或安全线程，请先确认断电并恢复')

    def check(self):
        with self.control._lock, self._lock:
            self.idle()
            if self._thread and self._thread.is_alive():
                return dict(self.state)
            self.state.update(phase='checking', message='正在检查 btf 更新', blocked=None)
            self._thread = threading.Thread(target=self._check, daemon=True)
            self._thread.start()
            return dict(self.state)

    def _check(self):
        try:
            head, dirty = self.inspect()
            self.git('fetch', '--no-tags', 'origin', 'refs/heads/btf:refs/remotes/origin/btf')
            latest = self.git('rev-parse', 'refs/remotes/origin/btf')
            self.git('merge-base', '--is-ancestor', head, latest)
            files = set(self.git('diff', '--name-only', self._running or head, latest).splitlines())
            dependency_change = bool(files & DEPENDENCIES)
            changes = int(self.git('rev-list', '--count', f'{head}..{latest}'))
            available = latest != head or head != self._running
            blocked = ('本次更新包含依赖/驱动配置变更，请先更新运行环境后手动更新' if dependency_change else None)
            if dirty:
                blocked = '本地有未提交修改；先提交或备份整理后再更新，不会自动覆盖'
            if available and self.restart is None:
                blocked = '当前启动方式不支持自动重启，请使用项目 Web 启动入口'
            with self._lock:
                self.state.update(phase='available' if available else 'current', current=head, running=self._running,
                                  latest=latest, available=available, changes=changes, blocked=blocked,
                                  checked_at=time.time(), message='发现新版本' if available else '已是最新版本')
        except Exception as exc:
            with self._lock:
                self.state.update(phase='error', available=False, blocked=str(exc), message='检查失败：'+str(exc))

    def apply(self):
        with self.control._lock, self._lock:
            self.idle()
            if self._thread and self._thread.is_alive():
                raise RuntimeError('已有更新操作正在进行')
            if self.state['blocked'] or not self.state['available'] or self.restart is None:
                raise RuntimeError(self.state['blocked'] or '请先检查更新，当前没有可安装版本')
            previous = replace(self.control.status)
            self.control.status.state = 'running'
            self.control.status.task_kind = 'software_update'
            self.control.status.message = '软件更新中 · 仪器操作已锁定'
            self.state.update(phase='installing', message='正在安全关闭设备并更新代码')
            self._thread = threading.Thread(target=self._apply, args=(previous,), daemon=True)
            self._thread.start()
            return dict(self.state)

    def _apply(self, previous):
        try:
            head, dirty = self.inspect()
            if dirty or head != self.state['current']:
                raise RuntimeError('本地代码或修改状态发生变化，请重新检查更新')
            # OFF and Pico close precede any checkout mutation and process restart.
            self.control.shutdown()
            if self.control.bias_unknown or self.control.bias_cleanup_pending:
                raise RuntimeError('设备未确认安全收尾，更新已停止')
            folder = self.root/'data/software_updates'
            hooks = folder/'no-hooks'
            hooks.mkdir(parents=True, exist_ok=True)
            self.git('-c', f'core.hooksPath={hooks}', 'merge', '--ff-only', self.state['latest'])
            revision = self.git('rev-parse', 'HEAD')
            (folder/'last-update.json').write_text(json.dumps({'before':head,'after':revision,'time':time.time()},indent=2),encoding='utf-8')
            with self._lock:
                self.state.update(phase='restarting', current=revision, message='代码已更新，正在重启服务')
            self.restart()
        except Exception as exc:
            with self.control._lock:
                self.control.status = previous
            with self._lock:
                self.state.update(phase='error', blocked=str(exc), message='更新未完成：'+str(exc))
