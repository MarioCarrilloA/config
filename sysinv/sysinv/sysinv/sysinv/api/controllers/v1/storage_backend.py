# vim: tabstop=4 shiftwidth=4 softtabstop=4

#
# Copyright 2013 UnitedStack Inc.
# All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
# Copyright (c) 2013-2018 Wind River Systems, Inc.
#


import jsonpatch
import pecan

from pecan import rest

import wsme
from wsme import types as wtypes
import wsmeext.pecan as wsme_pecan

from sysinv.api.controllers.v1 import base
from sysinv.api.controllers.v1 import collection
from sysinv.api.controllers.v1 import link
from sysinv.api.controllers.v1 import types
from sysinv.api.controllers.v1 import utils
from sysinv.api.controllers.v1.utils import SBApiHelper as api_helper
from sysinv.common import constants
from sysinv.common import exception
from sysinv.common import utils as cutils
from sysinv import objects
from sysinv.openstack.common import log
from sysinv.openstack.common.gettextutils import _
from sysinv.openstack.common import uuidutils
from oslo_serialization import jsonutils

from sysinv.api.controllers.v1 import storage_ceph           # noqa
from sysinv.api.controllers.v1 import storage_lvm            # noqa
from sysinv.api.controllers.v1 import storage_file           # noqa
from sysinv.api.controllers.v1 import storage_ceph_external  # noqa

LOG = log.getLogger(__name__)


class StorageBackendPatchType(types.JsonPatchType):
    @staticmethod
    def mandatory_attrs():
        return ['/backend']


class StorageBackend(base.APIBase):
    """API representation of a storage backend.

        This class enforces type checking and value constraints, and converts
        between the internal object model and the API representation of
        a storage backend.
    """

    uuid = types.uuid
    "Unique UUID for this storage backend."

    backend = wtypes.text
    "Represents the storage backend (file, lvm, or ceph)."

    name = wtypes.text
    "The name of the backend (to differentiate between multiple common backends)."

    state = wtypes.text
    "The state of the backend. It can be configured or configuring."

    forisystemid = int
    "The isystemid that this storage backend belongs to."

    isystem_uuid = types.uuid
    "The UUID of the system this storage backend belongs to"

    task = wtypes.text
    "Current task of the corresponding cinder backend."

    # sqlite (for tox) doesn't support ARRAYs, so services is a comma separated
    # string
    services = wtypes.text
    "The openstack services that are supported by this storage backend."

    capabilities = {wtypes.text: types.apidict}
    "Meta data for the storage backend"

    links = [link.Link]
    "A list containing a self link and associated storage backend links."

    created_at = wtypes.datetime.datetime
    updated_at = wtypes.datetime.datetime

    # Confirmation parameter: [API-only field]
    confirmed = types.boolean
    "Represent confirmation that the backend operation should proceed"

    def __init__(self, **kwargs):
        defaults = {'uuid': uuidutils.generate_uuid(),
                    'state': constants.SB_STATE_CONFIGURING,
                    'task': constants.SB_TASK_NONE,
                    'capabilities': {},
                    'services': None,
                    'confirmed': False}

        self.fields = list(objects.storage_backend.fields.keys())

        # 'confirmed' is not part of objects.storage_backend.fields
        # (it's an API-only attribute)
        self.fields.append('confirmed')

        for k in self.fields:
            setattr(self, k, kwargs.get(k, defaults.get(k)))

    @classmethod
    def convert_with_links(cls, rpc_storage_backend, expand=True):

        storage_backend = StorageBackend(**rpc_storage_backend.as_dict())
        if not expand:
            storage_backend.unset_fields_except(['uuid',
                                                 'created_at',
                                                 'updated_at',
                                                 'isystem_uuid',
                                                 'backend',
                                                 'name',
                                                 'state',
                                                 'task',
                                                 'services',
                                                 'capabilities'])

        # never expose the isystem_id attribute
        storage_backend.isystem_id = wtypes.Unset

        storage_backend.links =\
            [link.Link.make_link('self', pecan.request.host_url,
                                 'storage_backends',
                                 storage_backend.uuid),
             link.Link.make_link('bookmark', pecan.request.host_url,
                                 'storage_backends',
                                 storage_backend.uuid,
                                 bookmark=True)]

        return storage_backend


