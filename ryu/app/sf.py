from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.ofproto import ofproto_v1_3
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.topology import event
from ryu.topology.api import get_switch, get_link
import networkx as nx


class ShortestForwarding(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(ShortestForwarding, self).__init__(*args, **kwargs)
        self.network = nx.DiGraph()
        self.topology_api_app = self
        self.paths = {}

    #print topo
    def printG(self):
        G = self.network
        print("G")
        print("nodes", G.nodes())  # 输出全部的节点： [1, 2, 3]
        print("edges", G.edges())  # 输出全部的边：[(2, 3)]
        print("number_of_edges", G.number_of_edges())  # 输出边的数量：1
        for e in G.edges():
            print(G.get_edge_data(e[0], e[1]))

    # handle switch features info
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_feature_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # install a table_miss flow entry for each datapath
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]

        # install flow entry
        self.add_flow(datapath, 0, match, actions)

    def add_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]
        # if buffer_id:
        #     mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
        #                             priority=priority, match=match,
        #                             instructions=inst)
        # else:
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                match=match, instructions=inst)
        datapath.send_msg(mod)

    # get topoloty and store it into networkx object
    @set_ev_cls(event.EventSwitchEnter, [CONFIG_DISPATCHER, MAIN_DISPATCHER])
    def get_topology(self, ev):
        # get nodes
        switch_list = get_switch(self.topology_api_app, None)
        switches = [switch.dp.id for switch in switch_list]
        self.network.add_nodes_from(switches)

        # get links
        links_list = get_link(self.topology_api_app, None)
        links = [(link.src.dpid, link.dst.dpid, {'port': link.src.port_no}) for link in links_list]
        self.network.add_edges_from(links)

        # reverse links
        links = [(link.dst.dpid, link.src.dpid, {'port': link.dst.port_no}) for link in links_list]
        self.network.add_edges_from(links)

        self.printG()

    # get our_port by using networks's Dijkstra algorithm
    def get_out_port(self, datapath, src, dst, in_port):
        dpid = datapath.id

        # add links between host and switches
        if src not in self.network:
            self.network.add_node(src)
            self.network.add_edge(src, dpid)
            self.network.add_edge(dpid, src, port=in_port, weight=0)
            self.paths.setdefault(src, {})

        # search dst's shortest path
        if dst in self.network:
            G= self.network
            G[1][2]['weight'] = 100
            G[2][1]['weight'] = 100
            G[2][3]['weight'] = 10
            G[3][2]['weight'] = 10

            G[1][5]['weight'] = 10
            G[5][1]['weight'] = 10
            G[4][5]['weight'] = 10
            G[5][4]['weight'] = 10
            G[4][3]['weight'] = 10
            G[3][4]['weight'] = 10
            if dst not in self.paths[src]:
                # try:
                path = nx.shortest_path(self.network, src, dst, weight="weight")
                #     print(path)
                # except nx.NetworkXNoPath:
                #     print('No path')
                self.paths[src][dst] = path

            path = self.paths[src][dst]
            next_hop = path[path.index(dpid) + 1]
            out_port = self.network[dpid][next_hop]['port']
            print('path: ', path)
        else:
            out_port = datapath.ofproto.OFPP_FLOOD

        return out_port

    # handle packet_in msg
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        in_port = msg.match["in_port"]

        # get out_port
        out_port = self.get_out_port(datapath, eth.src, eth.dst, in_port)
        actions = [parser.OFPActionOutput(out_port)]

        # install a flow to avoid packet_in next time
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=eth.dst)
            self.add_flow(datapath, 1, match, actions)

        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=msg.data)
        datapath.send_msg(out)
