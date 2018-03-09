#!/usr/bin/python
# vim: sw=4 et

# Copyright (c) 2016 SUSE LINUX GmbH, Nuernberg, Germany.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

from __future__ import print_function

import argparse
import glob
import os
import platform
import shutil
import sys
import tempfile
import time

import pymod2pkg

import sh
from sh import Command


def pymodule2pkg(spectemplate):
    specname = os.path.splitext(spectemplate)[0]
    modulename = os.path.splitext(os.path.basename(specname))[0]
    pkgname = pymod2pkg.module2package(modulename,
                                       platform.linux_distribution()[0])
    if modulename == 'openstack-macros':
        pkgname = modulename

    return pkgname


def get_osc_user():
    import osc.conf
    osc.conf.get_config()
    return osc.conf.get_apiurl_usr(osc.conf.config['apiurl'])


def upload_meta(project, build_repository, linkproject):
    projectlink = ''
    if linkproject:
        projectlink = '<link project="%s"/>\n' % linkproject

    description = ''
    if linkproject:
        if 'ZUUL_UUID' in os.environ:
            description = """
This project tests the following Zuul Change IDs: %(ZUUL_CHANGE_IDS)s\n
Branch used: %(ZUUL_BRANCH)s\n
Project used: %(ZUUL_PROJECT)s
""" % (os.environ)
    templ = """
<project name="%(project)s">
  <title>Autogenerated CI project</title>
  <description>
%(description)s
  </description>
%(projectlink)s
  <person userid="%(user)s" role="maintainer"/>
  <publish>
    <disable/>
  </publish>
%(build_repository)s
</project>""" % ({'project': project,
                  'user': get_osc_user(),
                  'description': description,
                  'projectlink': projectlink,
                  'build_repository': build_repository})

    with tempfile.NamedTemporaryFile() as meta:
        meta.write(templ)
        meta.flush()
        print('Updating meta for ', project)

        # work around build service bug that triggers a database deadlock
        for fail_counter in range(1, 5):
            try:
                sh.osc('api', '-T', meta.name, '/source/%s/_meta' % project)
                break
            except sh.ErrorReturnCode_1:
                # Sleep a bit and try again. This has not been scientifically
                # proven to be the correct sleep factor, but it seems to work
                time.sleep(2)
                continue


def upload_meta_enable_repository(project, linkproject):
    repository = """
  <repository name="standard" %(repoflags)s>
    <path project="%(linkproject)s" repository="standard"/>
    <arch>x86_64</arch>
  </repository>
""" % ({'linkproject': linkproject,
        'repoflags': 'rebuild="direct" block="local" linkedbuild="localdep"'})

    upload_meta(project, repository, linkproject)


def freeze_project(project):
    """Generate a _frozenlink file for the project"""
    result = sh.osc('api', '-X', 'POST', '/source/%s?cmd=freezelink' % project)
    if '<status code="ok" />' not in result:
        print('WARNING: freeze the project fails: %s' % result)


def create_new_build_project(workdir, project, linkproject):
    sh.mkdir('-p', workdir)
    olddir = os.getcwd()
    try:
        os.chdir(workdir)
        if linkproject:
            upload_meta_enable_repository(project, linkproject)
            freeze_project(project)
        sh.osc('init', project)
    finally:
        os.chdir(olddir)


def generate_pkgspec(pkgoutdir, global_requirements, spectemplate, pkgname):

    obsservicedir = '/usr/lib/obs/service/'
    outdir = ('--outdir', pkgoutdir)

    olddir = os.getcwd()
    try:
        os.chdir(pkgoutdir)
        renderspec = Command(os.path.join(obsservicedir, 'renderspec'))

        renderspec(
            '--input-template', os.path.join(olddir, spectemplate),
            '--requirements', os.path.join(olddir, global_requirements),
            '--output-name', pkgname + '.spec', *outdir)

        format_spec_file = Command(
            os.path.join(obsservicedir, 'format_spec_file'))
        format_spec_file(*outdir)

        # configure a download cache to avoid downloading the same files
        download_env = os.environ.copy()
        download_env["CACHEDIRECTORY"] = os.path.join(
            os.path.expanduser("~"), ".cache", "download_files")

        download_files = Command(os.path.join(obsservicedir, 'download_files'))
        download_files(_env=download_env, *outdir)
    finally:
        os.chdir(olddir)


