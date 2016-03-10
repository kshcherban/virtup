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

```bash
wget -O debian.xz http://goo.gl/queYqC
unxz debian.xz
```

Or create basic installation from [kickstart file](./ubuntu-kickstart.cfg):

```bash
# Set ubuntu release to create
release="trusty"
# Create vm drive for installation
qemu-img create -f qcow2 ./${release}.qcow2 8G
virt-install --name base-${release} --ram 1024 --disk path=./${release}.qcow2,size=8 \
  --vcpus 1 --os-type linux --os-variant generic --network bridge=virbr0 \
  --graphics none --console pty,target_type=serial --noreboot \
  --location "http://archive.ubuntu.com/ubuntu/dists/${release}/main/installer-amd64/" \
  --extra-args "console=ttyS0,115200n8 ks=http://pastebin.com/raw/eY5ybGfc"
```

4\. Import it with preferred name, optionally memory, cpu, net and storage pool can be
specified

```
./virtup.py import -i ./${release}.qcow2 ubuntu-${release}
Uploading template into volume ubuntu-trusty
done 100.00%
Temporary template written in /tmp/ubuntu-trusty.xml
ubuntu-trusty created, you can start it now
```

5\. Start it

```
./virtup.py up ubuntu-trusty
ubuntu-trusty started
```

6\. Open console of virtual machine.  
Image produced from this example has ubuntu user with password ubuntu.

```
./virtup.py console ubuntu-trusty
```

## Templates creation
Create virtual machine with ```create``` command or with virsh or using kickstart
installation described above.  
Install operating system on it, booting it from iso, pxe or your preferred method.  
Start it, install necessary soft. Configure ssh.  
Remove **/etc/udev/rules.d/70-persistent-net.rules** file.  
This is required for network interface name to be eth0 on first boot of newly created machine.  
To enable console access please follow [this](http://www.vanemery.com/Linux/Serial/serial-console.html) manual.  
Shut it down and use it's disk image as template.
