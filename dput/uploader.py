# -*- coding: utf-8 -*-
# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4

# Copyright (c) 2012 dput authors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.	See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301, USA.

import re
import os
import abc
import sys
from contextlib import contextmanager

import dput.conf
from dput.conf import Opt
from dput.core import logger
from dput.overrides import (make_delayed_upload, force_passive_ftp_upload)
from dput.checker import run_checker
from dput.util import (load_obj, load_config, run_command)
from dput.exceptions import (NoSuchConfigError, DputConfigurationError,
                             DputError, UploadException)


class AbstractUploader(object):
    __metaclass__ = abc.ABCMeta

    def __init__(self, config, profile):
        self._config = config
        self._profile = profile

    def _pre_hook(self):
        self._run_hook("pre_upload_command")

    def _post_hook(self):
        self._run_hook("post_upload_command")

    def _run_hook(self, hook):
        if hook in self._config and self._config[hook] != "":
            (output, stderr, ret) = run_command(self._config[hook])
            sys.stdout.write(output)  # XXX: Fixme
            if ret != 0:
                raise DputError(
                    "Command `%s' returned an error: %s [err=%d]" % (
                        self._config[hook],
                        stderr,
                        ret
                    )
                )

    def upload_write_error(self, e):
        logger.warning("""Upload permissions error

You either don't have the rights to upload a file, or, if this is on
ftp-master, you may have tried to overwrite a file already on the server.

Continuing anyway in case you want to recover from an incomplete upload.
No file was uploaded, however.""")

    @abc.abstractmethod
    def initialize(self, **kwargs):
        pass

    @abc.abstractmethod
    def upload_file(self, filename):
        pass

    @abc.abstractmethod
    def shutdown(self):
        pass


def get_uploader(uploader_method):
    # XXX: return (defn, obj), so we can use the stored .json file for more.
    # XXX: refactor this and dput.checker.get_checker
    logger.debug("Attempting to resolve %s" % (uploader_method))
    try:
        config = load_config('uploaders', uploader_method)
    except NoSuchConfigError:
        logger.debug("failed to resolve %s" % (uploader_method))
        return None
    path = config['plugin']
    logger.debug("loading %s" % (path))
    try:
        return load_obj(path)
    except ImportError:
        logger.debug("failed to resolve %s" % (path))
        return None


@contextmanager
def uploader(uploader_method, config, profile):
    """
    Rent-a-uploader :)
    """
    klass = get_uploader(uploader_method)

    if not klass:
        logger.error(
            "Failed to resolve method %s to an uploader class" % (
                uploader_method
            )
        )
        raise DputConfigurationError(
            "Failed to resolve method %s to an uploader class" % (
                uploader_method
            )
        )

    obj = klass(config, profile)
    obj.initialize()
    obj._pre_hook()
    try:
        yield obj
    finally:
        obj._post_hook()
        obj.shutdown()


class BadDistributionError(UploadException):
    pass


def invoke_dput(changes, args):  # XXX: Name sucks, used under a different name
#                                        elsewhere, try again.

    conf = dput.conf.load_dput_configs(args.host)
    profile = dput.util.load_config(
        'profiles',
        conf.name()
    )

    fqdn = conf[Opt.KEY_FQDN]
    host = args.host

    # XXX: Correct --force behavior
    logfile = changes.get_changes_file()  # XXX: Check for existing one
    xtn = ".changes"
    if logfile.endswith(xtn):
        logfile = "%s.%s.upload" % (logfile[:-len(xtn)], host)
    else:
        # XXX: WTF WTF WTF WTF SHIT
        raise UploadException("The christ is a %s file???" % (
            logfile
        ))

    # XXX: This function is huge, let's break this up!

    suite = changes['Distribution']
    srgx = conf['allowed_distributions']
    if re.match(srgx, suite) is None:
        raise BadDistributionError("'%s' doesn't match '%s'" % (
            suite,
            srgx
        ))

    if args.simulate:
        logger.warning("Not uploading for real - dry run")

    if args.delayed:
        make_delayed_upload(conf, args.delayed)

    if args.passive:
        force_passive_ftp_upload(conf)

    if 'checkers' in profile:
        for checker in profile['checkers']:
            logger.info("Running checker %s" % (checker))
            run_checker(checker, changes, conf, profile)
    else:
        logger.debug(profile)
        logger.warning("No checkers defined in the profile. "
                       "Not checking upload.")

    logger.info("Uploading %s to %s (%s)" % (
        changes.get_filename(),
         fqdn,
         conf[Opt.KEY_INCOMING]
    ))

    with open(logfile, 'w') as log:
        logger.debug("Writing log to %s" % (logfile))
        with uploader(conf[Opt.KEY_METHOD], conf, profile) as obj:
            for path in changes.get_files() + [changes.get_changes_file(), ]:
                logger.info("Uploading %s => %s" % (
                    os.path.basename(path),
                    conf.name()
                ))
                if not args.simulate:
                    obj.upload_file(path)
                log.write(
                    "Successfully uploaded %s to %s for %s.\n" % (
                        os.path.basename(path),
                        conf[Opt.KEY_FQDN] or host,
                        host
                    )
                )
