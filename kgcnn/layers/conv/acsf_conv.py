import tensorflow as tf
import numpy as np
import math
from kgcnn.layers.base import GraphBaseLayer
from kgcnn.layers.gather import GatherNodesOutgoing, GatherNodesSelection, GatherNodesIngoing
from kgcnn.layers.geom import NodeDistanceEuclidean, NodePosition
from kgcnn.layers.pooling import PoolingLocalEdges, RelationalPoolingLocalEdges
from kgcnn.layers.modules import LazyMultiply, LazySubtract, ExpandDims

ks = tf.keras


@tf.keras.utils.register_keras_serializable(package='kgcnn', name='ACSFRadial')
class ACSFRadial(GraphBaseLayer):
    r"""Atom-centered symmetry functions (ACSF) for high-dimensional neural network potentials (HDNNPs).

    This layer implements the radial part :math:`W_{i}^{rad}` :

    .. math::

        W_{i}^{rad} = \sum_{j \neq i} \; e^{−\eta \, (r_{ij} − \mu)^{2} } \; f_{ij}

    Here, for each atom type there is a set of parameters :math:`\eta` and :math:`\mu` and cutoff.
    The cutoff function :math:`f_ij = f_c(r_{ij})` is given by:

    .. math::

        f_c(r_{ij}) = 0.5 [\cos{\frac{\pi r_{ij}}{R_c}} + 1]

    In principle these parameters can be made trainable. The above sum is conducted for each atom type.

    Example:

    .. code-block:: python

        import tensorflow as tf
        from kgcnn.layers.conv.acsf_conv import ACSFRadial
        layer = ACSFRadial(
            eta_rs_rc=[[[0.0, 0.0, 8.0], [1.0, 0.0, 8.0]],[[0.0, 0.0, 8.0], [1.0, 0.0, 8.0]]],
            element_mapping=[1, 6]
        )
        z = tf.ragged.constant([[1, 6]], ragged_rank=1)
        xyz = tf.ragged.constant([[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]], ragged_rank=1, inner_shape=(3,))
        eij = tf.ragged.constant([[[0,1], [1, 0]]], ragged_rank=1, inner_shape=(2,))
        rep_i = layer([z, xyz, eij])

    """

    _max_atomic_number = 96

    def __init__(self,
                 eta_rs_rc: list,
                 element_mapping: list,
                 add_eps: bool = False,
                 param_constraint=None, param_regularizer=None, param_initializer="zeros",
                 param_trainable: bool = False,
                 **kwargs):
        r"""Initialize layer.

            Args:
                eta_rs_rc (list, np.ndarray): List of shape `(N, N, m, 3)` or `(N, m, 3)` where `N` are the considered
                    atom types and m the number of representations. Tensor output will be shape `(batch, None, N*m)` .
                    In the last dimension are the values for :math:`eta`, :math:`R_s` and :math:`R_c` .
                element_mapping (list): Atomic numbers of elements in :obj:`eta_rs_rc` , must have shape `(N, )` .
                    Should not contain duplicate elements.
                add_eps (bool): Whether to add epsilon. Default is False.
                param_constraint: Parameter constraint for weights. Default is None.
                param_regularizer: Parameter regularizer for weights. Default is None.
                param_initializer: Parameter initializer for weights. Default is "zeros".
                param_trainable (bool): Parameter make trainable. Default is False.
        """
        super(ACSFRadial, self).__init__(**kwargs)
        # eta_rs_rc of shape (N, N, m, 3) with m combinations of eta, rs, rc
        # or simpler (N, m, 3) where we repeat an additional N dimension assuming same parameter of source.
        self.eta_rs_rc = np.array(eta_rs_rc)
        assert len(self.eta_rs_rc.shape) in [3, 4], "Require `eta_rs_rc` of shape `(N, N, m, 3)` or `(N, m, 3)`"
        self.use_target_set = (len(self.eta_rs_rc.shape) == 4)
        self.num_relations = self.eta_rs_rc.shape[1] if self.use_target_set else self.eta_rs_rc.shape[0]
        self.element_mapping = np.array(element_mapping, dtype="int")  # of shape (N, ) with atomic number for eta_rs_rc
        self.reverse_mapping = np.empty(self._max_atomic_number, dtype="int")
        self.reverse_mapping.fill(np.iinfo(self.reverse_mapping.dtype).max)
        for i, pos in enumerate(self.element_mapping):
            self.reverse_mapping[pos] = i
        self.add_eps = add_eps

        self.lazy_mult = LazyMultiply()
        self.layer_pos = NodePosition()
        self.layer_gather = GatherNodesSelection([0, 1])
        self.layer_dist = NodeDistanceEuclidean(add_eps=add_eps)
        self.pool_sum = RelationalPoolingLocalEdges(num_relations=self.num_relations, pooling_method="sum")

        # We can do this in init since weights do not depend on input shape.
        self.param_initializer = param_initializer
        self.param_regularizer = param_regularizer
        self.param_constraint = param_constraint
        self.param_trainable = param_trainable

        self.weight_eta_rs_rc = self.add_weight(
            "eta_rs_rc",
            shape=self.eta_rs_rc.shape,
            initializer=self.param_initializer,
            regularizer=self.param_regularizer,
            constraint=self.param_constraint,
            dtype=self.dtype, trainable=self.param_trainable
        )
        self.weight_reverse_mapping = self.add_weight(
            "reverse_mapping",
            shape=(self._max_atomic_number,),
            initializer=self.param_initializer,
            regularizer=self.param_regularizer,
            constraint=self.param_constraint,
            dtype="int64", trainable=False
        )

        self.set_weights([self.eta_rs_rc, self.reverse_mapping])

    def _find_atomic_number_maps(self, inputs):
        return tf.gather(self.weight_reverse_mapping, inputs, axis=0)

    def _find_params_per_bond(self, inputs: list):
        zi_map, zj_map = inputs
        if self.use_target_set:
            params = tf.gather(tf.gather(self.weight_eta_rs_rc, zi_map, axis=0), zj_map, axis=1, batch_dims=1)
        else:
            # Atomic specific for j but not i.
            params = tf.gather(self.weight_eta_rs_rc, zj_map, axis=0)
        return params

    @staticmethod
    def _compute_fc(inputs: tf.Tensor):
        rij, params = inputs
        cutoff = tf.gather(params, 2, axis=-1)
        fc = tf.clip_by_value(tf.broadcast_to(rij, tf.shape(cutoff)), -cutoff, cutoff)
        fc = (tf.math.cos(fc * math.pi / cutoff) + 1.0) * 0.5
        # fc = tf.where(tf.abs(inputs) < self.cutoff, fc, tf.zeros_like(fc))
        return fc

    @staticmethod
    def _compute_gaussian_expansion(inputs: tf.Tensor):
        rij, params = inputs
        eta, mu = tf.gather(params, 0, axis=-1), tf.gather(params, 1, axis=-1)
        arg = tf.square(rij - mu) * eta
        return tf.exp(-arg)

    @staticmethod
    def _flatten_relations(inputs):
        input_shape = tf.shape(inputs)
        flatten_shape = tf.concat(
            [input_shape[:1], tf.constant([inputs.shape[1] * inputs.shape[2]], dtype=input_shape.dtype)], axis=0)
        return tf.reshape(inputs, flatten_shape)

    def build(self, input_shape):
        super(ACSFRadial, self).build(input_shape)

    def call(self, inputs, **kwargs):
        r"""Forward pass.

        Args:
            inputs: [z, xyz, ij]

                - z (tf.RaggedTensor): Atomic numbers of shape (batch, [N])
                - xyz (tf.RaggedTensor): Node coordinates of shape (batch, [N], 3)
                - ij (tf.RaggedTensor): Edge indices referring to nodes of shape (batch, [M], 2)

        Returns:
            tf.RaggedTensor: Atomic representation of shape `(batch, None, units)` .
        """
        z, xyz, eij = inputs
        z = self.map_values(tf.cast, z, dtype=eij.dtype)
        xi, xj = self.layer_pos([xyz, eij], **kwargs)
        rij = self.layer_dist([xi, xj], **kwargs)
        zi, zj = self.layer_gather([z, eij])
        zi_map = self.map_values(self._find_atomic_number_maps, zi)
        zj_map = self.map_values(self._find_atomic_number_maps, zj)
        params_per_bond = self.map_values(self._find_params_per_bond, [zi_map, zj_map])
        fc = self.map_values(self._compute_fc, [rij, params_per_bond])
        gij = self.map_values(self._compute_gaussian_expansion, [rij, params_per_bond])
        rep = self.lazy_mult([gij, fc], **kwargs)
        pooled = self.pool_sum([xyz, rep, eij, zj_map], **kwargs)
        return self.map_values(self._flatten_relations, pooled)

    def get_config(self):
        config = super(ACSFRadial, self).get_config()
        config.update({
            "eta_rs_rc": self.eta_rs_rc.tolist(),
            "element_mapping": self.element_mapping.tolist(),
            "add_eps": self.add_eps,
            "param_constraint": self.param_constraint,
            "param_regularizer": self.param_regularizer,
            "param_initializer": self.param_initializer,
            "param_trainable": self.param_trainable
        })
        return config


