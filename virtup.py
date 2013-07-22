#!/usr/bin/env python
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
import time
import random
import libvirt
import argparse
from multiprocessing import Pool
from xml.etree import ElementTree as ET

# Generate random MAC address
def randomMAC():
    mac = [ 0x00, 0x16, 0x3e,
        random.randint(0x00, 0x7f),
        random.randint(0x00, 0xff),
        random.randint(0x00, 0xff) ]
    return ':'.join(map(lambda x: "%02x" % x, mac))

# Guess image type
def find_image_format(filepath):
    f = open(filepath).read(1024)
    if 'QFI' in f:
        return 'qcow2'
    if 'Virtual Disk Image' in f:
        return 'vdi'
    if 'virtualHWVersion' in f:
        return 'vmdk'
    return 'raw'

# Check if storage pool is LVM or dir
def is_lvm(pool):
    s = conn.storagePoolLookupByName(pool)
    if ET.fromstring(s.XMLDesc(0)).get('type') == 'logical':
        return 1
    return 0

# Upload image into volume
def upload_vol(vol, src):
    # Build stream object
    stream = conn.newStream(0)
    def safe_send(data):
        while True:
            ret = stream.send(data)
            if ret == 0 or ret == len(data):
                break
            data = data[ret:]
    # Build placeholder volume
    size = os.path.getsize(src)
    basename = os.path.basename(src)
    try:
        # Register upload
        offset = 0
        length = size
        flags = 0
        stream.upload(vol, offset, length, flags)
        # Open source file
        fileobj = file(src, "r")
        # Start transfer
        total = 0
        print 'Uploading template into volume', vol.name()
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

# Create storage volume
def create_vol(machname, imgsize, imgpath, stor):
    if os.path.isfile(imgpath):
        imgsize = os.path.getsize(imgpath)
        format = find_image_format(imgpath)
        upload = True
    else:
        imgsize = imgsize * 1024
        format = 'raw'
        upload = False
    try:
        s = conn.storagePoolLookupByName(stor)
    except libvirt.libvirtError:
        sys.exit(1)
    xe = ET.fromstring(s.XMLDesc(0))
    # find storage pool path
    spath = [i for i in xe.find('.//path').itertext()][0]
    tmpl = '''
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
'''.format(machname, imgsize, spath, format)
    try:
        v = s.createXML(tmpl, 0)
    except libvirt.libvirtError:
        sys.exit(1)
    if upload:
        upres = upload_vol(v, imgpath)
        if not upres:
            print 'Error importing template into storage pool'
            sys.exit(1)
    return spath + '/' + machname, format


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
        return 0

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

    def ip(self, machname):
        """Get virtual machine ip address"""
        ipaddr = self.arp2ip(self.mac(machname))
        if ipaddr:
            return ipaddr
        pool = Pool(processes=50)
        cidr = self.get_subnet(self.ifname(machname))
        iprange = self.block2range(self.cidr2block(cidr)[0], self.cidr2block(cidr)[1])
        pool.map(ping, iprange, 1)
        pool.close()
        pool.join()
        return self.arp2ip(self.mac(machname))


def ping(ip):
    """Ping ip"""
    if ip.split('.')[-1] != '0' and ip.split('.')[-1] != '255':
        os.popen("ping -q -c1 " + ip + ' 2>/dev/null')
        return 0

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

def argcheck(arg):
    if arg[-1].lower() == 'm':
        return int(arg[:-1]) * 1024
    elif arg[-1].lower() == 'g':
        return int(arg[:-1]) * (1024 ** 2)
    else:
        print 'Error! Format can be <int>M or <int>G'
        sys.exit(1)

def lsvirt(storage):
    if storage:
        print '{0:<30}{1:<10}{2:<10}{3:<10}{4:<10}'.format('Pool name', 'Size',
            'Used', 'Avail', 'Use')
        for i in sorted(conn.listStoragePools()):
            p = conn.storagePoolLookupByName(i).info()
            use = '{0:.2%}'.format(float(p[2]) / float(p[1]))
            print '{0:<30}{1:<10}{2:<10}{3:<10}{4:<10}'.format(i, convert_bytes(p[1]),
                convert_bytes(p[2]), convert_bytes(p[3]), use)
        sys.exit(0)
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

