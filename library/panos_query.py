#!/usr/bin/env python

#  Copyright 2017 Palo Alto Networks, Inc
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

ANSIBLE_METADATA = {'status': ['preview'],
                    'supported_by': 'community',
                    'version': '1.0'}

DOCUMENTATION = '''
---
module: panos_search_address
short_description: retrieve address object or address group
description: >
    Security policies allow you to enforce rules and take action, and can be as general or specific as needed.
    The policy rules are compared against the incoming traffic in sequence, and because the first rule that matches
    the traffic is applied, the more specific rules must precede the more general ones.
author: "Bob Hagen (@rnh556)"
version_added: "1.0"
requirements:
    - pan-python can be obtained from PyPi U(https://pypi.python.org/pypi/pan-python)
    - pandevice can be obtained from PyPi U(https://pypi.python.org/pypi/pandevice)
notes:
    - Checkmode is not supported.
    - Panorama is supported
options:
    ip_address:
        description:
            - IP address (or hostname) of PAN-OS device being configured.
        required: true
    username:
        description:
            - Username credentials to use for auth.
        required: false
        default: "admin"
    password:
        description:
            - Password credentials to use for auth.
        required: true
    api_key:
        description:
            - API key that can be used instead of I(username)/I(password) credentials.
    rule_name:
        description:
            - Name of the security rule.
        required: true
    devicegroup:
        description: >
            Device groups are used for the Panorama interaction with Firewall(s). The group must exists on Panorama.
            If device group is not define we assume that we are contacting Firewall.
        required: false
        default: None
'''

EXAMPLES = '''
- name: search for shared address object
  panos_searchobject:
    ip_address: '10.0.0.1'
    username: 'admin'
    password: 'paloalto'
    address: 'DevNet'

- name: search for devicegroup address object
  panos_searchobject:
    ip_address: '10.0.0.1'
    password: 'paloalto'
    object: 'DevNet'
    address: 'DeviceGroupA'
'''

RETURN = '''
# Default return values
'''

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.basic import get_exception

try:
    import pan.xapi
    from pan.xapi import PanXapiError
    import pandevice
    from pandevice import base
    from pandevice import firewall
    from pandevice import panorama
    from pandevice import objects
    from pandevice import policies
    import ipaddress
    import xmltodict
    import json
    HAS_LIB = True
except ImportError:
    HAS_LIB = False


def get_devicegroup(device, devicegroup):
    dg_list = device.refresh_devices()
    for group in dg_list:
        if isinstance(group, pandevice.panorama.DeviceGroup):
            if group.name == devicegroup:
                return group
    return False


def get_rulebase(device, devicegroup):
    # Build the rulebase
    if isinstance(device, firewall.Firewall):
        rulebase = policies.Rulebase()
        device.add(rulebase)
    elif isinstance(device, panorama.Panorama):
        dg = panorama.DeviceGroup(devicegroup)
        device.add(dg)
        rulebase = policies.PreRulebase()
        dg.add(rulebase)
    else:
        return False
    policies.SecurityRule.refreshall(rulebase)
    return rulebase


def get_object(device, dev_group, obj_name):
    # Search global address objects
    match = device.find(obj_name, objects.AddressObject)
    if match:
        return match

    # Search global address groups
    match = device.find(obj_name, objects.AddressGroup)
    if match:
        return match

    # Search Panorama device group
    if isinstance(device, pandevice.panorama.Panorama):
        # Search device group address objects
        match = dev_group.find(obj_name, objects.AddressObject)
        if match:
            return match

        # Search device group address groups
        match = dev_group.find(obj_name, objects.AddressGroup)
        if match:
            return match
    return False


def addr_in_obj(addr, obj):
    ip = ipaddress.ip_address(unicode(addr))
    # Process address objects
    if isinstance(obj, objects.AddressObject):
        if obj.type == 'ip-netmask':
            net = ipaddress.ip_network(unicode(obj.value))
            if ip in net:
                return True
        if obj.type == 'ip-range':
            ip_range = obj.value.split('-')
            lower = ipaddress.ip_address(unicode(ip_range[0]))
            upper = ipaddress.ip_address(unicode(ip_range[1]))
            if lower < ip < upper:
                return True
    return False


