# emacs: -*- mode: python; py-indent-offset: 4; tab-width: 4; indent-tabs-mode: nil -*-
# ex: set sts=4 ts=4 sw=4 noet:
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
#
#   See COPYING file distributed along with the datalad package for the
#   copyright and license terms.
#
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
"""
Run a dataset-level metadata extractor on a dataset
or run a file-level metadata extractor on a file
"""
import json
import logging
import re
import time
from os import curdir
from pathlib import (
    Path,
    PurePath,
)
from typing import (
    Dict,
    Iterable,
    List,
    Optional,
    Tuple,
    Type,
    Union,
)
from uuid import UUID

from dataclasses import dataclass

from datalad.distribution.dataset import Dataset
from datalad.distribution.dataset import (
    datasetmethod,
    EnsureDataset,
)
from datalad.interface.base import Interface
from datalad.interface.base import build_doc
from datalad.interface.utils import eval_results
from datalad.metadata.extractors.base import BaseMetadataExtractor
from datalad.support.annexrepo import AnnexRepo
from datalad.ui import ui

from .extractors.base import (
    DataOutputCategory,
    DatasetMetadataExtractor,
    FileInfo,
    FileMetadataExtractor,
    MetadataExtractor,
    MetadataExtractorBase,
)

from datalad.support.constraints import (
    EnsureNone,
    EnsureStr,
)
from datalad.support.param import Parameter

from dataladmetadatamodel.metadatapath import MetadataPath

from .utils import (
    NoDatasetFound,
    args_to_dict,
    check_dataset,
)


__docformat__ = "restructuredtext"

default_mapper_family = "git"

lgr = logging.getLogger("datalad.metadata.extract")


@dataclass
class ExtractionParameter:
    source_dataset: Dataset
    source_dataset_id: UUID
    source_dataset_version: str
    local_source_object_path: Path
    extractor_class: Union[type(MetadataExtractor), type(FileMetadataExtractor)]
    extractor_name: str
    extractor_arguments: Dict[str, str]
    file_tree_path: Optional[MetadataPath]
    agent_name: str
    agent_email: str


