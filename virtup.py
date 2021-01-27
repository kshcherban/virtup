#!/usr/bin/python3 -u
# -*- coding: utf-8 -*-
#
#  Copyright 2013 Konstantin Shcherban <k.scherban@gmail.com>
#
#  This program is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 2 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#

import os
import re
import sys
import tty
import random
import termios
import atexit
import libvirt
import argparse
import xml.dom.minidom  # for pretty printing
from multiprocessing import Pool
from xml.etree import ElementTree as ET


class Disk:
    """Contains disk based procedures, provides methods to create, delete,
    download, upload volumes.
    Takes storage pool name and libvirt connection object as arguments
    """
    def __init__(self, conn, pool):
        self.conn = conn
        self.pool = pool

    def vol_tmpl(self, imgtype, name, capacity, path):
        """Generate volume template based on disk type"""
        tmpl_root = ET.Element('volume')
        tmpl_name = ET.SubElement(tmpl_root, 'name')
        tmpl_name.text = name
        tmpl_cap = ET.SubElement(tmpl_root, 'capacity')
        tmpl_cap.text = str(capacity)
        tmpl_target = ET.SubElement(tmpl_root, 'target')
        tmpl_target_path = ET.SubElement(tmpl_target, 'path')
        tmpl_target_path.text = path + '/' + name
        tmpl_target_perm = ET.SubElement(tmpl_target, 'permissions')
        tmpl_target_perm_mode = ET.SubElement(tmpl_target_perm, 'mode')
        tmpl_target_perm_mode.text = '0600'
        tmpl_target_format = ET.SubElement(tmpl_target, 'format')
        tmpl_target_format.set('type', imgtype)
        if imgtype == 'qcow2':
            tmpl_alloc = ET.SubElement(tmpl_root, 'allocation')
            tmpl_alloc.text = '536576'
        return ET.tostring(tmpl_root, encoding="unicode")

    def vol_obj(self, obj):
        """Return volume object independently of what provided:
        volume object or name"""
        if not isinstance(obj, str):
            return obj
        p = self.pool
        try:
            vol = self.conn.storagePoolLookupByName(p).storageVolLookupByName(obj)
            return vol
        except libvirt.libvirtError:
            sys.exit(1)

    def create_vol(self, name, imgsize, imgtype):
        """Create volume in specified pool with specified name, size, format
        and pool. Return full path to created volume"""
        try:
            s = self.conn.storagePoolLookupByName(self.pool)
        except libvirt.libvirtError:
            sys.exit(1)
        xe = ET.fromstring(s.XMLDesc(0))
        # find storage pool path
        spath = xe.find('.//path').text
        tmpl = self.vol_tmpl(imgtype, name, imgsize, spath)
        try:
            v = s.createXML(tmpl, 0)
        except libvirt.libvirtError:
            sys.exit(1)
        return spath + '/' + name

    def delete_vol(self, vol):
        """Delete volume by name"""
        try:
            p = self.conn.storagePoolLookupByName(self.pool)
            p.storageVolLookupByName(vol).delete(0)
        except libvirt.libvirtError:
            sys.exit(1)
        return 1

    def download_vol(self, vol, src):
        """Download specified volume by name into specified file"""
        # Get volume object
        vol = self.vol_obj(vol)
        # Build stream object
        stream = self.conn.newStream(0)
        # Get volume size
        size = vol.info()[1]
        # Register download
        offset = 0
        length = size
        flags = 0
        vol.download(stream, offset, length, flags)
        # Open file
        f = open(src, 'w')
        # Start transfer
        total = 0
        print('Downloading volume {0} into {1}'.format(vol.name(),
                os.path.abspath(src)))
        try:
            while True:
                ret = stream.recv(256000)
                if not ret:
                    break
                f.write(ret)
                total += len(ret)
                sys.stderr.write('\rdone {0:.2%}'.format(float(total) / size))
            # Cleanup
            stream.finish()
            f.close()
            print('')
            return 1
        except libvirt.libvirtError:
            os.remove(src)
            print('Error downloading volume')
            return 0

    def upload_vol(self, vol, src):
        """Upload image into specified volume"""
        vol = self.vol_obj(vol)
        # Build stream object
        stream = self.conn.newStream(0)

        def safe_send(data):
            while True:
                ret = stream.send(data)
                if ret == 0 or ret == len(data):
                    break
                data = data[ret:]
        # Build placeholder volume
        size = os.path.getsize(src)
        try:
            # Register upload
            offset = 0
            length = size
            flags = 0
            vol.upload(stream, offset, length, flags)
            # Open source file
            fileobj = open(src, "rb")
            # Start transfer
            total = 0
            print('Uploading volume {0} from {1}'.format(vol.name(),
                    os.path.abspath(src)))
            while True:
                blocksize = 256000
                data = fileobj.read(blocksize)
                if not data:
                    break
                safe_send(data)
                total += len(data)
                sys.stderr.write('\rdone {0:.2%}'.format(float(total) / size))
            # Cleanup
            stream.finish()
            print('')
        except Exception as e:
            print(e)
            if vol:
                vol.delete(0)
            return 0
        return 1


