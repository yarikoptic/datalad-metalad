# emacs: -*- mode: python-mode; py-indent-offset: 4; tab-width: 4; indent-tabs-mode: nil -*-
# -*- coding: utf-8 -*-
# ex: set sts=4 ts=4 sw=4 noet:
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
#
#   See COPYING file distributed along with the datalad package for the
#   copyright and license terms.
#
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
"""Test metadata """

from six import text_type
import os

from datalad.distribution.dataset import Dataset
from datalad.support.gitrepo import GitRepo
from .. import (
    get_metadata_type,
    get_refcommit,
)
from datalad.tests.utils import (
    with_tempfile,
    eq_,
    create_tree,
    assert_repo_status,
    known_failure
)


@with_tempfile(mkdir=True)
def test_get_metadata_type(path):
    ds = Dataset(path).create()
    # nothing set, nothing found
    eq_(get_metadata_type(ds), [])
    # minimal setting
    ds.config.set(
        'datalad.metadata.nativetype', 'mamboschwambo',
        where='dataset')
    eq_(get_metadata_type(ds), 'mamboschwambo')


# FIXME remove when support for the old config var is removed
@with_tempfile(mkdir=True)
def test_get_metadata_type_oldcfg(path):
    ds = Dataset(path).create()
    # minimal setting
    ds.config.set(
        'metadata.nativetype', 'mamboschwambo',
        where='dataset')
    eq_(get_metadata_type(ds), 'mamboschwambo')


@known_failure
@with_tempfile(mkdir=True)
def test_get_refcommit(path):
    # # dataset without a single commit
    ds = Dataset(GitRepo(path, create=True).path)
    eq_(get_refcommit(ds), None)
    # we get a commit via create
    ds.create(force=True)
    # still not metadata-relevant changes
    eq_(get_refcommit(ds), None)
    # place irrelevant file and commit
    create_tree(ds.path, {'.datalad': {'ignored': 'content'}})
    ds.save()
    # no change to the previous run, irrelevant changes are ignored
    eq_(get_refcommit(ds), None)
    # a real change
    create_tree(ds.path, {'real': 'othercontent'})
    ds.save()
    real_change = get_refcommit(ds)
    eq_(real_change, ds.repo.get_hexsha('HEAD'))
    # another irrelevant change, no change in refcommit
    create_tree(ds.path, {'.datalad': {'ignored2': 'morecontent'}})
    ds.save()
    eq_(get_refcommit(ds), real_change)
    # we can pick up deletions
    os.unlink(text_type(ds.pathobj / 'real'))
    ds.save()
    eq_(get_refcommit(ds), ds.repo.get_hexsha('HEAD'))
    # subdataset addition
    subds = ds.create('sub')
    subds_addition = get_refcommit(ds)
    eq_(subds_addition, ds.repo.get_hexsha('HEAD'))
    # another irrelevant change, no change in refcommit, despite subds presence
    create_tree(ds.path, {'.datalad': {'ignored3': 'evenmorecontent'}})
    ds.save()
    eq_(get_refcommit(ds), subds_addition)
    # subdataset modification is a relevant change
    create_tree(subds.path, {'real': 'real'})
    ds.save(recursive=True)
    eq_(get_refcommit(ds), ds.repo.get_hexsha('HEAD'))
    # and subdataset removal
    ds.remove('sub', check=False)
    assert_repo_status(ds.path)
    eq_(get_refcommit(ds), ds.repo.get_hexsha('HEAD'))
