from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from onnx import numpy_helper, ValueInfoProto, AttributeProto, GraphProto, NodeProto, TensorProto, TensorShapeProto
import onnx
from typing import Any, Text, Iterable, List, Dict, Sequence, Optional, Tuple, Union
from typing_extensions import Protocol
import numpy as np


class Transformer(Protocol):
    def __call__(self, graph):  # type: (Graph) -> Graph
        pass


EdgeInfo = Tuple[Text, Any, TensorShapeProto]
AttributeValue = Any # TODO Union[Sequence[float], Sequence[int], Sequence[Text], Sequence[TensorProto], Sequence[GraphProto]]

def _input_from_onnx_input(input):  # type: (ValueInfoProto) -> EdgeInfo
    name = input.name
    type = input.type.tensor_type.elem_type
    shape = tuple([d.dim_value for d in input.type.tensor_type.shape.dim])
    return (name, type, shape)


def _convertAttributeProto(onnx_arg):  # type: (AttributeProto) -> AttributeValue
    """
    Convert an ONNX AttributeProto into an appropriate Python object
    for the type.
    NB: Tensor attribute gets returned as numpy array
    """
    if onnx_arg.HasField('f'):
        return onnx_arg.f
    elif onnx_arg.HasField('i'):
        return onnx_arg.i
    elif onnx_arg.HasField('s'):
        return onnx_arg.s
    elif onnx_arg.HasField('t'):
        return numpy_helper.to_array(onnx_arg.t)
    elif len(onnx_arg.floats):
        return list(onnx_arg.floats)
    elif len(onnx_arg.ints):
        return list(onnx_arg.ints)
    elif len(onnx_arg.strings):
        return list(onnx_arg.strings)
    else:
        raise ValueError("Unsupported ONNX attribute: {}".format(onnx_arg))


class Attributes(Dict[Text, Any]):
    @staticmethod
    def from_onnx(args):  # type: (Iterable[AttributeProto]) -> Attributes
        d = Attributes()
        for arg in args:
            d[arg.name] = _convertAttributeProto(arg)
        return d


class Node(object):
    def __init__(self,
                 name,  # type: Optional[Text]
                 op_type,  # type: Text
                 attrs,  # type: Dict[Text, AttributeValue]
                 inputs,  # type: List[Text]
                 outputs,  # type: List[Text]
                 ):
        # type: (...) -> None
        self.name = name
        self.op_type = op_type
        self.attrs = attrs
        self.inputs = inputs
        self.outputs = outputs
        self.input_tensors = {}  # type: Dict[Text, np._ArrayLike[Any]]
        self.parents = []  # type: List[Node]
        self.children = []  # type: List[Node]
        self.metadata = {}  # type: Dict[Any, Any]

    def add_parent(self, parent_node):  # type: (Node) -> None
        assert parent_node not in self.parents
        self.parents.append(parent_node)
        if self not in parent_node.children:
            parent_node.children.append(self)

    def add_child(self, child_node):  # type: (Node) -> None
        assert child_node not in self.children
        self.children.append(child_node)
        if self not in child_node.parents:
            child_node.parents.append(self)

    def get_only_parent(self):  # type: () -> Node
        if len(self.parents) != 1:
            raise ValueError('Node ({}) expected to have 1 parent. Found {}.'
                             .format(self, len(self.parents)))
        return self.parents[0]

    @staticmethod
    def from_onnx(node):  # type: (NodeProto) -> Node
        attrs = Attributes.from_onnx(node.attribute)
        name = Text(node.name)
        if len(name) == 0:
            name = "_".join(node.output)
        return Node(
            name, node.op_type, attrs, list(node.input), list(node.output)
        )


