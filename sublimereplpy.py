# Original version: copyright (c) 2011, Wojciech Bederski (wuub.net)

import fcntl
import os
import queue
import re
import select
import signal
import subprocess
import sys
import threading
import traceback
from uuid import uuid4

import sublime
import sublime_plugin

SETTINGS_FILE = "SublimeREPL-py.sublime-settings"
SYNTAX_FILE = "Packages/Python/Python.sublime-syntax"
ENCODING = "utf8"

# On POSIX system SublimeText launched from GUI does not inherit
# a proper environment. Often leading to problems with finding interpreters
# or not using the ones affected by changes in ~/.profile / *rc files
# This command is used as a workaround, it's launched before any subprocess
# repl starts and it's output is parsed as an environment
GETENV_COMMAND = ["/bin/bash", "--login", "-c", "env"]


class Repl:
    """Represent a running REPL process abstraction."""

    def __init__(self):
        """Initialize base REPL state."""
        self.id = uuid4().hex

    def close(self):
        """Close the REPL process if it is still alive."""
        if self.is_alive():
            self.kill()

    # def name(self):
    #     """Return the display name used by REPL views."""
    #     raise NotImplementedError

    def is_alive(self):
        """Return whether the underlying process is still running."""
        raise NotImplementedError

    def write_bytes(self, bytes):
        """Write raw bytes to the REPL process."""
        raise NotImplementedError

    def read_bytes(self):
        """Read raw bytes from the REPL process.

        Returns:
            bytes | None: Process output, or ``None`` when stream is closed.
        """
        raise NotImplementedError

    def kill(self):
        """Terminate the underlying REPL process."""
        raise NotImplementedError

    def write(self, command):
        """Encode and write a command string to the REPL.

        Args:
            command: Command string to send.

        Returns:
            Any: Result from ``write_bytes`` implementation.
        """
        return self.write_bytes(command.encode(ENCODING))

    def read(self):
        """Read decoded output from the REPL.

        Returns:
            str | None: Decoded output chunk, or ``None`` when process exits.
        """
        while True:
            bs = self.read_bytes()
            if not bs:
                return None
            output = bs.decode(ENCODING, errors="replace")
            if output:
                return output


# class Unsupported(Exception):
#     """Represent an unsupported subprocess command configuration."""

#     def __init__(self, msgs):
#         """Store user-facing unsupported messages.

#         Args:
#             msgs: Message list describing why the command is unsupported.
#         """
#         super().__init__()
#         self.msgs = msgs

#     def __repr__(self):
#         """Return a printable unsupported message."""
#         return "\n".join(self.msgs)