@build_doc
class Extract(Interface):
    """Run a metadata extractor on a dataset or file.

    This command distinguishes between dataset-level extraction and
    file-level extraction.

    If no "path" argument is given, the command
    assumes that a given extractor is a dataset-level extractor and
    executes it on the dataset that is given by the current working
    directory or by the "-d" argument.

    If a path is given, the command assumes that the path identifies a
    file and that the given extractor is a file-level extractor, which
    will then be executed on the specified file. If the file level
    extractor requests the content of a file that is not present, the
    command might "get" the file content to make it locally available.
    Path must not refer to a sub-dataset. Path must not be a directory.

    .. note::

        If you want to insert sub-dataset-metadata into the super-dataset's
        metadata, you currently have to do the following:
        first, extract dataset metadata of the sub-dataset using a dataset-
        level extractor, second add the extracted metadata with sub-dataset
        information (i.e. dataset_path, root_dataset_id, root-dataset-
        version) to the metadata of the super-dataset.

    The extractor configuration can be parameterized with key-value pairs
    given as additional arguments. Each key-value pair consists of two
    arguments, first the key, followed by the value. If no path is given,
    and you want to provide key-value pairs, you have to give the path
    "++", to prevent that the first key is interpreted as path.

    The results are written into the repository of the source dataset
    or into the repository of the dataset given by the "-i" parameter.
    If the same extractor is executed on the same element (dataset or
    file) with the same configuration, any existing results will be
    overwritten.

    The command can also take legacy datalad-metalad extractors and
    will execute them in either "content" or "dataset" mode, depending
    on the presence of the "path"-parameter.
    """

    result_renderer = "tailored"

    _examples_ = [
        dict(
            text='Use the metalad_core_file-extractor to extract metadata'
                 'from the file "subdir/data_file_1.txt". The dataset is '
                 'given by the current working directory',
            code_cmd="datalad meta-extract metalad_core_file "
                     "subdir/data_file_1.txt"
        ),
        dict(
            text='Use the metalad_core_file-extractor to extract metadata '
                 'from the file "subdir/data_file_1.txt" in the dataset '
                 '/home/datasets/ds0001',
            code_cmd="datalad meta-extract -d /home/datasets/ds0001 "
                     "metalad_core_file subdir/data_file_1.txt"
        ),
        dict(
            text='Use the metalad_core_dataset-extractor to extract '
                 'dataset-level metadata from the dataset given by the '
                 'current working directory',
            code_cmd="datalad meta-extract metalad_core_dataset"
        ),
        dict(
            text='Use the metalad_core_dataset-extractor to extract '
                 'dataset-level metadata from the dataset in '
                 '/home/datasets/ds0001',
            code_cmd="datalad meta-extract -d /home/datasets/ds0001 "
                     "metalad_core_dataset"
        )]

    _params_ = dict(
        extractorname=Parameter(
            args=("extractorname",),
            metavar="EXTRACTOR_NAME",
            doc="Name of a metadata extractor to be executed."),
        path=Parameter(
            args=("path",),
            metavar="FILE",
            nargs="?",
            doc="""Path of a file or dataset to extract metadata
            from. If this argument is provided, we assume a file
            extractor is requested, if the path is not given, or
            if it identifies the root of a dataset, i.e. "", we
            assume a dataset level metadata extractor is
            specified.""",
            constraints=EnsureStr() | EnsureNone()),
        dataset=Parameter(
            args=("-d", "--dataset"),
            doc="""Dataset to extract metadata from. If no dataset
            is given, the dataset is determined by the current work
            directory.""",
            constraints=EnsureDataset() | EnsureNone()),
        context=Parameter(
            args=("-c", "--context"),
            doc="""Context, a JSON-serialized dictionary that provides
            constant data which has been gathered before, so meta-extract
            will not have re-gather this data. Keys and values are strings.
            meta-extract will look for the following key: 'dataset_version'.""",
            constraints=EnsureDataset() | EnsureNone()),
        get_context=Parameter(
            args=("--get-context",),
            action="store_true",
            doc="""Show the context that meta-extract determines with the
            given parameters and exit. The context can be used in subsequent
            calls to meta-extract with identical parameter, except from
            --get-context, to speed up the execution of meta-extract."""),
        extractorargs=Parameter(
            args=("extractorargs",),
            metavar="EXTRACTOR_ARGUMENTS",
            doc="""Extractor arguments given as string arguments to the
            extractor. If dataset level extraction is performed, i.e. no path
            is required, specify '--' as path to prevent interpretation of
            the first extractor argument as path.""",
            nargs="*",
            constraints=EnsureStr() | EnsureNone()))

    @staticmethod
    @datasetmethod(name="meta_extract")
    @eval_results
    def __call__(
            extractorname: str,
            path: Optional[str] = None,
            dataset: Optional[Union[Dataset, str]] = None,
            context: Optional[Union[str, Dict[str, str]]] = None,
            get_context: bool = False,
            extractorargs: Optional[List[str]] = None):

        # Get basic arguments
        extractor_name = extractorname
        extractor_args = extractorargs
        path = None if path == "--" else path
        context = (
            {}
            if context is None
            else (
                json.loads(context)
                if isinstance(context, str)
                else context))

        try:
            source_dataset = check_dataset(dataset or curdir,
                                           "extract metadata")
        except NoDatasetFound as raised_exception:
            yield dict(
                status="error",
                path=dataset or curdir,
                type="dataset" if path is None else "file",
                action="meta_extract",
                message=str(raised_exception),
                logger=lgr
            )
            return

        source_dataset_version = context.get("dataset_version", None)
        if source_dataset_version is None:
            source_dataset_version = source_dataset.repo.get_hexsha()

        if get_context is True:
            yield dict(
                status="ok",
                action="meta_extract",
                path=source_dataset.path,
                logger=lgr,
                context=dict(
                    dataset_version=source_dataset_version
                )
            )
            return

        extractor_class = get_extractor_class(extractor_name)
        dataset_tree_path, file_tree_path = get_path_info(
            source_dataset,
            Path(path) if path else None,
            None)

        extraction_parameters = ExtractionParameter(
            source_dataset=source_dataset,
            source_dataset_id=UUID(source_dataset.id),
            source_dataset_version=source_dataset_version,
            local_source_object_path=(
                    source_dataset.pathobj / file_tree_path).absolute(),
            extractor_class=extractor_class,
            extractor_name=extractor_name,
            extractor_arguments=args_to_dict(extractor_args),
            file_tree_path=file_tree_path,
            agent_name=source_dataset.config.get("user.name"),
            agent_email=source_dataset.config.get("user.email"))

        # If a path is given, we assume file-level metadata extraction is
        # requested, and the extractor class should be a subclass of
        # FileMetadataExtractor (or a legacy extractor).
        # If path is not given, we assume that a dataset-level extraction is
        # requested and the extractor class is a subclass of
        # DatasetMetadataExtractor (or a legacy extractor class).
        if path:
            # Check whether the path points to a sub_dataset.
            ensure_path_validity(source_dataset, file_tree_path)
            yield from do_file_extraction(extraction_parameters)
        else:
            yield from do_dataset_extraction(extraction_parameters)
        return

    @staticmethod
    def custom_result_renderer(res, **kwargs):
        if res["status"] != "ok" or res.get("action", "") != 'meta_extract':
            # logging complained about this already
            return

        metadata_record = res.get("metadata_record", None)
        if metadata_record is not None:
            path = (
                {"path": str(metadata_record["path"])}
                if "path" in metadata_record
                else {}
            )

            dataset_path = (
                {"dataset_path": str(metadata_record["dataset_path"])}
                if "dataset_path" in metadata_record
                else {}
            )

            ui.message(json.dumps({
                **metadata_record,
                **path,
                **dataset_path,
                "dataset_id": str(metadata_record["dataset_id"])
            }))

        context = res.get("context")
        if context is not None:
            ui.message(json.dumps(context))