class Net:
    """Contains network based procedures, provides method to obtain virtual
    machine ip address from hypervisor arp cache.
    Takes virtual machine name and libvirt connection object arguments
    """
    def __init__(self, conn):
        self.conn = conn

    def mac(self, machname):
        """Return virtual machine MAC address"""
        xe = ET.fromstring(self.conn.lookupByName(machname).XMLDesc(0))
        for iface in xe.findall('.//devices/interface'):
            mac = iface.find('mac').get('address')
        return mac

    @staticmethod
    def arp2ip(mac):
        """Read arp cache and extracts ip address that corresponds virtual machine
        MAC"""
        f = open('/proc/net/arp', 'r')
        for i in f.readlines():
            if mac in i:
                return i.split()[0]
        f.close()
        return None

    def ifname(self, machname):
        """Extract network interface name from domain XML decription"""
        dom = self.conn.lookupByName(machname).XMLDesc(0)
        net = ET.fromstring(dom).find('.//interface/source').get('network')
        if not net:
            return ET.fromstring(dom).find('.//interface/source').get('bridge')
        ifname = ET.fromstring(
                self.conn.networkLookupByName(net).XMLDesc(0)
                            ).find('.//bridge').get('name')
        return ifname

    @staticmethod
    def get_subnet(ifname):
        """Return CIDR got from /bin/ip output"""
        patt = re.compile(r'inet\s*(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,3})')
        return patt.findall(os.popen('ip a s ' + ifname).read())[0]

    @staticmethod
    def long2ip(l):
        """Convert a network byte order 32-bit integer to a dotted quad ip
        address.
        """
        MAX_IP = 0xffffffff
        MIN_IP = 0x0
        if MAX_IP < l or l < MIN_IP:
            raise TypeError(
            "expected int between %d and %d inclusive" % (MIN_IP, MAX_IP))
        return '%d.%d.%d.%d' % (l >> 24 & 255, l >> 16 & 255, l >> 8 & 255, l & 255)

    @staticmethod
    def ip2long(ip):
        """Convert a dotted-quad ip address to a network byte order 32-bit
        integer.
        :param ip: Dotted-quad ip address (eg. '127.0.0.1').
        :type ip: str
        :returns: Network byte order 32-bit integer or ``None`` if ip is invalid.
        """
        quads = ip.split('.')
        if len(quads) == 1:
            # only a network quad
            quads = quads + [0, 0, 0]
        elif len(quads) < 4:
            # partial form, last supplied quad is host address, rest is network
            host = quads[-1:]
            quads = quads[:-1] + [0, ] * (4 - len(quads)) + host
        lngip = 0
        for q in quads:
            lngip = (lngip << 8) | int(q)
        return lngip

    def cidr2block(self, cidr):
        """Convert a CIDR notation ip address into a tuple containing the network
        block start and end addresses.
        """
        ip, prefix = self.ip2long(cidr.split('/')[0]), int(cidr.split('/')[-1])
        # keep left most prefix bits of ip
        shift = 32 - prefix
        block_start = ip >> shift << shift
        # expand right most 32 - prefix bits to 1
        mask = (1 << shift) - 1
        block_end = block_start | mask
        return (self.long2ip(block_start), self.long2ip(block_end))

    @staticmethod
    def block2range(start, end):
        """Convert network block start and end address into a range of network
        addresses
        """
        for j in range(1, 5):
            globals()["oct" + str(j)] = [i for i in range(int(start.split('.')[j - 1]),
                int(end.split('.')[j-1]) + 1)]
        iprange = []
        for i in oct1:
            for j in oct2:
                for m in oct3:
                    for n in oct4:
                        iprange.append(str(i)+'.'+str(j)+'.'+str(m)+'.'+str(n))
        return iprange

    def ip(self, machname):
        """Get virtual machine ip address"""
        ipaddr = self.arp2ip(self.mac(machname))
        if ipaddr:
            return ipaddr
        pool = Pool(processes=128)
        cidr = self.get_subnet(self.ifname(machname))
        iprange = self.block2range(self.cidr2block(cidr)[0], self.cidr2block(cidr)[1])
        pool.map(ping, iprange, 1)
        pool.close()
        pool.join()
        return self.arp2ip(self.mac(machname))