class StorageBackendCollection(collection.Collection):
    """API representation of a collection of storage backends."""

    storage_backends = [StorageBackend]
    "A list containing storage backend objects."

    def __init__(self, **kwargs):
        self._type = 'storage_backends'

    @classmethod
    def convert_with_links(cls, rpc_storage_backends, limit, url=None,
                           expand=False, **kwargs):
        collection = StorageBackendCollection()
        collection.storage_backends = \
            [StorageBackend.convert_with_links(p, expand)
             for p in rpc_storage_backends]
        collection.next = collection.get_next(limit, url=url, **kwargs)
        return collection


LOCK_NAME = 'StorageBackendController'


class StorageBackendController(rest.RestController):
    """REST controller for storage backend."""

    _custom_actions = {
        'detail': ['GET'],
        'summary': ['GET']
    }

    def __init__(self, from_isystems=False):
        self._from_isystems = from_isystems
        self._tier_lookup = {}

    def _get_storage_backend_collection(self, isystem_uuid, marker, limit,
                                        sort_key, sort_dir, expand=False,
                                        resource_url=None):

        if self._from_isystems and not isystem_uuid:
            raise exception.InvalidParameterValue(_(
                "System id not specified."))

        limit = utils.validate_limit(limit)
        sort_dir = utils.validate_sort_dir(sort_dir)

        marker_obj = None
        if marker:
            marker_obj = objects.storage_backend.get_by_uuid(
                pecan.request.context,
                marker)

        if isystem_uuid:
            storage_backends = \
                pecan.request.dbapi.storage_backend_get_by_isystem(
                    isystem_uuid, limit,
                    marker_obj,
                    sort_key=sort_key,
                    sort_dir=sort_dir)
        else:
            storage_backends = \
                pecan.request.dbapi.storage_backend_get_list(
                    limit,
                    marker_obj,
                    sort_key=sort_key,
                    sort_dir=sort_dir)

        # TODO: External backend case for emc_vnx, hpe3par, hpelefthand will be
        # handled in a separate task
        # If cinder is not configured yet, calling cinder_has_external_backend() will
        # timeout. If any of these loosely coupled backend exists, create an external
        # backend with services set to cinder if external backend is not created yet.
        # if api_helper.is_svc_enabled(storage_backends, constants.SB_SVC_CINDER):
        #    try:
        #        if pecan.request.rpcapi.cinder_has_external_backend(pecan.request.context):
        #
        #            # Check if external backend already exists.
        #            need_soft_ext_sb = True
        #            for s_b in storage_backends:
        #                if s_b.backend == constants.SB_TYPE_EXTERNAL:
        #                    if s_b.services is None:
        #                        s_b.services = [constants.SB_SVC_CINDER]
        #                    elif constants.SB_SVC_CINDER not in s_b.services:
        #                        s_b.services.append(constants.SB_SVC_CINDER)
        #                    need_soft_ext_sb = False
        #                    break
        #
        #            if need_soft_ext_sb:
        #                ext_sb = StorageBackend()
        #                ext_sb.backend = constants.SB_TYPE_EXTERNAL
        #                ext_sb.state = constants.SB_STATE_CONFIGURED
        #                ext_sb.task = constants.SB_TASK_NONE
        #                ext_sb.services = [constants.SB_SVC_CINDER]
        #                storage_backends.extend([ext_sb])
        #    except Timeout:
        #        LOG.exception("Timeout while getting external backend list!")

        return StorageBackendCollection\
            .convert_with_links(storage_backends,
                                limit,
                                url=resource_url,
                                expand=expand,
                                sort_key=sort_key,
                                sort_dir=sort_dir)

    @wsme_pecan.wsexpose(StorageBackendCollection, types.uuid, types.uuid,
                         int, wtypes.text, wtypes.text)
    def get_all(self, isystem_uuid=None, marker=None, limit=None,
                sort_key='id', sort_dir='asc'):
        """Retrieve a list of storage backends."""

        return self._get_storage_backend_collection(isystem_uuid, marker,
                                                    limit, sort_key, sort_dir)

    @wsme_pecan.wsexpose(StorageBackend, types.uuid)
    def get_one(self, storage_backend_uuid):
        """Retrieve information about the given storage backend."""

        rpc_storage_backend = objects.storage_backend.get_by_uuid(
            pecan.request.context,
            storage_backend_uuid)
        return StorageBackend.convert_with_links(rpc_storage_backend)

    @cutils.synchronized(LOCK_NAME)
    @wsme_pecan.wsexpose(StorageBackend, body=StorageBackend)
    def post(self, storage_backend):
        """Create a new storage backend."""
        try:
            storage_backend = storage_backend.as_dict()
            api_helper.validate_backend(storage_backend)
            new_storage_backend = _create(storage_backend)

        except exception.SysinvException as e:
            LOG.exception(e)
            raise wsme.exc.ClientSideError(_("Invalid data: failed to create "
                                             "a storage backend record."))

        return StorageBackend.convert_with_links(new_storage_backend)

    @wsme_pecan.wsexpose(StorageBackendCollection)
    def detail(self):
        """Retrieve a list of storage_backends with detail."""
        raise wsme.exc.ClientSideError(_("detail not implemented."))

    @cutils.synchronized(LOCK_NAME)
    @wsme.validate(types.uuid, [StorageBackendPatchType])
    @wsme_pecan.wsexpose(StorageBackend, types.uuid,
                         body=[StorageBackendPatchType])
    def patch(self, storage_backend_uuid, patch):
        """Update the current Storage Backend."""
        if self._from_isystems:
            raise exception.OperationNotPermitted

        # This is the base class call into the appropriate backend class to
        # update
        return _patch(storage_backend_uuid, patch)

        rpc_storage_backend = objects.storage_backend.get_by_uuid(
            pecan.request.context, storage_backend_uuid)
        # action = None
        for p in patch:
            # if '/action' in p['path']:
            #     value = p['value']
            #     patch.remove(p)
            #     if value in (constants.APPLY_ACTION,
            #                  constants.INSTALL_ACTION):
            #         action = value
            # elif p['path'] == '/capabilities':
            if p['path'] == '/capabilities':
                p['value'] = jsonutils.loads(p['value'])

        # replace isystem_uuid and storage_backend_uuid with corresponding
        patch_obj = jsonpatch.JsonPatch(patch)
        state_rel_path = ['/uuid', '/forisystemid', '/isystem_uuid']
        if any(p['path'] in state_rel_path for p in patch_obj):
            raise wsme.exc.ClientSideError(_("The following fields can not be "
                                             "modified: %s" %
                                             state_rel_path))
        for p in patch_obj:
            if p['path'] == '/isystem_uuid':
                isystem = objects.system.get_by_uuid(pecan.request.context,
                                                     p['value'])
                p['path'] = '/forisystemid'
                p['value'] = isystem.id
                break

        try:
            storage_backend = StorageBackend(**jsonpatch.apply_patch(
                rpc_storage_backend.as_dict(),
                patch_obj))

        except utils.JSONPATCH_EXCEPTIONS as e:
            raise exception.PatchError(patch=patch, reason=e)

        # Update only the fields that have changed
        for field in objects.storage_backend.fields:
            if rpc_storage_backend[field] != getattr(storage_backend, field):
                rpc_storage_backend[field] = getattr(storage_backend, field)

        # Save storage_backend
        rpc_storage_backend.save()
        return StorageBackend.convert_with_links(rpc_storage_backend)

    @wsme_pecan.wsexpose(None)
    def delete(self):
        """Retrieve a list of storage_backend with detail."""
        raise wsme.exc.ClientSideError(_("delete not implemented."))


#
# Create
#

def _create(storage_backend):
    # Get and call the specific backend create function based on the backend
    # provided.
    backend_create = getattr(eval('storage_' + storage_backend['backend']),
                             '_create')
    new_backend = backend_create(storage_backend)

    return new_backend


#
# Update/Modify/Patch
#

def _patch(storage_backend_uuid, patch):
    rpc_storage_backend = objects.storage_backend.get_by_uuid(
        pecan.request.context, storage_backend_uuid)

    # Get and call the specific backend patching function based on the backend
    # provided.
    backend_patch = getattr(eval('storage_' + rpc_storage_backend.backend),
                            '_patch')
    return backend_patch(storage_backend_uuid, patch)