def get_service(device, dev_group, obj_name):
    # Search global address objects
    match = device.find(obj_name, objects.ServiceObject)
    if match:
        return match

    # Search global address groups
    match = device.find(obj_name, objects.ServiceGroup)
    if match:
        return match

    # Search Panorama device group
    if isinstance(device, pandevice.panorama.Panorama):
        # Search device group address objects
        match = dev_group.find(obj_name, objects.ServiceObject)
        if match:
            return match

        # Search device group address groups
        match = dev_group.find(obj_name, objects.ServiceGroup)
        if match:
            return match
    return False


def port_in_svc(orientation, port, protocol, obj):
    # Process address objects
    if orientation is 'source':
        for x in obj.source_port.split(','):
            if '-' in x:
                port_range = x.split('-')
                lower = int(port_range[0])
                upper = int(port_range[1])
                if lower < int(port) < upper and obj.protocol == protocol:
                    return True
            else:
                if port == x and obj.protocol == protocol:
                    return True
    elif orientation is 'destination':
        for x in obj.destination_port.split(','):
            if '-' in x:
                port_range = x.split('-')
                lower = int(port_range[0])
                upper = int(port_range[1])
                if lower < int(port) < upper and obj.protocol == protocol:
                    return True
            else:
                if port == x and obj.protocol == protocol:
                    return True
    return False


def get_tag(device, dev_group, obj_name):
    # Search global address objects
    match = device.find(obj_name, objects.Tag)
    if match:
        return match
    # Search Panorama device group
    if isinstance(device, pandevice.panorama.Panorama):
        # Search device group address objects
        match = dev_group.find(obj_name, objects.Tag)
        if match:
            return match
    return False


