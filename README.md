virtup
======

Virtup was initially developed to become a partial Vagrant replacement for KVM.

Virtup provides simple command line interface to operate with virtual machines.
It can easily import virtual machine from image into hypervisor, start it 
and provide you with it's ip address to connect via SSH or other preferred method.

## Requirements
Python >= 2.7 <= 3

libvirt Python API bindings

## Quick start
If you plan to deploy virtual machines on local host, then you should install 
libvirtd and requirements. Also ensure that your host is KVM capable.

If you would like to use external host as hypervisor, then install only requirements.

### Ubuntu/Debian
    sudo apt-get install libvirt-bin python-libvirt

### CentOS/Fedora
    sudo yum install qemu-kvm libvirt libvirt-python

    1\. Download one of prebuild boxes from [here](http://yadi.sk/d/KJROKkGb6Xv7u)

    ```wget https://dl.dropboxusercontent.com/s/0l9wvtnzsl69hx0/ubuntu-12.04-amd64.img.tar.gz```

    2\. Unpack it

    ```tar -xzf ubuntu-12.04-amd64.img.tar.gz```

    3\. Import it with preferred name, optionally memory, cpu, net and storage pool can be
    specified

    ```
    ./virtup.py add -i ubuntu64.img ubuntu64
    Uploading template into volume ubuntu64
    done 100.00%
    Temporary template written in /tmp/ubuntu64.xml
    ubuntu64 created, you can start it now
    ```

    4\. And start it

    ```
    ./virtup.py up ubuntu64
    ubuntu64 started
    Waiting for ip...
    192.168.122.250
    ```

    5\. Ssh into newly created machine. Template used in example has passswordless ssh root login.

        ssh root@192.168.122.250

## Templates creation
Create virtual machine with ```create``` command or with virsh.  
Install operating system on it, booting it from iso, pxe or your preferred method.  
Start it, install necessary soft. Configure ssh.  
Remove **/etc/udev/rules.d/70-persistent-net.rules** file. This is required for network interface name to be eth0 on first boot of newly created machine.  
Shut it down and use it's disk image as template.
