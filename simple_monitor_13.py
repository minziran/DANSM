# Copyright (C) 2016 Nippon Telegraph and Telephone Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import array
import copy

import yaml
import time
from operator import attrgetter
import subprocess

from ryu.app import simple_switch_13
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib import hub
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import ether_types
from ryu.lib.xflow import sflow
from ryu.ofproto import ofproto_v1_3
from PoissonDistribution import PD
from statistics import mean, variance

DEBUG = False


class SimpleMonitor13(simple_switch_13.SimpleSwitch13):
    # Tasks
    # 1. Workpieces Scanning - NS0
    # Traffic from ueransim-ue-5G-Camera -> ueransim-ue-Robot-Arm

    # 2. Defect Detection  - NS1
    # Traffic from ueransim-ue-Edge-Devices -> ueransim-ue-Robot-Arm

    # 3. Robot Milling - NS2
    # Traffic from ueransim-ue-Robot-Arm -> ueransim-ue-Edge-Devices

    # 4. Milling Monitoring - NS3
    #  Traffic from ueransim-ue-5G-Camera -> ueransim-ue-Edge-Devices

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    ue_SIM_dic = {"imsi-208930000000003": "20.100.200.104", "imsi-208930000000004": "20.100.200.107",
                  "imsi-208930000000005": "20.100.200.110",
                  "imsi-208930000000006": "20.100.200.113", "imsi-208930000000007": "20.100.200.116",
                  "imsi-208930000000008": "20.100.200.105",
                  "imsi-208930000000009": "20.100.200.108", "imsi-208930000000010": "20.100.200.111",
                  "imsi-208930000000011": "20.100.200.114",
                  "imsi-208930000000012": "20.100.200.117", "imsi-208930000000013": "20.100.200.106",
                  "imsi-208930000000014": "20.100.200.109",
                  "imsi-208930000000015": "20.100.200.112", "imsi-208930000000016": "20.100.200.115",
                  "imsi-208930000000017": "20.100.200.118"}

    #remove upf6
    NS_to_UPF_assignment = {"NS0": ["upf0", "upf4", "upf5"], "NS1": ["upf1", "upf7", "upf8"],
                            "NS2": ["upf2", "upf9"], "NS3": ["upf3"]}  # NS0 is for 5G camera scanning; NS2 is for Robot Arm; NS1 is for Edge Devices; NS3 is for 5G camera monitoring.

    upf_dic = {'10.100.200.200': 'upf0', '10.100.200.201': 'upf1', '10.100.200.202': 'upf2',
               '10.100.200.203': 'upf3', '10.100.200.204': 'upf4', '10.100.200.205': 'upf5',
               '10.100.200.206': 'upf6', '10.100.200.207': 'upf7', '10.100.200.208': 'upf8',
               '10.100.200.209': 'upf9'}

    ue_dic = {'20.100.200.103': 'gNB1', '20.100.200.104': 'ueransim-ue-5G-Camera',
              '20.100.200.105': 'ueransim-ue-Robot-Arm',
              '20.100.200.106': 'ueransim-ue-Edge-Devices', '20.100.200.107': 'ueransim-ue-5G-Camera2',
              '20.100.200.108': 'ueransim-ue-Robot-Arm2',
              '20.100.200.109': 'ueransim-ue-Edge-Devices2', '20.100.200.110': 'ueransim-ue-5G-Camera3',
              '20.100.200.111': 'ueransim-ue-Robot-Arm3',
              '20.100.200.112': 'ueransim-ue-Edge-Devices3', '20.100.200.113': 'ueransim-ue-5G-Camera4',
              '20.100.200.114': 'ueransim-ue-Robot-Arm4',
              '20.100.200.115': 'ueransim-ue-Edge-Devices4', '20.100.200.116': 'ueransim-ue-5G-Camera5',
              '20.100.200.117': 'ueransim-ue-Robot-Arm5',
              '20.100.200.118': 'ueransim-ue-Edge-Devices5'}

    ues_avg_request_rate = {"5G-Camera1": [100], "Edge-Devices": [100], "Robot-Arm": [100], "5G-Camera4": [100]}
    task_length = {"5G-Camera": 24000, "Edge-Devices": 8000, "Robot-Arm": 16000}


    ue_last_task = {}
    available_ue = {}
    task_priority = {1: 4, 2: 3, 3: 2, 4: 1}

    datapath_in_ues = {3, 4}
    ue_start_time = {}
    ue_sending_rate = {}

    UPF_load_threshold = 900


    def __init__(self, *args, **kwargs):
        if DEBUG:
            print("__init__")
        super(SimpleMonitor13, self).__init__(*args, **kwargs)
        self.start_time = time.time()
        self.datapaths = {}
        self.mac_to_ip = {}
        self.port_to_ip = {}
        self.upf_status = {}
        self.monitor_thread = hub.spawn(self._monitor)
        self.upf_service_rate = 900
        self.UE_to_UPF_assignment = {}
        self.MF_Flag = True
        self.SMF_Flag = False
        self.ue_tasks = {}
        self.ue_current_rate = {}
        for key in self.ue_dic:
            self.ue_tasks[key] = 5
            self.ue_current_rate[key] = PD.pop(0)
        self.total_migration_times = 0

    def text_create(self, name, msg):
        pwd = "/home/minziran/Free5GC-Compose/free5gc-compose/"
        full_path = pwd + name + '.txt'
        file = open(full_path, 'w')
        file.write(msg)
        file.close()

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        # install table-miss flow entry
        # We specify NO BUFFER to max_len of the output action due to
        # OVS bug. At this moment, if we specify a lesser number, e.g.,
        # 128, OVS will send Packet-In with invalid buffer_id and
        # truncated packet data. In that case, we cannot output packets
        # correctly.  The bug has been fixed in OVS v2.1.0.
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    @set_ev_cls(ofp_event.EventOFPStateChange,
                [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        if DEBUG:
            print("_state_change_handler")
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            if datapath.id not in self.datapaths:
                self.logger.debug('register datapath: %016x', datapath.id)
                self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            if datapath.id in self.datapaths:
                self.logger.debug('unregister datapath: %016x', datapath.id)
                del self.datapaths[datapath.id]

    def yaml_to_json(self):
        data = ""
        UE_to_UPF_assignment = {}
        with open('./config/uerouting.yaml', encoding='utf-8') as file:
            data = yaml.safe_load(file)

        for info in data:
            if info == "ueRoutingInfo":
                for UEgroups in data[info]:
                    UPF_temp = []
                    target_upf = ""
                    for ueinfo in data[info][UEgroups]:
                        if ueinfo == "members":
                            for ue in data[info][UEgroups][ueinfo]:
                                if ue in self.ue_SIM_dic:
                                    UPF_temp.append(self.ue_SIM_dic[ue])
                        if ueinfo == "topology":
                            target_upf = data[info][UEgroups][ueinfo][0]["B"].replace("AnchorUPF", "upf")
                            if target_upf not in UE_to_UPF_assignment:
                                UE_to_UPF_assignment[target_upf] = UPF_temp
                            else:
                                for key in UPF_temp:
                                    if key not in UE_to_UPF_assignment[target_upf]:
                                        UE_to_UPF_assignment[target_upf].append(key)
            else:
                continue
        return UE_to_UPF_assignment

    def check_ue_status(self):
        for ue in self.ue_sending_rate:
            last_item = 0
            count = 0
            for item in self.ue_sending_rate[ue]:
                if item < last_item:
                    count += 1
                last_item = item
            if count == 4:
                ue_priorty = 0
                if "5G-Camera" in self.ue_dic[ue]:
                    if self.ue_last_task[ue] == 4:
                        ue_priorty = self.task_priority[1] + 1 / (self.ue_start_time[ue])
                        self.ue_last_task[ue] = 1
                        self.ues_avg_request_rate["5G-Camera1"].append(mean(self.ue_sending_rate[ue]))
                    else:
                        ue_priorty = self.task_priority[4] + 1 / (self.ue_start_time[ue])
                        self.ue_last_task[ue] = 4
                        self.ues_avg_request_rate["5G-Camera4"].append(mean(self.ue_sending_rate[ue]))
                if "Edge-Devices" in self.ue_dic[ue]:
                    ue_priorty = self.task_priority[2] + 1 / (self.ue_start_time[ue])
                    self.ues_avg_request_rate["Edge-Devices"].append(mean(self.ue_sending_rate[ue]))
                if "Robot-Arm" in self.ue_dic[ue]:
                    ue_priorty = self.task_priority[3] + 1 / (self.ue_start_time[ue])
                    self.ues_avg_request_rate["Robot-Arm"].append(mean(self.ue_sending_rate[ue]))
                self.available_ue[ue] = ue_priorty


        print("Available UE", self.available_ue)

    def get_ue_avg_request_rate(self, ue):
        #ue_avg = PD.pop(0)
        if "5G-Camera" in self.ue_dic[ue]:
            if self.ue_last_task[ue] == 1:
                ue_avg = mean(self.ues_avg_request_rate["5G-Camera4"])
            elif self.ue_last_task[ue] == 4:
                ue_avg = mean(self.ues_avg_request_rate["5G-Camera1"])
        elif "Edge-Devices" in self.ue_dic[ue]:
            ue_avg = mean(self.ues_avg_request_rate["Edge-Devices"])
        elif "Robot-Arm" in self.ue_dic[ue]:
            ue_avg = mean(self.ues_avg_request_rate["Robot-Arm"])
        #print("ue_avg ", ue_avg)
        return ue_avg

    def current_UPFs_load_and_assignment(self):  # without available ue
        UPF_loads = {}
        UE_to_UPF_assignment = self.yaml_to_json()
        remove_list = {}
        print("UE_to_UPF_assignment in current_UPFs ", UE_to_UPF_assignment)
        for UPF in UE_to_UPF_assignment:
            UPF_loads[UPF] = 0
            for ue in UE_to_UPF_assignment[UPF]:
                #print(self.ue_sending_rate)
                if ue in self.available_ue:
                    if UPF not in remove_list:
                        remove_list[UPF] = [ue]
                    else:
                        remove_list[UPF].append(ue)
                    #UE_to_UPF_assignment[UPF].remove(ue)
                elif ue in self.ue_sending_rate:
                    print("In upf loads")
                    UPF_loads[UPF] += self.ue_current_rate[ue]
        for key in remove_list:
            for value in remove_list[key]:
                UE_to_UPF_assignment[key].remove(value)
        #print("UE sending rate", self.ue_sending_rate)
        print("UPF Loads", UPF_loads)
        print("UPF Assignment ", UE_to_UPF_assignment)
        return UPF_loads, UE_to_UPF_assignment

    def F_X(self, UPF_loads_before, UE_to_UPF_assignment_before, upf, ue, ue_load):
        # ue in self.available_ue

        ue_latency = []
        ue_length = 0
        for fx_upf in UE_to_UPF_assignment_before:
            #print("a_lala",ue, fx_upf,upf)
            if fx_upf == upf:
                fx_upf_load = UPF_loads_before[fx_upf] + ue_load
                # key = fx_ue + " " + fx_upf
                # tx_delaty = (self.len_of_pkt_UE_to_UPF[key] / self.tx_rate_UE_to_UPF[key])
                if "5G-Camera" in ue:
                    ue_length = self.task_length["5G-Camera"]
                elif "Edge-Devices" in ue:
                    ue_length = self.task_length["Edge-Devices"]
                elif "Robot-Arm" in ue:
                    ue_length = self.task_length["Robot-Arm"]
                latency = ((1/(self.upf_service_rate - fx_upf_load)) + (ue_length/self.ue_current_rate[ue]))
                ue_latency.append(latency)

            else:
                fx_upf_load = UPF_loads_before[fx_upf]
            for fx_ue in UE_to_UPF_assignment_before[fx_upf]:
                print("fx_ue", fx_ue, fx_upf)
                if "5G-Camera" in self.ue_dic[fx_ue]:
                    ue_length = self.task_length["5G-Camera"]
                elif "Edge-Devices" in self.ue_dic[fx_ue]:
                    ue_length = self.task_length["Edge-Devices"]
                elif "Robot-Arm" in self.ue_dic[fx_ue]:
                    ue_length = self.task_length["Robot-Arm"]
                # key = fx_ue + " " + fx_upf
                # tx_delaty = (self.len_of_pkt_UE_to_UPF[key] / self.tx_rate_UE_to_UPF[key])
                latency = ((1 / (self.upf_service_rate - fx_upf_load)) + (ue_length/self.ue_current_rate[fx_ue]))
                ue_latency.append(latency)

        avg = mean(ue_latency)
        if len(ue_latency) == 1:
            var = 0
        else:
            var = variance(ue_latency)
        print("avg ", avg)
        print("var ", var)
        print("FX ", avg + var*0.01)
        return (avg + var*0.01)

    def assignment_safe_check(self, new_assignment):
        temp_ue_list = []
        for key in self.ue_SIM_dic:
            temp_ue_list.append(self.ue_SIM_dic[key])
        #remove_list=[]
        for upf in new_assignment:
            for ue in new_assignment[upf]:
                if ue in temp_ue_list:
                    #remove_list.append(ue)
                    temp_ue_list.remove(ue)
                else:
                    new_assignment[upf].remove(ue)
        print("After Safe Check ", new_assignment)
        return new_assignment
    def dict_to_yaml(self, new_assignment):

        data = {'info': {
            'version': '1.0.1',
            'description': 'Routing information for UE'},
            'ueRoutingInfo': {}}

        ueRoutiung = {}

        new_assignment = self.assignment_safe_check(new_assignment)
        print("New_assignment After Safe check ", new_assignment)
        for i, value in enumerate(new_assignment):
            if len(new_assignment[value]) == 0:
                continue
            name = 'UE' + str(i + 1)
            ueRoutiung[name] = {'members': [], 'topology': []}
            sim_list = []
            for ip in new_assignment[value]:
                for sim in self.ue_SIM_dic:
                    if self.ue_SIM_dic[sim] == ip:
                        sim_list.append(sim)
                        break
            print(list(set(sim_list)))

            ueRoutiung[name]['members'] = list(set(sim_list))
            uplink = {'A': 'gNB1', 'B': value.replace('upf', 'AnchorUPF')}

            ueRoutiung[name]['topology'].append(uplink)

        data['ueRoutingInfo'] = ueRoutiung
        with open('/home/minziran/Free5GC-Compose/free5gc-compose/config/uerouting.yaml', 'w') as yaml_file:
            yaml.dump(data, yaml_file, default_flow_style=False)

    def reconfigure_ue_connection(self, UE_to_UPF_assignment_before):
        UE_to_UPF_assignment = self.yaml_to_json()
        print("compairing")
        print(UE_to_UPF_assignment_before)
        print(UE_to_UPF_assignment)

        changed_ue = {}
        if UE_to_UPF_assignment_before == UE_to_UPF_assignment:
            print("No change")
        else:
            for ue in self.ue_sending_rate:

                before_upf = ""
                after_upf = ""

                for upf_1 in UE_to_UPF_assignment:
                    if ue in UE_to_UPF_assignment[upf_1]:
                        before_upf = upf_1
                        break
                    else:
                        continue

                for upf_2 in UE_to_UPF_assignment_before:
                    if ue in UE_to_UPF_assignment_before[upf_2]:
                        after_upf = upf_2
                        break
                    else:
                        continue
                print("Before_UPF and After_UPF ", before_upf, after_upf)
                if before_upf != after_upf:
                    changed_ue[ue] = after_upf

        return changed_ue

    def update_ue_connection(self, changed_ue):

        task = "sudo docker restart smf"
        output = subprocess.getoutput(task)
        print(output)

        # task = "sudo docker restart ueransim-gnb"
        # output = subprocess.getoutput(task)
        # print(output)

        # for key in changed_ue:
        #     name = self.ue_dic[key]
        #     task = "sudo docker exec -itd " + name +"  /bin/bash -c 'pkill nr-ue'"
        #     output = subprocess.getoutput(task)
        #     print(output)
        #     task = "sudo docker exec -itd " + name +"  /bin/bash -c 'pkill python3'"
        #     output = subprocess.getoutput(task)
        #     print(output)
        #     task = "sudo docker exec -itd " + name + "  /bin/bash -c 'pkill iperf3'"
        #     output = subprocess.getoutput(task)
        #     print(output)
        #     task = "sudo docker exec -itd " + name + " /bin/bash -c '/root/UERANSIM/build/nr-ue -c /root/UERANSIM/config/free5gc-ue.yaml'"
        #     output = subprocess.getoutput(task)
        #     print(output)

    def assign_UE_to_UPF(self):

        sorted_list = copy.deepcopy(sorted(self.available_ue, key=lambda key: self.available_ue[key], reverse=True))
        # print("Sorted_List ", sorted_list)
        # UPF loads before assign available UEs
        print("current UE rate ", self.ue_current_rate)
        UPF_loads_before, UE_to_UPF_assignment_before = self.current_UPFs_load_and_assignment()

        for ue in sorted_list:

            print("UE ", ue)
            ns_tag = ""
            if self.available_ue[ue] >= self.task_priority[1]:
                ns_tag = "NS0"
            elif (self.available_ue[ue] <= self.task_priority[1]) and (self.available_ue[ue] >= self.task_priority[2]):
                ns_tag = "NS1"
            elif (self.available_ue[ue] <= self.task_priority[2]) and (self.available_ue[ue] >= self.task_priority[3]):
                ns_tag = "NS2"
            elif (self.available_ue[ue] <= self.task_priority[3]) and (self.available_ue[ue] >= self.task_priority[4]):
                ns_tag = "NS3"

            UPF_avg_load = sum(UPF_loads_before.values()) / len(UPF_loads_before)
            ue_load = self.ue_current_rate[ue]

            if UPF_avg_load < self.UPF_load_threshold:
            # check the average UPF loads and less than threshold

                # Calculate the F(x) value for every UPF
                min_upf_name = ""
                min_fx = float('inf')
                print("UPF Load ", UPF_loads_before)
                print("UE to UPF Assignment ", UE_to_UPF_assignment_before)
                for upf in self.NS_to_UPF_assignment[ns_tag]:
                    print("UUpf ", upf)
                    print("NS tag", ns_tag)
                    if upf in UPF_loads_before:
                        print("in it")
                        fx_value = self.F_X(UPF_loads_before, UE_to_UPF_assignment_before, upf, ue, ue_load)
                        if fx_value < min_fx:
                            min_fx = fx_value
                            min_upf_name = upf
                    else:
                        continue
                print(UPF_loads_before)
                print("ue and min upf ", ue, " ", min_upf_name)
                if min_upf_name == "":
                    for upf in self.NS_to_UPF_assignment[ns_tag]:
                        if upf not in UPF_loads_before:
                            UPF_loads_before[upf] = ue_load
                            if upf in UE_to_UPF_assignment_before:
                                if ue not in UE_to_UPF_assignment_before[upf]:
                                    UE_to_UPF_assignment_before[upf].append(ue)
                                break
                            else:
                                UE_to_UPF_assignment_before[upf] = [ue]
                                break

                else:
                    # pre-assign
                    if (UPF_loads_before[min_upf_name] + ue_load) < self.UPF_load_threshold:
                        # change the state of UPF_loads_before and UE_to_UPF_assignment_before
                        UPF_loads_before[min_upf_name] += ue_load
                        if ue not in UE_to_UPF_assignment_before[min_upf_name]:
                            UE_to_UPF_assignment_before[min_upf_name].append(ue)
                        continue
                    else:
                        for upf in self.NS_to_UPF_assignment[ns_tag]:
                            if upf not in UPF_loads_before:
                                    UPF_loads_before[upf] = ue_load
                                    if upf in UE_to_UPF_assignment_before:
                                        if ue not in UE_to_UPF_assignment_before[upf]:
                                            UE_to_UPF_assignment_before[upf].append(ue)
                                        break
                                    else:
                                        UE_to_UPF_assignment_before[upf] = [ue]
                                        break
            else:
            # check the average UPF loads and greater than threshold

                for upf in self.NS_to_UPF_assignment[ns_tag]:
                    if upf not in UPF_loads_before:
                        UPF_loads_before[upf] = ue_load
                        if upf in UE_to_UPF_assignment_before:
                            if ue not in UE_to_UPF_assignment_before[upf]:
                                UE_to_UPF_assignment_before[upf].append(ue)
                            break
                        else:
                            UE_to_UPF_assignment_before[upf] = [ue]
                            break
                # change the state of UPF_loads_before and UE_to_UPF_assignment_before
        print("=================="+str(time.time())+"===================")
        print("===========UPF loads===========")
        print(UPF_loads_before)
        print("===========UE_to_UPF assignment===========")
        print(UE_to_UPF_assignment_before)

        changed_ue = self.reconfigure_ue_connection(UE_to_UPF_assignment_before)
        self.dict_to_yaml(UE_to_UPF_assignment_before)
        if changed_ue:
            self.update_ue_connection(changed_ue)
        print("===========Migration Times===========")
        self.total_migration_times += len(changed_ue)
        print(self.total_migration_times)
        self.assign_task(changed_ue)

    def assign_task(self, changed_ue):
        sorted_list = sorted(self.available_ue, key=lambda key: self.available_ue[key], reverse=True)

        for ue in sorted_list:
            #print("in loop ", ue)
            name = self.ue_dic[ue]
            print("Changed UE", ue, changed_ue)
            # if ue in changed_ue:
            #     cmd = "sudo docker exec -itd " + name + " /bin/bash -c 'echo $(ifconfig uesimtun0)'"
            #     print(cmd)
            #     temp = subprocess.getoutput(cmd)
            #     print(temp)
            #     while ("error fetching") in temp or (temp==""):
            #         print("in sleep")
            #         time.sleep(15)
            #         # name = self.ue_dic[ue]
            #         # task = "sudo docker exec -itd " + name + "  /bin/bash -c 'pkill nr-ue'"
            #         # output = subprocess.getoutput(task)
            #         # print(output)
            #         # task = "sudo docker exec -itd " + name + " /bin/bash -c '/root/UERANSIM/build/nr-ue -c /root/UERANSIM/config/free5gc-ue.yaml'"
            #         # output = subprocess.getoutput(task)
            #         # print(output)
            #         cmd = "sudo docker exec -itd " + name + " /bin/bash -c 'echo $(ifconfig uesimtun0)'"
            #         print(cmd)
            #         temp = subprocess.getoutput(cmd)
            #         print(temp)

            print(name)
            self.ue_tasks[ue] -= 1
            cmd = "sudo docker exec -itd " + name + " /bin/bash -c 'python3 /root/ue_task.py " + name + " " + str(self.ue_tasks[ue]) + " " + str(self.ue_current_rate[ue]) +"'"
            self.ue_start_time[ue] = time.time()
            self.ue_current_rate[ue] = PD.pop(0)
            print(cmd)
            temp = subprocess.getoutput(cmd)
            print(temp)
        print("UE task ", self.ue_tasks)
        self.available_ue = {}

    def FFD_assign_UE_to_UPF(self):

        sorted_list = sorted(self.available_ue, key=lambda key: self.available_ue[key], reverse=True)

        # UPF loads before assign available UEs
        UPF_loads_before, UE_to_UPF_assignment_before = self.current_UPFs_load_and_assignment()

        for ue in sorted_list:
            ns_tag = ""
            if self.available_ue[ue] >= self.task_priority[1]:
                ns_tag = "NS0"
            elif (self.available_ue[ue] <= self.task_priority[1]) and (self.available_ue[ue] >= self.task_priority[2]):
                ns_tag = "NS1"
            elif (self.available_ue[ue] <= self.task_priority[2]) and (self.available_ue[ue] >= self.task_priority[3]):
                ns_tag = "NS2"
            elif (self.available_ue[ue] <= self.task_priority[3]) and (self.available_ue[ue] >= self.task_priority[4]):
                ns_tag = "NS3"

            UPF_avg_load = sum(UPF_loads_before.values()) / len(UPF_loads_before)
            ue_load = self.ue_current_rate[ue]

            for upf in self.NS_to_UPF_assignment[ns_tag]:
                if upf in UPF_loads_before:
                    if (UPF_loads_before[upf] + ue_load) < self.UPF_load_threshold:
                        UPF_loads_before[upf] += ue_load
                        if upf in UE_to_UPF_assignment_before:
                            if ue not in UE_to_UPF_assignment_before[upf]:
                                UE_to_UPF_assignment_before[upf].append(ue)
                            break
                        else:
                            UE_to_UPF_assignment_before[upf] = [ue]
                            break
                else:
                    UPF_loads_before[upf] = ue_load
                    if upf in UE_to_UPF_assignment_before:
                        if ue not in UE_to_UPF_assignment_before[upf]:
                            UE_to_UPF_assignment_before[upf].append(ue)
                        break
                    else:
                        UE_to_UPF_assignment_before[upf] = [ue]
                        break

        print("==================" + str(time.time()) + "===================")
        print("===========UPF loads===========")
        print(UPF_loads_before)
        print("===========UE_to_UPF assignment===========")
        print(UE_to_UPF_assignment_before)

        # print("UPF loads")
        # print(UPF_loads_before)
        # print("UE_to_UPF assignment")
        # print(UE_to_UPF_assignment_before)
        changed_ue = self.reconfigure_ue_connection(UE_to_UPF_assignment_before)
        self.dict_to_yaml(UE_to_UPF_assignment_before)
        if changed_ue:
            self.update_ue_connection(changed_ue)
        print("===========Migration Times===========")
        self.total_migration_times += len(changed_ue)
        print(self.total_migration_times)
        self.assign_task(changed_ue)

    def BFD_assign_UE_to_UPF(self):

        sorted_list = sorted(self.available_ue, key=lambda key: self.available_ue[key], reverse=True)

        # UPF loads before assign available UEs
        UPF_loads_before, UE_to_UPF_assignment_before = self.current_UPFs_load_and_assignment()

        for ue in sorted_list:
            ns_tag = ""
            if self.available_ue[ue] >= self.task_priority[1]:
                ns_tag = "NS0"
            elif (self.available_ue[ue] <= self.task_priority[1]) and (self.available_ue[ue] >= self.task_priority[2]):
                ns_tag = "NS1"
            elif (self.available_ue[ue] <= self.task_priority[2]) and (self.available_ue[ue] >= self.task_priority[3]):
                ns_tag = "NS2"
            elif (self.available_ue[ue] <= self.task_priority[3]) and (self.available_ue[ue] >= self.task_priority[4]):
                ns_tag = "NS3"

            UPF_avg_load = sum(UPF_loads_before.values()) / len(UPF_loads_before)
            ue_load = self.ue_current_rate[ue]

            for upf in self.NS_to_UPF_assignment[ns_tag]:
                min_upf_value = 99999
                min_upf_name = ""

                if upf in UPF_loads_before:
                    remain_load = self.UPF_load_threshold - UPF_loads_before[upf] - ue_load
                    if remain_load > 0:
                            if remain_load < min_upf_value:
                                min_upf_value = remain_load
                                min_upf_name = upf
                    else:
                        continue

                else:
                    remain_load =  self.UPF_load_threshold
                    if remain_load < min_upf_value:
                        min_upf_value = remain_load
                        min_upf_name = upf

            if min_upf_name in UPF_loads_before:
                UPF_loads_before[min_upf_name] += ue_load
            else:
                UPF_loads_before[min_upf_name] = ue_load

            if min_upf_name in UE_to_UPF_assignment_before:
                if ue not in UE_to_UPF_assignment_before[min_upf_name]:
                    UE_to_UPF_assignment_before[min_upf_name].append(ue)

            else:
                UE_to_UPF_assignment_before[min_upf_name] = [ue]

        print("==================" + str(time.time()) + "===================")
        print("===========UPF loads===========")
        print(UPF_loads_before)
        print("===========UE_to_UPF assignment===========")
        print(UE_to_UPF_assignment_before)
        # print("UPF loads")
        # print(UPF_loads_before)
        # print("UE_to_UPF assignment")
        # print(UE_to_UPF_assignment_before)
        changed_ue = self.reconfigure_ue_connection(UE_to_UPF_assignment_before)
        self.dict_to_yaml(UE_to_UPF_assignment_before)
        if changed_ue:
            self.update_ue_connection(changed_ue)
        print("===========Migration Times===========")
        self.total_migration_times += len(changed_ue)
        print(self.total_migration_times)
        self.assign_task(changed_ue)

    def NFD_assign_UE_to_UPF(self):

        sorted_list = sorted(self.available_ue, key=lambda key: self.available_ue[key], reverse=True)

        # UPF loads before assign available UEs
        UPF_loads_before, UE_to_UPF_assignment_before = self.current_UPFs_load_and_assignment()
        ns_index = {"NS0": 0, "NS1": 0, "NS2": 0, "NS3": 0}
        for ue in sorted_list:
            ns_tag = ""
            if self.available_ue[ue] >= self.task_priority[1]:
                ns_tag = "NS0"
            elif (self.available_ue[ue] <= self.task_priority[1]) and (self.available_ue[ue] >= self.task_priority[2]):
                ns_tag = "NS1"
            elif (self.available_ue[ue] <= self.task_priority[2]) and (self.available_ue[ue] >= self.task_priority[3]):
                ns_tag = "NS2"
            elif (self.available_ue[ue] <= self.task_priority[3]) and (self.available_ue[ue] >= self.task_priority[4]):
                ns_tag = "NS3"

            UPF_avg_load = sum(UPF_loads_before.values()) / len(UPF_loads_before)
            ue_load = self.get_ue_avg_request_rate(ue)

            ns_id = ns_index[ns_tag]

            for i, upf in enumerate(self.NS_to_UPF_assignment[ns_tag]):

                if ns_id < len(self.NS_to_UPF_assignment[ns_tag]):
                    pass
                else:
                    ns_id = ns_id % len(self.NS_to_UPF_assignment)

                if i == ns_id:
                    if upf in UPF_loads_before:
                        if UPF_loads_before[upf] + ue_load < self.UPF_load_threshold:
                            UPF_loads_before[upf] += ue_load
                            if upf in UE_to_UPF_assignment_before:
                                if ue not in UE_to_UPF_assignment_before[upf]:
                                    UE_to_UPF_assignment_before[upf].append(ue)

                            else:
                                UE_to_UPF_assignment_before[upf] = [ue]
                            ns_index[ns_tag] += 1
                            break
                        ns_id += 1
                    else:
                        UPF_loads_before[upf] = ue_load
                        if upf in UE_to_UPF_assignment_before:
                            if ue not in UE_to_UPF_assignment_before[upf]:
                                UE_to_UPF_assignment_before[upf].append(ue)
                        else:
                            UE_to_UPF_assignment_before[upf] = [ue]
                        ns_index[ns_tag] += 1
                        break
        print("UPF loads")
        print(UPF_loads_before)
        print("UE_to_UPF assignment")
        print(UE_to_UPF_assignment_before)
        changed_ue = self.reconfigure_ue_connection(UE_to_UPF_assignment_before)
        self.dict_to_yaml(UE_to_UPF_assignment_before)
        if changed_ue:
            self.update_ue_connection(changed_ue)
        self.assign_task(changed_ue)

    def F_X_before(self, UPF_loads_before, UPF_avg_load, UE_to_UPF_assignment_before, upf, ue, ue_load):
        # ue in self.available_ue

        ue_latency = []
        ue_length = 0
        for fx_upf in UE_to_UPF_assignment_before:
            if fx_upf == upf:
                fx_upf_load = UPF_loads_before[fx_upf] + ue_load
                # key = fx_ue + " " + fx_upf
                # tx_delaty = (self.len_of_pkt_UE_to_UPF[key] / self.tx_rate_UE_to_UPF[key])
                if "5G-Camera" in ue:
                    ue_length = self.task_length["5G-Camera"]
                elif "Edge-Devices" in ue:
                    ue_length = self.task_length["Edge-Devices"]
                elif "Robot-Arm" in ue:
                    ue_length = self.task_length["Robot-Arm"]
                latency = ((1 / (self.upf_service_rate - fx_upf_load)))
                ue_latency.append(latency)

            else:
                fx_upf_load = UPF_loads_before[fx_upf]
            for fx_ue in UE_to_UPF_assignment_before[fx_upf]:
                print("fx_ue", fx_ue, fx_upf)
                if "5G-Camera" in self.ue_dic[fx_ue]:
                    ue_length = self.task_length["5G-Camera"]
                elif "Edge-Devices" in self.ue_dic[fx_ue]:
                    ue_length = self.task_length["Edge-Devices"]
                elif "Robot-Arm" in self.ue_dic[fx_ue]:
                    ue_length = self.task_length["Robot-Arm"]
                # key = fx_ue + " " + fx_upf
                # tx_delaty = (self.len_of_pkt_UE_to_UPF[key] / self.tx_rate_UE_to_UPF[key])
                latency = ((1 / (self.upf_service_rate - fx_upf_load)))
                ue_latency.append(latency)

        avg = mean(ue_latency)
        avg_upf = 0
        for key in UPF_loads_before:
            avg_upf +=pow((UPF_loads_before[key] - UPF_avg_load),2)
        avg_upf = avg_upf/len(UPF_loads_before)
        print("avg ", avg, avg_upf)

        return (avg + avg_upf)


    def Modiefed_Greedy_Algorithm(self):

        sorted_list = copy.deepcopy(sorted(self.available_ue, key=lambda key: self.available_ue[key], reverse=True))
        # print("Sorted_List ", sorted_list)
        # UPF loads before assign available UEs
        print("current UE rate ", self.ue_current_rate)
        UPF_loads_before, UE_to_UPF_assignment_before = self.current_UPFs_load_and_assignment()

        for ue in sorted_list:

            print("UE ", ue)
            ns_tag = ""
            if self.available_ue[ue] >= self.task_priority[1]:
                ns_tag = "NS0"
            elif (self.available_ue[ue] <= self.task_priority[1]) and (self.available_ue[ue] >= self.task_priority[2]):
                ns_tag = "NS1"
            elif (self.available_ue[ue] <= self.task_priority[2]) and (self.available_ue[ue] >= self.task_priority[3]):
                ns_tag = "NS2"
            elif (self.available_ue[ue] <= self.task_priority[3]) and (self.available_ue[ue] >= self.task_priority[4]):
                ns_tag = "NS3"

            UPF_avg_load = sum(UPF_loads_before.values()) / len(UPF_loads_before)
            ue_load = self.ue_current_rate[ue]

            if UPF_avg_load < self.UPF_load_threshold:
            # check the average UPF loads and less than threshold

                # Calculate the F(x) value for every UPF
                min_upf_name = ""
                min_fx = float('inf')
                print("UPF Load ", UPF_loads_before)
                print("UE to UPF Assignment ", UE_to_UPF_assignment_before)
                for upf in self.NS_to_UPF_assignment[ns_tag]:
                    print("UUpf ", upf)
                    print("NS tag", ns_tag)
                    if upf in UPF_loads_before:
                        print("in it")
                        fx_value = self.F_X_before(UPF_loads_before, UPF_avg_load,UE_to_UPF_assignment_before, upf, ue, ue_load)
                        if fx_value < min_fx:
                            min_fx = fx_value
                            min_upf_name = upf
                    else:
                        continue
                print(UPF_loads_before)
                print("ue and min upf ", ue, " ", min_upf_name)
                if min_upf_name == "":
                    for upf in self.NS_to_UPF_assignment[ns_tag]:
                        if upf not in UPF_loads_before:
                            UPF_loads_before[upf] = ue_load
                            if upf in UE_to_UPF_assignment_before:
                                if ue not in UE_to_UPF_assignment_before[upf]:
                                    UE_to_UPF_assignment_before[upf].append(ue)
                                break
                            else:
                                UE_to_UPF_assignment_before[upf] = [ue]
                                break

                else:
                    # pre-assign
                    if (UPF_loads_before[min_upf_name] + ue_load) < self.UPF_load_threshold:
                        # change the state of UPF_loads_before and UE_to_UPF_assignment_before
                        UPF_loads_before[min_upf_name] += ue_load
                        if ue not in UE_to_UPF_assignment_before[min_upf_name]:
                            UE_to_UPF_assignment_before[min_upf_name].append(ue)
                        continue
                    else:
                        for upf in self.NS_to_UPF_assignment[ns_tag]:
                            if upf not in UPF_loads_before:
                                    UPF_loads_before[upf] = ue_load
                                    if upf in UE_to_UPF_assignment_before:
                                        if ue not in UE_to_UPF_assignment_before[upf]:
                                            UE_to_UPF_assignment_before[upf].append(ue)
                                        break
                                    else:
                                        UE_to_UPF_assignment_before[upf] = [ue]
                                        break
            else:
            # check the average UPF loads and greater than threshold

                for upf in self.NS_to_UPF_assignment[ns_tag]:
                    if upf not in UPF_loads_before:
                        UPF_loads_before[upf] = ue_load
                        if upf in UE_to_UPF_assignment_before:
                            if ue not in UE_to_UPF_assignment_before[upf]:
                                UE_to_UPF_assignment_before[upf].append(ue)
                            break
                        else:
                            UE_to_UPF_assignment_before[upf] = [ue]
                            break
                # change the state of UPF_loads_before and UE_to_UPF_assignment_before
        print("=================="+str(time.time())+"===================")
        print("===========UPF loads===========")
        print(UPF_loads_before)
        print("===========UE_to_UPF assignment===========")
        print(UE_to_UPF_assignment_before)

        changed_ue = self.reconfigure_ue_connection(UE_to_UPF_assignment_before)
        self.dict_to_yaml(UE_to_UPF_assignment_before)
        if changed_ue:
            self.update_ue_connection(changed_ue)
        print("===========Migration Times===========")
        self.total_migration_times += len(changed_ue)
        print(self.total_migration_times)
        self.assign_task(changed_ue)

    def DNSA(self):
        if DEBUG:
            print("DNSA")
            print(self.ue_start_time)
        self.UE_to_UPF_assignment = self.yaml_to_json()
        self.check_ue_status()
        if self.available_ue and ((time.time() -self.start_time) <(25*60)):
            print("In assignment")
            if (len(self.available_ue) == 12) and (self.SMF_Flag == False):
                print("++++++++++++++++++++++++++++++++++++++++++")
                print("++++++++++++++++++++++++++++++++++++++++++")
                print("START!!!")
                print("++++++++++++++++++++++++++++++++++++++++++")
                cmd = "sed -i 's/ulcl: false/ulcl: true/' /home/minziran/Free5GC-Compose/free5gc-compose/config/smfcfg.yaml"
                output = subprocess.getoutput(cmd)
                print(cmd)
                cmd = "sudo docker restart smf"
                output = subprocess.getoutput(cmd)
                print(cmd)
                self.SMF_Flag = True
            if self.SMF_Flag == True:
                print("Progress")
                print((time.time() -self.start_time)/(25*60))
                #self.assign_UE_to_UPF()
                #self.FFD_assign_UE_to_UPF()
                #self.BFD_assign_UE_to_UPF()
                #self.NFD_assign_UE_to_UPF()
                self.Modiefed_Greedy_Algorithm()
        if (time.time() -self.start_time) > (25*60):
            print("++++++++++++++++++++++++++++++++++++++++++")
            print("++++++++++++++++++++++++++++++++++++++++++")
            print("STOP!!!")
            print("++++++++++++++++++++++++++++++++++++++++++")
            print("++++++++++++++++++++++++++++++++++++++++++")
    def _monitor(self):
        self.text_create("port_mac_ip", " ")
        while True:
            print("=================="+str(time.time())+"===================")
            if self.MF_Flag:
                self.DNSA()
            for dp in self.datapaths.values():
                self._request_stats(dp)
            hub.sleep(10)

    def _request_stats(self, datapath):
        if DEBUG:
            print("_request_stats")
        self.logger.debug('send stats request: %016x', datapath.id)
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        req = parser.OFPFlowStatsRequest(datapath)
        datapath.send_msg(req)

        req = parser.OFPPortStatsRequest(datapath, 0, ofproto.OFPP_ANY)
        datapath.send_msg(req)

    # @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    # def _flow_stats_reply_handler(self, ev):
    #     body = ev.msg.body
    #     self.logger.info('datapath         '
    #                      'in-port  eth-dst           '
    #                      'out-port packets  bytes')
    #     self.logger.info('---------------- '
    #                      '-------- ----------------- '
    #                      '-------- -------- --------')
    #     for stat in sorted([flow for flow in body if flow.priority == 1],
    #                        key=lambda flow: (flow.match['in_port'],
    #                                          flow.match['eth_dst'])):
    #         if ev.msg.datapath.id in self.datapath_in_ues:
    #             self.logger.info('%016x %8x %17s %8x %8d %8d',
    #                              ev.msg.datapath.id,
    #                              stat.match['in_port'], stat.match['eth_dst'],
    #                              stat.instructions[0].actions[0].port,
    #                              stat.packet_count, stat.byte_count)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):

        print("Data Plane Packet rate")
        for key in self.ue_sending_rate:
            print(key, self.ue_sending_rate[key])
        self.logger.info('datapath         IP               Name                         '
                         'rx-pkts  rx-bytes '
                         'tx-pkts  tx-bytes')
        # print('datapath         IP               Name                         '
        #                  'rx-pkts  rx-bytes '
        #                  'tx-pkts  tx-bytes')
        self.logger.info('---------------- ---------------- ----------------------------- '
                         '-------- -------- '
                         '-------- --------')
        # print(('---------------- ---------------- ----------------------------- '
        #                  '-------- -------- '
        #                  '-------- --------'))
        body = ev.msg.body
        for stat in sorted(body, key=attrgetter('port_no')):
            stat_datapath_port = str(ev.msg.datapath.id) + " " + str(stat.port_no)
            if stat_datapath_port in self.port_to_ip:
                ip = self.port_to_ip[stat_datapath_port]
            else:
                continue
            if (ip in self.ue_dic) and (ip != "20.100.200.103"):
                name = self.ue_dic[ip]
                ue_rate = stat.tx_bytes / (time.time() - self.ue_start_time[ip])
                if ip in self.ue_sending_rate:
                    self.ue_sending_rate[ip].pop(0)
                    self.ue_sending_rate[ip].append(ue_rate)
                else:
                    self.ue_sending_rate[ip] = [0, 0, 0, 0, ue_rate]
                # if ip not in self.ue_d:
                #     self.ue_d[ip] = [stat.tx_packets, time.time(), time.time(), 'on', [stat.tx_packets]]
                # else:
                #     self.ue_d[ip][2] = time.time()
                #     self.ue_d[ip][0] = stat.tx_packets
                #     rate = self.ue_d[ip][0] / stat.duration_sec
                #     #if len(self.ue_d[ip][4]) < 5:
                #     self.ue_d[ip][4].append(rate)
                # else: # len(self.ue_d[ip][4]) == 5

                # print(rate)
            elif ip in self.upf_dic:
                name = self.upf_dic[ip]
            else:
                continue

            if ev.msg.datapath.id in self.datapath_in_ues:
                self.logger.info('%016x %14s %29s %8d %8d %8d %8d',
                                 ev.msg.datapath.id, ip, name,
                                 stat.rx_packets, stat.rx_bytes,
                                 stat.tx_packets, stat.tx_bytes)
                # print(
                #                  ev.msg.datapath.id, ip, name,
                #                  stat.rx_packets, stat.rx_bytes,
                #                  stat.tx_packets, stat.tx_bytes)
        # self.logger.info('datapath         port     '
        #                  'rx-pkts  rx-bytes rx-error '
        #                  'tx-pkts  tx-bytes tx-error')
        # self.logger.info('---------------- -------- '
        #                  '-------- -------- -------- '
        #                  '-------- -------- --------')
        #
        # for stat in sorted(body, key=attrgetter('port_no')):
        #     self.logger.info('%016x %8x %8d %8d %8d %8d %8d %8d',
        #                      ev.msg.datapath.id, stat.port_no,
        #                      stat.rx_packets, stat.rx_bytes, stat.rx_errors,
        #                      stat.tx_packets, stat.tx_bytes, stat.tx_errors)

    def add_flow(self, datapath, priority, match, actions):
        if DEBUG:
            print(" _add_flow")
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]

        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                idle_timeout=0, hard_timeout=0,
                                match=match, instructions=inst)
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        if DEBUG:
            print(" _packet_in_handler")
        if ev.msg.msg_len < ev.msg.total_len:
            self.logger.debug("packet truncated: only %s of %s bytes",
                              ev.msg.msg_len, ev.msg.total_len)
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(array.array('B', msg.data))

        pName = []
        ethernet_key = ""
        for p in pkt.protocols:
            # if DEBUG:
            #    print('received protocol data:', p)
            if hasattr(p, "protocol_name"):
                pName.append(p.protocol_name)
                with open("./port_mac_ip.txt", "w") as file:
                    file.write("self.mac_to_port =" + str(self.mac_to_port))
                    file.write("\n")
                    file.write("self.mac_to_ip =" + str(self.mac_to_ip))
                    file.write("\n")
                    file.write("self.port_to_ip =" + str(self.port_to_ip))
                    file.write("\n")
                if p.protocol_name == 'ethernet':
                    if p.src not in self.mac_to_ip:
                        ethernet_key = str(datapath.id) + " " + p.src
                        self.mac_to_ip[ethernet_key] = ""
                if p.protocol_name == 'ipv4' or p.protocol_name == 'arp':
                    if p.protocol_name == 'ipv4':
                        src = p.src
                        dst = p.dst
                    else:
                        src = p.src_ip
                        dst = p.dst_ip
                    if ethernet_key != "":
                        self.mac_to_ip[ethernet_key] = src
                    datapath_in_port = str(datapath.id) + " " + str(in_port)
                    self.port_to_ip[datapath_in_port] = src
                    # print(self.mac_to_port)
                    print("ipv4 ", dst, src)
                    if (src in self.ue_dic) and (src not in self.ue_start_time) and (
                            src != '20.100.200.103'):  # initial UE request rate
                        self.ue_start_time[src] = time.time()
                        if "5G-Camera" in self.ue_dic[src]:
                            self.ue_last_task[src] = 4
                        if "Edge-Devices" in self.ue_dic[src]:
                            self.ue_last_task[src] = 2
                        if "Robot-Arm" in self.ue_dic[src]:
                            self.ue_last_task[src] = 3
                            # if (src in self.ue_dic) and (dst in self.ue_dic) and self.ue_dic[dst] == 'gNB1':
                    #     # print("YES")
                    #     if src in self.ue_dic and src not in self.ue_c:
                    #         self.ue_c[src] = [1, time.time(), time.time(), 'on']
                    #     elif src in self.ue_dic and src in self.ue_c:
                    #         self.ue_c[src][0] += 1
                    #         self.ue_c[src][2] = time.time()

        print('protocol category:', pName)
        # self.logger.info('datapath         IP               Name                         '
        #                  'rx-pkts')
        # self.logger.info('---------------- ---------------- ----------------------------- '
        #                  '--------')
        # for key in self.ue_c:
        #     self.logger.info('%016x %14s %24s %8d',
        #                      datapath.id, key, self.ue_dic[key],
        #                      self.ue_c[key][0])
        # for key in self.upf0_c:
        #     self.logger.info('%016x %14s %24s %8d',
        #                      datapath.id, key, self.upf_dic[key],
        #                      self.upf0_c[key][0])
        # for key in self.upf1_c:
        #     self.logger.info('%016x %14s %24s %8d',
        #                      datapath.id, key, self.upf_dic[key],
        #                      self.upf1_c[key][0])
        # for key in self.upf2_c:
        #     self.logger.info('%016x %14s %24s %8d',
        #                      datapath.id, key, self.upf_dic[key],
        #                      self.upf2_c[key][0])
        # for key in self.upf3_c:
        #     self.logger.info('%016x %14s %24s %8d',
        #                      datapath.id, key, self.upf_dic[key],
        #                      self.upf3_c[key][0])
        #
        # self.logger.info('Current UE routing table')
        # self.logger.info('Path                              UPF')
        # self.logger.info('--------------------------------- --------')
        # for key in self.UERouting:
        #     self.logger.info('%29s %8s', key, self.UERouting[key])
        #
        # self.logger.info("upf0 ")
        # self.logger.info(self.upf0_c)
        # self.logger.info("upf1 ")
        # self.logger.info(self.upf1_c)
        # self.logger.info("upf2 ")
        # self.logger.info(self.upf2_c)
        # self.logger.info("upf3 ")
        # self.logger.info(self.upf3_c)

        # datapath = msg.datapath
        # ofproto = datapath.ofproto
        # parser = datapath.ofproto_parser
        # in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            # ignore lldp packet
            return

        dst = eth.dst
        src = eth.src

        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})

        #self.logger.info("packet in %s %s %s %s", dpid, src, dst, in_port)
        # learn a mac address to avoid FLOOD next time.
        self.mac_to_port[dpid][src] = in_port

        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # install a flow to avoid packet_in next time
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                self.add_flow(datapath, 1, match, actions, msg.buffer_id)
                return
            else:
                self.add_flow(datapath, 1, match, actions)

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)
