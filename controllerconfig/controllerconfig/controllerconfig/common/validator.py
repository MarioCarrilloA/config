"""
Copyright (c) 2015-2019 Wind River Systems, Inc.

SPDX-License-Identifier: Apache-2.0

"""
from controllerconfig.common.configobjects import DEFAULT_NAMES
from controllerconfig.common.configobjects import NETWORK_PREFIX_NAMES
from controllerconfig.common.configobjects import OAM_TYPE
from controllerconfig.common.configobjects import MGMT_TYPE
from controllerconfig.common.configobjects import Network
from controllerconfig.common.configobjects import REGION_CONFIG
from controllerconfig.common.configobjects import DEFAULT_DOMAIN_NAME
from controllerconfig.common.configobjects import HP_NAMES
from controllerconfig.common.configobjects import SUBCLOUD_CONFIG
from controllerconfig.common.configobjects import CLUSTER_TYPE
from netaddr import IPRange
from controllerconfig.utils import lag_mode_to_str
from controllerconfig.utils import validate_network_str
from controllerconfig.utils import check_network_overlap
from controllerconfig.utils import is_mtu_valid
from controllerconfig.utils import get_service
from controllerconfig.utils import get_optional
from controllerconfig.utils import validate_address_str
from controllerconfig.utils import validate_nameserver_address_str
from controllerconfig.utils import is_valid_url
from controllerconfig.utils import is_valid_domain_or_ip
from controllerconfig.utils import is_valid_bool_str
from controllerconfig.common.exceptions import ConfigFail
from controllerconfig.common.exceptions import ValidateFail


# Constants
TiS_VERSION = "xxxSW_VERSIONxxx"

# Minimum values for partition sizes
MIN_DATABASE_STORAGE = 20
MIN_IMAGE_STORAGE = 10
MIN_IMAGE_CONVERSIONS_VOLUME = 20

WRSROOT_PASSWD_NO_AGING = 99999

# System mode
SYSTEM_MODE_DUPLEX = "duplex"
SYSTEM_MODE_SIMPLEX = "simplex"
SYSTEM_MODE_DUPLEX_DIRECT = "duplex-direct"

DISTRIBUTED_CLOUD_ROLE_SYSTEMCONTROLLER = 'systemcontroller'
DISTRIBUTED_CLOUD_ROLE_SUBCLOUD = 'subcloud'

# System type
SYSTEM_TYPE_AIO = "All-in-one"
SYSTEM_TYPE_STANDARD = "Standard"