# Here we parse all the commands
parser = argparse.ArgumentParser(prog='virtup.py')
parser.add_argument('-u', '--uri', type=str, default='qemu:///system',
        help='hypervisor connection URI, default is qemu:///system')
parser.add_argument('-v', '--version', action='version', version='%(prog)s 0.1')
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
box_add = subparsers.add_parser('add', parents=[parent, suparent],
        description='Add virtual machine from image file',
        help='Add virtual machine from image file')
box_add.add_argument('-i', dest='image', type=str, metavar='IMAGE',
        required=True,
        help='template image file location')
box_create = subparsers.add_parser('create', parents=[parent, suparent],
        description='Create virtual machine from scratch',
        help='Create virtual machine')
box_create.add_argument('-s', dest='size', type=str, default='8G',
        help='disk image size, can be M or G, default is 8G')
box_ls = subparsers.add_parser('ls', help='List virtual machines/storage pools',
        description='List existing virtual machines and their state or active storage pools')
box_ls.add_argument('-s', dest='storage', action='store_true',
        help='if specified active storage pools will be listed')
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
    if len(sys.argv) < 2:
        parser.parse_args(['--help'])
    args = parser.parse_args()
# Help command emulation
    if args.sub == "help":
        if not args.command:
            parser.parse_args(['--help'])
        else:
            parser.parse_args([args.command, '--help'])
    conn = libvirt.open(args.uri)
    try: mem = argcheck(args.mem)
    except: pass
# Ls command section
    if args.sub == 'ls':
        lsvirt(args.storage)
# Add and Create section
    if args.sub == 'create' or args.sub == 'add':
        if args.sub == 'add':
            if not os.path.isfile(args.image):
                print args.image, 'not found'
                sys.exit(1)
        if args.sub == 'create':
            imgsize = argcheck(args.size)
            args.image = 'None'
        else:
            imgsize = 0
        mac = randomMAC()
        image, format = create_vol(args.name, imgsize, args.image, args.pool)
        if is_lvm(args.pool):
            dtype = 'block'
        else:
            dtype = 'file'
        template = prepare_tmpl(args.name, mac, args.cpus, mem, image, format,
            dtype, args.net)
        try:
            conn.defineXML(template)
            print args.name, 'created, you can start it now'
        except libvirt.libvirtError:
            sys.exit(1)
# Up section
    if args.sub == 'up':
        try:
            dom = conn.lookupByName(args.name)
            s = dom.create()
            if s == 0:
                print args.name, 'started'
            if args.uri == 'qemu:///system':
                print 'Waiting for ip...'
                time.sleep(20)
                ip = Net(conn).ip(args.name)
                if ip:
                    print ip
        except libvirt.libvirtError:
            sys.exit(1)
# Down section
    if args.sub == 'down':
        try:
            dom = conn.lookupByName(args.name)
            s = dom.destroy()
            if s == 0:
                print args.name, 'powered off'
                sys.exit(0)
        except libvirt.libvirtError:
            sys.exit(1)
# Rm section
    if args.sub == 'rm':
        try:
            dom = conn.lookupByName(args.name)
            xe = ET.fromstring(dom.XMLDesc(0))
            dom.undefine()
            print args.name, 'removed'
            if args.full:
                pv = xe.find('.//devices/disk').find('source').get('file')
                if not pv:
                    pv = xe.find('.//devices/disk').find('source').get('dev')
                vol = pv.split('/')[-1]
                sp = {}
                for i in conn.listStoragePools():
                    sp[i] = conn.storagePoolLookupByName(i).listVolumes()
                for i in sp.iteritems():
                    if vol in i[1]: pool = i[0]
                try:
                    conn.storagePoolLookupByName(pool).storageVolLookupByName(vol).delete(0)
                except libvirt.libvirtError:
                    sys.exit(1)
        except libvirt.libvirtError:
            sys.exit(1)
        except OSError, e:
            print 'Can not remove assigned image'
            print e
            sys.exit(1)
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

