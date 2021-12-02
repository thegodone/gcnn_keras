import tensorflow as tf
import tensorflow.keras as ks

from kgcnn.layers.base import GraphBaseLayer
from kgcnn.layers.keras import Dense


@tf.keras.utils.register_keras_serializable(package='kgcnn', name='TrafoEdgeNetMessages')
class TrafoEdgeNetMessages(GraphBaseLayer):
    """Make message from edges for a linear transformation, i.e. matrix multiplication.
    The actual matrix is not a trainable weight of this layer but generated by a dense layer.
    This was proposed by `NMPNN <http://arxiv.org/abs/1704.01212>`_ .

    Args:
        target_shape (int): Target shape for message matrix.
    """

    def __init__(self, target_shape,
                 activation="linear",
                 use_bias=True,
                 kernel_regularizer=None, bias_regularizer=None, activity_regularizer=None,
                 kernel_constraint=None, bias_constraint=None,
                 kernel_initializer='glorot_uniform', bias_initializer='zeros',
                 **kwargs):
        """Initialize layer."""
        super(TrafoEdgeNetMessages, self).__init__(**kwargs)
        self.target_shape = target_shape
        self._units_out = int(target_shape[0])
        self._units_in = int(target_shape[1])
        self.lay_dense = Dense(units=self._units_out*self._units_in,
                               activation=activation, use_bias=use_bias,
                               kernel_regularizer=kernel_regularizer, bias_regularizer=bias_regularizer,
                               activity_regularizer=activity_regularizer, kernel_constraint=kernel_constraint,
                               bias_constraint=bias_constraint,kernel_initializer=kernel_initializer,
                               bias_initializer=bias_initializer)

    def build(self, input_shape):
        """Build layer."""
        super(TrafoEdgeNetMessages, self).build(input_shape)

    def call(self, inputs, **kwargs):
        """Forward pass.

        Args:
            inputs (tf.RaggedTensor): Messages embeddings or messages (batch, [M], F)

        Returns:
            tf.RaggedTensor: Messages in matrix for multiplication of shape (batch, [M], F, F)
        """
        assert isinstance(inputs, tf.RaggedTensor), "%s requires `RaggedTensor` input." % self.name
        assert inputs.ragged_rank == 1, "%s must have ragged_rank=1 input." % self.name
        up_scale = self.lay_dense(inputs, **kwargs)
        dens_trafo, trafo_part = up_scale.values, up_scale.row_splits
        dens_m = tf.reshape(dens_trafo, (ks.backend.shape(dens_trafo)[0], self._units_out, self._units_in))
        out = tf.RaggedTensor.from_row_splits(dens_m, trafo_part, validate=self.ragged_validate)
        return out

    def get_config(self):
        """Update layer config."""
        config = super(TrafoEdgeNetMessages, self).get_config()
        config.update({"target_shape": self.target_shape})
        config_dense = self.lay_dense.get_config()
        for x in ["kernel_regularizer", "activity_regularizer", "bias_regularizer", "kernel_constraint",
                  "bias_constraint", "kernel_initializer", "bias_initializer", "activation", "use_bias"]:
            config.update({x: config_dense[x]})
        return config


@tf.keras.utils.register_keras_serializable(package='kgcnn', name='MatMulMessages')
class MatMulMessages(GraphBaseLayer):
    """Linear transformation of edges or messages, i.e. matrix multiplication.
    The message dimension must be suitable for matrix multiplication. The actual matrix is not a trainable weight of
    this layer but passed as input.
    This was proposed by `NMPNN <http://arxiv.org/abs/1704.01212>`_ .

    """

    def __init__(self, **kwargs):
        """Initialize layer."""
        super(MatMulMessages, self).__init__(**kwargs)

    def build(self, input_shape):
        """Build layer."""
        super(MatMulMessages, self).build(input_shape)

    def call(self, inputs, **kwargs):
        """Forward pass. Operates on values without checking splits of the ragged dimension.

        Args:
            inputs (list): [trafo_mat, edges]

                - trafo_mat (tf.RaggedTensor): Transformation matrix for each message of shape (batch, [M], F', F).
                - edges (tf.RaggedTensor): Edge embeddings or messages (batch, [M], F)
            
        Returns:
            tf.RaggedTensor: Transformation of messages by matrix multiplication of shape (batch, [M], F')
        """
        assert all([isinstance(x, tf.RaggedTensor) for x in inputs]), "%s requires `RaggedTensor` input." % self.name
        assert all([x.ragged_rank == 1 for x in inputs]), "%s must have ragged_rank=1 input." % self.name
        dens_trafo, trafo_part = inputs[0].values, inputs[0].row_splits
        dens_e, epart = inputs[1].values, inputs[1].row_splits
        out = tf.keras.backend.batch_dot(dens_trafo, dens_e)
        out = tf.RaggedTensor.from_row_splits(out, epart, validate=self.ragged_validate)
        return out

    def get_config(self):
        """Update layer config."""
        config = super(MatMulMessages, self).get_config()
        return config