def main():
    argument_spec = dict(
        ip_address=dict(required=True),
        password=dict(no_log=True),
        username=dict(default='admin'),
        api_key=dict(no_log=True),
        application=dict(default=None),
        source_zone=dict(default=None),
        destination_zone=dict(default=None),
        source_ip=dict(default=None),
        destination_ip=dict(default=None),
        source_port=dict(default=None),
        destination_port=dict(default=None),
        protocol=dict(default=None, choices=['tcp', 'udp']),
        tag=dict(default=None),
        devicegroup=dict(default=None)
    )
    module = AnsibleModule(argument_spec=argument_spec, supports_check_mode=False,
                           required_one_of=[['api_key', 'password']]
                           )
    if not HAS_LIB:
        module.fail_json(msg='Missing required libraries.')

    ip_address = module.params["ip_address"]
    password = module.params["password"]
    username = module.params['username']
    api_key = module.params['api_key']
    application = module.params['application']
    source_zone = module.params['source_zone']
    source_ip = module.params['source_ip']
    source_port = module.params['source_port']
    destination_zone = module.params['destination_zone']
    destination_ip = module.params['destination_ip']
    destination_port = module.params['destination_port']
    protocol = module.params['protocol']
    tag = module.params['tag']
    devicegroup = module.params['devicegroup']

    # Create the device with the appropriate pandevice type
    device = base.PanDevice.create_from_device(ip_address, username, password, api_key=api_key)
    objects.AddressObject.refreshall(device)
    objects.AddressGroup.refreshall(device)
    objects.ServiceObject.refreshall(device)
    objects.ServiceGroup.refreshall(device)
    objects.Tag.refreshall(device)

    # If Panorama, validate the devicegroup
    dev_group = None
    if devicegroup and isinstance(device, panorama.Panorama):
        dev_group = get_devicegroup(device, devicegroup)
        if dev_group:
            device.add(dev_group)
            objects.AddressObject.refreshall(dev_group)
            objects.AddressGroup.refreshall(dev_group)
            objects.ServiceObject.refreshall(dev_group)
            objects.ServiceGroup.refreshall(dev_group)
            objects.Tag.refreshall(dev_group)
        else:
            module.fail_json(
                failed=1,
                msg='\'%s\' device group not found in Panorama. Is the name correct?' % devicegroup
            )

    # Build the rulebase and produce list
    rulebase = get_rulebase(device, dev_group)
    rulelist = rulebase.children
    hitbase = policies.Rulebase()
    loose_match = True

    # Process each rule
    for rule in rulelist:
        hitlist = []

        if source_zone:
            source_zone_match = False
            if loose_match and 'any' in rule.fromzone:
                source_zone_match = True
            else:
                for object_string in rule.fromzone:
                    if object_string == source_zone:
                        source_zone_match = True
            hitlist.append(source_zone_match)

        if destination_zone:
            destination_zone_match = False
            if loose_match and 'any' in rule.tozone:
                destination_zone_match = True
            else:
                for object_string in rule.tozone:
                    if object_string == destination_zone:
                        destination_zone_match = True
            hitlist.append(destination_zone_match)

        if source_ip:
            source_ip_match = False
            if loose_match and 'any' in rule.source:
                source_ip_match = True
            else:
                for object_string in rule.source:
                    obj = get_object(device, dev_group, object_string)
                    if obj is False:
                        try:
                            obj = ipaddress.ip_network(unicode(object_string))
                        except ValueError:
                            continue
                    if isinstance(obj, objects.AddressObject) and addr_in_obj(source_ip, obj):
                        source_ip_match = True
                    elif isinstance(obj, objects.AddressGroup) and obj.static_value:
                        for member_string in obj.static_value:
                            member = get_object(device, dev_group, member_string)
                            if addr_in_obj(source_ip, member):
                                source_ip_match = True
            hitlist.append(source_ip_match)

        if destination_ip:
            destination_ip_match = False
            if loose_match and 'any' in rule.destination:
                destination_ip_match = True
            else:
                for object_string in rule.destination:
                    obj = get_object(device, dev_group, object_string)
                    if obj is False:
                        try:
                            obj = ipaddress.ip_network(unicode(object_string))
                        except ValueError:
                            continue
                    if isinstance(obj, objects.AddressObject) and addr_in_obj(destination_ip, obj):
                        destination_ip_match = True
                    elif isinstance(obj, objects.AddressGroup) and obj.static_value:
                        for member_string in obj.static_value:
                            member = get_object(device, dev_group, member_string)
                            if addr_in_obj(destination_ip, member):
                                destination_ip_match = True
            hitlist.append(destination_ip_match)

        if source_port:
            source_port_match = False
            orientation = 'source'
            if loose_match and 'any' in rule.service:
                source_port_match = True
            elif 'application-default' in rule.service:
                source_port_match = False  # Fix this once apps are supported
            else:
                for object_string in rule.service:
                    obj = get_service(device, dev_group, object_string)
                    if isinstance(obj, objects.ServiceObject):
                        if port_in_svc(orientation, source_port, protocol, obj):
                            source_port_match = True
                    elif isinstance(obj, objects.ServiceGroup):
                        for member_string in obj.value:
                            member = get_service(device, dev_group, member_string)
                            if port_in_svc(orientation, source_port, protocol, member):
                                source_port_match = True
            hitlist.append(source_port_match)

        if destination_port:
            destination_port_match = False
            orientation = 'destination'
            if loose_match and 'any' in rule.service:
                destination_port_match = True
            elif 'application-default' in rule.service:
                destination_port_match = False  # Fix this once apps are supported
            else:
                for object_string in rule.service:
                    obj = get_service(device, dev_group, object_string)
                    if isinstance(obj, objects.ServiceObject):
                        if port_in_svc(orientation, destination_port, protocol, obj):
                            destination_port_match = True
                    elif isinstance(obj, objects.ServiceGroup):
                        for member_string in obj.value:
                            member = get_service(device, dev_group, member_string)
                            if port_in_svc(orientation, destination_port, protocol, member):
                                destination_port_match = True
            hitlist.append(destination_port_match)

        if tag:
            tag_match = False
            for object_string in rule.tag:
                obj = get_tag(device, dev_group, object_string)
                if obj and obj.name == tag:
                    return True
            hitlist.append(tag_match)

        # Add to hit rulebase
        if False not in hitlist:
            hitbase.add(rule)

    # Dump the hit rulebase
    if hitbase.children:
        output_string = xmltodict.parse(hitbase.element_str())
        module.exit_json(
            stdout_lines=json.dumps(output_string, indent=2),
            msg='%s of %s rules matched' % (hitbase.children.__len__(), rulebase.children.__len__())
        )
    else:
        module.fail_json(msg='No matching rules found.')


if __name__ == '__main__':
    main()
