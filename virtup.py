#!/usr/bin/python -u
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
import time
import random
import termios
import atexit
import libvirt
import argparse
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
        self.voltmpl = '''
<volume>
  <name>{0}</name>
  <capacity>{1}</capacity>
  <target>
    <path>{2}/{0}</path>
    <format type='{3}'/>
    <permissions>
      <mode>0660</mode>
    </permissions>
  </target>
</volume>
'''

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
        tmpl = self.voltmpl.format(name, imgsize, spath, imgtype)
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
        stream.download(vol, offset, length, flags)
        # Open file
        f = open(src, 'w')
        # Start transfer
        total = 0
        print 'Downloading volume {0} into {1}'.format(vol.name(), 
                os.path.abspath(src))
        try:
            while True:
                ret = stream.recv(256000)
                if not ret:
                    break
                f.write(ret)
                total += len(ret)
                sys.stderr.write('\rdone {0:.2%}'.format(float(total)/size))
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
            stream.upload(vol, offset, length, flags)
            # Open source file
            fileobj = open(src, "r")
            # Start transfer
            total = 0
            print 'Uploading volume {0} from {1}'.format(vol.name(),
                    os.path.abspath(src))
            while True:
                blocksize = 256000
                data = fileobj.read(blocksize)
                if not data:
                    break
                safe_send(data)
                total += len(data)
                sys.stderr.write('\rdone {0:.2%}'.format(float(total)/size))
            # Cleanup
            stream.finish()
            print('')
        except:
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
        for j in range(1,5):
            globals()["oct" + str(j)] = [i for i in range(int(start.split('.')[j-1]),
                int(end.split('.')[j-1]) + 1)]
        iprange = []
        for i in oct1:
            for j in oct2:
                for m in oct3:
                    for n in oct4:
                        iprange.append(str(i)+'.'+str(j)+'.'+str(m)+'.'+str(n))
        return iprange

    @staticmethod
    def is_mac_addr(mac):
        mac = mac.rstrip().lower()
        if re.match("[0-9a-f]{2}(:)[0-9a-f]{2}(\\1[0-9a-f]{2}){4}$", mac):
            return True
        return False

    def mac(self, machname):
        """Return virtual machine MAC address"""
        xe = ET.fromstring(self.conn.lookupByName(machname).XMLDesc(0))
        for iface in xe.findall('.//devices/interface'):
            mac = iface.find('mac').get('address')
        return mac

    def ifname(self, machname):
        """Extract network interface name from domain XML decription"""
        dom = self.conn.lookupByName(machname).XMLDesc(0)
        net = ET.fromstring(dom).find('.//interface/source').get('network')
        if not net:
            return ET.fromstring(dom).find('.//interface/source').get('bridge')
        ifname = ET.fromstring(self.conn.networkLookupByName(net).XMLDesc(0)
            ).find('.//bridge').get('name')
        return ifname

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

    @staticmethod
    def get_subnet(ifname):
        """Return CIDR got from /bin/ip output"""
        patt = re.compile(r'inet\s*(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,3})')
        return patt.findall(os.popen('ip a s ' + ifname).read())[0]

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
    mac = [ 0x00, 0x16, 0x3e,
        random.randint(0x00, 0x7f),
        random.randint(0x00, 0xff),
        random.randint(0x00, 0xff) ]
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
def get_stor(machname, pool=True, lxc=False):
    try:
        dom = conn.lookupByName(machname)
    except libvirt.libvirtError:
        sys.exit(1)
    xe = ET.fromstring(dom.XMLDesc(0))
    if lxc:
        return xe.find('.//devices/filesystem/source').get('dir')[1:]
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
        data = {}
        for i in conn.listStoragePools():
            data[i] = conn.storagePoolLookupByName(i).listVolumes()
    if isinstance(data, list):
        for p in data:
            o = conn.storagePoolLookupByName(p)
            if ET.fromstring(o.XMLDesc(0)).find('.//path').text == path:
                return p
    else:
        for i in data.iteritems():
            for v in i[1]:
                o = conn.storagePoolLookupByName(i[0]).storageVolLookupByName(v)
                if ET.fromstring(o.XMLDesc(0)).find('.//path').text == path:
                    return v
    return None