def ping(ip):
    """Ping ip"""
    if ip.split('.')[-1] != '0' and ip.split('.')[-1] != '255':
        os.popen("ping -W1 -q -c1 " + ip + ' 2>/dev/null')
        return 0


# Generate random MAC address
def randomMAC():
    mac = [0x00, 0x16, 0x3e,
        random.randint(0x00, 0x7f),
        random.randint(0x00, 0xff),
        random.randint(0x00, 0xff)]
    return ':'.join(map(lambda x: "%02x" % x, mac))


# Guess image type
def find_image_format(filepath):
    try:
        f = open(filepath).read(1024)
    except:
        return 'raw'
    if 'QFI' in f:
        return 'qcow2'
    if 'Virtual Disk Image' in f:
        return 'vdi'
    if 'virtualHWVersion' in f:
        return 'vmdk'
    elif 'KDMV' == open(filepath).read(4):
        return 'vmdk'
    return 'raw'


# Check if storage pool is LVM or dir
def is_lvm(pool):
    s = conn.storagePoolLookupByName(pool)
    if ET.fromstring(s.XMLDesc(0)).get('type') == 'logical':
        return 1
    return 0


# Get pool or volume name for given guest
def get_stor(machname, pool=True):
    try:
        dom = conn.lookupByName(machname)
    except libvirt.libvirtError:
        sys.exit(1)
    xe = ET.fromstring(dom.XMLDesc(0))
    try:
        path = xe.find('.//devices/disk/source').get('file')
    except AttributeError:
        return None
    if not path:
        path = xe.find('.//devices/disk/source').get('dev')
    if pool:
        path = '/'.join(path.split('/')[:-1])
        data = conn.listStoragePools()
    else:
        data = {i: conn.storagePoolLookupByName(i).listVolumes() for i in conn.listStoragePools()}
    if isinstance(data, list):
        for p in data:
            o = conn.storagePoolLookupByName(p)
            if ET.fromstring(o.XMLDesc(0)).find('.//path').text == path:
                return p
    else:
        for i in data.items():
            for v in i[1]:
                o = conn.storagePoolLookupByName(i[0]).storageVolLookupByName(v)
                if ET.fromstring(o.XMLDesc(0)).find('.//path').text == path:
                    return v
    return None


# Return list of volumes for specified virtual machine
def get_vol(machname):
    try:
        dom = conn.lookupByName(machname)
    except libvirt.libvirtError:
        sys.exit(1)
    xe = ET.fromstring(dom.XMLDesc(0))
    return [vol.items()[0][1].split('/')[-1] for vol in xe.findall('.//devices/disk/source')]


