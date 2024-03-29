#
# Copyright (c) 2018-2019 Wind River Systems, Inc.
#
# SPDX-License-Identifier: Apache-2.0
#

from sysinv.common import constants
from sysinv.common import exception
from sysinv.common.storage_backend_conf import StorageBackendConfig

from sysinv.openstack.common import log as logging
from sysinv.helm import common
from sysinv.helm import openstack

LOG = logging.getLogger(__name__)


class CinderHelm(openstack.OpenstackBaseHelm):
    """Class to encapsulate helm operations for the cinder chart"""

    CHART = constants.HELM_CHART_CINDER

    SERVICE_NAME = 'cinder'
    SERVICE_TYPE = 'volume'
    AUTH_USERS = ['cinder']

    def get_overrides(self, namespace=None):
        overrides = {
            common.HELM_NS_OPENSTACK: {
                'pod': {
                    'replicas': {
                        'api': self._num_controllers(),
                        'volume': self._num_controllers(),
                        'scheduler': self._num_controllers(),
                        'backup': self._num_controllers()
                    }
                },
                'conf': {
                    'cinder': self._get_conf_cinder_overrides(),
                    'ceph': self._get_conf_ceph_overrides(),
                    'backends': self._get_conf_backends_overrides(),
                },
                'endpoints': self._get_endpoints_overrides(),
                'ceph_client': self._get_ceph_client_overrides()
            }
        }

        if namespace in self.SUPPORTED_NAMESPACES:
            return overrides[namespace]
        elif namespace:
            raise exception.InvalidHelmNamespace(chart=self.CHART,
                                                 namespace=namespace)
        else:
            return overrides

    def _get_conf_ceph_overrides(self):
        ceph_backend = self._get_primary_ceph_backend()
        if not ceph_backend:
            return {}

        replication, min_replication =\
            StorageBackendConfig.get_ceph_pool_replication(self.dbapi)

        rule_name = "{0}{1}{2}".format(
            constants.SB_TIER_DEFAULT_NAMES[
                constants.SB_TIER_TYPE_CEPH],
            constants.CEPH_CRUSH_TIER_SUFFIX,
            "-ruleset").replace('-', '_')

        conf_ceph = {
            'monitors': self._get_formatted_ceph_monitor_ips(),
            'admin_keyring': 'null',
            'pools': {
                'backup': {
                    # We use the chart to configure the pool for backups, so
                    # it's safe to use the same replication as for the primary
                    # tier pools.
                    'replication': replication,
                    'crush_rule': rule_name,
                },
                'volume': {
                    # The cinder chart doesn't currently support specifying
                    # the config for multiple volume/backup pools.
                    'replication': replication,
                    'crush_rule': rule_name,
                }
            }
        }

        return conf_ceph

    def _get_conf_cinder_overrides(self):
        # Get all the internal CEPH backends.
        backends = self.dbapi.storage_backend_get_list_by_type(
            backend_type=constants.SB_TYPE_CEPH)
        conf_cinder = {
            'DEFAULT': {
                # Use the CEPH backup driver. The chart will create a pool for
                # keeping the backups. All cinder backup pods will use it.
                'backup_driver': 'cinder.backup.drivers.ceph.CephBackupDriver',
                'enabled_backends': ','.join(
                    b.name.encode('utf8', 'strict') for b in backends)
            },
        }

        # If there are more CEPH backends, we default to the volume type
        # associated with the primary CEPH tier, which currently has the name
        # set to 'ceph-store'.
        if len(backends) > 1:
            default = next(
                (b.name for b in backends
                 if b.name ==
                 constants.SB_DEFAULT_NAMES[constants.SB_TYPE_CEPH]), None)
            if default:
                conf_cinder['DEFAULT']['default_volume_type'] = \
                    default.encode('utf8', 'strict')

        return conf_cinder

    def _get_conf_backends_overrides(self):
        conf_backends = {}

        # We don't use the chart's default backends.
        conf_backends['rbd1'] = ""

        # Get tier info.
        tiers = self.dbapi.storage_tier_get_list()
        primary_tier_name =\
            constants.SB_TIER_DEFAULT_NAMES[constants.SB_TIER_TYPE_CEPH]

        # We support primary and secondary CEPH tiers.
        backends = self.dbapi.storage_backend_get_list_by_type(
            backend_type=constants.SB_TYPE_CEPH)

        # No data if there are no CEPH backends.
        if not backends:
            return {}

        for bk in backends:
            bk_name = bk.name.encode('utf8', 'strict')
            tier = next((t for t in tiers if t.forbackendid == bk.id), None)
            if not tier:
                raise Exception("No tier present for backend %s" % bk_name)

            if tier.name == primary_tier_name:
                rbd_pool = constants.CEPH_POOL_VOLUMES_NAME
            else:
                rbd_pool = "%s-%s" % (constants.CEPH_POOL_VOLUMES_NAME,
                                      tier.name)

            conf_backends[bk_name] = {
                'volume_backend_name': bk_name,
                'volume_driver': 'cinder.volume.drivers.rbd.RBDDriver',
                'rbd_pool': rbd_pool.encode('utf8', 'strict'),
                'rbd_ceph_conf':
                    (constants.CEPH_CONF_PATH +
                     constants.SB_TYPE_CEPH_CONF_FILENAME),
            }

        return conf_backends

    def _get_endpoints_overrides(self):
        return {
            'identity': {
                'auth':
                self._get_endpoints_identity_overrides(
                    self.SERVICE_NAME, self.AUTH_USERS),
            },
            'oslo_db': {
                'auth': self._get_endpoints_oslo_db_overrides(
                    self.SERVICE_NAME, self.AUTH_USERS)
            },
            'oslo_cache': {
                'auth': {
                    'memcache_secret_key':
                        self._get_common_password('auth_memcache_key')
                }
            },
            'oslo_messaging': {
                'auth': self._get_endpoints_oslo_messaging_overrides(
                    self.SERVICE_NAME, self.AUTH_USERS)
            },

        }

    def _get_primary_ceph_backend(self):
        try:
            backend = self.dbapi.storage_backend_get_by_name(
                constants.SB_DEFAULT_NAMES[constants.SB_TYPE_CEPH])
        except Exception:
            backend = None
            pass

        return backend

    def get_region_name(self):
        return self._get_service_region_name(self.SERVICE_NAME)

    def get_service_name_v2(self):
        return self._get_configured_service_name(self.SERVICE_NAME, 'v2')

    def get_service_type_v2(self):
        service_type = self._get_configured_service_type(
            self.SERVICE_NAME, 'v2')
        if service_type is None:
            return self.SERVICE_TYPE + 'v2'
        else:
            return service_type