# Prepare template to import with virsh
def prepare_tmpl(machname, mac, cpu, mem, img, format, dtype, net):
    if net == 'default':
        ntype = 'network'
    else:
        ntype = 'bridge'
    if dtype == 'file':
        src = 'file'
    else:
        src = 'dev'
    tmpl = '''
<domain type='kvm'>
  <name>{0}</name>
  <memory>{1}</memory>
  <vcpu placement='static'>{2}</vcpu>
  <os>
    <type arch='x86_64' machine='pc'>hvm</type>
    <boot dev='hd'/>
  </os>
  <features>
    <acpi/>
    <apic/>
    <pae/>
  </features>
  <clock offset='utc'/>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>restart</on_crash>
  <devices>
    <disk type='{6}' device='disk'>
      <driver name='qemu' type='{3}' cache='none' io='native'/>
      <source {7}='{4}'/>
      <target dev='vda' bus='virtio'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x05' function='0x0'/>
    </disk>
    <disk type='block' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <target dev='hdc' bus='ide'/>
      <readonly/>
      <address type='drive' controller='0' bus='1' target='0' unit='0'/>
    </disk>
    <controller type='ide' index='0'>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x1'/>
    </controller>
    <interface type='{8}'>
      <mac address='{5}'/>
      <source {8}='{9}'/>
      <model type='virtio'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x03' function='0x0'/>
    </interface>
    <serial type='pty'>
      <target port='0'/>
    </serial>
    <console type='pty'>
      <target type='serial' port='0'/>
    </console>
    <input type='tablet' bus='usb'/>
    <input type='mouse' bus='ps2'/>
    <graphics type='vnc' port='-1' autoport='yes'/>
    <sound model='ich6'>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x04' function='0x0'/>
    </sound>
    <video>
      <model type='cirrus' vram='9216' heads='1'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x02' function='0x0'/>
    </video>
    <memballoon model='virtio'>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x06' function='0x0'/>
    </memballoon>
  </devices>
</domain> '''.format(machname, mem, cpu, format, img, mac, dtype, src, ntype, net)
    tmpf = '/tmp/' + machname + '.xml'
    f = open(tmpf, 'w')
    f.write(tmpl)
    f.close()
    print 'Temporary template written in', tmpf
    return tmpl

# Return modified xml from imported file ready for defining guest 
def xml2tmpl(xmlf, machname, image=None, format=None, dtype=None):
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
    return ET.tostring(xe)

def argcheck(arg):
    if arg[-1].lower() == 'm':
        return int(arg[:-1]) * 1024
    elif arg[-1].lower() == 'g':
        return int(arg[:-1]) * (1024 ** 2)
    else:
        print 'Error! Format can be <int>M or <int>G'
        sys.exit(1)

def lsvirt(storage, volumes):
    pools = sorted(conn.listStoragePools())
    # List storage pools
    if storage:
        print '{0:<30}{1:<10}{2:<10}{3:<10}{4:<10}'.format('Pool name', 'Size',
            'Used', 'Avail', 'Use')
        for i in pools:
            p = conn.storagePoolLookupByName(i).info()
            use = '{0:.2%}'.format(float(p[2]) / float(p[1]))
            print '{0:<30}{1:<10}{2:<10}{3:<10}{4:<10}'.format(i, convert_bytes(p[1]),
                convert_bytes(p[2]), convert_bytes(p[3]), use)
        sys.exit(0)
    # List volumes
    if volumes:
        # Find list of machines and create dict with list of vols associated to them
        ml = [conn.lookupByID(i).name() for i in conn.listDomainsID()] + conn.listDefinedDomains()
        md = {}
	for i in ml:
	    md[get_stor(i,0)] = i
        print '{0:<15}{1:<30}{2:<10}{3:<10}{4:<10}'.format('Pool', 'Volume', 'Size', 
                'Use', 'Used by')
        for p in pools:
            pinf = conn.storagePoolLookupByName(p).info()
            print p
            print '{0:>15}'.format('\\')
            vols = conn.storagePoolLookupByName(p).listVolumes()
            for v in sorted(vols):
                if v not in md:
                    md[v] = None
                vinf = conn.storagePoolLookupByName(p).storageVolLookupByName(v).info()
                use = '{0:.2%}'.format(float(vinf[2]) / float(pinf[1])) 
                print '{0:<15}{1:<30}{2:<10}{3:<10}{4:<10}'.format(' ', v, 
                        convert_bytes(vinf[2]), use, md[v])
        sys.exit(0)
    # List machines
    vsorted = [conn.lookupByID(i).name() for i in conn.listDomainsID()]
    print '{0:<30}{1:<15}{2:<15}{3:>10}'.format('Name', 'CPUs', 'Memory', 'State')
    for i in sorted(vsorted):
        j = conn.lookupByName(i).info()
        print '{0:<30}{1:<15}{2:<15}{3:>10}'.format(i, j[3], 
            convert_bytes(j[2] * 1024), 'up')
    for i in sorted(conn.listDefinedDomains()):
        j = conn.lookupByName(i).info()
        print '{0:<30}{1:<15}{2:<15}{3:>10}'.format(i, j[3], 
            convert_bytes(j[2] * 1024), 'down')
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