# Prepare template to import with virsh
def prepare_tmpl(machname, mac, cpu, mem, img, format, dtype, net, type='kvm'):
    if net == 'default':
        ntype = 'network'
    else:
        ntype = 'bridge'
    if type == 'kvm':
        if dtype == 'file':
            dsrc = 'file'
        else:
            dsrc = 'dev'
    else:
        dtype = 'mount'
    xml_root = ET.Element('domain')
    xml_root.set('type', type)
    xml_name = ET.SubElement(xml_root, 'name')
    xml_name.text = machname
    xml_memory = ET.SubElement(xml_root, 'memory')
    xml_memory.text = str(mem)
    xml_cpu = ET.SubElement(xml_root, 'vcpu')
    xml_cpu.set('placement', 'static')
    xml_cpu.text = str(cpu)
    xml_os = ET.SubElement(xml_root, 'os')
    xml_type = ET.SubElement(xml_os, 'type')
    xml_devices = ET.SubElement(xml_root, 'devices')
    xml_interface = ET.SubElement(xml_devices, 'interface')
    xml_interface.set('type', ntype)
    xml_mac = ET.SubElement(xml_interface, 'mac')
    xml_mac.set('address', mac)
    xml_source = ET.SubElement(xml_interface, 'source')
    xml_source.set(ntype, net)
    xml_console = ET.SubElement(xml_devices, 'console')
    xml_console.set('type', 'pty')
    xml_cs_target = ET.SubElement(xml_console, 'target')
    xml_cs_target.set('port', '0')
    xml_cs_alias = ET.SubElement(xml_console, 'alias')
    xml_cs_alias.set('name', 'console0')
    if type == 'kvm':
        xml_type.set('arch', 'x86_64')
        xml_type.set('machine', 'pc')
        xml_type.text = 'hvm'
        xml_boot = ET.SubElement(xml_os, 'boot')
        xml_boot.set('dev', 'hd')
        xml_model = ET.SubElement(xml_interface, 'model')
        xml_model.set('type', 'virtio')
        xml_features = ET.SubElement(xml_root, 'features')
        xml_acpi = ET.SubElement(xml_features, 'acpi')
        xml_apic = ET.SubElement(xml_features, 'apic')
        xml_pae = ET.SubElement(xml_features, 'pae')
        xml_disk = ET.SubElement(xml_devices, 'disk')
        xml_disk.set('type', dtype)
        xml_disk.set('device', 'disk')
        xml_disk_driver = ET.SubElement(xml_disk, 'driver')
        for key, value in {'name': 'qemu', 'type': format, 'cache': 'none',
                'cache.direct': 'on', 'io': 'native'}.items():
            xml_disk_driver.set(key, value)
        xml_disk_source = ET.SubElement(xml_disk, 'source')
        xml_disk_source.set(dsrc, img)
        xml_disk_target = ET.SubElement(xml_disk, 'target')
        xml_disk_target.set('dev', 'vda')
        xml_disk_target.set('bus', 'virtio')
        xml_cs_target.set('type', 'serial')
        xml_vnc = ET.SubElement(xml_devices, 'graphics')
        xml_vnc.set('type', 'vnc')
        xml_vnc.set('autoport', 'yes')
        xml_video = ET.SubElement(xml_devices, 'video')
        xml_video_model = ET.SubElement(xml_video, 'model')
        xml_video_model.set('type', 'cirrus')
        xml_video_model.set('vram', '9216')
        xml_video_model.set('heads', '1')
        xml_video_address = ET.SubElement(xml_video, 'address')
        for key, value in {'type': 'pci', 'domain': '0x0000', 'bus': '0x00',
                            'slot': '0x02', 'function': '0x0'}.items():
            xml_video_address.set(key, value)
    else:
        xml_type.text = 'exe'
        xml_init = ET.SubElement(xml_os, 'init')
        xml_init.text = '/sbin/init'
        xml_filesystem = ET.SubElement(xml_devices, 'filesystem')
        xml_filesystem.set('type', 'mount')
        xml_filesystem.set('accessmode', 'passthrough')
        xml_fs_source = ET.SubElement(xml_filesystem, 'source')
        xml_fs_source.set('dir', img)
        xml_fs_target = ET.SubElement(xml_filesystem, 'target')
        xml_fs_target.set('dir', '/')
        xml_cs_target.set('type', 'lxc')
    tmpf = '/tmp/' + machname + '.xml'
    pretty_xml = xml.dom.minidom.parseString(ET.tostring(xml_root)).toprettyxml()
    with open(tmpf, 'w') as wf:
        wf.write(pretty_xml)
        print(f'Temporary template written in {tmpf}')
    return ET.tostring(xml_root, encoding="unicode")


# Return modified xml from imported file ready for defining guest
def xml2tmpl(xmlf, machname, image=None, format=None, dtype=None, mac=None):
    xe = ET.fromstring(xmlf)
    # Remove values that may cause error
    try:
        xe.remove(xe.find('.//currentMemory'))
        xe.remove(xe.find('.//uuid'))
        xe.find('.//devices').remove(xe.find('.//devices/emulator'))
    except:
        pass
    # Replacing values
    xe.find('.//name').text = machname
    if image:
        xe.find('.//devices/disk').set('type', dtype)
        if dtype == 'file':
            stype = 'file'
        else:
            stype = 'dev'
        xe.find('.//devices/disk/driver').set('type', format)
        xe.find('.//devices/disk/source').set(stype, image)
    if mac:
        xe.find('.//devices/interface/mac').set('address', mac)
    return ET.tostring(xe, encoding="unicode")


def argcheck(arg):
    if arg[-1].lower() == 'm':
        return int(arg[:-1]) * 1024
    elif arg[-1].lower() == 'g':
        return int(arg[:-1]) * (1024 ** 2)
    else:
        print('Error! Format can be <int>M or <int>G')
        sys.exit(1)