@tf.keras.utils.register_keras_serializable(package='kgcnn', name='GRUUpdate')
class GRUUpdate(GraphBaseLayer):
    """Gated recurrent unit for updating embeddings. First proposed by `NMPNN <http://arxiv.org/abs/1704.01212>`_ .

    Args:
        units (int): Units for GRU.
        activation: Activation function to use. Default: hyperbolic tangent
            (`tanh`). If you pass None, no activation is applied
            (ie. "linear" activation: `a(x) = x`).
        recurrent_activation: Activation function to use for the recurrent step.
            Default: sigmoid (`sigmoid`). If you pass `None`, no activation is
            applied (ie. "linear" activation: `a(x) = x`).
        use_bias: Boolean, (default `True`), whether the layer uses a bias vector.
        kernel_initializer: Initializer for the `kernel` weights matrix,
            used for the linear transformation of the inputs. Default:
            `glorot_uniform`.
        recurrent_initializer: Initializer for the `recurrent_kernel`
            weights matrix, used for the linear transformation of the recurrent state.
            Default: `orthogonal`.
        bias_initializer: Initializer for the bias vector. Default: `zeros`.
        kernel_regularizer: Regularizer function applied to the `kernel` weights
            matrix. Default: `None`.
        recurrent_regularizer: Regularizer function applied to the
            `recurrent_kernel` weights matrix. Default: `None`.
        bias_regularizer: Regularizer function applied to the bias vector. Default:
            `None`.
        kernel_constraint: Constraint function applied to the `kernel` weights
            matrix. Default: `None`.
        recurrent_constraint: Constraint function applied to the `recurrent_kernel`
            weights matrix. Default: `None`.
        bias_constraint: Constraint function applied to the bias vector. Default:
            `None`.
        dropout: Float between 0 and 1. Fraction of the units to drop for the
            linear transformation of the inputs. Default: 0.
        recurrent_dropout: Float between 0 and 1. Fraction of the units to drop for
            the linear transformation of the recurrent state. Default: 0.
        reset_after: GRU convention (whether to apply reset gate after or
            before matrix multiplication). False = "before",
            True = "after" (default and CuDNN compatible).
    """

    def __init__(self, units,
                 activation='tanh', recurrent_activation='sigmoid',
                 use_bias=True, kernel_initializer='glorot_uniform',
                 recurrent_initializer='orthogonal',
                 bias_initializer='zeros', kernel_regularizer=None,
                 recurrent_regularizer=None, bias_regularizer=None, kernel_constraint=None,
                 recurrent_constraint=None, bias_constraint=None, dropout=0.0,
                 recurrent_dropout=0.0, reset_after=True,
                 **kwargs):
        """Initialize layer."""
        super(GRUUpdate, self).__init__(**kwargs)
        self.units = units

        self.gru_cell = tf.keras.layers.GRUCell(units=units,
                                                activation=activation, recurrent_activation=recurrent_activation,
                                                use_bias=use_bias, kernel_initializer=kernel_initializer,
                                                recurrent_initializer=recurrent_initializer,
                                                bias_initializer=bias_initializer,
                                                kernel_regularizer=kernel_regularizer,
                                                recurrent_regularizer=recurrent_regularizer,
                                                bias_regularizer=bias_regularizer,
                                                kernel_constraint=kernel_constraint,
                                                recurrent_constraint=recurrent_constraint,
                                                bias_constraint=bias_constraint,
                                                dropout=dropout,
                                                recurrent_dropout=recurrent_dropout, reset_after=reset_after)

    def build(self, input_shape):
        """Build layer."""
        super(GRUUpdate, self).build(input_shape)

    def call(self, inputs, **kwargs):
        """Forward pass.

        Args:
            inputs (list): [nodes, updates]

                - nodes (tf.RaggedTensor): Node embeddings of shape (batch, [N], F)
                - updates (tf.RaggedTensor): Matching node updates of shape (batch, [N], F)

        Returns:
           tf.RaggedTensor: Updated nodes of shape (batch, [N], F)
        """
        assert all([isinstance(x, tf.RaggedTensor) for x in inputs]), "%s requires `RaggedTensor` input." % self.name
        assert all([x.ragged_rank == 1 for x in inputs]), "%s Must have ragged_rank=1 input." % self.name
        n, npart = inputs[0].values, inputs[0].row_splits
        eu, _ = inputs[1].values, inputs[1].row_splits
        out, _ = self.gru_cell(eu, n, **kwargs)
        out = tf.RaggedTensor.from_row_splits(out, npart, validate=self.ragged_validate)
        return out

    def get_config(self):
        """Update layer config."""
        config = super(GRUUpdate, self).get_config()
        conf_cell = self.gru_cell.get_config()
        param_list = ["units", "activation", "recurrent_activation",
                      "use_bias", "kernel_initializer",
                      "recurrent_initializer",
                      "bias_initializer", "kernel_regularizer",
                      "recurrent_regularizer", "bias_regularizer", "kernel_constraint",
                      "recurrent_constraint", "bias_constraint", "dropout",
                      "recurrent_dropout", "reset_after"]
        for x in param_list:
            config.update({x: conf_cell[x]})
        return config