class ConfigValidator(object):

    def __init__(self, system_config, cgcs_config, config_type, offboard,
                 naming_type=DEFAULT_NAMES):
        """
        :param system_config: system configuration
        :param cgcs_config: if not None config data should be returned
        :param config_type: indicates whether it is system, region or subcloud
        config
        :param offboard: if true only perform general error checking
        :return:
        """
        self.conf = system_config
        self.cgcs_conf = cgcs_config
        self.config_type = config_type
        self.naming_type = naming_type
        self.offboard = offboard
        self.next_lag_index = 0
        self.configured_networks = []
        self.configured_vlans = []
        self.pxeboot_network_configured = False
        self.pxeboot_section_name = None
        self.management_interface = None
        self.cluster_interface = None
        self.mgmt_network = None
        self.cluster_network = None
        self.oam_network = None
        self.vswitch_type = None
        self.system_mode = None
        self.system_type = None
        self.system_dc_role = None

    def is_simplex_cpe(self):
        return self.system_mode == SYSTEM_MODE_SIMPLEX

    def is_subcloud(self):
        return self.system_dc_role == DISTRIBUTED_CLOUD_ROLE_SUBCLOUD

    def set_system_mode(self, mode):
        self.system_mode = mode

    def set_system_dc_role(self, dc_role):
        self.system_dc_role = dc_role

    def set_oam_config(self, use_lag, external_oam_interface_name):
        if self.cgcs_conf is not None:
            self.cgcs_conf.add_section('cEXT_OAM')
            self.cgcs_conf.set('cEXT_OAM', 'EXTERNAL_OAM_MTU',
                               self.oam_network.logical_interface.mtu)
            self.cgcs_conf.set('cEXT_OAM', 'EXTERNAL_OAM_SUBNET',
                               self.oam_network.cidr)
            if use_lag:
                self.cgcs_conf.set('cEXT_OAM', 'LAG_EXTERNAL_OAM_INTERFACE',
                                   'yes')
                self.cgcs_conf.set('cEXT_OAM', 'EXTERNAL_OAM_BOND_MEMBER_0',
                                   self.oam_network.logical_interface.ports[0])
                self.cgcs_conf.set('cEXT_OAM', 'EXTERNAL_OAM_BOND_MEMBER_1',
                                   self.oam_network.logical_interface.ports[1])
                self.cgcs_conf.set('cEXT_OAM', 'EXTERNAL_OAM_BOND_POLICY',
                                   lag_mode_to_str(self.oam_network.
                                                   logical_interface.lag_mode))
            else:
                self.cgcs_conf.set('cEXT_OAM', 'LAG_EXTERNAL_OAM_INTERFACE',
                                   'no')
            self.cgcs_conf.set('cEXT_OAM', 'EXTERNAL_OAM_INTERFACE',
                               external_oam_interface_name)
            if self.oam_network.vlan:
                self.cgcs_conf.set('cEXT_OAM', 'EXTERNAL_OAM_VLAN',
                                   str(self.oam_network.vlan))
                external_oam_interface_name += '.' + str(self.oam_network.vlan)

            self.cgcs_conf.set('cEXT_OAM', 'EXTERNAL_OAM_INTERFACE_NAME',
                               external_oam_interface_name)
            if self.oam_network.gateway_address:
                self.cgcs_conf.set('cEXT_OAM', 'EXTERNAL_OAM_GATEWAY_ADDRESS',
                                   str(self.oam_network.gateway_address))
            if self.system_mode == SYSTEM_MODE_SIMPLEX:
                self.cgcs_conf.set('cEXT_OAM', 'EXTERNAL_OAM_FLOATING_ADDRESS',
                                   str(self.oam_network.floating_address))
                self.cgcs_conf.set('cEXT_OAM', 'EXTERNAL_OAM_0_ADDRESS',
                                   str(self.oam_network.address_0))
                self.cgcs_conf.set('cEXT_OAM', 'EXTERNAL_OAM_1_ADDRESS',
                                   str(self.oam_network.address_1))
            else:
                self.cgcs_conf.set('cEXT_OAM', 'EXTERNAL_OAM_FLOATING_ADDRESS',
                                   str(self.oam_network.floating_address or
                                       self.oam_network.start_address))
                self.cgcs_conf.set('cEXT_OAM', 'EXTERNAL_OAM_0_ADDRESS',
                                   str(self.oam_network.address_0 or
                                       self.oam_network.start_address + 1))
                self.cgcs_conf.set('cEXT_OAM', 'EXTERNAL_OAM_1_ADDRESS',
                                   str(self.oam_network.address_1 or
                                       self.oam_network.start_address + 2))

    def process_oam_on_its_own_interface(self):
        use_lag = False
        oam_prefix = NETWORK_PREFIX_NAMES[self.naming_type][OAM_TYPE]
        # OAM on its own LAG interface
        if self.oam_network.logical_interface.lag_interface:
            if self.oam_network.logical_interface.lag_mode not in (1, 2, 4):
                raise ConfigFail(
                    "Unsupported LAG mode (%d) for %s interface"
                    " - use LAG mode 1, 2, or 4 instead" %
                    (self.oam_network.logical_interface.lag_mode, oam_prefix))
            use_lag = True
            external_oam_interface = 'bond' + str(self.next_lag_index)
        else:
            # CAN on its own non-LAG interface
            external_oam_interface = (
                self.oam_network.logical_interface.ports[0])
        return use_lag, external_oam_interface

    def validate_oam_common(self):
        # validate OAM network
        self.oam_network = Network()
        if self.is_simplex_cpe():
            min_addresses = 1
        else:
            min_addresses = 3
        try:
            self.oam_network.parse_config(self.conf, self.config_type,
                                          OAM_TYPE,
                                          min_addresses=min_addresses,
                                          multicast_addresses=0,
                                          naming_type=self.naming_type)
        except ConfigFail:
            raise
        except Exception as e:
            raise ConfigFail("Error parsing configuration file: %s" % e)

    def validate_aio_simplex_mgmt(self):
        # AIO simplex management network configuration
        mgmt_prefix = NETWORK_PREFIX_NAMES[self.naming_type][MGMT_TYPE]
        self.mgmt_network = Network()

        min_addresses = 16

        try:
            self.mgmt_network.parse_config(self.conf, self.config_type,
                                           MGMT_TYPE,
                                           min_addresses=min_addresses,
                                           multicast_addresses=0,
                                           naming_type=self.naming_type,
                                           logical_interface_required=False)

        except ConfigFail:
            raise
        except Exception as e:
            raise ConfigFail("Error parsing configuration file: %s" % e)

        if self.mgmt_network.vlan or self.mgmt_network.multicast_cidr or \
                self.mgmt_network.start_end_in_config or \
                self.mgmt_network.floating_address or \
                self.mgmt_network.address_0 or self.mgmt_network.address_1 or \
                self.mgmt_network.dynamic_allocation or \
                self.mgmt_network.gateway_address or \
                self.mgmt_network.logical_interface:
            raise ConfigFail("For AIO simplex, only the %s network CIDR can "
                             "be specified" % mgmt_prefix)

        if self.mgmt_network.cidr.version == 6:
            raise ConfigFail("IPv6 management network not supported on "
                             "simplex configuration.")

        if self.cgcs_conf is not None:
            self.cgcs_conf.add_section('cMGMT')
            self.cgcs_conf.set('cMGMT', 'MANAGEMENT_SUBNET',
                               self.mgmt_network.cidr)

    def validate_aio_network(self, subcloud=False):
        if not subcloud:
            # AIO-SX subcloud supports MGMT_NETWORK & PXEBOOT_NETWORK
            if self.conf.has_section('PXEBOOT_NETWORK'):
                raise ConfigFail("PXEBoot Network configuration is not "
                                 "supported.")
            if self.conf.has_section('MGMT_NETWORK'):
                self.validate_aio_simplex_mgmt()
        if self.conf.has_section('BOARD_MANAGEMENT_NETWORK'):
            raise ConfigFail("Board Management Network configuration is not "
                             "supported.")
        # validate OAM network
        oam_prefix = NETWORK_PREFIX_NAMES[self.naming_type][OAM_TYPE]
        self.validate_oam_common()
        (use_lag, external_oam_interface_name) = (
            self.process_oam_on_its_own_interface())

        # Ensure that the gateway was configured
        if self.oam_network.gateway_address is None:
            raise ConfigFail(
                "No gateway specified -  %s_GATEWAY must be specified"
                % oam_prefix)

        # Check overlap with management network
        if self.mgmt_network is not None:
            try:
                self.configured_networks.append(self.mgmt_network.cidr)
                check_network_overlap(self.oam_network.cidr,
                                      self.configured_networks)
            except ValidateFail:
                raise ConfigFail("%s CIDR %s overlaps with another configured "
                                 "network" %
                                 (oam_prefix, str(self.mgmt_network.cidr)))

        self.set_oam_config(use_lag, external_oam_interface_name)

    def validate_version(self):
        if self.offboard:
            version = TiS_VERSION
        else:
            from tsconfig.tsconfig import SW_VERSION
            version = SW_VERSION

        if not self.conf.has_option('VERSION', 'RELEASE'):
            raise ConfigFail(
                "Version information is missing from this config file. Please"
                " refer to the installation documentation for details on "
                "the correct contents of the configuration file.")
        ini_version = self.conf.get('VERSION', 'RELEASE')
        if version != ini_version:
            raise ConfigFail(
                "The configuration file given is of a different version (%s) "
                "than the installed software (%s). Please refer to the "
                "installation documentation for details on the correct "
                "contents of the configuration file and update it with "
                "any changes required for this release." %
                (ini_version, version))

    def validate_system(self):
        # timezone section
        timezone = 'UTC'
        if self.conf.has_option('SYSTEM', 'TIMEZONE'):
            timezone = self.conf.get('SYSTEM', 'TIMEZONE')

        # system type section
        if self.conf.has_option("SYSTEM", "SYSTEM_TYPE"):
            self.system_type = self.conf.get("SYSTEM", "SYSTEM_TYPE")
            available_system_types = [
                SYSTEM_TYPE_STANDARD,
                SYSTEM_TYPE_AIO
            ]
            if self.system_type not in available_system_types:
                raise ConfigFail("Available options for SYSTEM_TYPE are: %s" %
                                 available_system_types)
        elif not self.offboard:
            from tsconfig.tsconfig import system_type
            self.system_type = system_type

        # system mode section
        if self.conf.has_option("SYSTEM", "SYSTEM_MODE"):
            self.system_mode = self.conf.get("SYSTEM", "SYSTEM_MODE")
            available_system_modes = [SYSTEM_MODE_DUPLEX]
            if self.system_type != SYSTEM_TYPE_STANDARD:
                available_system_modes.append(SYSTEM_MODE_SIMPLEX)
                available_system_modes.append(SYSTEM_MODE_DUPLEX_DIRECT)
            if self.system_mode not in available_system_modes:
                raise ConfigFail("Available options for SYSTEM_MODE are: %s" %
                                 available_system_modes)
        else:
            if self.system_type == SYSTEM_TYPE_STANDARD:
                self.system_mode = SYSTEM_MODE_DUPLEX
            else:
                self.system_mode = SYSTEM_MODE_DUPLEX_DIRECT

        if self.conf.has_option("SYSTEM", "DISTRIBUTED_CLOUD_ROLE"):
            self.system_dc_role = \
                self.conf.get("SYSTEM", "DISTRIBUTED_CLOUD_ROLE")
            if self.config_type == SUBCLOUD_CONFIG:
                available_dc_role = [DISTRIBUTED_CLOUD_ROLE_SUBCLOUD]
            elif self.config_type != REGION_CONFIG:
                available_dc_role = [DISTRIBUTED_CLOUD_ROLE_SYSTEMCONTROLLER]
            else:
                raise ConfigFail("DISTRIBUTED_CLOUD_ROLE option is "
                                 "not avaialbe for this configuration")

            if self.system_dc_role not in available_dc_role:
                raise ConfigFail(
                    "Available options for DISTRIBUTED_CLOUD_ROLE are: %s" %
                    available_dc_role)

            if (self.system_dc_role ==
                    DISTRIBUTED_CLOUD_ROLE_SYSTEMCONTROLLER and
                    self.system_type == SYSTEM_TYPE_AIO):
                raise ConfigFail("An All-in-one controller cannot be "
                                 "configured as Distributed Cloud "
                                 "System Controller")
        elif self.config_type == SUBCLOUD_CONFIG:
            self.system_dc_role = DISTRIBUTED_CLOUD_ROLE_SUBCLOUD
        else:
            self.system_dc_role = None

        if self.cgcs_conf is not None:
            self.cgcs_conf.add_section("cSYSTEM")
            self.cgcs_conf.set("cSYSTEM", "TIMEZONE", timezone)
            self.cgcs_conf.set("cSYSTEM", "SYSTEM_MODE", self.system_mode)
            if self.system_dc_role is not None:
                self.cgcs_conf.set("cSYSTEM", "DISTRIBUTED_CLOUD_ROLE",
                                   self.system_dc_role)

    def validate_storage(self):
        if (self.conf.has_option('STORAGE', 'DATABASE_STORAGE') or
                self.conf.has_option('STORAGE', 'IMAGE_STORAGE') or
                self.conf.has_option('STORAGE', 'BACKUP_STORAGE') or
                self.conf.has_option('STORAGE', 'IMAGE_CONVERSIONS_VOLUME') or
                self.conf.has_option('STORAGE', 'SHARED_INSTANCE_STORAGE') or
                self.conf.has_option('STORAGE', 'CINDER_BACKEND') or
                self.conf.has_option('STORAGE', 'CINDER_DEVICE') or
                self.conf.has_option('STORAGE', 'CINDER_LVM_TYPE') or
                self.conf.has_option('STORAGE', 'CINDER_STORAGE')):
            msg = "DATABASE_STORAGE, IMAGE_STORAGE, BACKUP_STORAGE, " + \
                "IMAGE_CONVERSIONS_VOLUME, SHARED_INSTANCE_STORAGE, " + \
                "CINDER_BACKEND, CINDER_DEVICE, CINDER_LVM_TYPE, " + \
                "CINDER_STORAGE " + \
                "are not valid entries in config file."
            raise ConfigFail(msg)

    def validate_pxeboot(self):
        # PXEBoot network configuration
        start_end_in_config = False

        if self.config_type in [REGION_CONFIG, SUBCLOUD_CONFIG]:
            self.pxeboot_section_name = 'REGION2_PXEBOOT_NETWORK'
        else:
            self.pxeboot_section_name = 'PXEBOOT_NETWORK'

        if self.conf.has_section(self.pxeboot_section_name):
            pxeboot_cidr_str = self.conf.get(self.pxeboot_section_name,
                                             'PXEBOOT_CIDR')
            try:
                pxeboot_subnet = validate_network_str(pxeboot_cidr_str, 16)
                if pxeboot_subnet.version != 4:
                    raise ValidateFail("Invalid PXEBOOT_NETWORK IP version - "
                                       "only IPv4 supported")
                self.configured_networks.append(pxeboot_subnet)
                pxeboot_start_address = None
                pxeboot_end_address = None
                if self.conf.has_option(self.pxeboot_section_name,
                                        "IP_START_ADDRESS"):
                    start_addr_str = self.conf.get(self.pxeboot_section_name,
                                                   "IP_START_ADDRESS")
                    pxeboot_start_address = validate_address_str(
                        start_addr_str, pxeboot_subnet
                    )

                if self.conf.has_option(self.pxeboot_section_name,
                                        "IP_END_ADDRESS"):
                    end_addr_str = self.conf.get(self.pxeboot_section_name,
                                                 "IP_END_ADDRESS")
                    pxeboot_end_address = validate_address_str(
                        end_addr_str, pxeboot_subnet
                    )

                if pxeboot_start_address or pxeboot_end_address:
                    if not pxeboot_end_address:
                        raise ConfigFail("Missing attribute %s for %s" %
                                         ('IP_END_ADDRESS',
                                          self.pxeboot_section_name))

                    if not pxeboot_start_address:
                        raise ConfigFail("Missing attribute %s for %s" %
                                         ('IP_START_ADDRESS',
                                          self.pxeboot_section_name))

                    if not pxeboot_start_address < pxeboot_end_address:
                        raise ConfigFail("Start address %s not "
                                         "less than end address %s for %s."
                                         % (start_addr_str,
                                            end_addr_str,
                                            self.pxeboot_section_name))

                    min_addresses = 8
                    if not IPRange(start_addr_str, end_addr_str).size >= \
                            min_addresses:
                        raise ConfigFail("Address range for %s must contain "
                                         "at least %d addresses." %
                                         (self.pxeboot_section_name,
                                          min_addresses))
                    start_end_in_config = True

                self.pxeboot_network_configured = True
            except ValidateFail as e:
                raise ConfigFail("Invalid PXEBOOT_CIDR value of %s for %s."
                                 "\nReason: %s" %
                                 (pxeboot_cidr_str,
                                  self.pxeboot_section_name, e))

        if self.cgcs_conf is not None:
            self.cgcs_conf.add_section('cPXEBOOT')
            if self.pxeboot_network_configured:
                self.cgcs_conf.set('cPXEBOOT', 'PXEBOOT_SUBNET',
                                   str(pxeboot_subnet))
                if start_end_in_config:
                    self.cgcs_conf.set("cPXEBOOT",
                                       "PXEBOOT_START_ADDRESS",
                                       start_addr_str)
                    self.cgcs_conf.set("cPXEBOOT",
                                       "PXEBOOT_END_ADDRESS",
                                       end_addr_str)

                    pxeboot_floating_addr = pxeboot_start_address
                    pxeboot_controller_addr_0 = pxeboot_start_address + 1
                    pxeboot_controller_addr_1 = pxeboot_controller_addr_0 + 1
                else:
                    pxeboot_floating_addr = pxeboot_subnet[2]
                    pxeboot_controller_addr_0 = pxeboot_subnet[3]
                    pxeboot_controller_addr_1 = pxeboot_subnet[4]
                self.cgcs_conf.set('cPXEBOOT',
                                   'CONTROLLER_PXEBOOT_FLOATING_ADDRESS',
                                   str(pxeboot_floating_addr))
                self.cgcs_conf.set('cPXEBOOT', 'CONTROLLER_PXEBOOT_ADDRESS_0',
                                   str(pxeboot_controller_addr_0))
                self.cgcs_conf.set('cPXEBOOT', 'CONTROLLER_PXEBOOT_ADDRESS_1',
                                   str(pxeboot_controller_addr_1))
            self.cgcs_conf.set('cPXEBOOT', 'PXECONTROLLER_FLOATING_HOSTNAME',
                               'pxecontroller')

    def validate_mgmt(self):
        # Management network configuration
        mgmt_prefix = NETWORK_PREFIX_NAMES[self.naming_type][MGMT_TYPE]
        self.mgmt_network = Network()

        if self.config_type == SUBCLOUD_CONFIG:
            min_addresses = 5
        else:
            min_addresses = 8

        try:
            self.mgmt_network.parse_config(self.conf, self.config_type,
                                           MGMT_TYPE,
                                           min_addresses=min_addresses,
                                           multicast_addresses=16,
                                           naming_type=self.naming_type)
        except ConfigFail:
            raise
        except Exception as e:
            raise ConfigFail("Error parsing configuration file: %s" % e)

        if self.mgmt_network.floating_address:
            raise ConfigFail("%s network cannot specify individual unit "
                             "addresses" % mgmt_prefix)

        if not self.mgmt_network.multicast_cidr:
            # The MULTICAST_CIDR is optional for subclouds (default is used)
            if self.config_type != SUBCLOUD_CONFIG:
                raise ConfigFail("%s MULTICAST_CIDR attribute is missing."
                                 % mgmt_prefix)

        try:
            check_network_overlap(self.mgmt_network.cidr,
                                  self.configured_networks)
            self.configured_networks.append(self.mgmt_network.cidr)
        except ValidateFail:
            raise ConfigFail("%s CIDR %s overlaps with another configured "
                             "network" %
                             (mgmt_prefix, str(self.mgmt_network.cidr)))

        if (self.system_dc_role ==
                DISTRIBUTED_CLOUD_ROLE_SYSTEMCONTROLLER):
            # For Distributed Cloud SystemController, we require the setting
            # of the IP_START_ADDRESS/IP_END_ADDRESS config settings so as to
            # raise awareness that some space in MGMT subnet must be set aside
            # for gateways to reach subclouds.

            if not self.mgmt_network.start_end_in_config:
                raise ConfigFail("IP_START_ADDRESS and IP_END_ADDRESS required"
                                 " for %s network as this configuration "
                                 "requires address space left for gateway "
                                 "address(es)" % mgmt_prefix)
            else:
                # Warn user that some space in the management subnet must
                # be reserved for the system controller gateway address(es)
                # used to communicate with the subclouds. - 2 because of
                # subnet and broadcast addresses.
                address_range = \
                    IPRange(str(self.mgmt_network.start_address),
                            str(self.mgmt_network.end_address)).size

                if address_range >= (self.mgmt_network.cidr.size - 2):
                    raise ConfigFail(
                        "Address range for %s network too large, no addresses"
                        " left for gateway(s), required in this "
                        "configuration." % mgmt_prefix)

        if self.mgmt_network.logical_interface.lag_interface:
            supported_lag_mode = [1, 4]
            if (self.mgmt_network.logical_interface.lag_mode not in
                    supported_lag_mode):
                raise ConfigFail("Unsupported LAG mode (%d) for %s interface"
                                 " - use LAG mode %s instead" %
                                 (self.mgmt_network.logical_interface.lag_mode,
                                  mgmt_prefix, supported_lag_mode))

            self.management_interface = 'bond' + str(self.next_lag_index)
            management_interface_name = self.management_interface
            self.next_lag_index += 1
        else:
            self.management_interface = (
                self.mgmt_network.logical_interface.ports[0])
            management_interface_name = self.management_interface

        if self.mgmt_network.vlan:
            if not self.pxeboot_network_configured:
                raise ConfigFail(
                    "Management VLAN cannot be configured because "
                    "PXEBOOT_NETWORK is not configured.")
            self.configured_vlans.append(self.mgmt_network.vlan)
            management_interface_name += '.' + str(self.mgmt_network.vlan)
        elif self.pxeboot_network_configured:
            raise ConfigFail(
                "Management VLAN must be configured because "
                "%s configured." % self.pxeboot_section_name)

        if not self.is_simplex_cpe() and self.mgmt_network.cidr.version == 6 \
                and not self.pxeboot_network_configured:
            raise ConfigFail("IPv6 management network cannot be configured "
                             "because PXEBOOT_NETWORK is not configured.")

        mtu = self.mgmt_network.logical_interface.mtu
        if not is_mtu_valid(mtu):
            raise ConfigFail(
                "Invalid MTU value of %s for %s. "
                "Valid values: 576 - 9216"
                % (mtu, self.mgmt_network.logical_interface.name))

        if self.cgcs_conf is not None:
            self.cgcs_conf.add_section('cMGMT')
            self.cgcs_conf.set('cMGMT', 'MANAGEMENT_MTU',
                               self.mgmt_network.logical_interface.mtu)
            self.cgcs_conf.set('cMGMT', 'MANAGEMENT_SUBNET',
                               self.mgmt_network.cidr)
            if self.mgmt_network.logical_interface.lag_interface:
                self.cgcs_conf.set('cMGMT', 'LAG_MANAGEMENT_INTERFACE', 'yes')
                self.cgcs_conf.set(
                    'cMGMT', 'MANAGEMENT_BOND_MEMBER_0',
                    self.mgmt_network.logical_interface.ports[0])
                self.cgcs_conf.set(
                    'cMGMT', 'MANAGEMENT_BOND_MEMBER_1',
                    self.mgmt_network.logical_interface.ports[1])
                self.cgcs_conf.set('cMGMT', 'MANAGEMENT_BOND_POLICY',
                                   lag_mode_to_str(self.mgmt_network.
                                                   logical_interface.lag_mode))
            else:
                self.cgcs_conf.set('cMGMT', 'LAG_MANAGEMENT_INTERFACE', 'no')
            self.cgcs_conf.set('cMGMT', 'MANAGEMENT_INTERFACE',
                               self.management_interface)

            if self.mgmt_network.vlan:
                self.cgcs_conf.set('cMGMT', 'MANAGEMENT_VLAN',
                                   str(self.mgmt_network.vlan))

            self.cgcs_conf.set('cMGMT', 'MANAGEMENT_INTERFACE_NAME',
                               management_interface_name)

            if self.mgmt_network.gateway_address:
                self.cgcs_conf.set('cMGMT', 'MANAGEMENT_GATEWAY_ADDRESS',
                                   str(self.mgmt_network.gateway_address))

            self.cgcs_conf.set('cMGMT', 'CONTROLLER_FLOATING_ADDRESS',
                               str(self.mgmt_network.start_address))
            self.cgcs_conf.set('cMGMT', 'CONTROLLER_0_ADDRESS',
                               str(self.mgmt_network.start_address + 1))
            self.cgcs_conf.set('cMGMT', 'CONTROLLER_1_ADDRESS',
                               str(self.mgmt_network.start_address + 2))
            first_nfs_ip = self.mgmt_network.start_address + 3
            self.cgcs_conf.set('cMGMT', 'NFS_MANAGEMENT_ADDRESS_1',
                               str(first_nfs_ip))
            self.cgcs_conf.set('cMGMT', 'NFS_MANAGEMENT_ADDRESS_2',
                               str(first_nfs_ip + 1))
            self.cgcs_conf.set('cMGMT', 'CONTROLLER_FLOATING_HOSTNAME',
                               'controller')
            self.cgcs_conf.set('cMGMT', 'CONTROLLER_HOSTNAME_PREFIX',
                               'controller-')
            self.cgcs_conf.set('cMGMT', 'OAMCONTROLLER_FLOATING_HOSTNAME',
                               'oamcontroller')
            if self.mgmt_network.dynamic_allocation:
                self.cgcs_conf.set('cMGMT', 'DYNAMIC_ADDRESS_ALLOCATION',
                                   "yes")
            else:
                self.cgcs_conf.set('cMGMT', 'DYNAMIC_ADDRESS_ALLOCATION',
                                   "no")
            if self.mgmt_network.start_address and \
                    self.mgmt_network.end_address:
                self.cgcs_conf.set('cMGMT', 'MANAGEMENT_START_ADDRESS',
                                   self.mgmt_network.start_address)
                self.cgcs_conf.set('cMGMT', 'MANAGEMENT_END_ADDRESS',
                                   self.mgmt_network.end_address)
            if self.mgmt_network.multicast_cidr:
                self.cgcs_conf.set('cMGMT', 'MANAGEMENT_MULTICAST_SUBNET',
                                   self.mgmt_network.multicast_cidr)

    def validate_cluster(self):
        # Kubernetes cluster network configuration
        cluster_prefix = NETWORK_PREFIX_NAMES[self.naming_type][CLUSTER_TYPE]
        if not self.conf.has_section(cluster_prefix + '_NETWORK'):
            return
        self.cluster_network = Network()
        try:
            self.cluster_network.parse_config(self.conf, self.config_type,
                                              CLUSTER_TYPE,
                                              min_addresses=8,
                                              naming_type=self.naming_type)
        except ConfigFail:
            raise
        except Exception as e:
            raise ConfigFail("Error parsing configuration file: %s" % e)

        if self.cluster_network.floating_address:
            raise ConfigFail("%s network cannot specify individual unit "
                             "addresses" % cluster_prefix)

        try:
            check_network_overlap(self.cluster_network.cidr,
                                  self.configured_networks)
            self.configured_networks.append(self.cluster_network.cidr)
        except ValidateFail:
            raise ConfigFail("%s CIDR %s overlaps with another configured "
                             "network" %
                             (cluster_prefix, str(self.cluster_network.cidr)))

        if self.cluster_network.logical_interface.lag_interface:
            supported_lag_mode = [1, 2, 4]
            if (self.cluster_network.logical_interface.lag_mode not in
                    supported_lag_mode):
                raise ConfigFail(
                    "Unsupported LAG mode (%d) for %s interface"
                    " - use LAG mode %s instead" %
                    (self.cluster_network.logical_interface.lag_mode,
                     cluster_prefix, supported_lag_mode))

            self.cluster_interface = 'bond' + str(self.next_lag_index)
            cluster_interface_name = self.cluster_interface
            self.next_lag_index += 1
        else:
            self.cluster_interface = (
                self.cluster_network.logical_interface.ports[0])
            cluster_interface_name = self.cluster_interface

        if self.cluster_network.vlan:
            if any(self.cluster_network.vlan == vlan for vlan in
                   self.configured_vlans):
                raise ConfigFail(
                    "%s_NETWORK VLAN conflicts with another configured "
                    "VLAN" % cluster_prefix)
            self.configured_vlans.append(self.cluster_network.vlan)
            cluster_interface_name += '.' + str(self.cluster_network.vlan)

        mtu = self.cluster_network.logical_interface.mtu
        if not is_mtu_valid(mtu):
            raise ConfigFail(
                "Invalid MTU value of %s for %s. "
                "Valid values: 576 - 9216"
                % (mtu, self.cluster_network.logical_interface.name))

        if self.cgcs_conf is not None:
            self.cgcs_conf.add_section('cCLUSTER')
            self.cgcs_conf.set('cCLUSTER', 'CLUSTER_MTU',
                               self.cluster_network.logical_interface.mtu)
            self.cgcs_conf.set('cCLUSTER', 'CLUSTER_SUBNET',
                               self.cluster_network.cidr)
            if self.cluster_network.logical_interface.lag_interface:
                self.cgcs_conf.set('cCLUSTER', 'LAG_CLUSTER_INTERFACE', 'yes')
                self.cgcs_conf.set(
                    'cCLUSTER', 'CLUSTER_BOND_MEMBER_0',
                    self.cluster_network.logical_interface.ports[0])
                self.cgcs_conf.set(
                    'cCLUSTER', 'CLUSTER_BOND_MEMBER_1',
                    self.cluster_network.logical_interface.ports[1])
                self.cgcs_conf.set('cCLUSTER', 'CLUSTER_BOND_POLICY',
                                   lag_mode_to_str(self.cluster_network.
                                                   logical_interface.lag_mode))
            else:
                self.cgcs_conf.set('cCLUSTER', 'LAG_CLUSTER_INTERFACE', 'no')
            self.cgcs_conf.set('cCLUSTER', 'CLUSTER_INTERFACE',
                               self.cluster_interface)

            if self.cluster_network.vlan:
                self.cgcs_conf.set('cCLUSTER', 'CLUSTER_VLAN',
                                   str(self.cluster_network.vlan))

            self.cgcs_conf.set('cCLUSTER', 'CLUSTER_INTERFACE_NAME',
                               cluster_interface_name)

            if self.cluster_network.dynamic_allocation:
                self.cgcs_conf.set('cCLUSTER', 'DYNAMIC_ADDRESS_ALLOCATION',
                                   "yes")
            else:
                self.cgcs_conf.set('cCLUSTER', 'DYNAMIC_ADDRESS_ALLOCATION',
                                   "no")

    def validate_oam(self):
        # OAM network configuration
        oam_prefix = NETWORK_PREFIX_NAMES[self.naming_type][OAM_TYPE]
        mgmt_prefix = NETWORK_PREFIX_NAMES[self.naming_type][MGMT_TYPE]
        self.validate_oam_common()
        try:
            check_network_overlap(self.oam_network.cidr,
                                  self.configured_networks)
            self.configured_networks.append(self.oam_network.cidr)
        except ValidateFail:
            raise ConfigFail(
                "%s CIDR %s overlaps with another configured network" %
                (oam_prefix, str(self.oam_network.cidr)))

        use_lag = False
        if (self.oam_network.logical_interface.name ==
                self.mgmt_network.logical_interface.name):
            # CAN sharing CLM interface
            external_oam_interface = self.management_interface
        elif (self.cluster_network and
              (self.oam_network.logical_interface.name ==
               self.cluster_network.logical_interface.name)):
            # CAN sharing BLS interface
            external_oam_interface = self.cluster_interface
        else:
            (use_lag, external_oam_interface) = (
                self.process_oam_on_its_own_interface())
        external_oam_interface_name = external_oam_interface

        if self.oam_network.vlan:
            if any(self.oam_network.vlan == vlan for vlan in
                   self.configured_vlans):
                raise ConfigFail(
                    "%s_NETWORK VLAN conflicts with another configured VLAN" %
                    oam_prefix)
            self.configured_vlans.append(self.oam_network.vlan)
        elif external_oam_interface in (self.management_interface,
                                        self.cluster_interface):
            raise ConfigFail(
                "VLAN required for %s_NETWORK since it uses the same interface"
                " as another network" % oam_prefix)

        # Ensure that exactly one gateway was configured
        if (self.mgmt_network.gateway_address is None and self.oam_network.
                gateway_address is None):
            raise ConfigFail(
                "No gateway specified - either the %s_GATEWAY or %s_GATEWAY "
                "must be specified" % (mgmt_prefix, oam_prefix))
        elif self.mgmt_network.gateway_address and (
                self.oam_network.gateway_address):
            # In subcloud configs we support both a management and OAM gateway
            if self.config_type != SUBCLOUD_CONFIG:
                raise ConfigFail(
                    "Two gateways specified - only one of the %s_GATEWAY or "
                    "%s_GATEWAY can be specified" % (mgmt_prefix, oam_prefix))
        self.set_oam_config(use_lag, external_oam_interface_name)

    def validate_sdn(self):
        if self.conf.has_section('SDN'):
            raise ConfigFail("SDN Configuration is no longer supported")

    def validate_dns(self):
        # DNS config is optional
        if not self.conf.has_section('DNS'):
            return
        if self.cgcs_conf is not None:
            self.cgcs_conf.add_section('cDNS')
        for x in range(0, 3):
            if self.conf.has_option('DNS', 'NAMESERVER_' + str(x + 1)):
                dns_address_str = self.conf.get('DNS', 'NAMESERVER_' + str(
                    x + 1))
                try:
                    dns_address = validate_nameserver_address_str(
                        dns_address_str)
                    if self.cgcs_conf is not None:
                        self.cgcs_conf.set('cDNS', 'NAMESERVER_' + str(x + 1),
                                           str(dns_address))
                except ValidateFail as e:
                    raise ConfigFail(
                        "Invalid DNS NAMESERVER value of %s.\nReason: %s" %
                        (dns_address_str, e))

    def validate_docker_proxy(self):
        if not self.conf.has_section('DOCKER_PROXY'):
            return
        if self.cgcs_conf is not None:
            self.cgcs_conf.add_section('cDOCKER_PROXY')
        # check http_proxy
        if self.conf.has_option('DOCKER_PROXY', 'DOCKER_HTTP_PROXY'):
            docker_http_proxy_str = self.conf.get(
                'DOCKER_PROXY', 'DOCKER_HTTP_PROXY')
            if is_valid_url(docker_http_proxy_str):
                if self.cgcs_conf is not None:
                    self.cgcs_conf.set('cDOCKER_PROXY', 'DOCKER_HTTP_PROXY',
                                       docker_http_proxy_str)
            else:
                raise ConfigFail(
                    "Invalid DOCKER_HTTP_PROXY value of %s." %
                    docker_http_proxy_str)
        # check https_proxy
        if self.conf.has_option('DOCKER_PROXY', 'DOCKER_HTTPS_PROXY'):
            docker_https_proxy_str = self.conf.get(
                'DOCKER_PROXY', 'DOCKER_HTTPS_PROXY')
            if is_valid_url(docker_https_proxy_str):
                if self.cgcs_conf is not None:
                    self.cgcs_conf.set('cDOCKER_PROXY', 'DOCKER_HTTPS_PROXY',
                                       docker_https_proxy_str)
            else:
                raise ConfigFail(
                    "Invalid DOCKER_HTTPS_PROXY value of %s." %
                    docker_https_proxy_str)
        # check no_proxy
        if self.conf.has_option('DOCKER_PROXY', 'DOCKER_NO_PROXY'):
            docker_no_proxy_list_str = self.conf.get(
                'DOCKER_PROXY', 'DOCKER_NO_PROXY')
            docker_no_proxy_list = docker_no_proxy_list_str.split(',')
            for no_proxy_str in docker_no_proxy_list:
                if not is_valid_domain_or_ip(no_proxy_str):
                    raise ConfigFail(
                        "Invalid DOCKER_NO_PROXY value of %s." %
                        no_proxy_str)
            if self.cgcs_conf is not None:
                self.cgcs_conf.set('cDOCKER_PROXY', 'DOCKER_NO_PROXY',
                                   docker_no_proxy_list_str)

    def validate_docker_registry(self):
        if not self.conf.has_section('DOCKER_REGISTRY'):
            return
        if self.cgcs_conf is not None:
            self.cgcs_conf.add_section('cDOCKER_REGISTRY')
        # check k8s_registry
        if self.conf.has_option('DOCKER_REGISTRY', 'DOCKER_K8S_REGISTRY'):
            docker_k8s_registry_str = self.conf.get(
                'DOCKER_REGISTRY', 'DOCKER_K8S_REGISTRY')
            if is_valid_domain_or_ip(docker_k8s_registry_str):
                if self.cgcs_conf is not None:
                    self.cgcs_conf.set('cDOCKER_REGISTRY',
                                       'DOCKER_K8S_REGISTRY',
                                       docker_k8s_registry_str)
            else:
                raise ConfigFail(
                    "Invalid DOCKER_K8S_REGISTRY value of %s." %
                    docker_k8s_registry_str)
        # check gcr_registry
        if self.conf.has_option('DOCKER_REGISTRY', 'DOCKER_GCR_REGISTRY'):
            docker_gcr_registry_str = self.conf.get(
                'DOCKER_REGISTRY', 'DOCKER_GCR_REGISTRY')
            if is_valid_domain_or_ip(docker_gcr_registry_str):
                if self.cgcs_conf is not None:
                    self.cgcs_conf.set('cDOCKER_REGISTRY',
                                       'DOCKER_GCR_REGISTRY',
                                       docker_gcr_registry_str)
            else:
                raise ConfigFail(
                    "Invalid DOCKER_GCR_REGISTRY value of %s." %
                    docker_gcr_registry_str)
        # check quay_registry
        if self.conf.has_option('DOCKER_REGISTRY', 'DOCKER_QUAY_REGISTRY'):
            docker_quay_registry_str = self.conf.get(
                'DOCKER_REGISTRY', 'DOCKER_QUAY_REGISTRY')
            if is_valid_domain_or_ip(docker_quay_registry_str):
                if self.cgcs_conf is not None:
                    self.cgcs_conf.set('cDOCKER_REGISTRY',
                                       'DOCKER_QUAY_REGISTRY',
                                       docker_quay_registry_str)
            else:
                raise ConfigFail(
                    "Invalid DOCKER_QUAY_REGISTRY value of %s." %
                    docker_quay_registry_str)
        # check docker_registry
        if self.conf.has_option('DOCKER_REGISTRY', 'DOCKER_DOCKER_REGISTRY'):
            docker_docker_registry_str = self.conf.get(
                'DOCKER_REGISTRY', 'DOCKER_DOCKER_REGISTRY')
            if is_valid_domain_or_ip(docker_docker_registry_str):
                if self.cgcs_conf is not None:
                    self.cgcs_conf.set('cDOCKER_REGISTRY',
                                       'DOCKER_DOCKER_REGISTRY',
                                       docker_docker_registry_str)
            else:
                raise ConfigFail(
                    "Invalid DOCKER_DOCKER_REGISTRY value of %s." %
                    docker_docker_registry_str)
        # check is_secure_registry
        if self.conf.has_option('DOCKER_REGISTRY', 'IS_SECURE_REGISTRY'):
            docker_is_secure_registry_str = self.conf.get(
                'DOCKER_REGISTRY', 'IS_SECURE_REGISTRY')
            if is_valid_bool_str(docker_is_secure_registry_str):
                if self.cgcs_conf is not None:
                    self.cgcs_conf.set('cDOCKER_REGISTRY',
                                       'IS_SECURE_REGISTRY',
                                       docker_is_secure_registry_str)
            else:
                raise ConfigFail(
                    "Invalid IS_SECURE_REGISTRY value of %s." %
                    docker_is_secure_registry_str)

    def validate_ntp(self):
        if self.conf.has_section('NTP'):
            raise ConfigFail("NTP Configuration is no longer supported")

    def validate_region(self, config_type=REGION_CONFIG):
        region_1_name = self.conf.get('SHARED_SERVICES', 'REGION_NAME')
        region_2_name = self.conf.get('REGION_2_SERVICES', 'REGION_NAME')
        if region_1_name == region_2_name:
            raise ConfigFail(
                "The Region Names must be unique.")

        if not (self.conf.has_option('REGION_2_SERVICES', 'CREATE') and
                self.conf.get('REGION_2_SERVICES', 'CREATE') == 'Y'):
            password_fields = [
                'PATCHING', 'SYSINV', 'FM', 'BARBICAN', 'NFV', 'MTCE'
            ]
            for pw in password_fields:
                if not self.conf.has_option('REGION_2_SERVICES',
                                            pw + '_PASSWORD'):
                    raise ConfigFail("User password for %s is required and "
                                     "missing." % pw)

        admin_user_name = self.conf.get('SHARED_SERVICES', 'ADMIN_USER_NAME')
        if self.conf.has_option('SHARED_SERVICES',
                                'ADMIN_USER_DOMAIN'):
            admin_user_domain = self.conf.get('SHARED_SERVICES',
                                              'ADMIN_USER_DOMAIN')
        else:
            admin_user_domain = DEFAULT_DOMAIN_NAME

        # for now support both ADMIN_PROJECT_NAME and ADMIN_TENANT_NAME
        if self.conf.has_option('SHARED_SERVICES', 'ADMIN_PROJECT_NAME'):
            admin_project_name = self.conf.get('SHARED_SERVICES',
                                               'ADMIN_PROJECT_NAME')
        else:
            admin_project_name = self.conf.get('SHARED_SERVICES',
                                               'ADMIN_TENANT_NAME')
        if self.conf.has_option('SHARED_SERVICES',
                                'ADMIN_PROJECT_DOMAIN'):
            admin_project_domain = self.conf.get('SHARED_SERVICES',
                                                 'ADMIN_PROJECT_DOMAIN')
        else:
            admin_project_domain = DEFAULT_DOMAIN_NAME

        # for now support both SERVICE_PROJECT_NAME and SERVICE_TENANT_NAME
        if self.conf.has_option('SHARED_SERVICES', 'SERVICE_PROJECT_NAME'):
            service_project_name = self.conf.get('SHARED_SERVICES',
                                                 'SERVICE_PROJECT_NAME')
        else:
            service_project_name = self.conf.get('SHARED_SERVICES',
                                                 'SERVICE_TENANT_NAME')
        keystone_service_name = get_service(self.conf, 'SHARED_SERVICES',
                                            'KEYSTONE_SERVICE_NAME')
        keystone_service_type = get_service(self.conf, 'SHARED_SERVICES',
                                            'KEYSTONE_SERVICE_TYPE')

        # validate the patch service name and type
        get_service(self.conf, 'REGION_2_SERVICES', 'PATCHING_SERVICE_NAME')
        get_service(self.conf, 'REGION_2_SERVICES', 'PATCHING_SERVICE_TYPE')
        patch_user_name = self.conf.get('REGION_2_SERVICES',
                                        'PATCHING_USER_NAME')
        patch_password = get_optional(self.conf, 'REGION_2_SERVICES',
                                      'PATCHING_PASSWORD')
        sysinv_user_name = self.conf.get('REGION_2_SERVICES',
                                         'SYSINV_USER_NAME')
        sysinv_password = get_optional(self.conf, 'REGION_2_SERVICES',
                                       'SYSINV_PASSWORD')
        sysinv_service_name = get_service(self.conf, 'REGION_2_SERVICES',
                                          'SYSINV_SERVICE_NAME')
        sysinv_service_type = get_service(self.conf, 'REGION_2_SERVICES',
                                          'SYSINV_SERVICE_TYPE')

        # validate nfv service name and type
        get_service(self.conf, 'REGION_2_SERVICES', 'NFV_SERVICE_NAME')
        get_service(self.conf, 'REGION_2_SERVICES', 'NFV_SERVICE_TYPE')
        nfv_user_name = self.conf.get('REGION_2_SERVICES', 'NFV_USER_NAME')
        nfv_password = get_optional(self.conf, 'REGION_2_SERVICES',
                                    'NFV_PASSWORD')

        # validate mtce user
        mtce_user_name = self.conf.get('REGION_2_SERVICES', 'MTCE_USER_NAME')
        mtce_password = get_optional(self.conf, 'REGION_2_SERVICES',
                                     'MTCE_PASSWORD')

        # validate fm service name and type
        get_service(self.conf, 'REGION_2_SERVICES', 'FM_SERVICE_NAME')
        get_service(self.conf, 'REGION_2_SERVICES', 'FM_SERVICE_TYPE')
        fm_user_name = self.conf.get('REGION_2_SERVICES', 'FM_USER_NAME')
        fm_password = get_optional(self.conf, 'REGION_2_SERVICES',
                                   'FM_PASSWORD')

        # validate barbican service name and type
        get_service(self.conf, 'REGION_2_SERVICES', 'BARBICAN_SERVICE_NAME')
        get_service(self.conf, 'REGION_2_SERVICES', 'BARBICAN_SERVICE_TYPE')
        barbican_user_name = self.conf.get('REGION_2_SERVICES',
                                           'BARBICAN_USER_NAME')
        barbican_password = get_optional(self.conf, 'REGION_2_SERVICES',
                                         'BARBICAN_PASSWORD')

        if self.conf.has_option('REGION_2_SERVICES', 'USER_DOMAIN_NAME'):
            user_domain = self.conf.get('REGION_2_SERVICES',
                                        'USER_DOMAIN_NAME')
        else:
            user_domain = DEFAULT_DOMAIN_NAME
        if self.conf.has_option('REGION_2_SERVICES', 'PROJECT_DOMAIN_NAME'):
            project_domain = self.conf.get('REGION_2_SERVICES',
                                           'PROJECT_DOMAIN_NAME')
        else:
            project_domain = DEFAULT_DOMAIN_NAME

        system_controller_subnet = None
        system_controller_floating_ip = None
        if config_type == SUBCLOUD_CONFIG:
            system_controller_subnet = self.conf.get(
                'SHARED_SERVICES', 'SYSTEM_CONTROLLER_SUBNET')
            system_controller_floating_ip = self.conf.get(
                'SHARED_SERVICES', 'SYSTEM_CONTROLLER_FLOATING_ADDRESS')

        # Create cgcs_config file if specified
        if self.cgcs_conf is not None:
            self.cgcs_conf.add_section('cREGION')
            self.cgcs_conf.set('cREGION', 'REGION_CONFIG', 'True')
            self.cgcs_conf.set('cREGION', 'REGION_1_NAME', region_1_name)
            self.cgcs_conf.set('cREGION', 'REGION_2_NAME', region_2_name)
            self.cgcs_conf.set('cREGION', 'ADMIN_USER_NAME', admin_user_name)
            self.cgcs_conf.set('cREGION', 'ADMIN_USER_DOMAIN',
                               admin_user_domain)
            self.cgcs_conf.set('cREGION', 'ADMIN_PROJECT_NAME',
                               admin_project_name)
            self.cgcs_conf.set('cREGION', 'ADMIN_PROJECT_DOMAIN',
                               admin_project_domain)
            self.cgcs_conf.set('cREGION', 'SERVICE_PROJECT_NAME',
                               service_project_name)
            self.cgcs_conf.set('cREGION', 'KEYSTONE_SERVICE_NAME',
                               keystone_service_name)
            self.cgcs_conf.set('cREGION', 'KEYSTONE_SERVICE_TYPE',
                               keystone_service_type)
            self.cgcs_conf.set('cREGION', 'PATCHING_USER_NAME',
                               patch_user_name)
            self.cgcs_conf.set('cREGION', 'PATCHING_PASSWORD', patch_password)
            self.cgcs_conf.set('cREGION', 'SYSINV_USER_NAME', sysinv_user_name)
            self.cgcs_conf.set('cREGION', 'SYSINV_PASSWORD', sysinv_password)
            self.cgcs_conf.set('cREGION', 'SYSINV_SERVICE_NAME',
                               sysinv_service_name)
            self.cgcs_conf.set('cREGION', 'SYSINV_SERVICE_TYPE',
                               sysinv_service_type)
            self.cgcs_conf.set('cREGION', 'NFV_USER_NAME', nfv_user_name)
            self.cgcs_conf.set('cREGION', 'NFV_PASSWORD', nfv_password)
            self.cgcs_conf.set('cREGION', 'MTCE_USER_NAME', mtce_user_name)
            self.cgcs_conf.set('cREGION', 'MTCE_PASSWORD', mtce_password)
            self.cgcs_conf.set('cREGION', 'FM_USER_NAME', fm_user_name)
            self.cgcs_conf.set('cREGION', 'FM_PASSWORD', fm_password)
            self.cgcs_conf.set('cREGION', 'BARBICAN_USER_NAME',
                               barbican_user_name)
            self.cgcs_conf.set('cREGION', 'BARBICAN_PASSWORD',
                               barbican_password)

            self.cgcs_conf.set('cREGION', 'USER_DOMAIN_NAME',
                               user_domain)
            self.cgcs_conf.set('cREGION', 'PROJECT_DOMAIN_NAME',
                               project_domain)
            if config_type == SUBCLOUD_CONFIG:
                self.cgcs_conf.set('cREGION', 'SYSTEM_CONTROLLER_SUBNET',
                                   system_controller_subnet)
                self.cgcs_conf.set('cREGION',
                                   'SYSTEM_CONTROLLER_FLOATING_ADDRESS',
                                   system_controller_floating_ip)

    def validate_security(self):
        if self.conf.has_section('SECURITY'):
            raise ConfigFail("The section SECURITY is "
                             "no longer supported.")

    def validate_licensing(self):
        if self.conf.has_section('LICENSING'):
            raise ConfigFail("The section LICENSING is no longer supported.")

    def validate_authentication(self):
        if self.config_type in [REGION_CONFIG, SUBCLOUD_CONFIG]:
            password = self.conf.get('SHARED_SERVICES', 'ADMIN_PASSWORD')
        else:
            password = self.conf.get('AUTHENTICATION', 'ADMIN_PASSWORD')
        if self.cgcs_conf is not None:
            self.cgcs_conf.add_section('cAUTHENTICATION')
            self.cgcs_conf.set('cAUTHENTICATION', 'ADMIN_PASSWORD', password)