def lsvirt(storage, volumes):
    pools = sorted(conn.listStoragePools())
    # List storage pools
    if storage:
        print('{0:<30}{1:<10}{2:<10}{3:<10}{4:<10}'.format('Pool name', 'Size',
            'Used', 'Avail', 'Use'))
        for i in pools:
            p = conn.storagePoolLookupByName(i).info()
            use = '{0:.2%}'.format(float(p[2]) / float(p[1]))
            print('{0:<30}{1:<10}{2:<10}{3:<10}{4:<10}'.format(i, convert_bytes(p[1]),
                convert_bytes(p[2]), convert_bytes(p[3]), use))
        sys.exit(0)
    # List volumes
    if volumes:
        # Find list of machines and create dict with list of vols associated to them
        ml = [conn.lookupByID(i).name() for i in conn.listDomainsID()] + conn.listDefinedDomains()
        print('{0:<15}{1:<30}{2:<10}{3:<10}{4:<10}'.format('Pool', 'Volume', 'Size',
                'Use', 'Used by'))
        for p in pools:
            vols = conn.storagePoolLookupByName(p).listVolumes()
            vol_dict = {}
            for mach in ml:
                associated_vols = get_vol(mach)
                if associated_vols:
                    for v in associated_vols:
                        vol_dict[v] = mach
            pinf = conn.storagePoolLookupByName(p).info()
            print(p)
            print('{0:>15}'.format('\\'))
            for v in sorted(vols):
                if v not in vol_dict:
                    vol_dict[v] = None
                vinf = conn.storagePoolLookupByName(p).storageVolLookupByName(v).info()
                use = '{0:.2%}'.format(float(vinf[2]) / float(pinf[1]))
                print('{0:<15}{1:<30}{2:<10}{3:<10}{4:<10}'.format(' ', v,
                        convert_bytes(vinf[2]), use, vol_dict[v]))
        sys.exit(0)
    # List machines
    vsorted = [conn.lookupByID(i).name() for i in conn.listDomainsID()]
    # Function to return basic domain info
    def dinfo(domain):
        d = conn.lookupByName(domain)
        j = d.info()
        if d.autostart():
            a = 'on'
        else:
            a = 'off'
        if d.isActive():
            state = 'up'
        else:
            state = 'down'
        print('{0:<30}{1:<10}{2:<10}{3:<10}{4:>5}'.format(i, j[3],
            convert_bytes(j[1] * 1024), state, a))
    print('{0:<30}{1:<10}{2:<10}{3:<10}{4:>5}'.format('Name', 'CPUs', 'Memory',
            'State', 'Autostart'))
    for i in sorted(vsorted):
        dinfo(i)
    for i in sorted(conn.listDefinedDomains()):
        dinfo(i)
    sys.exit(0)


# Converting bytes to human-readable
def convert_bytes(bytes):
    bytes = float(bytes)
    if bytes >= 1099511627776:
        terabytes = bytes / 1099511627776
        size = '%.2fT' % terabytes
    elif bytes >= 1073741824:
        gigabytes = bytes / 1073741824
        size = '%.2fG' % gigabytes
    elif bytes >= 1048576:
        megabytes = bytes / 1048576
        size = '%.2fM' % megabytes
    elif bytes >= 1024:
        kilobytes = bytes / 1024
        size = '%.2fK' % kilobytes
    else:
        size = '%.2fb' % bytes
    return size


# Check for MAC address correctness
def is_mac_addr(mac):
    """Return True if provided argument is L2 (MAC) address"""
    if mac is None:
        return True
    mac = mac.rstrip().lower()
    if re.match("[0-9a-f]{2}(:)[0-9a-f]{2}(\\1[0-9a-f]{2}){4}$", mac):
        return True
    return False


# Check uri connection type
def uri_lxc(uri):
    if re.findall('lxc', uri[:7]):
        return True
    return False


# Functions to operate with terminal. Required for console option
def reset_term():
    termios.tcsetattr(0, termios.TCSADRAIN, attrs)


def stdin_callback(watch, fd, events, unused):
    global run_console
    readbuf = os.read(fd, 1024)
    if readbuf.startswith(""):
        run_console = False
        return
    stream.send(readbuf)


def stream_callback(stream, events, unused):
    global run_console
    if events & libvirt.VIR_EVENT_HANDLE_READABLE:
        receivedData = stream.recv(1024)
        if receivedData == "":
            run_console = False
            return
        os.write(0, receivedData)


# Here we parse all the commands
parser = argparse.ArgumentParser(prog='virtup.py')
parser.add_argument('-c', '--connect', dest='uri', type=str, default='qemu:///system',
        help='hypervisor connection URI, default is qemu:///system')
parser.add_argument('-v', '--version', action='version', version='%(prog)s 0.7')
subparsers = parser.add_subparsers(dest='sub')
# Parent argparser to contain repeated arguments
suparent = argparse.ArgumentParser(add_help=False)
suparent.add_argument('name', type=str, help='virtual machine name')
parent = argparse.ArgumentParser(add_help=False)
parent.add_argument('-c', dest='cpus', type=int, default=1,
        help='amount of CPU cores, default is 1')
