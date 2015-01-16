# -*- coding: utf-8 -*-

# print(..., file=...) in python 2
from __future__ import print_function

import git
import os
import re
import sys
import shutil
import subprocess


class TargetConfig(object):
    '''Config for a branch/tag target'''
    def __init__(self, path, filters=None, eventCmd=None):
        self.path = path
        if filters is None:
            filters = [r'.*']
        self.filters = [re.compile(filter) for filter in filters]
        self.eventCmd = eventCmd

    def emit(self, refType, event, name, desc=None):
        '''Call eventCmd'''
        if self.eventCmd is None:
            return
        args = [self.eventCmd, refType, event, name]
        if desc is not None:
            args.append(desc)
        subprocess.call(args)
        # TODO: check return code

    def is_valid_name(self, name):
        '''Does name match the branch/tag refNames and is a valid filename?'''
        # apply all filters and check for some nasty characters in a filename
        if not any([filter.match(name) for filter in self.filters]):
            return False
        match = re.search(r'^\.|.*[/$\0\n\t]', name)
        if match is not None:
            print('name \'{0}\' is not allowed (contains \'{1}\')'
                  .format(name, match.group(0)), file=sys.stderr)
            return False
        return True


class Repo(git.Repo):
    '''Extends git.Repo by a sync method that tracks changes when fetching.'''

    def __init__(self, *args, **kwargs):
        git.Repo.__init__(self, *args, **kwargs)

    @staticmethod
    def get_diff(before, after):
        '''Compute a diff of branches/tags.'''
        beforeSet = set(before.keys())
        afterSet = set(after.keys())

        removed = beforeSet.difference(afterSet)
        added = afterSet.difference(beforeSet)
        modified = {name for name in beforeSet.intersection(afterSet)
                    if before[name] != after[name]}
        unmodified = beforeSet.intersection(afterSet).difference(modified)
        return {
            'removed': list(removed),
            'added': list(added),
            'modified': list(modified),
            'unmodified': list(unmodified)
            }

    def sync(self):
        '''Calls fetch() and returns a diff of branches and tags'''
        # remember commit shas of branches and tags
        branchesBefore = {branch.name: branch.commit.hexsha
                          for branch in self.branches}
        tagsBefore = {tag.name: tag.commit.hexsha for tag in self.tags}

        # fetch all remotes
        self.git.fetch('--all', '-f', '-p')

        # get commit shas after update
        branchesAfter = {branch.name: branch.commit.hexsha
                         for branch in self.branches}
        tagsAfter = {tag.name: tag.commit.hexsha for tag in self.tags}

        # compute diffs
        branchesDiff = self.get_diff(branchesBefore, branchesAfter)
        tagsDiff = self.get_diff(tagsBefore, tagsAfter)

        return branchesDiff, tagsDiff


class MirrorHub(object):
    '''Maintains a mirror of a remote repo and checks out branches and tags'''
    def __init__(self, path, remoteUrl=None,
                 branchesConfig=None, tagsConfig=None):
        self.branchesConfig = branchesConfig
        self.tagsConfig = tagsConfig
        if os.path.isdir(path):
            # open repo
            self.repo = Repo(path)

            # update origin url
            if remoteUrl is not None:
                self.repo.git.remote('set-url', 'origin', remoteUrl)

            self.sync()
        else:
            # clone
            self.repo = Repo.clone_from(remoteUrl, path, mirror=True)

            # apply 'virtual' diff where all branches + tags are added
            self._apply_diff('branches', {
                'removed': [],
                'added': [branch.name for branch in self.repo.branches],
                'modified': [],
                'unmodified': []
                })
            self._apply_diff('tags', {
                'removed': [],
                'added': [tag.name for tag in self.repo.tags],
                'modified': [],
                'unmodified': []
                })

    def sync(self):
        '''Sync the mirror and apply all changes'''
        branchesDiff, tagsDiff = self.repo.sync()
        self._apply_diff('branches', branchesDiff)
        self._apply_diff('tags', tagsDiff)

    def _apply_diff(self, refType, diff):
        '''Remove/add branch/tag checkouts based on a diff'''
        # get config for provided refType
        if refType == 'branches':
            config = self.branchesConfig
        elif refType == 'tags':
            config = self.tagsConfig
        else:
            raise ValueError('refType must be \'branches\' or \'tags\'')

        # return if there's no config for the provided refType
        if config is None:
            return

        # delete all removed and modified refNames
        self._remove(refType, config, diff['removed'] + diff['modified'])

        # add all modified and added refNames
        self._add(refType, config, diff['modified'] + diff['added'])

    def _remove(self, refType, config, refNames):
        '''Remove provided branch/tag checkouts'''
        for name in refNames:
            # check if name is valid
            if not config.is_valid_name(name):
                continue

            # delete target dir if it exists
            path = config.path + '/' + name
            if os.path.exists(path):
                # emit pre-remove event
                config.emit(refType, 'pre-remove', name,
                            'about to remove path {0}'.format(path))

                # actually remove path
                shutil.rmtree(path)

                # emit post-remove event
                config.emit(refType, 'post-remove', name,
                            'removed path {0}'.format(path))

    def _add(self, refType, config, refNames):
        '''Add provided branch/tag checkouts'''
        for name in refNames:
            # check if name is valid
            if not config.is_valid_name(name):
                continue

            # delete target dir if it exists (it shouldn't!)
            path = config.path + '/' + name
            if os.path.exists(path):
                print('Warning: removing path {0} (which shouldn\'t be there)'
                      .format(path), file=sys.stderr)
                shutil.rmtree(path)

            # emit pre-add event
            config.emit(refType, 'pre-add', name,
                        'about to clone to path {0}'.format(path))

            # shallow-clone
            self.repo.clone(path, branch=name, depth=1)

            # emit post-add event
            config.emit(refType, 'post-add', name,
                        'cloned to path {0}'.format(path))
