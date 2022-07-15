#!/bin/sh
    sudo docker-compose -f docker-compose-ues.yaml down
    sudo docker-compose -f docker-compose.yaml down --remove-orphans
    sed -i 's/ulcl: true/ulcl: false/' ./config/smfcfg.yaml
    sudo systemctl restart docker
#    sudo ./clear_ovs_flow_table.sh
    sleep 10
    sudo python3 ./clear_ovs_port.py
    sleep 10
    sudo gnome-terminal -t "ryu-monitor" -x bash -c "sudo ryu run /home/minziran/ryu/ryu/app/gui_topology/gui_topology.py /home/minziran/ryu/ryu/app/simple_monitor_13.py --observe-links --ofp-tcp-listen-port 5555|tee log1.txt"
#    sudo gnome-terminal -t "ryu-monitor" -x bash -c "sudo ryu run /home/minziran/ryu/ryu/app/gui_topology/gui_topology.py /home/minziran/ryu/ryu/app/simple_switch_13.py --observe-links --ofp-tcp-listen-port 5555"
    sleep 10
    sudo ./DNSA-iperf-server.sh
#    sudo ./FFD-iperf-server.sh
#    sudo ./BFD-iperf-server.sh
#    sudo ./NFD-iperf-server.sh
#     sudo ./MGA-iperf-server.sh
#    sudo gnome-terminal -t "iperf-server" -x bash -c "iperf3 -s -i 1 -p 1314"
    sudo gnome-terminal -t "docker-compose" -x bash -c "sudo docker-compose -f docker-compose.yaml up" 
    sleep 30
    sudo ovs-vsctl show
#    echo ueransim-ue-5G-Camera
#    sudo docker exec -it ueransim-ue-5G-Camera /bin/bash -c 'ifconfig uesimtun0'
#    sudo docker logs ueransim-ue-5G-Camera
#    echo ueransim-ue-Robot-Arm
#    sudo docker exec -it ueransim-ue-Robot-Arm /bin/bash -c 'ifconfig uesimtun0'
#    sudo docker logs ueransim-ue-Robot-Arm
#    echo ueransim-ue-Edge-Devices
#    sudo docker exec -it ueransim-ue-Edge-Devices /bin/bash -c 'ifconfig uesimtun0'

    sudo docker inspect --format='{{.Name}} - {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' $(sudo docker ps -aq)
    sudo docker-compose -f docker-compose-ues.yaml up -d
    sleep 30
    sudo ./start_ue.sh



