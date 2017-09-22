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


class VSAException(Exception):
    pass


class JsonParseException(VSAException):
    message = "An unknown exception occurred."


class DiskFileError(VSAException):
    message = "An unknown exception in reading file"


class NetmaskRetrieveFailed(VSAException):
    message = "An unknown exception during netmask retrieve"


class ComputeGatewayFailed(VSAException):
    message = "An unknown exception during compute gateway"


class InvalidInputException(VSAException):
    message = "An unknown exception while reading input file"


class DefaultJSONCreationFailed(VSAException):
    message = "An unknown exception while creating default json"


class DeviceValidationFailed(VSAException):
    message = "Invalid disk entries present"


class DefaultJSONInitializationFailed(VSAException):
    message = "An unknown exception while parsing default Json"


class BridgeCreationFailed(VSAException):
    message = "An unknown exception at network creation"


class StoragePoolCreationFailed(VSAException):
    message = "An unknown exception while parsing default Json"


class VMCreateFailed(VSAException):
    message = "An unknown exception for create VM "


class StartVMFailed(VSAException):
    message = "An unknown exception while starting VM "


class DomainDestroyFailed(VSAException):
    message = "An unknown exception while destroy VSA VM"


class InstallVSAfailed(VSAException):
    message = "An unknown exception while installing VSA"


class PoolDestroyFailed(VSAException):
    message = "An unknown exception while destroy storage pool"


class NetworkDestroyFailed(VSAException):
    message = "An unknown exception while destroy vsa network"


class PersistConfigFailed(VSAException):
    message = "An unknown exception while setting up ephemeral configuration"


class UpdateConfigFailed(VSAException):
    message = "An unknown exception while updating VSA configuration"


class DestroyFailed(VSAException):
    message = ("An unknown exception while destroying VSA setup")


class VsaStatusFailed(VSAException):
    message = ("An unknown exception while getting the status of VSA VM")