def do_dataset_extraction(ep: ExtractionParameter):

    if not issubclass(ep.extractor_class, MetadataExtractorBase):

        lgr.debug(
            "performing legacy dataset level metadata "
            "extraction for dataset at at %s",
            ep.source_dataset.path)

        yield from legacy_extract_dataset(ep)
        return

    if not issubclass(ep.extractor_class, DatasetMetadataExtractor):
        raise ValueError(
            "A dataset-level metadata-extraction was attempted (since no "
            "path argument was given), but the specified extractor "
            f"({ep.extractor_name}) is not a dataset-level extractor.")

    lgr.debug(
        "extracting dataset level metadata for dataset at %s",
        ep.source_dataset.path)

    extractor = ep.extractor_class(
        ep.source_dataset,
        ep.source_dataset_version,
        ep.extractor_arguments)

    yield from perform_dataset_metadata_extraction(ep, extractor)


def do_file_extraction(ep: ExtractionParameter):

    if not issubclass(ep.extractor_class, MetadataExtractorBase):

        lgr.debug(
            "performing legacy file level metadata "
            "extraction for file at %s/%s",
            ep.source_dataset.path,
            ep.file_tree_path)

        yield from legacy_extract_file(ep)
        return

    if not issubclass(ep.extractor_class, FileMetadataExtractor):

        raise ValueError(
            "A file-level metadata-extraction was attempted, but the "
            f"specified extractor ({ep.extractor_name}) is not a "
            f"file-level extractor.")

    lgr.debug(
        "performing file level extracting for file at %s/%s",
        ep.source_dataset.path,
        ep.file_tree_path)

    file_info = get_file_info(ep.source_dataset, ep.file_tree_path)
    extractor = ep.extractor_class(
        ep.source_dataset,
        ep.source_dataset_version,
        file_info,
        ep.extractor_arguments)

    ensure_content_availability(extractor, file_info)

    yield from perform_file_metadata_extraction(ep, extractor)


