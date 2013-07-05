#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import time
import random
import libvirt
import argparse
import subprocess
from subprocess import Popen, PIPE
from xml.etree import ElementTree as ET

# Generate random MAC address
def randomMAC():
    mac = [ 0x00, 0x16, 0x3e,
        random.randint(0x00, 0x7f),
        random.randint(0x00, 0xff),
        random.randint(0x00, 0xff) ]
    return ':'.join(map(lambda x: "%02x" % x, mac))

# Create disk image
def createimg(machname, imgsize, imgpath=None):
    if os.path.isfile(imgpath):
        return imgpath
    elif os.path.isdir(imgpath):
        imgpath = imgpath + '/' + machname + '.img'
    else:
        print 'Error! Provided path not found'
        sys.exit(1)
    cmd = "/usr/bin/qemu-img create -f raw {} {}K".format(imgpath, imgsize).split()
    run = Popen(cmd, stdout=PIPE, stderr=PIPE).communicate()
# Print error if is and exit
    if len(run[1]) > 0:
        print run[1]
        sys.exit(1)
    print run[0].rstrip()
    return imgpath

# Prepare template to import with virsh
def preptempl(machname, mac, cpu=1, mem=524288, img=None):
    cmd = '/usr/bin/qemu-img info {}'.format(img).split()
    format = Popen(cmd, stdout=PIPE, stderr=PIPE).communicate()[0].split()[4]
    tmpl = '''
<domain type='kvm'>
  <name>{0}</name>
  <memory unit='KiB'>{1}</memory>
  <currentMemory unit='KiB'>{1}</currentMemory>
  <vcpu placement='static'>{2}</vcpu>
  <os>
    <type arch='x86_64' machine='pc-1.1'>hvm</type>
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
    <emulator>/usr/bin/kvm</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='{3}' cache='none' io='native'/>
      <source file='{4}'/>
      <target dev='vda' bus='virtio'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x05' function='0x0'/>
    </disk>
    <disk type='block' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <target dev='hdc' bus='ide'/>
      <readonly/>
      <address type='drive' controller='0' bus='1' target='0' unit='0'/>
    </disk>
    <controller type='usb' index='0'>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x2'/>
    </controller>
    <controller type='ide' index='0'>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x1'/>
    </controller>
    <interface type='network'>
      <mac address='{5}'/>
      <source network='default'/>
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
</domain> '''.format(machname, mem, cpu, format, img, mac)
    tmpf = '/tmp/' + machname + str(random.randint(1000, 9999)) + '.xml'
    f = open(tmpf, 'w')
    f.write(tmpl)
    f.close()
    print 'Temporary template written in', tmpf
    return tmpl

# Get MAC address of virtual domain
def getmac(dom):
    xe = ET.fromstring(dom.XMLDesc(0))
    for iface in xe.findall('.//devices/interface'):
        mac = iface.find('mac').get('address')
    return mac

# Get IP address of running machine
def getip(mac):
    try:
        lease = open('/var/lib/libvirt/dnsmasq/default.leases', 'r')
    except:
        print "Can't open /var/lib/libvirt/dnsmasq/default.leases"
        sys.exit(1)
    t = 0
    ip = None
    print 'Waiting for machine\'s ip...'
    while t < 60:
        lease = open('/var/lib/libvirt/dnsmasq/default.leases', 'r')
        for i in lease.readlines():
            if mac in i:
                ip = i.split()[2]
                t = 60
                break
        time.sleep(1)
        lease.close()
        t += 1
    if not ip:
        return 'not found'
    return ip

# Here we parse all the commands
parser = argparse.ArgumentParser(prog='virtup.py')
parser.add_argument('-v', '--version', action='version', version='%(prog)s 0.1')
subparsers = parser.add_subparsers(dest='sub')
# Parent argparser to contain repeated arguments
suparent = argparse.ArgumentParser(add_help=False)
suparent.add_argument('name', type=str, help='virtual machine name')
parent = argparse.ArgumentParser(add_help=False)
parent.add_argument('-c', dest='cpus', type=int, default=1, 
        help='amount of CPU cores, default is 1')
parent.add_argument('-m', dest='mem', metavar='RAM', type=str, default='512M', 
        help='amount of memory, can be M or G, default is 512M')
