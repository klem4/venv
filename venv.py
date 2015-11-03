# coding: utf-8

import sys
import subprocess
import glob
import inspect
import difflib
import os
import shutil
import re


class VirtualenvKeeper(object):
    def __init__(self, base_dir, project, release,
                 venv, save_venv_dir,
                 make_cmd="gmake", make_venv_cmd="virtualenv",
                 rpatterns='requires*,requirements*'):
        """
        :param base_dir: base directory, like '/data'
        :param project:  project name, like 'yast'
        :param release:  release name, like 'yast-1.5.1-03'
        :param venv: virtualenv folder name, like 'virtualenv'
        :param save_venv_dir: directory for saving virtualenv folder
        for reusing in future
        """
        self.base_dir = base_dir
        self.project = project
        self.release = release
        self.venv = venv
        self.save_venv_dir = save_venv_dir
        self.rpatterns = rpatterns

        self.make_cmd = make_cmd
        self.make_venv_cmd = make_venv_cmd
        self.log("initialized with %s" % self.__dict__)

    @property
    def full_saved_venv_path(self):
        return os.path.join(
            self.save_venv_dir,
            '{venv}_{project}'.format(venv=self.venv, project=self.project)
        )

    @property
    def full_new_virtualenv_path(self):
        return os.path.join(
            self.base_dir,
            self.release,
            self.venv
        )

    @property
    def rpatterns_list(self):
        return self.rpatterns.split(',')

    def get_pip_version(self, venv_path):
        cmd = subprocess.Popen([
            self._get_pip(venv_path), "--version"],
            stdout=subprocess.PIPE
        )
        out, err = cmd.communicate()
        data = out.decode()
        m = re.search(r'pip ([\d\.]+) from', data, re.I)
        return tuple(map(int, m.groups()[0].split('.')))

    def ensure(self):
        """ if possible, copying  previously save venv or
        creating new venv and install all needed requires
        """

        if self._saved_venv_exists() and self._venv_not_changed():
            self._copy_venv()
        else:
            self._remove_actual_venv()
            return_code = self._create_new_venv()

            self._remove_saved_venv()
            if not return_code:
                self._save()
            else:
                # error happened while creating new virtualenv
                self.log(
                    "ensure: _create_new_venv return %s! removing actual venv" %
                    return_code
                )
                self._remove_actual_venv()

    def _get_python(self, path=None):
        if not path:
            path = self.full_new_virtualenv_path
        return os.path.join(path, "bin/python")

    def _get_pip(self, path=None):
        if not path:
            path = self.full_new_virtualenv_path
        return os.path.join(path, "bin/pip")

    def _saved_venv_exists(self):
        path = self.full_saved_venv_path
        result = os.path.exists(path)
        self.log("on '%s': %s" % (path, result))
        return result

    def _remove_saved_venv(self):
        path = self.full_saved_venv_path
        self.log("from %s" % path)
        shutil.rmtree(path, ignore_errors=True)

    def _remove_actual_venv(self):
        self.log("from '%s'" % self.full_new_virtualenv_path)
        shutil.rmtree(self.full_new_virtualenv_path, ignore_errors=True)

    def _save_venv(self):
        """ saving created venv to 'save-folder' for future possible reusing
        """
        self.log("copy from '%s' to '%s'" % (
            self.full_new_virtualenv_path, self.full_saved_venv_path))

        shutil.copytree(
            self.full_new_virtualenv_path,
            self.full_saved_venv_path,
            symlinks=True,
            ignore=shutil.ignore_patterns('*.pyc')
        )

    def _collect_rfiles(self, from_path=''):
        files = []
        for pattern in self.rpatterns_list:
            full_path = os.path.join(from_path, pattern)
            self.log("from %s" % full_path)
            files.extend(glob.glob(full_path))

        return files

    def _save_rfiles(self):
        """ saving requires* and requirements* files for future diff
        """
        files = self._collect_rfiles()
        self.log("collected rfile: %s" % files)

        dest = self.full_saved_venv_path
        for f in files:
            self.log("copying rfile '%s' to '%s" % (f, dest))
            shutil.copy(f, dest)

    def _get_outdated_pkgs(self, venv_path=None):

        args = [
            self._get_pip(venv_path),
            "list", "-o", "-l",
            "-f", "http://y.rutube.ru/vrepo/dist/",
            "--no-index"
        ]

        if self.get_pip_version(venv_path) >= (1, 5, 0):
            args.append("--process-dependency-links")

        pip_cmd = subprocess.Popen(args, stdout=subprocess.PIPE)

        out, err = pip_cmd.communicate()

        data = out.decode().split("\n")

        rx = r'^([^\s]+ \(Current:.+? Latest:.+?)\)$'
        result = []
        for line in data:
            if re.search(rx, line):
                result.append(line.strip("\n"))
        return sorted(result)

    def _save_outdated(self):
        data = self._get_outdated_pkgs()
        path = os.path.join(self.full_saved_venv_path, 'outdated.txt')

        self.log("collected: %s" % data)
        self.log("saving to %s" % path)

        with open(path, 'w') as f:
            f.write("\n".join(data))
        f.close()

    def _save(self):
        self._save_venv()
        self._save_rfiles()
        self._save_outdated()

    def _copy_venv(self):
        self.log(
            "will copy from '%s' to '%s'" % (
                self.full_saved_venv_path, self.full_new_virtualenv_path)
        )

        self.log(
            "removing current venv if exists "
            "from %s" % self.full_new_virtualenv_path
        )

        shutil.rmtree(self.full_new_virtualenv_path, ignore_errors=True)

        self.log("copying from '%s' to '%s'" % (
            self.full_saved_venv_path, self.full_new_virtualenv_path))

        shutil.copytree(
            self.full_saved_venv_path,
            self.full_new_virtualenv_path,
            symlinks=True,
            ignore=shutil.ignore_patterns('*.pyc')
        )

    def _venv_not_changed(self):
        result = not (self._requires_has_diff() or self._packages_updated())
        self.log("%s" % result)
        return result

    def _requires_has_diff(self):
        result = False

        old_files = self._collect_rfiles(from_path=self.full_saved_venv_path)
        new_files = self._collect_rfiles()

        self.log("getting diff between %s and %s" % (old_files, new_files))

        diff_files_map = dict(
            map(lambda f: (os.path.basename(f), f), old_files)
        )

        if set(diff_files_map.keys()) != set(new_files):
            self.log("some rfiles was added or removed")
            result = True
        else:
            self.log("diff map: %s" % diff_files_map)
            for new, old in diff_files_map.items():
                diff = difflib.unified_diff(
                    open(new).readlines(),
                    open(old).readlines()
                )

                diff = ''.join(list(diff))
                self.log("diff '%s' vs '%s' = '%s'" % (new, old, diff))
                if diff:
                    result = True
                    break

        self.log(result)
        return result

    def _packages_updated(self):
        now_outdated = self._get_outdated_pkgs(self.full_saved_venv_path)
        path = os.path.join(self.full_saved_venv_path, 'outdated.txt')

        with open(path, 'r') as f:
            prev_outdated = map(lambda line: line.strip(), f.readlines())

        diff = set(prev_outdated) ^ set(now_outdated)
        self.log("diff=%s" % diff)

        result = bool(diff)
        self.log(result)

        return result

    def _create_new_venv(self):
        self.log(
            "creating new virtualenv on %s" % self.full_new_virtualenv_path)
        retval = subprocess.call(
            [
                self.make_cmd,
                "-e", "VENV_DIR=%s" % self.full_new_virtualenv_path,
                self.make_venv_cmd,
                "requirements",
                "relocatable"
            ]
        )

        return retval

    @classmethod
    def log(cls, msg):
        from_function = inspect.getouterframes(inspect.currentframe())[1][3]
        print("[VirtualenvKeeper.%s] %s" % (from_function, msg))


if __name__ == '__main__':
    vk = VirtualenvKeeper(*sys.argv[1:])
    vk.ensure()
    vk.log("done ^_^")
