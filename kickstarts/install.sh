#!/bin/bash -e
RELEASE="${1:-jessie}"

cd "$( dirname "${BASH_SOURCE[0]}" )"
python -m SimpleHTTPServer 8080&

echo "** Create 8GB disk for image"
qemu-img create -f qcow2 /var/lib/libvirt/images/base-${RELEASE}.qcow2 10G

echo "** Start virt-install"
if ( echo $RELEASE | grep -q centos >/dev/null 2>&1 ); then
  CENTOS_VERSION="$(echo $RELEASE | cut -d- -f2)"
  virt-install --name base-${RELEASE} --ram 2048 --vcpus 2 \
    --disk path=/var/lib/libvirt/images/base-${RELEASE}.qcow2,size=8 \
    --vcpus 1 --os-type linux --os-variant generic --network bridge=virbr0 \
    --graphics none  --console pty,target_type=serial \
    --location "http://mirror.centos.org/${RELEASE}/${CENTOS_VERSION}/os/x86_64/" \
    --noreboot \
    --extra-args "install auto=true console=ttyS0,115200n8 serial hostname=$RELEASE domain=virtup.local priority=critical ks=http://192.168.122.1:8080/${RELEASE}.cfg"
else
  virt-install --name base-${RELEASE} --ram 1024 --vcpus 2 \
    --disk path=/var/lib/libvirt/images/base-${RELEASE}.qcow2,size=8 \
    --vcpus 1 --os-type linux --os-variant generic --network bridge=virbr0 \
    --graphics none  --console pty,target_type=serial \
    --location "http://ftp.de.debian.org/debian/dists/${RELEASE}/main/installer-amd64/" \
    --noreboot \
    --extra-args "install auto=true console=ttyS0,115200n8 serial hostname=debian domain=virtup.local priority=critical preseed/url=http://192.168.122.1:8080/debian-preseed.cfg"
fi

pkill -f SimpleHTTPServer
