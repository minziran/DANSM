import subprocess
import sys

iperf_port_dic ={"ueransim-ue-5G-Camera": 1314,"ueransim-ue-5G-Camera2": 1315, "ueransim-ue-5G-Camera3": 1316, "ueransim-ue-5G-Camera4": 1317, "ueransim-ue-5G-Camera5": 1318,
                "ueransim-ue-Robot-Arm": 1319, "ueransim-ue-Robot-Arm2": 1320, "ueransim-ue-Robot-Arm3": 1321, "ueransim-ue-Robot-Arm4": 1322, "ueransim-ue-Robot-Arm5": 1323,
                "ueransim-ue-Edge-Devices": 1324,  "ueransim-ue-Edge-Devices2": 1325,  "ueransim-ue-Edge-Devices3": 1326,  "ueransim-ue-Edge-Devices4": 1327,  "ueransim-ue-Edge-Devices5": 1328}
def ue_task():
    cmd = subprocess.getoutput("ifconfig uesimtun0|grep 'inet ' |cut -d \" \" -f 10")
    device_name = sys.argv[1]
    if "5G-Camera" in device_name:
        pkt_len = "3G"
        port = str(1314)
    elif "Robot-Arm" in device_name:
        pkt_len = "2G"
        port = str(1315)
    elif "Edge-Devices" in device_name:
        pkt_len = "1G"
        port = str(1316)
    #task = "iperf3 -c 192.168.0.24 -B " + cmd +" -b 50K -n "+ pkt_len + " -p "+ str(iperf_port_dic[device_name]) +" --logfile ./iperf3"+sys.argv[2]+".txt --connect-timeout 1200"
    task = "iperf3 -c 192.168.1.164 -B " + cmd + " -n " + pkt_len + " -p " + str(
        iperf_port_dic[device_name]) +" -b "+sys.argv[3] +"M --logfile ./iperf3" + sys.argv[2] + ".txt --connect-timeout 300"
    output =subprocess.getoutput(task)
    print(cmd + "\n" +output)
ue_task()