#!/usr/bin/env bash

# LiveCD Creator 3
# Copyright (C) 2012-2014  Kevin Atwood
# 
# Customizer - Advanced LiveCD Remastering Tool
# Copyright (C) 2010-2013  Ivailo Monev
# 
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

set -e
source /etc/live-cd/common
source /etc/live-cd/default

Root_it() {
  EXTRA_INFO_MESSAGE "Executing $(basename ${1}) at" "$(date +%X/%d-%m-%Y)"
  if [ "${UID}" != "0" ];then 
      WARNING_MESSAGE "You are not root! Prompting for password!"
      su -c "${1}"
  else
      "${1}"
      
	fi
  EXTRA_INFO_MESSAGE "Finished $(basename ${1}) at" "$(date +%X/%d-%m-%Y)"
}

Usage () {
echo "
 Main options:

     -e|--extract   Exctract Image File
     -i|--cdimage   Exctract Image File from Disk
     -c|--chroot    chroot into the FileSystem
     -x|--xnest     Execute Nested X-Session
     -p|--pkgm      Execute Package Manager
     -d|--deb       Install Debian Package
     -k|--hook      Execute Hook
     -g|--gui       Install Desktop Environment
     -r|--rebuild   Rebuild Image File
     -q|--qemu      Test Build Image with Desktop Emulator
     -t|--clean     Clean All Temporary Files and Folders

 Other options:
     
     -h|--help      Display This Message
     -v|--version   Show the Current Version and More
"
}

Version () {
echo "
LiveCD Creator 3

Links:

  Homepage: https://github.com/fluxer/Customizer
  Wiki: https://github.com/fluxer/Customizer/wiki
  Issues: https://github.com/fluxer/Customizer/issues


Credits:

  Main developer:
    Kevin Atwood (a.k.a nooksee)
    <kevin@nooksee.com>

  Main developer:
    Ivailo Monev (a.k.a SmiL3y)
    <xakepa10@gmail.com>

  PPA maintainer:
    Michał Głowienka (a.k.a. eloaders)
    <eloaders@yahoo.com>

  Documentation:
    Mubiin Kimura (a.k.a. clearkimura)
    <clearkimura@gmail.com>
    
  Gambas3 port:
    Thiago Abreu (a.k.a thiagoabreu)
    <thiagoa7@gmail.com>
"
}

if [ "$#" != "0" ];then
	for arg in "$@"; do
		case $arg in
			-e|--extract) Root_it /etc/live-cd/scripts/extract ;;
			-i|--cdimage) Root_it /etc/live-cd/scripts/cdimage ;;
			-c|--chroot) Root_it /etc/live-cd/scripts/chroot ;;
			-x|--xnest) Root_it /etc/live-cd/scripts/xnest ;;
			-p|--pkgm) Root_it /etc/live-cd/scripts/pkgm ;;
			-d|--deb) Root_it /etc/live-cd/scripts/deb ;;
			-k|--hook) Root_it /etc/live-cd/scripts/hook ;;
			-g|--gui) Root_it /etc/live-cd/scripts/gui ;;
			-r|--rebuild) Root_it /etc/live-cd/scripts/rebuild ;;
			-q|--qemu) Root_it /etc/live-cd/scripts/qemu ;;
			-t|--clean) Root_it /etc/live-cd/scripts/clean ;;
			-v|--version) Version ;;
			-h|--help) Usage ;;
			*) EXTRA_ERROR_MESSAGE "unrecognised argument" "$arg" ;;
		esac
	done
else
	Usage
fi