@tf.keras.utils.register_keras_serializable(package='kgcnn', name='ACSFAngular')
class ACSFAngular(GraphBaseLayer):
    r"""

    Example:

    .. code-block:: python

        import tensorflow as tf
        from kgcnn.layers.conv.acsf_conv import ACSFAngular
        layer = ACSFAngular(
            eta_zeta_lambda_rc=[[[0.0, 1.0, -1.0, 8.0]],[[0.0, 1.0, -1.0, 8.0]], [[0.0, 1.0, -1.0, 8.0]]],
            element_mapping=[1, 6],
            keep_pair_order=False
        )
        z = tf.ragged.constant([[1, 6, 6]], ragged_rank=1)
        xyz = tf.ragged.constant([[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]], ragged_rank=1, inner_shape=(3,))
        ijk = tf.ragged.constant([[[0, 1, 2], [1, 0, 2], [2, 0, 1]]], ragged_rank=1, inner_shape=(3,))
        rep_i = layer([z, xyz, ijk])

    """

    _max_atomic_number = 96

    def __init__(self, eta_zeta_lambda_rc: list,
                 element_mapping: list,
                 element_pair_mapping: list = None,
                 add_eps: bool = False,
                 keep_pair_order: bool = False,
                 param_initializer="zeros", param_regularizer=None, param_constraint=None,
                 param_trainable: bool = False,
                 **kwargs):
        super(ACSFAngular, self).__init__(**kwargs)
        self.add_eps = add_eps
        self.keep_pair_order = keep_pair_order
        self.eta_zeta_lambda_rc = np.array(eta_zeta_lambda_rc, dtype="float")
        assert len(self.eta_zeta_lambda_rc.shape) in [3, 4], "Require `eta_zeta_lambda_rc` rank 3 or 4."
        self.use_target_set = (len(self.eta_zeta_lambda_rc.shape) == 4)
        if self.use_target_set:
            self.num_relations = self.eta_zeta_lambda_rc.shape[1]
        else:
            self.num_relations = self.eta_zeta_lambda_rc.shape[0]
        self.element_mapping = np.array(element_mapping, dtype="int")  # of shape (N, ) with atomic number.
        if element_pair_mapping is None:
            element_pair_index = np.expand_dims(self.element_mapping, axis=-1)
            self.element_pair_mapping = np.concatenate([
                    np.repeat(np.expand_dims(element_pair_index, axis=0), len(self.element_mapping), axis=0),
                    np.repeat(np.expand_dims(element_pair_index, axis=1), len(self.element_mapping), axis=1)
                ], axis=-1
            ).reshape((-1, 2))
            self.element_pair_mapping = np.unique(np.sort(self.element_pair_mapping, axis=-1), axis=0)
        else:
            self.element_pair_mapping = np.array(element_pair_mapping, dtype="int")
        assert len(self.element_pair_mapping.shape) == 2 and self.element_pair_mapping.shape[1] == 2
        assert self.element_pair_mapping.shape[0] == self.num_relations

        self.reverse_mapping = np.empty(self._max_atomic_number, dtype="int")
        self.reverse_mapping.fill(np.iinfo(self.reverse_mapping.dtype).max)
        for i, pos in enumerate(self.element_mapping):
            self.reverse_mapping[pos] = i

        self.reverse_pair_mapping = np.empty((self._max_atomic_number, self._max_atomic_number), dtype="int")
        self.reverse_pair_mapping.fill(np.iinfo(self.reverse_pair_mapping.dtype).max)
        for i, pos in enumerate(self.element_pair_mapping):
            self.reverse_pair_mapping[pos[0], pos[1]] = i
            if not self.keep_pair_order:
                self.reverse_pair_mapping[pos[1], pos[0]] = i

        # Sub-layer.
        self.lazy_mult = LazyMultiply()
        self.layer_pos = NodePosition(selection_index=[0, 1, 2])
        self.layer_dist = NodeDistanceEuclidean(add_eps=add_eps)
        self.pool_sum = RelationalPoolingLocalEdges(num_relations=self.num_relations, pooling_method="sum")
        self.lazy_sub = LazySubtract()
        self.layer_gather = GatherNodesSelection(selection_index=[0,1,2])

        # We can do this in init since weights do not depend on input shape.
        self.param_initializer = param_initializer
        self.param_regularizer = param_regularizer
        self.param_constraint = param_constraint
        self.param_trainable = param_trainable

        self.weight_eta_zeta_lambda_rc = self.add_weight(
            "eta_zeta_lambda_rc",
            shape=self.eta_zeta_lambda_rc.shape,
            initializer=self.param_initializer,
            regularizer=self.param_regularizer,
            constraint=self.param_constraint,
            dtype=self.dtype, trainable=self.param_trainable
        )
        self.weight_reverse_mapping = self.add_weight(
            "reverse_mapping",
            shape=(self._max_atomic_number,),
            initializer=self.param_initializer,
            regularizer=self.param_regularizer,
            constraint=self.param_constraint,
            dtype="int64", trainable=False
        )
        self.weight_reverse_pair_mapping = self.add_weight(
            "reverse_pair_mapping",
            shape=(self._max_atomic_number, self._max_atomic_number),
            initializer=self.param_initializer,
            regularizer=self.param_regularizer,
            constraint=self.param_constraint,
            dtype="int64", trainable=False
        )
        self.set_weights([self.eta_zeta_lambda_rc, self.reverse_mapping, self.reverse_pair_mapping])

    def _find_atomic_number_maps(self, inputs):
        return tf.gather(self.weight_reverse_mapping, inputs, axis=0)

    def _find_atomic_number_pair_maps(self, inputs):
        zj, zk = inputs
        return tf.gather(tf.gather(self.weight_reverse_pair_mapping, zj, axis=0), zk, axis=1, batch_dims=1)

    def _find_params_per_bond(self, inputs: list):
        zi_map, zjk_map = inputs
        if self.use_target_set:
            params = tf.gather(tf.gather(self.weight_eta_zeta_lambda_rc, zi_map, axis=0), zjk_map, axis=1, batch_dims=1)
        else:
            # Atomic specific for j,k but not i.
            params = tf.gather(self.weight_eta_zeta_lambda_rc, zjk_map, axis=0)
        return params

    @staticmethod
    def _compute_fc(inputs: tf.Tensor):
        rij, params = inputs
        cutoff = tf.gather(params, 3, axis=-1)
        fc = tf.clip_by_value(tf.broadcast_to(rij, tf.shape(cutoff)), -cutoff, cutoff)
        fc = (tf.math.cos(fc * np.pi / cutoff) + 1.0) * 0.5
        # fc = tf.where(tf.abs(inputs) < self.cutoff, fc, tf.zeros_like(fc))
        return fc

    @staticmethod
    def _compute_gaussian_expansion(inputs: tf.Tensor):
        rij, params = inputs
        eta = tf.gather(params, 0, axis=-1)
        # mu = tf.gather(params, 1, axis=-1)
        arg = tf.square(rij) * eta
        return tf.exp(-arg)

    @staticmethod
    def _compute_pow_cos_angle_(inputs: list):
        vij, vik, rij, rik, params = inputs
        lamda, zeta = tf.gather(params, 2, axis=-1), tf.gather(params, 1, axis=-1)
        cos_theta = tf.reduce_sum(vij * vik, axis=-1, keepdims=True) / rij / rik
        cos_term = cos_theta * lamda + 1.0
        cos_term = tf.pow(cos_term, zeta)
        scale = tf.ones_like(cos_term) * 2.0
        scaled_cos_term = tf.pow(scale, 1.0 - zeta) * cos_term
        return scaled_cos_term

    @staticmethod
    def _flatten_relations(inputs):
        input_shape = tf.shape(inputs)
        flatten_shape = tf.concat(
            [input_shape[:1], tf.constant([inputs.shape[1] * inputs.shape[2]], dtype=input_shape.dtype)], axis=0)
        return tf.reshape(inputs, flatten_shape)

    def build(self, input_shape):
        super(ACSFAngular, self).build(input_shape)

    def call(self, inputs, **kwargs):
        r"""Forward pass.

        Args:
            inputs: [z, xyz, ijk]

                - z (tf.RaggedTensor): Atomic numbers of shape (batch, [N])
                - xyz (tf.RaggedTensor): Node coordinates of shape (batch, [N], 3)
                - ijk (tf.RaggedTensor): Angle indices referring to nodes of shape (batch, [M], 3)

        Returns:
            tf.RaggedTensor: Atomic representation of shape `(batch, None, units)` .
        """
        z, xyz, ijk = inputs
        z = self.map_values(tf.cast, z, dtype=ijk.dtype)
        zi, zj, zk = self.layer_gather([z, ijk], **kwargs)
        xi, xj, xk = self.layer_pos([xyz, ijk], **kwargs)
        zi_map = self.map_values(self._find_atomic_number_maps, zi)
        zjk_map = self.map_values(self._find_atomic_number_pair_maps, [zj, zk])
        params_per_bond = self.map_values(self._find_params_per_bond, [zi_map, zjk_map])
        rij = self.layer_dist([xi, xj], **kwargs)
        rik = self.layer_dist([xi, xk], **kwargs)
        rjk = self.layer_dist([xj, xk], **kwargs)
        fij = self.map_values(self._compute_fc, [rij, params_per_bond])
        fik = self.map_values(self._compute_fc, [rik, params_per_bond])
        fjk = self.map_values(self._compute_fc, [rjk, params_per_bond])
        gij = self.map_values(self._compute_gaussian_expansion, [rij, params_per_bond])
        gik = self.map_values(self._compute_gaussian_expansion, [rik, params_per_bond])
        gjk = self.map_values(self._compute_gaussian_expansion, [rjk, params_per_bond])
        vij = self.lazy_sub([xi, xj], **kwargs)
        vik = self.lazy_sub([xi, xk], **kwargs)
        pow_cos_theta = self.map_values(self._compute_pow_cos_angle_, [vij, vik, rij, rik, params_per_bond])
        rep = self.lazy_mult([pow_cos_theta, gij, gik, gjk, fij, fik, fjk], **kwargs)
        pool_ang = self.pool_sum([xyz, rep, ijk, zjk_map], **kwargs)
        return self.map_values(self._flatten_relations, pool_ang)

    def get_config(self):
        config = super(ACSFAngular, self).get_config()
        config.update({
            "eta_zeta_lambda_rc": self.eta_zeta_lambda_rc,
            "add_eps": self.add_eps,
            "element_mapping": self.element_mapping,
            "keep_pair_order": self.keep_pair_order,
            "element_pair_mapping": self.element_pair_mapping,
            "param_trainable": self.param_trainable,
            "param_constraint": self.param_constraint,
            "param_regularizer": self.param_regularizer,
            "param_initializer": self.param_initializer,
        })
        return config