def validate(system_config, config_type=REGION_CONFIG, cgcs_config=None,
             offboard=False):
    """
    Perform general errors checking on a system configuration file
    :param system_config: system configuration
    :param config_type: indicates whether it is system, region or subcloud
    configuration
    :param cgcs_config: if not None config data should be returned
    :param offboard: if true only perform general error checking
    :return: None
    """
    if config_type == REGION_CONFIG and system_config.has_section(
            'CLM_NETWORK'):
        naming_type = HP_NAMES
    else:
        naming_type = DEFAULT_NAMES
    validator = ConfigValidator(system_config, cgcs_config, config_type,
                                offboard, naming_type)
    # Version configuration
    validator.validate_version()
    # System configuration
    validator.validate_system()
    # Storage configuration
    validator.validate_storage()
    # SDN configuration
    validator.validate_sdn()

    if validator.is_simplex_cpe():
        if validator.is_subcloud():
            # For AIO-SX subcloud, mgmt n/w will be on a separate physical
            # interface or could be on a VLAN interface (on PXEBOOT n/w).
            validator.validate_aio_network(subcloud=True)
            validator.validate_pxeboot()
            validator.validate_mgmt()
        else:
            validator.validate_aio_network()
    else:
        # PXEBoot network configuration
        validator.validate_pxeboot()
        # Management network configuration
        validator.validate_mgmt()
        # OAM network configuration
        validator.validate_oam()
    # Kubernetes Cluster network configuration
    validator.validate_cluster()
    # Neutron configuration - leave blank to use defaults
    # DNS configuration
    validator.validate_dns()
    # Docker Proxy configuration
    validator.validate_docker_proxy()
    # Docker Registry configuration
    validator.validate_docker_registry()
    # NTP configuration
    validator.validate_ntp()
    # Region configuration
    if config_type in [REGION_CONFIG, SUBCLOUD_CONFIG]:
        validator.validate_region(config_type)
    # Security configuration
    validator.validate_security()
    # Licensing configuration
    validator.validate_licensing()
    # Authentication configuration
    validator.validate_authentication()