def perform_file_metadata_extraction(ep: ExtractionParameter,
                                     extractor: FileMetadataExtractor):

    output_category = extractor.get_data_output_category()
    if output_category != DataOutputCategory.IMMEDIATE:
        raise NotImplementedError(
            f"Output category {output_category} not supported")

    result = extractor.extract(None)
    result.datalad_result_dict["action"] = "meta_extract"
    result.datalad_result_dict["path"] = ep.local_source_object_path
    if result.extraction_success:
        result.datalad_result_dict["metadata_record"] = dict(
            type="file",
            dataset_id=ep.source_dataset_id,
            dataset_version=ep.source_dataset_version,
            path=ep.file_tree_path,
            extractor_name=ep.extractor_name,
            extractor_version=extractor.get_version(),
            extraction_parameter=ep.extractor_arguments,
            extraction_time=time.time(),
            agent_name=ep.agent_name,
            agent_email=ep.agent_email,
            extracted_metadata=result.immediate_data)

    yield result.datalad_result_dict


def perform_dataset_metadata_extraction(ep: ExtractionParameter,
                                        extractor: DatasetMetadataExtractor):

    output_category = extractor.get_data_output_category()
    if output_category != DataOutputCategory.IMMEDIATE:
        raise NotImplementedError(
            f"Output category {output_category} not supported")

    # Process results
    result = extractor.extract(None)
    result.datalad_result_dict["action"] = "meta_extract"
    result.datalad_result_dict["path"] = ep.local_source_object_path
    if result.extraction_success:
        result.datalad_result_dict["metadata_record"] = dict(
            type="dataset",
            dataset_id=ep.source_dataset_id,
            dataset_version=ep.source_dataset_version,
            extractor_name=ep.extractor_name,
            extractor_version=extractor.get_version(),
            extraction_parameter=ep.extractor_arguments,
            extraction_time=time.time(),
            agent_name=ep.agent_name,
            agent_email=ep.agent_email,
            extracted_metadata=result.immediate_data)

    yield result.datalad_result_dict


def get_extractor_class(extractor_name: str) -> Union[
                                            Type[DatasetMetadataExtractor],
                                            Type[FileMetadataExtractor]]:

    """ Get an extractor from its name """
    from pkg_resources import iter_entry_points

    entry_points = list(
        iter_entry_points("datalad.metadata.extractors", extractor_name))

    if not entry_points:
        raise ValueError(
            "Requested metadata extractor '{}' not available".format(
                extractor_name))

    entry_point, ignored_entry_points = entry_points[-1], entry_points[:-1]
    lgr.debug(
        "Using metadata extractor %s from distribution %s",
        extractor_name,
        entry_point.dist.project_name)

    # Inform about overridden entry points
    for ignored_entry_point in ignored_entry_points:
        lgr.warning(
            "Metadata extractor %s from distribution %s overrides "
            "metadata extractor from distribution %s",
            extractor_name,
            entry_point.dist.project_name,
            ignored_entry_point.dist.project_name)

    return entry_point.load()


def get_file_info(dataset: Dataset,
                  file_path: MetadataPath) -> FileInfo:
    """
    Get information about the file in the dataset or
    None, if the file is not part of the dataset.
    """

    # Convert the metadata file-path into a system file path
    path = Path(file_path)
    try:
        relative_path = path.relative_to(dataset.pathobj)
    except ValueError:
        relative_path = path

    path = dataset.pathobj / relative_path

    path_status = (
            list(dataset.status(path, result_renderer="disabled")) or [None])[0]

    if path_status is None:
        raise FileNotFoundError(
            "no dataset status for dataset: {} file: {}".format(
                dataset.path, path))

    if path_status["state"] == "untracked":
        raise ValueError("file not tracked: {}".format(path))

    path_relative_to_dataset = PurePath(
        path_status["path"]).relative_to(dataset.pathobj)

    # noinspection PyUnresolvedReferences
    return FileInfo(
        type="file",     # TODO: what about the situation where path_status["type"] == "symlink"?
        git_sha_sum=path_status["gitshasum"],
        byte_size=path_status.get("bytesize", 0),
        state=path_status["state"],
        path=path_status["path"],   # Absolute path, used by extractors
        intra_dataset_path=str(
            MetadataPath(*path_relative_to_dataset.parts)))