def osc_mkpac(workdir, packagename):
    olddir = os.getcwd()
    try:
        os.chdir(workdir)
        sh.osc('mkpac', packagename)
    finally:
        os.chdir(olddir)


def spec_is_modified(pkgoutdir, project, pkgname):
    specname = pkgname + ".spec"
    cached_spec = os.path.join(pkgoutdir, '.osc', specname)
    cleanup = False
    if not os.path.exists(cached_spec):
        cleanup = True
        sh.osc('api', '/source/%s/%s/%s.spec' % (
            project, pkgname, pkgname), _out=cached_spec)
    r = sh.cmp(
        '-s', os.path.join(pkgoutdir, specname), cached_spec, _ok_code=[0, 1])
    if cleanup:
        os.remove(cached_spec)
    return r.exit_code == 1


def osc_detachbranch(workdir, project, pkgname):
    olddir = os.getcwd()
    try:
        os.chdir(os.path.join(workdir))
        sh.osc('detachbranch', project, pkgname)
        os.mkdir(pkgname + '.b')
        for f in glob.glob(os.path.join(pkgname, '*')):
            os.rename(f, os.path.join(pkgname + '.b', os.path.basename(f)))
        sh.rm('-rf', pkgname)
        sh.osc('co', pkgname)
        for f in glob.glob(os.path.join(pkgname + '.b', '*')):
            dst = os.path.basename(f)
            try:
                os.unlink(os.path.join(pkgname, dst))
            except OSError:
                pass
            os.rename(f, os.path.join(pkgname, dst))
        os.rmdir(pkgname + '.b')
    finally:
        os.chdir(olddir)


def osc_commit_all(workdir, packagename):
    olddir = os.getcwd()
    try:
        os.chdir(os.path.join(workdir, packagename))
        sh.osc('addremove')
        sh.osc('commit', '--noservice', '-n')
    finally:
        os.chdir(olddir)


def copy_extra_sources(specdir, pkgoutdir):
    for f in glob.glob(os.path.join(specdir, '*')):
        if f.endswith(".j2"):
            continue
        shutil.copy2(f, pkgoutdir)


def create_project(worktree, project, linkproject):
    workdir = os.path.join(os.getcwd(), 'out')
    sh.rm('-rf', workdir)
    create_new_build_project(workdir, project, linkproject)
    try:
        existing_pkgs = [x.strip() for x in
                         sh.osc('ls', '-e', project, _iter=True)]
    except:
        existing_pkgs = []

    alive_pkgs = set()
    worktree_pattern = os.path.join(worktree, 'openstack', '*', '*.spec.j2')

    for spectemplate in sorted(glob.glob(worktree_pattern)):
        pkgname = pymodule2pkg(spectemplate)
        alive_pkgs.add(pkgname)
        print(pkgname)
        sys.stdout.flush()

        pkgoutdir = os.path.join(workdir, pkgname)
        osc_mkpac(workdir, pkgname)
        copy_extra_sources(os.path.dirname(spectemplate), pkgoutdir)
        generate_pkgspec(
            pkgoutdir,
            os.path.join(worktree, 'global-requirements.txt'),
            spectemplate, pkgname)

        if pkgname in existing_pkgs:
            if spec_is_modified(pkgoutdir, project, pkgname):
                osc_detachbranch(workdir, project, pkgname)

                print("Committing update to %s" % pkgname)
                osc_commit_all(workdir, pkgname)
        else:
            print("Adding new pkg %s" % pkgname)
            osc_commit_all(workdir, pkgname)

    # remove no longer alive pkgs
    for i in existing_pkgs:
        if not linkproject and i not in alive_pkgs:
            print("Removing outdated ", i)
            sh.osc('rdelete', '-m', 'x', project, i)


def main():
    parser = argparse.ArgumentParser(
        description='Build a testproject for a given rpm-packaging checkout')
    parser.add_argument('worktree',
                        help='directory with a rpm-packaging checkout')
    parser.add_argument('project',
                        help='name of the destination buildservice project')
    parser.add_argument('--linkproject',
                        help='create project link to given project')

    args = parser.parse_args()

    sh.ErrorReturnCode.truncate_cap = 9000
    create_project(args.worktree, args.project, args.linkproject)


if __name__ == '__main__':
    main()
