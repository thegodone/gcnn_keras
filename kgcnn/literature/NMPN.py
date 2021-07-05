import tensorflow.keras as ks

from kgcnn.layers.casting import ChangeTensorType
from kgcnn.layers.gather import GatherNodesOutgoing
from kgcnn.layers.keras import Dense
from kgcnn.layers.mlp import MLP
from kgcnn.layers.pooling import PoolingLocalEdges, PoolingNodes
from kgcnn.layers.set2set import Set2Set
from kgcnn.layers.update import GRUUpdate, TrafoMatMulMessages
from kgcnn.ops.models import generate_edge_embedding, update_model_args, generate_node_embedding


# Neural Message Passing for Quantum Chemistry
# by Justin Gilmer, Samuel S. Schoenholz, Patrick F. Riley, Oriol Vinyals, George E. Dahl
# http://arxiv.org/abs/1704.01212    


def make_nmpn(
        # Input
        input_node_shape,
        input_edge_shape,
        input_embedding: dict = None,
        # Output
        output_embedding: dict = None,
        output_mlp: dict = None,
        # Model specific
        depth=3,
        node_dim=128,
        edge_dense: dict = None,
        use_set2set=True,
        set2set_args: dict = None,
        pooling_args: dict = None
):
    """Get Message passing model.

    Args:
        input_node_shape (list): Shape of node features. If shape is (None,) embedding layer is used.
        input_edge_shape (list): Shape of edge features. If shape is (None,) embedding layer is used.
        input_embedding (dict): Dictionary of embedding parameters used if input shape is None. Default is
            {"nodes": {"input_dim": 95, "output_dim": 64},
            "edges": {"input_dim": 5, "output_dim": 64},
            "state": {"input_dim": 100, "output_dim": 64}}.
        output_embedding (str): Dictionary of embedding parameters of the graph network. Default is
            {"output_mode": 'graph', "output_tensor_type": 'padded'}
        output_mlp (dict): Dictionary of MLP arguments for output regression or classification. Default is
            {"use_bias": [True, True, False], "units": [25, 10, 1],
            "output_activation": ['selu', 'selu', 'sigmoid']}
        depth (int, optional): Depth. Defaults to 3.
        node_dim (int, optional): Dimension for hidden node representation. Defaults to 128.
        edge_dense (dict): Dictionary of arguments for NN to make edge matrix. Default is
            {'use_bias' : True, 'activation' : 'selu'}
        use_set2set (bool, optional): Use set2set layer. Defaults to True.
        set2set_args (dict): Dictionary of Set2Set Layer Arguments. Default is
            {'channels': 32, 'T': 3, "pooling_method": "sum", "init_qstar": "0"}
        pooling_args (dict): Dictionary for message pooling arguments. Default is
            {'pooling_method': "segment_mean"}

    Returns:
        tf.keras.models.Model: Message Passing model.
    """
    # Make default parameter
    model_default = {'input_embedding': {"nodes": {"input_dim": 95, "output_dim": 64},
                                         "edges": {"input_dim": 5, "output_dim": 64},
                                         "state": {"input_dim": 100, "output_dim": 64}},
                     'output_embedding': {"output_mode": 'graph', "output_tensor_type": 'padded'},
                     'output_mlp': {"use_bias": [True, True, False], "units": [25, 10, 1],
                                    "activation": ['selu', 'selu', 'sigmoid']},
                     'set2set_args': {'channels': 32, 'T': 3, "pooling_method": "sum",
                                      "init_qstar": "0"},
                     'pooling_args': {'pooling_method': "segment_mean"},
                     'edge_dense': {'use_bias': True, 'activation': 'selu'}
                     }

    # Update model args
    input_embedding = update_model_args(model_default['input_embedding'], input_embedding)
    output_embedding = update_model_args(model_default['output_embedding'], output_embedding)
    output_mlp = update_model_args(model_default['output_mlp'], output_mlp)
    set2set_args = update_model_args(model_default['set2set_args'], set2set_args)
    pooling_args = update_model_args(model_default['pooling_args'], pooling_args)
    edge_dense = update_model_args(model_default['edge_dense'], edge_dense)

    # Make input embedding, if no feature dimension
    node_input = ks.layers.Input(shape=input_node_shape, name='node_input', dtype="float32", ragged=True)
    edge_input = ks.layers.Input(shape=input_edge_shape, name='edge_input', dtype="float32", ragged=True)
    edge_index_input = ks.layers.Input(shape=(None, 2), name='edge_index_input', dtype="int64", ragged=True)
    n = generate_node_embedding(node_input, input_node_shape, input_embedding['nodes'])
    ed = generate_edge_embedding(edge_input, input_edge_shape, input_embedding['edges'])
    edi = edge_index_input

    n = Dense(node_dim, activation="linear")(n)
    edge_net = Dense(node_dim * node_dim, **edge_dense)(ed)
    gru = GRUUpdate(node_dim)

    for i in range(0, depth):
        eu = GatherNodesOutgoing()([n, edi])
        eu = TrafoMatMulMessages(node_dim, )([edge_net, eu])
        eu = PoolingLocalEdges(**pooling_args)(
            [n, eu, edi])  # Summing for each node connections
        n = gru([n, eu])

    if output_embedding["output_mode"] == 'graph':
        if use_set2set:
            # output
            outss = Dense(set2set_args['channels'], activation="linear")(n)
            out = Set2Set(**set2set_args)(outss)
        else:
            out = PoolingNodes(**pooling_args)(n)

        # final dense layers
        main_output = MLP(**output_mlp)(out)

    else:  # Node labeling
        out = n
        main_output = MLP(**output_mlp)(out)
        main_output = ChangeTensorType(input_tensor_type='ragged', output_tensor_type="tensor")(main_output)
        # no ragged for distribution supported atm

    model = ks.models.Model(inputs=[node_input, edge_input, edge_index_input], outputs=main_output)

    return model