# LXC sections
def prepare_tmpl_lxc(machname, mac, cpu, mem, img, net):
    if net == 'default':
        ntype = 'network'
    else:
        ntype = 'bridge'
    dtype = 'mount'
    tmpl = '''
<domain type='lxc'>
  <name>{0}</name>
  <memory>{1}</memory>
  <vcpu placement='static'>{2}</vcpu>
  <os>
    <type>exe</type>
    <init>/sbin/init</init>
  </os>
  <features>
    <acpi/>
    <apic/>
    <pae/>
  </features>
  <clock offset='utc'/>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>restart</on_crash>
  <devices>
    <interface type='{3}'>
      <mac address='{5}'/>
      <source {3}='{6}'/>
    </interface>
    <filesystem type='{4}' accessmode='passthrough'>
        <source dir='{7}'/>
        <target dir='/'/>
    </filesystem>
    <console type='pty'>
      <target type='lxc' port='0'/>
      <alias name='console0'/>
    </console>
  </devices>
</domain>
'''.format(machname, mem, cpu, ntype, dtype, mac, net, img)
    tmpf = '/tmp/' + machname + '.xml'
    f = open(tmpf, 'w')
    f.write(tmpl)
    f.close()
    print 'Temporary template written in', tmpf
    return tmpl

def uri_lxc(uri):
    if re.findall('lxc', uri[:7]):
        return True
    return False

# Operations with ZFS
def is_snapshot(image):
    try:
        os.popen('zfs list -t snapshot %s' % (image)).read()[0]
    except IndexError:
        return False
    return True

def clone_snapshot(image, machname):
    zpool = image.split('/')[0]
    zpoolpath = os.popen('zfs list -H {0}'.format(zpool)).read().split()[-1]
    try:
        os.popen('zfs clone {0} {1}/{2}'.format(image, zpool, machname))
    except:
        print 'Error cloning snaphot {0} into {2}/{1}'.format(image, machname, zpool)
        sys.exit(1)
    return '{0}/{1}'.format(zpoolpath, machname)

# End LXC

# MAC address condition procedure
def prepare_mac(mac=None):
    if not mac:
        return randomMAC()
    elif Net(conn).is_mac_addr(mac):
        return mac
    else:
        print 'Incorrect MAC address:', mac
        sys.exit(1)

# Here we parse all the commands
parser = argparse.ArgumentParser(prog='virtup.py')
parser.add_argument('-c', '--connect', dest='uri', type=str, default='lxc:///',
        help='hypervisor connection URI, default is lxc:///')
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
parent.add_argument('-mac', dest='mac', metavar='MAC', type=str,
        help='MAC address')
parent.add_argument('-m', dest='mem', metavar='RAM', type=str, default='512M',
        help='amount of memory, can be M or G, default is 512M')
parent.add_argument('-p', dest='pool', metavar='POOL', type=str,
        default='default',
        help='storage pool name, default is "default"')
box_add = subparsers.add_parser('add', parents=[parent, suparent],
        description='Add virtual machine from image file or XML description',
        help='Add virtual machine from image/XML file')
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
box_export = subparsers.add_parser('export', parents=[suparent],
        description='Export virtual machine description/disk image',
        help='Export virtual machine')
box_export.add_argument('-xml', dest='xml', action='store_true',
        help='virtual machine XML description will be printed')
box_export.add_argument('-i', dest='image', type=str,
        help='image file name to export disk image')
box_ls = subparsers.add_parser('ls', help='List virtual machines',
        description='List existing virtual machines, active storage pools, ip addresses')
box_ls.add_argument('-s', dest='storage', action='store_true',
        help='list active storage pools')
box_ls.add_argument('-ip', dest='ip', action='store_true',
        help='list ip of running virtual machines')
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


# Ls command section
    if args.sub == 'ls':
        if args.ip + args.storage + args.volumes >= 2:
            print 'Please specify only one option at a time'
            sys.exit(1)
        if not args.ip:
            lsvirt(args.storage, args.volumes)
            sys.exit(0)
        if args.uri == 'qemu:///system' or args.uri == 'lxc:///':
            vsorted = [conn.lookupByID(i).name() for i in conn.listDomainsID()]
            print '{0:<30}{1:<15}'.format('Name', 'IP')
            for i in sorted(vsorted):
                ip = Net(conn).ip(i)
                print '{0:<30}{1:<15}'.format(i, ip)
        else:
            print 'Not available for remote connections'

