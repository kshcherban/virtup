virtup
======

Virtup was initially developed to become a partial Vagrant replacement for KVM.

Virtup provides simple command line interface to operate with virtual machines.
It can easily import virtual machine from image into hypervisor, start it 
and provide you with it's ip address to connect via SSH or other preferred method.

## Requirements
Python 2.7

libvirt Python API bindings

## Quick start
To deploy virtual machines on local host, you should install 
libvirtd and requirements. Also ensure that your host is KVM capable.  
If you would like to use external host as hypervisor, then install only requirements.  
You can have running guest in six steps.

1\. Install requirements  
**Ubuntu/Debian**  
```sudo apt-get install libvirt-bin python-libvirt```

**CentOS/Fedora**  
```sudo yum install qemu-kvm libvirt libvirt-python```

2\. Download one of prebuild boxes. You can download them from [here](http://yadi.sk/d/KJROKkGb6Xv7u)

```wget -O ubuntu.tgz http://goo.gl/5p4DjB```

3\. Unpack it

```tar -xzf ubuntu.tgz```

4\. Import it with preferred name, optionally memory, cpu, net and storage pool can be
specified

```
./virtup.py import -i ubuntu-12.04-amd64.img ubuntu64
Uploading template into volume ubuntu64
done 100.00%
Temporary template written in /tmp/ubuntu64.xml
ubuntu64 created, you can start it now
```

5\. Start it

```
./virtup.py up ubuntu64
ubuntu64 started
```

6\. Open console of virtual machine.  
Also template used in example has passswordless ssh root login.

    ./virtup.py console ubuntu64

## Templates creation
Create virtual machine with ```create``` command or with virsh.  
Install operating system on it, booting it from iso, pxe or your preferred method.  
Start it, install necessary soft. Configure ssh.  
Remove **/etc/udev/rules.d/70-persistent-net.rules** file.  
This is required for network interface name to be eth0 on first boot of newly created machine.  
To enable console access please follow [this](http://www.vanemery.com/Linux/Serial/serial-console.html) manual.  
Shut it down and use it's disk image as template.
