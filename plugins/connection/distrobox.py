# Copyright (c) 2024 Aleksandar Radivojevic (@sandorex)
# License: GPL v3.0+
#
# Based on podman connection plugin written by Tomas Tomecek (@TomasTomecek)
# https://github.com/containers/ansible-podman-collections/blob/efbfba7c3c4ed95bb75fcabfced61f650b28bac8/plugins/connection/podman.py

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

DOCUMENTATION = '''
    author: Aleksandar Radivojevic (@sandorex)
    name: distrobox
    short_description: Execute plays on distrobox containers as hosts
    description:
        - Run commands or push/fetch files to existing distrobox container.
    options:
      remote_addr:
        description:
          - The name of the container you want to access.
        default: inventory_hostname
        vars:
          - name: ansible_host
          - name: inventory_hostname
          - name: ansible_distrobox_host
      remote_user:
        description:
            - User specified via name which is used to execute commands inside the container.
        ini:
          - section: defaults
            key: remote_user
        env:
          - name: ANSIBLE_REMOTE_USER
        vars:
          - name: ansible_user
      podman_executable:
        description:
          - Executable for podman command.
        default: podman
        vars:
          - name: ansible_podman_executable
        env:
          - name: ANSIBLE_PODMAN_EXECUTABLE
      distrobox_executable:
        description:
          - Executable for podman command.
        default: distrobox
        vars:
          - name: ansible_distrobox_executable
        env:
          - name: ANSIBLE_DISTROBOX_EXECUTABLE
'''

import subprocess

from ansible.module_utils.common.process import get_bin_path
from ansible.errors import AnsibleError
from ansible.module_utils._text import to_bytes
from ansible.plugins.connection import ConnectionBase, ensure_connect
from ansible.utils.display import Display

display = Display()

# this _has to be_ named Connection
class Connection(ConnectionBase):
    """Connection plugin that works on a distrobox container"""

    transport = 'sandorex.distrobox.distrobox'

    has_pipelining = False

    def __init__(self, play_context, new_stdin, *args, **kwargs):
        super(Connection, self).__init__(play_context, new_stdin, *args, **kwargs)

        self._container_id = self._play_context.remote_addr
        self.user = self._play_context.remote_user
        self._connected = False
        display.vvvv("Using distrobox connection")

    # TODO make it generic so any container manager can work
    def _podman(self, subcommand: str, args=None, in_data=None):
        """
        run podman executable

        :param cmd: podman's command to execute (str or list)
        :param cmd_args: list of arguments to pass to the command (list of str/bytes)
        :param in_data: data passed to podman's stdin
        :return: return code, stdout, stderr
        """

        podman_exec = self.get_option('podman_executable')

        try:
            podman_cmd = get_bin_path(podman_exec)
        except ValueError:
            raise AnsibleError("%s command not found in PATH" % podman_exec)

        if not podman_cmd:
            raise AnsibleError("%s command not found in PATH" % podman_exec)

        cmd = [podman_cmd, subcommand]

        if args:
            cmd += args

        cmd = [to_bytes(i, errors='surrogate_or_strict') for i in cmd]

        display.vvv("RUN %s" % (cmd,), host=self._container_id)
        p = subprocess.Popen(cmd, shell=False, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        stdout, stderr = p.communicate(input=in_data)
        display.vvvvv("STDOUT %s" % stdout)
        display.vvvvv("STDERR %s" % stderr)
        display.vvvvv("RC CODE %s" % p.returncode)
        stdout = to_bytes(stdout, errors='surrogate_or_strict')
        stderr = to_bytes(stderr, errors='surrogate_or_strict')
        return p.returncode, stdout, stderr

    def _distrobox(self, subcommand, args=[], in_data=None):
        """Runs distrobox command"""
        distrobox_exec = self.get_option('distrobox_executable')

        try:
            distrobox_cmd = get_bin_path(distrobox_exec)
        except ValueError:
            raise AnsibleError("%s command not found in PATH" % distrobox_exec)

        if not distrobox_cmd:
            raise AnsibleError("%s command not found in PATH" % distrobox_exec)

        cmd = [distrobox_cmd, subcommand] + args
        cmd = [to_bytes(i, errors='surrogate_or_trict') for i in cmd]

        display.vvv("RUN %s" % (cmd,), host=self._container_id)
        p = subprocess.Popen(cmd, shell=False, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        stdout, stderr = p.communicate(input=in_data)
        display.vvvvv("STDOUT %s" % stdout)
        display.vvvvv("STDERR %s" % stderr)
        display.vvvvv("RC CODE %s" % p.returncode)
        stdout = to_bytes(stdout, errors='surrogate_or_strict')
        stderr = to_bytes(stderr, errors='surrogate_or_strict')
        return p.returncode, stdout, stderr

    def _distrobox_exec(self, cmd, distrobox_args=[]):
        """Runs distrobox-enter to execute command inside a container with proper distrobox env"""
        # NOTE: i added bash -l -c as the su parses the arguments weirdly
        args = [
            '-a',
            '--user=' + (self.user if self.user else 'root'),
            '--name', self._container_id
        ] + distrobox_args + ['--', 'bash', '-l', '-c', cmd]

        return self._distrobox('enter', args)

    @ensure_connect
    def exec_command(self, cmd, in_data=None, sudoable=True):
        super(Connection, self).exec_command(cmd, in_data=in_data, sudoable=sudoable)

        rc, stdout, stderr = self._distrobox_exec(
            cmd,
            distrobox_args=['-a', '--user=' + (self.user if self.user else 'root')])

        display.vvvvv("STDOUT %r STDERR %r" % (stderr, stderr))
        return rc, stdout, stderr

    def put_file(self, in_path, out_path):
        """ Place a local file located in 'in_path' inside container at 'out_path' """
        super(Connection, self).put_file(in_path, out_path)
        display.vvv("PUT %s TO %s" % (in_path, out_path), host=self._container_id)

        rc, stdout, stderr = self._podman('cp', [in_path, '%s:%s' % (self._container_id, out_path)])
        if rc != 0:
            raise AnsibleError(
                "Failed to copy file from %s to %s in container %s\n%s" % (
                    in_path, out_path, self._container_id, stderr)
            )

        # if running as user chown the file
        if self.user:
            rc, stdout, stderr = self._podman("exec", [self._container_id, "chown", self.user, out_path])
            if rc != 0:
                raise AnsibleError(
                    "Failed to chown file %s for user %s in container %s\n%s" % (
                        out_path, self.user, self._container_id, stderr)
                )

    def fetch_file(self, in_path, out_path):
        """ obtain file specified via 'in_path' from the container and place it at 'out_path' """
        super(Connection, self).fetch_file(in_path, out_path)
        display.vvv("FETCH %s TO %s" % (in_path, out_path), host=self._container_id)

        rc, stdout, stderr = self._podman('cp', ['%s:%s' % (self._container_id, in_path), out_path])

        if rc != 0:
            raise AnsibleError("Failed to fetch file from %s to %s from container %s\n%s" % (
                in_path, out_path, self._container_id, stderr))

    def _connect(self):
        """
        there is no literal connection
        """
        super(Connection, self)._connect()
        self._connected = True

    def close(self):
        """ unmount container's filesystem """
        super(Connection, self).close()
        self._connected = False