box_add = subparsers.add_parser('add', parents=[parent, suparent], 
        description='Add virtual machine from image file', 
        help='Add virtual machine from image file')
box_add.add_argument('-i', dest='image', type=str, metavar='IMAGE',
        help='image file location')
box_create = subparsers.add_parser('create', parents=[parent, suparent], 
        description='Create virtual machine from scratch', 
        help='Create virtual machine')
box_create.add_argument('-p', dest='image', type=str, default='/var/lib/libvirt/images', 
        help='path to directory where image will be stored, default is /var/lib/libvirt/images')
box_create.add_argument('-s', dest='size', type=str, default='8G', 
        help='disk image size, can be M or G, default is 8G')
box_ls = subparsers.add_parser('ls', help='List virtual machines', 
        description='List existing virtual machines and their state')
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
        help='File where machine state will be saved, default is ./<name>.sav')
box_resume = subparsers.add_parser('resume', parents=[suparent],
        help='Resume virtual machine',
        description='Resume virtual machine from file')
box_resume.add_argument('-f', metavar='FILE',  
        help='File from which machine state will be resumed, default is ./<name>.sav')
help_c = subparsers.add_parser('help')
help_c.add_argument('command', nargs="?", default=None)

def argcheck(arg):
    if arg[-1].lower() == 'm':
        return int(arg[:-1]) * 1024
    elif arg[-1].lower() == 'g':
        return int(arg[:-1]) * 1024 * 1024
    else:
        print 'Error! Format can be <int>M or <int>G'
        sys.exit(1)


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
    conn = libvirt.open('qemu:///system')
    try: mem = argcheck(args.mem)
    except: pass
    if args.sub == 'ls':
        vsorted = [conn.lookupByID(i).name() for i in conn.listDomainsID()]
        for i in sorted(vsorted):
            print '{:<30}{:>10}'.format(i, 'up')
        for i in sorted(conn.listDefinedDomains()):
            print '{:<30}{:>10}'.format(i, 'down')
        sys.exit(0)
    if args.sub == 'create':
        imgsize = argcheck(args.size)
    else:
        imgsize = argcheck('8G')
    if args.sub == 'create' or args.sub == 'add':
        if not os.path.isfile(args.image) and args.sub == 'add':
            print args.image, 'not found'
            sys.exit(1)
        mac = randomMAC()
        image = createimg(args.name, imgsize, os.path.abspath(args.image))
        template = preptempl(args.name, mac, args.cpus, mem, image)
        try:
            conn.defineXML(template)
            print args.name, 'created, you can start it now'
        except libvirt.libvirtError:
            sys.exit(1)
    if args.sub == 'up':
        try:
            dom = conn.lookupByName(args.name)
            s = dom.create()
            if s == 0:
                print args.name, 'started'
            ip = getip(getmac(dom))
            print 'You can connect to running machine at', ip
        except libvirt.libvirtError:
            sys.exit(1)
    if args.sub == 'down':
        try:
            dom = conn.lookupByName(args.name)
            s = dom.destroy()
            if s == 0:
                print args.name, 'powered off'
                sys.exit(0)
        except libvirt.libvirtError:
            sys.exit(1)
    if args.sub == 'rm':
        try:
            dom = conn.lookupByName(args.name)
            xe = ET.fromstring(dom.XMLDesc(0))
            dom.undefine()
            print args.name, 'removed'
            if args.full:
                imgfile = xe.find('.//devices/disk').find('source').get('file')
                os.remove(imgfile)
        except libvirt.libvirtError:
            sys.exit(1)
        except OSError, e:
            print 'Can not remove assigned image'
            print e
            sys.exit(1)
    if args.sub == 'suspend':
        try:
            dom = conn.lookupByName(args.name)
            if not args.f:
                args.f = './' + args.name + '.sav'
            dom.save(args.f)
            print args.name, 'suspended to', args.f
        except:
            sys.exit(1)
    if args.sub == 'resume':
        saved = './' + args.name + '.sav'
        if not args.f and not os.path.isfile(saved):
            print 'Resume file not provided and default saved not found'
            sys.exit(1)
        elif not args.f:
            args.f = saved
        if not os.path.isfile(args.f):
            print args.f, 'not found'
            sys.exit(1)
        try:
            conn.restore(args.f)
            print args.name, 'resumed from', args.f
        except libvirt.libvirtError:
            sys.exit(1)
    
