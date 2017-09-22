#!/bin/bash
#
# (c) Copyright 2015 Hewlett Packard Enterprise Development LP
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
#
# gets all devices except boot
default_disk_pattern="/dev/sd[a-z]\+$"

function _get_all_devices() {
    devs=$( ls /dev/sd* | grep $default_disk_pattern 2>/dev/null )
    echo "${devs}"
}

function get_boot_dev() {
    # the label we use for boot device is always cloudimg-rootfs
    boot_dev=$( findfs LABEL=cloudimg-rootfs | sed 's/[0-9]\+//g' )
    echo $boot_dev
}

function get_devices-boot_dev() {
    devs=$( _get_all_devices )
    boot_dev=$( get_boot_dev )
    other_devs=$( echo "${devs}" | grep -v $boot_dev )
    echo "${other_devs}"
}

devices=
function get_devices() {
    devices=$( get_devices-boot_dev )
    echo -n $devices
}

get_devices;
