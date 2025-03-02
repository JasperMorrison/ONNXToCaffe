# -*- coding: utf-8 -*-
from __future__ import print_function
import sys

import os,sys
# caffe_root='/opt/caffe/python'
# os.chdir(caffe_root)
# sys.path.insert(0,caffe_root)

import caffe
import onnx
import numpy as np
from caffe.proto import caffe_pb2
caffe.set_mode_cpu()
from onnx2caffe._transformers import *
from onnx2caffe._graph import Graph

import onnx2caffe._operators as cvt
import onnx2caffe._weightloader as wlr
from onnx2caffe._error_utils import ErrorHandling
from collections import OrderedDict
from onnx import shape_inference

from modelComparator import compareOnnxAndCaffe

transformers = [
    TransposeKiller(),
    ConstantsToInitializers(),
    ConvAddFuser(),
    MatmulAddFuser(),
    UnsqueezeFuser(),
]

def convertToCaffe(graph,opset_version, prototxt_save_path, caffe_model_save_path):

    exist_edges = []
    layers = []
    exist_nodes = []
    err = ErrorHandling()
    for i in graph.inputs:
        edge_name = i[0]
        input_layer = cvt.make_input(i,opset_version)
        layers.append(input_layer)
        exist_edges.append(i[0])
        graph.channel_dims[edge_name] = graph.shape_dict[edge_name][1]


    for id, node in enumerate(graph.nodes):
        print(node.name, node.op_type)
        node_name = node.name
        op_type = node.op_type
        inputs = node.inputs
        inputs_tensor = node.input_tensors
        input_non_exist_flag = False

        for inp in inputs:
            if inp not in exist_edges and inp not in inputs_tensor:
                input_non_exist_flag = True
                break
        if input_non_exist_flag:
            continue

        if op_type not in cvt._ONNX_NODE_REGISTRY:
            err.unsupported_op(node)
            continue
        converter_fn = cvt._ONNX_NODE_REGISTRY[op_type]
        layer = converter_fn(node,graph,err)
        if type(layer)==tuple:
            for l in layer:
                layers.append(l)
        else:
            layers.append(layer)
        outs = node.outputs
        for out in outs:
            exist_edges.append(out)

    net = caffe_pb2.NetParameter()
    for id,layer in enumerate(layers):
        layers[id] = layer._to_proto()
    net.layer.extend(layers)

    with open(prototxt_save_path, 'w') as f:
        print(net,file=f)

    caffe.set_mode_cpu()
    deploy = prototxt_save_path
    net = caffe.Net(deploy,
                    caffe.TEST)

    for id, node in enumerate(graph.nodes):
        node_name = node.name
        op_type = node.op_type
        inputs = node.inputs
        inputs_tensor = node.input_tensors
        input_non_exist_flag = False
        if op_type not in wlr._ONNX_NODE_REGISTRY:
            err.unsupported_op(node)
            continue
        converter_fn = wlr._ONNX_NODE_REGISTRY[op_type]
        converter_fn(net, node, graph, err)

    net.save(caffe_model_save_path)
    return net

def getGraph(onnx_path):
    model = onnx.load(onnx_path)
    opset_version = model.opset_import[0].version  # 获取 opset version ,不同的 opset version 下 onnx的 op解析方式不同
    #model = shape_inference.infer_shapes(model)
    print(onnx.helper.printable_graph(model.graph))
    model_graph = model.graph
    graph = Graph.from_onnx(model_graph)
    graph = graph.transformed(transformers)
    graph.channel_dims = {}

    return graph, opset_version

if __name__ == "__main__":
    onnx_path = sys.argv[1]
    prototxt_path = sys.argv[2]
    caffemodel_path = sys.argv[3]
    # graph = getGraph(onnx_path)
    graph, opset_version = getGraph(onnx_path)
    convertToCaffe(graph, opset_version, prototxt_path, caffemodel_path)
    compareOnnxAndCaffe(onnx_path,prototxt_path,caffemodel_path)

