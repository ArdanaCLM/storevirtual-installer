#!/usr/bin/python
#
# (c) Copyright 2015-2016 Hewlett Packard Enterprise Development LP
# (c) Copyright 2017 SUSE LLC
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.


import datetime
import fcntl
import json
import os
import re
import shutil
import socket
import struct
import sys
import subprocess
from subprocess import PIPE, Popen
from netaddr import IPNetwork
import logging
import libvirt
import vsa_excs
import glob

LOG = logging.getLogger()


class Deployer:
    """
    Creates necessary infrastructure like bridge, disk configuration file
    etc and creates VSA VM. Also, it provides other functionalities
    like start/stop/update VM.
    """

    def __init__(self, connection, is_AO_capable, disk_file):
        self.hypervisor_conn = connection
        self.is_AO_enabled = is_AO_capable
        self.disk_file = disk_file
        self.CONFIG_DIR = os.environ['VSA_CONFIG_DIR']
        self.VSA_PACKAGE = os.environ['VSA_IMAGE_PATH']
        self.VSA_INSTALLER = os.environ['VSA_INSTALLER']
        self.VSA_NETWORK_CONFIG_FILE = os.environ['VSA_NETWORK_CONFIG_FILE']
        self.VSA_CONFIG_FILE = os.environ['VSA_CONFIG_FILE']

    def install_vsa(self):
        self._pre_install_vsa()
        LOG.info("VSA deployment started")
        try:
            self._read_inputs()
            self._create_installer_input_json()
            self._initialize_default_json()
            self._create_bridge()
            self._create_storage_pool()
            self._create_vsa_vm()
        except vsa_excs.StoragePoolCreationFailed:
            LOG.error("VSA installation failed,rolling back")
            self._vsa_network_destroy()
            sys.exit(1)
        except vsa_excs.VMCreateFailed:
            LOG.error("VSA installation failed,rolling back")
            self._roll_back_installation()
            sys.exit(1)

    def destroy_vsa(self):
        """
        Destroy the VSA that were created.
        """
        try:
            self._read_configuration_input(self.VSA_CONFIG_FILE)
            self._vsa_domain_destroy()
            self._vsa_network_destroy()
            self._vsa_storage_pool_destroy()
            shutil.rmtree(self.pool_location, ignore_errors=True)
            LOG.info("VSA destroy success")
        except IOError as e:
            msg = ("Failed to destroy VSA setup %s" % e.message)
            LOG.error(msg)
            raise vsa_excs.DestroyFailed(msg)

    def _pre_install_vsa(self):
        """
        Check for VSA VM status if exists
        :return:True: Exit the script. No VSA deployment required
                False: Continue with fresh deployment
        """
        self._read_configuration_input(self.VSA_CONFIG_FILE)
        self.vsa_status = self._get_vsa_vm_status()
        if self.vsa_status == 1 or self.vsa_status == 3:
            msg = "The VSA vm is present on the host, exit from installation"
            LOG.info(msg)
            print "%s" % msg
            sys.exit(0)

    def _read_inputs(self):
        self._read_network_input()
        self._read_configuration_input(self.VSA_CONFIG_FILE)

        #
        # As directed, we will either read from input file or
        # discover disks.
        #

        try:
            if self.disk_file:
                self.vsa_disks = self._read_json(self.disk_file)
                self.tiered_disks = self.vsa_disks['vsa_disks']
                self.disks = self._make_list_from_tiered()
            else:
                self.disks = self._discover_disks()

            self.total_disks = len(self.disks)
            LOG.info("Total disks for VSA deployment = %s" % self.total_disks)
        except IOError as e:
            msg = ("Failed to read vsa_disks.json  %s" % e.message)
            LOG.error(msg)
            raise vsa_excs.DiskFileError(msg)

    def _create_installer_input_json(self):

        command = \
            self.VSA_INSTALLER + " -create-default-json -disks " + \
            str(self.total_disks)
        if self.total_disks == 0:
            msg = "Minimum number of disks must be 1. No disks are available"
            LOG.error(msg)
            raise vsa_excs.InvalidInputException(msg)

        if self.is_AO_enabled:
            if self.total_disks > 1:
                command += " -tiering"
            else:
                msg = "Cannot enable AO as only one disk is available"
                LOG.error(msg)
                raise vsa_excs.InvalidInputException(msg)
        resp = self._do_operation(command)
        if resp:
            raise vsa_excs.DefaultJSONCreationFailed(resp)

    def _initialize_default_json(self):
        try:
            data = self._read_json('default-input.json')
            data["HostName"] = self.host_name
            data["OSImageStoragePool"] = self.os_image_storagepool
            LOG.debug("HostName: %s\n OSImageStoragePool: %s"
                      % (self.host_name, self.os_image_storagepool))
            for network in data["Networks"]:
                network["DHCP"] = 0
                network["IPAddress"] = self.vsa_ip
                LOG.debug("IPAddress: %s" % self.vsa_ip)
                network["Subnet"] = self.v_netmask
                LOG.debug("Subnet: %s" % self.v_netmask)
                network["Gateway"] = self.v_gateway
                LOG.debug("Gateway: %s" % self.v_gateway)
                network["NetworkInterface"] = self.v_network_name
                LOG.debug("NetworkInterface: %s" % self.v_network_name)
            counter = 0
            tier_1_counter = 0

            self._validate_disks()

            if self.is_AO_enabled:
                if self.disk_file:
                    tier0count = len(self.tier0List)
                    tier1count = len(self.tier1List)
                    LOG.debug("tier0count: %s\t tier1count:%s"
                              % (tier0count, tier1count))
                    for disk in data["Disks"]:
                        if tier0count:
                            disk["Location"] = self.tier0List[tier0count - 1]
                            disk["Size"] = ""
                            tier0count -= 1
                        elif tier1count > 0:
                            disk["Location"] = self.tier1List[tier_1_counter]
                            disk["Tier"] = "Tier 1"
                            tier1count -= 1
                            tier_1_counter += 1
                            disk["Size"] = ""
                else:
                    for disk in data["Disks"]:
                        if self.disks[counter] == '/dev/sdb':
                            #
                            # /dev/sdb is considered as AO disk if disk_file
                            #  not mentioned
                            #
                            disk["Tier"] = "Tier 0"
                        else:
                            disk["Tier"] = "Tier 1"
                            disk["Location"] = self.disks[counter]
                            disk["Size"] = ""
                            counter += 1
            else:
                for disk in data["Disks"]:
                    disk["Location"] = self.disks[counter]
                    disk["Size"] = ""
                    counter += 1

            with open('default-input.json', 'w') as file_desc:
                file_desc.write(json.dumps(data, sort_keys=False, indent=4))

            LOG.info("default input json initialization success")

        except Exception as e:
            msg = \
                "An unknown exception during default input json initialization"
            LOG.error(msg + e.message)
            raise vsa_excs.DefaultJSONInitializationFailed(msg)

    def _create_bridge(self):
        try:
            with open(self.CONFIG_DIR + '/data/network_template.xml', 'r') \
                    as file_desc:
                xmlstring = file_desc.read()
                substitutions = {'NETWORK_NAME': self.v_network_name,
                                 'BRIDGE_NAME': self.v_bridge_name}
                pattern = re.compile(r'%([^%]+)%')
                xmlstring = \
                    re.sub(pattern, lambda m: substitutions[m.group(1)],
                           xmlstring)
            with open('network_vsa.xml', 'w') as file_desc:
                file_desc.write(xmlstring)
            return self._virtual_network_define('network_vsa.xml')
        except Exception as e:
            raise vsa_excs.BridgeCreationFailed(e.message)

    def _create_storage_pool(self):
        try:
            with open(self.CONFIG_DIR + '/data/storage_pool_template.xml',
                      'r') as file_desc:
                xmlstring = file_desc.read()
                substitutions = \
                    dict(POOL_NAME=self.os_image_storagepool,
                         POOL_PATH=self.pool_location)
                pattern = re.compile(r'%([^%]+)%')
                xmlstring = \
                    re.sub(pattern, lambda m: substitutions[m.group(1)],
                           xmlstring)
            with open('storage_pool_vsa.xml', 'w') as file_desc:
                file_desc.write(xmlstring)
            return self._virtual_storage_pool_define('storage_pool_vsa.xml')
        except Exception as e:
            raise vsa_excs.StoragePoolCreationFailed(e.message)

    def _create_vsa_vm(self):
        vsa_install_resp = \
            self._do_operation(self.VSA_INSTALLER + " -no-prompt " +
                               "default-input.json " + self.VSA_PACKAGE,
                               need_output=True)
        if vsa_install_resp:
            msg = "VSA installation failed"
            LOG.error(msg)
            raise vsa_excs.VMCreateFailed(msg)
        LOG.info("VSA VM creation success")
        self._update_vsa_config()
        if self.autostart:
            # Set VSA VM autostart
            self._do_operation("virsh autostart " + self.host_name)
            LOG.info("Set VSA VM to autostart")

    def _read_configuration_input(self, config_file):
        LOG.info("Read VSA config from %s" % config_file)
        self.vsa_config_data = self._read_json(config_file)
        self.v_network_name = \
            self.vsa_config_data["vsa_config"]["network_name"]
        self.host_name = self.vsa_config_data["vsa_config"]["hostname"]
        self.os_image_storagepool = \
            self.vsa_config_data["vsa_config"]["os_image_storagepool"]
        self.pool_location = self.vsa_config_data["vsa_config"]["os_image_dir"]
        self.autostart = True if self.vsa_config_data["vsa_config"][
                                     "autostart"] == 'True' else False
        if not os.path.exists(self.pool_location):
            os.makedirs(self.pool_location)

    def _read_network_input(self):
        """
        This method will parse the JSON configuration file(vsa_config.json)
        and will consider the values as inputs for VBridge and VSA
        configuration
        @returns the json content of the vsa_config.json
        """
        LOG.info(
            "Read VSA network config from %s" % self.VSA_NETWORK_CONFIG_FILE)
        self.network_config_data = self._read_json(
            self.VSA_NETWORK_CONFIG_FILE)
        self.v_bridge_name = self.network_config_data["virtual_bridge"]["name"]
        self.v_bridge_ip = \
            self.network_config_data["virtual_bridge"]["ip_address"]
        self.v_interface = \
            self.network_config_data["virtual_bridge"]["interface"]
        self.vsa_ip = self.network_config_data["vsa_network"]["ip_address"]
        LOG.debug("v_bridge_name:%s v_bridge_ip:%s v_interface:%s vsa_ip:%s"
                  % (self.v_bridge_name, self.v_bridge_ip, self.v_interface,
                     self.vsa_ip))
        self.v_netmask = self._get_netmask_from_interface()
        self.v_gateway = self._compute_gateway()

    def _read_json(self, file_path):
        """
        This method will parse the JSON configuration file(vsa_config.json)
        and will consider the values as inputs for VBridge and VSA
        configuration
        @returns the json content of the vsa_config.json
        """
        LOG.info("Parse JSON : %s" % file_path)
        try:
            with open(file_path) as file_desc:
                config_data = json.load(file_desc)
                LOG.debug("Config data: %s" % config_data)
            return config_data
        except IOError as e:
            msg = ("Failed to load %s %s" % (file_path, e.message))
            LOG.error(msg)
            raise vsa_excs.JsonParseException(msg)

    def _make_list_from_tiered(self):
        self.disks = []
        self.tier0List = self.tiered_disks['Tier 0']
        if any(self.tier0List):
            self.disks += self.tier0List
        LOG.info("Tier0 disks : %s" % self.tier0List)
        self.tier1List = self.tiered_disks['Tier 1']
        if any(self.tier1List):
            self.disks += self.tier1List
        LOG.info("Tier1 disks : %s" % self.tier1List)
        LOG.info("Disks for VSA deployment : %s" % self.disks)
        return self.disks

    def _get_netmask_from_interface(self):
        """
        Using socket this method retrieves the netmask details from the
        given interface
        """
        try:
            netmask = socket.inet_ntoa(fcntl.ioctl(socket.socket(
                socket.AF_INET,
                socket.SOCK_DGRAM),
                35099, struct.pack('256s', str(self.v_bridge_name)))[20:24])
            LOG.debug("Netmask : %s" % netmask)
            return netmask
        except Exception as e:
            msg = \
                ("Cannot retrieve netmask from interface %s  %s"
                 % (self.v_interface, e.message))
            LOG.error(msg)
            raise vsa_excs.NetmaskRetrieveFailed(msg)

    def _compute_gateway(self):
        """
        Computes the gateway from the give vsa ip and netmask
        """
        try:
            vsa_ip = IPNetwork(self.vsa_ip + '/' + self.v_netmask)
            gateway = vsa_ip.network + 1
            LOG.debug("Gateway : %s" % gateway)
            return str(gateway)
        except Exception as e:
            msg = ("Computing gateway details failed %s" % e.message)
            LOG.error(msg)
            raise vsa_excs.ComputeGatewayFailed(msg)

    def _validate_disks(self):
        disks_from_host = self._discover_disks()
        for disk in self.disks:
            if disk not in disks_from_host:
                msg = "Invalid disk entries are present in the file"
                LOG.error(msg)
                raise vsa_excs.DeviceValidationFailed(msg)
        LOG.debug("Disk validation success")

    def _discover_disks(self):
        """
        Gets all non-root disk. Rely on specific pattern (as per script)
        to determine whether disk is a root disk or not. Returns a list()
        containing reference of disk(s).
        """
        disk_lists = \
            Popen(self.CONFIG_DIR + "/scripts/get_devices.sh",
                  stdout=PIPE).stdout.read().split(' ')
        LOG.debug("Disks in host : %s" % disk_lists)
        return disk_lists

    def _virtual_network_define(self, network_file):
        """
        Defines and starts the virtual network from a xml file
        """
        return_val = self._do_operation("virsh net-define " + network_file)
        if str(return_val) == "0":
            self._do_operation("virsh net-start " + self.v_network_name)
            self._do_operation("virsh net-autostart " + self.v_network_name)
        else:
            msg = "Creation of virtual network failed"
            LOG.error(msg)
            raise vsa_excs.BridgeCreationFailed(msg)
        LOG.info("VSA network creation success")

    def _virtual_storage_pool_define(self, pool_file):
        """
        Defines and starts the storage pool from a xml file
        :type self: object
        """
        return_val = self._do_operation("virsh pool-define " + pool_file)
        if str(return_val) == "0":
            self._do_operation("virsh pool-start " + self.os_image_storagepool)
            self._do_operation("virsh pool-autostart " +
                               self.os_image_storagepool)
        else:
            msg = "Creation of virtual storage pool failed"
            LOG.error(msg)
            raise vsa_excs.StoragePoolCreationFailed(msg)
        LOG.info("VSA storage pool creation success")

    def _update_vsa_config(self):
        """
        This method updates the vsa_config.json file which is persisted.
        It will update the created_at, updated_at and file_access_count
        For Fresh deployment , the created and updated date will be the
        same.
        """
        try:
            with open(self.VSA_CONFIG_FILE, 'w') as file_desc:
                created_date = str(datetime.datetime.utcnow())
                vsa_data = self.vsa_config_data["vsa_config"]
                if vsa_data["created_at"] == "":
                    vsa_data["created_at"] = created_date
                    vsa_data["updated_at"] = created_date
                else:
                    vsa_data["updated_at"] = str(datetime.datetime.utcnow())
                    vsa_data["file_access_count"] += 1
                file_desc.write(
                    json.dumps(self.vsa_config_data, sort_keys=False,
                               indent=4))
        except Exception as e:
            msg = "Updating VSA config file failed"
            LOG.error(msg + e.message)
            raise vsa_excs.UpdateConfigFailed(msg)
        LOG.info("Update VSA config file success")

    def _roll_back_installation(self):
        LOG.info("Initiate roll-back of VSA installation")
        self._vsa_network_destroy()
        self._vsa_storage_pool_destroy()

    def _vsa_network_destroy(self):
        """
        Destroys the VSA network created.
        """
        try:
            network = \
                self.hypervisor_conn.networkLookupByName(self.v_network_name)
            network.destroy()
            network.undefine()
        except Exception as e:
            msg = "Failed to destroy and undefine the network"
            LOG.error(msg + "%s" % e.message)
            raise vsa_excs.NetworkDestroyFailed(msg)
        LOG.info("%s network destroyed" % self.v_network_name)

    def _vsa_storage_pool_destroy(self):
        """
        Destroys the VSA storage pool created.
        """
        try:
            storage_pool = \
                self.hypervisor_conn.storagePoolLookupByName(
                    self.os_image_storagepool)
            storage_pool.destroy()
            storage_pool.undefine()
        except Exception as e:
            msg = "Failed to destroy and undefine the storage pool"
            LOG.error(msg + "%s" % e.message)
            raise vsa_excs.PoolDestroyFailed(msg)
        LOG.info("%s pool destroyed" % self.os_image_storagepool)

    def _get_vsa_vm_status(self):
        try:
            vsa = self.hypervisor_conn.lookupByName(self.host_name)
            vsa_state = vsa.state()
            return vsa_state[0]
        except:
            self._report_libvirt_error()
            return 0

    def _report_libvirt_error(self):
        """Call virGetLastError function to get the last error information."""
        err = libvirt.virGetLastError()
        if err[0] == 42:
            LOG.info("Running VSA VM not found on the host")
        else:
            LOG.error(" %s " % str(err[2]))
            raise vsa_excs.VsaStatusFailed()

    def _vsa_domain_destroy(self):
        """
        Destroys the domain VSA created.
        """
        try:
            vsa_domain = self.hypervisor_conn.lookupByName(self.host_name)
            vsa_domain.destroy()
            vsa_domain.undefine()
            LOG.info("%s Domain destroyed" % self.host_name)
            return True
        except Exception as e:
            msg = "Failed to destroy and undefine the VSA domain: "
            LOG.error(msg + "%s" % e.message)
            raise vsa_excs.DomainDestroyFailed(msg)

    def _do_operation(self, op, need_output=False):
        """
        Execute a command in a subprocess and catch the output so it
        may be logged, as well as returning it to stdout.
        Returns the subprocess exit code on failure and 0 on success
        :rtype : 0 - if success else error_code
        """
        try:
            LOG.info("Calling %s" % op)
            output = \
                subprocess.check_output(op, stderr=subprocess.STDOUT,
                                        shell=True)
            LOG.info("Command output: \n%s" % output if output else "")
            if need_output:
                print "%s" % output
            return 0
        except subprocess.CalledProcessError as e:
            LOG.error("Unable to execute command: status %s" % e.returncode)
            LOG.error("Command output: \n%s" % e.output)
            return e.returncode

    def vsa_recreate(self):
        """
        No more this function is being used as we are not doing following:
        1. persisting VSA VM data so that we can re-create
        2. Creating VSA VM from its persistent data VSA recreation
        parami vsa_state: state of VSA : 5- Shut off, 1 - Running, 0- No Exist
        param vsa_name: Name of the vsa vm in the config file
        param vsa_network: VSA Network
        return: True : VSA is already available
        """
        self.vsa_network_file = self.vsa_state_path + "/network_vsa.xml"
        self.vsa_pool_file = self.vsa_state_path + "/storage_pool_vsa.xml"
        self.vsa_file = self.vsa_state_path + "/" + self.host_name + ".xml"
        try:
            self._recreate_network()
            self._recreate_storage_pool()
            self._start_vsa_vm()
        except vsa_excs.StoragePoolCreationFailed:
            self._vsa_network_destroy()
            sys.exit(1)
        except vsa_excs.StartVMFailed:
            self._roll_back_installation()
            sys.exit(1)
        LOG.info("The VSA vm %s is deployed again" % self.host_name)

    def _persist_vsa_confgiuration(self):
        """
        No more this function is being used as we are not doing following:
        1. persisting VSA VM data so that we can re-create 2. Creating VSA VM
        from its persistent data
        This method will persist the required files to recreate VSA
        The files that will be persited are:
        i)network_vsa.xml
        ii)storage_pool_vsa.xml
        iii) <vsa_name>.xml
        """
        self._update_vsa_config()
        try:
            if not os.path.exists(self.vsa_state_path):
                os.makedirs(self.vsa_state_path)
            for file_name in glob.glob(os.path.join('./', '*.xml')):
                shutil.copy(file_name, self.vsa_state_path)
            shutil.copy(self.VSA_NETWORK_CONFIG_FILE, self.vsa_state_path)
            shutil.copy(self.VSA_CONFIG_FILE, self.vsa_state_path)
            vsa_vm_xml_path = '/etc/libvirt/qemu/' + self.host_name + ".xml"
            shutil.copy(vsa_vm_xml_path, self.vsa_state_path)
        except Exception as e:
            msg = ("Failed during persist if vsa config %s" % e.message)
            LOG.error(msg)
            raise vsa_excs.PersistConfigFailed(msg)
        LOG.info("Persist VSA config data success")

    def _is_vsa_state_path_exists(self):
        """
        No more this function is being used as we are not doing following:
        1. persisting VSA VM data so that we can re-create 2. Creating VSA VM
        from its persistent data
        """
        if not os.path.exists(self.vsa_state_path):
            return False
        else:
            return True

    def _recreate_network(self):
        """
        No more this function is being used as we are not doing following:
        1. persisting VSA VM data so that we can re-create
        2. Creating VSA VM from its persistent data
        Recreates the virtual network from the xml file present in
        /mnt/state/vsa
        """
        LOG.info("Recreating %s " % self.v_network_name)
        self._virtual_network_define(self.vsa_network_file)

    def _recreate_storage_pool(self):
        """
        No more this function is being used as we are not doing following:
        1. persisting VSA VM data so that we can re-create
        2. Creating VSA VM from its persistent data
        Recreates the storage pool from the xml file present in
        /mnt/state/vsa
        """
        LOG.info("Recreating %s " % self.os_image_storagepool)
        self._virtual_storage_pool_define(self.vsa_pool_file)

    def _start_vsa_vm(self):
        """
        No more this function is being used as we are not doing following:
        1. persisting VSA VM data so that we can re-create
        2. Creating VSA VM from its persistent data
        """
        try:
            self._do_operation("virsh define " + self.vsa_file)
            self._do_operation("virsh start " + self.host_name)
            self._do_operation("virsh autostart " + self.host_name)
        except:
            msg = "Ecxption occured while recreating VM"
            LOG.error(msg)
            raise vsa_excs.StartVMFailed(msg)
        LOG.info("VSA vm %s recreated and autostarted" % self.host_name)