parent.add_argument('-net', dest='net', metavar='IFACE', type=str, default='default',
        help='bridge network interface name, default is NAT network "default"')
parent.add_argument('-m', dest='mem', metavar='RAM', type=str, default='512M',
        help='amount of memory, can be M or G, default is 512M')
parent.add_argument('-p', dest='pool', metavar='POOL', type=str,
        default='default',
        help='storage pool name, default is "default"')
parent.add_argument('-mac', dest='mac', metavar='MAC', type=str,
        help='MAC address in format 00:00:00:00:00:00')
box_auto = subparsers.add_parser('autostart', parents=[suparent],
        description='Set autostart flag for virtual machine',
        help='Set autostart flag')
box_auto.add_argument('-set', dest='auto', choices=['on', 'off'], required=True,
        help='Flag can be on or off, required')
box_add = subparsers.add_parser('import', parents=[parent, suparent],
        description='Import virtual machine from image file or XML description',
        help='Import virtual machine from image/XML file')
box_add.add_argument('-i', dest='image', type=str, metavar='IMAGE',
        help='template image file location')
box_add.add_argument('-xml', dest='xml', type=argparse.FileType('r'),
        help='xml file, describing virtual machine to import')
console = subparsers.add_parser('console', parents=[suparent],
        description='Connect to virtual machine\'s console',
        help='Connect to console')
box_create = subparsers.add_parser('create', parents=[parent, suparent],
        description='Create virtual machine from scratch',
        help='Create virtual machine')
box_create.add_argument('-s', dest='size', type=str, default='8G',
        help='disk image size, can be M or G, default is 8G')
box_create.add_argument('-f', '--disk-format', dest='dformat', type=str,
        default='raw',
        help='disk image format type, can be raw,bochs,qcow,qcow2,qed,vmdk, default is raw')
box_export = subparsers.add_parser('export', parents=[suparent],
        description='Export virtual machine description/disk image',
        help='Export virtual machine')
box_export.add_argument('-xml', dest='xml', action='store_true',
        help='virtual machine XML description will be printed')
box_export.add_argument('-i', dest='image', type=str,
        help='image file name to export disk image')
box_ls = subparsers.add_parser('ls', help='List virtual machines',
        description='List existing virtual machines, active storage pools, ip addresses')
box_ls.add_argument('-i', dest='info', action='store_true',
        help='print information about hypervisor hardware')
box_ls.add_argument('-s', dest='storage', action='store_true',
        help='list active storage pools')
box_ls.add_argument('-ip', dest='ip', action='store_true',
        help='list ip of running virtual machines')
box_ls.add_argument('-net', dest='net', action='store_true',
        help='list ip of hypervisor network interfaces')
box_ls.add_argument('-v', dest='volumes', action='store_true',
        help='list active volumes')
box_rm = subparsers.add_parser('rm', parents=[suparent],
        description='Remove virtual machine',
        help='Remove virtual machine')
box_rm.add_argument('--full', action='store_true',
        help='remove machine with image assigned to it')
box_start = subparsers.add_parser('up', parents=[suparent],
        description='Start virtual machine',
        help='Start virtual machine')
box_stop = subparsers.add_parser('down', parents=[suparent],
        description='Power off virtual machine',
        help='Power off virtual machine')
box_suspend = subparsers.add_parser('suspend', parents=[suparent],
        help='Suspend virtual machine',
        description='Suspend current state of virtual machine to disk')
box_suspend.add_argument('-f', metavar='FILE',
        help='file where machine state will be saved, default is ./<name>.sav')
box_resume = subparsers.add_parser('resume', parents=[suparent],
        help='Resume virtual machine',
        description='Resume virtual machine from file')
box_resume.add_argument('-f', metavar='FILE',
        help='file from which machine state will be resumed, default is ./<name>.sav')
box_vol = subparsers.add_parser('vol', help='Manage virtual volumes',
        description='Create or remove virtual volumes')
box_vol.add_argument('volume', type=str, help='virtual volume name')
box_vol.add_argument('-p', dest='pool', metavar='POOL', type=str,
        default='default',
        help='storage pool name, default is "default"')
box_vol.add_argument('-s', dest='size', metavar='SIZE', type=str,
        default='8G',
        help='volume size, can be M or G, default is 8G')
action = box_vol.add_mutually_exclusive_group(required=True)
action.add_argument('--add', action='store_true',
        help='create virtual volume')
action.add_argument('--del', action='store_true',
        help='remove virtual volume')
help_c = subparsers.add_parser('help')
help_c.add_argument('command', nargs="?", default=None)