class SubprocessRepl(Repl):
    """Run a REPL backed by a subprocess."""

    def __init__(
        self,
        cmd,
        cwd,
        soft_quit="",
        **kwds,
    ):
        """Start a subprocess REPL backend.

        Args:
            cmd: Executable command list.
            cwd: Optional working directory.
            soft_quit: Optional text sent before hard kill.
            **kwds: Forwarded REPL base arguments.
        """
        super().__init__(**kwds)
        # settings = sublime.load_settings(SETTINGS_FILE)

        # env = self.env(settings)
        env = self.getenv()

        self._cmd = cmd  # self.cmd(cmd, env)
        self._soft_quit = soft_quit
        self._killed = False
        self.popen = subprocess.Popen(
            self._cmd,
            bufsize=1,
            # preexec_fn=os.setsid,
            cwd=cwd,  # self.cwd(cwd, settings),
            env=env,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

        flags = fcntl.fcntl(self.popen.stdout, fcntl.F_GETFL)
        fcntl.fcntl(self.popen.stdout, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    def getenv(self):
        """Load a shell-like environment for subprocesses.

        Returns:
            dict: Environment mapping.
        """
        # getenv_command = settings.get("getenv_command")

        output = subprocess.check_output(GETENV_COMMAND)
        lines = output.decode(ENCODING, errors="replace").splitlines()
        env = dict(line.split("=", 1) for line in lines)
        return env
        # if getenv_command:
        #     try:
        #         if not (
        #             isinstance(getenv_command, (list, tuple))
        #             and getenv_command
        #             and all(isinstance(part, str) for part in getenv_command)
        #         ):
        #             raise ValueError(
        #                 "'getenv_command' must be a non-empty string list/tuple."
        #             )
        #         output = subprocess.check_output(getenv_command)
        #         lines = output.decode(ENCODING, errors="replace").splitlines()
        #         env = dict(line.split("=", 1) for line in lines)
        #         return env
        #     except Exception:
        #         import traceback as _traceback

        #         _traceback.print_exc()
        #         sublime.error_message(
        #             "SublimeREPL-py: obtaining sane environment failed in getenv()\n"
        #             "Check console and 'getenv_command' setting \n"
        #             "WARN: Falling back to SublimeText environment"
        #         )

        # return os.environ.copy()

    # def env(self, settings):
    #     """Build environment bytes mapping for subprocess launch.

    #     Args:
    #         env: Optional base environment mapping.
    #         extend_env: Optional environment overrides.
    #         settings: Sublime settings object.

    #     Returns:
    #         dict: Environment mapping encoded as bytes pairs.
    #     """
    #     # updated_env = dict(env) if env else self.getenv(settings)
    #     updated_env = self.getenv(settings)
    #     # default_extend_env = settings.get("default_extend_env")
    #     # if default_extend_env:
    #     #     updated_env.update(
    #     #         self.interpolate_extend_env(updated_env, default_extend_env)
    #     #     )
    #     # if extend_env:
    #     #     updated_env.update(self.interpolate_extend_env(updated_env, extend_env))
    #     return {str(k): str(v) for k, v in updated_env.items()}

    # def interpolate_extend_env(self, env, extend_env):
    #     """Substitute values in an extend-env mapping.

    #     Args:
    #         env: Existing environment mapping.
    #         extend_env: Mapping containing ``str.format`` placeholders.

    #     Returns:
    #         dict: New mapping with placeholders substituted.
    #     """
    #     new_env = {}
    #     for key, val in list(extend_env.items()):
    #         new_env[key] = str(val).format(**env)
    #     return new_env

    # def name(self):
    #     """Return process name displayed in the REPL view title."""
    #     if isinstance(self._cmd, str):
    #         return self._cmd
    #     return " ".join([str(x) for x in self._cmd])

    def is_alive(self):
        """Return whether the subprocess is still running."""
        return self.popen.poll() is None

    def read_bytes(self):
        """Read bytes from subprocess stdout.

        Returns:
            bytes: Output bytes chunk.
        """
        out = self.popen.stdout
        while True:
            i, _, _ = select.select([out], [], [], 0.1)
            if i:
                data = out.read(4096)
                if data:
                    return data
                if self.popen.poll() is not None:
                    return None
            elif self.popen.poll() is not None:
                return None

    def write_bytes(self, bytes):
        """Write bytes to subprocess stdin.

        Args:
            bytes: Bytes chunk to write.
        """
        si = self.popen.stdin
        si.write(bytes)
        si.flush()

    def kill(self):
        """Terminate subprocess and its process group."""
        self._killed = True
        self.write(self._soft_quit)
        try:
            os.killpg(self.popen.pid, signal.SIGKILL)
        except OSError:
            pass
        self.popen.kill()


class ReplInsertTextCommand(sublime_plugin.TextCommand):
    """Insert text into a REPL view at a given position."""

    def run(self, edit, pos, text):
        """Insert text.

        Args:
            edit: Sublime edit token.
            pos: Integer-like insertion position.
            text: Text to insert.
        """
        self.view.set_read_only(False)  # make sure view is writable
        self.view.insert(edit, int(pos), text)


# class ReplEraseTextCommand(sublime_plugin.TextCommand):
#     """Erase text from a REPL view range."""

#     def run(self, edit, start, end):
#         """Erase view content in the requested range.

#         Args:
#             edit: Sublime edit token.
#             start: Range start position.
#             end: Range end position.
#         """
#         self.view.set_read_only(False)  # make sure view is writable
#         self.view.erase(edit, sublime.Region(int(start), int(end)))


class ReplPass(sublime_plugin.TextCommand):
    """No-op command used to block invalid key actions."""

    def run(self, edit):
        """Execute the no-op command.

        Args:
            edit: Sublime edit token.
        """


class ReplReader(threading.Thread):
    """Read REPL output on a background thread."""

    def __init__(self, repl):
        """Create a reader thread for the given REPL.

        Args:
            repl: REPL backend object.
        """
        super().__init__()
        self.repl = repl
        self.daemon = True
        self.queue = queue.Queue()

    def run(self):
        """Continuously pull REPL output into a queue."""
        r = self.repl
        q = self.queue
        while True:
            result = r.read()
            q.put(result)
            if result is None:
                break


class HistoryMatchList:
    """Navigate through history entries matching a prefix."""

    def __init__(self, command_prefix, commands):
        """Initialize a match list.

        Args:
            command_prefix: Prefix used to build this match list.
            commands: Matching command strings.
        """
        self._command_prefix = command_prefix
        self._commands = commands
        self._cur = len(commands)  # no '-1' on purpose

    def current_command(self):
        """Return the current command in the match list."""
        if not self._commands:
            return ""
        if self._cur >= len(self._commands):
            return self._command_prefix
        return self._commands[self._cur]

    def prev_command(self):
        """Move to and return the previous matching command."""
        if not self._commands:
            return self._command_prefix
        self._cur = max(0, self._cur - 1)
        return self.current_command()

    def next_command(self):
        """Move to and return the next matching command."""
        if not self._commands:
            return self._command_prefix
        self._cur = min(len(self._commands), self._cur + 1)
        return self.current_command()


class History:
    """Abstract command history interface."""

    def __init__(self):
        """Create history state."""
        self._last = None

    def push(self, command):
        """Push a command unless it is empty or duplicate.

        Args:
            command: Candidate command text.
        """
        cmd = command.rstrip()
        if not cmd or cmd == self._last:
            return
        self.append(cmd)
        self._last = cmd

    def append(self, cmd):
        """Append a command entry."""
        raise NotImplementedError()

    def match(self, command_prefix):
        """Return a matcher for a given command prefix."""
        raise NotImplementedError()


class MemHistory(History):
    """In-memory history storage implementation."""

    def __init__(self):
        """Initialize memory-backed history."""
        super().__init__()
        self._stack = []

    def append(self, cmd):
        """Append one command to memory history.

        Args:
            cmd: Command string.
        """
        self._stack.append(cmd)

    def match(self, command_prefix):
        """Return all commands that start with the given prefix.

        Args:
            command_prefix: Prefix to match.

        Returns:
            HistoryMatchList: Navigator over matching commands.
        """
        matching_commands = []
        for cmd in self._stack:
            if cmd.startswith(command_prefix):
                matching_commands.append(cmd)
        return HistoryMatchList(command_prefix, matching_commands)


class ReplView:
    """Wrap a Sublime view and connect it to a running REPL."""

    def __init__(self, view, repl, banner=None):
        """Initialize a REPL view bridge.

        Args:
            view: Sublime view used for REPL IO.
            repl: REPL backend instance.
            banner: Optional text written before any REPL output.
        """
        self.repl = repl
        self._view = view
        self._window = view.window()
        # list of callable(repl) to handle view close events
        self.call_on_close = []

        view.set_syntax_file(SYNTAX_FILE)
        self._output_end = view.size()
        self._prompt_size = 0

        self._repl_reader = ReplReader(repl)
        self._repl_reader.start()

        settings = sublime.load_settings(SETTINGS_FILE)

        # view.settings().set("repl_external_id", repl.external_id)
        view.settings().set("repl_id", repl.id)
        view.settings().set("repl", True)

        rv_settings = settings.get("repl_view_settings", {})
        for setting, value in list(rv_settings.items()):
            view.settings().set(setting, value)

        view.settings().set("history_arrows", settings.get("history_arrows", True))

        self._history = MemHistory()
        self._history_match = None

        self._filter_color_codes = settings.get("filter_ascii_color_codes")

        if banner:
            self.write(banner)

        # optionally move view to a different group
        # find current position of this replview
        (group, index) = self._window.get_view_index(view)

        # get the view that was focussed before the repl was opened.
        # we'll have to focus this one briefly to make sure it's in the
        # foreground again after moving the replview away
        oldview = self._window.views_in_group(group)[max(0, index - 1)]

        target = settings.get("open_repl_in_group")

        # either the target group is specified by index
        if isinstance(target, int):
            if 0 <= target < self._window.num_groups() and target != group:
                self._window.set_view_index(
                    view, target, len(self._window.views_in_group(target))
                )
                self._window.focus_view(oldview)
                self._window.focus_view(view)
        ## or, if simply set to true, move it to the next group from the currently active one
        elif target and group + 1 < self._window.num_groups():
            self._window.set_view_index(
                view, group + 1, len(self._window.views_in_group(group + 1))
            )
            self._window.focus_view(oldview)
            self._window.focus_view(view)

        # begin refreshing attached view
        self.update_view_loop()

    def on_backspace(self):
        """Handle backspace key behavior inside REPL input."""
        if self.delta < 0:
            self._view.run_command("left_delete")

    def on_ctrl_backspace(self):
        """Handle ctrl+backspace behavior inside REPL input."""
        if self.delta < 0:
            self._view.run_command("delete_word", {"forward": False, "sub_words": True})

    def on_left(self):
        """Move cursor left while keeping output area protected."""
        if self.delta != 0:
            self._window.run_command(
                "move", {"by": "characters", "forward": False, "extend": False}
            )

    def on_shift_left(self):
        """Extend selection left while protecting output area."""
        if self.delta != 0:
            self._window.run_command(
                "move", {"by": "characters", "forward": False, "extend": True}
            )

    def on_home(self):
        """Handle home key behavior in REPL input line."""
        if self.delta > 0:
            self._window.run_command("move_to", {"to": "bol", "extend": False})
        else:
            for i in range(abs(self.delta)):
                self._window.run_command(
                    "move", {"by": "characters", "forward": False, "extend": False}
                )

    def on_shift_home(self):
        """Handle shift+home behavior in REPL input line."""
        if self.delta > 0:
            self._window.run_command("move_to", {"to": "bol", "extend": True})
        else:
            for i in range(abs(self.delta)):
                self._window.run_command(
                    "move", {"by": "characters", "forward": False, "extend": True}
                )

    def on_selection_modified(self):
        """Toggle read-only mode depending on selection position."""
        self._view.set_read_only(self.delta > 0)

    def on_close(self):
        """Close the backend REPL and execute close callbacks."""
        self.repl.close()
        for fun in self.call_on_close:
            fun(self)

    def clear(self, edit):
        """Clear current REPL content while preserving prompt behavior.

        Args:
            edit: Sublime edit token.
        """
        self.escape(edit)
        self._view.erase(edit, self.output_region)
        self._output_end = self._view.sel()[0].begin()

    def escape(self, edit):
        """Reset current user input section.

        Args:
            edit: Sublime edit token.
        """
        self._view.set_read_only(False)
        self._view.erase(edit, self.input_region)
        self._view.show(self.input_region)

    def enter(self, cmd_postfix="\n"):
        """Submit current input to the REPL backend."""
        v = self._view
        if v.sel()[0].begin() != v.size():
            v.sel().clear()
            v.sel().add(sublime.Region(v.size()))

        # l = self._output_end

        self.push_history(self.user_input)  # don't include cmd_postfix in history
        v.run_command("insert", {"characters": cmd_postfix})
        command = self.user_input
        self.adjust_end()
        self.repl.write(command)

    def previous_command(self, edit):
        """Replace input with previous matching history command.

        Args:
            edit: Sublime edit token.
        """
        self._view.set_read_only(False)
        self.ensure_history_match()
        self.replace_current_input(edit, self._history_match.prev_command())
        self._view.show(self.input_region)

    def next_command(self, edit):
        """Replace input with next matching history command.

        Args:
            edit: Sublime edit token.
        """
        self._view.set_read_only(False)
        self.ensure_history_match()
        self.replace_current_input(edit, self._history_match.next_command())
        self._view.show(self.input_region)

    def update_view(self, view):
        """Swap to a new view instance when Sublime recreates views.

        Args:
            view: New Sublime view instance.
        """
        if self._view is not view:
            self._view = view

    def adjust_end(self):
        """Recalculate output boundary after user input changes."""
        # if self.repl.suppress_echo:
        #     v = self._view
        #     vsize = v.size()
        #     self._output_end = min(vsize, self._output_end)
        #     v.run_command("repl_erase_text", {"start": self._output_end, "end": vsize})
        # else:
        self._output_end = self._view.size()

    def write(self, unistr):
        """Write backend output into the REPL view.

        Args:
            unistr: Output text to render.
        """
        # remove color codes
        if self._filter_color_codes:
            unistr = re.sub(r"\033\[\d*(;\d*)?\w", "", unistr)
            unistr = re.sub(r".\x08", "", unistr)

        # string is assumed to be already correctly encoded
        self._view.run_command(
            "repl_insert_text",
            {"pos": self._output_end - self._prompt_size, "text": unistr},
        )
        self._output_end += len(unistr)
        self._view.show(self.input_region)

    # def write_prompt(self, unistr):
    #     """Write prompt text while preserving prompt insertion behavior.

    #     Args:
    #         unistr: Prompt text.
    #     """
    #     self._prompt_size = 0
    #     self.write(unistr)
    #     self._prompt_size = len(unistr)

    def handle_repl_output(self):
        """Process queued output packets.

        Returns:
            bool: ``True`` while backend is active, otherwise ``False``.
        """
        try:
            while True:
                packet = self._repl_reader.queue.get_nowait()
                if packet is None:
                    return False

                self.handle_repl_packet(packet)

        except queue.Empty:
            return True

    def handle_repl_packet(self, packet):
        """Handle one packet from the REPL backend.

        Args:
            packet: Packet payload or plain output string.
        """
        self.write(packet)

    def update_view_loop(self):
        """Refresh REPL output and reschedule loop while backend is alive."""
        is_still_working = self.handle_repl_output()
        if is_still_working:
            sublime.set_timeout(self.update_view_loop, 100)
        else:
            self.write(
                "\n***Repl Killed***\n"
                if self.repl._killed
                else "\n***Repl Closed***\n"
            )
            self._view.set_read_only(True)
            if sublime.load_settings(SETTINGS_FILE).get("view_auto_close"):
                window = self._view.window()
                if window is not None:
                    window.focus_view(self._view)
                    window.run_command("close")

    def push_history(self, command):
        """Push command to history and reset current history match.

        Args:
            command: Command text entered by user.
        """
        self._history.push(command)
        self._history_match = None

    def ensure_history_match(self):
        """Ensure history matcher reflects current user input."""
        user_input = self.user_input
        if (
            self._history_match is not None
            and user_input != self._history_match.current_command()
        ):
            # user did something! reset
            self._history_match = None
        if self._history_match is None:
            self._history_match = self._history.match(user_input)

    def replace_current_input(self, edit, cmd):
        """Replace current input region text.

        Args:
            edit: Sublime edit token.
            cmd: Replacement command text.
        """
        if cmd:
            self._view.replace(edit, self.input_region, cmd)
            self._view.sel().clear()
            self._view.sel().add(sublime.Region(self._view.size()))

    @property
    def view(self):
        """Return the underlying Sublime view."""
        return self._view

    @property
    def input_region(self):
        """Return editable input region in the view."""
        return sublime.Region(self._output_end, self._view.size())

    @property
    def output_region(self):
        """Return output region in the view."""
        return sublime.Region(0, self._output_end - 2)

    @property
    def user_input(self):
        """Return text currently entered by the user."""
        return self._view.substr(self.input_region)

    @property
    def delta(self):
        """Return cursor distance from selection to input start."""
        return self._output_end - self._view.sel()[0].begin()

    def allow_deletion(self):
        """Return whether current selection may delete only user input."""
        # returns true if all selections falls in user input
        # and can be safetly deleted
        output_end = self._output_end
        for sel in self._view.sel():
            if sel.begin() == sel.end() and sel.begin() == output_end:
                # special case, when single selecion
                # is at the very beggining of prompt
                return False
            # i don' really know if end() is always after begin()
            if sel.begin() < output_end or sel.end() < output_end:
                return False
        return True


class ReplManager:
    """Manage REPL instances and their associated views."""

    def __init__(self):
        """Initialize REPL view registry."""
        self.repl_views = {}

    def repl_view(self, view):
        """Return managed ``ReplView`` for a Sublime view.

        Args:
            view: Sublime view object.

        Returns:
            ReplView | None: Matched managed view.
        """
        repl_id = view.settings().get("repl_id")
        if repl_id not in self.repl_views:
            return None
        rv = self.repl_views[repl_id]
        rv.update_view(view)
        return rv

    def open(self, window, **kwds):
        """Open and register a new REPL view.

        Args:
            window: Sublime window where REPL opens.
            **kwds: Backend-specific keyword arguments. An optional
                ``banner`` string may be included; it is not forwarded to
                the subprocess backend and is instead written to the top
                of the REPL view once it is created.

        Returns:
            ReplView | None: Created view wrapper or ``None`` on failure.
        """
        banner = kwds.pop("banner", None)
        try:
            kwds = ReplManager.translate(window, kwds)
            r = SubprocessRepl(**kwds)

            # found = None
            # for view in window.views():
            #     if view.id() == None:  # view_id:
            #         found = view
            #         break
            # view = found or window.new_file()
            view = window.new_file()

            rv = ReplView(view, r, banner=banner)
            rv.call_on_close.append(self._delete_repl)
            self.repl_views[r.id] = rv
            view.set_scratch(True)
            # view.set_name(f"*REPL* [{r.name()}]")
            if "-i" in kwds["cmd"]:
                view.set_name("REPL >>")
            else:
                view.set_name("REPL")
            return rv
        except Exception as e:
            traceback.print_exc()
            sublime.error_message(repr(e))

    def _delete_repl(self, repl_view):
        """Remove a closed REPL view from registry.

        Args:
            repl_view: Managed REPL view wrapper.
        """
        repl_id = repl_view.repl.id
        if repl_id not in self.repl_views:
            return
        del self.repl_views[repl_id]

    @staticmethod
    def translate(window, obj, subst=None):
        """Translate templates recursively in launch structures.

        Args:
            window: Sublime window used for substitution context.
            obj: Object to translate.
            subst: Optional precomputed substitution map.

        Returns:
            Any: Translated object.
        """
        if subst is None:
            subst = ReplManager._subst_for_translate(window)
        if isinstance(obj, dict):
            return ReplManager._translate_dict(window, obj, subst)
        if isinstance(obj, str):
            return ReplManager._translate_string(window, obj, subst)
        if isinstance(obj, list):
            return ReplManager._translate_list(window, obj, subst)
        return obj

    @staticmethod
    def _subst_for_translate(window):
        """Return all available substitutions"""
        res = {
            "packages": sublime.packages_path(),
            "installed_packages": sublime.installed_packages_path(),
        }
        if window.folders():
            res["folder"] = window.folders()[0]
        res["editor"] = "subl -w"
        av = window.active_view()
        if av is None:
            return res
        filename = av.file_name()
        if not filename:
            return res
        filename = os.path.abspath(filename)
        res["file"] = filename
        res["file_path"] = os.path.dirname(filename)
        res["file_basename"] = os.path.basename(filename)
        if "folder" not in res:
            res["folder"] = res["file_path"]

        return res

    @staticmethod
    def _translate_string(window, string, subst=None):
        """Translate one template string.

        Args:
            window: Sublime window used for fallback substitutions.
            string: Input template string.
            subst: Optional substitutions mapping.

        Returns:
            str: Translated string.
        """
        from string import Template

        if subst is None:
            subst = ReplManager._subst_for_translate(window)

        return Template(string).safe_substitute(**subst)

    @staticmethod
    def _translate_list(window, list, subst=None):
        """Translate values in a list recursively.

        Args:
            window: Sublime window used for substitutions.
            list: Input list.
            subst: Optional substitutions mapping.

        Returns:
            list: Translated list.
        """
        if subst is None:
            subst = ReplManager._subst_for_translate(window)
        return [ReplManager.translate(window, x, subst) for x in list]

    @staticmethod
    def _translate_dict(window, dictionary, subst=None):
        """Translate values in a dictionary recursively.

        Args:
            window: Sublime window used for substitutions.
            dictionary: Input dictionary.
            subst: Optional substitutions mapping.

        Returns:
            dict: Translated dictionary.
        """
        if subst is None:
            subst = ReplManager._subst_for_translate(window)
        for k, v in list(dictionary.items()):
            dictionary[k] = ReplManager.translate(window, v, subst)
        return dictionary


manager = ReplManager()

# Window Commands #########################################


# Opens a new REPL
class ReplOpenCommand(sublime_plugin.WindowCommand):
    """Sublime window command that opens a REPL view."""

    def run(self, **kwds):
        """Open a REPL.

        Args:
            **kwds: Backend arguments.
        """
        manager.open(self.window, **kwds)


# REPL Comands ############################################


# Submits the Command to the REPL
class ReplEnterCommand(sublime_plugin.TextCommand):
    """Submit user input to the REPL."""

    def run(self, edit):
        """Run the enter command.

        Args:
            edit: Sublime edit token.
        """
        rv = manager.repl_view(self.view)
        if rv:
            rv.enter()


class ReplClearCommand(sublime_plugin.TextCommand):
    """Clear command for REPL views."""

    def run(self, edit):
        """Clear current REPL output/input.

        Args:
            edit: Sublime edit token.
        """
        rv = manager.repl_view(self.view)
        if rv:
            rv.clear(edit)


# Resets Repl Command Line
class ReplEscapeCommand(sublime_plugin.TextCommand):
    """Reset command line input in REPL views."""

    def run(self, edit):
        """Clear current input only.

        Args:
            edit: Sublime edit token.
        """
        rv = manager.repl_view(self.view)
        if rv:
            rv.escape(edit)


class ReplBackspaceCommand(sublime_plugin.TextCommand):
    """Handle backspace command for REPL views."""

    def run(self, edit):
        """Execute REPL backspace handling.

        Args:
            edit: Sublime edit token.
        """
        rv = manager.repl_view(self.view)
        if rv:
            rv.on_backspace()


class ReplCtrlBackspaceCommand(sublime_plugin.TextCommand):
    """Handle ctrl+backspace command for REPL views."""

    def run(self, edit):
        """Execute REPL ctrl+backspace handling.

        Args:
            edit: Sublime edit token.
        """
        rv = manager.repl_view(self.view)
        if rv:
            rv.on_ctrl_backspace()


class ReplLeftCommand(sublime_plugin.TextCommand):
    """Handle left-arrow command for REPL views."""

    def run(self, edit):
        """Execute REPL left movement handling.

        Args:
            edit: Sublime edit token.
        """
        rv = manager.repl_view(self.view)
        if rv:
            rv.on_left()


class ReplShiftLeftCommand(sublime_plugin.TextCommand):
    """Handle shift+left command for REPL views."""

    def run(self, edit):
        """Execute REPL shift+left handling.

        Args:
            edit: Sublime edit token.
        """
        rv = manager.repl_view(self.view)
        if rv:
            rv.on_shift_left()


class ReplHomeCommand(sublime_plugin.TextCommand):
    """Handle home command for REPL views."""

    def run(self, edit):
        """Execute REPL home-key handling.

        Args:
            edit: Sublime edit token.
        """
        rv = manager.repl_view(self.view)
        if rv:
            rv.on_home()


class ReplShiftHomeCommand(sublime_plugin.TextCommand):
    """Handle shift+home command for REPL views."""

    def run(self, edit):
        """Execute REPL shift+home handling.

        Args:
            edit: Sublime edit token.
        """
        rv = manager.repl_view(self.view)
        if rv:
            rv.on_shift_home()


class ReplViewPreviousCommand(sublime_plugin.TextCommand):
    """Navigate to previous matching REPL history command."""

    def run(self, edit):
        """Execute previous-history command.

        Args:
            edit: Sublime edit token.
        """
        rv = manager.repl_view(self.view)
        if rv:
            rv.previous_command(edit)


class ReplViewNextCommand(sublime_plugin.TextCommand):
    """Navigate to next matching REPL history command."""

    def run(self, edit):
        """Execute next-history command.

        Args:
            edit: Sublime edit token.
        """
        rv = manager.repl_view(self.view)
        if rv:
            rv.next_command(edit)


class ReplKillCommand(sublime_plugin.TextCommand):
    """Kill the active REPL's underlying process."""

    def run(self, edit):
        """Execute the kill command on the current REPL view.

        Args:
            edit: Sublime edit token.
        """
        rv = manager.repl_view(self.view)
        if rv:
            rv.repl.kill()

    def is_visible(self):
        """Return whether this view is a REPL view, for menu display."""
        rv = manager.repl_view(self.view)
        return bool(rv)

    def is_enabled(self):
        """Return whether the kill command can currently run."""
        return self.is_visible()


class SublimeReplListener(sublime_plugin.EventListener):
    """Event listener that enforces REPL view interaction rules."""

    def on_selection_modified(self, view):
        """Handle selection changes in REPL views.

        Args:
            view: Sublime view where selection changed.
        """
        rv = manager.repl_view(view)
        if rv:
            rv.on_selection_modified()

    def on_close(self, view):
        """Handle REPL view close events.

        Args:
            view: Closed Sublime view.
        """
        rv = manager.repl_view(view)
        if rv:
            rv.on_close()

    def on_text_command(self, view, command_name, args):
        """Intercept text commands to protect REPL output regions.

        Args:
            view: Sublime view receiving command.
            command_name: Sublime command name.
            args: Command argument dictionary.

        Returns:
            tuple | None: Replacement command pair or ``None``.
        """
        args = args or {}
        rv = manager.repl_view(view)
        if not rv:
            return None

        # stop backspace on ST3 w/o breaking brackets
        if command_name == "left_delete" and not rv.allow_deletion():
            return "repl_pass", {}

        # stop ctrl+backspace on ST3 w/o breaking brackets
        if (
            command_name == "delete_word"
            and not args.get("forward")
            and not rv.allow_deletion()
        ):
            return "repl_pass", {}

        return None


class RunPythonReplCommand(sublime_plugin.TextCommand):
    """Run current Python file or interactive session in SublimeREPL-py."""

    def run(self, edit, interactive=False):
        """Run Python REPL command from the current view context.

        Args:
            edit: Sublime edit token.
            interactive: Whether to open interactive mode.
            name: External REPL identifier shown in tab naming.
        """
        window = self.view.window()
        for view in window.views():
            if view.is_dirty() and view.file_name():
                view.run_command("save")

        file_name = self.view.file_name()
        settings = sublime.load_settings(SETTINGS_FILE)
        python_path, source = self.resolve_python(settings, file_name)

        # Command list passed to subprocess backend.
        if not interactive and file_name:
            cmd_list = [python_path, "-u", file_name]
        else:
            cmd_list = [python_path, "-u", "-i"]

        # Working directory for REPL process
        cwd = os.path.dirname(file_name) if file_name else os.path.expanduser("~")

        banner = f"[Python interpreter ({source}): {python_path}]\n\n"

        self.view.window().run_command(
            "repl_open",
            {
                "cmd": cmd_list,
                "cwd": cwd,
                "banner": banner,
            },
        )

    def resolve_python(self, settings, file_name):
        """Resolve the Python interpreter to launch, honoring user overrides.

        The ``python_venv_path`` setting, when it points to an existing,
        executable file, takes precedence over the automatic upward search
        for a ``.venv/bin/python`` performed by :meth:`get_venv_python`.

        Args:
            settings: Loaded SublimeREPL-py settings object.
            file_name: Current file path, used for the automatic search.

        Returns:
            tuple[str, str]: Resolved python executable path, and a short
            string describing where it came from ("python_venv_path" or
            "auto-detected").
        """
        configured_path = settings.get("python_venv_path")
        if configured_path:
            # If the user has set a custom python_venv_path, we need to check if it
            # exists and is executable.
            configured_path = os.path.join(
                os.path.expanduser(str(configured_path)),
                "bin",
                "python",
            )
            if os.path.isfile(configured_path) and os.access(configured_path, os.X_OK):
                return configured_path, "python_venv_path setting"
            sublime.error_message(
                f"SublimeREPL-py: 'python_venv_path' is set to '{configured_path}' but that "
                "file does not exist or is not executable.\nFalling back to "
                "auto-detected interpreter."
            )

        return self.get_venv_python(file_name), "auto-detected"

    def get_venv_python(self, start_path):
        """Find nearest ``.venv/bin/python`` searching upward.

        Args:
            start_path: Starting file path for upward directory walk.

        Returns:
            str: Executable Python path.
        """
        fallback_python = sys.executable or "python3"
        if not start_path:
            return fallback_python

        dir_path = os.path.dirname(start_path)

        while True:
            candidate = os.path.join(dir_path, ".venv", "bin", "python")
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
            parent = os.path.dirname(dir_path)
            if parent == dir_path:
                break
            dir_path = parent

        return fallback_python
