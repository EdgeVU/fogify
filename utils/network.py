import os
import subprocess
import time

import docker
import requests
import yaml

def generate_network_distribution(path, name="experimental"):
    subprocess.check_output(['/bin/sh', '-c',"cat %s | grep icmp_seq | cut -d'=' -f4 | cut -d' ' -f1 >  %s"%(os.path.join(path, "rttdata.txt"), os.path.join(path, "rttdata2.txt"))])
    subprocess.check_output(['/bin/sh', '-c',os.path.dirname(os.path.abspath(__file__)) + "/network_scripts/maketable %s > %s"%(os.path.join(path, "rttdata2.txt"), os.path.join(path, name+".dist"))])
    return subprocess.check_output(['/bin/sh', '-c', os.path.dirname(os.path.abspath(__file__)) + "/network_scripts/stats %s" % os.path.join(path, "rttdata2.txt")]).decode("utf-8")


def inject_network_distribution(trace_file):
    return subprocess.check_output(['/bin/sh', '-c', "cp %s /usr/lib/tc" % trace_file])

def apply_network_rule(container, network, in_rule, out_rule, ifb_interface, create="TRUE", _ips={},
                       namespace_path=os.environ['NAMESPACE_PATH'] if 'NAMESPACE_PATH' in os.environ else "proc"):
    from utils import DockerManager

    pid = DockerManager.get_pid_from_container(container)
    adapter = None
    count = 0

    namespace_path = namespace_path[1:] if namespace_path.startswith("/") else namespace_path
    namespace_path = namespace_path[:-1] if namespace_path.endswith("/") else namespace_path

    while(adapter is None and count<20):
        adapter = DockerManager.get_containers_adapter_for_network(container, network, namespace_path=namespace_path)
        time.sleep(1)
        count+=1
    ifb_interface = ifb_interface + adapter[-1]

    subprocess.check_output(
        [os.path.dirname(os.path.abspath(__file__)) + '/apply_rule.sh',
            pid, adapter, in_rule, out_rule, ifb_interface, str(create).lower(), namespace_path])
    print(pid, adapter, in_rule, out_rule, ifb_interface, str(create).lower(), namespace_path)
    counter = 12
    for ip in _ips:
        ips = ip.split("|")
        subprocess.check_output(['/bin/sh', '-c',"nsenter -n/%s/%s/ns/net tc class add dev %s parent 1:1 classid 1:%s htb rate 10000mbit" % (
            namespace_path, pid, 'ifb'+ifb_interface, str(counter))])
        subprocess.check_output(['/bin/sh', '-c',"nsenter -n/%s/%s/ns/net tc qdisc add dev %s parent 1:%s handle %s: netem %s " % (namespace_path, pid, 'ifb'+ifb_interface,str(counter),str(counter), _ips[ip])])
        for ip in ips:
            subprocess.check_output(['/bin/sh', '-c',"nsenter -n/%s/%s/ns/net tc filter add dev %s protocol ip prio 1 u32 match ip src %s flowid 1:%s \n" % (
            namespace_path, pid, 'ifb'+ifb_interface, ip, str(counter))])
        counter += 1


def read_network_rules(path):
    f = open(os.path.join(path, "network.yaml"), "r")
    infra = yaml.load(f, Loader=yaml.UnsafeLoader)
    return infra

def apply_default_rules(infra, service_name, container_name, container_id):
    net_rules = infra[service_name.replace("fogify_", "")]
    f_name = service_name.replace("fogify_", "")
    from utils import DockerManager
    for net in net_rules:
        ips_to_rule = {}
        if 'links' in net_rules[net] and f_name in net_rules[net]['links']:

            for i in net_rules[net]['links'][f_name]:
                network_ips={}
                while net not in network_ips:
                    network_ips = DockerManager.get_ips_for_service(i)

                ips_to_rule["|".join(network_ips[net])] = net_rules[net]['links'][service_name.replace("fogify_", "")][i]

        apply_network_rule(container_name,
                           net,
                           net_rules[net]['downlink'],
                           net_rules[net]['uplink'],
                           container_id[:10],
                           create="TRUE", _ips=ips_to_rule)  # TODO update rules


class NetworkController(object):


    def submition(self, path):

        client = docker.from_env()
        for event in client.events(decode=True):

            try:
                if 'status' in event and event['status']=='start' and 'Type' in event and event['Type']=='container':


                    attrs = event['Actor']['Attributes']
                    infra = read_network_rules(path)
                    if attrs['com.docker.stack.namespace']=='fogify':

                        service_name = attrs['com.docker.swarm.service.name']
                        container_id = attrs['com.docker.swarm.task.id']
                        container_name = attrs['com.docker.swarm.task.name']
                        apply_default_rules(infra, service_name, container_name, container_id)


                        # update containers for new links
                        update_for_services_needed = set()
                        net_rules = infra[service_name.replace("fogify_","")]
                        f_name = service_name.replace("fogify_", "")
                        for net in net_rules:
                            for i in net_rules[net]['links']:
                                for j in net_rules[net]['links'][i]:
                                    if j == f_name:
                                        update_for_services_needed.add(i)
                        # for i in update_for_services_needed:
                        str_set="|".join(update_for_services_needed)
                        action_url = 'http://%s:5000/control/%s/'%(os.environ['CONTROLLER_IP'] if 'CONTROLLER_IP' in os.environ else '0.0.0.0', str_set)
                        requests.post(action_url, headers={'Content-Type': "application/json"} )
                        # update network rules to controller


            except KeyError as ex:
                print(ex)
                continue