if __name__ == '__main__':
    # Help command emulation
    if len(sys.argv) < 2:
        parser.parse_args(['--help'])
    args = parser.parse_args()
    if args.sub == "help":
        if not args.command:
            parser.parse_args(['--help'])
        else:
            parser.parse_args([args.command, '--help'])
    libvirt.virEventRegisterDefaultImpl()
    try:
        conn = libvirt.open(args.uri)
    except libvirt.libvirtError:
        sys.exit(1)

# Autostart section
    if args.sub == 'autostart':
        try:
            dom = conn.lookupByName(args.name)
            if args.auto == 'on':
                auto = 1
            else:
                auto = 0
            dom.setAutostart(auto)
            print('{0} autostart {1}'.format(args.name, args.auto))
        except libvirt.libvirtError:
            sys.exit(1)

# Ls command section
    if args.sub == 'ls':
        if args.ip + args.storage + args.volumes + args.net + args.info >= 2:
            print('Please specify only one option at a time')
            sys.exit(1)
        vsorted = [conn.lookupByID(i).name() for i in conn.listDomainsID()]
        if args.net:
            print('{0:<30}{1:<15}'.format('Interfaces', 'Status'))
            for i in conn.listInterfaces():
                print('{0:<30}{1:<15}'.format(i, 'active'))
            for i in conn.listDefinedInterfaces():
                print('{0:<30}{1:<15}'.format(i, 'inactive'))
            sys.exit(0)
        if args.info:
            hyper_info = conn.getInfo()
            used_mem = sum([conn.lookupByName(i).info()[2] for i in vsorted])
            print('{0:<30}{1:<15}'.format('Hostname:', conn.getHostname()))
            print('{0:<30}{1:<15}'.format('CPU count:', hyper_info[2]))
            print('{0:<30}{1:<15}'.format('CPU MHz:', hyper_info[3]))
            print('{0:<30}{1:<15}'.format('Architecture:', hyper_info[0]))
            print('{0:<30}{1:<15}'.format('Memory total:', str(hyper_info[1]) + 'MB'))
            print('{0:<30}{1:<15}'.format('Memory used:', str(used_mem / 1024) + 'MB'))
            print('{0:<30}{1:<15}'.format('Memory free:',
                str(hyper_info[1] - used_mem / 1024) + 'MB'))
            sys.exit(0)
        if not args.ip:
            lsvirt(args.storage, args.volumes)
            sys.exit(0)
        if args.uri == 'qemu:///system':
            print('{0:<30}{1:<15}'.format('Name', 'IP'))
            for i in sorted(vsorted):
                ip = Net(conn).ip(i)
                print('{0:<30}{1:<15}'.format(i, str(ip)))
        else:
            print('Not available for remote connections')

# Import and Create section
    if args.sub == 'import':
        if not args.xml and not args.image:
            print('Either -xml or -i should be specified')
            sys.exit(1)
        mem = argcheck(args.mem)
        if not args.mac and not args.xml:
            mac = randomMAC()
        elif not is_mac_addr(args.mac):
            print('Incorrect mac address: {0}'.format(args.mac))
            sys.exit(1)
        else:
            mac = args.mac
        if args.xml and not args.image:
            upload = False
            template = xml2tmpl(args.xml.read(), args.name, mac=mac)
        else:
            # LXC
            if uri_lxc(args.uri):
                upload = False
                if not os.path.isdir(args.image):
                    if not args.xml:
                        print('No image and xml specified')
                        sys.exit(1)
                elif not args.xml:
                    template = prepare_tmpl(args.name, mac, args.cpus, mem,
                                            args.image, '', '', args.net, 'lxc')
                else:
                    template = xml2tmpl(args.xml.read(), args.name, args.image,
                                        'format', 'mount', mac)
            else:   # QEMU
                if not os.path.isfile(args.image):
                    print('{0} not found'.format(args.image))
                    sys.exit(1)
                format = find_image_format(args.image)
                imgsize = os.path.getsize(args.image)
                upload = True
                image = Disk(conn, args.pool).create_vol(args.name, imgsize, format)
                if is_lvm(args.pool):
                    dtype = 'block'
                else:
                    dtype = 'file'
                if args.xml:
                    template = xml2tmpl(args.xml.read(), args.name, image, format, dtype, mac)
                elif not args.xml:
                    template = prepare_tmpl(args.name, mac, args.cpus, mem, image,
                                            format, dtype, args.net, 'kvm')
        try:
            conn.defineXML(template)
            print('{0} imported'.format(args.name))
        except libvirt.libvirtError:
            sys.exit(1)
        if upload:
            ret = Disk(conn, args.pool).upload_vol(args.name, args.image)
            if not ret:
                print('Upload failed. Exiting')
                sys.exit(1)