class Graph(object):
    def __init__(self,
                 nodes,  # type: List[Node]
                 inputs,  # type: List[EdgeInfo]
                 outputs,  # type: List[EdgeInfo]
                 shape_dict, # type: Dict[Text,Tuple[int,...]]
                 ):
        # type: (...) -> None
        self.nodes = nodes
        self.inputs = inputs
        self.outputs = outputs
        self.shape_dict = shape_dict  # data blob name to its shape

        # data blob name to the list of op types it feeds into
        self.blob_to_op_type = {} # type: Dict[Text, List[Text]]
        # data blob name to the op_type that generates it
        self.blob_from_op_type = {}  # type: Dict[Text, Text]

        for node_ in nodes:
            for input_ in node_.inputs:
                if input_ in self.blob_to_op_type:
                    self.blob_to_op_type[input_].append(node_.op_type)
                else:
                    self.blob_to_op_type[input_] = [node_.op_type]
            for output_ in node_.outputs:
                if output_ in self.blob_from_op_type:
                    raise ValueError("Data blob: %s, is generated by more than 1 op" %(output_))
                self.blob_from_op_type[output_] = node_.op_type


    def transformed(self, transformers):  # type: (Iterable[Transformer]) -> Graph
        graph = self
        for transformer in transformers:
            graph = transformer(graph)
        return graph

    def has_edge_name(self, name):  # type: (Text) -> bool
        '''
        Check if name is already used for graph inputs/outputs or for nodes
        inputs/outputs
        '''
        names = set()
        for input in self.inputs:
            names.add(input[0])
        for output in self.outputs:
            names.add(output[0])
        for node in self.nodes:
            names.update(node.inputs)
            names.update(node.outputs)
        return name in names

    def get_unique_edge_name(self, name):  # type: (Text) -> Text
        n_ = name
        i = 0
        while self.has_edge_name(n_):
            n_ = "{}_{}".format(name, i)
            i += 1
        return n_

    @staticmethod
    def from_onnx(graph):  # type: (GraphProto) -> Graph
        input_tensors = {
            t.name: numpy_helper.to_array(t) for t in graph.initializer
        }
        print("=======weights tensors name:",input_tensors.keys())
        nodes_ = []
        nodes_by_input = {}  # type: Dict[Text, List[Node]]
        nodes_by_output = {}
        for node in graph.node:
            #print("============", onnx.helper.printable_node(node))
            node_ = Node.from_onnx(node)
            for input_ in node_.inputs:
                if input_ in input_tensors:
                    node_.input_tensors[input_] = input_tensors[input_]
                else:
                    if input_ in nodes_by_input:
                        input_nodes = nodes_by_input[input_]
                    else:
                        input_nodes = []
                        nodes_by_input[input_] = input_nodes
                    input_nodes.append(node_)
            for output_ in node_.outputs:
                nodes_by_output[output_] = node_
            nodes_.append(node_)

        inputs = []
        for i in graph.input:
            if i.name not in input_tensors:
                inputs.append(_input_from_onnx_input(i))

        outputs = []
        for o in graph.output:
            outputs.append(_input_from_onnx_input(o))

        for node_ in nodes_:
            for input_ in node_.inputs:
                if input_ in nodes_by_output:
                    node_.parents.append(nodes_by_output[input_])
            for output_ in node_.outputs:
                if output_ in nodes_by_input:
                    node_.children.extend(nodes_by_input[output_])

        # Dictionary to hold the "value_info" field from ONNX graph
        shape_dict = {} # type: Dict[Text,Tuple[int,...]]

        def extract_value_info(shape_dict, # type: Dict[Text,Tuple[int,...]]
                               value_info, # type: ValueInfoProto[...]
                               ):
            # type: (...) -> None
            shape_dict[value_info.name] = tuple([int(dim.dim_value) for dim in value_info.type.tensor_type.shape.dim])

        for value_info in graph.value_info:
            extract_value_info(shape_dict, value_info)
        for value_info in graph.input:
            extract_value_info(shape_dict, value_info)
        for value_info in graph.output:
            extract_value_info(shape_dict, value_info)


        return Graph(nodes_, inputs, outputs, shape_dict)
