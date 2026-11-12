

"""Dataset class for loading and processing KG datasets."""

import os
import pickle as pkl
import queue

import numpy as np
import torch

import networkx as nx
import matplotlib.pyplot as plt
from collections import Counter
from tqdm import tqdm

# from process_icews_gdelt import get_idx

class KGDataset(object):

    def __init__(self, data_path, debug):

        self.data_path = data_path
        self.debug = debug
        self.data = {}
        for split in ["train", "test", "valid"]:
            file_path = os.path.join(self.data_path, split + ".pickle")
            with open(file_path, "rb") as in_file:
                self.data[split] = pkl.load(in_file)
        filters_file = open(os.path.join(self.data_path, "to_skip.pickle"), "rb")
        self.to_skip = pkl.load(filters_file)
        filters_file.close()
        max_axis = np.max(self.data["train"], axis=0)
        self.n_entities = int(max(max_axis[0], max_axis[2]) + 1)
        self.n_predicates = int(max_axis[1] + 1) * 2
        # if "wikidata" in data_path or "yago15k" in data_path:
        #     self.end_time = int(max_axis[4] + 1)

    def get_examples(self, split, rel_idx=-1):

        examples = self.data[split]
        if split == "train":
            copy = np.copy(examples)
            tmp = np.copy(copy[:, 0])
            copy[:, 0] = copy[:, 2]
            copy[:, 2] = tmp
            copy[:, 1] += self.n_predicates // 2
            examples = np.vstack((examples, copy))
        if rel_idx >= 0:
            examples = examples[examples[:, 1] == rel_idx]
        if self.debug:
            examples = examples[:1000]
        return torch.from_numpy(examples.astype("int64"))

    def get_filters(self, ):
        """Return filter dict to compute ranking metrics in the filtered setting."""
        return self.to_skip

    def get_shape(self):
        """Returns KG dataset shape."""
        return self.n_entities, self.n_predicates, self.n_entities

    def calculate_hierarchy_score(self,data):
        # event=np.zeros((self.n_predicates//2, self.n_entities, self.n_entities))
        # temporal=np.zeros((self.n_time, self.n_entities, self.n_entities))
        relation = []
        timestamps = []

        example=self.data["train"].tolist()
        example+=self.data["valid"].tolist()
        example+=self.data["test"].tolist()

        # for s, r, o, t in example:
        #     event[r][s][o] = 1
        #     temporal[t][s][o] = 1

        # r={}
        # for s, re, o, t in example:
        #     if re not in r.keys():
        #         r[re]=1
        #     else:
        #         r[re]+=1
        # file1 = "./num_rela_{}.txt".format(data)
        #
        # r_list=sorted(r)
        #
        # f = open(file1,"w")
        # for ke in r_list:
        #     f.write(str(ke)+'\t'+str(r[ke])+'\n')

        # f.close()

        for r in range(self.n_predicates//2):
            event = np.zeros((self.n_entities, self.n_entities))
            for s, re, o in example:
                if re == r:
                    event[s][o] = 1

            even=0
            total_e=0
            for s in range(self.n_entities):
                for o in range(self.n_entities):
                    if event[s][o] == 1:
                        total_e += 1
                        if event[o][s] == 0:
                            even += 1

            score_event = even / total_e
            print(score_event)
            file = "./hier_rela_{}.txt".format(data)
            with open(file, 'a') as f:
                f.write(str(r)+'\t'+str(score_event)+'\t'+'\n')

        # for t in range(self.n_time):
        #     time = np.zeros((self.n_entities, self.n_entities))
        #     for s, r, o, ti in example:
        #         if ti == t:
        #             time[s][o] = 1
        #
        #     ti=0
        #     total_t=0
        #     for s in range(self.n_entities):
        #         for o in range(self.n_entities):
        #             if time[s][o] == 1:
        #                 total_t += 1
        #                 if time[o][s] == 0:
        #                     ti += 1
        #
        #     score_time = ti / total_t
        #     print(score_time)
        #     file = "./hier_time_{}.txt".format(data)
        #     with open(file, 'a') as f:
        #         f.write(str(t)+'\t'+str(score_time)+'\t'+'\n')
    #
    #
    #
    # def dis(self,path,r,h,distance1,di,view1):
    #     view = view1.copy()
    #     distance = distance1.copy()
    #     if h in path[r].keys():
    #         for nod in path[r][h]:
    #             if h not in view:
    #                 view.append(h)
    #             if nod in view:
    #                 return 0
    #             if h not in distance[r].keys():
    #                 distance[r][h]={}
    #
    #             distance[r][h][di] = self.dis(path,r,nod,distance,di,view)+1
    #             di +=1
    #             # distance[r][h][di]=distance[r][nod][di]+1
    #
    #         return distance[r][h][di-1]
    #     else:
    #         return 0
    #
    #
    #
    #
    # def cal_path(self):
    #     path={}
    #     example1=self.data["train"].tolist()
    #     example1+=self.data["valid"].tolist()
    #     example1+=self.data["test"].tolist()
    #     example=[]
    #     for i in example1:
    #         if i not in example:
    #             example.append(i)
    #     for s,r,o,t in example:
    #         if r not in path.keys():
    #             path[r]={}
    #         if s not in path[r].keys():
    #             path[r][s]=[]
    #             path[r][s].append(o)
    #             path[r][s]= list(set(path[r][s]))
    #         else:
    #             path[r][s].append(o)
    #             path[r][s]= list(set(path[r][s]))
    #
    #     distance={}
    #
    #
    #     for r in path.keys():
    #
    #         distance[r]={}
    #         view = []
    #
    #         for h in path[r].keys():
    #             distance[r][h]={}
    #             di = 0
    #             self.dis(path,r,h,distance,di,view)
    #             view = []
    #         print(r)
    #
    #     return distance
    #
    # def cal_long(self):
    #     dis = self.cal_path()
    #     path=0
    #     long=[]
    #     aver=[]
    #     for r in range(self.n_predicates//2):
    #         sum_value = 0
    #         sum_key = 0
    #         max = 0
    #         for h in dis[r].keys():
    #             for index in dis[r][h].keys():
    #                 sum_key+=1
    #                 sum_value+=dis[r][h][index]
    #                 if dis[r][h][index]>max:
    #                     max = dis[r][h][index]
    #
    #         aver.append(sum_value/sum_key)
    #         long.append(max)
    #
    #         print(r)
    #
    #
    #     _,rel2idx,_ = get_idx(self.data_path)
    #
    #     file = "./path_{}.txt".format(data)
    #     f = open(file,"w")
    #
    #     f.write("rel" + '\t' + "aver" + '\t' + "max" + '\n')
    #     for r,idx in rel2idx.items():
    #         f.write(str(r) + '\t' + str(aver[idx]) + '\t' + str(long[idx]) + '\n')
    #
    #     f.close()
    #
    #     return aver,long


    def path(self):
        example1=self.data["train"].tolist()
        example1+=self.data["valid"].tolist()
        example1+=self.data["test"].tolist()
        example=[]
        for i in example1:
            if i not in example:
                example.append(i)
        for rel in range(self.n_predicates//2):
            G = nx.MultiDiGraph()
            for s,r,o,t in example:
                if r !=rel:
                    continue

                G.add_nodes_from([s,o])
                G.add_edge(s,o)

    def get_max_path_list(self,g):
        path_len = []
        for node in tqdm(list(g.nodes)):
            for _ in list(g.nodes):
                g.nodes[_]["state"] = 0

            g.nodes[node]["state"] = 1
            k = 0
            node_nbor = list(g.neighbors(node))
            if len(node_nbor) == 0:
                k=0
            else:
                while True:
                    k += 1
                    new_nbor = []
                    for node1 in node_nbor:
                        g.nodes[node1]["state"] = 1
                    for node2 in node_nbor:
                        if len(list(g.neighbors(node2))) == 0:
                            pass
                        else:
                            for N in list(g.neighbors(node2)):
                                if g.nodes[N]["state"] == 1:
                                    pass
                                else:
                                    g.nodes[N]["state"] = 1
                                    new_nbor.append(N)
                    if len(new_nbor) == 0:
                        path_len.append(k)
                        break
                    else:
                        node_nbor = new_nbor
        return path_len



    def path(self):
        '''
        网络结构从边关系文件加载格式为列表，列表的元素是一个节点对的元组例如
        edges = [(1,2),(1,3),(3,4),...]
        '''

        example = self.data["train"].tolist()
        example+= self.data["valid"].tolist()
        example+=self.data["test"].tolist()
        # for ex in example1:
        #     if ex not in example:
        #         example.append(ex)
        # example += self.data["valid"].tolist()
        # example += self.data["test"].tolist()
        long=[]
        aver=[]

        for rel in range(self.n_predicates // 2):
            G = nx.MultiDiGraph()
            node=[]
            rn=0
            for s, r, o in example:
                if r != rel:
                    continue
                rn+=1
                node.append([s,o])
                # G.add_nodes_from([s, o])

            G.add_edges_from(node)
            print(G.number_of_edges())
            path_length = self.get_max_path_list(G)
            length_distribute = Counter(path_length)
            print(length_distribute)

            max=0
            sum=0
            nu=0
            for len,num in length_distribute.items():
                if max<len:
                    max =len
                sum +=len*num
                nu +=num
            long.append(max)
            aver.append(sum/nu)

            # pathlengths=[]
            # for v in G.nodes():
            #     spl = nx.single_source_shortest_path_length(G, v)
            #     for p in spl.values():
            #         if p ==0:
            #             continue
            #         pathlengths.append(p)
            # av = sum(pathlengths) / pathlengths.__len__()
            # aver.append(av)
            # # 取出每条路径，计算平均值。
            # print('')
            # print("average shortest path length %s" % (av))



        file = "./path_{}.txt".format(data)
        f = open(file,"w")

        for rel in range(self.n_predicates // 2):
            f.write(str(rel) + '\t' + str(long[rel])+ '\t' + str(aver[rel])  + '\n')

        f.close()


    def tim(self):
        '''
        网络结构从边关系文件加载格式为列表，列表的元素是一个节点对的元组例如
        edges = [(1,2),(1,3),(3,4),...]
        '''

        example = self.data["train"].tolist()

        example+= self.data["valid"].tolist()
        example+=self.data["test"].tolist()
        # for ex in example1:
        #     if ex not in example:
        #         example.append(ex)

        # example += self.data["valid"].tolist()
        # example += self.data["test"].tolist()
        long=[]
        aver=[]

        for ti in range(self.n_time):
            G = nx.MultiDiGraph()
            node=[]
            rn=0
            for s, r, o, t in example:
                if t != ti:
                    continue
                rn+=1
                node.append([s,o])
                # G.add_nodes_from([s, o])

            G.add_edges_from(node)
            print(G.number_of_edges())
            path_length = self.get_max_path_list(G)
            length_distribute = Counter(path_length)
            print(length_distribute)

            max=0
            sum=0
            nu=0
            for len,num in length_distribute.items():
                if max<len:
                    max =len
                sum +=len*num
                nu +=num
            long.append(max)
            aver.append(sum/nu)

            # pathlengths=[]
            # for v in G.nodes():
            #     spl = nx.single_source_shortest_path_length(G, v)
            #     for p in spl.values():
            #         if p ==0:
            #             continue
            #         pathlengths.append(p)
            # av = sum(pathlengths) / pathlengths.__len__()
            # aver.append(av)
            # # 取出每条路径，计算平均值。
            # print('')
            # print("average shortest path length %s" % (av))



        file = "./time_{}.txt".format(data)
        f = open(file,"w")

        for ti in range(self.n_time):
            f.write(str(ti) + '\t' + str(long[ti])+ '\t' + str(aver[ti])  + '\n')

        f.close()


data="FB237"



data_dir = "../data/%s/" %data

d = KGDataset(data_dir, False)
d.calculate_hierarchy_score(data)
# aver, long = d.cal_long()


# d.path()
# d.tim()