# Create section
    if args.sub == 'create':
        mem = argcheck(args.mem)
        if not args.mac:
            mac = randomMAC()
        elif not is_mac_addr(args.mac):
            print('Incorrect mac address: {0}'.format(args.mac))
            sys.exit(1)
        else:
            mac = args.mac
        format = args.dformat
        imgsize = argcheck(args.size) * 1024
        args.image = args.name
        if is_lvm(args.pool):
            dtype = 'block'
        else:
            dtype = 'file'
        image = Disk(conn, args.pool).create_vol(args.name, imgsize, format)
        template = prepare_tmpl(args.name, mac, args.cpus, mem, image, format,
            dtype, args.net)
        try:
            conn.defineXML(template)
            print('{0} created'.format(args.name))
        except libvirt.libvirtError:
            sys.exit(1)

# Up section
    if args.sub == 'up':
        try:
            dom = conn.lookupByName(args.name)
            s = dom.create()
            if s == 0:
                print('{0} started'.format(args.name))
        except libvirt.libvirtError:
            sys.exit(1)

# Down section
    if args.sub == 'down':
        try:
            dom = conn.lookupByName(args.name)
            s = dom.destroy()
            if s == 0:
                print('{0} powered off'.format(args.name))
        except libvirt.libvirtError:
            sys.exit(1)

# Rm section
    if args.sub == 'rm':
        pool = get_stor(args.name)
        vol = get_stor(args.name, 0)
        try:
            conn.lookupByName(args.name).undefine()
            print('{0} removed'.format(args.name))
        except libvirt.libvirtError:
            sys.exit(1)
        if args.full and vol:
            Disk(conn, pool).delete_vol(vol)
            print('Volume {0} removed'.format(vol))

# Suspend section
    if args.sub == 'suspend':
        try:
            dom = conn.lookupByName(args.name)
            if not args.f:
                if args.uri != 'qemu:///system':
                    print('Option -f is required for remote connection')
                    sys.exit(1)
                args.f = args.name + '.sav'
            dom.save(args.f)
            print('{0} suspended into {1}'.format(args.name, args.f))
        except:
            sys.exit(1)

# Resume section
    if args.sub == 'resume':
        if not args.f:
            if args.uri != 'qemu:///system':
                print('Option -f is required for remote connection')
                sys.exit(1)
            args.f = args.name + '.sav'
        try:
            conn.restore(args.f)
            print('{0} resumed from {1}'.format(args.name, args.f))
        except libvirt.libvirtError:
            sys.exit(1)

# Export section
    if args.sub == 'export':
        if not args.xml and not args.image:
            print('Nothing to export')
            sys.exit(1)
        if args.xml:
            try:
                print(conn.lookupByName(args.name).XMLDesc(0))
            except libvirt.libvirtError:
                sys.exit(1)
        if not args.image:
            sys.exit(0)
        try:
            f = open(args.image, 'w')
            f.close()
        except IOError as e:
            print(e)
            sys.exit(1)
        pool = get_stor(args.name)
        vol = get_stor(args.name, 0)
        if not vol:
            print('Volume attached to {0} not found'.format(args.name))
            sys.exit(1)
        if Disk(conn, pool).download_vol(vol, args.image):
            sys.exit(0)
        else:
            sys.exit(1)

# Console section
    if args.sub == 'console':
        try:
            dom = conn.lookupByName(args.name)
        except libvirt.libvirtError:
            sys.exit(1)
        atexit.register(reset_term)
        attrs = termios.tcgetattr(0)
        tty.setraw(0)
        stream = conn.newStream(libvirt.VIR_STREAM_NONBLOCK)
        try:
            dom.openConsole(None, stream, 0)
        except libvirt.libvirtError:
            sys.exit(1)
        print('Escape character is ^] press Enter')
        run_console = True
        stdin_watch = libvirt.virEventAddHandle(
            0,
            libvirt.VIR_EVENT_HANDLE_READABLE,
            stdin_callback,
            None)
        stream.eventAddCallback(libvirt.VIR_STREAM_EVENT_READABLE, stream_callback, None)
        while run_console:
                libvirt.virEventRunDefaultImpl()

# Volume section
    if args.sub == 'vol':
        if args.add:
            imgsize = argcheck(args.size) * 1024
            Disk(conn, args.pool).create_vol(args.volume, imgsize, 'raw')
            print('Volume {0} created'.format(args.volume))
        else:
            Disk(conn, args.pool).delete_vol(args.volume)
            print('Volume {0} removed'.format(args.volume))
