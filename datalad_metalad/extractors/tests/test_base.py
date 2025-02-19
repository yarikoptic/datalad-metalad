# emacs: -*- mode: python-mode; py-indent-offset: 4; tab-width: 4; indent-tabs-mode: nil; coding: utf-8 -*-
# ex: set sts=4 ts=4 sw=4 noet:
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
#
#   See COPYING file distributed along with the datalad package for the
#   copyright and license terms.
#
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
"""Test all extractors at a basic level"""

from six import (
    text_type,
)
from pkg_resources import iter_entry_points
from datalad.api import (
    Dataset,
    meta_extract,
    save,
    install,
)
from datalad.support.gitrepo import GitRepo

from nose import SkipTest
from datalad.tests.utils import (
    assert_repo_status,
    assert_true,
    assert_not_in,
    assert_in,
    assert_raises,
    assert_result_count,
    known_failure_windows,
    with_tree,
    with_tempfile,
)
from ...tests import (
    make_ds_hierarchy_with_metadata,
    _get_dsmeta_from_core_metadata,
)


@with_tree(tree={'file.dat': ''})
def check_api(annex, path):
    ds = Dataset(path).create(force=True, annex=annex)
    ds.save()
    assert_repo_status(ds.path)

    processed_extractors, skipped_extractors = [], []
    for extractor_ep in iter_entry_points('datalad.metadata.extractors'):

        # There are a number of extractors that do not
        # work on empty datasets, or datasets, or without
        # external processes. We skip those here, instead
        # of skipping them later.
        if extractor_ep.name in ("metalad_core_file",
                                 "external_dataset",
                                 "external_file",
                                 "metalad_studyminimeta",
                                 "bids",
                                 "dicom"):
            continue

        # we need to be able to query for metadata, even if there is none
        # from any extractor
        try:
            res = meta_extract(
                dataset=ds,
                extractorname=extractor_ep.name,
            )
        except Exception as exc:
            skipped_extractors += [f"{extractor_ep.name}: {exc}"]
            continue

        # metalad_core does provide some information about our precious file
        if extractor_ep.name == 'metalad_core':
            assert_result_count(
                res,
                1,
                path=ds.path,
                type='dataset',
                status='ok',
            )

            assert_true(
                all([
                    r['metadata_record']['extractor_name'] == extractor_ep.name
                    for r in res]))

            extracted_metadata = [
                r['metadata_record']['extracted_metadata']
                for r in res
            ]

            # every single report comes with an identifier
            assert_true(all(
                (em["@graph"][1]["identifier"] == ds.id
                 for em in extracted_metadata)
            ))

        processed_extractors.append(extractor_ep.name)
    assert "metalad_core" in processed_extractors, \
        "Should have managed to find at least the core extractor extractor"
    if skipped_extractors:
        raise SkipTest(
            "Not fully tested/succeded since some extractors failed"
            " to load:\n%s" % ("\n".join(skipped_extractors)))


def test_api_git():
    # should tolerate both pure git and annex repos
    yield check_api, False


def test_api_annex():
    yield check_api, True


@with_tempfile(mkdir=True)
def test_plainest(path):

    # blow on nothing
    assert_raises(
        ValueError,
        meta_extract, dataset=path, extractorname='metalad_core')

    r = GitRepo(path, create=True)
    # proper error, no crash, when there is the thinnest of all traces
    # of a dataset: but nothing to describe
    assert_result_count(
        meta_extract(
            dataset=r.path,
            extractorname='metalad_core',
            on_failure='ignore',
        ),
        1,
        status='error',
        # message contains exception
        type='dataset',
        path=r.path,
    )
    # not we add some dummy content that does not count as metadata-relevant
    # and we still fail
    (r.pathobj / '.datalad').mkdir()
    (r.pathobj / '.datalad' / 'dummy').write_text(text_type('stamp'))
    ds = Dataset(r.path)
    ds.save()
    assert_result_count(
        meta_extract(
            dataset=ds.path,
            extractorname='metalad_core',
            on_failure='ignore',
        ),
        1,
        status='error',
        # message contains exception
        type='dataset',
        path=ds.path,
    )


@known_failure_windows
@with_tempfile
@with_tempfile
def test_report(path, orig):
    origds, subds = make_ds_hierarchy_with_metadata(orig)
    # now clone to a new place to ensure no content is present
    ds = install(source=origds.path, path=path)
    # only dataset-global metadata
    res = meta_extract(dataset=ds, extractorname='metalad_core')
    assert_result_count(res, 1)
    core_dsmeta = _get_dsmeta_from_core_metadata(
        res[0]['metadata_record']['extracted_metadata']
    )
    assert_in(
            {'@type': 'Dataset',
            '@id': 'datalad:{}'.format(subds.repo.get_hexsha()),
            'identifier': 'datalad:{}'.format(subds.id), 'name': 'sub'},
        core_dsmeta['hasPart']
    )
    # has not seen the content
    assert_not_in(
        'contentbytesize',
        core_dsmeta
    )
    res = meta_extract(dataset=ds,
                       extractorname='metalad_annex',
                       path="file.dat")
    assert(any(
        dict(tag=['one', 'two']) == r['metadata_record']['extracted_metadata']
        for r in res
    ))
    # we have a report on file(s)
    assert(len(res) > 0)
    # but no subdataset reports
    assert_result_count(res, 0, type='dataset')