def get_path_info(dataset: Dataset,
                  element_path: Optional[Path],
                  into_dataset_path: Optional[Path] = None
                  ) -> Tuple[MetadataPath, MetadataPath]:
    """
    Determine the dataset tree path and the file tree path.

    If the path is absolute, we can determine the containing dataset
    and the metadatasets around it. If the path is not an element of
    a locally known dataset, we signal an error.

    If the path is relative, we convert it to an absolute path
    by appending it to the dataset or current directory and perform
    the above check.
    """
    full_dataset_path = Path(dataset.path).resolve()
    if into_dataset_path is None:
        dataset_tree_path = MetadataPath("")
    else:
        full_into_dataset_path = into_dataset_path.resolve()
        dataset_tree_path = MetadataPath(
            full_dataset_path.relative_to(full_into_dataset_path))

    if element_path is None:
        return dataset_tree_path, MetadataPath("")

    if element_path.is_absolute():
        full_file_path = element_path
    else:
        full_file_path = full_dataset_path / element_path

    file_tree_path = full_file_path.relative_to(full_dataset_path)

    return dataset_tree_path, MetadataPath(file_tree_path)


def ensure_path_validity(dataset: Dataset, file_tree_path: MetadataPath):
    # TODO: there is most likely a better way to do this in datalad,
    # but I want to ensure, that we do not enumerate all sub-datasets
    # in order to perform this check on a known path.

    full_path = dataset.pathobj / file_tree_path
    if full_path.is_dir():
        raise ValueError(f"FILE must not point to a directory ({full_path})")


def ensure_content_availability(extractor: FileMetadataExtractor,
                                file_info: FileInfo):

    if extractor.is_content_required():
        for result in extractor.dataset.get(path={file_info.path},
                                            get_data=True,
                                            return_type="generator",
                                            result_renderer="disabled"):
            if result.get("status", "") == "error":
                lgr.error(
                    "cannot make content of {} available in dataset {}".format(
                        file_info.path, extractor.dataset))
                return
        lgr.debug(
            "requested content {}:{} available".format(
                extractor.dataset.path, file_info.intra_dataset_path))


def ensure_legacy_path_availability(ep: ExtractionParameter, path: str):
    for result in ep.source_dataset.get(path=path,
                                        get_data=True,
                                        return_type="generator",
                                        result_renderer="disabled"):

        if result.get("status", "") == "error":
            lgr.error(
                "cannot make content of {} available "
                "in dataset {}".format(
                    path, ep.source_dataset))
            return

    lgr.debug(
        "requested content {}:{} available".format(
            ep.source_dataset.path, path))


def ensure_legacy_content_availability(ep: ExtractionParameter,
                                       extractor: MetadataExtractor,
                                       operation: str,
                                       status: List[dict]):

    try:
        for required_element in extractor.get_required_content(
                    ep.source_dataset,
                    operation,
                    status):

            ensure_legacy_path_availability(ep, required_element.path)
    except AttributeError:
        pass


def legacy_extract_dataset(ep: ExtractionParameter) -> Iterable[dict]:

    if issubclass(ep.extractor_class, MetadataExtractor):

        status = ep.source_dataset.repo.get_submodules()
        extractor = ep.extractor_class()
        ensure_legacy_content_availability(ep, extractor, "dataset", status)

        for result in extractor(ep.source_dataset,
                                ep.source_dataset_version,
                                "dataset",
                                status):

            yield {
                "status": result["status"],
                "action": "meta_extract",
                "path": ep.source_dataset.path,
                "type": "dataset",
                ** {
                    "message": result["message"]
                    for _ in [1]
                    if "message" in result
                },
                ** (
                    {
                        "metadata_record": dict(
                            type="dataset",
                            dataset_id=ep.source_dataset_id,
                            dataset_version=ep.source_dataset_version,
                            extractor_name=ep.extractor_name,
                            extractor_version=str(
                                extractor.get_state(ep.source_dataset).get(
                                    "version", "---")),
                            extraction_parameter=ep.extractor_arguments,
                            extraction_time=time.time(),
                            agent_name=ep.agent_name,
                            agent_email=ep.agent_email,
                            extracted_metadata=result["metadata"])
                    }
                    if result["status"] == "ok"
                    else {})
            }

    elif issubclass(ep.extractor_class, BaseMetadataExtractor):

        # Datalad legacy extractor
        ds_path = str(ep.source_dataset.pathobj)
        if ep.extractor_class.NEEDS_CONTENT:
            ensure_legacy_path_availability(ep, ds_path)

        extractor = ep.extractor_class(ep.source_dataset, [ds_path])
        dataset_result, _ = extractor.get_metadata(True, False)

        yield dict(
            action="meta_extract",
            status="ok",
            type="dataset",
            metadata_record=dict(
                type="dataset",
                dataset_id=ep.source_dataset_id,
                dataset_version=ep.source_dataset_version,
                extractor_name=ep.extractor_name,
                extractor_version="un-versioned",
                extraction_parameter=ep.extractor_arguments,
                extraction_time=time.time(),
                agent_name=ep.agent_name,
                agent_email=ep.agent_email,
                extracted_metadata=dataset_result))

    else:
        raise ValueError(
            f"unknown extractor class: {type(ep.extractor_class).__name__}")