# Add and Create section
    if args.sub == 'add':
        if not args.xml and not args.image:
            print 'Either -xml or -i should be specified'
            sys.exit(1)
        mem = argcheck(args.mem)
        mac = prepare_mac(args.mac)
        if args.xml:
            xml = args.xml.read()
        if not args.image:
            upload = False
        # For LXC we do not upload image as it's a folder
        elif args.image and uri_lxc(args.uri):
            if is_snapshot(args.image):
                image = clone_snapshot(args.image, args.name)
            else:
                image = args.image
            upload = False
        else:
            if not os.path.isfile(args.image):
                print args.image, 'not found'
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
                template = xml2tmpl(xml, args.name, image, format, dtype)
        if args.xml and not args.image:
            template = xml2tmpl(xml, args.name)
        elif not args.xml:
            # Condition for LXC
            if uri_lxc(args.uri):
                template = prepare_tmpl_lxc(args.name, mac, args.cpus, mem, image, 
                            args.net)
                # Replace hostname
                hostfile = image + '/etc/sysconfig/network'
                with open(hostfile, 'r') as fp:
                    oldfile = fp.read()
                newfile  = re.sub(r'HOSTNAME=.+',r'HOSTNAME=%s' % (args.name), oldfile)
                with open(hostfile, 'w') as fp:
                    fp.write(newfile)
            else:
                template = prepare_tmpl(args.name, mac, args.cpus, mem, image, format,
                dtype, args.net)
        try:
            conn.defineXML(template)
            print args.name, 'created'
        except libvirt.libvirtError:
            sys.exit(1)
        if upload:
            ret = Disk(conn, args.pool).upload_vol(args.name, args.image)
            if not ret:
                print 'Upload failed. Exiting'
                sys.exit(1)

# Create section
    if args.sub == 'create':
        mem = argcheck(args.mem)
        mac = prepare_mac(args.mac)
        format = 'raw'
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
            print args.name, 'created'
        except libvirt.libvirtError:
            sys.exit(1)

# Up section
    if args.sub == 'up':
        try:
            dom = conn.lookupByName(args.name)
            s = dom.create()
            if s == 0:
                print args.name, 'started'
        except libvirt.libvirtError:
            sys.exit(1)

# Down section
    if args.sub == 'down':
        try:
            dom = conn.lookupByName(args.name)
            s = dom.destroy()
            if s == 0:
                print args.name, 'powered off'
        except libvirt.libvirtError:
            sys.exit(1)

# Rm section
    if args.sub == 'rm':
        pool = get_stor(args.name)
        vol = get_stor(args.name, 0)
        if uri_lxc(args.uri):
            vol = get_stor(args.name, 0, 1)
        try:
            conn.lookupByName(args.name).undefine()
            print args.name, 'removed'
        except libvirt.libvirtError:
            sys.exit(1)
        if args.full:
            if uri_lxc(args.uri):
                os.popen('zfs destroy -f %s' % (vol))
            else:
                Disk(conn, pool).delete_vol(vol)
            print 'Volume {0} removed'.format(vol)

# Suspend section
    if args.sub == 'suspend':
        try:
            dom = conn.lookupByName(args.name)
            if not args.f:
                if args.uri != 'qemu:///system':
                    print 'Option -f is required for remote connection'
                    sys.exit(1)
                args.f = args.name + '.sav'
            dom.save(args.f)
            print args.name, 'suspended into', args.f
        except:
            sys.exit(1)

# Resume section
    if args.sub == 'resume':
        if not args.f:
            if args.uri != 'qemu:///system':
                print 'Option -f is required for remote connection'
                sys.exit(1)
            args.f = args.name + '.sav'
        try:
            conn.restore(args.f)
            print args.name, 'resumed from', args.f
        except libvirt.libvirtError:
            sys.exit(1)

# Export section
    if args.sub == 'export':
        if not args.xml and not args.image:
            print 'Nothing to export'
            sys.exit(1)
        if args.xml:
            try:
                print conn.lookupByName(args.name).XMLDesc(0)
            except libvirt.libvirtError:
                sys.exit(1)
        if not args.image:
            sys.exit(0)
        try:
            f = open(args.image, 'w')
            f.close()
        except IOError, e:
            print e
            sys.exit(1)
        pool = get_stor(args.name)
        vol = get_stor(args.name, 0)
        if not vol:
            print 'Volume attached to {0} not found'.format(args.name)
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
        print 'Escape character is ^] press Enter'
        run_console = True
        stdin_watch = libvirt.virEventAddHandle(0, libvirt.VIR_EVENT_HANDLE_READABLE,
                stdin_callback, None)
        stream.eventAddCallback(libvirt.VIR_STREAM_EVENT_READABLE, stream_callback, None)
        while run_console:
                libvirt.virEventRunDefaultImpl()