def annex_status(annex_repo, paths=None):
    info = annex_repo.get_content_annexinfo(
        paths=paths,
        eval_availability=False,
        init=annex_repo.get_content_annexinfo(
            paths=paths,
            ref="HEAD",
            eval_availability=False,
            init=annex_repo.status(
                paths=paths,
                untracked="no",
                eval_submodule_state="full")
        )
    )
    annex_repo._mark_content_availability(info)
    return info


def legacy_get_file_info(dataset: Dataset,
                         path: Path
                         ) -> Dict:
    return {
        "path": str(path),
        **(
            annex_status(dataset.repo, [path])[path]
            if isinstance(dataset.repo, AnnexRepo)
            else dataset.repo.status([path], untracked="no")[path]
        )
    }


def legacy_extract_file(ep: ExtractionParameter) -> Iterable[dict]:

    if issubclass(ep.extractor_class, MetadataExtractor):

        # Call metalad legacy extractor with a single status record.

        file_path = ep.source_dataset.pathobj / ep.file_tree_path
        # Determine the file type:
        extractor = ep.extractor_class()
        status = legacy_get_file_info(ep.source_dataset, file_path)
        ensure_legacy_content_availability(ep, extractor, "content", [status])

        for result in extractor(ep.source_dataset,
                                ep.source_dataset_version,
                                "content",
                                [status]):

            if result["status"] == "ok":
                yield dict(
                    action="meta_extract",
                    status="ok",
                    type="file",
                    path=str(file_path.absolute()),
                    metadata_record=dict(
                        type="file",
                        dataset_id=ep.source_dataset_id,
                        dataset_version=ep.source_dataset_version,
                        path=ep.file_tree_path,
                        extractor_name=ep.extractor_name,
                        extractor_version=str(
                            extractor.get_state(ep.source_dataset).get(
                                "version", "---")),
                        extraction_parameter=ep.extractor_arguments,
                        extraction_time=time.time(),
                        agent_name=ep.agent_name,
                        agent_email=ep.agent_email,
                        extracted_metadata=result["metadata"]))
            else:
                yield dict(
                    action="meta_extract",
                    status=result["status"],
                    type="file",
                    path=str(file_path.absolute()),
                    message=result["message"])

    elif issubclass(ep.extractor_class, BaseMetadataExtractor):

        # Datalad legacy extractor
        path = str(ep.source_dataset.pathobj / ep.file_tree_path)
        if ep.extractor_class.NEEDS_CONTENT:
            ensure_legacy_path_availability(ep, path)

        extractor = ep.extractor_class(ep.source_dataset, [str(ep.file_tree_path)])
        _, file_result = extractor.get_metadata(False, True)

        for extracted_path, metadata in file_result:
            yield dict(
                action="meta_extract",
                status="ok",
                type="file",
                path=path,
                metadata_record=dict(
                    type="file",
                    dataset_id=ep.source_dataset_id,
                    dataset_version=ep.source_dataset_version,
                    path=MetadataPath(extracted_path),
                    extractor_name=ep.extractor_name,
                    extractor_version="un-versioned",
                    extraction_parameter=ep.extractor_arguments,
                    extraction_time=time.time(),
                    agent_name=ep.agent_name,
                    agent_email=ep.agent_email,
                    extracted_metadata=metadata
                ))

    else:
        raise ValueError(
            f"unknown extractor class: {type(ep.extractor_class).__name__}")
